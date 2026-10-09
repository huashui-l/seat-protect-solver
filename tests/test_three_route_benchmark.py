"""Contract checks for the shared 24+16 three-route benchmark loader."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_three_routes import load_d3_cases, load_d4_cases
from scripts.merge_three_route_benchmarks import merge_reports


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
        with patch("scripts.merge_three_route_benchmarks._sha256", return_value="hash"):
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
