"""Vectorized exact-cost adult relocations and exchanges.

Special passengers and their exclusive protected seats stay fixed in this
neighborhood. Caregiver support is checked before every move; a passenger swap
within a group preserves both its seat set and all caregiver coverage.
"""

from __future__ import annotations

import time

import numpy as np


def descend(inst, factory, assignment, deadline):
    movable = [
        p for p in inst.passengers.values() if p.is_caregiver and not p.fixed_seat
    ]
    if not movable or time.perf_counter() >= deadline:
        return assignment, {"adult_relocations": 0, "adult_exchanges": 0}
    seats = inst.seat_ids
    index = {s: j for j, s in enumerate(seats)}
    xy = np.asarray([(inst.x(s), inst.y(s)) for s in seats])
    factors = -inst.w_c * np.asarray([inst.compact_x_factor, inst.compact_y_factor])
    current = dict(assignment)
    resources = set().union(*(p.resources for p in factory.remember(current)))
    protected = resources - set(current.values())
    babies = [p for p in inst.passengers.values() if p.is_infant]
    score = np.asarray(
        [
            [
                p.score.get(s, -np.inf)
                + sum(
                    inst.baby_pairs.get((current[b.key], s), 0)
                    for b in babies
                    if b.gid != p.gid
                )
                for s in seats
            ]
            for p in movable
        ]
    )
    gids = np.asarray([p.gid for p in movable])
    same = gids[:, None] == gids[None, :]
    stats = {"adult_relocations": 0, "adult_exchanges": 0}
    gains = np.empty_like(score)
    dirty = set(gids)
    care = {
        g.gid: [
            (p, set(inst.caregiver_neighbors(p, current[p.key])))
            for p in g.passengers
            if p.needs_caregiver
        ]
        for g in inst.groups
    }
    for _ in range(300):
        if time.perf_counter() >= deadline:
            break
        at = np.asarray([index[current[p.key]] for p in movable])
        linear = score - score[np.arange(len(movable)), at, None]
        for i, p in enumerate(movable):
            if p.gid not in dirty:
                continue
            group = inst.group_by_id[p.gid]
            group_seats = [current[q.key] for q in group.passengers]
            coords = xy[[index[s] for s in group_seats]]
            old = float(
                (np.abs(coords - coords.mean(axis=0)).max(axis=0) * factors).sum()
            )
            rest = np.asarray(
                [index[current[q.key]] for q in group.passengers if q.key != p.key],
                dtype=int,
            )
            if len(rest):
                mean = (xy[rest].sum(axis=0) + xy) / group.size
                low = np.minimum(xy[rest].min(axis=0), xy)
                high = np.maximum(xy[rest].max(axis=0), xy)
                cost = (np.maximum(mean - low, high - mean) * factors).sum(axis=1)
            else:
                cost = np.zeros(len(seats))
            gains[i] = linear[i] + old - cost
            # Removing the last supporting adult requires replacing that support.
            for _special, neighbors in care[p.gid]:
                supporters = [
                    q
                    for q in group.passengers
                    if q.is_caregiver and current[q.key] in neighbors
                ]
                if len(supporters) == 1 and supporters[0].key == p.key:
                    allowed = np.zeros(len(seats), dtype=bool)
                    allowed[[index[s] for s in neighbors]] = True
                    gains[i, ~allowed] = -np.inf
        dirty.clear()
        exchanges = gains[:, at] + gains[:, at].T
        direct = linear[:, at] + linear[:, at].T
        exchanges[same] = direct[same]
        np.fill_diagonal(exchanges, -np.inf)
        i, j = np.unravel_index(np.argmax(exchanges), exchanges.shape)
        best_gain = exchanges[i, j]
        occupied = set(current.values())
        free = [
            k for k, s in enumerate(seats) if s not in occupied and s not in protected
        ]
        relocation = False
        if free:
            fi, fj = np.unravel_index(
                np.argmax(gains[:, free]), (len(movable), len(free))
            )
            if gains[fi, free[fj]] > best_gain:
                i, j, best_gain, relocation = fi, free[fj], gains[fi, free[fj]], True
        if best_gain <= 1e-7:
            break
        p = movable[i]
        dirty.add(p.gid)
        if relocation:
            current[p.key] = seats[j]
            stats["adult_relocations"] += 1
        else:
            q = movable[j]
            current[p.key], current[q.key] = current[q.key], current[p.key]
            dirty.add(q.gid)
            stats["adult_exchanges"] += 1
    return current, stats
