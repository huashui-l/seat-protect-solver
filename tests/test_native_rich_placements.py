import copy
import dataclasses
import json
import random
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

    def replay_patterns(self, case, active_types):
        config = copy.deepcopy(self.config)
        config["_column_generation_active_ssr_types"] = active_types
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
        old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
        fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
        baby_cost = exact._baby_pairs(new, case["groupsData"], config["weights"], config)
        requests, expected = [], []
        rng = random.Random(4109)
        for g, group in enumerate(case["groupsData"]):
            options = exact._placement_options(group, new, old, config["weights"], config, fixed)
            for iteration in range(8):
                choices = [[p, (0 if iteration == 0 else rng.randrange(len(row)))] for p, row in enumerate(options)]
                if iteration % 2:
                    choices.reverse()
                selected = [options[p][i] for p, i in choices]
                pattern = exact._pattern_from_placements(group, selected, new, config["weights"], config, baby_cost)
                expected.append(dict(group_id=pattern.group_id, signature=pattern.signature,
                                     assignments=pattern.assignments, blocked_by=pattern.blocked_by,
                                     seat_resources=sorted(pattern.seat_resources), infant_seats=sorted(pattern.infant_seats),
                                     occupied_seats=sorted(pattern.occupied_seats), ssr_all=pattern.ssr_all,
                                     ssr_flagged=pattern.ssr_flagged, master_cost=pattern.master_cost,
                                     caregiver_ok=exact._caregiver_ok(group, selected, new, config)))
                requests.append(dict(group_index=g, choices=choices))
        expected = json.loads(json.dumps(expected))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_assembly": requests, "active_ssr_types": active_types},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(len(actual), len(expected))
        for native, python in zip(actual, expected):
            with self.subTest(group=python["group_id"], signature=python["signature"]):
                self.assertAlmostEqual(native.pop("master_cost"), python.pop("master_cost"), places=8)
                # Resource coefficients are keyed rows, not search-order lists.
                for name in ("ssr_all", "ssr_flagged"):
                    native[name] = {json.dumps(k): v for k, v in native[name]}
                    python[name] = {json.dumps(k): v for k, v in python[name]}
                self.assertEqual(native, python)
        return actual

    def test_public_pattern_assembly_coefficients_costs_and_caregivers(self):
        for case in self.cases[:11]:
            with self.subTest(case=case["id"]):
                active = sorted({p["ssr"] for g in case["groupsData"] for p in g["psrs"] if p.get("ssr")})
                self.replay_patterns(case, active)

    def test_pattern_flag_expansion_and_infant_pair_correction(self):
        case = self.synthetic([(101, {"ssr": "BSCT", "mandatoryRule": {"sameRowNoOtherSSR": "Y"}}),
                               (101, {}), (101, {"ssr": "BSCT"}),
                               (202, {"ssr": "UM", "mandatoryRule": {"sameSubRowNoOtherSSR": "Y"}}),
                               (202, {"ssr": "UM"}),
                               (303, {"needCared": "Y"}), (303, {}),
                               (404, {"mandatoryRule": {"needSingleSideEmpty": "Y"}})])
        for active in ([], ["BSCT", "UM", "UNKNOWN"]):
            with self.subTest(active=active):
                actual = self.replay_patterns(case, active)
                self.assertTrue(any(p["infant_seats"] for p in actual))
                self.assertTrue(any(not p["caregiver_ok"] for p in actual))
                self.assertTrue(any(p["blocked_by"] for p in actual))
                self.assertTrue(any(count > 1 for p in actual for count in p["ssr_all"].values()))
                if active:
                    self.assertTrue(any('UNKNOWN' in key for p in actual for key in p["ssr_flagged"]))

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
