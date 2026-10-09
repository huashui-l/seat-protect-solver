import copy
import itertools
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from tests import test_native_rich_repair as repair_tests


class NativeRichGeometryTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def generate(self, case, seconds=3.0):
        config = copy.deepcopy(self.config)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"geometry": {"group_index": 0, "deadline_seconds": seconds}},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        patterns = json.loads(run.stdout)
        group = case["groupsData"][0]
        keys = [(group["groupId"], p["hostnum"]) for p in group["psrs"]]
        scores = []
        for pattern in patterns:
            assignment = {keys[p]: seat for p, seat in pattern["assignments"]}
            violations, unassigned, detail = evaluator.count_hard_constraint_violations(
                case["newSeatmapData"]["seats"], case["groupsData"], assignment, config)
            self.assertEqual((violations, unassigned), (0, 0), detail)
            occupied = set(assignment.values())
            blocked = [seat for seat, _ in pattern["blocked"]]
            self.assertEqual(len(blocked), len(set(blocked)))
            self.assertFalse(occupied.intersection(blocked))
            score, _ = evaluator.calculate_soft_score(case["newSeatmapData"]["seats"],
                case["oldSeatmapData"]["seats"], case["groupsData"], assignment, config["weights"], config)
            # These fixtures have no infants; the pattern objective is the exact group score.
            self.assertAlmostEqual(score, -pattern["master_cost"], places=8)
            scores.append(score)
        return patterns, scores

    def test_ordinary_matching_matches_tiny_exhaustive_optimum(self):
        case = self.synthetic([(101, {"oldSeat": {"seatNum": "1D", "seatValue": ""}}),
                               (101, {"oldSeat": {"seatNum": "1A", "seatValue": ""}})])
        for field in ("newSeatmapData", "oldSeatmapData"):
            case[field]["seats"] = [s for s in case[field]["seats"] if s["row"] == 1]
        patterns, scores = self.generate(case)
        self.assertTrue(patterns)
        best = float("-inf")
        for seats in itertools.permutations([s["seatId"] for s in case["newSeatmapData"]["seats"]], 2):
            assignment = {(101, i + 1): s for i, s in enumerate(seats)}
            violations, unassigned, _ = evaluator.count_hard_constraint_violations(
                case["newSeatmapData"]["seats"], case["groupsData"], assignment, self.config)
            if violations or unassigned:
                continue
            score, _ = evaluator.calculate_soft_score(case["newSeatmapData"]["seats"],
                case["oldSeatmapData"]["seats"], case["groupsData"], assignment, self.config["weights"], self.config)
            best = max(best, score)
        self.assertAlmostEqual(max(scores), best, places=8)

    def test_shared_caregiver_and_exclusive_empty_resource(self):
        case = self.synthetic([(101, {"needCared": "Y"}), (101, {"needCared": "Y"}), (101, {}),
            (101, {"ssr": "EXST", "mandatoryRule": {"needSingleSideEmpty": "Y", "sameSubRowNoOtherSSR": "Y"}})])
        for field in ("newSeatmapData", "oldSeatmapData"):
            case[field]["seats"] = [s for s in case[field]["seats"] if s["row"] == 1]
        patterns, _ = self.generate(case)
        self.assertTrue(patterns)
        self.assertTrue(all(len(p["blocked"]) == 1 for p in patterns))

    def test_fixed_anchor_and_conditional_ssr(self):
        case = self.synthetic([(101, {"ssr": "BLND"}), (101, {"ssr": "BLND"}),
            (101, {}), (101, {}),
            (101, {"ssr": "EXST", "newSeat": {"seatNum": "1D"},
                   "mandatoryRule": {"needSingleSideEmpty": "Y", "sameRowNoOtherSSR": "Y"}})])
        patterns, _ = self.generate(case)
        self.assertTrue(patterns)
        self.assertTrue(all(dict(p["assignments"])[4] == "1D" for p in patterns))

    def test_expired_deadline_returns_no_candidates(self):
        case = self.synthetic([(101, {}), (101, {})])
        patterns, _ = self.generate(case, seconds=0.0)
        self.assertEqual(patterns, [])
