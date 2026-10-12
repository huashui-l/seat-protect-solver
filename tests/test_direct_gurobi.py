"""Semantic and objective parity tests for the direct Gurobi route."""
from __future__ import annotations

import unittest
import copy
import itertools
from unittest.mock import patch

try:
    import gurobipy  # noqa: F401
    HAVE_GUROBI = True
except ImportError:  # pragma: no cover
    HAVE_GUROBI = False

from tests.test_cg_gurobi import _config, _groups, _pax, _seatmap, _brute_force


@unittest.skipUnless(HAVE_GUROBI, "gurobipy 不可用")
class DirectGurobiTests(unittest.TestCase):
    def test_every_small_assignment_matches_evaluator_feasibility_and_objective(self):
        import gurobipy as gp
        from src import allocation_evaluator as evaluator
        from src.cg_gurobi.instance import Instance
        from src.direct_gurobi.model import DirectModel

        scenarios = {
            "centroid": (_seatmap(2, ["A", "B", "C"]), _groups(
                (1, [_pax(1, "1A"), _pax(2, "1B"), _pax(3, "2C")]))),
            "fixed": (_seatmap(2, ["A", "B"]), _groups(
                (1, [_pax(1, "1A", new="2B"), _pax(2, "1B")]))),
            "single_matching": (_seatmap(1, ["A", "B", "C", "D"]), _groups(
                (1, [_pax(1, "1A", single="Y")]), (2, [_pax(1, "1D", single="Y")]))),
            "both_and_single": (_seatmap(1, ["A", "B", "C", "D"]), _groups(
                (1, [_pax(1, "1B", both="Y", single="Y")]))),
            "caregiver_exit": (_seatmap(2, ["A", "B"], exit_rows=(2,)), _groups(
                (1, [_pax(1, "1A", "CHD"), _pax(2, "1B")]))),
            "strict_care": (_seatmap(1, ["A", "B", "C", "D"]), _groups(
                (1, [_pax(1, "1A", cared="Y"), _pax(2, "1B")]))),
            "ssr_flags": (_seatmap(2, ["A", "B"]), _groups(
                (1, [_pax(1, "1A", "UM", rowflag="Y", subflag="Y")]),
                (2, [_pax(1, "1B", "UM")]), (3, [_pax(1, "2A", "TEST")]))),
            "baby": (_seatmap(2, ["A", "B", "C"], bassinet_rows=(1,)), _groups(
                (1, [_pax(1, "1A", "BSCT"), _pax(2, "1B")]), (2, [_pax(1, "2A")]))),
        }
        for name, (seatmap, groups) in scenarios.items():
            with self.subTest(scenario=name):
                cfg = copy.deepcopy(_config())
                cfg["direct_gurobi"] = {"threads": 1, "log": False}
                if name == "both_and_single":
                    cfg["mandatory_rules"]["require_two_real_neighbors"] = False
                inst = Instance(seatmap["seats"], seatmap["seats"], groups, cfg["weights"], cfg)
                keys = list(inst.passengers)
                with gp.Env(params={"OutputFlag": 0}) as env:
                    direct = DirectModel.build(inst, env)
                    for seats in itertools.product(inst.seat_ids, repeat=len(keys)):
                        assigned = dict(zip(keys, seats))
                        for (key, sid), var in direct.assignment.items():
                            var.LB = var.UB = float(assigned[key] == sid)
                        direct.model.optimize()
                        hard, unassigned, _ = evaluator.count_hard_constraint_violations(
                            seatmap["seats"], groups, assigned, cfg)
                        legal = hard == unassigned == 0
                        self.assertEqual(direct.model.Status == gp.GRB.OPTIMAL, legal, (name, assigned))
                        if legal:
                            score, _ = evaluator.calculate_soft_score(seatmap["seats"], seatmap["seats"],
                                groups, assigned, cfg["weights"], cfg)
                            self.assertAlmostEqual(direct.model.ObjVal, score, places=6,
                                                   msg=str((name, assigned)))
                    direct.model.dispose()

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

    def test_zero_budget_does_not_grant_extra_optimization_time(self):
        from src.direct_gurobi import solve
        cfg = _config()
        seatmap = _seatmap(1, ["A", "B"])
        groups = _groups((1, [_pax(1, "1A")]))
        result = solve(seatmap["seats"], seatmap["seats"], groups, cfg["weights"], cfg, time_limit=0)
        self.assertEqual(result["optimization_time_limit"], 0)
        self.assertFalse(result["proven_optimal"])

    def test_optimal_solver_status_does_not_override_score_disagreement(self):
        from src.direct_gurobi import solve
        from src import allocation_evaluator as evaluator
        original = evaluator.calculate_soft_score
        def disagree(*args, **kwargs):
            score, detail = original(*args, **kwargs)
            return score + 1.0, detail
        cfg = _config()
        seatmap = _seatmap(1, ["A", "B"])
        groups = _groups((1, [_pax(1, "1A")]))
        with patch("src.direct_gurobi.solver.evaluator.calculate_soft_score", side_effect=disagree):
            result = solve(seatmap["seats"], seatmap["seats"], groups, cfg["weights"], cfg, time_limit=5)
        self.assertEqual(result["solver_status"], "optimal")
        self.assertTrue(result["complete"])
        self.assertGreater(result["model_objective_error"], 0.9)
        self.assertFalse(result["proven_optimal"])


if __name__ == "__main__":
    unittest.main()
