"""分支定价：在根 LP 之上按“同行组 g 是否占用座位 s”二分。

- 左子节点：组 g 必须占用 s（该组不含 s 的列禁用；定价加 Σ_p x_ps = 1）；
- 右子节点：组 g 不得占用 s（该组含 s 的列禁用；定价加 Σ_p x_ps = 0）。
分支只改变列的上界和定价的临时约束，不重建主问题。每个节点做完整
列生成直到该节点认证（所有组 Cutoff/Optimal）或时间耗尽；未认证节点
以其安全界（RMP + Σ min(0, lb_g)）作为节点界，无安全界则不能关闭。
全局 U = 已关闭/开放节点界的最大值；开放节点为空且全部认证时 proven。
"""
from __future__ import annotations

import heapq
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import gurobipy as gp
from gurobipy import GRB

from .instance import Instance
from .master import Master
from .pricing import Column, GroupPricer, TOL, reduced_cost

Rule = Tuple[int, str, bool]   # (gid, seat, must_use)


@dataclass(order=True)
class Node:
    bound_cost: float                 # 父节点 LP 成本（越小越优先）
    serial: int
    depth: int = field(compare=False)
    rules: Tuple[Rule, ...] = field(compare=False, default=())


def _column_respects(col: Column, rules: Sequence[Rule]) -> bool:
    occupied = set(col.occupied)
    for gid, seat, must in rules:
        if col.gid != gid:
            continue
        if must and seat not in occupied:
            return False
        if not must and seat in occupied:
            return False
    return True


def _choose_branch(master: Master, rules: Sequence[Rule]) -> Optional[Rule]:
    """选 Σ_{k∋s} y_gk 最接近 0.5 的 (g, s)。"""
    values = master.lp_values()
    usage: Dict[Tuple[int, str], float] = {}
    for idx, val in values.items():
        col = master.columns[idx]
        for s in col.occupied:
            usage[(col.gid, s)] = usage.get((col.gid, s), 0.0) + val
    ruled = {(g, s) for g, s, _ in rules}
    best, best_key = 1.0, None
    for key, f in usage.items():
        if key in ruled or f < 1e-6 or f > 1 - 1e-6:
            continue
        if abs(f - 0.5) < best:
            best, best_key = abs(f - 0.5), key
    return (best_key[0], best_key[1], True) if best_key else None


def branch_and_price(
    inst: Instance, master: Master, pricers: Dict[int, GroupPricer], executor: ThreadPoolExecutor,
    *, root_cost: float, incumbent_cost: float, incumbent: Optional[List[Column]],
    deadline: float, cfg: Dict[str, Any], log: Callable[[str], None],
) -> Dict[str, Any]:
    workers = max(1, int(cfg.get("pricing_workers", 4)))
    node_limit = int(cfg.get("bp_node_limit", 200))
    int_time = float(cfg.get("bp_integer_time_limit", 5.0))
    frontier: List[Node] = [Node(root_cost, 0, 0, ())]
    serial = 1
    processed = 0
    closed_bounds: List[float] = []          # 已关闭节点（剪枝或整数）的界，供全局 U 使用
    open_uncertified: List[float] = []       # 未认证节点的安全界
    all_certified = True
    stats = {"nodes": 0, "columns_added": 0, "incumbent_updates": 0, "pruned": 0, "infeasible": 0}
    best_cost, best_cols = incumbent_cost, incumbent

    while frontier and processed < node_limit and time.perf_counter() < deadline:
        node = heapq.heappop(frontier)
        if node.bound_cost >= best_cost - TOL:
            stats["pruned"] += 1
            closed_bounds.append(node.bound_cost)
            continue
        processed += 1
        stats["nodes"] += 1
        # 应用分支：禁用违反规则的列
        for col, var in zip(master.columns, master.column_vars):
            var.UB = GRB.INFINITY if _column_respects(col, node.rules) else 0.0
        master.model.update()
        node_cost: Optional[float] = None
        node_certified = False
        node_infeasible = False
        node_safe: Optional[float] = None
        escalation = 1.0
        while time.perf_counter() < deadline:
            status, lp_cost, art = master.solve_lp()
            if status != "optimal":
                node_infeasible = True; break
            duals = master.duals()
            remaining = deadline - time.perf_counter()
            limit = max(0.05, min(float(cfg.get("pricing_time_limit", 60.0)) * escalation, remaining / 2))

            def price(g):
                gid = g.gid
                extra = [(s, must) for gg, s, must in node.rules if gg == gid]
                return pricers[gid].price_with_rules(duals, extra, phase_one=False, time_limit=limit)

            outs = list(executor.map(price, inst.groups))
            cols = [c for o in outs for c in o.columns
                    if _column_respects(c, node.rules) and reduced_cost(c, duals, False) < -TOL]
            # 新列在其他节点也可能被使用；加入后按当前规则设上界
            before = len(master.columns)
            master.add_columns(cols)
            for col, var in zip(master.columns[before:], master.column_vars[before:]):
                var.UB = GRB.INFINITY if _column_respects(col, node.rules) else 0.0
            master.model.update()
            added = len(master.columns) - before
            stats["columns_added"] += added
            certified = all(o.certified for o in outs)
            lbs = [o.lower_bound for o in outs]
            if all(math.isfinite(v) for v in lbs):
                cand = lp_cost + sum(min(0.0, v) for v in lbs)
                node_safe = cand if node_safe is None else max(node_safe, cand)
            if added == 0:
                if certified:
                    node_cost, node_certified = lp_cost, True
                else:
                    escalation *= 2.0
                    if time.perf_counter() + 1.0 >= deadline:
                        break
                    continue
                break
        if node_infeasible:
            stats["infeasible"] += 1
            closed_bounds.append(math.inf)
            continue
        if not node_certified:
            all_certified = False
            if node_safe is not None:
                open_uncertified.append(node_safe)
            else:
                open_uncertified.append(node.bound_cost)
            log(f"node {processed} depth={node.depth}: not certified within time; safe={node_safe}")
            continue
        bound = node_cost
        log(f"node {processed} depth={node.depth} rules={len(node.rules)} lp={bound:.4f} incumbent={best_cost:.4f} frontier={len(frontier)}")
        if bound >= best_cost - TOL:
            stats["pruned"] += 1
            closed_bounds.append(bound)
            continue
        # 节点整数启发式（列池 MIP，受当前规则约束）
        int_status, selected, int_cost = master.solve_integer(min(int_time, max(0.5, deadline - time.perf_counter())))
        if selected is not None and int_cost is not None and int_cost < best_cost - TOL:
            best_cost, best_cols = int_cost, selected
            stats["incumbent_updates"] += 1
            log(f"node {processed}: incumbent improved to cost {best_cost:.4f}")
        if bound >= best_cost - TOL:
            stats["pruned"] += 1
            closed_bounds.append(bound)
            continue
        master.solve_lp()  # 恢复 LP 解以选分支
        choice = _choose_branch(master, node.rules)
        if choice is None:
            # LP 已整数（或无可分支对）：该节点解就是最优整数解
            closed_bounds.append(bound)
            selected = [master.columns[i] for i, v in master.lp_values().items() if v > 0.5]
            if len({c.gid for c in selected}) == len(inst.groups) and bound < best_cost - TOL:
                best_cost, best_cols = bound, selected
                stats["incumbent_updates"] += 1
            continue
        gid, seat, _ = choice
        heapq.heappush(frontier, Node(bound, serial, node.depth + 1, node.rules + ((gid, seat, True),))); serial += 1
        heapq.heappush(frontier, Node(bound, serial, node.depth + 1, node.rules + ((gid, seat, False),))); serial += 1

    # 复位列上界
    for var in master.column_vars:
        var.UB = GRB.INFINITY
    master.model.update()
    open_bounds = [n.bound_cost for n in frontier] + open_uncertified
    proven = not frontier and all_certified and processed <= node_limit
    # 全局成本下界 = 开放节点界与已关闭节点界的最小值（成本口径）
    global_lb_candidates = open_bounds + [b for b in closed_bounds if math.isfinite(b)] + ([best_cost] if best_cols else [])
    global_cost_lb = min(global_lb_candidates) if global_lb_candidates else root_cost
    if not all_certified and not open_uncertified:
        global_cost_lb = root_cost
    return {
        "proven": proven, "incumbent_cost": best_cost, "incumbent": best_cols,
        "global_cost_lower_bound": min(global_cost_lb, best_cost) if best_cols else global_cost_lb,
        "open_nodes": len(frontier), "stats": stats, "all_certified": all_certified,
    }
