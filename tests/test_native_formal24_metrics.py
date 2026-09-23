import unittest

from native.run_native_formal24 import paired_python_quality


class NativeFormal24MetricTests(unittest.TestCase):
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
