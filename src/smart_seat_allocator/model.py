"""Direct passenger/seat CP-SAT model; no legacy search or reference solutions.

The objective uses integer coefficients. ``rounding_error`` bounds its absolute
error in original score units, so restricted-domain bounds are never certificates
for the unrestricted problem, and even full-domain bounds include rounding error.
"""

from __future__ import annotations

import time
from collections import defaultdict
from fractions import Fraction
from math import lcm

from ortools.sat.python import cp_model

from src.seat_model import Instance

COST_SCALE = 1_000_000


class SeatModel:
    def __init__(self, inst: Instance, domains=None, hint=None, deadline=float("inf")):
        def check_deadline():
            if time.perf_counter() >= deadline:
                raise TimeoutError("Seat model construction reached its deadline")

        self.inst = inst
        self.cost_scale = COST_SCALE * min(
            10000, lcm(*(max(1, g.size) for g in inst.groups))
        )
        self.model = m = cp_model.CpModel()
        self.domains = domains or {p.key: p.domain for p in inst.passengers.values()}
        self.full_domain = all(
            set(self.domains[p.key]) == set(p.domain) for p in inst.passengers.values()
        )
        self.x = {}
        resources, occupied, group_occupied, infant = (
            defaultdict(list) for _ in range(4)
        )
        self.terms, self.rounding_error, self.offset = [], 0.0, 0.0
        self.deviations = {}
        for p in inst.passengers.values():
            check_deadline()
            coeffs = []
            baseline = min((-p.score[s] for s in self.domains[p.key]), default=0)
            self.offset += baseline
            for s in self.domains[p.key]:
                v = self.x[p.key, s] = m.new_bool_var(f"x_{p.key}_{s}")
                resources[s].append(v)
                occupied[s].append(v)
                group_occupied[p.gid, s].append(v)
                if p.is_infant:
                    infant[p.gid, s].append(v)
                cost = -p.score[s] - baseline
                rounded = round(cost * self.cost_scale)
                self.terms.append(rounded * v)
                coeffs.append(abs(rounded / self.cost_scale - cost))
                if p.both_side:
                    for t in inst.both_side_neighbors(s):
                        resources[t].append(v)
                if p.single_side:
                    zs = []
                    for t in inst.single_side_neighbors(s):
                        z = m.new_bool_var(f"z_{p.key}_{s}_{t}")
                        resources[t].append(z)
                        zs.append(z)
                    m.add(sum(zs) == v)
            self.rounding_error += max(coeffs, default=0)
            m.add_exactly_one(self.x[p.key, s] for s in self.domains[p.key])
        for vs in resources.values():
            m.add_at_most_one(vs)
        for g in inst.groups:
            check_deadline()
            for p in g.passengers:
                if p.needs_caregiver:
                    for s in self.domains[p.key]:
                        support = [
                            self.x[q.key, t]
                            for q in g.caregivers
                            if q.key != p.key
                            for t in inst.caregiver_neighbors(p, s)
                            if (q.key, t) in self.x
                        ]
                        m.add_bool_or([self.x[p.key, s].Not(), *support])

        # Conditional SSR isolation: once any flagged passenger activates a
        # location, EACH type may appear at most once (different types may mix).
        flags, counts = defaultdict(list), defaultdict(list)
        for p in inst.passengers.values():
            check_deadline()
            if not p.ssr:
                continue
            for s in self.domains[p.key]:
                v = self.x[p.key, s]
                for kind, flagged in [("row", p.row_flag), ("subrow", p.subrow_flag)]:
                    loc = inst.location_of(s, kind)
                    counts[loc, p.ssr].append(v)
                    if flagged:
                        flags[loc].append(v)
        for loc, vs in flags.items():
            active = m.new_bool_var(f"active_{loc}")
            m.add_max_equality(active, vs)
            for ssr in inst.ssr_types:
                if len(counts[loc, ssr]) > 1:
                    m.add(sum(counts[loc, ssr]) <= 1).only_enforce_if(active)

        # n * deviation = max_p |n * coordinate_p - sum_q coordinate_q|.
        # Coordinates are represented exactly as small rational numbers.
        scale = 1
        for s in inst.seat_ids:
            for value in (inst.x(s), inst.y(s)):
                f = Fraction(value).limit_denominator(10000)
                if abs(float(f) - value) > 1e-9:
                    raise ValueError(
                        "Seat geometry needs rational coordinates within 1e-9"
                    )
                scale = lcm(scale, f.denominator)
        if scale > 1_000_000:
            raise ValueError("Seat geometry scale too large for CP-SAT")
        axes = [
            (
                coord,
                factor,
                max(
                    (
                        abs(coord(s) - round(scale * coord(s)) / scale)
                        for s in inst.seat_ids
                    ),
                    default=0.0,
                ),
            )
            for coord, factor in [
                (inst.x, inst.compact_x_factor),
                (inst.y, inst.compact_y_factor),
            ]
        ]
        for g in inst.groups:
            check_deadline()
            if g.size <= 1:
                continue
            for axis, (coord, factor, geometry_error) in enumerate(axes):
                # Both a passenger coordinate and its centroid may move by
                # geometry_error. Include that error when certifying a bound.
                self.rounding_error += 2 * (-inst.w_c) * factor * geometry_error
                cost = -inst.w_c * factor / (scale * g.size)
                if cost < 0:
                    raise ValueError("Compactness factors must be nonnegative")
                if cost == 0:
                    continue
                coords = []
                all_values = []
                for p in g.passengers:
                    vals = [round(scale * coord(s)) for s in self.domains[p.key]]
                    if not vals:
                        continue
                    q = m.new_int_var(min(vals), max(vals), "")
                    m.add(
                        q
                        == sum(
                            val * self.x[p.key, s]
                            for val, s in zip(vals, self.domains[p.key])
                        )
                    )
                    coords.append(q)
                    all_values.extend(vals)
                if not coords:
                    continue
                upper = g.size * (max(all_values) - min(all_values))
                dev = m.new_int_var(0, upper, "")
                self.deviations[g.gid, axis] = dev
                total = sum(coords)
                # The positive objective coefficient makes this epigraph exact
                # at an optimum. A time-limited incumbent may retain slack;
                # decoding always recomputes the original centroid penalty.
                for q in coords:
                    m.add(dev >= g.size * q - total)
                    m.add(dev >= total - g.size * q)
                self.add_cost(dev, cost, upper)

        # Infant effects only involve occupants in OTHER groups. This avoids
        # subtracting same-group products or accidentally charging empty seats.
        for (gid, u), ivs in infant.items():
            check_deadline()
            for uu, v in inst.baby_pairs_by_infant_seat.get(u, []):
                other = occupied[v]
                own_indices = {x.index for x in group_occupied[gid, v]}
                other = [x for x in other if x.index not in own_indices]
                if not other:
                    continue
                b = m.new_bool_var("baby")
                m.add(b >= sum(ivs) + sum(other) - 1)
                m.add(b <= sum(ivs))
                m.add(b <= sum(other))
                self.add_cost(b, -inst.baby_pairs[u, v], 1)
        m.minimize(sum(self.terms))
        if hint:
            for (pk, s), v in self.x.items():
                if pk in hint:
                    m.add_hint(v, int(hint[pk] == s))

    def add_cost(self, var, coefficient, upper):
        rounded = round(coefficient * self.cost_scale)
        self.terms.append(rounded * var)
        self.rounding_error += abs(rounded / self.cost_scale - coefficient) * upper

    def assignment(self, solver):
        return {pk: s for (pk, s), v in self.x.items() if solver.value(v)}

    def cost(self, solver):
        return self.offset + solver.objective_value / self.cost_scale

    def lower_bound(self, solver):
        return (
            self.offset
            + solver.best_objective_bound / self.cost_scale
            - self.rounding_error
        )
