import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from src import exact_column_generation as exact
from tests.general_validation_case_factory import materialize_cases

ROOT = Path(__file__).resolve().parents[1]


class NativePlacementDomainTests(unittest.TestCase):
    def test_public_domains_match_python_placement_options(self):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            self.skipTest("set SEAT_PROTECT_NATIVE_EXE to a built native CLI")
        probe = Path(executable).resolve().with_name("native_full_core_probe.exe")
        base_config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        checked = 0
        for case in materialize_cases():
            with self.subTest(case=case["id"]), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                config = dict(base_config)
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}
                }}
                payloads = {
                    "old.json": case["oldSeatmapData"],
                    "new.json": case["newSeatmapData"],
                    "case.json": {"caseId": case["id"], "direction": "public-test",
                                  "groups": case["groupsData"]},
                    "config.json": config,
                }
                for name, payload in payloads.items():
                    (work / name).write_text(json.dumps(payload), encoding="utf-8")
                run = subprocess.run([str(probe), "--input", str(work / "case.json"),
                                      "--config", str(work / "config.json"),
                                      "--placement-domains"], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads(run.stdout)
                seats = case["newSeatmapData"]["seats"]
                new = evaluator.SeatTopology(seats, config)
                old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
                fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
                index = 0
                for group in case["groupsData"]:
                    expected = exact._placement_options(group, new, old, config["weights"], config, fixed)
                    for passenger, placements in zip(group["psrs"], expected):
                        actual = {}
                        both = passenger.get("mandatoryRule", {}).get("needBothSideEmpty") == "Y"
                        for seat, block, score in result["placement_domains"][index]:
                            seat_id = result["seats"][seat]["id"]
                            if both:
                                blocked = new.row_neighbors(seat_id, allow_cross_aisle=config.get(
                                    "mandatory_rules", {}).get("both_side_empty_allow_cross_aisle", False))
                            else:
                                blocked = [] if block < 0 else [result["seats"][block]["id"]]
                            actual[(seat_id, tuple(sorted(blocked)))] = score
                        wanted = {(option.seat_id, tuple(sorted(option.blocked))): -option.individual_cost
                                  for option in placements}
                        self.assertEqual(actual.keys(), wanted.keys(),
                                         (case["id"], group["groupId"], passenger["hostnum"]))
                        for placement, score in wanted.items():
                            self.assertAlmostEqual(actual[placement], score, places=10)
                        index += 1
                        checked += 1
        self.assertGreater(checked, 0)
