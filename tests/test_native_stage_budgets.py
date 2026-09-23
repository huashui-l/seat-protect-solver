import ast
import copy
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from tests.general_validation_case_factory import materialize_cases

ROOT = Path(__file__).resolve().parents[1]


def python_budget_prefix():
    # Execute the actual frozen function prefix, stopping before allocation.
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_run_allocation_single_cabin")
    demand = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_seat_demand_for_budgeting")
    stop = next(i for i, node in enumerate(function.body) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "construction_deadline"
                        for target in node.targets))
    function.body = function.body[:stop] + ast.parse(
        "return dict(business_time_limit=business_time_limit, scoring_reserve=scoring_reserve, "
        "usable_time=usable_time, seat_demand_total=seat_demand_total, stages=stage_budgets, "
        "post_protected_tail_reserve_active=post_protected_tail_reserve_active, "
        "post_protected_special_pricing_active=post_protected_special_pricing_active)"
    ).body
    for node in (function, demand):
        node.returns = None
        for arg in node.args.args:
            arg.annotation = None
    namespace = {"time": types.SimpleNamespace(perf_counter=lambda: 0.0)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[demand, function], type_ignores=[])),
                 "frozen_budget_prefix", "exec"), namespace)
    return namespace["_run_allocation_single_cabin"]


class NativeStageBudgetTests(unittest.TestCase):
    def test_budget_allocation_matches_frozen_python_prefix(self):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            self.skipTest("set SEAT_PROTECT_NATIVE_EXE to a built native CLI")
        probe = Path(executable).resolve().with_name("native_full_core_probe.exe")
        oracle = python_budget_prefix()
        cases = materialize_cases()
        base = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        variants = [
            {},
            {"business_time_limit_seconds": 0.0},
            {"business_time_limit_seconds": 60.0, "adaptive_stage_budgets": False},
            {"adaptive_stage_budgets": False, "construction_time_budget": -1.0,
             "structured_pattern_time_budget": 100.0},
            {"adaptive_stage_budgets": False, "business_time_limit_seconds": 60.0,
             "construction_time_budget": -1.0, "repair_time_budget": 0.0,
             "vnd_time_budget": 0.0, "lns_time_budget": 0.0},
            {"adaptive_budget_low_seat_demand": 0, "adaptive_budget_high_seat_demand": 10},
            {"adaptive_budget_low_seat_demand": 0, "adaptive_budget_high_seat_demand": 0},
            {"enable_protected_multigroup_pattern_mip": True},
            {"enable_priority_multigroup_pattern_mip": True, "business_time_limit_seconds": 10},
            {"enable_priority_multigroup_pattern_mip": True, "business_time_limit_seconds": 9.99},
            {"scoring_time_reserve": -1}, {"scoring_time_reserve": 100},
            {"business_time_limit_seconds": 60, "enable_post_protected_special_pricing": False},
        ]
        scenarios = [(cases[0], algorithm) for algorithm in variants]
        scenarios += [(case, algorithm) for case in cases for algorithm in (
            {"business_time_limit_seconds": 60},
            {"business_time_limit_seconds": 60, "enable_post_protected_special_pricing": False},
            {"adaptive_budget_low_seat_demand": 10, "adaptive_budget_high_seat_demand": 25},
        )]
        for case, algorithm in scenarios:
            with self.subTest(case=case["id"], algorithm=algorithm), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                config = copy.deepcopy(base)
                config["algorithm"] = algorithm
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}
                }}
                for name, value in {"old.json": case["oldSeatmapData"],
                                    "new.json": case["newSeatmapData"],
                                    "case.json": {"caseId": "budget", "direction": "public-test",
                                                  "groups": case["groupsData"]},
                                    "config.json": config}.items():
                    (work / name).write_text(json.dumps(value), encoding="utf-8")
                run = subprocess.run([str(probe), "--input", str(work / "case.json"),
                                      "--config", str(work / "config.json")],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)["stage_budgets"]
                expected = oracle(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                  case["groupsData"], config["weights"], config)
                for name, value in expected.items():
                    if name == "stages":
                        self.assertEqual(actual[name].keys(), value.keys())
                        for stage, budget in value.items():
                            self.assertAlmostEqual(actual[name][stage], budget, places=10)
                    else:
                        self.assertAlmostEqual(actual[name], value, places=10)
