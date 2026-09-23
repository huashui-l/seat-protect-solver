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


class NativeRichEliteTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)

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
