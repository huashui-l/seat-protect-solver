"""Sparse floating-point MIP recombination for longer search budgets."""

from __future__ import annotations

import time
from collections import defaultdict

import highspy
import numpy as np
from scipy.sparse import csc_matrix


def recombine(inst, pools, deadline, incumbent=None, workers=1, seed=42):
    started = time.perf_counter()
    if started >= deadline or any(not pools[g.gid] for g in inst.groups):
        return None
    patterns = [p for g in inst.groups for p in pools[g.gid].values()]
    costs = [p.cost for p in patterns]
    rows = []
    lower = []
    upper = []

    def row(vs, lb=-np.inf, ub=np.inf):
        rows.append(vs)
        lower.append(lb)
        upper.append(ub)

    groups, resources, flags, counts, occupied, infants = (
        defaultdict(dict) for _ in range(6)
    )
    for j, p in enumerate(patterns):
        if j % 64 == 0 and time.perf_counter() >= deadline:
            return None
        groups[p.gid][j] = 1
        for seat in sorted(p.resources):
            resources[seat][j] = 1
        for loc in sorted(p.flags):
            flags[loc][j] = 1
        for key, n in sorted(p.counts.items()):
            counts[key][j] = n
        for seat in p.assigned.values():
            occupied[seat][j] = 1
        for seat in sorted(p.infants):
            infants[p.gid, seat][j] = 1
    for vs in groups.values():
        row(vs, 1, 1)
    for vs in resources.values():
        row(vs, ub=1)
    for loc, vs in flags.items():
        active = len(costs)
        costs.append(0)
        by_group = defaultdict(dict)
        for j in vs:
            by_group[patterns[j].gid][j] = 1
        for terms in by_group.values():
            row({**terms, active: -1}, ub=0)
        for ssr in inst.ssr_types:
            if counts[loc, ssr]:
                row(
                    {**counts[loc, ssr], active: inst.big_m(loc, ssr)},
                    ub=inst.big_m(loc, ssr) + 1,
                )
    for (gid, u), iv in infants.items():
        if time.perf_counter() >= deadline:
            return None
        for _, v in inst.baby_pairs_by_infant_seat.get(u, []):
            ov = {j: 1 for j in occupied[v] if patterns[j].gid != gid}
            if not ov:
                continue
            b = len(costs)
            costs.append(-inst.baby_pairs[u, v])
            row({**iv, **ov, b: -1}, ub=1)
            row({**iv, b: -1}, lb=0)
            row({**ov, b: -1}, lb=0)
    rr = []
    cc = []
    vv = []
    for i, terms in enumerate(rows):
        if i % 64 == 0 and time.perf_counter() >= deadline:
            return None
        for j, value in terms.items():
            rr.append(i)
            cc.append(j)
            vv.append(value)
    matrix = csc_matrix((vv, (rr, cc)), shape=(len(rows), len(costs)))
    lp = highspy.HighsLp()
    lp.num_col_ = len(costs)
    lp.num_row_ = len(rows)
    lp.col_cost_ = np.asarray(costs)
    lp.col_lower_ = np.zeros(len(costs))
    lp.col_upper_ = np.ones(len(costs))
    lp.row_lower_ = np.asarray(lower)
    lp.row_upper_ = np.asarray(upper)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = matrix.indptr
    lp.a_matrix_.index_ = matrix.indices
    lp.a_matrix_.value_ = matrix.data
    lp.integrality_ = [highspy.HighsVarType.kInteger] * len(patterns) + [
        highspy.HighsVarType.kContinuous
    ] * (len(costs) - len(patterns))
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("threads", 1)
    h.setOptionValue("random_seed", seed)
    h.setOptionValue("mip_rel_gap", 0.0)
    h.setOptionValue("mip_abs_gap", 1e-6)
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return None
    h.setOptionValue("time_limit", remaining)
    h.passModel(lp)
    if time.perf_counter() >= deadline:
        return None
    if incumbent:
        selected = []
        for g in inst.groups:
            choice = next(
                (
                    j
                    for j in groups[g.gid]
                    if all(
                        incumbent.get(k) == s for k, s in patterns[j].assigned.items()
                    )
                ),
                None,
            )
            if choice is None:
                break
            selected.append(choice)
        else:
            values = np.zeros(len(patterns))
            values[selected] = 1
            h.setSolution(
                len(patterns), np.arange(len(patterns), dtype=np.int32), values
            )
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return None
    h.setOptionValue("time_limit", remaining)
    h.run()
    solution = h.getSolution()
    info = h.getInfo()
    if (
        not solution.value_valid
        or info.primal_solution_status != highspy.SolutionStatus.kSolutionStatusFeasible
    ):
        return None
    chosen = [p for j, p in enumerate(patterns) if solution.col_value[j] > 0.5]
    if len(chosen) != len(inst.groups):
        return None
    assignment = {k: s for p in chosen for k, s in p.assigned.items()}
    return assignment, {
        "pool_size": len(patterns),
        "master_seconds": time.perf_counter() - started,
        "pool_cost": info.objective_function_value,
        "pool_bound": info.mip_dual_bound,
        "backend": "highs",
    }
