import ast
import copy
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src import heuristic_seat_allocator as rich
from src.allocation_evaluator import IncrementalSoftScorer
from tests.general_validation_case_factory import materialize_cases
from tests.test_native_rich_elite import python_capture_namespace

ROOT = Path(__file__).resolve().parents[1]


def construction_prefix():
    """Run the actual frozen allocation prefix through remaining construction."""
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_run_allocation_single_cabin")
    stop = next(i for i, node in enumerate(function.body) if isinstance(node, ast.Try))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "rescue_failed_paired_ssrs"
               for statement in function.body[:stop] for node in ast.walk(statement))
    function.body = function.body[:stop] + ast.parse("return locals()").body
    namespace = dict(rich.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 "frozen_construction_prefix", "exec"), namespace)
    return types.FunctionType(namespace[function.name].__code__, rich.__dict__)


class NativeRichPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            raise unittest.SkipTest("set SEAT_PROTECT_NATIVE_EXE to the current build")
        cls.probe = Path(executable).resolve().with_name("native_rich_repair_probe.exe")
        cls.config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cls.cases = materialize_cases()
        cls.prefix = staticmethod(construction_prefix())

    def replay(self, case, algorithm):
        config = copy.deepcopy(self.config)
        # Generous nonbinding stage times isolate order/state/node semantics.
        # Real clock scheduling has a separate Python differential oracle.
        config["algorithm"].update(business_time_limit_seconds=120.0,
                                   adaptive_stage_budgets=False,
                                   construction_time_budget=60.0,
                                   small_group_dfs_time_limit=20.0,
                                   stage3_time_budget=60.0,
                                   final_repair_time_limit=60.0)
        config["algorithm"].update(algorithm)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        global_names = ("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW",
                        "_SEAT_X", "_SEAT_ROW_INDEX", "_GROUP_COMPACT_CACHE",
                        "_OLD_SEATS", "_OLD_SEAT_X", "_OLD_SEAT_OWNER_REGRET", "_ACTIVE_CONFIG")
        with patch.multiple(rich, **{name: getattr(rich, name) for name in global_names}):
            expected = self.prefix(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                   case["groupsData"], config["weights"], config)
            context = expected["context"]
            passengers = [(g.group_id, p.hostnum) for g in expected["groups"] for p in g.passengers]
            scorer = IncrementalSoftScorer(case["newSeatmapData"]["seats"],
                                          case["oldSeatmapData"]["seats"], case["groupsData"],
                                          config["weights"], config)

            def checkpoint():
                self.assertEqual(context.occupied, set(context.assigned_seats.values()))
                return ([context.assigned_seats.get(key) for key in passengers],
                        [sorted(context.assigned_blocked.get(key, ())) for key in passengers],
                        scorer.components(context.assigned_seats))

            captures = python_capture_namespace(context, expected["groups"], scorer,
                                                config["algorithm"].get("elite_patterns_per_group", 12))
            captures["capture_stage_patterns"]("construction")
            construction = checkpoint()
            metrics = rich._group_repair_metrics(case["groupsData"], context.assigned_seats,
                                                 scorer.new_topology, config["weights"], config,
                                                 scorer.old_topology)
            queue = sorted(metrics.values(), key=rich._repair_priority_key)
            repair = rich.repair_unassigned_by_local_relocation(
                expected["groups"], context, expected["passenger_sorted_seats"], config)
            captures["capture_stage_patterns"]("repair")
            expected_elites = json.loads(json.dumps({str(g): list(patterns.values())
                for g, patterns in captures["elite_pattern_store"].items()}))
            repaired = checkpoint()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, data in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "config.json": config, "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"],
                "replay.json": {"assignments": [], "construction_pipeline": True},
            }.items():
                (work / name).write_text(json.dumps(data), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual([m["group_id"] for m in actual["construction_repair_queue"]],
                         [m["group_id"] for m in queue])
        for native, reference in zip(actual["construction_repair_queue"], queue):
            for key, value in reference.items():
                self.assertAlmostEqual(native[key], value, places=8, msg=key)
        self.assertEqual(actual["elite_store"].keys(), expected_elites.keys())
        for group_id, patterns in expected_elites.items():
            native_patterns = actual["elite_store"][group_id]
            self.assertEqual(len(native_patterns), len(patterns))
            for native, reference in zip(native_patterns, patterns):
                self.assertAlmostEqual(native["local_score"], reference["local_score"], places=8)
                self.assertEqual({k: v for k, v in native.items() if k != "local_score"},
                                 {k: v for k, v in reference.items() if k != "local_score"})
        for prefix, reference, score_key in (("construction_", construction, "construction_score"),
                                              ("", repaired, "repair_score")):
            self.assertEqual(actual[prefix + "assignments"], reference[0], prefix + "assignments")
            self.assertEqual([sorted(row) for row in actual[prefix + "blocked"]], reference[1])
            for name, value in actual[score_key].items():
                self.assertAlmostEqual(value, reference[2][name], places=8, msg=score_key + ":" + name)
        for name in ("groups_considered", "dfs_attempted", "dfs_succeeded", "dfs_nodes",
                     "beam_groups", "transaction_failures"):
            self.assertEqual(actual[name], expected["construction_search_stats"][name], name)
        self.assertEqual(actual["paired_passes"], expected["paired_ssr_passes"])
        rescue = expected["paired_rescue_diagnostics"]
        for name in ("attempted", "rescued", "unresolved"):
            self.assertEqual(actual["rescue"][name], [list(key) for key in rescue[name]])
        self.assertEqual(actual["rescue"]["joint_rebuilds"], rescue.get("joint_rebuilds", 0))
        for name in ("attempted", "repaired", "unresolved", "search_nodes"):
            self.assertEqual(actual[name], repair[name], name)
        return actual

    def test_combined_construction_and_repair_match_python(self):
        for algorithm in ({}, {"small_group_dfs_enabled": False}, {"stage3_time_budget": 0.0}):
            for case in self.cases[:11]:
                with self.subTest(case=case["id"], algorithm=algorithm):
                    self.replay(case, algorithm)
