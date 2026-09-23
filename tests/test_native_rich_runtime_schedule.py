import copy
import unittest

from tests import test_native_group_soft as group_tests
from tests.test_native_stage_budgets import python_budget_prefix, python_schedule_replay


class NativeRichRuntimeScheduleTests(unittest.TestCase):
    setUpClass = classmethod(group_tests.NativeGroupSoftTests.setUpClass.__func__)
    run_case = group_tests.NativeGroupSoftTests.run_case

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
                        self.assertTrue(pattern["pinned"])
                        self.assertIn(pattern["source"], {"construction", "repair", "vnd"})
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
                    self.assertAlmostEqual(timing["base_budget"], budgets["stages"][stage], places=10)
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
