"""Contract checks for the shared 24+16 three-route benchmark loader."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.benchmark_methods import _run_method, audit_optimality, load_d3_cases, load_d4_cases
from scripts.merge_method_benchmarks import merge_reports


class ThreeRouteBenchmarkCorpusTests(unittest.TestCase):
    def test_corpus_sizes_and_input_paths(self):
        d3 = load_d3_cases()
        d4 = load_d4_cases()
        self.assertEqual(24, len(d3))
        self.assertEqual(16, len(d4))
        self.assertEqual({"forward", "reverse"}, {case["direction"] for case in d3})
        self.assertEqual(4, len({case["direction"] for case in d4}))
        for case in d3 + d4:
            self.assertTrue(case["groups_path"].is_file(), case)
            self.assertTrue(case["old_seatmap_path"].is_file(), case)
            self.assertTrue(case["new_seatmap_path"].is_file(), case)


class OptimalityEvidenceTests(unittest.TestCase):
    def row(self, **fields):
        return dict(method="cg_gurobi", complete=True, violations=0, unassigned=0,
                    score=-10.0, proven_optimal=True, error=None) | fields

    def test_open_safe_and_lp_bounds_do_not_prove_optimality(self):
        for fields in ({"safe_bound": -5.0}, {"bound": -9.0, "bound_certified": True},
                       {"bound": -10.0, "bound_certified": False}):
            with self.subTest(fields=fields):
                self.assertEqual("unverified_claim", audit_optimality(self.row(**fields)))
        self.assertEqual("cg_root_lp_closed", audit_optimality(
            self.row(bound=-10.0, bound_certified=True)))

    def test_closed_tree_bound_can_prove_an_open_root_gap(self):
        row = self.row(bound=-9.0, bound_certified=True, bp_bound=-10.0, bp_bound_certified=True)
        self.assertEqual("cg_branch_price_closed", audit_optimality(row))
        row["bp_bound_certified"] = False
        self.assertEqual("unverified_claim", audit_optimality(row))

    def test_runner_retains_cabin_tree_evidence_without_top_level_flag(self):
        cabins = {"Economy": {"bp_bound_certified": True, "bp_bound": -10.0,
                              "branch_and_price": {"open_nodes": 0, "proven": True}}}
        raw = dict(assigned_seats={}, lp_bound=-9.0, lp_bound_certified=True,
                   bp_bound=-10.0, safe_bound=-8.0, cabins=cabins, proven_optimal=True)
        module = SimpleNamespace(solve=lambda *args: raw)
        with patch.dict(sys.modules, {"src.cg_gurobi": module}), \
                patch("scripts.benchmark_methods._load_case", return_value=([], [], [])), \
                patch("scripts.benchmark_methods._metrics", return_value=self.row()):
            result = _run_method({}, "cg_gurobi", 900, {})
        self.assertEqual("cg_branch_price_closed", result["optimality_certificate"])
        self.assertEqual(cabins, result["cabins"])
        self.assertEqual(-8.0, result["safe_bound"])
        self.assertEqual(-9.0, result["bound"])

    def test_direct_proof_requires_closed_bound_and_matching_objective(self):
        row = self.row(method="direct_gurobi", solver_status="optimal", bound=-10.0,
                       objective_error=0.0, mip_gap=0.0)
        self.assertEqual("direct_global_bound_closed", audit_optimality(row))
        for fields in ({"bound": -9.0}, {"objective_error": 0.01}, {"mip_gap": 0.01},
                       {"solver_status": "time_limit"}):
            with self.subTest(fields=fields):
                self.assertEqual("unverified_claim", audit_optimality(row | fields))

    def test_illegal_or_nonfinite_incumbent_cannot_have_a_certificate(self):
        for fields in ({"complete": False}, {"violations": 1}, {"unassigned": 1},
                       {"score": float("nan")}, {"score": None}, {"error": "failed"}):
            with self.subTest(fields=fields):
                self.assertEqual("invalid_incumbent", audit_optimality(
                    self.row(bound=-10.0, bound_certified=True) | fields))


class ThreeRouteMergeTests(unittest.TestCase):
    def setUp(self):
        self.cases = [{
            "case_id": "tiny", "corpus": "formal24", "direction": "forward",
            "groups_path": Path("groups.json"), "old_seatmap_path": Path("old.json"),
            "new_seatmap_path": Path("new.json"),
        }]
        self.source = {
            "cases": [{
                "case_id": "tiny", "corpus": "formal24", "direction": "forward",
                "runs": [
                    self.run_row("cpp_heuristic", 5),
                    self.run_row("cg_gurobi", 150, proven=True),
                    self.run_row("direct_gurobi", 150),
                    self.run_row("direct_gurobi", 600),
                ],
            }],
            "provenance": {
                "commit": "source-commit",
                "files_sha256": {
                    "config": "config-hash", "groups.json": "hash",
                    "old.json": "hash", "new.json": "hash",
                },
            },
        }

    @staticmethod
    def run_row(method, budget, proven=False):
        return {
            "method": method, "budget_seconds": budget, "elapsed_seconds": 1.0,
            "complete": True, "violations": 0, "unassigned": 0,
            "score": -1.0, "proven_optimal": proven, "error": None,
        }

    def merge(self, report=None):
        with patch("scripts.merge_method_benchmarks._sha256", return_value="hash"):
            return merge_reports([(Path("source.json"), report or self.source)], self.cases)

    def test_conditional_supplement_and_source_provenance(self):
        report = self.merge()
        self.assertEqual(4, len(report["cases"][0]["runs"]))
        self.assertEqual("source_1", report["cases"][0]["runs"][0]["source"])
        self.assertEqual(self.source["provenance"], report["provenance"]["source_1"]["run_provenance"])
        self.assertNotIn("source", self.source["cases"][0]["runs"][0])

    def test_rejects_missing_or_unnecessary_supplements(self):
        for remove, append in ((True, False), (False, True)):
            source = copy.deepcopy(self.source)
            runs = source["cases"][0]["runs"]
            if remove:
                runs.pop()
            if append:
                runs.append(self.run_row("cg_gurobi", 600))
            with self.subTest(remove=remove, append=append), self.assertRaisesRegex(ValueError, "supplement coverage"):
                self.merge(source)

    def test_rejects_duplicates_and_input_drift(self):
        source = copy.deepcopy(self.source)
        source["cases"][0]["runs"].append(self.run_row("cpp_heuristic", 5))
        with self.assertRaisesRegex(ValueError, "duplicate run"):
            self.merge(source)
        source = copy.deepcopy(self.source)
        source["provenance"]["files_sha256"]["groups.json"] = "changed"
        with self.assertRaisesRegex(ValueError, "input hash mismatch"):
            self.merge(source)


if __name__ == "__main__":
    unittest.main()
