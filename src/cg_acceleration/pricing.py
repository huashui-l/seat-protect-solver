"""Dual-guided relocation/swap search, with exact pricing for certification."""
from __future__ import annotations

import math
import time

from src.cg_gurobi.pricing import GroupPricer, PricingOutcome, TOL, reduced_cost


class HybridPricer(GroupPricer):
    def __init__(self, inst, group, env):
        started = time.perf_counter()
        super().__init__(inst, group, env)
        self.cache = {}
        self.emitted = set()
        self.in_branch = False
        self.enabled = bool(inst.config.get("cg_acceleration", {}).get("heuristic", True))
        self.stats = dict(gid=group.gid, cabin=group.cabin, passengers=group.size,
                          domain_sum=sum(len(p.domain) for p in group.passengers),
                          build_seconds=time.perf_counter()-started,
                          heuristic_calls=0, heuristic_seconds=0.0, heuristic_columns=0,
                          exact_calls=0, exact_seconds=0.0, warm_seconds=0.0,
                          exact_statuses={})

    def remember(self, column):
        if column is not None:
            self.cache[column.signature] = column
            self.emitted.add(column.signature)
            # Bound retained search seeds independently of the total master pool.
            if len(self.cache) > 16:
                del self.cache[next(iter(self.cache))]
        return column

    def greedy_column(self, blocked, time_limit):
        started = time.perf_counter()
        col = super().greedy_column(blocked, time_limit)
        self.stats["warm_seconds"] += time.perf_counter()-started
        return self.remember(col)

    def column_from_assignment(self, assignment, protected=None):
        started = time.perf_counter()
        col = super().column_from_assignment(assignment, protected)
        self.stats["warm_seconds"] += time.perf_counter()-started
        return self.remember(col)

    def legal_column(self, assignment, seat_duals):
        """Check the complete group and match its exclusive protection seats."""
        inst, group = self.inst, self.group
        if len(set(assignment.values())) != group.size:
            return None
        used = set(assignment.values())
        protected = []
        for p in group.passengers:
            s = assignment[p.key]
            if s not in p.domain:
                return None
            if p.needs_caregiver and not any(
                q.key != p.key and assignment[q.key] in inst.caregiver_neighbors(p, s)
                for q in group.caregivers
            ):
                return None
            if p.both_side:
                neighbors = inst.both_side_neighbors(s)
                if any(t in used for t in neighbors):
                    return None
                used.update(neighbors)
                protected.extend((p.key, t) for t in neighbors)
        demands = []
        for p in group.passengers:
            if p.single_side:
                choices = [t for t in inst.single_side_neighbors(assignment[p.key]) if t not in used]
                choices.sort(key=lambda t: (-seat_duals.get(t, 0.0), t))
                demands.append((p.key, choices))
        owners = {}

        def augment(i, visited):
            for t in demands[i][1]:
                if t in visited:
                    continue
                visited.add(t)
                if t not in owners or augment(owners[t], visited):
                    owners[t] = i
                    return True
            return False

        for i in sorted(range(len(demands)), key=lambda i: len(demands[i][1])):
            if not augment(i, set()):
                return None
        protected.extend((demands[i][0], t) for t, i in owners.items())
        col = self.make_column(assignment, protected)
        if any(count > 1 and loc in col.flag_locations for loc, _, count in col.ssr_counts):
            return None
        return col

    def search(self, duals, phase_one, deadline):
        # Every candidate is scored with ALL master dual families. The separable
        # score orders moves only; it is never used as a bound or certificate.
        seeds = sorted(self.cache.values(), key=lambda c: reduced_cost(c, duals, phase_one))[:2]
        found = {}
        for seed in seeds:
            current = seed
            while time.perf_counter() < deadline:
                assignment = current.as_dict()
                current_rc = reduced_cost(current, duals, phase_one)
                best, best_rc = current, current_rc
                owner = {s: pk for pk, s in assignment.items()}
                for p in self.group.passengers:
                    targets = sorted(p.domain, key=lambda s: (
                        -(0.0 if phase_one else p.score[s])-duals.seat.get(s, 0.0), s))
                    for s in targets:
                        if time.perf_counter() >= deadline:
                            break
                        if s == assignment[p.key]:
                            continue
                        trial = dict(assignment)
                        if s in owner:
                            trial[owner[s]] = assignment[p.key]
                        trial[p.key] = s
                        col = self.legal_column(trial, duals.seat)
                        if col is None:
                            continue
                        rc = reduced_cost(col, duals, phase_one)
                        if rc < -TOL and col.signature not in self.emitted:
                            found[col.signature] = col
                            if len(found) >= 8:
                                return sorted(found.values(), key=lambda c: reduced_cost(c, duals, phase_one))[:8]
                        if rc < best_rc-TOL:
                            best, best_rc = col, rc
                    if time.perf_counter() >= deadline:
                        break
                if best_rc >= current_rc-TOL:
                    break
                current = best
        return sorted(found.values(), key=lambda c: reduced_cost(c, duals, phase_one))[:8]

    def price(self, duals, *, phase_one, time_limit, use_cutoff=True, early_stop=None):
        started = time.perf_counter()
        if self.enabled and not self.in_branch and self.cache:
            self.stats["heuristic_calls"] += 1
            # A small fixed fraction of this call's remaining budget; no case IDs.
            cols = self.search(duals, phase_one, started+min(0.02, 0.1*max(0, time_limit)))
            elapsed = time.perf_counter()-started
            self.stats["heuristic_seconds"] += elapsed
            if cols:
                self.stats["heuristic_columns"] += len(cols)
                for c in cols:
                    self.remember(c)
                # Unknown lower bound forces exact fallback before any certificate.
                return PricingOutcome(self.group.gid, "heuristic", -math.inf, cols,
                                      reduced_cost(cols[0], duals, phase_one), elapsed)
        exact_started = time.perf_counter()
        outcome = super().price(duals, phase_one=phase_one,
                                time_limit=max(0.01, time_limit-(exact_started-started)),
                                use_cutoff=use_cutoff, early_stop=early_stop)
        self.stats["exact_calls"] += 1
        self.stats["exact_seconds"] += time.perf_counter()-exact_started
        statuses = self.stats["exact_statuses"]
        statuses[outcome.status] = statuses.get(outcome.status, 0)+1
        for c in outcome.columns:
            self.remember(c)
        outcome.seconds = time.perf_counter()-started
        return outcome

    def price_with_rules(self, duals, rules, *, phase_one, time_limit):
        # Exact branch pricing retains the original rule application and bounds.
        self.in_branch = True
        try:
            return super().price_with_rules(duals, rules, phase_one=phase_one, time_limit=time_limit)
        finally:
            self.in_branch = False
