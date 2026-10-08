"""Joint CP-SAT recombination; pool bounds never imply global optimality."""

from __future__ import annotations

import time
from collections import defaultdict


def recombine(inst, pools, deadline, incumbent=None, workers=8, seed=42):
    from ortools.sat.python import cp_model

    started = time.perf_counter()
    if started >= deadline or any(not pools[g.gid] for g in inst.groups):
        return None
    pats = [p for g in inst.groups for p in pools[g.gid].values()]
    m = cp_model.CpModel()
    y = [m.new_bool_var("") for _ in pats]
    groups, res, flags, counts, occupied, infants = (
        defaultdict(list) for _ in range(6)
    )
    costs = [round(p.cost * 1_000_000) * y[j] for j, p in enumerate(pats)]
    for j, p in enumerate(pats):
        if j % 64 == 0 and time.perf_counter() >= deadline:
            return None
        var = y[j]
        groups[p.gid].append(var)
        for s in sorted(p.resources):
            res[s].append(var)
        for loc in sorted(p.flags):
            flags[loc].append(var)
        for key, n in sorted(p.counts.items()):
            counts[key].append(n * var)
        for s in p.assigned.values():
            occupied[s].append((p.gid, var))
        for s in sorted(p.infants):
            infants[p.gid, s].append(var)
    for vs in groups.values():
        m.add_exactly_one(vs)
    for vs in res.values():
        m.add_at_most_one(vs)
    for loc, vs in flags.items():
        active = m.new_bool_var("")
        m.add_max_equality(active, vs)
        for ssr in inst.ssr_types:
            if counts[loc, ssr]:
                m.add(sum(counts[loc, ssr]) <= 1).only_enforce_if(active)
    for (gid, u), iv in infants.items():
        if time.perf_counter() >= deadline:
            return None
        for _, v in inst.baby_pairs_by_infant_seat.get(u, []):
            ov = [x for g, x in occupied[v] if g != gid]
            if not ov:
                continue
            b = m.new_bool_var("")
            m.add(b >= sum(iv) + sum(ov) - 1)
            m.add(b <= sum(iv))
            m.add(b <= sum(ov))
            costs.append(round(-inst.baby_pairs[u, v] * 1_000_000) * b)
    m.minimize(sum(costs))
    if incumbent:
        chosen = set()
        for j, p in enumerate(pats):
            match = p.gid not in chosen and all(
                incumbent.get(k) == s for k, s in p.assigned.items()
            )
            if match:
                chosen.add(p.gid)
            m.add_hint(y[j], int(match))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return None
    build_seconds = time.perf_counter() - started
    solver.parameters.max_time_in_seconds = remaining
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assignment = {
        k: s
        for j, p in enumerate(pats)
        if solver.value(y[j])
        for k, s in p.assigned.items()
    }
    return assignment, {
        "pool_size": len(pats),
        "master_seconds": time.perf_counter() - started,
        "master_build_seconds": build_seconds,
        "master_search_seconds": solver.wall_time,
        "master_workers": workers,
        "pool_cost": solver.objective_value / 1e6,
        "pool_bound": solver.best_objective_bound / 1e6,
    }
