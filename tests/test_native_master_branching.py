import json
import os
import subprocess
import unittest
from pathlib import Path


class NativeMasterBranchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            raise unittest.SkipTest("set SEAT_PROTECT_NATIVE_EXE to a built native CLI")
        cls.executable = Path(executable).resolve().with_name("native_master_benchmark.exe")
        if not cls.executable.exists():
            raise unittest.SkipTest("native_master_benchmark.exe has not been built")

    def solve(self, radius=None, center="-", order="original", check=True,
              attempts=1, growth=1, maximum=2):
        # Improving either group alone collides with the other incumbent seat.
        # The improving complete assignment requires changing both groups.
        payload = "MASTER_V1 2 2 0 0 1 4\nGROUP 0 10\nGROUP 1 20\n"
        for pid, group, seat, cost, keep in (
            (1, 0, 0, 10, 1), (2, 0, 1, 0, 0),
            (3, 1, 1, 10, 1), (4, 1, 0, 0, 0),
        ):
            payload += (f"PATTERN {pid} {group} {cost} {keep} "
                        f"1 {seat} 0 0 0 1 {seat} 1 {group} {seat} 0\n")
        args = [str(self.executable), "1", order, "0", "1", center]
        if radius is not None:
            args.append(str(radius))
            args.extend(map(str, (attempts, growth, maximum)))
        run = subprocess.run(args, input=payload, text=True, capture_output=True)
        if not check:
            return run
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_radius_blocks_then_allows_two_group_exchange(self):
        for radius, expected in ((0, 20), (1, 20), (2, 0), (20, 0)):
            with self.subTest(radius=radius):
                result = self.solve(radius)
                self.assertEqual(result["objective_cost"], expected)
                self.assertFalse(result["used_fallback"])

    def test_explicit_center_and_pattern_reordering(self):
        for order in ("original", "reversed", "random"):
            with self.subTest(order=order):
                result = self.solve(0, "2,4", order)
                self.assertEqual(result["objective_cost"], 0)
                self.assertFalse(result["used_fallback"])
        self.assertEqual(self.solve(1, "1,2", check=False).returncode, 2)
        self.assertEqual(self.solve(1, "1,99", check=False).returncode, 2)

    def test_disabled_option_preserves_legacy_model(self):
        legacy = self.solve()
        disabled = self.solve(-1)
        self.assertEqual(legacy["model_coefficient_hash"], disabled["model_coefficient_hash"])
        self.assertEqual(legacy["objective_cost"], 0)
        self.assertEqual(legacy["rows"] + 1, self.solve(1)["rows"])

    def test_retries_expand_fixed_center_and_exclude_previous_selection(self):
        result = self.solve(1, attempts=3)
        self.assertEqual(result["objective_cost"], 0)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["radius_history"], [1, 2, 2])
        self.assertIsNone(result["mip_dual_bound"])
        self.assertIsNone(result["mip_gap"])
        # The third solve exhausts both assignments. Keep the best earlier one.
        self.assertEqual(result["status"], "Infeasible")

    def test_radius_cap_prevents_exchange_and_preserves_incumbent(self):
        result = self.solve(1, attempts=3, maximum=1)
        self.assertEqual(result["objective_cost"], 20)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["radius_history"], [1, 1])
