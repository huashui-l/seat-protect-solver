"""Exact linear enumeration for a one-passenger PNR.

This removes solver call overhead for the smallest pricing problems. It is
not a heuristic: every legal seat/protection option is visited before certifying.
"""
import math
import time

from src.cg_gurobi.pricing import PricingOutcome, TOL
from .pricing import HybridPricer


class SingletonPricer(HybridPricer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.singleton_columns = None
        self.stats.update(enumeration_calls=0, enumeration_seconds=0.0)

    def options(self):
        if self.singleton_columns is None:
            p = self.group.passengers[0]
            self.singleton_columns = []
            if p.needs_caregiver or (p.single_side and p.both_side):
                return []
            for s in p.domain:
                if p.single_side:
                    protections = [[(p.key,t)] for t in self.inst.single_side_neighbors(s)]
                elif p.both_side:
                    protections = [[(p.key,t) for t in self.inst.both_side_neighbors(s)]]
                else:
                    protections = [[]]
                for protected in protections:
                    self.singleton_columns.append(self.make_column({p.key:s},protected))
        return self.singleton_columns

    def price(self, duals, *, phase_one, time_limit, use_cutoff=True, early_stop=None):
        if not self.enabled or self.group.size != 1 or self.in_branch:
            return super().price(duals,phase_one=phase_one,time_limit=time_limit,
                                 use_cutoff=use_cutoff,early_stop=early_stop)
        started = time.perf_counter()
        deadline = started+max(0,time_limit)
        # Compile additive dual coefficients once, rather than scan all master
        # rows for every seat. This is algebraically identical to reduced_cost.
        activation, infant, occupied = {}, {}, {}
        for (gid,loc,_),pi in duals.ssr_activation.items():
            if gid == self.group.gid:
                activation[loc] = activation.get(loc,0.0)+pi
        for (u,v),pi in duals.baby.items():
            infant[u] = infant.get(u,0.0)+pi
            occupied[v] = occupied.get(v,0.0)+pi
        best, columns, complete = math.inf, [], True
        options = self.options()
        for col in options:
            if time.perf_counter() >= deadline:
                complete = False
                break
            rc = (0.0 if phase_one else col.cost)-duals.convexity.get(col.gid,0.0)
            rc -= sum(duals.seat.get(s,0.0) for s in col.resources)
            rc -= sum(activation.get(loc,0.0) for loc in col.flag_locations)
            rc -= sum(duals.ssr_count.get((loc,tau),0.0)*n for loc,tau,n in col.ssr_counts)
            rc -= sum(infant.get(s,0.0) for s in col.infant_seats)
            rc -= sum(occupied.get(s,0.0) for s in col.occupied)
            best = min(best,rc)
            if rc < -TOL:
                columns.append((rc,col))
        columns.sort(key=lambda item:item[0])
        selected = [col for _,col in columns[:8]]
        for col in selected:
            self.remember(col)
        elapsed = time.perf_counter()-started
        self.stats["enumeration_calls"] += 1
        self.stats["enumeration_seconds"] += elapsed
        status = ("optimal" if options else "infeasible") if complete else "time_limit"
        return PricingOutcome(self.group.gid,status,best if complete else -math.inf,
                              selected,best,elapsed)
