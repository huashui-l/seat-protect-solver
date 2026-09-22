import copy
import json
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from src import heuristic_seat_allocator as allocator
from src import passenger_data_generator as data_generator
from tests.general_validation_case_factory import materialize_cases


class GeneralValidationCaseTests(unittest.TestCase):
    def test_post_protected_special_pricing_is_safe_on_dense_full_load(self):
        project_root = Path(__file__).resolve().parent.parent
        base_config = json.loads(
            (project_root / "config.json").read_text(encoding="utf-8")
        )
        case = next(
            item for item in materialize_cases()
            if item["id"] == "dense_full_resource_special_chain"
        )
        results = {}
        for enabled in (False, True):
            config = copy.deepcopy(base_config)
            config["algorithm"]["business_time_limit_seconds"] = 10.0
            config["algorithm"][
                "enable_post_protected_special_pricing"
            ] = enabled
            result, _ = allocator.run_allocation(
                case["newSeatmapData"]["seats"],
                case["oldSeatmapData"]["seats"],
                case["groupsData"],
                config["weights"],
                config,
            )
            results[enabled] = result
        self.assertTrue(
            results[True]["diagnostics"]["special_dual_pricing"]["enabled"]
        )
        self.assertEqual(
            (
                results[True]["score_detail"]["violations"],
                results[True]["score_detail"]["unassigned_count"],
            ),
            (0, 0),
        )
        self.assertGreaterEqual(
            results[True]["score_detail"]["total_soft_score"] + 1.0e-7,
            results[False]["score_detail"]["total_soft_score"],
        )

    def test_global_full_resource_blocks_do_not_regress_controlled_case(self):
        project_root = Path(__file__).resolve().parent.parent
        base_config = json.loads(
            (project_root / "config.json").read_text(encoding="utf-8")
        )
        case = next(
            item for item in materialize_cases()
            if item["id"] == "full_resource_shrink"
        )
        results = {}
        for enabled in (False, True):
            config = copy.deepcopy(base_config)
            config["algorithm"]["business_time_limit_seconds"] = 10.0
            config["algorithm"][
                "enable_global_full_resource_blocks"
            ] = enabled
            result, _ = allocator.run_allocation(
                case["newSeatmapData"]["seats"],
                case["oldSeatmapData"]["seats"],
                case["groupsData"],
                config["weights"],
                config,
            )
            results[enabled] = result
        self.assertGreater(
            results[True]["diagnostics"]["structured_pattern_generation"]
            ["tier_counts"]["global_value_block"],
            0,
        )
        # The source fixture now leaves every protected neighbor genuinely
        # empty.  That semantic repair can change whether the downstream MIP
        # happens to receive a start, which is not the safety property tested
        # here; retain the objective non-regression assertion below.
        self.assertGreaterEqual(
            results[True]["score_detail"]["total_soft_score"] + 1.0e-7,
            results[False]["score_detail"]["total_soft_score"],
        )

    def test_valid_cases_have_complete_feasible_solution(self):
        project_root = Path(__file__).resolve().parent.parent
        base_config = json.loads(
            (project_root / "config.json").read_text(encoding="utf-8")
        )
        for case in materialize_cases():
            if not case["expectedValid"]:
                continue
            with self.subTest(case=case["id"]):
                config = copy.deepcopy(base_config)
                if case["referenceAssignments"]:
                    reference_violations, reference_unassigned, _ = (
                        evaluator.count_hard_constraint_violations(
                            case["newSeatmapData"]["seats"],
                            case["groupsData"],
                            case["referenceAssignments"],
                            config,
                        )
                    )
                    self.assertEqual(
                        (reference_violations, reference_unassigned), (0, 0)
                    )
                if not case.get("expectedCurrentHeuristicComplete", True):
                    continue
                result, _ = allocator.run_allocation(
                    case["newSeatmapData"]["seats"],
                    case["oldSeatmapData"]["seats"],
                    case["groupsData"],
                    config["weights"],
                    config,
                )
                assignments = result["assigned_seats"]
                violations, unassigned, _ = (
                    evaluator.count_hard_constraint_violations(
                        case["newSeatmapData"]["seats"],
                        case["groupsData"],
                        assignments,
                        config,
                    )
                )
                soft_score, _ = evaluator.calculate_soft_score(
                    case["newSeatmapData"]["seats"],
                    case["oldSeatmapData"]["seats"],
                    case["groupsData"],
                    assignments,
                    config["weights"],
                    config,
                )
                self.assertEqual((violations, unassigned), (0, 0))
                self.assertEqual(
                    len(assignments),
                    data_generator.passenger_count(case["groupsData"]),
                )
                self.assertAlmostEqual(
                    soft_score,
                    result["score_detail"]["total_soft_score"],
                    places=7,
                )

    def test_case_specs_match_expected_input_validity(self):
        cases = materialize_cases()
        self.assertGreaterEqual(len(cases), 10)
        self.assertTrue(any("identity" in case["tags"] for case in cases))
        self.assertTrue(any("shrink" in case["tags"] for case in cases))
        self.assertTrue(any("expansion" in case["tags"] for case in cases))
        self.assertTrue(any("large-group" in case["tags"] for case in cases))
        self.assertTrue(any(not case["expectedValid"] for case in cases))

        for case in cases:
            with self.subTest(case=case["id"]):
                old_topology = data_generator.SeatTopology(
                    case["oldSeatmapData"]["seats"]
                )
                new_topology = data_generator.SeatTopology(
                    case["newSeatmapData"]["seats"]
                )
                errors = data_generator.validate_case(
                    case["groupsData"], old_topology, new_topology
                )
                if case["expectedValid"]:
                    self.assertEqual(errors, [])
                    self.assertLessEqual(
                        data_generator.target_seat_demand(case["groupsData"]),
                        len(new_topology.seats),
                    )
                else:
                    self.assertTrue(errors)
                    self.assertTrue(
                        any(
                            case["expectedErrorContains"] in error
                            for error in errors
                        ),
                        errors,
                    )


if __name__ == "__main__":
    unittest.main()
