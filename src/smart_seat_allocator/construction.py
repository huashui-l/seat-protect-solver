"""Regret construction over complete group patterns using bitset resources."""

from __future__ import annotations

import math
import random
import time
from collections import defaultdict

import numpy as np


def complete_residual(factory, group, free, deadline, accept=None):
    """Fit a group to compact shapes made from the currently free resources."""
    inst = factory.inst
    seats = sorted(s for s in free if inst.cabin_of_seat[s] == group.cabin)
    demand = group.size + sum(2 * p.both_side + p.single_side for p in group.passengers)
    if len(seats) < demand or time.perf_counter() >= deadline:
        return None
    coordinates = np.asarray([(inst.x(s), inst.y(s)) for s in seats])
    dx = abs(coordinates[:, None, 0] - coordinates[None, :, 0])
    dy = abs(coordinates[:, None, 1] - coordinates[None, :, 1])
    indices = set()
    fixed = {
        seats.index(p.fixed_seat) for p in group.passengers if p.fixed_seat in seats
    }
    for aspect in (0.5, 1.0, 2.0):
        nearest = np.argsort(aspect * dx + dy, axis=1, kind="stable")
        for row in nearest:
            indices.add(
                tuple(
                    sorted(
                        fixed
                        | set([k for k in row if k not in fixed][: demand - len(fixed)])
                    )
                )
            )
    candidates = sorted(indices)
    array = np.asarray(candidates)
    costs = np.asarray(
        [[-p.score.get(s, -1e6) for s in seats] for p in group.passengers]
    )
    lower = costs[:, array].min(axis=2).sum(axis=0)
    coords = coordinates[array]
    deviations = abs(coords - coords.mean(axis=1)[:, None, :]).max(axis=1)
    lower += -inst.w_c * (
        inst.compact_x_factor * deviations[:, 0]
        + inst.compact_y_factor * deviations[:, 1]
    )
    best = None
    factory.deadline = deadline
    for j in np.argsort(lower, kind="stable")[:12]:
        if time.perf_counter() >= deadline:
            break
        for pattern in factory.fit(group, [seats[k] for k in candidates[j]]):
            if (accept is None or accept(pattern)) and (
                best is None or pattern.cost < best.cost
            ):
                best = pattern
    if best is not None:
        factory.add(best)
        factory.operator_counts["residual_seat_completion"] += 1
    return best


def construct(inst, pools, deadline, seed=42, factory=None):
    """Find an inexpensive legal MIP start without invoking another solver.

    Dynamic regret places groups with scarce alternatives first. Independent
    restarts vary resource prices; bitsets enforce protection and conditional
    SSR isolation exactly. Infant costs are recomputed for complete solutions.
    """

    def is_ordinary(group):
        return all(p.is_caregiver and not p.fixed_seat for p in group.passengers)

    if any(
        not pools[g.gid] and (factory is None or not is_ordinary(g))
        for g in inst.groups
    ):
        return None
    rng = random.Random(seed)
    seat_bits = {s: 1 << j for j, s in enumerate(inst.seat_ids)}
    keys = sorted(
        {
            (inst.location_of(s, kind), ssr)
            for s in inst.seat_ids
            for kind in ("row", "subrow")
            for ssr in inst.ssr_types
        }
    )
    key_bits = {k: 1 << j for j, k in enumerate(keys)}
    location_bits = defaultdict(int)
    for (loc, _ssr), bit in key_bits.items():
        location_bits[loc] |= bit

    def entry(pattern):
        return (
            pattern,
            sum(seat_bits[s] for s in pattern.resources),
            sum(key_bits[k] for k in pattern.counts),
            sum(key_bits[k] for k, n in pattern.counts.items() if n > 1),
            sum(location_bits[loc] for loc in pattern.flags),
        )

    entries = {}
    pressure = defaultdict(float)
    for g in inst.groups:
        ordered = sorted(pools[g.gid].values(), key=lambda p: p.cost)
        for p in ordered[:8]:
            for s in p.resources:
                pressure[s] += 1 / min(8, len(ordered))
        entries[g.gid] = [entry(p) for p in ordered]
    best, best_cost, attempts = None, math.inf, 0
    dfs_nodes = 0
    cabins = {g.cabin for g in inst.groups}
    capacity = sum(c in cabins for c in inst.cabin_of_seat.values())
    demand = sum(1 + 2 * p.both_side + p.single_side for p in inst.passengers.values())
    if demand >= 0.95 * capacity:
        search_deadline = (
            time.perf_counter() + max(0, deadline - time.perf_counter()) * 0.65
        )

        def pack(pending, used, present, duplicate, active, chosen):
            nonlocal dfs_nodes
            dfs_nodes += 1
            if not pending:
                return chosen
            if time.perf_counter() >= search_deadline:
                return None
            root, options, priority = None, None, (math.inf, 0)
            for gid in pending:
                available = []
                for e in entries[gid]:
                    if e[1] & used or (active | e[4]) & (
                        duplicate | e[3] | (present & e[2])
                    ):
                        continue
                    available.append(e)
                    if len(available) == 24:
                        break
                if not available:
                    return None
                key = (len(available), -inst.group_by_id[gid].size)
                if key < priority:
                    root, options, priority = gid, available, key
            for e in options[:8]:
                found = pack(
                    [gid for gid in pending if gid != root],
                    used | e[1],
                    present | e[2],
                    duplicate | e[3] | (present & e[2]),
                    active | e[4],
                    chosen + [e[0]],
                )
                if found is not None:
                    return found
                if time.perf_counter() >= search_deadline:
                    break
            return None

        packed = pack(list(entries), 0, 0, 0, 0, [])
        if packed:
            best = {k: s for p in packed for k, s in p.assigned.items()}
            occupied = {s: p.gid for p in packed for s in p.assigned.values()}
            best_cost = sum(p.cost for p in packed) - sum(
                inst.baby_pairs[u, v]
                for p in packed
                for u in p.infants
                for _, v in inst.baby_pairs_by_infant_seat.get(u, ())
                if v in occupied and occupied[v] != p.gid
            )
    while time.perf_counter() < deadline and attempts < 100:
        factor = (0.0, 0.4, 1.2, 3.0)[attempts % 4]
        attempts += 1
        ranked = {
            gid: sorted(
                options,
                key=lambda e: (
                    e[0].cost + factor * sum(pressure[s] for s in e[0].resources)
                ),
            )
            if factor
            else options
            for gid, options in entries.items()
        }
        ordinary = []
        pending = list(entries)
        if factory is not None:
            ordinary = [g for g in inst.groups if is_ordinary(g)]
            ordinary_ids = {g.gid for g in ordinary}
            pending = [gid for gid in pending if gid not in ordinary_ids]
        if attempts > 1:
            rng.shuffle(pending)
        used, present, duplicate, active = 0, 0, 0, 0
        chosen = []
        failed = False
        while pending and time.perf_counter() < deadline:
            root, root_options, priority = None, None, -math.inf
            for gid in pending:
                options = []
                for e in ranked[gid]:
                    if e[1] & used:
                        continue
                    doubles = duplicate | e[3] | (present & e[2])
                    if (active | e[4]) & doubles:
                        continue
                    options.append(e)
                    if len(options) == 2:
                        break
                if not options:
                    if factory is not None:
                        free = [s for s, bit in seat_bits.items() if not bit & used]

                        def compatible(
                            pattern, present=present, duplicate=duplicate, active=active
                        ):
                            e = entry(pattern)
                            return not (
                                (active | e[4]) & (duplicate | e[3] | (present & e[2]))
                            )

                        pattern = complete_residual(
                            factory, inst.group_by_id[gid], free, deadline, compatible
                        )
                        if pattern is not None:
                            e = entry(pattern)
                            entries[gid].append(e)
                            options.append(e)
                    if not options:
                        failed = True
                        break
                regret = (
                    abs(options[1][0].cost - options[0][0].cost)
                    if len(options) == 2
                    else 1e9
                )
                if attempts > 1:
                    regret = (regret + 0.1) * rng.uniform(0.5, 1.5)
                if regret > priority:
                    root, root_options, priority = gid, options, regret
            if failed:
                break
            e = root_options[0]
            pending.remove(root)
            chosen.append(e[0])
            used |= e[1]
            duplicate |= e[3] | (present & e[2])
            present |= e[2]
            active |= e[4]
        if factory is not None:
            partial = {k: s for p in chosen for k, s in p.assigned.items()}
            if len(partial) > len(factory.partial_start):
                factory.partial_start = partial
        if pending:
            continue
        for group in sorted(ordinary, key=lambda g: (-g.size, g.gid)):
            if time.perf_counter() >= deadline:
                failed = True
                break
            pattern = next((e[0] for e in ranked[group.gid] if not (e[1] & used)), None)
            if pattern is None:
                free = [s for s, bit in seat_bits.items() if not (bit & used)]
                pattern = complete_residual(factory, group, free, deadline)
            if pattern is None:
                failed = True
                break
            chosen.append(pattern)
            used |= sum(seat_bits[s] for s in pattern.resources)
        if failed:
            continue
        assignment = {k: s for p in chosen for k, s in p.assigned.items()}
        occupied = {s: p.gid for p in chosen for s in p.assigned.values()}
        cost = sum(p.cost for p in chosen) - sum(
            inst.baby_pairs[u, v]
            for p in chosen
            for u in p.infants
            for _, v in inst.baby_pairs_by_infant_seat.get(u, ())
            if v in occupied and occupied[v] != p.gid
        )
        if cost < best_cost:
            best, best_cost = assignment, cost
    return (
        (
            best,
            {
                "construction_attempts": attempts,
                "packing_nodes": dfs_nodes,
                "construction_cost": best_cost,
            },
        )
        if best is not None
        else None
    )
