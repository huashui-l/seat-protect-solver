import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from tests.general_validation_case_factory import materialize_cases


ROOT = Path(__file__).resolve().parents[1]


class NativeGroupSoftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            raise unittest.SkipTest(
                "set SEAT_PROTECT_NATIVE_EXE to a binary built from this checkout"
            )
        cls.executable = Path(executable).resolve()
        cls.base_config = json.loads(
            (ROOT / "config.json").read_text(encoding="utf-8")
        )
        cls.cases = {case["id"]: case for case in materialize_cases()}

    def run_case(self, case_id, objective="group-soft", groups=None, algorithm=None):
        case = self.cases[case_id]
        groups = groups if groups is not None else case["groupsData"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "old.json").write_text(
                json.dumps(case["oldSeatmapData"]), encoding="utf-8"
            )
            (work / "new.json").write_text(
                json.dumps(case["newSeatmapData"]), encoding="utf-8"
            )
            case_path = work / "case.json"
            case_path.write_text(json.dumps({
                "caseId": case_id,
                "direction": "public-test",
                "groups": groups,
            }), encoding="utf-8")
            config = dict(self.base_config)
            if algorithm is not None:
                config["algorithm"] = {**config["algorithm"], **algorithm}
            config["input_contract"] = {"seatmaps_by_direction": {
                "public-test": {"old": "old.json", "new": "new.json"}
            }}
            config_path = work / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            completed = subprocess.run([
                str(self.executable), "--input", str(case_path),
                "--config", str(config_path), "--time-limit", "5",
                "--seed", "0", "--construction-objective", objective,
            ], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        assignments = {
            (item["groupId"], item["hostnum"]): item["seatId"]
            for item in result["assignments"]
        }
        violations, unassigned, _ = evaluator.count_hard_constraint_violations(
            case["newSeatmapData"]["seats"], groups, assignments,
            self.base_config,
        )
        score, _ = evaluator.calculate_soft_score(
            case["newSeatmapData"]["seats"],
            case["oldSeatmapData"]["seats"], groups, assignments,
            self.base_config["weights"], self.base_config,
        )
        self.assertEqual((0, 0), (violations, unassigned))
        self.assertEqual(0, result["native_hard_violations"])
        self.assertAlmostEqual(score, result["native_score"], places=8)
        return result

    def test_group_soft_is_strict_better_or_q0_fallback(self):
        q0 = self.run_case("shrink_small_blockers", "individual-soft")
        group = self.run_case("shrink_small_blockers")
        self.assertEqual("Optimal", q0["status"])
        self.assertEqual("", q0["q0_solver_status"])
        self.assertEqual("Optimal", group["q0_solver_status"])
        self.assertEqual("HeuristicComplete", group["status"])
        self.assertGreaterEqual(group["native_score"], q0["native_score"])
        if group["selected_incumbent"] == "group-aware":
            self.assertGreater(group["native_score"], q0["native_score"])
            self.assertGreater(group["score_delta"], 0.0)
            self.assertGreater(group["component_deltas"]["score_c"], 0.0)
            individual_delta = sum(
                group["component_deltas"][key]
                for key in ("score_s", "score_v", "score_p")
            )
            self.assertLess(individual_delta, 0.0)
            self.assertGreater(
                group["component_deltas"]["score_c"] + individual_delta,
                0.0,
            )
        elif group["selected_incumbent"] in {"rich-m2-vnd", "rich-m3-pattern-master", "rich-m4-protected-mip", "rich-m5-lns", "rich-m6-restricted-mip"}:
            self.assertGreater(group["native_score"], q0["native_score"])
            self.assertAlmostEqual(group["score_delta"], group["native_score"] - group["q0_score"])
        else:
            self.assertEqual("q0", group["selected_incumbent"])
            self.assertAlmostEqual(q0["native_score"], group["native_score"])

    def test_single_passenger_group_falls_back_to_q0(self):
        groups = self.cases["identity_keep_seats"]["groupsData"][:1]
        group = self.run_case("identity_keep_seats", groups=groups)
        self.assertEqual("q0", group["selected_incumbent"])
        self.assertEqual("not_strictly_better", group["fallback_reason"])

    def test_fixed_caregiver_protection_and_ssr_cases_stay_legal(self):
        for case_id in (
            "fixed_and_protected",
            "caregiver_and_ssr",
            "conditional_ssr_isolation",
        ):
            with self.subTest(case=case_id):
                result = self.run_case(case_id)
                self.assertIn(result["selected_incumbent"], {"q0", "group-aware", "rich-m2-vnd", "rich-m3-pattern-master", "rich-m4-protected-mip", "rich-m5-lns", "rich-m6-restricted-mip"})

    def test_beam_is_deterministic_and_bounded(self):
        first = self.run_case("large_groups_9_10")
        second = self.run_case("large_groups_9_10")
        self.assertEqual(first["assignments"], second["assignments"])
        self.assertEqual(first["beam_nodes"], second["beam_nodes"])
        self.assertGreater(first["beam_groups"], 0)
        self.assertLessEqual(first["beam_nodes"], 2 * 10 * 96 * 28)


if __name__ == "__main__":
    unittest.main()
