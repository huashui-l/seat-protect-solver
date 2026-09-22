import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from tests.general_validation_case_factory import materialize_cases


ROOT = Path(__file__).resolve().parents[1]


class NativeSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("SEAT_PROTECT_NATIVE_EXE"),
        "set SEAT_PROTECT_NATIVE_EXE to a binary built from this checkout",
    )
    def test_raw_cli_matches_python_on_small_synthetic_case(self):
        executable = Path(os.environ["SEAT_PROTECT_NATIVE_EXE"]).resolve()
        self.assertTrue(executable.is_file(), executable)
        case = next(
            item for item in materialize_cases()
            if item["id"] == "identity_keep_seats"
        )
        base_config = json.loads(
            (ROOT / "config.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            old_path = work / "old_seatmap.json"
            new_path = work / "new_seatmap.json"
            case_path = work / "case.json"
            config_path = work / "config.json"
            old_path.write_text(
                json.dumps(case["oldSeatmapData"], ensure_ascii=False),
                encoding="utf-8",
            )
            new_path.write_text(
                json.dumps(case["newSeatmapData"], ensure_ascii=False),
                encoding="utf-8",
            )
            case_path.write_text(
                json.dumps(
                    {
                        "caseId": case["id"],
                        "direction": "smoke",
                        "groups": case["groupsData"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config = dict(base_config)
            config["input_contract"] = {
                "seatmaps_by_direction": {
                    "smoke": {"old": old_path.name, "new": new_path.name}
                }
            }
            config_path.write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    str(executable),
                    "--input", str(case_path),
                    "--config", str(config_path),
                    "--time-limit", "5",
                    "--seed", "0",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            individual_completed = subprocess.run(
                [
                    str(executable),
                    "--input", str(case_path),
                    "--config", str(config_path),
                    "--time-limit", "5",
                    "--seed", "0",
                    "--construction-objective", "individual-soft",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            0, individual_completed.returncode, individual_completed.stderr
        )
        result = json.loads(completed.stdout)
        individual_result = json.loads(individual_completed.stdout)
        assignments = {
            (item["groupId"], item["hostnum"]): item["seatId"]
            for item in result["assignments"]
        }
        violations, unassigned, _ = evaluator.count_hard_constraint_violations(
            case["newSeatmapData"]["seats"],
            case["groupsData"],
            assignments,
            base_config,
        )
        score, detail = evaluator.calculate_soft_score(
            case["newSeatmapData"]["seats"],
            case["oldSeatmapData"]["seats"],
            case["groupsData"],
            assignments,
            base_config["weights"],
            base_config,
        )
        self.assertTrue(result["complete"])
        self.assertEqual((0, 0), (violations, unassigned))
        self.assertEqual(0, result["native_hard_violations"])
        self.assertAlmostEqual(score, result["native_score"], places=8)
        self.assertEqual("feasibility", result["construction_objective"])
        self.assertAlmostEqual(
            detail["score_s"] + detail["score_v"] + detail["score_p"],
            result["individual_score"],
            places=8,
        )

        individual_assignments = {
            (item["groupId"], item["hostnum"]): item["seatId"]
            for item in individual_result["assignments"]
        }
        individual_violations, individual_unassigned, _ = (
            evaluator.count_hard_constraint_violations(
                case["newSeatmapData"]["seats"],
                case["groupsData"],
                individual_assignments,
                base_config,
            )
        )
        _, individual_detail = evaluator.calculate_soft_score(
            case["newSeatmapData"]["seats"],
            case["oldSeatmapData"]["seats"],
            case["groupsData"],
            individual_assignments,
            base_config["weights"],
            base_config,
        )
        expected_individual_score = sum(
            individual_detail[key] for key in ("score_s", "score_v", "score_p")
        )
        self.assertEqual("individual-soft", individual_result["construction_objective"])
        self.assertEqual((0, 0), (individual_violations, individual_unassigned))
        self.assertEqual(0, individual_result["native_hard_violations"])
        self.assertAlmostEqual(
            expected_individual_score,
            individual_result["individual_score"],
            places=8,
        )
        self.assertGreater(
            individual_result["individual_score"], result["individual_score"]
        )
        self.assertAlmostEqual(0.0, individual_result["individual_score"], places=8)

    @unittest.skipUnless(
        os.environ.get("SEAT_PROTECT_NATIVE_EXE"),
        "set SEAT_PROTECT_NATIVE_EXE to a binary built from this checkout",
    )
    def test_individual_soft_preserves_public_hard_constraint_cases(self):
        executable = Path(os.environ["SEAT_PROTECT_NATIVE_EXE"]).resolve()
        base_config = json.loads(
            (ROOT / "config.json").read_text(encoding="utf-8")
        )
        cases = {
            item["id"]: item for item in materialize_cases()
            if item["id"] in {
                "fixed_and_protected",
                "caregiver_and_ssr",
                "conditional_ssr_isolation",
            }
        }
        for case_id, case in cases.items():
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as directory:
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
                    "groups": case["groupsData"],
                }), encoding="utf-8")
                config = dict(base_config)
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}
                }}
                config_path = work / "config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                completed = subprocess.run([
                    str(executable), "--input", str(case_path),
                    "--config", str(config_path), "--time-limit", "5",
                    "--seed", "0", "--construction-objective", "individual-soft",
                ], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(0, completed.returncode, completed.stderr)
                result = json.loads(completed.stdout)
                assignments = {
                    (item["groupId"], item["hostnum"]): item["seatId"]
                    for item in result["assignments"]
                }
                violations, unassigned, _ = evaluator.count_hard_constraint_violations(
                    case["newSeatmapData"]["seats"], case["groupsData"],
                    assignments, base_config,
                )
                self.assertEqual((0, 0), (violations, unassigned))
                self.assertEqual(0, result["native_hard_violations"])
                for group in case["groupsData"]:
                    for passenger in group["psrs"]:
                        fixed = passenger.get("newSeat", {}).get("seatNum")
                        if fixed:
                            self.assertEqual(
                                fixed,
                                assignments[(group["groupId"], passenger["hostnum"])],
                            )


if __name__ == "__main__":
    unittest.main()

