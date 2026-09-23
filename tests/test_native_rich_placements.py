import copy
import dataclasses
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from src import exact_column_generation as exact
from tests import test_native_rich_repair as repair_tests


class NativeRichPlacementTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def replay(self, case, config=None, invalid=False):
        config = copy.deepcopy(config or self.config)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
        old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)

        def reference():
            fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
            return {str(g["groupId"]): [[dataclasses.asdict(p) for p in options]
                    for options in exact._placement_options(g, new, old, config["weights"], config, fixed)]
                    for g in case["groupsData"]}

        if invalid:
            with self.assertRaises(ValueError):
                reference()
        else:
            expected = json.loads(json.dumps(reference()))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"placement_options": True},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        if invalid:
            self.assertEqual(run.returncode, 3)
            self.assertTrue(run.stderr)
            return
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(actual.keys(), expected.keys())
        for group, passengers in expected.items():
            self.assertEqual(len(actual[group]), len(passengers))
            for native_options, options in zip(actual[group], passengers):
                self.assertEqual(len(native_options), len(options))
                for native, python in zip(native_options, options):
                    self.assertAlmostEqual(native.pop("individual_cost"), python.pop("individual_cost"), places=8)
                    self.assertEqual(native, python)
        return actual

    def test_public_complete_domains_and_input_order(self):
        for original in self.cases[:11]:
            for reverse in (False, True):
                with self.subTest(case=original["id"], reverse=reverse):
                    case = copy.deepcopy(original)
                    if reverse:
                        case["newSeatmapData"]["seats"] = case["newSeatmapData"]["seats"][::-1]
                    self.replay(case)

    def test_fixed_single_protection_is_not_arbitrarily_reserved(self):
        case = self.synthetic([(101, {"newSeat": {"seatNum": "1B"},
                                      "mandatoryRule": {"needSingleSideEmpty": "Y"}}), (202, {})])
        actual = self.replay(case)
        self.assertEqual([o["blocked"] for o in actual["101"][0]], [["1A"], ["1C"]])
        self.assertTrue({"1A", "1C"}.issubset({o["seat_id"] for o in actual["202"][0]}))

    def test_protection_modes_multiple_preferences_and_ssr_flags(self):
        case = self.synthetic([(101, {"ssr": "UM", "mandatoryRule": {
            "needSingleSideEmpty": "Y", "sameRowNoOtherSSR": "Y", "sameSubRowNoOtherSSR": "Y"}}),
            (202, {"mandatoryRule": {"needBothSideEmpty": "Y", "needSingleSideEmpty": "Y"}}),
            (303, {"ssr": "BSCT"}), (303, {}), (404, {"ssr": "UNKNOWN"})])
        for group in case["groupsData"]:
            for p in group["psrs"]:
                p["optionRule"] = [{"nearToilet": "Y", "weight": 2.0}, {"nearToilet": "N", "weight": 3.0}]
        for cross in (False, True):
            for require_two in (False, True):
                with self.subTest(cross=cross, require_two=require_two):
                    config = copy.deepcopy(self.config)
                    config["mandatory_rules"].update(both_side_empty_allow_cross_aisle=cross,
                                                     require_two_real_neighbors=require_two)
                    self.replay(case, config)

    def test_empty_domains_and_fixed_resource_conflicts_fail(self):
        for attributes in ({"cabin": "NO_SUCH_CABIN"}, {"newSeat": {"seatNum": "99Z"}},
                           {"ssr": "BSCT", "newSeat": {"seatNum": "2A"}}):
            with self.subTest(attributes=attributes):
                self.replay(self.synthetic([(101, attributes)]), invalid=True)
        case = self.synthetic([(101, {"newSeat": {"seatNum": "1B"},
                                      "mandatoryRule": {"needBothSideEmpty": "Y"}}),
                               (202, {"newSeat": {"seatNum": "1A"}})])
        self.replay(case, invalid=True)

    def test_dual_aisle_layouts_and_neighbor_order(self):
        for columns, aisles in (("ABCDEFGH", "BCFG"), ("ABCDEFGHIJ", "CDGH")):
            with self.subTest(columns=columns):
                case = self.synthetic([(101, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}),
                                       (202, {"ssr": "UM", "mandatoryRule": {"sameSubRowNoOtherSSR": "Y"}})])
                prototype = case["newSeatmapData"]["seats"][0]
                seats = []
                for row in (1, 2, 10):
                    for col in columns:
                        seats.append({**prototype, "seatId": f"{row}{col}", "row": row, "col": col,
                                      "isAisle": col in aisles, "isWindow": col in (columns[0], columns[-1])})
                case["oldSeatmapData"] = {"seats": copy.deepcopy(seats)}
                case["newSeatmapData"] = {"seats": seats[::-1]}
                self.replay(case)

    def test_domain_retains_overlapping_protection_for_later_joint_check(self):
        case = self.synthetic([(101, {"newSeat": {"seatNum": "1C"},
                                      "mandatoryRule": {"needBothSideEmpty": "Y"}}),
                               (202, {"mandatoryRule": {"needSingleSideEmpty": "Y"}})])
        config = copy.deepcopy(self.config)
        config["mandatory_rules"]["both_side_empty_allow_cross_aisle"] = True
        actual = self.replay(case, config)
        choices = [(o["seat_id"], o["blocked"]) for o in actual["202"][0]]
        self.assertIn(("1E", ["1D"]), choices)
        self.assertNotIn("1D", [o["seat_id"] for o in actual["202"][0]])
