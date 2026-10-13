"""Isolated process entry point for baseline profiling and hybrid pricing."""
from __future__ import annotations

import time

from src.cg_gurobi import solver, __main__ as cli
from .pricing import HybridPricer


def main():
    pricers, masters = [], []
    original_pricer, original_master, original_solve = solver.GroupPricer, solver.Master, cli.solve

    class ProfiledPricer(HybridPricer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pricers.append(self)

    class ProfiledMaster(original_master):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.profile = dict(lp_seconds=0.0, lp_calls=0, integer_seconds=0.0, integer_calls=0)
            masters.append(self)

        def solve_lp(self):
            started = time.perf_counter()
            try:
                return super().solve_lp()
            finally:
                self.profile["lp_seconds"] += time.perf_counter()-started
                self.profile["lp_calls"] += 1

        def solve_integer(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                return super().solve_integer(*args, **kwargs)
            finally:
                self.profile["integer_seconds"] += time.perf_counter()-started
                self.profile["integer_calls"] += 1

    def profiled_solve(*args, **kwargs):
        result = original_solve(*args, **kwargs)
        result["acceleration_profile"] = dict(groups=[p.stats for p in pricers],
                                               masters=[m.profile for m in masters])
        return result

    # These bindings live only in this dedicated process and are restored even
    # on failure. No collaborator source is copied, rewritten, or re-attributed.
    solver.GroupPricer, solver.Master, cli.solve = ProfiledPricer, ProfiledMaster, profiled_solve
    try:
        cli.main()
    finally:
        solver.GroupPricer, solver.Master, cli.solve = original_pricer, original_master, original_solve


if __name__ == "__main__":
    main()
