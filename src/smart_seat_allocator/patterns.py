"""Compact resource shapes + exact matching of passenger identities.

Only special passengers are enumerated. Ordinary passengers are assigned by the
Hungarian algorithm, removing their factorial permutation symmetry.
"""

from __future__ import annotations

import itertools
import math
import time
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass
class Pattern:
    gid: int
    assigned: dict
    resources: frozenset
    cost: float
    flags: frozenset
    counts: dict
    infants: frozenset


def make_pattern(inst, group, assigned, protected=()):
    flags, counts = set(), defaultdict(int)
    for p in group.passengers:
        s = assigned[p.key]
        if p.ssr:
            for kind, flag in [("row", p.row_flag), ("subrow", p.subrow_flag)]:
                loc = inst.location_of(s, kind)
                counts[loc, p.ssr] += 1
                if flag:
                    flags.add(loc)
    if any(n > 1 and loc in flags for (loc, ssr), n in counts.items()):
        return None
    return Pattern(
        group.gid,
        dict(assigned),
        frozenset(set(assigned.values()) | set(protected)),
        -inst.column_score(group, assigned),
        frozenset(flags),
        dict(counts),
        frozenset(assigned[p.key] for p in group.passengers if p.is_infant),
    )


class PatternFactory:
    def __init__(self, inst):
        self.inst = inst
        self.cache = {}
        self.geometry_rankings = {}
        self.prepared = {}
        self.attempted = defaultdict(set)
        self.seat_index = {s: j for j, s in enumerate(inst.seat_ids)}
        self.position = {
            (s["row"], s["col"], s.get("seatClass", "")): s["seatId"]
            for s in inst.new_seats
        }
        self.operator_counts = defaultdict(int)
        self.deadline = math.inf
        self.pools = {g.gid: {} for g in inst.groups}
        self.partial_start = {}
        self.fixed_resources = {
            p.fixed_seat for p in inst.passengers.values() if p.fixed_seat
        }
        for p in inst.passengers.values():
            if p.fixed_seat and p.both_side:
                self.fixed_resources.update(inst.both_side_neighbors(p.fixed_seat))

    def shapes(self, cabin, demand):
        key = cabin, demand
        if key in self.cache:
            return self.cache[key]
        inst = self.inst
        seats = [
            s
            for s in inst.seat_ids
            if inst.cabin_of_seat[s] == cabin and s not in self.fixed_resources
        ]
        if not seats:
            return []
        if demand > len(seats):
            self.cache[key] = []
            return []
        if cabin not in self.geometry_rankings:
            coords = np.asarray([(inst.x(s), inst.y(s)) for s in seats])
            # Neighbor order depends on geometry, not group demand. Stable ties
            # preserve the original seat-order semantics for every prefix.
            delta_x = coords[None, :, 0] - coords[:, None, 0]
            delta_y = abs(coords[None, :, 1] - coords[:, None, 1])
            lexical = sorted(seats)
            lexical_index = {seat: j for j, seat in enumerate(lexical)}
            to_lexical = np.asarray([lexical_index[s] for s in seats], dtype=np.int32)
            orders = []
            for dx in (0, 0.5, -0.5):
                for aspect in (0.35, 0.7, 1.5, 3.0):
                    distance = abs(delta_x - dx) * aspect + delta_y
                    order = np.argsort(distance, axis=1, kind="stable")
                    orders.append(to_lexical[order])
            self.geometry_rankings[cabin] = lexical, np.concatenate(orders)
            self.operator_counts["geometry_rank_builds"] += 1
        else:
            self.operator_counts["geometry_prefix_reuses"] += 1
        lexical, orders = self.geometry_rankings[cabin]
        base = orders[:, :demand]
        if demand >= 2 and len(seats) > demand:
            alternative = np.concatenate(
                (base[:, :-1], orders[:, demand : demand + 1]), axis=1
            )
            base = np.concatenate((base, alternative))
        # Integer lexical ranks let NumPy sort and deduplicate the same resource
        # sets without repeatedly sorting thousands of Python string lists.
        indices = np.unique(np.sort(base, axis=1), axis=0)
        shapes = [tuple(lexical[j] for j in row) for row in indices]
        self.cache[key] = shapes
        return shapes

    def add(self, pattern):
        if pattern is None:
            return
        # Resource usage plus ordered passenger assignment identifies a pattern.
        key = (
            pattern.resources,
            frozenset(pattern.assigned.values()),
            pattern.flags,
            frozenset(pattern.counts.items()),
            pattern.infants,
        )
        old = self.pools[pattern.gid].get(key)
        if old is None or pattern.cost < old.cost - 1e-9:
            self.pools[pattern.gid][key] = pattern

    def ranked_shapes(self, group):
        if group.gid in self.prepared:
            return self.prepared[group.gid]
        inst = self.inst
        demand = group.size + sum(
            2 if p.both_side else int(p.single_side) for p in group.passengers
        )
        fixed = {p.fixed_seat for p in group.passengers if p.fixed_seat}
        for p in group.passengers:
            if p.fixed_seat and p.both_side:
                fixed.update(inst.both_side_neighbors(p.fixed_seat))
        movable = demand - len(fixed)
        shapes = (
            [
                tuple(sorted(set(shape) | fixed))
                for shape in (
                    self.shapes(group.cabin, movable)
                    + self.shapes(group.cabin, movable + 1)
                )
            ]
            if movable > 0
            else [tuple(sorted(fixed))]
        )
        # Score all resource shapes in vectorized batches. Python nested minima
        # can otherwise exhaust a short per-group slice before fitting any shape.
        costs = np.asarray(
            [[-p.score.get(s, -1e6) for s in inst.seat_ids] for p in group.passengers]
        )
        lower_scores = {}
        for width in {len(s) for s in shapes}:
            batch = [s for s in shapes if len(s) == width]
            indices = np.asarray(
                [[self.seat_index[s] for s in shape] for shape in batch]
            )
            values = costs[:, indices].min(axis=2).sum(axis=0)
            lower_scores.update(zip(batch, values))

        def lower(shape):
            return lower_scores[shape]

        # Preserve exact compact blocks, positional diversity and shapes with a
        # spare candidate independently. A loose lower bound otherwise ranks all
        # spare shapes first and can eliminate full-load packing alternatives.
        exact = sorted(
            (s for s in shapes if len(s) == demand), key=lambda s: (lower(s), s)
        )
        spare = sorted(
            (s for s in shapes if len(s) > demand), key=lambda s: (lower(s), s)
        )
        by_row = {}
        for shape in exact:
            row = min(inst.seat_map[s]["row"] for s in shape)
            by_row.setdefault(row, shape)
        ranked = list(by_row.values())
        for i, shape in enumerate(exact):
            ranked.append(shape)
            if i % 3 == 2 and i // 3 < len(spare):
                ranked.append(spare[i // 3])
        ranked.extend(spare)
        ranked = list(dict.fromkeys(ranked))
        self.prepared[group.gid] = ranked
        return ranked

    def generate(self, group, deadline, limit=250, hard_deadline=None):
        self.deadline = deadline
        ranked = self.ranked_shapes(group)
        # Preprocessing must not consume the entire small per-group slice and
        # silently leave a mandatory group without any column.
        if hard_deadline is not None and not self.pools[group.gid]:
            deadline = min(hard_deadline, max(deadline, time.perf_counter() + 0.01))
            self.deadline = deadline
        accepted = 0
        for shape in ranked:
            if shape in self.attempted[group.gid]:
                continue
            if time.perf_counter() >= deadline:
                break
            if accepted >= limit:
                break
            if any(not any(s in p.score for s in shape) for p in group.passengers):
                continue
            results = self.fit(group, shape)
            if results or time.perf_counter() < deadline:
                self.attempted[group.gid].add(shape)
            for pattern in results:
                self.add(pattern)
            accepted += bool(results)
        return accepted

    def fit(self, g, shape):
        inst = self.inst
        ordinary = [p for p in g.passengers if p.is_caregiver]
        special = [p for p in g.passengers if not p.is_caregiver]
        own = {p.fixed_seat for p in g.passengers if p.fixed_seat}
        for p in g.passengers:
            if p.fixed_seat and p.both_side:
                own.update(inst.both_side_neighbors(p.fixed_seat))
        seatset = set(shape) - (self.fixed_resources - own)
        domains = {
            p.key: sorted(
                (s for s in seatset if s in p.score), key=lambda s: (-p.score[s], s)
            )
            for p in g.passengers
        }
        if any(not domains[p.key] for p in g.passengers):
            return []
        special.sort(
            key=lambda p: (len(domains[p.key]), not p.both_side, not p.needs_caregiver)
        )
        results = []
        assignment = {}
        nodes = 0

        def visit(i, occupied, protected):
            nonlocal nodes
            nodes += 1
            if len(results) >= 64 or nodes > 4096:
                return
            if nodes % 32 == 0 and time.perf_counter() >= self.deadline:
                return
            if i == len(special):
                rest = sorted(seatset - occupied - protected)
                if len(rest) < len(ordinary):
                    return
                care = []
                for p in special:
                    if p.needs_caregiver:
                        support = sorted(
                            set(inst.caregiver_neighbors(p, assignment[p.key]))
                            & set(rest)
                        )
                        if not support:
                            return
                        care.append(support)
                forced_sets = (
                    {frozenset(v) for v in itertools.product(*care)}
                    if care
                    else {frozenset()}
                )
                for forced in sorted(forced_sets, key=lambda x: tuple(sorted(x))):
                    if len(forced) > len(ordinary):
                        continue
                    if ordinary:
                        cost = np.zeros((len(rest), len(rest)))
                        cost[: len(ordinary), :] = [
                            [-p.score.get(s, -1e8) for s in rest] for p in ordinary
                        ]
                        for j, seat in enumerate(rest):
                            if seat in forced:
                                cost[len(ordinary) :, j] = 1e8
                        ii, jj = linear_sum_assignment(cost)
                        if np.max(cost[ii, jj]) >= 1e7:
                            continue
                        assignment.update(
                            {
                                ordinary[i].key: rest[j]
                                for i, j in zip(ii, jj)
                                if i < len(ordinary)
                            }
                        )
                    elif forced:
                        continue
                    pattern = make_pattern(inst, g, assignment, protected)
                    if pattern:
                        results.append(pattern)
                    for p in ordinary:
                        assignment.pop(p.key, None)
                return
            p = special[i]
            for s in domains[p.key]:
                if s in occupied or s in protected:
                    continue
                choices = [set()]
                if p.both_side:
                    choices = [set(inst.both_side_neighbors(s))]
                if p.single_side:
                    choices = [
                        x | {t}
                        for x in choices
                        for t in inst.single_side_neighbors(s)
                        if t not in x
                    ]
                for empty in choices:
                    if not empty <= seatset or empty & (occupied | protected | {s}):
                        continue
                    assignment[p.key] = s
                    visit(i + 1, occupied | {s}, protected | empty)
                    assignment.pop(p.key, None)

        visit(0, set(), set())
        # Equivalent ordinary permutations have already been solved exactly.
        return sorted(results, key=lambda p: p.cost)[:8]

    def remember(self, assignment):
        inst = self.inst
        occupied = set(assignment.values())
        protected = defaultdict(set)
        for p in inst.passengers.values():
            if p.both_side:
                protected[p.gid].update(inst.both_side_neighbors(assignment[p.key]))
        blocked = occupied | set().union(*protected.values())
        demands = [p for p in inst.passengers.values() if p.single_side]
        owner = {}

        def augment(p, seen):
            for s in inst.single_side_neighbors(assignment[p.key]):
                if s in blocked or s in seen:
                    continue
                seen.add(s)
                if s not in owner or augment(owner[s], seen):
                    owner[s] = p
                    return True
            return False

        for p in demands:
            if not augment(p, set()):
                raise RuntimeError("Cannot recover exclusive protection resources")
        for s, p in owner.items():
            protected[p.gid].add(s)
        patterns = []
        for g in inst.groups:
            p = make_pattern(
                inst,
                g,
                {p.key: assignment[p.key] for p in g.passengers},
                protected[g.gid],
            )
            self.add(p)
            patterns.append(p)
        return patterns

    def refine(self, assignment, deadline, limit=80):
        self.deadline = deadline
        inst = self.inst
        patterns = self.remember(assignment)
        patterns.sort(key=lambda p: -p.cost)
        added = 0
        for index, pattern in enumerate(patterns):
            if time.perf_counter() >= deadline:
                break
            group = inst.group_by_id[pattern.gid]
            per_deadline = time.perf_counter() + (deadline - time.perf_counter()) / (
                len(patterns) - index
            )
            shape = set(pattern.resources)
            fixed = {p.fixed_seat for p in group.passengers if p.fixed_seat}
            eligible = {s for p in group.passengers for s in p.domain}
            nearby = [
                s
                for s in eligible - shape
                if min(abs(inst.y(s) - inst.y(t)) for t in shape) <= 3.01
            ]
            options = set()
            coordinated = set()
            occupied = set(pattern.assigned.values())
            for p in group.passengers:
                if p.fixed_seat or not (p.both_side or p.single_side):
                    continue
                old = pattern.assigned[p.key]
                old_empty = set(inst.both_side_neighbors(old)) if p.both_side else set()
                if p.single_side:
                    available = sorted(
                        (set(inst.single_side_neighbors(old)) & shape)
                        - occupied
                        - old_empty
                    )
                    if not available:
                        continue
                    old_empty.add(available[0])
                old_block = old_empty | {old}
                for seat in sorted(p.domain, key=lambda s: -p.score[s])[:32]:
                    empty_options = (
                        [set(inst.both_side_neighbors(seat))]
                        if p.both_side
                        else [set()]
                    )
                    if p.single_side:
                        empty_options = [
                            e | {s}
                            for e in empty_options
                            for s in inst.single_side_neighbors(seat)
                            if s not in e
                        ]
                    for empty in empty_options:
                        new_shape = (shape - old_block) | empty | {seat}
                        if len(new_shape) == len(shape) and fixed <= new_shape:
                            coordinated.add(tuple(sorted(new_shape)))
            for shift in (-3, -2, -1, 1, 2, 3):
                translated = {
                    s
                    if s in fixed
                    else self.position.get(
                        (
                            inst.seat_map[s]["row"] + shift,
                            inst.seat_map[s]["col"],
                            group.cabin,
                        )
                    )
                    for s in shape
                }
                if None not in translated and len(translated) == len(shape):
                    coordinated.add(tuple(sorted(translated)))
            for old in sorted(shape - fixed):
                for new in sorted(nearby):
                    options.add(tuple(sorted(shape - {old} | {new})))

            def key(ss, group=group):
                return sum(
                    min((-p.score.get(s, -1e5) for s in ss), default=1e5)
                    for p in group.passengers
                ) - inst.compactness_score(ss)

            coordinated.discard(tuple(sorted(shape)))
            ordered = [
                (ss, "block_or_group_relocation")
                for ss in sorted(coordinated, key=lambda ss: (key(ss), ss))[:32]
            ]
            ordered += [
                (ss, "one_seat_deformation")
                for ss in sorted(options - coordinated, key=lambda ss: (key(ss), ss))[
                    :limit
                ]
            ]
            for ss, operator in ordered:
                if time.perf_counter() >= per_deadline:
                    break
                for p in self.fit(group, ss):
                    self.add(p)
                    added += 1
                    self.operator_counts[operator] += 1
        return added
