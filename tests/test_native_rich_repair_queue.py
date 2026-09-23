import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import heuristic_seat_allocator as rich
from src.allocation_evaluator import IncrementalSoftScorer
from tests import test_native_rich_repair as repair_tests


class NativeRichRepairQueueTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)

    def test_metrics_and_queue_match_actual_python(self):
        scenarios = [(case, partial, seconds, weight) for case in self.cases[:11]
                     for partial, seconds, weight in ((False, 5.0, -2.0), (True, 10.0, -2.0),
                                                      (True, 9.99, 0.0), (False, 60.0, 0.0))]
        special = copy.deepcopy(self.cases[0])
        for group in special["groupsData"]:
            for passenger in group["psrs"]:
                passenger["optionRule"] = [{"nearToilet": "Y", "weight": 2.0},
                                            {"nearToilet": "N", "weight": 3.0}]
        scenarios.append((special, False, 10.0, -2.0))
        for case, partial, seconds, weight in scenarios:
            with self.subTest(case=case["id"], partial=partial, seconds=seconds, weight=weight):
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=seconds,
                                           three_tier_min_business_time_seconds=10.0)
                config["weights"]["w_c"] = weight
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}}}
                passengers = [(g["groupId"], p) for g in case["groupsData"] for p in g["psrs"]]
                initial, assignments = [], {}
                for index, (gid, passenger) in enumerate(passengers):
                    seat = passenger.get("newSeat", {}).get("seatNum")
                    if not seat and (not partial or index % 3):
                        seat = case["referenceAssignments"].get((gid, passenger["hostnum"]))
                    if seat:
                        initial.append([index, seat])
                        assignments[gid, passenger["hostnum"]] = seat
                scorer = IncrementalSoftScorer(case["newSeatmapData"]["seats"],
                                              case["oldSeatmapData"]["seats"], case["groupsData"],
                                              config["weights"], config)
                metrics = rich._group_repair_metrics(case["groupsData"], assignments, scorer.new_topology,
                                                     config["weights"], config, scorer.old_topology)
                expected = sorted(metrics.values(), key=rich._repair_priority_key)
                with tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    for name, value in {
                        "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                        "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"],
                        "config.json": config, "replay.json": {"assignments": initial, "repair_metrics_only": True},
                    }.items():
                        (work / name).write_text(json.dumps(value), encoding="utf-8")
                    run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                          str(work / "replay.json")], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)["repair_queue"]
                self.assertEqual([m["group_id"] for m in actual], [m["group_id"] for m in expected])
                for native, reference in zip(actual, expected):
                    self.assertEqual(native.keys(), reference.keys())
                    for key, value in reference.items():
                        self.assertAlmostEqual(native[key], value, places=8, msg=key)
