"""分支定价（最优先搜索）。

分支规则分两级：
1. ('seat', gid, seat, must)：组 g 是否占用座位 s（列级：看 col.occupied）；
2. ('pax', gid, hostnum, seat, must)：旅客 (g, hostnum) 是否坐座位 s。
先按 (组, 座位) 用量选最接近 0.5 的分支；若组级用量已整数但 y 仍分数
（同座位集合的不同排列/不同保护空座选择），再按旅客级用量分支；两级都
找不到分数用量而 y 仍分数时，该节点标为未认证（不冒充整数解）。

节点关闭规则：
- 受限主问题 LP 不可行 ≠ 节点不可行（没有 Farkas 定价），一律记为未认证；
- 只有当 LP 解中所有列 y ∈ {0,1} 时才把它当候选整数解，且必须通过调用方
  提供的 validate() （独立评估器：硬违规为 0，成本按评估器重算）才能成为 incumbent；
- proven 仅当前沿为空且所有处理过的节点都被认证。
"""
from __future__ import annotations

import heapq
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from gurobipy import GRB

from .instance import Instance
from .master import Master
from .pricing import Column, GroupPricer, TOL, reduced_cost

Rule = Tuple  # ('seat', gid, seat, must) | ('pax', gid, hostnum, seat, must)


@dataclass(order=True)
class Node:
    bound_cost: float
    serial: int
    depth: int = field(compare=False)
    rules: Tuple[Rule, ...] = field(compare=False, default=())


def _column_respects(col: Column, rules: Sequence[Rule]) -> bool:
    occupied = set(col.occupied)
    assignment = dict(col.assignment)
    for rule in rules:
        if rule[0] == 'seat':
            _, gid, seat, must = rule
            if col.gid != gid:
                continue
            if must != (seat in occupied):
                return False
        else:
            _, gid, hostnum, seat, must = rule
            if col.gid != gid:
                continue
            if must != (assignment.get((gid, hostnum)) == seat):
                return False
    return True


def _pricing_rules(rules: Sequence[Rule], gid: int) -> List[Tuple]:
    out = []
    for rule in rules:
        if rule[1] != gid:
            continue
        if rule[0] == 'seat':
            out.append(('seat', rule[2], rule[3]))
        else:
            out.append(('pax', rule[2], rule[3], rule[4]))
    return out


def _choose_branch(master: Master, rules: Sequence[Rule]) -> Tuple[Optional[Rule], bool]:
    """返回 (分支规则或 None, LP 是否已整数)。"""
    values = master.lp_values()
    integral = all(v > 1.0 - 1e-6 for v in values.values())
    if integral:
        return None, True
    seat_usage: Dict[Tuple[int, str], float] = {}
    pax_usage: Dict[Tuple[int, int, str], float] = {}
    for idx, val in values.items():
        col = master.columns[idx]
        for s in col.occupied:
            seat_usage[(col.gid, s)] = seat_usage.get((col.gid, s), 0.0) + val
        for (gid, hostnum), s in col.assignment:
            pax_usage[(gid, hostnum, s)] = pax_usage.get((gid, hostnum, s), 0.0) + val
    ruled_seat = {(r[1], r[2]) for r in rules if r[0] == 'seat'}
    ruled_pax = {(r[1], r[2], r[3]) for r in rules if r[0] == 'pax'}
    best, key = 1.0, None
    for k, f in seat_usage.items():
        if k in ruled_seat or f < 1e-6 or f > 1 - 1e-6:
            continue
        if abs(f - 0.5) < best:
            best, key = abs(f - 0.5), k
    if key is not None:
        return ('seat', key[0], key[1], True), False
    for k, f in pax_usage.items():
        if k in ruled_pax or f < 1e-6 or f > 1 - 1e-6:
            continue
        if abs(f - 0.5) < best:
            best, key = abs(f - 0.5), k
    if key is not None:
        return ('pax', key[0], key[1], key[2], True), False
    return None, False


def branch_and_price(
    inst: Instance, master: Master, pricers: Dict[int, GroupPricer], executor: ThreadPoolExecutor,
    *, root_cost: float, incumbent_cost: float, incumbent: Optional[List[Column]],
    deadline: float, cfg: Dict[str, Any], log: Callable[[str], None],
    validate: Callable[[List[Column]], Tuple[bool, float]],
) -> Dict[str, Any]:
    """validate(columns) -> (合法?, 成本=-评估器软分)。"""
    node_limit = int(cfg.get("bp_node_limit", 200))
    int_time = float(cfg.get("bp_integer_time_limit", 5.0))
    frontier: List[Node] = [Node(root_cost, 0, 0, ())]
    serial = 1
    processed = 0
    open_uncertified: List[float] = []
    stats = {"nodes": 0, "columns_added": 0, "incumbent_updates": 0, "pruned": 0,
             "lp_infeasible_uncertified": 0, "fractional_unbranchable": 0, "timeout_uncertified": 0}
    best_cost, best_cols = incumbent_cost, incumbent

    def apply_rules(rules: Sequence[Rule]) -> None:
        for col, var in zip(master.columns, master.column_vars):
            var.UB = GRB.INFINITY if _column_respects(col, rules) else 0.0
        master.model.update()

    while frontier and processed < node_limit and time.perf_counter() < deadline:
        node = heapq.heappop(frontier)
        if node.bound_cost >= best_cost - TOL:
            stats["pruned"] += 1
            continue
        processed += 1
        stats["nodes"] += 1
        apply_rules(node.rules)
        node_cost: Optional[float] = None
        node_certified = False
        node_infeasible_certified = False
        lp_failed = False
        escalation = 1.0
        while time.perf_counter() < deadline:
            status, lp_cost, art = master.solve_lp()
            if status != "optimal":
                if master.phase_one:
                    lp_failed = True      # Phase I 带人工列本不应不可行：视为求解失败
                    break
                # 受限主问题不可行 ≠ 节点不可行：进入节点级 Phase I，用 Phase I 对偶定价
                master.set_phase(True)
                continue
            if master.phase_one and art <= TOL:
                master.set_phase(False)
                continue
            phase_one = master.phase_one
            duals = master.duals()
            remaining = max(0.05, deadline - time.perf_counter())
            limit = max(0.05, min(float(cfg.get("pricing_time_limit", 60.0)) * escalation, remaining / 2))

            def price(g, _duals=duals, _limit=limit, _rules=node.rules, _p1=phase_one):
                return pricers[g.gid].price_with_rules(_duals, _pricing_rules(_rules, g.gid), phase_one=_p1, time_limit=_limit)

            outs = [o for o in executor.map(price, inst.groups) if o is not None]
            if len(outs) != len(inst.groups):
                break
            cols = [c for o in outs for c in o.columns
                    if _column_respects(c, node.rules) and reduced_cost(c, duals, phase_one) < -TOL]
            before = len(master.columns)
            master.add_columns(cols)
            for col, var in zip(master.columns[before:], master.column_vars[before:]):
                var.UB = GRB.INFINITY if _column_respects(col, node.rules) else 0.0
            master.model.update()
            added = len(master.columns) - before
            stats["columns_added"] += added
            if added == 0:
                if all(o.certified for o in outs):
                    if phase_one:
                        node_infeasible_certified = True   # 人工列仍为正且无负列：节点不可行
                    else:
                        node_cost, node_certified = lp_cost, True
                    break
                escalation *= 2.0
                if time.perf_counter() + 1.0 >= deadline:
                    break
        if master.phase_one:
            master.set_phase(False)
        if lp_failed:
            stats["lp_infeasible_uncertified"] += 1
            open_uncertified.append(node.bound_cost)
            log(f"node {processed} depth={node.depth}: master LP failed -> uncertified")
            continue
        if node_infeasible_certified:
            stats["infeasible_certified"] = stats.get("infeasible_certified", 0) + 1
            log(f"node {processed} depth={node.depth}: certified infeasible (Phase I)")
            continue
        if not node_certified:
            stats["timeout_uncertified"] += 1
            open_uncertified.append(node.bound_cost)
            log(f"node {processed} depth={node.depth}: pricing not certified within time")
            continue
        bound = node_cost
        log(f"node {processed} depth={node.depth} rules={len(node.rules)} lp={bound:.4f} incumbent={best_cost:.4f} frontier={len(frontier)}")
        if bound >= best_cost - TOL:
            stats["pruned"] += 1
            continue
        # 节点整数启发式（列池 MIP，受当前规则约束）；结果经独立评估器验证
        int_status, selected, int_cost = master.solve_integer(min(int_time, max(0.5, deadline - time.perf_counter())))
        if selected is not None:
            ok, true_cost = validate(selected)
            if ok and true_cost < best_cost - TOL:
                best_cost, best_cols = true_cost, selected
                stats["incumbent_updates"] += 1
                log(f"node {processed}: incumbent improved to cost {best_cost:.4f}")
        if bound >= best_cost - TOL:
            stats["pruned"] += 1
            continue
        master.solve_lp()
        choice, integral = _choose_branch(master, node.rules)
        if integral:
            selected = [master.columns[i] for i, v in master.lp_values().items() if v > 0.5]
            if len({c.gid for c in selected}) == len(inst.groups):
                ok, true_cost = validate(selected)
                if ok and true_cost < best_cost - TOL:
                    best_cost, best_cols = true_cost, selected
                    stats["incumbent_updates"] += 1
            # LP 整数点：该节点最优值就是 bound（若验证失败，说明主问题与评估器不一致，按未认证处理）
            if not ok:
                open_uncertified.append(bound)
                log(f"node {processed}: integral LP failed evaluator validation -> uncertified")
            continue
        if choice is None:
            stats["fractional_unbranchable"] += 1
            open_uncertified.append(bound)
            log(f"node {processed}: fractional LP with no branchable usage -> uncertified")
            continue
        if choice[0] == 'seat':
            _, gid, seat, _ = choice
            left, right = ('seat', gid, seat, True), ('seat', gid, seat, False)
        else:
            _, gid, hostnum, seat, _ = choice
            left, right = ('pax', gid, hostnum, seat, True), ('pax', gid, hostnum, seat, False)
        heapq.heappush(frontier, Node(bound, serial, node.depth + 1, node.rules + (left,))); serial += 1
        heapq.heappush(frontier, Node(bound, serial, node.depth + 1, node.rules + (right,))); serial += 1

    for var in master.column_vars:
        var.UB = GRB.INFINITY
    master.model.update()
    open_bounds = [n.bound_cost for n in frontier] + open_uncertified
    all_certified = not open_uncertified
    proven = (not frontier) and all_certified and best_cols is not None
    # 全局成本下界：开放/未认证节点界的最小值；若无则 incumbent 即最优
    if open_bounds:
        global_cost_lb = min(min(open_bounds), best_cost if best_cols is not None else math.inf)
    else:
        global_cost_lb = best_cost if best_cols is not None else root_cost
    return {
        "proven": proven, "incumbent_cost": best_cost, "incumbent": best_cols,
        "global_cost_lower_bound": global_cost_lb,
        "open_nodes": len(frontier), "stats": stats, "all_certified": all_certified,
        "node_limit_reached": processed >= node_limit and bool(frontier),
    }
