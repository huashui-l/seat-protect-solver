import copy
import json
import unittest
from pathlib import Path

from src import allocation_evaluator as evaluator
from src import heuristic_seat_allocator as allocator
from tests.general_validation_case_factory import materialize_cases


ROOT = Path(__file__).resolve().parents[1]


class GeneralValidationSmokeTests(unittest.TestCase):
    def test_identity_synthetic_case_is_complete_and_legal(self):
        case = next(
            item for item in materialize_cases()
            if item["id"] == "identity_keep_seats"
        )
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        config = copy.deepcopy(config)
        config["algorithm"]["business_time_limit_seconds"] = 0.5
        config["algorithm"]["stage3_time_budget"] = 0.5
        result, _ = allocator.run_allocation(
            case["newSeatmapData"]["seats"],
            case["oldSeatmapData"]["seats"],
            case["groupsData"],
            config["weights"],
            config,
        )
        violations, unassigned, _ = evaluator.count_hard_constraint_violations(
            case["newSeatmapData"]["seats"],
            case["groupsData"],
            result["assigned_seats"],
            config,
        )
        self.assertEqual((0, 0), (violations, unassigned))


if __name__ == "__main__":
    unittest.main()
