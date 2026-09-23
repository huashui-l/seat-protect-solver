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
                if result["selected_incumbent"] == "q2a-group-first":
                    selected += 1
                    escaped_q1_stall += result["q1_selected_incumbent"] == "q0"
                    self.assertTrue(result["from_scratch_complete"])
                    self.assertGreater(result["from_scratch_score"], result["q1_score"])
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
                self.run_group_first(case_id)

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


if __name__ == "__main__":
    import unittest

    unittest.main()
