import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import heuristic_seat_allocator as rich
from src.allocation_evaluator import IncrementalSoftScorer
from tests import test_native_rich_repair as repair_tests
from tests.test_native_rich_elite import python_conflict_activation


class NativeRichRepairQueueTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_conflict_diversity_activation_boundaries(self):
        for variant in ("active", "short", "time_clamp", "large_limit", "few_free", "equal_maps",
                        "no_value_loss", "small_group", "single_protection", "ssr", "need_care",
                        "empty_new_seat", "nonempty_new_seat"):
            with self.subTest(variant=variant):
                case = self.synthetic([(101, {}), (101, {}),
                                       (202, {"mandatoryRule": {"needBothSideEmpty": "Y"}})])
                case["oldSeatmapData"] = copy.deepcopy(case["oldSeatmapData"])
                keep = {"1A", "1B", "1C", "2A", "2B", "2C", "2D"}
                case["newSeatmapData"]["seats"] = [s for s in case["newSeatmapData"]["seats"] if s["seatId"] in keep]
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=10.0, elite_patterns_per_group=2,
                                           structured_pattern_min_group_size=2, three_tier_min_business_time_seconds=10.0)
                ordinary = case["groupsData"][0]["psrs"]
                if variant == "short": config["algorithm"]["business_time_limit_seconds"] = 9.99
                if variant == "time_clamp":
                    config["algorithm"].update(business_time_limit_seconds=0.0, three_tier_min_business_time_seconds=0.05)
                if variant == "large_limit": config["algorithm"]["elite_patterns_per_group"] = 5
                if variant == "few_free": case["newSeatmapData"]["seats"].pop()
                if variant == "equal_maps": case["oldSeatmapData"] = copy.deepcopy(case["newSeatmapData"])
                if variant == "no_value_loss":
                    for p, seat in zip(ordinary, ("2A", "2B")): p["oldSeat"]["seatNum"] = seat
                if variant == "small_group": config["algorithm"]["structured_pattern_min_group_size"] = 3
                if variant == "single_protection":
                    case["groupsData"][1]["psrs"][0]["mandatoryRule"] = {"needSingleSideEmpty": "Y"}
                if variant == "ssr": ordinary[0]["ssr"] = "UM"
                if variant == "need_care": ordinary[1]["needCared"] = "Y"
                if variant == "empty_new_seat": ordinary[0]["newSeat"] = {}
                if variant == "nonempty_new_seat": ordinary[0]["newSeat"] = {"seatNum": ""}
                initial = [[0, "2A"], [1, "2B"], [2, "1B"]]
                assignments = {(101, 1): "2A", (101, 2): "2B", (202, 1): "1B"}
                scorer = IncrementalSoftScorer(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                              case["groupsData"], config["weights"], config)
                metrics = rich._group_repair_metrics(case["groupsData"], assignments, scorer.new_topology,
                                                     config["weights"], config, scorer.old_topology)
                expected = python_conflict_activation(case, config, metrics)
                self.assertEqual(expected, variant in {"active", "empty_new_seat"})
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}}}
                with tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    for name, value in {
                        "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                        "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                        "replay.json": {"assignments": initial, "repair_metrics_only": True},
                    }.items():
                        (work / name).write_text(json.dumps(value), encoding="utf-8")
                    run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                          str(work / "replay.json")], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout)["conflict_diversity_active"], expected)

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
