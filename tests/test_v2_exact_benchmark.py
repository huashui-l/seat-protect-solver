"""Certificate and conditional-budget gates for the V2 exact benchmark."""
import unittest

from scripts.benchmark_v2_exact import needs_followup, verified_proof


class V2ExactBenchmarkTests(unittest.TestCase):
    def test_safe_bound_and_pool_optimum_do_not_prove_global_optimality(self):
        raw = dict(proven_optimal=True, total_score=-10, safe_bound=-10,
                   lp_bound=-10, lp_bound_certified=False)
        self.assertEqual(verified_proof("cg_gurobi", raw, True, True), (False, None))

    def test_certified_gap_must_close_and_assignment_must_pass_audit(self):
        raw = dict(proven_optimal=True, total_score=-10, lp_bound=-10, lp_bound_certified=True)
        self.assertEqual(verified_proof("cg_gurobi", raw, True, True), (True, -10))
        self.assertFalse(verified_proof("cg_gurobi", raw, False, True)[0])
        self.assertFalse(verified_proof("cg_gurobi", raw, True, False)[0])
        raw["lp_bound"] = -9
        self.assertFalse(verified_proof("cg_gurobi", raw, True, True)[0])

    def test_branch_and_price_requires_every_cabin_certificate(self):
        raw = dict(proven_optimal=True, total_score=-10, bp_bound=-10,
                   cabins={"Business": {"bp_bound_certified": True},
                           "Economy": {"bp_bound_certified": False}})
        self.assertFalse(verified_proof("cg_gurobi", raw, True, True)[0])
        raw["cabins"]["Economy"]["bp_bound_certified"] = True
        self.assertTrue(verified_proof("cg_gurobi", raw, True, True)[0])

    def test_unresolved_and_failed_runs_require_900s_followup(self):
        record = dict(proven_optimal=False, proven_infeasible_by_method=False,
                      external_infeasibility_certificate=None)
        self.assertTrue(needs_followup(record))
        record["proven_optimal"] = True
        self.assertFalse(needs_followup(record))
        record["proven_optimal"] = False
        record["proven_infeasible_by_method"] = True
        self.assertFalse(needs_followup(record))
        record["proven_infeasible_by_method"] = False
        record["external_infeasibility_certificate"] = {"proofs": ["revalidated matching certificate"]}
        self.assertFalse(needs_followup(record))


if __name__ == "__main__":
    unittest.main()
