import json
import subprocess
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "rich_python_reference_config.json"
CASE_PATH = ROOT / "data/generator_v3_2/d3_official_24cases_2026-09-07/forward/50_normal_groups.json"
EXECUTABLE = ROOT / "outputs/research/seat_protect_cpp.exe"
SUMMARY_PATH = ROOT / "outputs/research/native_feasibility_formal24/summary.json"


class NativeFeasibilitySolverTests(unittest.TestCase):
    def test_raw_cli_produces_externally_legal_scored_assignment(self):
        if not EXECUTABLE.exists():
            self.skipTest("seat_protect_cpp.exe has not been built")
        completed = subprocess.run(
            [
                str(EXECUTABLE), "--input", str(CASE_PATH),
                "--config", str(CONFIG_PATH), "--time-limit", "30", "--seed", "0",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("RAW_NATIVE", result["mode"])
        self.assertTrue(result["complete"])
        self.assertEqual(0, result["unassigned"])
        self.assertEqual(0, result["native_hard_violations"])

        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw = json.loads(CASE_PATH.read_text(encoding="utf-8"))
        new_seats = json.loads((ROOT / "data/3-3seatmap.json").read_text(encoding="utf-8"))["seats"]
        old_seats = json.loads((ROOT / "data/3-4-3seatmap.json").read_text(encoding="utf-8"))["seats"]
        assignments = {
            (item["groupId"], item["hostnum"]): item["seatId"]
            for item in result["assignments"]
        }
        violations, unassigned, _ = evaluator.count_hard_constraint_violations(
            new_seats, raw["groups"], assignments, config
        )
        score, _ = evaluator.calculate_soft_score(
            new_seats, old_seats, raw["groups"], assignments, config["weights"], config
        )
        self.assertEqual((0, 0), (violations, unassigned))
        self.assertAlmostEqual(score, result["native_score"], places=8)

    def test_frozen_formal24_feasibility_gate(self):
        if not SUMMARY_PATH.exists():
            self.skipTest("native feasibility Formal24 summary is absent")
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        self.assertEqual("RAW_NATIVE", summary["mode"])
        self.assertEqual(24, summary["case_count"])
        self.assertEqual(24, summary["valid_complete_count"])
        self.assertEqual(0, summary["unassigned_total"])
        self.assertEqual(0, summary["native_hard_violations_total"])
        self.assertEqual(0, summary["external_hard_violations_total"])
        self.assertEqual(24, summary["evaluator_consistent_count"])
        self.assertLessEqual(summary["max_score_error"], 1e-8)
        self.assertEqual(0, summary["provenance"]["production_python_callback_count"])


if __name__ == "__main__":
    unittest.main()
