import unittest

from native.run_native_formal24 import (
    paired_python_quality, construction_statistic, reference_gap_percent, gap_statistics,
)


class NativeFormal24MetricTests(unittest.TestCase):
    def test_maximization_gap_preserves_negative_improvements_and_small_denominator(self):
        self.assertEqual(reference_gap_percent(-102, -100), 2.0)
        self.assertEqual(reference_gap_percent(-99, -100), -1.0)
        self.assertEqual(reference_gap_percent(-0.02, 0), 2.0)
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                reference_gap_percent(-100, bad)

    def test_gap_thresholds_and_missing_references_are_distinct(self):
        result = gap_statistics([{"gap_percent": gap} for gap in (-1, 1, 2, 5, 6, None)])
        self.assertEqual(result["reference_available_count"], 5)
        self.assertEqual(result["reference_unavailable_count"], 1)
        self.assertEqual(result["mean_gap_percent"], 2.6)
        self.assertEqual(result["median_gap_percent"], 2)
        self.assertEqual(result["worst_gap_percent"], 6)
        self.assertEqual([result[f"cases_le_{i}_percent"] for i in (1, 2, 5)], [2, 3, 4])
        missing = gap_statistics([{"gap_percent": None}])
        self.assertIsNone(missing["mean_gap_percent"])
        self.assertIsNone(missing["cases_le_2_percent"])

    def test_cabin_statistics_sum_counts_but_require_all_complete(self):
        result = {"cabin_decomposition": {"cabins": {
            "Business": {"result": {"dfs_nodes": 12, "from_scratch_complete": True}},
            "Economy": {"result": {"dfs_nodes": 50, "from_scratch_complete": False}},
        }}}
        self.assertEqual(construction_statistic(result, "dfs_nodes"), 62)
        self.assertFalse(construction_statistic(result, "from_scratch_complete"))
        self.assertEqual(construction_statistic({"dfs_nodes": 7}, "dfs_nodes"), 7)

    def test_positive_mean_cannot_hide_one_regression(self):
        result = paired_python_quality([
            {"case_id": "improved", "delta_vs_python": 100.0},
            {"case_id": "regressed", "delta_vs_python": -0.01},
        ])
        self.assertGreater(result["mean_delta"], 0)
        self.assertFalse(result["all_cases_nonregressing"])
        self.assertEqual(result["regressed_cases"], ["regressed"])
        self.assertEqual((result["improve"], result["tie"], result["regress"]), (1, 0, 1))

    def test_tolerance_is_per_case_and_inclusive(self):
        result = paired_python_quality([
            {"case_id": str(i), "delta_vs_python": delta}
            for i, delta in enumerate((-1e-8, 0.0, 1e-8))
        ])
        self.assertTrue(result["all_cases_nonregressing"])
        self.assertEqual(result["tie"], 3)

    def test_empty_or_nonfinite_results_cannot_pass(self):
        for rows in ([], [{"case_id": "x", "delta_vs_python": float("nan")}],
                     [{"case_id": "x", "delta_vs_python": float("inf")} ]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                paired_python_quality(rows)
