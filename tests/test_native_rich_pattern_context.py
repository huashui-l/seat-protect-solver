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
from tests.test_native_rich_elite import python_capture_namespace
from src import allocation_evaluator as evaluator

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

    def protected_replay(self, case, algorithm, variant="active", initial_override=None, extra_patterns=(), stage=False, special=False):
        config = copy.deepcopy(self.config)
        config["algorithm"].update(business_time_limit_seconds=120.0, adaptive_stage_budgets=False,
            construction_time_budget=60.0, small_group_dfs_time_limit=20.0,
            enable_protected_multigroup_pattern_mip=True, enable_priority_multigroup_pattern_mip=True,
            protected_dynamic_relocation_seconds=120.0, structured_pattern_dfs_per_group=120.0)
        config["algorithm"].update(algorithm)
        config.setdefault("column_generation", {})["dfs_node_limit"] = 100
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        global_names = ("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW", "_SEAT_X", "_SEAT_ROW_INDEX",
                        "_ACTIVE_CONFIG", "_OLD_SEATS", "_OLD_SEAT_X", "_OLD_SEAT_OWNER_REGRET", "_GROUP_COMPACT_CACHE")
        with patch.multiple(rich, **{name: getattr(rich, name) for name in global_names}):
            built = construction_prefix()(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                          case["groupsData"], config["weights"], config)
            groups = built["groups"]
            keys = [(g.group_id, p.hostnum) for g in groups for p in g.passengers]
            if initial_override is None:
                context = built["context"]
                if len(context.assigned_seats) != len(keys):
                    context = rich.AssignmentContext(built["seats"])
                    for g in groups:
                        for p in g.passengers:
                            self.assertTrue(context.assign_passenger(p, case["referenceAssignments"][g.group_id, p.hostnum], g.group_id))
            else:
                context = rich.AssignmentContext(built["seats"])
                passengers = [(g.group_id, p) for g in groups for p in g.passengers]
                for index, seat, *block in initial_override:
                    gid, passenger = passengers[index]
                    self.assertTrue(context.assign_passenger(passenger, seat, gid, chosen_block=block[0] if block else None))
            initial = [[keys.index(key), seat, *sorted(context.assigned_blocked.get(key, ()))[:1]]
                       for key, seat in context.assigned_seats.items()]
            scorer = evaluator.IncrementalSoftScorer(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                                    case["groupsData"], config["weights"], config)
            captured = python_capture_namespace(context, groups, scorer, 12)
            captured["capture_stage_patterns"]("current")
            if initial_override is None:
                rich.generate_structured_group_patterns(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                    case["groupsData"], context, scorer, config["weights"], config, rich.time.perf_counter() + 120,
                    captured["record_elite_pattern"])
            for gid, assignments, blocked in extra_patterns:
                proposal = dict(context.assigned_seats)
                proposal.update({(gid, host): seat for host, seat in assignments})
                captured["record_elite_pattern"](gid, tuple(((gid, host), seat) for host, seat in assignments),
                    scorer.components(proposal, {gid})["total_soft_score"], "fixture", dict(blocked), False)
            elite = captured["elite_pattern_store"]
            patterns = [dict(group_id=gid, **pattern) for gid, entries in elite.items() for pattern in entries.values()]
            duration = -1.0 if variant == "expired" else 120.0
            initial_score = scorer.components(context.assigned_seats)["total_soft_score"]
            if stage:
                tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
                function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_allocation_single_cabin")
                def assignment_index(name):
                    return next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
                statements = function.body[assignment_index("protected_max_passes"):assignment_index("protected_mip_finished")]
                namespace = dict(rich.__dict__, new_seats_data=case["newSeatmapData"]["seats"], old_seats_data=case["oldSeatmapData"]["seats"],
                    groups_data=case["groupsData"], groups=groups, context=context, elite_pattern_store=elite, stage_scorer=scorer,
                    weights=config["weights"], config=config, algorithm_config=config["algorithm"],
                    protected_mip_deadline=rich.time.perf_counter() + duration, capture_stage_patterns=captured["capture_stage_patterns"])
                exec(compile(ast.Module(body=statements, type_ignores=[]), "frozen_protected_passes", "exec"), namespace)
                expected = namespace["protected_multigroup_mip_diagnostics"]
                expected_special = rich.generate_special_dual_pricing_patterns(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                    case["groupsData"], context, scorer, config["weights"], config, elite, namespace["protected_mip_deadline"],
                    captured["record_elite_pattern"], special)
            else:
                expected = rich.improve_protected_multigroup_pattern_mip(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                    case["groupsData"], groups, context, elite, scorer, config["weights"], config, rich.time.perf_counter() + duration)
            final_score = scorer.components(context.assigned_seats)["total_soft_score"]
            expected_elite = {str(gid): list(entries.values()) for gid, entries in elite.items()}
            expected_assignment = [context.assigned_seats.get(key) for key in keys]
            expected_blocks = [sorted(context.assigned_blocked.get(key, ())) for key in keys]
            expected_order = [keys.index(key) for key in context.assigned_seats]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_context": dict(protected_mip=True, initial=initial, patterns=patterns,
                                                          pairs=[], deadline_seconds=duration, protected_stage=stage, special_enabled=special)},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        if stage:
            self.assertAlmostEqual(actual["selected_score"], max(initial_score, final_score), places=8)
            self.assertEqual(actual["selected_count"], len(keys) if final_score > initial_score + 1e-9 else 0)
            actual["special"].pop("seconds"); expected_special.pop("seconds")
            self.assertEqual(len(actual["special"]["accepted_patterns"]), len(expected_special["accepted_patterns"]))
            for a, b in zip(actual["special"]["accepted_patterns"], expected_special["accepted_patterns"]):
                self.assertAlmostEqual(a.pop("reduced_cost"), b.pop("reduced_cost"), places=8)
            self.assertEqual(actual["special"], json.loads(json.dumps(expected_special)))
        self.assertEqual(actual["state"]["assignments"], expected_assignment)
        self.assertEqual([sorted(row) for row in actual["state"]["blocked"]], expected_blocks)
        self.assertEqual(actual["state"]["assignment_order"], expected_order)
        native_d = actual["diagnostics"]
        native_d.pop("seconds"); expected.pop("seconds")
        self.assertAlmostEqual(native_d.pop("score_improvement"), expected.pop("score_improvement"), places=8)
        self.assertEqual(len(native_d["accepted_components"]), len(expected["accepted_components"]))
        for a, b in zip(native_d["accepted_components"], expected["accepted_components"]):
            a.pop("elapsed_seconds"); b.pop("elapsed_seconds")
            self.assertAlmostEqual(a.pop("delta"), b.pop("delta"), places=8)
        self.assertEqual(native_d, json.loads(json.dumps(expected)))
        expected_elite = json.loads(json.dumps(expected_elite))
        self.assertEqual(actual["elite"].keys(), expected_elite.keys())
        for gid, entries in expected_elite.items():
            self.assertEqual(len(actual["elite"][gid]), len(entries))
            for a, b in zip(actual["elite"][gid], entries):
                b.setdefault("conflict_groups", [])
                self.assertAlmostEqual(a.pop("local_score"), b.pop("local_score"), places=8)
                self.assertEqual(a, b)
        return actual

    def test_production_protected_and_special_wrappers_match_frozen_pass_loop(self):
        for case in self.cases[:11]:
            with self.subTest(case=case["id"]):
                self.protected_replay(case, {}, stage=True, special=True)
        case = self.synthetic([(20, {"oldSeat": {"seatNum": "1B", "seatValue": ""},
                                    "mandatoryRule": {"needSingleSideEmpty": "Y"}}),
                               (10, {"oldSeat": {"seatNum": "2A", "seatValue": ""}})])
        for variant, algorithm, passes in (
            ("multi", {"protected_multigroup_min_pass_gain": 0.0}, 2),
            ("limit", {"protected_multigroup_max_passes": 0}, 1),
            ("gain", {"protected_multigroup_min_pass_gain": 1e9}, 1),
            ("expired", {}, 1),
            ("disabled", {"enable_protected_multigroup_pattern_mip": False, "enable_priority_multigroup_pattern_mip": False}, 1)):
            with self.subTest(variant=variant):
                actual = self.protected_replay(case, {"protected_dynamic_relocation_enabled": False, **algorithm}, variant,
                    initial_override=[[0, "3B", "3A"], [1, "1B"]],
                    extra_patterns=[] if variant == "disabled" else [(20, [(1, "1B")], [(1, ["1A"])]), (10, [(1, "2A")], [])],
                    stage=True, special=True)
                self.assertEqual(actual["diagnostics"]["passes"], passes)
                if variant == "disabled": self.assertGreater(actual["special"]["negative_patterns"], 0)

    def test_complete_protected_mip_matches_frozen_function(self):
        calls = patterns = components = 0
        for case in self.cases[:11]:
            for algorithm, variant in (({}, "active"), ({"protected_dynamic_relocation_enabled": False}, "active"),
                    ({"enable_protected_multigroup_pattern_mip": False, "enable_priority_multigroup_pattern_mip": False}, "disabled"),
                    ({}, "expired")):
                with self.subTest(case=case["id"], algorithm=algorithm, variant=variant):
                    actual = self.protected_replay(case, algorithm, variant)
                    calls += actual["diagnostics"]["dynamic_relocation_calls"]
                    patterns += actual["diagnostics"]["dynamic_relocation_patterns"]
                    components += actual["diagnostics"]["components_tested"]
        self.assertGreater(calls, 0)
        self.assertGreater(patterns, 0)
        self.assertGreater(components, 0)

    def test_protected_mip_accepts_joint_improvement_and_rejects_fixed_violation(self):
        for fixed in (False, True):
            with self.subTest(fixed=fixed):
                protected = {"ssr": "TEST_A", "oldSeat": {"seatNum": "1B", "seatValue": ""},
                             "mandatoryRule": {"needSingleSideEmpty": "Y", "sameRowNoOtherSSR": "Y"}}
                if fixed: protected["newSeat"] = {"seatNum": "3B"}
                case = self.synthetic([(20, protected), (10, {"ssr": "TEST_A", "oldSeat": {"seatNum": "2A", "seatValue": ""}})])
                actual = self.protected_replay(case, {"protected_dynamic_relocation_enabled": False}, initial_override=[[0, "3B", "3A"], [1, "1B"]],
                    extra_patterns=[(20, [(1, "1B")], [(1, ["1A"])]), (10, [(1, "2A")], []), (10, [(1, "1D")], [])])
                self.assertGreater(actual["diagnostics"]["conditional_ssr_rows"], 0)
                self.assertEqual(actual["diagnostics"]["accepted"], 0 if fixed else 1)

    def test_priority_mip_activation_and_clamped_limits(self):
        case = self.synthetic([(20, {"oldSeat": {"seatNum": "1A", "seatValue": ""}}),
                               (20, {"oldSeat": {"seatNum": "1B", "seatValue": ""}}),
                               (10, {"oldSeat": {"seatNum": "2A", "seatValue": ""}})])
        for threshold in (120.0, 121.0):
            with self.subTest(threshold=threshold):
                actual = self.protected_replay(case, dict(enable_protected_multigroup_pattern_mip=False,
                    priority_multigroup_min_business_time_seconds=threshold, protected_dynamic_relocation_enabled=False,
                    protected_multigroup_root_limit=0, protected_multigroup_max_groups=0,
                    protected_multigroup_component_limit=0, protected_multigroup_options_per_group=0),
                    initial_override=[[0, "4A"], [1, "1F"], [2, "1A"]],
                    extra_patterns=[(20, [(1, "1A"), (2, "1B")], []), (10, [(1, "2A")], [])])
                self.assertEqual(actual["diagnostics"]["accepted"], 1 if threshold == 120 else 0)
                self.assertEqual(actual["diagnostics"]["enabled"], threshold == 120)

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
