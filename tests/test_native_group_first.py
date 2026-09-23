from tests import test_native_group_soft


class NativeGroupFirstTests(test_native_group_soft.NativeGroupSoftTests):
    def run_group_first(self, case_id, groups=None):
        return self.run_case(case_id, "group-first", groups)

    def test_full_load_group_first_is_strictly_selected_or_q1_fallback(self):
        selected = 0
        recovery_attempts = 0
        escaped_q1_stall = 0
        for case_id in (
            "full_resource_shrink",
            "dense_full_resource_special_chain",
            "full_resource_protection_heavy",
        ):
            with self.subTest(case=case_id):
                result = self.run_group_first(case_id)
                recovery_attempts += result["recovery_attempts"]
                self.assertGreaterEqual(result["native_score"], result["q1_score"])
                if (result["from_scratch_complete"]
                        and result["from_scratch_score"] > result["q1_score"] + 1e-9):
                    selected += 1
                    escaped_q1_stall += result["q1_selected_incumbent"] == "q0"
                    self.assertTrue(result["from_scratch_complete"])
                    self.assertGreater(result["from_scratch_score"], result["q1_score"])
                elif result["selected_incumbent"] in {"rich-m2-vnd", "rich-m3-pattern-master"}:
                    self.assertGreaterEqual(result["native_score"], result["q1_score"])
                else:
                    self.assertIn(result["selected_incumbent"], {"q0", "group-aware"})
                    self.assertAlmostEqual(result["native_score"], result["q1_score"])
        self.assertGreater(selected, 0)
        self.assertGreater(escaped_q1_stall, 0)
        self.assertGreater(recovery_attempts, 0)

    def test_fixed_protection_caregiver_and_ssr_stay_legal(self):
        for case_id in (
            "fixed_and_protected",
            "caregiver_and_ssr",
            "conditional_ssr_isolation",
        ):
            with self.subTest(case=case_id):
                result = self.run_group_first(case_id)
                self.assertGreater(result["rich_pattern_count"], 0)
                self.assertGreater(result["rich_selected_pattern_count"], 0)
                self.assertGreaterEqual(result["rich_pattern_score"], result["rich_vnd_score"] - 1e-8)
                self.assertAlmostEqual(result["rich_master_score"], result["rich_pattern_score"], places=8)
                if case_id == "caregiver_and_ssr":
                    self.assertGreater(result["rich_baby_pair_count"], 0)
                else:
                    self.assertEqual(result["rich_baby_pair_count"], 0)

    def test_group_first_replay_is_deterministic_and_bounded(self):
        first = self.run_group_first("large_groups_9_10")
        second = self.run_group_first("large_groups_9_10")
        for key in (
            "assignments",
            "selected_incumbent",
            "from_scratch_complete",
            "from_scratch_dfs_nodes",
            "from_scratch_beam_nodes",
            "recovery_attempts",
            "recovery_succeeded",
        ):
            self.assertEqual(first[key], second[key])
        self.assertLessEqual(first["recovery_succeeded"], first["recovery_attempts"])
        self.assertGreater(first["from_scratch_beam_groups"], 0)
        self.assertLessEqual(first["from_scratch_beam_nodes"], 2 * 10 * 96 * 28)

    def test_q2a_failure_or_worse_preserves_q1(self):
        groups = self.cases["identity_keep_seats"]["groupsData"][:1]
        result = self.run_group_first("identity_keep_seats", groups)
        self.assertNotEqual("q2a-group-first", result["selected_incumbent"])
        self.assertAlmostEqual(result["native_score"], result["q1_score"])

    def test_restricted_master_disabled_or_zero_budget_preserves_vnd(self):
        for algorithm in (
            {"enable_restricted_pattern_mip": False},
            {"restricted_pattern_mip_time_budget": 0.0,
             "restricted_pattern_mip_tail_budget": 0.0},
        ):
            with self.subTest(algorithm=algorithm):
                result = self.run_case("shrink_small_blockers", "group-first", algorithm=algorithm)
                self.assertGreater(result["rich_pattern_count"], 0)
                self.assertEqual(result["rich_master_time_limit"], 0.0)
                self.assertEqual(result["rich_selected_pattern_count"], 0)
                self.assertNotEqual(result["selected_incumbent"], "rich-m3-pattern-master")
                self.assertAlmostEqual(result["native_score"], result["rich_vnd_score"])

    def test_incomplete_construction_reaches_repair_and_keeps_fallback(self):
        for repair_seconds in (0.0, 2.0):
            with self.subTest(repair_seconds=repair_seconds):
                result = self.run_case("identity_keep_seats", "group-first", algorithm={
                    "small_group_dfs_node_limit": 1,
                    "stage3_time_budget": 0.0,
                    "final_repair_time_limit": repair_seconds,
                    "vnd_time_budget": 0.0,
                    "enable_restricted_pattern_mip": False,
                })
                self.assertFalse(result["from_scratch_complete"])
                self.assertGreater(result["rich_construction_unassigned"], 0)
                self.assertGreaterEqual(result["native_score"], result["q1_score"])
                self.assertNotEqual(result["selected_incumbent"], "rich-m1")
                if repair_seconds == 0:
                    self.assertEqual(result["rich_repair_attempted"], 0)
                    self.assertFalse(result["rich_candidate_complete"])
                else:
                    self.assertGreater(result["rich_repair_attempted"], 0)
                    self.assertEqual(result["rich_repair_repaired"], result["rich_construction_unassigned"])
                    self.assertTrue(result["rich_candidate_complete"])

    def test_rich_construction_uses_beam_after_dfs_failure_independently_of_q2a(self):
        result = self.run_case("identity_keep_seats", "group-first", algorithm={
            "small_group_dfs_node_limit": 1,
            "small_group_dfs_time_limit": 20.0,
            "vnd_time_budget": 0.0,
            "enable_restricted_pattern_mip": False,
        })
        self.assertFalse(result["from_scratch_complete"])
        self.assertGreater(result["rich_dfs_attempted"], 0)
        self.assertEqual(result["rich_dfs_succeeded"], 0)
        self.assertGreater(result["rich_beam_groups"], 0)
        self.assertEqual(result["rich_construction_unassigned"], 0)
        self.assertEqual(result["rich_repair_attempted"], 0)
        self.assertTrue(result["rich_candidate_complete"])

    def test_worse_complete_repair_does_not_replace_incumbent(self):
        result = self.run_case("shrink_small_blockers", "group-first", algorithm={
            "small_group_dfs_node_limit": 1,
            "small_group_dfs_max_size": 100,
            "stage3_time_budget": 0.0,
            "vnd_time_budget": 0.0,
            "enable_restricted_pattern_mip": False,
        })
        self.assertTrue(result["rich_candidate_complete"])
        self.assertGreater(result["rich_repair_repaired"], 0)
        self.assertLess(result["rich_repair_score"], result["q1_score"])
        self.assertAlmostEqual(result["native_score"], result["q1_score"])

    def test_partial_fixed_state_survives_disabled_repair(self):
        result = self.run_case("fixed_and_protected", "group-first", algorithm={
            "small_group_dfs_node_limit": 1,
            "final_repair_node_limit": 0,
            "stage3_time_budget": 0.0,
            "vnd_time_budget": 0.0,
            "enable_restricted_pattern_mip": False,
        })
        self.assertGreater(result["rich_construction_assigned"], 0)
        self.assertGreater(result["rich_construction_unassigned"], 0)
        self.assertFalse(result["rich_candidate_complete"])
        self.assertEqual(result["rich_repair_attempted"], 0)
        self.assertEqual(result["rich_repair_nodes"], 0)
        self.assertAlmostEqual(result["native_score"], result["q1_score"])

    def test_restricted_master_uses_configured_budget_and_tail_floor(self):
        for base, tail in ((0.2, 0.0), (0.0, 0.15), (0.2, 0.1)):
            with self.subTest(base=base, tail=tail):
                result = self.run_case("caregiver_and_ssr", "group-first", algorithm={
                    "restricted_pattern_mip_time_budget": base,
                    "restricted_pattern_mip_tail_budget": tail,
                })
                self.assertAlmostEqual(result["rich_master_time_limit"], max(base, tail))
                self.assertGreater(result["rich_selected_pattern_count"], 0)
                self.assertAlmostEqual(result["rich_master_score"], result["rich_pattern_score"])

    def test_restricted_master_budget_is_clipped_to_remaining_solver_time(self):
        result = self.run_case("caregiver_and_ssr", "group-first", algorithm={
            "restricted_pattern_mip_time_budget": 100.0,
            "restricted_pattern_mip_tail_budget": 0.0,
        })
        self.assertGreater(result["rich_master_time_limit"], 0.0)
        self.assertLess(result["rich_master_time_limit"], 5.0)
        self.assertGreater(result["rich_selected_pattern_count"], 0)

    def test_restricted_master_branching_and_attempt_config(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                result = self.run_case("caregiver_and_ssr", "group-first", algorithm={
                    "restricted_pattern_mip_attempts": 1,
                    "enable_pattern_local_branching": enabled,
                    "pattern_local_branching_initial_radius": 1,
                    "pattern_local_branching_max_radius": 1,
                })
                self.assertEqual(result["rich_master_attempts"], 1)
                self.assertEqual(result["rich_master_last_radius"], 1 if enabled else -1)
                self.assertAlmostEqual(result["rich_master_score"], result["rich_pattern_score"])


if __name__ == "__main__":
    import unittest

    unittest.main()
