"""Independent exhaustive and evaluator-based regression for the new heuristic."""

import json
import time
import unittest
from pathlib import Path

from ortools.sat.python import cp_model

from src import allocation_evaluator as ev
from src.seat_model import Instance
from src.smart_seat_allocator import solve
from src.smart_seat_allocator.capacity import infant_capacity
from src.smart_seat_allocator.construction import construct
from src.smart_seat_allocator.descent import descend
from src.smart_seat_allocator.highs_master import recombine as recombine_highs
from src.smart_seat_allocator.master import recombine
from src.smart_seat_allocator.model import SeatModel
from src.smart_seat_allocator.patterns import PatternFactory
from tests import test_cg_gurobi as fixtures
from tests.general_validation_case_factory import materialize_cases
from tests.test_cg_gurobi import _brute_force, _groups, _pax, _seatmap

ROOT = Path(__file__).resolve().parents[1]


def config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))


class ModelParity(unittest.TestCase):
    def test_infant_capacity_certificate_is_group_aware(self):
        cfg = config()
        sm = _seatmap(2, ["A", "B", "C"], bassinet_rows=[1])
        groups = _groups(
            (1, [_pax(1, "1A", "BSCT"), _pax(2, "1B")]),
            (2, [_pax(1, "1C", "BSCT"), _pax(2, "2A")]),
        )
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        certificate = infant_capacity(inst)
        self.assertEqual(certificate["required_distinct_groups"], 2)
        self.assertEqual(certificate["maximum_disjoint_pairs"], 1)
        self.assertEqual(_brute_force(sm, groups, cfg), float("-inf"))
        result = solve(
            sm["seats"], sm["seats"], groups, cfg["weights"], cfg, time_limit=0.5
        )
        self.assertEqual(result["status"], "infeasible")
        merged = _groups(
            (1, [_pax(1, "1A", "BSCT"), _pax(2, "1B"), _pax(3, "1C", "BSCT")])
        )
        inst = Instance(sm["seats"], sm["seats"], merged, cfg["weights"], cfg)
        self.assertIsNone(infant_capacity(inst))
        self.assertGreater(_brute_force(sm, merged, cfg), float("-inf"))

    def test_model_build_can_obey_an_expired_deadline(self):
        cfg = config()
        sm = _seatmap(2, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        with self.assertRaises(TimeoutError):
            SeatModel(inst, deadline=time.perf_counter() - 1)

    def test_pattern_masters_skip_an_expired_deadline(self):
        cfg = config()
        sm = _seatmap(2, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        # An expired call must not even inspect or construct the candidate pool.
        for backend in (recombine, recombine_highs):
            with self.subTest(backend=backend.__module__):
                self.assertIsNone(backend(inst, None, time.perf_counter() - 1))

    def test_adult_descent_preserves_care_and_exclusive_protection(self):
        cfg = config()
        for name, (sm, groups) in fixtures.BruteForceParityTests.SCENARIOS.items():
            with self.subTest(name=name):
                inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
                model = SeatModel(inst)
                model.model.clear_objective()
                solver = cp_model.CpSolver()
                solver.parameters.num_search_workers = 1
                self.assertIn(
                    solver.solve(model.model), (cp_model.OPTIMAL, cp_model.FEASIBLE)
                )
                before = model.assignment(solver)
                after, _ = descend(
                    inst, PatternFactory(inst), before, time.perf_counter() + 0.2
                )
                self.assertEqual(
                    (0, 0),
                    ev.count_hard_constraint_violations(
                        sm["seats"], groups, after, cfg
                    )[:2],
                )
                old_score = ev.calculate_soft_score(
                    sm["seats"], sm["seats"], groups, before, cfg["weights"], cfg
                )[0]
                new_score = ev.calculate_soft_score(
                    sm["seats"], sm["seats"], groups, after, cfg["weights"], cfg
                )[0]
                self.assertGreaterEqual(new_score, old_score - 1e-7)

    def test_exhaustive_model_parity(self):
        cfg = config()
        for name, (sm, groups) in fixtures.BruteForceParityTests.SCENARIOS.items():
            with self.subTest(name=name):
                inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
                m = SeatModel(inst)
                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = 5
                solver.parameters.num_search_workers = 1
                status = solver.solve(m.model)
                self.assertEqual(status, cp_model.OPTIMAL)
                a = m.assignment(solver)
                self.assertEqual(
                    (0, 0),
                    ev.count_hard_constraint_violations(sm["seats"], groups, a, cfg)[
                        :2
                    ],
                )
                score = ev.calculate_soft_score(
                    sm["seats"], sm["seats"], groups, a, cfg["weights"], cfg
                )[0]
                optimum = _brute_force(sm, groups, cfg)
                self.assertAlmostEqual(score, optimum, places=6)
                self.assertLessEqual(m.lower_bound(solver), -optimum + 1e-7)
                self.assertLessEqual(
                    abs(m.cost(solver) + score), m.rounding_error + 1e-7
                )

    def test_geometry_rounding_is_included_in_certified_bound(self):
        cfg = config()
        cfg["seat_geometry"]["row_spacing"] = 0.9999999999
        cfg["weights"]["w_c"] = -1_000_000.0
        sm = _seatmap(2, ["A", "B"])
        groups = _groups((1, [_pax(1, "1A"), _pax(2, "2A")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        model = SeatModel(inst)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.solve(model.model), cp_model.OPTIMAL)
        optimum = _brute_force(sm, groups, cfg)
        self.assertLessEqual(model.lower_bound(solver), -optimum + 1e-8)

    def test_nonminimal_epigraph_has_a_conservative_cost(self):
        cfg = config()
        sm = _seatmap(1, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A", new="1A"), _pax(2, "1C", new="1C")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        model = SeatModel(inst)
        dev = model.deviations[1, 0]
        model.model.add(dev == list(model.model.proto.variables[dev.index].domain)[-1])
        solver = cp_model.CpSolver()
        self.assertEqual(solver.solve(model.model), cp_model.OPTIMAL)
        assignment = model.assignment(solver)
        self.assertEqual(
            (0, 0),
            ev.count_hard_constraint_violations(sm["seats"], groups, assignment, cfg)[
                :2
            ],
        )
        score = ev.calculate_soft_score(
            sm["seats"], sm["seats"], groups, assignment, cfg["weights"], cfg
        )[0]
        self.assertGreater(model.cost(solver), -score + 1)

    def test_every_pattern_is_locally_legal_and_master_matches_evaluator(self):
        cfg = config()
        for c in materialize_cases():
            if not c["expectedValid"]:
                continue
            with self.subTest(case=c["id"]):
                ns, os = c["newSeatmapData"]["seats"], c["oldSeatmapData"]["seats"]
                inst = Instance(ns, os, c["groupsData"], cfg["weights"], cfg)
                f = PatternFactory(inst)
                for g in inst.groups:
                    f.generate(g, time.perf_counter() + 1, limit=50)
                    raw = [x for x in c["groupsData"] if x["groupId"] == g.gid]
                    for p in f.pools[g.gid].values():
                        self.assertEqual(
                            (0, 0),
                            ev.count_hard_constraint_violations(
                                ns, raw, p.assigned, cfg
                            )[:2],
                        )
                        score = ev.calculate_soft_score(
                            ns, os, raw, p.assigned, cfg["weights"], cfg
                        )[0]
                        self.assertAlmostEqual(-p.cost, score, places=7)
                r = recombine(inst, f.pools, time.perf_counter() + 2, workers=1)
                if r:
                    a, stats = r
                    self.assertEqual(
                        (0, 0),
                        ev.count_hard_constraint_violations(
                            ns, c["groupsData"], a, cfg
                        )[:2],
                    )
                    score = ev.calculate_soft_score(
                        ns, os, c["groupsData"], a, cfg["weights"], cfg
                    )[0]
                    self.assertAlmostEqual(-stats["pool_cost"], score, places=4)

    def test_scattered_fixed_anchors_leave_room_for_two_protection_roles(self):
        cfg = config()
        sm = _seatmap(12, ["A", "B", "C", "D", "E", "F"])
        groups = _groups(
            (
                1,
                [
                    _pax(1, "5B", both="Y"),
                    _pax(2, "12C", new="12C"),
                    _pax(3, "12E", new="12E"),
                    _pax(4, "7A", new="7A"),
                    _pax(5, "6D", new="6D"),
                    _pax(6, "2C", new="2C"),
                    _pax(7, "5E", "CBBG", single="Y"),
                ],
            )
        )
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        factory = PatternFactory(inst)
        factory.generate(inst.groups[0], time.perf_counter() + 1, limit=50)
        self.assertTrue(factory.pools[1])
        for pattern in factory.pools[1].values():
            self.assertEqual(len(pattern.resources), 10)
            self.assertEqual(
                (0, 0),
                ev.count_hard_constraint_violations(
                    sm["seats"], groups, pattern.assigned, cfg
                )[:2],
            )

    def test_highs_master_objective_and_cross_group_constraints(self):
        cfg = config()
        for name in ("conditional_ssr", "baby_cross_group", "fixed_and_protection"):
            with self.subTest(scenario=name):
                sm, groups = fixtures.BruteForceParityTests.SCENARIOS[name]
                inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
                factory = PatternFactory(inst)
                for group in inst.groups:
                    factory.generate(group, time.perf_counter() + 1, limit=100)
                result = recombine_highs(
                    inst, factory.pools, time.perf_counter() + 2, workers=1
                )
                self.assertIsNotNone(result)
                assignment, stats = result
                self.assertEqual(
                    (0, 0),
                    ev.count_hard_constraint_violations(
                        sm["seats"], groups, assignment, cfg
                    )[:2],
                )
                score = ev.calculate_soft_score(
                    sm["seats"], sm["seats"], groups, assignment, cfg["weights"], cfg
                )[0]
                self.assertAlmostEqual(-stats["pool_cost"], score, places=6)
                self.assertAlmostEqual(score, _brute_force(sm, groups, cfg), places=6)
                constructed = construct(inst, factory.pools, time.perf_counter() + 0.1)
                self.assertIsNotNone(constructed)
                assignment, stats = constructed
                self.assertEqual(
                    (0, 0),
                    ev.count_hard_constraint_violations(
                        sm["seats"], groups, assignment, cfg
                    )[:2],
                )
                score = ev.calculate_soft_score(
                    sm["seats"], sm["seats"], groups, assignment, cfg["weights"], cfg
                )[0]
                self.assertAlmostEqual(-stats["construction_cost"], score, places=6)

    def test_both_and_single_protections_cannot_share(self):
        cfg = config()
        sm = _seatmap(1, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1B", both="Y", single="Y")]))
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        solver = cp_model.CpSolver()
        self.assertEqual(solver.solve(SeatModel(inst).model), cp_model.INFEASIBLE)

    def test_caregiver_may_care_for_two_children(self):
        cfg = config()
        sm = _seatmap(1, ["A", "B", "C"])
        groups = _groups(
            (1, [_pax(1, "1A", "CHD"), _pax(2, "1B"), _pax(3, "1C", "CHD")])
        )
        inst = Instance(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        m = SeatModel(inst)
        solver = cp_model.CpSolver()
        self.assertEqual(solver.solve(m.model), cp_model.OPTIMAL)
        self.assertEqual(
            (0, 0),
            ev.count_hard_constraint_violations(
                sm["seats"], groups, m.assignment(solver), cfg
            )[:2],
        )


class SolverContract(unittest.TestCase):
    def test_cabin_decomposition_preserves_whole_aircraft_score_and_bound(self):
        cfg = config()
        sm = _seatmap(2, ["A", "B", "C"])
        for seat in sm["seats"]:
            if seat["row"] == 1:
                seat["seatClass"] = "Business"
        groups = _groups(
            (1, [_pax(1, "1A"), _pax(2, "1B")]), (2, [_pax(1, "2A"), _pax(2, "2B")])
        )
        for p in groups[0]["psrs"]:
            p["cabin"] = "Business"
        points = []
        result = solve(
            sm["seats"],
            sm["seats"],
            groups,
            cfg["weights"],
            cfg,
            time_limit=2,
            workers=1,
            on_improvement=points.append,
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(set(result["cabin_runs"]), {"Business", "Economy"})
        self.assertAlmostEqual(
            result["total_score"], _brute_force(sm, groups, cfg), places=6
        )
        self.assertTrue(result["proven_optimal"])
        self.assertGreaterEqual(result["upper_bound"], result["total_score"] - 1e-7)
        self.assertEqual(points, result["history"])
        self.assertAlmostEqual(points[-1]["score"], result["total_score"], places=6)

    def test_invalid_input_rejected(self):
        cfg = config()
        for c in materialize_cases():
            if not c["expectedValid"]:
                with self.subTest(case=c["id"]), self.assertRaises(ValueError):
                    solve(
                        c["newSeatmapData"]["seats"],
                        c["oldSeatmapData"]["seats"],
                        c["groupsData"],
                        cfg["weights"],
                        cfg,
                        time_limit=1,
                    )

    def test_infeasible_timeout_and_empty_are_distinct(self):
        cfg = config()
        sm = _seatmap(1, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A", "CHD")]))
        r = solve(
            sm["seats"],
            sm["seats"],
            groups,
            cfg["weights"],
            cfg,
            time_limit=1,
            workers=1,
        )
        self.assertEqual(r["status"], "infeasible")
        self.assertIsNone(r["total_score"])
        self.assertFalse(r["proven_optimal"])
        groups = _groups((1, [_pax(1, "1A")]))
        r = solve(
            sm["seats"],
            sm["seats"],
            groups,
            cfg["weights"],
            cfg,
            time_limit=1e-9,
            workers=1,
        )
        self.assertEqual(r["status"], "no_solution")
        r = solve(
            sm["seats"], sm["seats"], [], cfg["weights"], cfg, time_limit=1, workers=1
        )
        self.assertEqual(r["status"], "complete")
        self.assertEqual(r["total_score"], 0)
        self.assertTrue(r["proven_optimal"])

    def test_budget_and_score_history(self):
        cfg = config()
        c = materialize_cases()[0]
        r = solve(
            c["newSeatmapData"]["seats"],
            c["oldSeatmapData"]["seats"],
            c["groupsData"],
            cfg["weights"],
            cfg,
            time_limit=1,
            workers=1,
        )
        self.assertEqual(r["status"], "complete")
        self.assertEqual((r["hard_violations"], r["unassigned"]), (0, 0))
        scores = [x["score"] for x in r["history"]]
        self.assertEqual(scores, sorted(scores))
        self.assertLess(r["wall_seconds"], 2)
        # The best objective in a generated pool is not a full-model certificate.
        if r["upper_bound"] is not None:
            self.assertGreaterEqual(r["upper_bound"], r["total_score"] - 1e-6)
        for budget in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                solve([], [], [], cfg["weights"], cfg, time_limit=budget)


if __name__ == "__main__":
    unittest.main()
