import unittest

from src import compare_heuristic_column_solutions as comparison


class SolutionComparisonTest(unittest.TestCase):
    def test_movement_components_include_chains_and_isolated_groups(self):
        heuristic = {
            (1, 1): "1A",
            (2, 1): "1B",
            (3, 1): "2A",
        }
        column = {
            (1, 1): "1B",
            (2, 1): "1A",
            (3, 1): "2B",
        }
        self.assertEqual(
            comparison._movement_components(heuristic, column),
            [(1, 2), (3,)],
        )


if __name__ == "__main__":
    unittest.main()
