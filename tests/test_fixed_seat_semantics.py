import unittest

from src import allocation_evaluator as evaluator
from src import heuristic_seat_allocator as allocator


def make_seat(seat_id: str, col: str = "A") -> dict:
    return {
        "seatId": seat_id,
        "row": 1,
        "col": col,
        "isWindow": col == "A",
        "isAisle": col == "B",
        "isExitRow": False,
        "hasBassinet": False,
        "extraLegroom": False,
        "seatClass": "Economy",
        "nearToilet": False,
    }


def make_passenger(hostnum: int, fixed: str = "") -> dict:
    passenger = {
        "hostnum": hostnum,
        "cabin": "Y",
        "oldSeat": {"seatNum": "1A", "seatValue": ""},
        "needCared": "N",
        "ssr": "",
        "mandatoryRule": {},
        "optionRule": [],
    }
    if fixed:
        passenger["newSeat"] = {"seatNum": fixed}
    return passenger


def make_group(group_id: int, passengers: list) -> dict:
    return {
        "groupId": group_id,
        "groupType": "communityType",
        "PNR": f"PNR{group_id}",
        "psrs": passengers,
    }


class FixedSeatEvaluatorTests(unittest.TestCase):
    def test_unassigned_fixed_passenger_is_a_hard_violation(self):
        seats = [make_seat("1A")]
        groups = [make_group(1, [make_passenger(1, "1A")])]

        violations, unassigned, detail = (
            evaluator.count_hard_constraint_violations(
                seats, groups, assigned_seats={}, config={}
            )
        )

        self.assertEqual(violations, 1)
        self.assertEqual(unassigned, 1)
        self.assertEqual(detail["reserved_unassigned"], 1)

    def test_fixed_passenger_in_wrong_seat_is_a_hard_violation(self):
        seats = [make_seat("1A"), make_seat("1B", "B")]
        groups = [make_group(1, [make_passenger(1, "1A")])]

        violations, unassigned, detail = (
            evaluator.count_hard_constraint_violations(
                seats, groups, assigned_seats={(1, 1): "1B"}, config={}
            )
        )

        self.assertEqual(violations, 1)
        self.assertEqual(unassigned, 0)
        self.assertEqual(detail["reserved_mismatch"], 1)
        self.assertEqual(detail["reserved_unassigned"], 0)


class FixedSeatHeuristicTests(unittest.TestCase):
    def test_invalid_fixed_seat_stops_with_diagnostics(self):
        seats = [make_seat("1A")]
        groups = [make_group(1, [make_passenger(1, "9Z")])]

        with self.assertRaises(allocator.FixedSeatPrecheckError) as raised:
            allocator.run_allocation(seats, seats, groups, {}, {})

        diagnostics = raised.exception.diagnostics
        self.assertTrue(diagnostics["has_errors"])
        self.assertEqual(
            diagnostics["invalid_seats"][0]["reason"], "seat_not_found"
        )
        self.assertIn('"seat": "9Z"', str(raised.exception))

    def test_duplicate_fixed_seat_stops_instead_of_skipping_passengers(self):
        seats = [make_seat("1A")]
        groups = [
            make_group(1, [make_passenger(1, "1A")]),
            make_group(2, [make_passenger(1, "1A")]),
        ]

        with self.assertRaises(allocator.FixedSeatPrecheckError) as raised:
            allocator.run_allocation(seats, seats, groups, {}, {})

        self.assertEqual(
            raised.exception.diagnostics["counts"]["duplicate_seats"], 1
        )

    def test_valid_fixed_seat_is_always_assigned(self):
        seats = [make_seat("1A"), make_seat("1B", "B")]
        groups = [
            make_group(
                1,
                [make_passenger(1, "1A"), make_passenger(2)],
            )
        ]
        config = {
            "penalty": {"time_factor": 0},
            "algorithm": {"stage3_time_budget": 0.1},
        }

        result, _ = allocator.run_allocation(seats, seats, groups, {}, config)

        self.assertEqual(result["assigned_seats"][(1, 1)], "1A")
        self.assertFalse(
            result["diagnostics"]["reserved_precheck"]["has_errors"]
        )

    def test_single_empty_fixed_passenger_does_not_block_other_fixed_seat(self):
        seats = [
            make_seat("1A", "A"),
            make_seat("1B", "B"),
            make_seat("1C", "C"),
        ]
        needs_empty = make_passenger(1, "1B")
        needs_empty["mandatoryRule"]["needSingleSideEmpty"] = "Y"
        groups = [
            make_group(1, [needs_empty]),
            make_group(2, [make_passenger(1, "1A")]),
        ]
        config = {
            "penalty": {"time_factor": 0},
            "algorithm": {"stage3_time_budget": 0.1},
        }

        result, _ = allocator.run_allocation(seats, seats, groups, {}, config)

        self.assertEqual(result["assigned_seats"][(1, 1)], "1B")
        self.assertEqual(result["assigned_seats"][(2, 1)], "1A")
        self.assertIn("1C", result["occupied"] + list(result["blocked_by"]))


if __name__ == "__main__":
    unittest.main()
