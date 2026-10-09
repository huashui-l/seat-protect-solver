import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import test_native_rich_repair as repair_tests


class NativeRichFallbackTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def replay(self, assignment):
        case = self.synthetic([
            (10, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}),
            (20, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}),
            (30, {"newSeat": {"seatNum": "1A"}}),
            (40, {"mandatoryRule": {"needBothSideEmpty": "Y"}}),
            (50, {"ssr": "BLND"}), (50, {}),
        ])
        # One six-seat block: D can reserve C or E, while B can only reserve C
        # because A is occupied. The first passenger's greedy C choice fails.
        for key in ("oldSeatmapData", "newSeatmapData"):
            for seat in case[key]["seats"]:
                seat["isAisle"] = False
        config = copy.deepcopy(self.config)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": "fallback-resource-test", "direction": "public-test",
                              "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"],
                "config.json": config, "replay.json": {"fallback_assignment": assignment},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"),
                                  str(work / "config.json"), str(work / "replay.json")],
                                 capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_legal_fallback_matches_resources_and_seeds_complete_patterns(self):
        result = self.replay(["1D", "1B", "1A", "2B", "3A", "3B"])
        self.assertTrue(result["seeded"])
        self.assertEqual(result["blocked"][:3], [["1E"], ["1C"], []])
        self.assertEqual(set(result["blocked"][3]), {"2A", "2C"})
        self.assertEqual(len(result["elite"]), 5)
        for patterns in result["elite"].values():
            self.assertEqual(len(patterns), 1)
            self.assertEqual(patterns[0]["source"], "q0_fallback")
            self.assertTrue(patterns[0]["pinned"])

    def test_incomplete_conflicting_fixed_or_caregiver_invalid_fallback_is_rejected(self):
        for assignment in [
            ["1D", None, "1A", "2B", "3A", "3B"],
            ["1D", "1B", "1A", "2B", "3A", "3A"],
            ["1D", "1B", "1F", "2B", "3A", "3B"],
            ["1D", "1B", "1A", "2B", "3A", "3F"],
        ]:
            with self.subTest(assignment=assignment):
                result = self.replay(assignment)
                self.assertFalse(result["seeded"])
                self.assertEqual(result["elite"], {})


if __name__ == "__main__":
    unittest.main()
