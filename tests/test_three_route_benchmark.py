"""Contract checks for the shared 24+16 three-route benchmark loader."""
from __future__ import annotations

import unittest

from scripts.benchmark_three_routes import load_d3_cases, load_d4_cases


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


if __name__ == "__main__":
    unittest.main()
