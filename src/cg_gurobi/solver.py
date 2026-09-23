"""列生成主循环：按舱位独立运行 MP1/MP2，合并整数解与 LP 上界。

证书口径（文档第 6.5 节）：
- 精确 U：某轮所有组的定价均以 Optimal 结束且最小约化成本 >= -TOL，
  此时受限主问题的 LP 值就是完整列空间 LP 值；
- 安全 U：某轮所有组都返回有限下界 lb_g（Optimal 或 ObjBound），
  L_safe = L_RMP + Σ min(0, lb_g)；
- 无 U：任一组没有有限下界。
整数解 I 来自列池整数主问题，并由独立评估器复核。
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from concurrent.futures import ThreadPoolExecutor

import gurobipy as gp

from src import allocation_evaluator as evaluator

from .instance import Instance
from .master import Master
from .pricing import Column, GroupPricer, TOL, reduced_cost

PassengerKey = Tuple[int, int]


def _cfg(config: Mapping[str, Any]) -> Dict[str, Any]:
    c = dict(config.get("cg_gurobi", {}))
    c.setdefault("total_time_limit", 600.0)
    c.setdefault("pricing_time_limit", 60.0)
    c.setdefault("integer_time_limit", 30.0)
    c.setdefault("max_iterations", 10_000)
    c.setdefault("pricing_workers", 4)
    c.setdefault("dive", True)
    c.setdefault("dive_pricing_rounds", 2)
    c.setdefault("warm_start", "heuristic")   # heuristic | greedy | none
    c.setdefault("heuristic_seconds", 5.0)
    c.setdefault("log", True)
    return c


def _log(enabled: bool, msg: str) -> None:
    if enabled:
        print(f"[cg_gurobi] {msg}", flush=True)


def solve_single_cabin(
    new_seats: Sequence[dict], old_seats: Sequence[dict], groups_data: Sequence[dict],
    weights: Mapping[str, float], config: Mapping[str, Any], *, deadline: Optional[float] = None,
    env: Optional[gp.Env] = None,
) -> Dict[str, Any]:
    cfg = _cfg(config)
    log = bool(cfg["log"])
    started = time.perf_counter()
    if deadline is None:
        deadline = started + float(cfg["total_time_limit"])
    own_env = env is None
    if env is None:
        env = gp.Env(empty=True); env.setParam("OutputFlag", 0); env.start()

    inst = Instance(new_seats, old_seats, groups_data, weights, config)
    master = Master(inst, env)
    workers = max(1, int(cfg["pricing_workers"]))
    worker_envs: List[gp.Env] = []
    for _ in range(workers):
        e = gp.Env(empty=True); e.setParam("OutputFlag", 0); e.start(); worker_envs.append(e)
    pricers = {
        g.gid: GroupPricer(inst, g, worker_envs[idx % workers])
        for idx, g in enumerate(inst.groups)
    }
    executor = ThreadPoolExecutor(max_workers=workers)
    build_seconds = time.perf_counter() - started

    # 初始列：按组规模从大到小，用零对偶定价 MIP 逐组贪心占座。
    # 全部成功则 Phase I 立即可行；失败的组交给人工列处理。
    warm_columns = 0
    warm_mode = str(cfg["warm_start"]).lower()
    heuristic_score: Optional[float] = None
    if warm_mode == "heuristic":
        # 用项目现有的限时启发式取得一个完整合法方案，拆成各组初始列。
        # 这正是文档 6.3 允许的“启发式提供覆盖所有组的初始合法列”。
        try:
            from src import heuristic_seat_allocator as heuristic
            h_config = dict(config)
            h_alg = dict(h_config.get("algorithm", {}))
            h_alg["business_time_limit_seconds"] = float(cfg["heuristic_seconds"])
            h_config["algorithm"] = h_alg
            h_result, _ = heuristic.run_allocation(
                list(new_seats), list(old_seats), list(groups_data), dict(weights), h_config
            )
            h_assigned = h_result.get("assigned_seats", {})
            h_v, h_u, _ = evaluator.count_hard_constraint_violations(
                list(new_seats), list(groups_data), h_assigned, dict(config)
            )
            if h_v == 0 and h_u == 0:
                heuristic_score, _ = evaluator.calculate_soft_score(
                    list(new_seats), list(old_seats), list(groups_data), h_assigned, dict(weights), dict(config)
                )
                for g in inst.groups:
                    assignment = {p.key: h_assigned[p.key] for p in g.passengers}
                    col = pricers[g.gid].column_from_assignment(assignment)
                    if col is not None:
                        master.add_columns([col]); warm_columns += 1
            _log(log, f"heuristic warm start: legal={h_v == 0 and h_u == 0} score={heuristic_score} "
                      f"columns={warm_columns} in {time.perf_counter()-started:.2f}s")
        except Exception as exc:  # 启发式失败不影响精确路线
            _log(log, f"heuristic warm start failed: {type(exc).__name__}: {exc}")
            warm_columns = 0
    if warm_mode in ("greedy", "heuristic") and warm_columns < len(inst.groups):
        blocked: set = set()
        for g in inst.groups:
            if any(c.gid == g.gid for c in master.columns):
                blocked.update(next(c for c in master.columns if c.gid == g.gid).resources)
        for g in sorted(inst.groups, key=lambda x: -x.size):
            if time.perf_counter() >= deadline:
                break
            if any(c.gid == g.gid for c in master.columns):
                continue
            col = pricers[g.gid].greedy_column(blocked, min(5.0, max(0.05, deadline - time.perf_counter())))
            if col is not None:
                master.add_columns([col])
                blocked.update(col.resources)
                warm_columns += 1
        _log(log, f"warm start: {warm_columns}/{len(inst.groups)} groups seeded in {time.perf_counter()-started:.2f}s")

    iterations: List[Dict[str, Any]] = []
    termination = "unknown"
    exact_lp_cost: Optional[float] = None
    safe_lp_cost: Optional[float] = None
    root_lp_cost: Optional[float] = None
    infeasible_certified = False
    it = 0
    escalation = 1.0
    while True:
        it += 1
        if time.perf_counter() >= deadline:
            termination = "time_limit"; break
        if it > int(cfg["max_iterations"]):
            termination = "iteration_limit"; break
        status, lp_cost, art = master.solve_lp()
        if status != "optimal":
            termination = f"master_{status}"; break
        if master.phase_one and art <= TOL:
            master.start_phase_two()
            _log(log, f"iter {it}: Phase I feasible, switching to original objective")
            continue
        duals = master.duals()
        remaining = deadline - time.perf_counter()
        per_group = min(float(cfg["pricing_time_limit"]), max(0.05, remaining / max(1, len(inst.groups))))
        new_cols: List[Column] = []
        all_certified = True
        lbs: Dict[int, float] = {}
        phase_one = master.phase_one
        per_group_limit = min(per_group * workers * escalation, max(float(cfg["pricing_time_limit"]), remaining * 0.5))
        outcomes = list(executor.map(
            lambda g: pricers[g.gid].price(duals, phase_one=phase_one, time_limit=per_group_limit),
            inst.groups,
        ))
        for out in outcomes:
            lbs[out.gid] = out.lower_bound
            if not out.certified:
                all_certified = False
            for col in out.columns:
                if reduced_cost(col, duals, phase_one) < -TOL:
                    new_cols.append(col)
        priced_all = len(outcomes) == len(inst.groups)
        added = master.add_columns(new_cols)
        record = {
            "iteration": it, "phase": 1 if master.phase_one else 2, "rmp_cost": lp_cost,
            "artificial": art, "columns_added": added, "total_columns": len(master.columns),
            "all_certified": all_certified and priced_all,
            "min_reduced_cost": min((o.best_reduced_cost for o in outcomes), default=math.inf),
            "pricing_seconds": sum(o.seconds for o in outcomes),
            "elapsed": time.perf_counter() - started,
        }
        iterations.append(record)
        _log(log, f"iter {it}: phase={record['phase']} rmp={lp_cost:.4f} art={art:.3g} "
                  f"added={added} cols={len(master.columns)} min_rc={record['min_reduced_cost']:.4f} "
                  f"certified={record['all_certified']} t={record['elapsed']:.1f}s")
        if not master.phase_one:
            root_lp_cost = lp_cost
            if priced_all and all(math.isfinite(v) for v in lbs.values()):
                candidate = lp_cost + sum(min(0.0, v) for v in lbs.values())
                safe_lp_cost = candidate if safe_lp_cost is None else max(safe_lp_cost, candidate)
        if added == 0:
            if all_certified and priced_all:
                if master.phase_one:
                    infeasible_certified = True
                    termination = "infeasible"
                else:
                    exact_lp_cost = lp_cost
                    termination = "lp_optimal"
                break
            # 没有新列但有组在时限内未闭合：只要还有时间就放大定价时限重试。
            escalation *= 2.0
            if time.perf_counter() + 1.0 >= deadline:
                termination = "pricing_not_certified"
                break
            _log(log, f"iter {it}: no new columns, {sum(1 for o in outcomes if not o.certified)} groups uncertified -> escalate x{escalation:.0f}")

    result: Dict[str, Any] = {
        "termination": termination,
        "iterations": iterations,
        "columns": len(master.columns),
        "build_seconds": build_seconds,
        "warm_start_columns": warm_columns,
        "warm_start_mode": warm_mode,
        "dive": dive_stats if 'dive_stats' in dir() else None,
        "heuristic_score": heuristic_score,
        "pricing_workers": workers,
    }
    if infeasible_certified:
        result.update({"status": "infeasible", "assigned_seats": {}, "total_score": None})
        executor.shutdown(wait=True)
        for e in worker_envs:
            e.dispose()
        return result

    # 潜水启发式：逐步固定 LP 值最大的分数列并继续定价，扩充列池（不改变根 LP 界）。
    dive_stats = {"fixed": 0, "columns_added": 0, "rounds": 0}
    if bool(cfg["dive"]) and not master.phase_one and termination != "infeasible":
        int_reserve = min(float(cfg["integer_time_limit"]), max(1.0, 0.25 * (deadline - time.perf_counter())))
        dive_deadline = deadline - int_reserve
        try:
            while time.perf_counter() < dive_deadline:
                status, lp_cost, _ = master.solve_lp()
                if status != "optimal":
                    break
                values = master.lp_values()
                fixed = master.fixed_groups()
                frac = {i: v for i, v in values.items() if v < 1.0 - 1e-6 and master.columns[i].gid not in fixed}
                if not frac:
                    break
                master.fix_column(max(frac, key=frac.get))
                dive_stats["fixed"] += 1
                fixed = master.fixed_groups()
                free_groups = [g for g in inst.groups if g.gid not in fixed]
                for _ in range(int(cfg["dive_pricing_rounds"])):
                    if not free_groups or time.perf_counter() >= dive_deadline:
                        break
                    status, lp_cost, _ = master.solve_lp()
                    if status != "optimal":
                        break
                    duals = master.duals()
                    limit = max(0.05, min(float(cfg["pricing_time_limit"]), (dive_deadline - time.perf_counter()) / 2))
                    outs = list(executor.map(lambda g: pricers[g.gid].price(duals, phase_one=False, time_limit=limit), free_groups))
                    cols = [c for o in outs for c in o.columns if reduced_cost(c, duals, False) < -TOL]
                    added = master.add_columns(cols)
                    dive_stats["columns_added"] += added
                    dive_stats["rounds"] += 1
                    if added == 0:
                        break
        finally:
            master.unfix_all()
        _log(log, f"dive: fixed={dive_stats['fixed']} rounds={dive_stats['rounds']} columns_added={dive_stats['columns_added']} cols={len(master.columns)}")

    # 整数解：列池整数主问题
    int_status, selected, int_cost = ("phase_one", None, None)
    if not master.phase_one:
        int_budget = min(float(cfg["integer_time_limit"]), max(1.0, deadline - time.perf_counter()))
        int_status, selected, int_cost = master.solve_integer(int_budget)
    assigned: Dict[PassengerKey, str] = {}
    if selected:
        for col in selected:
            assigned.update(col.as_dict())
    violations, unassigned, detail = evaluator.count_hard_constraint_violations(
        list(new_seats), list(groups_data), assigned, dict(config)
    )
    soft, soft_detail = evaluator.calculate_soft_score(
        list(new_seats), list(old_seats), list(groups_data), assigned, dict(weights), dict(config)
    )
    if selected and abs(-int_cost - soft) > 1e-5 * max(1.0, abs(soft)):
        raise RuntimeError(f"列池整数目标 {-int_cost:.6f} 与评估器软分 {soft:.6f} 不一致")
    lp_bound = -exact_lp_cost if exact_lp_cost is not None else None
    safe_bound = -safe_lp_cost if safe_lp_cost is not None else None
    result.update({
        "status": "complete" if (selected and violations == 0 and unassigned == 0) else "incomplete",
        "assigned_seats": assigned,
        "total_score": soft if selected else None,
        "score_detail": soft_detail,
        "hard_violations": violations,
        "unassigned": unassigned,
        "violation_detail": detail,
        "integer_status": int_status,
        "lp_bound": lp_bound,
        "lp_bound_certified": lp_bound is not None,
        "safe_bound": safe_bound,
        "rmp_lp_cost": root_lp_cost,
        "gap_absolute": (lp_bound - soft) if (lp_bound is not None and selected) else None,
        "seconds": time.perf_counter() - started,
    })
    executor.shutdown(wait=True)
    for e in worker_envs:
        e.dispose()
    if own_env:
        env.dispose()
    return result


def solve(
    new_seats: Sequence[dict], old_seats: Sequence[dict], groups_data: Sequence[dict],
    weights: Mapping[str, float], config: Mapping[str, Any],
) -> Dict[str, Any]:
    """按舱位分块独立求解并严格合并（文档 6.0）。"""
    cfg = _cfg(config)
    started = time.perf_counter()
    deadline = started + float(cfg["total_time_limit"])
    mixed = evaluator.mixed_cabin_group_ids(groups_data)
    if mixed:
        raise ValueError(f"同行组必须舱位同质，混舱组: {mixed}")
    by_cabin: Dict[str, List[dict]] = {}
    for g in groups_data:
        cabin = evaluator.group_cabin_class(g)
        if cabin is None:
            raise ValueError(f"同行组缺少唯一有效舱位: groupId={g.get('groupId')}")
        by_cabin.setdefault(cabin, []).append(g)
    env = gp.Env(empty=True); env.setParam("OutputFlag", 0); env.start()
    per_cabin: Dict[str, Dict[str, Any]] = {}
    # 座位少的舱位先算，剩余时间留给最大的舱位
    order = sorted(by_cabin, key=lambda c: sum(1 for s in new_seats if s.get("seatClass") == c))
    for idx, cabin in enumerate(order):
        seats_c = [s for s in new_seats if s.get("seatClass") == cabin]
        old_c = [s for s in old_seats if s.get("seatClass") == cabin]
        remaining = deadline - time.perf_counter()
        share = remaining if idx == len(order) - 1 else remaining * max(0.1, len(seats_c) / max(1, len(new_seats)))
        per_cabin[cabin] = solve_single_cabin(
            seats_c, old_c, by_cabin[cabin], weights, config,
            deadline=time.perf_counter() + max(1.0, share), env=env,
        )
    env.dispose()
    assigned: Dict[PassengerKey, str] = {}
    for r in per_cabin.values():
        assigned.update(r.get("assigned_seats") or {})
    violations, unassigned, detail = evaluator.count_hard_constraint_violations(
        list(new_seats), list(groups_data), assigned, dict(config)
    )
    soft, soft_detail = evaluator.calculate_soft_score(
        list(new_seats), list(old_seats), list(groups_data), assigned, dict(weights), dict(config)
    )
    complete = violations == 0 and unassigned == 0 and all(r.get("status") == "complete" for r in per_cabin.values())
    exact_ok = all(r.get("lp_bound") is not None for r in per_cabin.values())
    safe_ok = all((r.get("lp_bound") is not None or r.get("safe_bound") is not None) for r in per_cabin.values())
    lp_bound = sum(r["lp_bound"] for r in per_cabin.values()) if exact_ok else None
    safe_bound = (
        sum((r["lp_bound"] if r.get("lp_bound") is not None else r["safe_bound"]) for r in per_cabin.values())
        if safe_ok else None
    )
    return {
        "status": "complete" if complete else "incomplete",
        "assigned_seats": assigned,
        "total_score": soft,
        "score_detail": soft_detail,
        "hard_violations": violations,
        "unassigned": unassigned,
        "violation_detail": detail,
        "lp_bound": lp_bound,
        "lp_bound_certified": lp_bound is not None,
        "safe_bound": safe_bound,
        "gap_absolute": (lp_bound - soft) if lp_bound is not None else None,
        "gap_relative": ((lp_bound - soft) / max(1.0, abs(soft))) if lp_bound is not None else None,
        "cabins": {c: {k: v for k, v in r.items() if k not in ("assigned_seats", "score_detail", "violation_detail")} for c, r in per_cabin.items()},
        "seconds": time.perf_counter() - started,
    }
