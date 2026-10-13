"""Reject contradictory certificates and incomplete final comparison tables."""
import unittest
import copy

from scripts.build_v2_exact_report import ROOT, comparison, render, verify_call


class V2ExactReportTests(unittest.TestCase):
    def test_actual_call_budget_and_input_paths_must_match_record(self):
        folder = ROOT / "build" / "call-contract-fixture"
        case = dict(groups_path="groups.json", old_seatmap_path="old.json", new_seatmap_path="new.json")
        record = dict(method="direct_gurobi", budget_seconds=60, raw_output=str(folder / "raw.json"))
        command = ["python", "-m", "src.direct_gurobi", "--time-limit", "60"]
        for option, path in (("--groups", ROOT / case["groups_path"]),
                             ("--old-seatmap", ROOT / case["old_seatmap_path"]),
                             ("--new-seatmap", ROOT / case["new_seatmap_path"]),
                             ("--config", folder / "config.json"), ("--output", folder / "raw.json")):
            command.extend([option, str(path)])
        record["command"] = command
        base = dict(weights={"score_c": -1})
        config = dict(base, direct_gurobi=dict(time_limit=60, threads=1, seed=0))
        verify_call(record, case, config, base, folder)
        wrong = copy.deepcopy(config)
        wrong["direct_gurobi"]["time_limit"] = 150
        with self.assertRaises(AssertionError):
            verify_call(record, case, wrong, base, folder)

        cg_record = copy.deepcopy(record)
        cg_record["method"] = "cg_gurobi"
        cg_record["command"][2] = "src.cg_gurobi"
        cg_base = dict(base, cg_gurobi=dict(warm_start="heuristic"))
        cg_config = copy.deepcopy(cg_base)
        cg_config["cg_gurobi"].update(total_time_limit=60, pricing_workers=1, master_threads=1, pricing_threads=1)
        verify_call(cg_record, case, cg_config, cg_base, folder)
        cg_config["cg_gurobi"]["warm_start"] = "greedy"
        with self.assertRaises(AssertionError):
            verify_call(cg_record, case, cg_config, cg_base, folder)
        wrong = copy.deepcopy(record)
        wrong["command"][wrong["command"].index("--groups") + 1] = str(ROOT / "other.json")
        with self.assertRaises(AssertionError):
            verify_call(wrong, case, config, base, folder)
        wrong = copy.deepcopy(config)
        wrong["weights"]["score_c"] = -2
        with self.assertRaises(AssertionError):
            verify_call(record, case, wrong, base, folder)

    def fixture(self):
        cases = [dict(case_id=str(i), groups_sha256="g", old_seatmap_sha256="o", new_seatmap_sha256="n",
            heuristic=dict(valid=True, score=-10, status="complete", seconds=60),
            independent_rescore=dict(score=-10, hard_violations=0, unassigned=0),
            inherited_infeasibility_certificate=None) for i in range(72)]
        return dict(cases=cases, audit_complete=True), dict(records=[], complete=False,
            metadata=dict(algorithm_commit="fixture"))

    def record(self, **changes):
        value = dict(case_id="0", method="direct_gurobi", budget_seconds=60,
            groups_sha256="g", old_seatmap_sha256="o", new_seatmap_sha256="n",
            valid_for_comparison=True, complete_legal=True, objective_parity=True,
            score=-11, proven_optimal=False, certified_upper_bound=-9,
            proven_infeasible_by_method=False, external_infeasibility_certificate=None,
            solver_status="time_limit")
        value.update(changes)
        return value

    def test_integer_reference_is_not_labelled_optimal(self):
        audit, batch = self.fixture()
        batch["records"].append(self.record())
        data = comparison(audit, batch, verify_files=False)
        row = data["rows"][0]
        self.assertEqual(row["reference_type"], "best_known_legal_integer")
        self.assertAlmostEqual(row["methods"]["direct_gurobi"]["gap_to_reference_percent"], 10)
        self.assertEqual(data["paired_60s"][0]["shared_legal_cases"], 1)
        self.assertEqual(data["paired_60s"][0]["left_wins"], 1)
        summary = next(s for s in data["summaries"] if s["method"] == "direct_gurobi" and s["tier_seconds"] == 60)
        self.assertEqual((summary["attempted"], summary["pending"], summary["not_required"]), (1, 71, 0))
        self.assertIn("阶段快照", render(data))

    def test_bound_below_independent_legal_incumbent_is_rejected(self):
        audit, batch = self.fixture()
        batch["records"].append(self.record(certified_upper_bound=-10.5))
        with self.assertRaises(AssertionError):
            comparison(audit, batch, verify_files=False)

    def test_claimed_optimum_cannot_be_worse_than_another_legal_incumbent(self):
        audit, batch = self.fixture()
        batch["records"].append(self.record(proven_optimal=True, certified_upper_bound=-11))
        with self.assertRaises(AssertionError):
            comparison(audit, batch, verify_files=False)

    def test_incomplete_batch_cannot_be_published_as_final(self):
        audit, batch = self.fixture()
        batch["complete"] = True
        with self.assertRaises(AssertionError):
            comparison(audit, batch, verify_files=False)


if __name__ == "__main__":
    unittest.main()
