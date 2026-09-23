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


def python_schedule_replay(budgets, replay):
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_run_allocation_single_cabin")
    assignments = [node for node in function.body if isinstance(node, ast.Assign)]

    def execute(names, namespace):
        selected = []
        for node in function.body:
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, ast.AugAssign) else [])
            if any(isinstance(target, ast.Name) and target.id in names for target in targets):
                selected.append(node)
        assert selected, names
        exec(compile(ast.Module(body=selected, type_ignores=[]), "frozen_schedule", "exec"), namespace)

    namespace = dict(stage_budgets=budgets["stages"], allocation_start=replay["allocation_start"],
                     search_deadline=min(replay["allocation_start"] + budgets["usable_time"],
                                         replay.get("search_deadline_limit", float("inf"))),
                     carry=0.0, post_protected_pricing_reserve=0.0,
                     post_protected_tail_reserve_active=budgets["post_protected_tail_reserve_active"],
                     construction_unassigned=replay["construction_unassigned"],
                     algorithm_config={"restricted_pattern_mip_tail_budget": replay["tail_budget"]})
    result = []
    for event in replay["events"]:
        stage = event["stage"]
        prefix = {"pattern_generation": "pattern", "protected_multigroup_mip": "protected_mip",
                  "restricted_mip": "restricted"}.get(stage, stage)
        namespace[prefix + "_started"] = event["started"]
        namespace[prefix + "_finished"] = event["finished"]
        if stage == "special_pricing":
            call = next(node.value for node in assignments
                        if isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "generate_special_dual_pricing_patterns")
            deadline_expr = next(arg for arg in call.args if isinstance(arg, ast.Call)
                                 and isinstance(arg.func, ast.Name) and arg.func.id == "min")
            namespace["time"] = types.SimpleNamespace(perf_counter=lambda: event["started"])
            deadline = eval(compile(ast.Expression(deadline_expr), "frozen_pricing_deadline", "eval"), namespace)
            effective = namespace["post_protected_pricing_reserve"]
        else:
            names = {prefix + "_effective_budget", prefix + "_deadline"}
            if stage == "vnd":
                names.add("post_protected_pricing_reserve")
            if stage == "restricted_mip":
                names.add("restricted_tail_budget")
            execute(names, namespace)
            deadline = namespace[prefix + "_deadline"]
            effective = (budgets["stages"][stage] if stage == "construction"
                         else namespace[prefix + "_effective_budget"])
            if stage not in ("lns", "restricted_mip"):
                carry_node = next(node for node in assignments
                                  if any(isinstance(t, ast.Name) and t.id == "carry" for t in node.targets)
                                  and any(isinstance(n, ast.Name) and n.id == prefix + "_deadline"
                                          for n in ast.walk(node)))
                exec(compile(ast.Module(body=[carry_node], type_ignores=[]), "frozen_carry", "exec"), namespace)
        result.append(dict(effective_budget=effective, deadline=deadline, carry=namespace["carry"],
                           pricing_reserve=namespace["post_protected_pricing_reserve"]))
    return result


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
                # Virtual-clock traces exercise early finish, overhead, deadline
                # exhaustion, incomplete construction, and negative tail settings.
                stages = ["construction", "repair", "vnd", "pattern_generation",
                          "protected_multigroup_mip", "special_pricing", "lns", "restricted_mip"]
                for unassigned, step, tail, cap in [(0, 0.01, 0.1, None), (1, 0.3, -1.0, None),
                                                    (0, 10.0, 2.0, None), (1, 0.3, 0.1, 123.5)]:
                    with self.subTest(unassigned=unassigned, step=step, tail=tail):
                        replay = dict(allocation_start=123.0, construction_unassigned=unassigned,
                                      tail_budget=tail, events=[
                                          dict(stage=stage, started=123.0 + i * step + step * 0.1,
                                               finished=123.0 + i * step + step * 0.8)
                                          for i, stage in enumerate(stages)])
                        if cap is not None:
                            replay["search_deadline_limit"] = cap
                        replay_path = work / "schedule.json"
                        replay_path.write_text(json.dumps(replay), encoding="utf-8")
                        run = subprocess.run([str(probe), "--input", str(work / "case.json"),
                                              "--config", str(work / "config.json"),
                                              "--schedule-replay", str(replay_path)],
                                             capture_output=True, text=True)
                        self.assertEqual(run.returncode, 0, run.stderr)
                        actual_schedule = json.loads(run.stdout)["stage_schedule"]
                        expected_schedule = python_schedule_replay(expected, replay)
                        self.assertEqual(len(actual_schedule), len(expected_schedule))
                        for stage, actual_window, expected_window in zip(
                                stages, actual_schedule, expected_schedule):
                            for key, value in expected_window.items():
                                self.assertAlmostEqual(actual_window[key], value, places=10,
                                                       msg=f"{stage}: {key}")
