"""Certificate and conditional-budget gates for the V2 exact benchmark."""
import unittest
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from scripts.benchmark_v2_exact import ROOT, needs_followup, verified_proof, run_case, sha


class V2ExactBenchmarkTests(unittest.TestCase):
    def test_relative_output_directory_writes_audited_record(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            folder = Path(directory)
            payload = folder / "input.json"
            payload.write_text(json.dumps(dict(groups=[], seats=[])), encoding="utf-8")
            case = dict(case_id="synthetic")
            for name in ("groups", "old_seatmap", "new_seatmap"):
                case[name + "_path"] = str(payload)
                case[name + "_sha256"] = sha(payload)
            def fake_solver(command, **kwargs):
                Path(command[command.index("--output") + 1]).write_text(
                    json.dumps(dict(assigned_seats={}, total_score=0)), encoding="utf-8")
                return SimpleNamespace(returncode=0)
            relative = Path(os.path.relpath(folder / "run", Path.cwd()))
            with patch("scripts.benchmark_v2_exact.subprocess.run", side_effect=fake_solver), \
                 patch("scripts.benchmark_v2_exact.evaluator.count_hard_constraint_violations", return_value=(0, 0, {})), \
                 patch("scripts.benchmark_v2_exact.evaluator.calculate_soft_score", return_value=(0, {})):
                record = run_case(case, "direct_gurobi", 60, {"weights": {}}, relative)
            self.assertTrue(record["valid_for_comparison"])
            self.assertTrue((folder / "run" / "record.json").is_file())
            self.assertTrue((ROOT / record["raw_output"]).is_file())

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
