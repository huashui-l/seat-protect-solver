import copy
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from src import heuristic_seat_allocator as rich
import unittest

from tests import test_native_group_soft as group_tests
from tests.test_native_stage_budgets import python_budget_prefix, python_schedule_replay
from tests.test_native_rich_elite import python_conflict_activation


class NativeRichRuntimeScheduleTests(unittest.TestCase):
    setUpClass = classmethod(group_tests.NativeGroupSoftTests.setUpClass.__func__)
    run_case = group_tests.NativeGroupSoftTests.run_case

    def test_rich_fast_profile_uses_rich_first_and_q0_only_on_failure(self):
        profile = json.loads((Path(__file__).resolve().parents[1]
                             / "configs/config_native_rich_5s.json").read_text(encoding="utf-8"))
        fallback_count = 0
        native_count = 0
        for case_id in self.cases:
            if case_id.endswith("_invalid"):
                continue
            with self.subTest(case=case_id):
                result = self.run_case(case_id, "rich-fast", algorithm=profile["algorithm"])
                self.assertTrue(result["complete"])
                self.assertEqual("HeuristicComplete", result["status"])
                cabins = result.get("cabin_decomposition", {}).get("cabins", {})
                results = [cabin["result"] for cabin in cabins.values()] if cabins else [result]
                for cabin in results:
                    self.assertEqual("NotRun", cabin["q1_selected_incumbent"])
                    if cabin["rich_candidate_complete"]:
                        native_count += 1
                        self.assertEqual("NotRun", cabin["q0_solver_status"])
                        self.assertTrue(cabin["selected_incumbent"].startswith("rich-"))
                        self.assertGreaterEqual(cabin["native_score"], cabin["rich_repair_score"] - 1e-8)
                    else:
                        fallback_count += 1
                        self.assertEqual("q0", cabin["selected_incumbent"])
                        self.assertEqual("rich_incomplete", cabin["fallback_reason"])
                        self.assertNotEqual("NotRun", cabin["q0_solver_status"])
                        self.assertEqual(cabin["native_score"], cabin["q0_score"])
                    self.assertEqual(0, cabin["dfs_nodes"] + cabin["beam_nodes"])
                    self.assertEqual(0, cabin["from_scratch_dfs_nodes"] + cabin["from_scratch_beam_nodes"])
                    self.assertFalse(cabin["rich_multigroup_lns"]["enabled"])
                    for timing in cabin["rich_stage_timing"].values():
                        self.assertLessEqual(timing["deadline"], cabin["rich_search_deadline"])

        self.assertGreater(native_count, 0)
        self.assertGreater(fallback_count, 0)

    def test_group_first_defers_fallback_search_until_after_rich(self):
        result = self.run_case("shrink_small_blockers", "group-first", algorithm={
            "enable_conflict_component_lns": False,
        })
        timings = result["rich_stage_timing"]
        self.assertGreaterEqual(result["fallback_improvement_started"], timings["restricted_mip"]["finished"])
        self.assertGreaterEqual(result["fallback_improvement_finished"], result["fallback_improvement_started"])
        self.assertGreater(result["dfs_nodes"] + result["beam_nodes"], 0)
        self.assertGreater(result["from_scratch_dfs_nodes"] + result["from_scratch_beam_nodes"], 0)
        self.assertGreaterEqual(result["native_score"], result["q0_score"] - 1e-8)
        self.assertGreaterEqual(result["native_score"], result["q1_score"] - 1e-8)
        self.assertGreaterEqual(result["native_score"], result["rich_master_score"] - 1e-8)
        for timing in timings.values():
            self.assertLessEqual(timing["deadline"], result["rich_search_deadline"])

    def test_cabin_wrapper_matches_frozen_order_filtering_and_budget(self):
        case = self.cases["two_cabin_valid_groups"]
        for minimum in (.1, 4.0):
            algorithm = {"business_time_limit_seconds": 5.0, "cabin_min_time_seconds": minimum}
            result = self.run_case(case["id"], "group-first", algorithm=algorithm)
            diagnostics = result["cabin_decomposition"]
            order = diagnostics["solve_order"]
            runs = diagnostics["cabins"]
            config = copy.deepcopy(self.base_config)
            config["algorithm"].update(algorithm)
            times = [0.0]
            for cabin in order:
                run = runs[cabin]
                times.extend((run["budget_started"], run["budget_started"], run["finished"]))
            times.append(result["wall_seconds"])
            calls = []
            def single(new, old, groups, weights, subconfig):
                cabin = order[len(calls)]
                run = runs[cabin]
                self.assertEqual({s["seatClass"] for s in new}, {cabin})
                self.assertEqual({s["seatClass"] for s in old}, {cabin})
                self.assertAlmostEqual(subconfig["algorithm"]["business_time_limit_seconds"], run["time_budget_seconds"])
                native = run["result"]
                self.assertAlmostEqual(native["rich_business_time_limit"], run["time_budget_seconds"])
                budgets = python_budget_prefix()(new, old, groups, weights, subconfig)
                for stage, budget in budgets["stages"].items():
                    self.assertAlmostEqual(native["rich_stage_budgets"][stage], budget)
                calls.append(cabin)
                assignments = {(a["groupId"], a["hostnum"]): a["seatId"] for a in native["assignments"]}
                return {"assigned_seats": assignments, "score_detail": {"total_soft_score": native["native_score"]}}, native["native_score"]
            with patch.object(rich.time, "perf_counter", side_effect=times), patch.object(rich, "_run_allocation_single_cabin", side_effect=single):
                expected, _ = rich.run_allocation(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                    case["groupsData"], config["weights"], config)
            self.assertEqual(calls, order)
            self.assertAlmostEqual(expected["score_detail"]["total_soft_score"], result["native_score"])
            self.assertTrue(expected["score_detail"]["soft_score_validation"]["passed"])
            self.assertTrue(result["rich_candidate_complete"])
            self.assertEqual(result["rich_construction_assigned"], len(result["assignments"]))

    def test_single_cabin_and_disabled_decomposition_keep_single_pipeline(self):
        for case_id, algorithm in (("identity_keep_seats", {}),
                                   ("two_cabin_valid_groups", {"cabin_decomposition_enabled": False})):
            result = self.run_case(case_id, "group-first", algorithm=algorithm)
            self.assertNotIn("cabin_decomposition", result)
            self.assertIn("rich_stage_timing", result)

    def test_rich_wrapper_rejects_mixed_cabin_group_even_when_disabled(self):
        case = self.cases["mixed_cabin_group_invalid"]
        for enabled in (True, False):
            config = copy.deepcopy(self.base_config)
            config["algorithm"]["cabin_decomposition_enabled"] = enabled
            config["input_contract"] = {"seatmaps_by_direction": {"public-test": {"old": "old.json", "new": "new.json"}}}
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                for name, value in {
                    "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                    "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                }.items():
                    (work / name).write_text(json.dumps(value), encoding="utf-8")
                run = subprocess.run([str(self.executable), "--input", str(work / "case.json"), "--config", str(work / "config.json"),
                                      "--construction-objective", "group-first"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 3)
            self.assertIn("one booking cabin", run.stderr)
            self.assertEqual(run.stdout, "")

    def test_raw_cli_lns_activation_capture_and_stage_window(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                result = self.run_case("identity_keep_seats", "group-first", algorithm={
                    "adaptive_stage_budgets": False, "construction_time_budget": .3,
                    "repair_time_budget": .1, "vnd_time_budget": .1,
                    "structured_pattern_time_budget": .1, "protected_multigroup_mip_time_budget": .1,
                    "lns_time_budget": 0.0, "enable_conflict_component_lns": enabled,
                    "multigroup_mip_solve_limit": 4, "multigroup_option_limit": 10,
                })
                self.assertTrue(result["rich_candidate_complete"])
                stats = result["rich_multigroup_lns"]
                self.assertEqual(stats["enabled"], enabled)
                timing = result["rich_stage_timing"]
                self.assertGreaterEqual(timing["lns"]["started"], timing["special_pricing"]["finished"])
                self.assertAlmostEqual(timing["lns"]["effective_budget"], timing["lns"]["base_budget"] + timing["special_pricing"]["carry"])
                if enabled:
                    self.assertEqual(timing["lns"]["base_budget"], 0.0)
                    self.assertGreater(timing["lns"]["effective_budget"], 0.0)
                    self.assertGreater(stats["search_nodes"], 0)
                    self.assertGreater(stats["options_generated"], 0)
                else:
                    self.assertEqual(stats["search_nodes"], 0)
                    self.assertEqual(stats["options_generated"], 0)

    def test_raw_cli_protected_and_special_stages_honor_activation_and_carry(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                result = self.run_case("dense_full_resource_special_chain", "group-first", algorithm={
                    "business_time_limit_seconds": 60.0, "adaptive_stage_budgets": False,
                    "construction_time_budget": 0.4, "repair_time_budget": 0.2, "vnd_time_budget": 2.2,
                    "structured_pattern_time_budget": 0.2, "structured_pattern_min_group_size": 1,
                    "structured_pattern_dfs_per_group": 0.01, "protected_multigroup_mip_time_budget": 0.3,
                    "enable_protected_multigroup_pattern_mip": enabled, "enable_priority_multigroup_pattern_mip": enabled,
                    "enable_post_protected_special_pricing": enabled,
                })
                self.assertTrue(result["rich_candidate_complete"])
                protected = result["rich_protected_multigroup_mip"]
                special = result["rich_special_dual_pricing"]
                self.assertEqual(protected["enabled"], enabled)
                self.assertGreaterEqual(protected["passes"], 1)
                self.assertEqual(special["enabled"], enabled)
                if enabled:
                    self.assertGreater(protected["roots_considered"], 0)
                    self.assertEqual(special["lp_status"], "Optimal")
                    self.assertGreater(special["lp_columns"], 0)
                    self.assertGreater(special["groups_attempted"], 0)
                else:
                    self.assertEqual(special["lp_status"], "disabled")
                    self.assertEqual(special["groups_attempted"], 0)
                timings = result["rich_stage_timing"]
                self.assertGreaterEqual(timings["protected_multigroup_mip"]["started"], timings["pattern_generation"]["finished"])
                self.assertGreaterEqual(timings["special_pricing"]["started"], timings["protected_multigroup_mip"]["finished"])
                self.assertGreater(timings["special_pricing"]["effective_budget"], 0)
                self.assertEqual(timings["special_pricing"]["carry"], timings["protected_multigroup_mip"]["carry"])

    def test_raw_cli_records_structured_patterns_and_honors_disable(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                result = self.run_case("identity_keep_seats", "group-first", algorithm={
                    "adaptive_stage_budgets": False, "construction_time_budget": 0.2,
                    "repair_time_budget": 0.1, "vnd_time_budget": 0.05,
                    "structured_pattern_time_budget": 0.15, "structured_pattern_dfs_per_group": 0.005,
                    "structured_pattern_min_group_size": 1, "enable_structured_pattern_generation": enabled,
                })
                self.assertTrue(result["rich_candidate_complete"])
                self.assertIn("pattern_generation", result["rich_stage_timing"])
                stats = result["rich_structured_pattern_generation"]
                self.assertEqual(stats["enabled"], enabled)
                self.assertEqual(stats["patterns_generated"], sum(stats["tier_counts"].values()))
                structured = [p for patterns in result["rich_elite_store"].values() for p in patterns
                              if p["source"].startswith("structured_")]
                if enabled:
                    self.assertGreater(stats["groups_attempted"], 0)
                    self.assertGreater(stats["patterns_generated"], 0)
                    self.assertTrue(structured)
                else:
                    self.assertEqual(stats["groups_attempted"], 0)
                    self.assertEqual(stats["patterns_generated"], 0)
                    self.assertEqual(structured, [])

    def test_production_windows_match_python_at_actual_stage_times(self):
        oracle = python_budget_prefix()
        variants = [
            {},
            {"adaptive_stage_budgets": False, "construction_time_budget": 0.3,
             "repair_time_budget": 0.2, "vnd_time_budget": 0.1},
            {"adaptive_stage_budgets": False, "construction_time_budget": -1.0,
             "repair_time_budget": 0.0},
            {"adaptive_stage_budgets": False, "stage3_time_budget": 0.0,
             "final_repair_time_limit": 0.0, "vnd_time_budget": 0.0},
            {"business_time_limit_seconds": 60.0, "scoring_time_reserve": 0.5},
        ]
        case = self.cases["identity_keep_seats"]
        for variant in variants:
            with self.subTest(algorithm=variant):
                config = copy.deepcopy(self.base_config)
                config["algorithm"].update(variant)
                budgets = oracle(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                 case["groupsData"], config["weights"], config)
                result = self.run_case(case["id"], "group-first", algorithm=variant)
                elites = result["rich_m1_elite_store"]
                final_elites = result["rich_elite_store"]
                self.assertEqual(set(final_elites), set(elites))
                for patterns in final_elites.values():
                    for pattern in patterns:
                        if pattern["source"] != "lns_generated": self.assertTrue(pattern["pinned"])
                        self.assertIn(pattern["source"], {"construction", "repair", "vnd", "lns_generated", "lns_final"})
                if result["rich_candidate_complete"]:
                    self.assertEqual(set(elites), {str(g["groupId"]) for g in case["groupsData"]})
                if variant.get("final_repair_time_limit") == 0.0:
                    self.assertEqual(elites, {})
                for patterns in elites.values():
                    self.assertTrue(patterns)
                    for pattern in patterns:
                        self.assertTrue(pattern["pinned"])
                        self.assertEqual(pattern["conflict_groups"], [])
                        self.assertIn(pattern["source"], {"construction", "repair"})
                queue = result["rich_construction_repair_queue"]
                self.assertEqual(result["rich_conflict_diversity_active"],
                                 python_conflict_activation(case, config, {m["group_id"]: m for m in queue}))
                self.assertEqual(len(queue), len(case["groupsData"]))
                self.assertEqual(sum(m["assigned"] for m in queue), result["rich_construction_assigned"])
                self.assertEqual(result["rich_extreme_dispersion_group_count"],
                                 sum(m["extreme_dispersion"] for m in queue))
                priorities = [(-m["priority_loss"], -m["row_span"], -m["compactness_penalty"], m["group_id"])
                              for m in queue]
                self.assertEqual(priorities, sorted(priorities))
                for stage, budget in budgets["stages"].items():
                    self.assertAlmostEqual(result["rich_stage_budgets"][stage], budget, places=10)
                self.assertAlmostEqual(result["rich_business_time_limit"], budgets["business_time_limit"])
                self.assertAlmostEqual(result["rich_scoring_reserve"], budgets["scoring_reserve"])
                search_limit = min(budgets["usable_time"], 5.0 - min(budgets["scoring_reserve"], 1.0))
                self.assertAlmostEqual(result["rich_search_deadline"], search_limit)
                timings = result["rich_stage_timing"]
                stages = ["construction", "repair", "vnd"]
                if result["rich_candidate_complete"]:
                    stages.extend(("pattern_generation", "protected_multigroup_mip", "special_pricing", "lns", "restricted_mip"))
                self.assertEqual(set(timings), set(stages))
                replay = dict(allocation_start=result["rich_allocation_start"], search_deadline_limit=search_limit,
                              construction_unassigned=result["rich_construction_unassigned"],
                              tail_budget=config["algorithm"].get("restricted_pattern_mip_tail_budget", 0.1),
                              events=[dict(stage=stage, started=timings[stage]["started"],
                                           finished=timings[stage]["finished"]) for stage in stages])
                expected = python_schedule_replay(budgets, replay)
                previous_finished = 0.0
                for stage, window in zip(stages, expected):
                    timing = timings[stage]
                    self.assertGreaterEqual(timing["started"], previous_finished)
                    self.assertGreaterEqual(timing["finished"], timing["started"])
                    self.assertLessEqual(timing["deadline"], search_limit)
                    previous_finished = timing["finished"]
                    self.assertAlmostEqual(timing["base_budget"], budgets["stages"].get(stage, 0.0), places=10)
                    for key, value in window.items():
                        self.assertAlmostEqual(timing[key], value, places=10, msg=stage + ":" + key)
                if variant.get("construction_time_budget") == -1.0:
                    self.assertGreater(result["rich_construction_unassigned"], 0)
                    self.assertGreater(result["rich_repair_attempted"], 0)
                    self.assertTrue(result["rich_candidate_complete"])
                    self.assertAlmostEqual(timings["repair"]["deadline"], search_limit)
                if variant.get("final_repair_time_limit") == 0.0:
                    self.assertFalse(result["rich_candidate_complete"])
                    self.assertEqual(result["rich_repair_attempted"], 0)
                    self.assertEqual(result["rich_vnd_seconds"], 0.0)
                    self.assertEqual(final_elites, elites)
                    self.assertGreater(timings["vnd"]["effective_budget"], timings["vnd"]["base_budget"])
