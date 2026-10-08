"""Semantic and objective parity tests for the direct Gurobi route."""
from __future__ import annotations

import unittest

try:
    import gurobipy  # noqa: F401
    HAVE_GUROBI = True
except ImportError:  # pragma: no cover
    HAVE_GUROBI = False

from tests.test_cg_gurobi import _config, _groups, _pax, _seatmap, _brute_force


@unittest.skipUnless(HAVE_GUROBI, "gurobipy 不可用")
class DirectGurobiTests(unittest.TestCase):
    def test_small_cases_match_bruteforce(self):
        from src.direct_gurobi import solve
        cfg = _config()
        scenarios = {
            "ordinary": (_seatmap(2, ["A", "B", "C"]), _groups(
                (1, [_pax(1, "1A"), _pax(2, "1B"), _pax(3, "2C")]),
                (2, [_pax(1, "2A"), _pax(2, "1C")]))),
            "protection": (_seatmap(2, ["A", "B", "C", "D"]), _groups(
                (1, [_pax(1, "1A", both="Y"), _pax(2, "2D")]),
                (2, [_pax(1, "2A", single="Y")]))),
            "caregiver": (_seatmap(2, ["A", "B", "C"]), _groups(
                (1, [_pax(1, "1A", "CHD"), _pax(2, "1B")]),
                (2, [_pax(1, "2A"), _pax(2, "2B")]))),
            "conditional_ssr": (_seatmap(2, ["A", "B", "C"]), _groups(
                (1, [_pax(1, "1A", "UM", rowflag="Y")]),
                (2, [_pax(1, "1B", "UM")]),
                (3, [_pax(1, "2C"), _pax(2, "2B")]))),
            "baby_cross_group": (_seatmap(2, ["A", "B", "C"], bassinet_rows=(1,)), _groups(
                (1, [_pax(1, "1A", "BSCT"), _pax(2, "1B")]),
                (2, [_pax(1, "2A"), _pax(2, "2B")]),
                (3, [_pax(1, "1C")]))),
        }
        for name, (seatmap, groups) in scenarios.items():
            with self.subTest(scenario=name):
                cfg["direct_gurobi"] = {"time_limit": 20, "threads": 1, "log": False}
                expected = _brute_force(seatmap, groups, cfg)
                result = solve(seatmap["seats"], seatmap["seats"], groups,
                               cfg["weights"], cfg, time_limit=20)
                self.assertTrue(result["complete"])
                self.assertTrue(result["proven_optimal"])
                self.assertEqual((0, 0), (result["hard_violations"], result["unassigned"]))
                self.assertAlmostEqual(expected, result["total_score"], places=6)
                self.assertAlmostEqual(result["model_objective"], result["total_score"], places=6)

    def test_invalid_or_fixed_semantics_are_rejected(self):
        from src.direct_gurobi import solve
        cfg = _config()
        seatmap = _seatmap(1, ["A", "B"])
        groups = _groups((1, [_pax(1, "1A", new="1A"), _pax(2, "1B", new="1A")]))
        with self.assertRaises(ValueError):
            solve(seatmap["seats"], seatmap["seats"], groups, cfg["weights"], cfg, time_limit=5)


if __name__ == "__main__":
    unittest.main()
