"""Anytime pattern recombination and adaptive CP-SAT large-neighborhood search.

Only model parameters and the input passengers are read. Legacy allocations,
benchmark reference scores and generator witnesses are never solver inputs.
"""

from __future__ import annotations

import math
import random
import time
from collections import defaultdict

from ortools.sat.python import cp_model

from src import allocation_evaluator as evaluator
from src.seat_model import Instance

from .capacity import infant_capacity
from .construction import construct
from .descent import descend
from .highs_master import recombine as recombine_highs
from .master import recombine
from .model import SeatModel
from .patterns import PatternFactory


def candidate_domains(inst, width=3):
    result = {}
    for g in inst.groups:
        rows = {inst.seat_map[s]["row"] for p in g.passengers for s in p.domain}
        desirability = {
            r: sum(
                max(
                    (
                        p.score[s]
                        for s in p.domain
                        if abs(inst.seat_map[s]["row"] - r) <= 1
                    ),
                    default=-10000,
                )
                for p in g.passengers
            )
            for r in rows
        }
        preferred = set(sorted(rows, key=lambda r: (-desirability[r], r))[:width])
        for p in g.passengers:
            seats = set(sorted(p.domain, key=lambda s: (-p.score[s], s))[: width * 6])
            seats.update(s for s in p.domain if inst.seat_map[s]["row"] in preferred)
            result[p.key] = sorted(seats)
    return result


def _solve_joint(
    new_seats,
    old_seats,
    groups,
    weights,
    config,
    *,
    time_limit=20.0,
    workers=8,
    seed=42,
    on_improvement=None,
):
    """Return a validated incumbent within a cooperative wall-clock budget.

    Initialization, construction, optimization and final auditing all consume the
    same budget. Native solver shutdown/presolve and Python model construction
    are not preemptible; ``wall_seconds`` records the actual elapsed time.
    ``proven_optimal`` requires a FULL-domain proof within numerical tolerance;
    optimizing a generated pool or neighborhood never supplies that proof.
    """
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("time_limit must be finite and positive")
    if not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    start = time.perf_counter()
    warm_budget = min(time_limit, 5.0)
    deadline = start + time_limit - min(0.2, time_limit * 0.03)
    inst = Instance(new_seats, old_seats, groups, weights, config)
    factory = PatternFactory(inst)
    rng = random.Random(seed)
    best, best_score, upper = {}, -math.inf, None
    history, stages = [], []
    certificate = infant_capacity(inst)
    proven, infeasible = False, certificate is not None

    def consider(
        assignment, phase, model_cost=None, tolerance=1e-5, cost_is_upper_bound=False
    ):
        nonlocal best, best_score
        v, u, _ = evaluator.count_hard_constraint_violations(
            new_seats, groups, assignment, config
        )
        score, _ = evaluator.calculate_soft_score(
            new_seats, old_seats, groups, assignment, weights, config
        )
        if v or u:
            raise RuntimeError(
                f"Model/evaluator feasibility mismatch: {v} violations, {u} unassigned"
            )
        discrepancy = model_cost + score if model_cost is not None else 0.0
        if discrepancy < -tolerance or (
            not cost_is_upper_bound and discrepancy > tolerance
        ):
            raise RuntimeError(
                f"Model/evaluator objective mismatch in {phase}: model cost {model_cost}, evaluator cost {-score}, tolerance {tolerance}"
            )
        if upper is not None and score > upper + 1e-6:
            raise RuntimeError("A feasible score exceeds the certified global bound")
        if score > best_score + 1e-8:
            best, best_score = dict(assignment), score
            point = {
                "seconds": time.perf_counter() - start,
                "score": score,
                "phase": phase,
            }
            history.append(point)
            if on_improvement is not None:
                on_improvement(dict(point))
        return discrepancy

    def run(domains, seconds, phase, feasibility_only=False):
        nonlocal upper, proven, infeasible
        if time.perf_counter() >= deadline:
            return None
        try:
            model = SeatModel(inst, domains, best or factory.partial_start, deadline)
        except TimeoutError:
            stages.append({"phase": phase, "status": "BUILD_DEADLINE"})
            return None
        if feasibility_only:
            model.model.clear_objective()
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return None
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = min(remaining, seconds)
        solver.parameters.num_search_workers = workers
        solver.parameters.random_seed = seed
        st = solver.solve(model.model)
        stages.append(
            {
                "phase": phase,
                "status": solver.status_name(st),
                "variables": len(model.x),
                "seconds": solver.wall_time,
            }
        )
        if st == cp_model.MODEL_INVALID:
            raise ValueError(f"Invalid CP-SAT model: {solver.solution_info()}")
        if model.full_domain:
            if st == cp_model.INFEASIBLE:
                infeasible = True
            elif not feasibility_only and st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                ub = -model.lower_bound(solver)
                if math.isfinite(ub):
                    upper = ub if upper is None else min(upper, ub)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            slack = consider(
                model.assignment(solver),
                phase,
                None if feasibility_only else model.cost(solver),
                model.rounding_error + 1e-6,
                cost_is_upper_bound=True,
            )
            stages[-1]["objective_epigraph_slack"] = max(0.0, slack)
            # Explicit tolerance accounts for coefficient rounding and arithmetic.
            proven = upper is not None and upper - best_score <= 1e-6
        return st

    def combine(factory, until, phase):
        now = time.perf_counter()
        available = min(deadline, until) - now
        if available <= 0:
            return
        use_highs = (
            phase != "patterns" and available >= 10 and deadline - now >= available + 10
        )
        backend = recombine_highs if use_highs else recombine
        pools = factory.pools
        if not use_highs:
            # A short run cannot search tens of thousands of pattern binaries.
            # Retain good placements plus row coverage and the exact incumbent.
            remembered = factory.remember(best) if best else []
            pools = {}
            demand = sum(
                1 + 2 * p.both_side + p.single_side for p in inst.passengers.values()
            )
            cabins = {g.cabin for g in inst.groups}
            capacity = sum(c in cabins for c in inst.cabin_of_seat.values())
            dense = demand >= 0.95 * capacity
            for group in inst.groups:
                ranked = sorted(factory.pools[group.gid].values(), key=lambda p: p.cost)
                by_row = {}
                for p in ranked:
                    row = min(inst.seat_map[s]["row"] for s in p.resources)
                    region = (
                        (
                            row,
                            min(
                                inst.topology.subrow[s][1]
                                for s in p.resources
                                if inst.seat_map[s]["row"] == row
                            ),
                        )
                        if dense
                        else row
                    )
                    by_row.setdefault(region, p)
                cap = 96 if dense else 48
                chosen = ranked[:cap] + list(by_row.values())[:cap]
                chosen += [p for p in remembered if p.gid == group.gid]
                pools[group.gid] = {id(p): p for p in chosen}
        candidate = backend(
            inst,
            pools,
            min(deadline, until),
            best,
            workers=1
            if time_limit >= 30 and phase != "patterns" and not use_highs
            else workers,
            seed=seed,
        )
        if candidate:
            assignment, stats = candidate
            # Every coefficient in the pattern master is rounded to 1e-6.
            tolerance = (
                len(inst.groups) + len(inst.baby_pairs) * max(1, len(inst.groups))
            ) * 0.5e-6 + 1e-6
            consider(assignment, phase, stats["pool_cost"], tolerance)
            stages.append({"phase": phase, **stats})

    def polish(seconds=0.15):
        if not best or time.perf_counter() >= deadline:
            return
        assignment, stats = descend(
            inst, factory, best, min(deadline, time.perf_counter() + seconds)
        )
        consider(assignment, "adult_descent")
        for operator, count in stats.items():
            factory.operator_counts[operator] += count

    if not inst.passengers:
        consider({}, "empty")
        upper, proven = 0.0, True
    elif len(inst.passengers) <= 30 and not infeasible:
        run(None, time_limit * 0.65, "full_model")

    if not proven and not infeasible:
        generation_deadline = min(deadline, time.perf_counter() + warm_budget * 0.25)
        for index, group in enumerate(inst.groups):
            now = time.perf_counter()
            share = max(0, (generation_deadline - now) / (len(inst.groups) - index))
            factory.generate(
                group, now + share, limit=12, hard_deadline=generation_deadline
            )
        for index, group in enumerate(inst.groups):
            now = time.perf_counter()
            if now >= generation_deadline:
                break
            share = (generation_deadline - now) / (len(inst.groups) - index)
            factory.generate(
                group,
                now + share,
                limit=60,
                hard_deadline=generation_deadline,
            )
        stages.append(
            {
                "phase": "pattern_generation",
                "pool_size": sum(len(p) for p in factory.pools.values()),
                "empty_groups": [g for g, p in factory.pools.items() if not p],
            }
        )
        if best:
            factory.remember(best)
        constructed = construct(
            inst,
            factory.pools,
            min(deadline, time.perf_counter() + min(0.3, time_limit * 0.06)),
            seed=seed,
            factory=factory,
        )
        if constructed:
            assignment, stats = constructed
            consider(assignment, "regret_construction", stats["construction_cost"])
            stages.append({"phase": "regret_construction", **stats})
        combine(factory, start + max(0.5, warm_budget * 0.45), "patterns")
        if not best:
            run(
                candidate_domains(inst, 4),
                min(2.0, time_limit * 0.15),
                "sparse_recovery",
            )
        if not best and not infeasible:
            run(
                None,
                max(0.1, (deadline - time.perf_counter()) * 0.65),
                "feasibility_recovery",
                True,
            )
        if not best and not infeasible:
            run(
                None,
                max(0.1, deadline - time.perf_counter()),
                "feasibility_recovery",
                True,
            )
        polish(0.3)
        if best and time_limit >= 10 and deadline - time.perf_counter() > 4:
            enrichment_deadline = min(deadline - 3, time.perf_counter() + 2)
            for index, group in enumerate(inst.groups):
                now = time.perf_counter()
                if now >= enrichment_deadline:
                    break
                share = (enrichment_deadline - now) / (len(inst.groups) - index)
                factory.generate(
                    group, now + share, limit=180, hard_deadline=enrichment_deadline
                )
        if best and deadline - time.perf_counter() > 0.8:
            factory.refine(
                best,
                min(deadline - 0.4, time.perf_counter() + min(1.5, time_limit * 0.1)),
            )
            now = time.perf_counter()
            remaining = deadline - now
            # Full-pool MIP startup/presolve is expensive and cannot always
            # interrupt promptly. Use it once, with ample global time left;
            # short and tail recombinations stay in the compact CP-SAT pool.
            master_budget = (
                min(12.0, remaining * 0.25)
                if remaining >= 40
                else min(3.0, time_limit * 0.12)
            )
            combine(factory, now + master_budget, "recombine")
            polish()

    preferred = candidate_domains(inst, 6) if best and not proven else {}
    iteration, stagnation = 0, 0
    while best and not proven and deadline - time.perf_counter() > 0.1:
        iteration += 1
        loss = {}
        for g in inst.groups:
            optimistic = min(
                (p.cost for p in factory.pools[g.gid].values()),
                default=-sum(max(p.score.values(), default=0) for p in g.passengers),
            )
            loss[g.gid] = -inst.column_score(g, best) - optimistic
        ordered = sorted(inst.groups, key=lambda g: (-loss[g.gid], g.gid))
        # Alternate regret-driven roots and full group coverage; intrinsically
        # expensive, already optimal groups must not starve the other roots.
        if iteration % 3 == 0:
            root = inst.groups[(iteration // 3 - 1) % len(inst.groups)]
        else:
            root = ordered[min(stagnation // 2, len(ordered) - 1)]
        selected = {root.gid}
        target = max(root.size + 4, 12 + (stagnation // 5 % 5) * 6)
        if iteration % 4 == 0:
            order = list(inst.groups)
            rng.shuffle(order)
        else:
            candidates = sorted(factory.pools[root.gid].values(), key=lambda p: p.cost)[
                :5
            ]
            desired = (
                (
                    set().union(*(p.resources for p in candidates))
                    if iteration % 2
                    else candidates[(iteration // 2) % len(candidates)].resources
                )
                if candidates
                else {s for p in root.passengers for s in preferred[p.key]}
            )
            center = sum(inst.y(best[p.key]) for p in root.passengers) / root.size
            resource_sets = {p.gid: p.resources for p in factory.remember(best)}
            order = sorted(
                inst.groups,
                key=lambda g: (
                    g.cabin != root.cabin,
                    -len(resource_sets[g.gid] & desired),
                    abs(
                        sum(inst.y(best[p.key]) for p in g.passengers) / g.size - center
                    ),
                    g.gid,
                ),
            )
        size = root.size
        for g in order:
            if g.gid in selected or g.cabin != root.cabin:
                continue
            if size >= target:
                break
            selected.add(g.gid)
            size += g.size
        locked = {
            best[p.key] for p in inst.passengers.values() if p.gid not in selected
        }
        released = {best[p.key] for p in inst.passengers.values() if p.gid in selected}
        # Regular full-domain repairs can escape an initially truncated candidate
        # set; all fixed passengers and their protection remain in the model.
        expand = iteration % 7 == 0 and size <= 20
        domains = {
            p.key: (
                [
                    s
                    for s in p.domain
                    if s not in locked
                    and (expand or s in released or s in preferred[p.key])
                ]
                if p.gid in selected
                else [best[p.key]]
            )
            for p in inst.passengers.values()
        }
        previous = best_score
        run(domains, 0.15 if size < 20 else 0.45, "lns")
        if best_score > previous + 1e-8:
            polish(0.05)
        stagnation = stagnation + 1 if best_score <= previous + 1e-8 else 0
        if iteration % 30 == 0 and deadline - time.perf_counter() > 3:
            factory.refine(
                best, time.perf_counter() + min(1.0, time_limit * 0.04), limit=100
            )
            combine(
                factory, time.perf_counter() + min(2.0, time_limit * 0.1), "recombine"
            )

    v, u, detail = evaluator.count_hard_constraint_violations(
        new_seats, groups, best, config
    )
    score, parts = evaluator.calculate_soft_score(
        new_seats, old_seats, groups, best, weights, config
    )
    complete = not (v or u)
    return {
        "status": "complete"
        if complete
        else ("infeasible" if infeasible else "no_solution"),
        "assigned_seats": best,
        "total_score": score if complete else None,
        "hard_violations": v,
        "unassigned": u,
        "violation_detail": detail,
        "upper_bound": upper,
        "proven_optimal": proven,
        "optimality_tolerance": 1e-6,
        "absolute_gap": max(0.0, upper - score)
        if complete and upper is not None
        else None,
        "score_detail": parts,
        "wall_seconds": time.perf_counter() - start,
        "requested_seconds": time_limit,
        "seed": seed,
        "workers": workers,
        "history": history,
        "stages": stages,
        "operator_patterns": dict(factory.operator_counts),
        "infeasibility_certificate": certificate,
    }


def solve(
    new_seats,
    old_seats,
    groups,
    weights,
    config,
    *,
    time_limit=20.0,
    workers=8,
    seed=42,
    on_improvement=None,
):
    """Solve independent cabins under one wall-clock budget, then audit together.

    Full seatmaps are retained in each subproblem, preserving coordinates and
    topology. Mixed-class target rows fall back to joint search, since empty
    seat protection could otherwise couple two cabins across their boundary.
    """
    started = time.perf_counter()
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("time_limit must be finite and positive")
    if not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    cabins, row_classes = defaultdict(list), defaultdict(set)
    for group in groups:
        cabins[evaluator.group_cabin_class(group)].append(group)
    for seat in new_seats:
        row_classes[seat["row"]].add(seat.get("seatClass", ""))
    if (
        len(cabins) <= 1
        or None in cabins
        or any(len(v) > 1 for v in row_classes.values())
    ):
        return _solve_joint(
            new_seats,
            old_seats,
            groups,
            weights,
            config,
            time_limit=time_limit,
            workers=workers,
            seed=seed,
            on_improvement=on_improvement,
        )
    # Validate duplicate identifiers and the complete input before decomposition.
    Instance(new_seats, old_seats, groups, weights, config)
    ordered = sorted(cabins, key=lambda c: sum(len(g["psrs"]) for g in cabins[c]))
    sizes = {c: sum(len(g["psrs"]) for g in cabins[c]) for c in ordered}
    assigned, results, history, stages = {}, {}, [], []
    prefix_score = 0.0
    deadline = started + time_limit - min(0.02, time_limit * 0.02)
    for index, cabin in enumerate(ordered):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        last = index == len(ordered) - 1
        budget = (
            remaining
            if last
            else min(
                remaining * 0.35,
                max(
                    0.25,
                    remaining * sizes[cabin] / sum(sizes[c] for c in ordered[index:]),
                ),
            )
        )
        offset = time.perf_counter() - started

        def improvement(
            point, last=last, offset=offset, prefix_score=prefix_score, cabin=cabin
        ):
            if (
                last
                and len(results) == len(ordered) - 1
                and all(r["status"] == "complete" for r in results.values())
            ):
                point = {
                    **point,
                    "seconds": offset + point["seconds"],
                    "score": prefix_score + point["score"],
                    "cabin": cabin,
                }
                history.append(point)
                if on_improvement is not None:
                    on_improvement(dict(point))

        result = _solve_joint(
            new_seats,
            old_seats,
            cabins[cabin],
            weights,
            config,
            time_limit=budget,
            workers=workers,
            seed=seed,
            on_improvement=improvement,
        )
        results[cabin] = result
        assigned.update(result["assigned_seats"])
        stages.extend({**s, "cabin": cabin} for s in result["stages"])
        if result["status"] == "complete":
            prefix_score += result["total_score"]
        if result["status"] == "infeasible":
            break
    violations, unassigned, detail = evaluator.count_hard_constraint_violations(
        new_seats, groups, assigned, config
    )
    score, parts = evaluator.calculate_soft_score(
        new_seats, old_seats, groups, assigned, weights, config
    )
    complete = violations == unassigned == 0
    upper = (
        sum(r["upper_bound"] for r in results.values())
        if len(results) == len(ordered)
        and all(r["upper_bound"] is not None for r in results.values())
        else None
    )
    if complete and abs(score - prefix_score) > 1e-6:
        raise RuntimeError(
            "Cabin decomposition objective differs from whole-aircraft evaluator"
        )
    if complete and upper is not None and score > upper + 1e-6:
        raise RuntimeError("A feasible score exceeds the combined cabin bound")
    return {
        "status": "complete"
        if complete
        else (
            "infeasible"
            if any(r["status"] == "infeasible" for r in results.values())
            else "no_solution"
        ),
        "assigned_seats": assigned,
        "total_score": score if complete else None,
        "hard_violations": violations,
        "unassigned": unassigned,
        "violation_detail": detail,
        "score_detail": parts,
        "upper_bound": upper,
        "proven_optimal": bool(
            complete and upper is not None and upper - score <= 1e-6
        ),
        "optimality_tolerance": 1e-6,
        "absolute_gap": max(0, upper - score)
        if complete and upper is not None
        else None,
        "wall_seconds": time.perf_counter() - started,
        "requested_seconds": time_limit,
        "seed": seed,
        "workers": workers,
        "history": history,
        "stages": stages,
        "cabin_runs": {
            c: {
                k: v
                for k, v in r.items()
                if k not in ("assigned_seats", "stages", "score_detail", "history")
            }
            for c, r in results.items()
        },
    }
