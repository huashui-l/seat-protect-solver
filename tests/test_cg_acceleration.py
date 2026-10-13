"""Exhaustive legality and certificate gates for the experimental pricer."""
import itertools
import math
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gurobipy as gp

from src import allocation_evaluator as evaluator
from src.cg_gurobi.instance import Instance
from src.cg_gurobi.pricing import Duals, reduced_cost
from src.cg_acceleration.pricing import HybridPricer
from tests import test_cg_gurobi as fixtures

_config, _groups, _pax, _seatmap = fixtures._config, fixtures._groups, fixtures._pax, fixtures._seatmap


class HybridPricingTests(unittest.TestCase):
    def setUp(self):
        self.env = gp.Env(empty=True)
        self.env.setParam("OutputFlag", 0)
        self.env.start()

    def tearDown(self):
        self.env.dispose()

    def test_local_legality_matches_evaluator_exhaustively(self):
        cfg = _config()
        scenarios = list(fixtures.BruteForceParityTests.SCENARIOS.items())
        scenarios.append(("two_ssr_flagged", (_seatmap(2, ["A", "B", "C"]),
            _groups((1, [_pax(1, "1A", "UM", rowflag="Y"), _pax(2, "1B", "UM")])))))
        for name, (sm, groups) in scenarios:
            inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
            for group in inst.groups:
                with self.subTest(scenario=name, group=group.gid):
                    pricer = HybridPricer(inst, group, self.env)
                    raw = [g for g in groups if g["groupId"] == group.gid]
                    for seats in itertools.product(*(p.domain for p in group.passengers)):
                        assignment = dict(zip((p.key for p in group.passengers), seats))
                        violations, missing, _ = evaluator.count_hard_constraint_violations(
                            sm["seats"], raw, assignment, cfg)
                        col = pricer.legal_column(assignment, {})
                        self.assertEqual(violations == missing == 0, col is not None, (name, assignment))
                        if col:
                            score, _ = evaluator.calculate_soft_score(sm["seats"], sm["seats"], raw,
                                assignment, cfg["weights"], cfg)
                            self.assertAlmostEqual(score, col.local_score, places=7)
                    pricer.model.dispose()

    def test_heuristic_success_is_not_a_certificate(self):
        cfg = _config()
        sm = _seatmap(2, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        pricer = HybridPricer(inst, inst.groups[0], self.env)
        seed = pricer.legal_column({(1, 1): "1A"}, {})
        pricer.remember(seed)
        duals = Duals(convexity={1: 100}, seat={"1A": -200})
        out = pricer.price(duals, phase_one=False, time_limit=1)
        self.assertEqual("heuristic", out.status)
        self.assertFalse(out.certified)
        self.assertEqual(-math.inf, out.lower_bound)
        self.assertTrue(out.columns)
        self.assertTrue(all(reduced_cost(c, duals, False) < -1e-6 for c in out.columns))
        pricer.model.dispose()

    def test_failed_search_uses_exact_pricing_and_branch_rules(self):
        cfg = _config()
        sm = _seatmap(2, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        pricer = HybridPricer(inst, inst.groups[0], self.env)
        pricer.remember(pricer.legal_column({(1, 1): "1A"}, {}))
        out = pricer.price(Duals(), phase_one=True, time_limit=2)
        self.assertTrue(out.certified)
        self.assertFalse(out.columns)
        out = pricer.price_with_rules(Duals(convexity={1: 100}), [("seat", "2C", True)],
                                     phase_one=False, time_limit=2)
        self.assertTrue(out.certified)
        self.assertTrue(out.columns)
        self.assertTrue(all("2C" in c.occupied for c in out.columns))
        self.assertEqual(2, pricer.stats["exact_calls"])
        pricer.model.dispose()

    def test_search_stops_when_output_batch_is_full(self):
        cfg = _config()
        sm = _seatmap(5, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        pricer = HybridPricer(inst, inst.groups[0], self.env)
        pricer.remember(pricer.legal_column({(1, 1): "1A"}, {}))
        duals = Duals(convexity={1: 1000})
        with patch.object(pricer, "legal_column", wraps=pricer.legal_column) as check:
            out = pricer.price(duals, phase_one=False, time_limit=1)
        self.assertEqual(8, len(out.columns))
        self.assertEqual(8, check.call_count)
        self.assertFalse(out.certified)
        self.assertEqual(-math.inf, out.lower_bound)
        self.assertTrue(all(reduced_cost(c, duals, False) < -1e-6 for c in out.columns))
        pricer.model.dispose()

    def test_cli_global_bounds_and_scores_match_brute_force(self):
        self.check_cli("src.cg_acceleration")

    def test_fast_cli_global_bounds_and_scores_match_brute_force(self):
        self.check_cli("src.cg_acceleration.fast")

    def check_cli(self, module):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            cfg = _config()
            cfg["cg_gurobi"].update(total_time_limit=15, pricing_workers=1)
            (folder/"config.json").write_text(json.dumps(cfg), encoding="utf-8")
            for name,(sm,groups) in fixtures.BruteForceParityTests.SCENARIOS.items():
                with self.subTest(scenario=name):
                    best = fixtures._brute_force(sm,groups,cfg)
                    (folder/"seats.json").write_text(json.dumps(sm),encoding="utf-8")
                    (folder/"groups.json").write_text(json.dumps({"groups":groups}),encoding="utf-8")
                    process = subprocess.run([sys.executable,"-m",module,
                        "--config",str(folder/"config.json"),"--groups",str(folder/"groups.json"),
                        "--old-seatmap",str(folder/"seats.json"),"--new-seatmap",str(folder/"seats.json"),
                        "--output",str(folder/"raw.json"),"--quiet"],capture_output=True,text=True,
                        cwd=Path(__file__).resolve().parents[1],timeout=30)
                    self.assertEqual(0,process.returncode,process.stderr)
                    result=json.loads((folder/"raw.json").read_text(encoding="utf-8"))
                    self.assertEqual((0,0),(result["hard_violations"],result["unassigned"]))
                    self.assertAlmostEqual(best,result["total_score"],places=6)
                    self.assertTrue(result["lp_bound_certified"])
                    self.assertGreaterEqual(result["lp_bound"],best-1e-6)
                    self.assertTrue(result["proven_optimal"])


if __name__ == "__main__":
    unittest.main()
