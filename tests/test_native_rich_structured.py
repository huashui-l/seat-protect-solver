import ast
import copy
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from src import heuristic_seat_allocator as rich
from src import allocation_evaluator as evaluator
from src import exact_column_generation as exact
from tests import test_native_rich_repair as repair_tests
from tests.test_native_rich_pipeline import construction_prefix

ROOT = Path(__file__).resolve().parents[1]


def python_rigid_relaxed(case, config, assignments):
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "generate_structured_group_patterns")
    loop = next(n for n in function.body if isinstance(n, ast.For)
                and isinstance(n.target, ast.Name) and n.target.id == "group")
    start = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "seat_at")
    stop = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "group_deadline")
    code = compile(ast.Module(body=loop.body[start:stop], type_ignores=[]), "frozen_rigid_relaxed", "exec")
    new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
    old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
    fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
    baby = exact._baby_pairs(new, case["groupsData"], config["weights"], config)
    result = []
    for group in case["groupsData"]:
        namespace = dict(rich.__dict__, group=group, passengers=group["psrs"], group_id=group["groupId"],
                         new_topology=new, pricing_config=config, algorithm=config["algorithm"],
                         weights=config["weights"], exact=exact, baby_cost=baby,
                         context=types.SimpleNamespace(assigned_seats=assignments), diagnostics={"three_tier_active": False},
                         cache=exact._build_group_pricing_cache(group, new, old, config["weights"], config, fixed))
        exec(code, namespace)
        for pattern, source in namespace["tiered_patterns"].values():
            result.append(dict(group_id=pattern.group_id, signature=pattern.signature, source=source,
                               assignments=pattern.assignments, blocked_by=pattern.blocked_by,
                               seat_resources=sorted(pattern.seat_resources), infant_seats=sorted(pattern.infant_seats),
                               occupied_seats=sorted(pattern.occupied_seats), ssr_all=pattern.ssr_all,
                               ssr_flagged=pattern.ssr_flagged, master_cost=pattern.master_cost,
                               caregiver_ok=exact._caregiver_ok(group, pattern.placements, new, config)))
    return json.loads(json.dumps(result))


class NativeRichStructuredTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def replay(self, case, assignments, config=None):
        config = copy.deepcopy(config or self.config)
        active = sorted({p["ssr"] for g in case["groupsData"] for p in g["psrs"] if p.get("ssr")})
        config["_column_generation_active_ssr_types"] = active
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        expected = python_rigid_relaxed(case, config, assignments)
        requests = [dict(group_index=i, current_targets=[assignments.get((g["groupId"], p["hostnum"])) for p in g["psrs"]])
                    for i, g in enumerate(case["groupsData"])]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"rigid_relaxed": requests, "active_ssr_types": active},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(len(actual), len(expected))
        for native, python in zip(actual, expected):
            self.assertAlmostEqual(native.pop("master_cost"), python.pop("master_cost"), places=8)
            for name in ("ssr_all", "ssr_flagged"):
                native[name] = {json.dumps(k): v for k, v in native[name]}
                python[name] = {json.dumps(k): v for k, v in python[name]}
            self.assertEqual(native, python)
        return actual

    def test_public_construction_states_match_frozen_rigid_relaxed(self):
        prefix = construction_prefix()
        sources = set()
        for case in self.cases[:11]:
            config = copy.deepcopy(self.config)
            config["algorithm"].update(business_time_limit_seconds=120.0, adaptive_stage_budgets=False,
                                       construction_time_budget=60.0, small_group_dfs_time_limit=20.0)
            context = prefix(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                             case["groupsData"], config["weights"], config)["context"]
            with self.subTest(case=case["id"]):
                actual = self.replay(case, context.assigned_seats, config)
                sources.update(p["source"] for p in actual)
        self.assertEqual(sources, {"rigid", "relaxed"})

    def test_activation_shift_caps_incomplete_and_duplicate_source_overwrite(self):
        case = self.synthetic([(101, {}), (101, {})])
        assignments = {(101, 1): "2A", (101, 2): "2B"}
        for variant in ({}, {"enable_three_tier_patterns": False}, {"business_time_limit_seconds": 9.99},
                        {"structured_rigid_shift_rows": 0}, {"structured_rigid_shift_rows": -3},
                        {"three_tier_min_business_time_seconds": None}):
            with self.subTest(variant=variant):
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=10.0, three_tier_min_business_time_seconds=10.0)
                config["algorithm"].update(variant)
                if variant.get("three_tier_min_business_time_seconds", 1) is None:
                    config["algorithm"].pop("three_tier_min_business_time_seconds")
                actual = self.replay(case, assignments, config)
                if variant.get("enable_three_tier_patterns") is False or variant.get("business_time_limit_seconds") == 9.99:
                    self.assertEqual(actual, [])
                else:
                    moved = next(p for p in actual if p["assignments"] == [[[101, 1], "1A"], [[101, 2], "1B"]])
                    self.assertEqual(moved["source"], "relaxed")
        config["algorithm"]["enable_three_tier_patterns"] = True
        self.assertEqual(self.replay(case, {(101, 1): "2A"}, config), [])

    def test_protection_backtracking_and_caregiver_mirroring(self):
        case = self.synthetic([(101, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}), (101, {}),
                               (202, {"ssr": "BLND"}), (202, {})])
        config = copy.deepcopy(self.config)
        config["algorithm"]["business_time_limit_seconds"] = 60.0
        actual = self.replay(case, {(101, 1): "2B", (101, 2): "2A", (202, 1): "1C", (202, 2): "1D"}, config)
        original = next(p for p in actual if p["assignments"] == [[[101, 1], "2B"], [[101, 2], "2A"]])
        self.assertEqual(original["blocked_by"], [["2C", [101, 1]]])
        self.assertTrue(all(p["caregiver_ok"] for p in actual))

    def test_unequal_row_widths_and_string_ordered_subrows(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                case = self.synthetic([(101, {}), (101, {}), (101, {})])
                prototypes = {s["col"]: s for s in case["newSeatmapData"]["seats"] if s["row"] == 1}
                seats = [{**prototypes[col], "seatId": f"{row}{col}", "row": row}
                         for row in (1, 2, 3, 9, 10, 11)
                         for col in ("ABCF" if row == 9 else "ABCDEF")]
                case["oldSeatmapData"] = {"seats": copy.deepcopy(seats)}
                case["newSeatmapData"] = {"seats": seats[::-1] if reverse else seats}
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=60.0, structured_rigid_shift_rows=1)
                actual = self.replay(case, {(101, 1): "10F", (101, 2): "2A", (101, 3): "10D"}, config)
                self.assertTrue(actual)
