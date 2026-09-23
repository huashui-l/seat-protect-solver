import copy
import unittest

from tests import test_native_group_soft as group_tests
from tests.test_native_stage_budgets import python_budget_prefix, python_schedule_replay
from tests.test_native_rich_elite import python_conflict_activation


class NativeRichRuntimeScheduleTests(unittest.TestCase):
    setUpClass = classmethod(group_tests.NativeGroupSoftTests.setUpClass.__func__)
    run_case = group_tests.NativeGroupSoftTests.run_case

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
                replay = dict(allocation_start=0.0, search_deadline_limit=search_limit,
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
