import ast
import copy
import json
import random
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from src import heuristic_seat_allocator as rich
from tests import test_native_rich_repair as repair_tests

ROOT = Path(__file__).resolve().parents[1]


def python_records(records, limit):
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    allocation = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_run_allocation_single_cabin")
    function = next(n for n in allocation.body if isinstance(n, ast.FunctionDef)
                    and n.name == "record_elite_pattern")
    namespace = dict(rich.__dict__, elite_pattern_store={}, elite_pattern_limit=max(2, limit))
    exec(compile(ast.Module(body=[function], type_ignores=[]), "frozen_elite_record", "exec"), namespace)
    snapshots = []
    for record in records:
        namespace["context"] = types.SimpleNamespace(
            assigned_seats={(owner, index): seat for index, (seat, owner) in enumerate(record["owners"].items())},
            assigned_blocked={})
        namespace["conflict_diversity_active"] = record["conflict_diversity_active"]
        namespace["record_elite_pattern"](
            record["group_id"], [((record["group_id"], host), seat) for host, seat in record["assignments"]],
            record["local_score"], record["source"], dict(record["blocked_by_host"]), record["pinned"])
        snapshots.append(copy.deepcopy({str(g): list(patterns.values())
                                       for g, patterns in namespace["elite_pattern_store"].items()}))
    return json.loads(json.dumps(snapshots))


def python_capture_namespace(context, groups, scorer, limit):
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    allocation = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_run_allocation_single_cabin")
    functions = [n for n in allocation.body if isinstance(n, ast.FunctionDef)
                 and n.name in ("record_elite_pattern", "capture_stage_patterns")]
    namespace = dict(rich.__dict__, elite_pattern_store={}, elite_pattern_limit=max(2, limit),
                     context=context, groups=groups, stage_scorer=scorer, conflict_diversity_active=True)
    exec(compile(ast.Module(body=functions, type_ignores=[]), "frozen_elite_capture", "exec"), namespace)
    return namespace


def python_conflict_activation(case, config, metrics):
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    allocation = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_run_allocation_single_cabin")
    names = {"business_time_limit", "elite_pattern_limit", "protected_empty_demand",
             "free_after_demand", "diversity_resource_threshold", "structured_min_group_size",
             "conflict_diversity_active"}
    statements = [n for n in allocation.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    namespace = dict(rich.__dict__, config=config, algorithm_config=config["algorithm"],
                     groups_data=case["groupsData"], new_seats_data=case["newSeatmapData"]["seats"],
                     old_seats_data=case["oldSeatmapData"]["seats"], construction_repair_metrics=metrics,
                     seat_demand_total=rich._seat_demand_for_budgeting(case["groupsData"]),
                     traveler_total=sum(len(g["psrs"]) for g in case["groupsData"]))
    exec(compile(ast.Module(body=statements, type_ignores=[]), "frozen_conflict_activation", "exec"), namespace)
    return namespace["conflict_diversity_active"]


class NativeRichEliteTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_candidate_conflicts_follow_current_occupied_and_protected_resources(self):
        case = self.synthetic([(101, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}), (202, {}), (303, {})])
        records = []
        for row, score, active, target_row in ((1, 1.0, True, 1), (2, 1.0, True, 1),
                                               (2, 2.0, True, 1), (2, 3.0, True, 2),
                                               (2, 4.0, False, 2)):
            records.append(dict(group_id=303, assignments=[[1, f"{target_row}A"]],
                                blocked_by_host=[[1, [f"{target_row}D"]]], local_score=score,
                                source="structured_rigid", pinned=False, conflict_diversity_active=active,
                                state_assignments=[[0, f"{row}B", f"{row}A"], [1, f"{row}D"]],
                                owners={f"{row}A": 101, f"{row}B": 101, f"{row}D": 202}))
        config = copy.deepcopy(self.config)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"elite_limit": 12, "elite_records": records},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)["snapshots"]
        self.assertEqual(actual, python_records(records, 12))
        self.assertEqual(actual[0]["303"][0]["conflict_groups"], [101, 202])
        self.assertEqual(actual[1]["303"][0]["conflict_groups"], [101, 202])
        self.assertEqual(actual[2]["303"][0]["conflict_groups"], [])
        self.assertEqual(actual[3]["303"][1]["conflict_groups"], [101, 202])
        self.assertEqual(actual[4]["303"][1]["conflict_groups"], [])

    def test_record_replace_pin_and_eviction_match_frozen_python(self):
        def entry(seat, score=-5.0, pinned=False, block=None, group=71, owners=None, active=True):
            return dict(group_id=group, assignments=[[2, seat], [1, "1A"]],
                        blocked_by_host=[[2, block or []], [1, []]], local_score=score,
                        source="construction" if pinned else "candidate", pinned=pinned,
                        owners=owners or {}, conflict_diversity_active=active)

        records = [entry("1C", pinned=True, block=["1D", "1B"]),
                   entry("1C", score=-6.0, block=["1B", "1D"]),
                   entry("1C", score=-4.0, block=["1B", "1D"]),
                   entry("1C", score=-4.0, pinned=True, block=["1B", "1D"]),
                   entry("1C", pinned=True, block=["1E"]),
                   entry("2A", pinned=True), entry("2B", pinned=True),
                   entry("2C", score=20.0),
                   entry("1B", group=92, owners={"1B": 31}),
                   entry("1C", group=92, owners={"1C": 32}),
                   entry("1D", group=92, owners={"1D": 32}),
                   entry("1E", group=92, owners={"1E": 33}, score=-100.0)]
        rng = random.Random(1907)
        for _ in range(200):
            seat = rng.choice(["2A", "2B", "2C", "2D", "2E"])
            records.append(entry(seat, rng.choice([-10.0, -5.0, 0.0, 5.0]), rng.random() < 0.2,
                                 rng.choice([[], ["3A"], ["3B", "3A"]]), rng.choice([71, 92]),
                                 {"3A": 31, seat: rng.choice([31, 32, 71, 92])}, rng.random() < 0.8))
        case = self.cases[0]
        for limit in (0, 2, 5):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                config = copy.deepcopy(self.config)
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}}}
                for name, value in {
                    "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                    "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                    "replay.json": {"elite_limit": limit, "elite_records": records},
                }.items():
                    (work / name).write_text(json.dumps(value), encoding="utf-8")
                run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                      str(work / "replay.json")], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)["snapshots"]
                expected = python_records(records, limit)
                self.assertEqual(len(actual), len(expected))
                for index, (native, reference) in enumerate(zip(actual, expected)):
                    with self.subTest(record=index):
                        self.assertEqual(native, reference)
