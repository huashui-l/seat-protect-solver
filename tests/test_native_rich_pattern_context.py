import ast
import copy
import json
import random
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import heuristic_seat_allocator as rich
from tests import test_native_rich_repair as repair_tests
from tests.test_native_rich_pipeline import construction_prefix

ROOT = Path(__file__).resolve().parents[1]


class NativeRichPatternContextTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def replay(self, case, initial, patterns, pairs, components, config=None):
        config = copy.deepcopy(config or self.config)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        new = case["newSeatmapData"]["seats"]
        topology = rich.build_seat_topology(new, config)
        globals_ = dict(zip(("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW",
                            "_SEAT_X", "_SEAT_ROW_INDEX"), topology))
        globals_["_ACTIVE_CONFIG"] = config
        with patch.multiple(rich, **globals_):
            seats = {s["seatId"]: rich.Seat(s) for s in new}
            groups = [rich.Group(g) for g in case["groupsData"]]
            passengers = [(g.group_id, p) for g in groups for p in g.passengers]
            objects = {(gid, p.hostnum): p for gid, p in passengers}
            key_indexes = {key: i for i, key in enumerate(objects)}
            group_indexes = {g.group_id: i for i, g in enumerate(groups)}
            context = rich.AssignmentContext(seats)
            for p, seat, *block in initial:
                gid, passenger = passengers[p]
                self.assertTrue(context.assign_passenger(passenger, seat, gid, chosen_block=block[0] if block else None))

            def snapshot(candidate):
                if candidate is None: return None
                owner = {seat: key_indexes[key] for key, seat in candidate.assigned_seats.items()}
                return dict(assignments=[candidate.assigned_seats.get(key) for key in objects],
                    blocked=[sorted(candidate.assigned_blocked.get(key, ())) for key in objects],
                    blocked_count=[candidate.blocked_counts.get(s, 0) for s in seats],
                    seat_to_passenger=[owner.get(s, -1) for s in seats],
                    owner_group_by_seat=[group_indexes[candidate.owner_group_by_seat[s]] if s in candidate.owner_group_by_seat else -1 for s in seats],
                    seat_ssr_passenger=[owner[s] if s in candidate.seat_ssr_map else -1 for s in seats],
                    assignment_order=[key_indexes[key] for key in candidate.assigned_seats])

            before = snapshot(context)
            expected_conflicts = [rich._patterns_have_conditional_ssr_conflict(
                patterns[a]["group_id"], patterns[a], patterns[b]["group_id"], patterns[b], objects, seats) for a, b in pairs]
            tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
            outer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "improve_protected_multigroup_pattern_mip")
            function = next(n for n in outer.body if isinstance(n, ast.FunctionDef) and n.name == "rebuild_candidate")
            elite = {}
            for i, pattern in enumerate(patterns): elite.setdefault(pattern["group_id"], {})[i] = pattern
            namespace = dict(rich.__dict__, context=context, passenger_objects=objects, config=config,
                keys_by_group={g.group_id: [(g.group_id, p.hostnum) for p in g.passengers] for g in groups}, elite_pattern_store=elite)
            exec(compile(ast.Module(body=[function], type_ignores=[]), "frozen_rebuild_candidate", "exec"), namespace)
            expected = []
            for component in components:
                choices = {patterns[i]["group_id"]: i for i in component}
                candidate = namespace["rebuild_candidate"](tuple(sorted(choices)), choices)
                expected.append(snapshot(candidate))
            self.assertEqual(before, snapshot(context))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_context": dict(initial=initial, patterns=patterns, pairs=pairs, components=components)},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(actual["conflicts"], expected_conflicts)
        for item in [actual["initial"], *actual["rebuilt"]]:
            if item is not None: item["blocked"] = [sorted(seats) for seats in item["blocked"]]
        self.assertEqual(actual["initial"], before)
        self.assertEqual(len(actual["rebuilt"]), len(expected))
        for component, native, python in zip(components, actual["rebuilt"], expected):
            with self.subTest(case=case["id"], component=component): self.assertEqual(native, python)
        return actual

    def test_public_pattern_reconstruction_and_conflicts(self):
        rng = random.Random(20260923)
        prefix = construction_prefix()
        successes = failures = conflicts = 0
        for case in self.cases[:11]:
            config = copy.deepcopy(self.config)
            config["algorithm"].update(business_time_limit_seconds=120.0, construction_time_budget=60.0,
                                       small_group_dfs_time_limit=20.0)
            context = prefix(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                             case["groupsData"], config["weights"], config)["context"]
            keys = [(g["groupId"], p["hostnum"]) for g in case["groupsData"] for p in g["psrs"]]
            initial = []
            for key, seat in context.assigned_seats.items():
                blocks = sorted(context.assigned_blocked.get(key, ()))
                initial.append([keys.index(key), seat, *blocks[:1]])
            patterns, by_group = [], []
            seat_ids = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            for group in case["groupsData"]:
                gid = group["groupId"]
                indexes = []
                for variant in range(12):
                    placements = [(p["hostnum"], context.assigned_seats[gid, p["hostnum"]])
                                  for p in group["psrs"] if (gid, p["hostnum"]) in context.assigned_seats] if variant == 0 else [
                                      (p["hostnum"], rng.choice(seat_ids)) for p in group["psrs"]]
                    blocked = [(p["hostnum"], sorted(context.assigned_blocked.get((gid, p["hostnum"]), ())))
                               for p in group["psrs"]] if variant == 0 else []
                    indexes.append(len(patterns))
                    patterns.append(dict(group_id=gid, assignments=placements, blocked_by_host=blocked))
                by_group.append(indexes)
            pairs = [(a, b) for a in range(len(patterns)) for b in range(a + 1, len(patterns))
                     if patterns[a]["group_id"] != patterns[b]["group_id"]]
            components = [[row[0] for row in by_group]]
            for _ in range(80):
                chosen = rng.sample(by_group, min(len(by_group), rng.randint(1, 4)))
                components.append([rng.choice(row) for row in chosen])
            actual = self.replay(case, initial, patterns, pairs, components, config)
            successes += sum(s is not None for s in actual["rebuilt"])
            failures += sum(s is None for s in actual["rebuilt"])
            conflicts += sum(actual["conflicts"])
        self.assertGreater(successes, 0)
        self.assertGreater(failures, 0)
        self.assertGreater(conflicts, 0)

    def test_third_ssr_type_activation_and_protection_exclusions(self):
        case = self.synthetic([(30, {"ssr": "TEST_A"}), (20, {"ssr": "TEST_A"}),
                               (10, {"ssr": "TEST_B", "mandatoryRule": {"sameRowNoOtherSSR": "Y"}})])
        patterns = [dict(group_id=10, assignments=[(1, seat)], blocked_by_host=[]) for seat in ("1C", "2A")]
        actual = self.replay(case, [[0, "1A"], [1, "1B"]], patterns, [], [[0], [1]])
        self.assertIsNone(actual["rebuilt"][0])
        self.assertIsNotNone(actual["rebuilt"][1])
        case = self.synthetic([(20, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}), (20, {}),
                               (10, {})])
        patterns = [dict(group_id=20, assignments=[(1, "1B"), (2, "1A")], blocked_by_host=blocks)
                    for blocks in ([], [(1, ["1A"])], [(1, ["1C"])])]
        actual = self.replay(case, [[2, "2A"]], patterns, [], [[0], [1], [2]])
        for candidate in actual["rebuilt"]:
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate["blocked"][0], ["1C"])

    def test_conditional_profile_activates_all_types_but_ignores_non_ssr_flags(self):
        for flag, seat, expected in (("sameRowNoOtherSSR", "1D", True),
                                     ("sameSubRowNoOtherSSR", "1D", False),
                                     ("sameSubRowNoOtherSSR", "1C", True)):
            with self.subTest(flag=flag, seat=seat):
                case = self.synthetic([(30, {"ssr": "TEST_A"}), (30, {"ssr": "TEST_A"}),
                    (20, {"ssr": "TEST_B", "mandatoryRule": {flag: "Y"}}),
                    (10, {"mandatoryRule": {flag: "Y"}})])
                patterns = [dict(group_id=30, assignments=[(1, "1A"), (2, "1B")], blocked_by_host=[]),
                            dict(group_id=20, assignments=[(1, seat)], blocked_by_host=[]),
                            dict(group_id=10, assignments=[(1, seat)], blocked_by_host=[])]
                actual = self.replay(case, [], patterns, [[0, 1], [1, 0], [0, 2]], [])
                self.assertEqual(actual["conflicts"], [expected, expected, False])


if __name__ == "__main__":
    unittest.main()
