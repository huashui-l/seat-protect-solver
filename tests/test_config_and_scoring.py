import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src import allocation_evaluator as evaluator
from src import allocation_visualizer as visualize
from src import heuristic_seat_allocator as final


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMAL24_ROOT = (
    PROJECT_ROOT
    / "data"
    / "generator_v3_2"
    / "d3_official_24cases_2026-09-07"
)
FORMAL24_CONFIG = PROJECT_ROOT / "rich_python_reference_config.json"


def make_seat(seat_id: str, row: int, seat_class: str = "Economy", col: str = "A") -> dict:
    return {
        "seatId": seat_id,
        "row": row,
        "col": col,
        "isWindow": True,
        "isAisle": False,
        "isExitRow": False,
        "hasBassinet": False,
        "extraLegroom": False,
        "seatClass": seat_class,
        "nearToilet": False,
    }


class ConfigTests(unittest.TestCase):
    def test_visualizer_rejects_two_result_sources(self):
        with self.assertRaisesRegex(ValueError, "只能提供一个"):
            visualize.run_visualization(
                PROJECT_ROOT / "config.json",
                allocation_result={},
                result_path=PROJECT_ROOT / "config.json",
                show=False,
            )

    def test_allocation_quality_is_lexicographic(self):
        complete = final._allocation_quality(0, 0, -1000.0)
        incomplete = final._allocation_quality(0, 1, 1000.0)
        violating = final._allocation_quality(1, 0, 1000.0)

        self.assertGreater(complete, incomplete)
        self.assertGreater(incomplete, violating)
        self.assertGreater(
            final._allocation_quality(0, 0, -10.0), complete
        )

    def test_group_signature_tolerates_unassigned_passenger(self):
        context = type(
            "ContextStub",
            (),
            {"assigned_seats": {(8, 1): "1A", (8, 2): "1C"}},
        )()

        signature = final._current_group_assignment_signature(
            context, [(8, 1), (8, 2), (8, 3)]
        )

        self.assertEqual(signature, ((1, "1A"), (2, "1C"), (3, None)))

    def test_budget_load_counts_protected_empty_seats(self):
        groups = [{
            "psrs": [
                {"mandatoryRule": {}},
                {"mandatoryRule": {"needSingleSideEmpty": "Y"}},
                {
                    "mandatoryRule": {
                        "needSingleSideEmpty": "Y",
                        "needBothSideEmpty": "Y",
                    }
                },
            ]
        }]

        self.assertEqual(final._seat_demand_for_budgeting(groups), 6)

    def test_config_matches_implemented_algorithm_options(self):
        config = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
        algorithm = config["algorithm"]

        self.assertNotIn("max_attempts_per_group", algorithm)
        self.assertNotIn("local_search_iterations", algorithm)
        self.assertEqual(algorithm["stage3_time_budget"], 20.0)
        self.assertEqual(algorithm["candidate_cap"], 48)
        self.assertEqual(algorithm["group_centroid_x_factor"], 1.0)
        self.assertEqual(algorithm["group_centroid_y_factor"], 1.0)
        self.assertLess(config["weights"]["w_c"], 0.0)
        self.assertNotIn("w_class", config["weights"])
        self.assertEqual(config["mandatory_rules"]["both_side_empty_semantics"], "empty_adjacent_seats")
        self.assertTrue(config["mandatory_rules"]["require_two_real_neighbors"])
        self.assertTrue(config["mandatory_rules"]["both_side_empty_allow_cross_aisle"])
        self.assertEqual(
            set(config["ssr_rules"]),
            {"CHD", "WCHR", "BLND", "BSCT", "UM", "EXST", "CBBG"},
        )
        self.assertFalse(
            config["ssr_rules"]["CHD"]["caregiver_allow_cross_aisle"]
        )
        self.assertFalse(
            config["ssr_rules"]["WCHR"]["caregiver_allow_cross_aisle"]
        )
        self.assertTrue(
            config["ssr_rules"]["BLND"]["caregiver_allow_cross_aisle"]
        )
        self.assertTrue(
            config["ssr_rules"]["BSCT"]["caregiver_allow_cross_aisle"]
        )
        pricing = config["column_generation"]
        self.assertEqual(pricing["exact_pricing_columns_per_group"], 4)
        self.assertGreater(pricing["quick_pricing_columns_per_group"], 1)
        self.assertGreater(
            pricing["dfs_exact_time_limit"],
            pricing["dfs_discovery_time_limit"],
        )

        for value in config["data"].values():
            self.assertFalse(Path(value).is_absolute(), value)

    def test_fixed_new_seats_preserve_cabin(self):
        config = json.loads(FORMAL24_CONFIG.read_text(encoding="utf-8"))
        old_seats = json.loads(
            (PROJECT_ROOT / config["data"]["oldseatmap"]).read_text(encoding="utf-8")
        )["seats"]
        new_seats = json.loads(
            (PROJECT_ROOT / config["data"]["seatmap"]).read_text(encoding="utf-8")
        )["seats"]
        groups = json.loads(
            (FORMAL24_ROOT / "forward" / "50_normal_groups.json").read_text(
                encoding="utf-8"
            )
        )["groups"]
        old_classes = {seat["seatId"]: seat["seatClass"] for seat in old_seats}
        new_classes = {seat["seatId"]: seat["seatClass"] for seat in new_seats}

        for group in groups:
            for passenger in group["psrs"]:
                fixed = passenger.get("newSeat", {}).get("seatNum")
                if fixed:
                    self.assertEqual(
                        old_classes[passenger["oldSeat"]["seatNum"]],
                        new_classes[fixed],
                    )

    def test_both_side_empty_coverage_increases_with_difficulty(self):
        from src.passenger_data_generator import SCENARIOS

        ratios = [SCENARIOS[name]["both_side_empty_ratio"] for name in ("normal", "stress", "edge")]
        minimums = [SCENARIOS[name]["minimum_both_side_empty"] for name in ("normal", "stress", "edge")]
        self.assertEqual(ratios, sorted(ratios))
        self.assertEqual(minimums, sorted(minimums))
        self.assertGreater(ratios[-1], ratios[0])

    def test_generator_can_assign_true_two_sided_empty_rule(self):
        from src.passenger_data_generator import SeatTopology, assign_both_side_empty_rules
        import random

        topology = SeatTopology([
            make_seat(f"1{col}", 1, col=col)
            for col in ("A", "B", "C", "D", "E")
        ])
        passenger = {
            "hostnum": 1,
            "cabin": "Y",
            "oldSeat": {"seatNum": "1C", "seatValue": ""},
            "needCared": "N",
            "ssr": "",
            "mandatoryRule": {
                "needBothSideEmpty": "N",
                "needSingleSideEmpty": "N",
            },
        }
        groups = [{"groupId": 1, "psrs": [passenger]}]
        assigned = assign_both_side_empty_rules(
            groups,
            topology,
            random.Random(1),
            {"both_side_empty_ratio": 0.01, "minimum_both_side_empty": 1},
        )

        self.assertEqual(assigned, 1)
        self.assertEqual(passenger["mandatoryRule"]["needBothSideEmpty"], "Y")
        self.assertEqual(topology.adjacent("1C"), ["1B", "1D"])


class ScoringTests(unittest.TestCase):
    def test_incremental_soft_delta_matches_full_evaluator(self):
        seats = [
            make_seat("1A", 1, col="A"),
            make_seat("1B", 1, col="B"),
            make_seat("2A", 2, col="A"),
        ]
        groups = [
            {
                "groupId": group_id,
                "psrs": [{
                    "hostnum": 1,
                    "cabin": "Y",
                    "oldSeat": {"seatNum": seat_id, "seatValue": ""},
                    "needCared": "N",
                    "ssr": "BSCT" if group_id == 1 else "",
                    "mandatoryRule": {},
                    "optionRule": [],
                }],
            }
            for group_id, seat_id in ((1, "1A"), (2, "1B"), (3, "2A"))
        ]
        weights = {
            "w_s": -1.0,
            "w_v": -0.5,
            "w_p": -0.8,
            "w_c": -3.0,
            "w_b": -0.3,
        }
        config = {
            "algorithm": {
                "group_centroid_x_factor": 1.0,
                "group_centroid_y_factor": 1.0,
                "baby_front_back_factor": 1.0,
            }
        }
        before = {(1, 1): "1A", (2, 1): "1B", (3, 1): "2A"}
        after = {(1, 1): "1B", (2, 1): "1A", (3, 1): "2A"}
        scorer = evaluator.IncrementalSoftScorer(
            seats, seats, groups, weights, config
        )
        incremental_delta = scorer.delta(
            before, after, {1, 2}
        )
        before_score, _ = evaluator.calculate_soft_score(
            seats, seats, groups, before, weights, config
        )
        after_score, _ = evaluator.calculate_soft_score(
            seats, seats, groups, after, weights, config
        )

        self.assertAlmostEqual(
            incremental_delta, after_score - before_score
        )

    def test_time_factor_is_applied_to_combined_score(self):
        config = {
            "penalty": {
                "unassigned": 300,
                "violation": 500,
                "time_factor": 10,
                "score_lower_bound": -999999,
            }
        }
        score, time_penalty = evaluator.calculate_combined_score(
            soft_score=-10,
            unassigned_count=1,
            violations=2,
            elapsed=2.0,
            config=config,
        )
        self.assertEqual(time_penalty, 20.0)
        self.assertEqual(score, -1330.0)

    def test_prioritize_front_changes_distance_penalty(self):
        old = final.Seat(make_seat("10A", 10))
        new = final.Seat(make_seat("9A", 9))
        passenger = final.Passenger({
            "hostnum": 1,
            "cabin": "Y",
            "oldSeat": {"seatNum": "10A", "seatValue": ""},
        })
        seats = {old.seat_id: old, new.seat_id: new}
        values = {old.seat_id: 0.0, new.seat_id: 0.0}
        weights = {"w_s": -1.0, "w_v": 0.0, "w_p": 0.0, "w_b": 0.0}
        final._SEAT_X = {old.seat_id: 0.0, new.seat_id: 0.0}
        final._OLD_SEATS = {old.seat_id: old}
        final._OLD_SEAT_X = {old.seat_id: 0.0}

        preferred = final.calc_seat_sort_key(
            passenger,
            new,
            values,
            weights,
            {"algorithm": {"prioritize_front": True, "front_penalty_reduction": 0.1}},
        )
        neutral = final.calc_seat_sort_key(
            passenger,
            new,
            values,
            weights,
            {"algorithm": {"prioritize_front": False, "front_penalty_reduction": 0.1}},
        )

        self.assertAlmostEqual(preferred, 0.1)
        self.assertAlmostEqual(neutral, 1.0)

    def test_cabin_is_not_a_soft_sort_component(self):
        old = final.Seat(make_seat("1A", 1, "Business"))
        changed = final.Seat(make_seat("1B", 1, "Economy", "B"))
        passenger = final.Passenger({
            "hostnum": 1,
            "cabin": "C",
            "oldSeat": {"seatNum": "1A", "seatValue": ""},
        })
        seats = {old.seat_id: old, changed.seat_id: changed}
        values = {old.seat_id: 0.0, changed.seat_id: 0.0}
        final._SEAT_X = {old.seat_id: 0.0, changed.seat_id: 0.0}
        # calc_seat_sort_key normally reads this table from run_allocation's
        # initialized module state; inject it explicitly so the unit test is
        # independent of execution order.
        with patch.object(final, "_OLD_SEATS", seats):
            score = final.calc_seat_sort_key(
                passenger,
                changed,
                values,
                {"w_s": 0.0, "w_v": 0.0, "w_p": 0.0, "w_b": 0.0},
                {"algorithm": {}},
            )
        self.assertEqual(score, 0.0)

    def test_cabin_change_is_rejected_as_a_hard_violation(self):
        business = make_seat("1A", 1, "Business", "A")
        economy = make_seat("1B", 1, "Economy", "B")
        passenger_data = {
            "hostnum": 1,
            "cabin": "C",
            "oldSeat": {"seatNum": "1A", "seatValue": ""},
            "needCared": "N",
            "ssr": "",
            "mandatoryRule": {},
            "optionRule": [],
        }
        groups = [{"groupId": 1, "psrs": [passenger_data]}]

        violations, unassigned, detail = (
            evaluator.count_hard_constraint_violations(
                [business, economy], groups, {(1, 1): "1B"}
            )
        )
        self.assertEqual(unassigned, 0)
        self.assertEqual(violations, 1)
        self.assertEqual(detail["cabin_mismatch_violation"], 1)

        seats = {
            seat.seat_id: seat
            for seat in (final.Seat(business), final.Seat(economy))
        }
        self.assertFalse(
            final.is_seat_feasible(
                seats["1B"],
                final.Passenger(passenger_data),
                set(),
                {},
                set(),
                seats,
            )
        )

    def test_mixed_cabin_group_is_a_hard_input_violation(self):
        seats = [
            make_seat("1A", 1, "Business", "A"),
            make_seat("2A", 2, "Economy", "A"),
        ]
        groups = [{
            "groupId": 7,
            "psrs": [
                {
                    "hostnum": 1, "cabin": "C", "ssr": "",
                    "needCared": "N", "oldSeat": {"seatNum": "1A"},
                    "mandatoryRule": {}, "optionRule": [],
                },
                {
                    "hostnum": 2, "cabin": "Y", "ssr": "",
                    "needCared": "N", "oldSeat": {"seatNum": "2A"},
                    "mandatoryRule": {}, "optionRule": [],
                },
            ],
        }]
        violations, _, detail = evaluator.count_hard_constraint_violations(
            seats, groups, {(7, 1): "1A", (7, 2): "2A"}, {}
        )
        self.assertEqual(violations, 1)
        self.assertEqual(detail["mixed_cabin_group_violation"], 1)
        self.assertEqual(detail["mixed_cabin_group_ids"], [7])
        with self.assertRaisesRegex(ValueError, "混舱组"):
            final.run_allocation(seats, seats, groups, {}, {})

    def test_baby_interference_does_not_cross_cabin(self):
        seats = [
            make_seat("1A", 1, "Business", "A"),
            make_seat("2A", 2, "Economy", "A"),
        ]
        topology = evaluator.SeatTopology(seats, {})
        self.assertEqual(
            evaluator.baby_interference_score(
                "1A", "2A", topology, {"w_b": -0.3}, {}
            ),
            0.0,
        )

    def test_conditional_ssr_isolation_does_not_cross_cabin(self):
        seats = [
            make_seat("1A", 1, "Business", "A"),
            make_seat("1B", 1, "Economy", "B"),
        ]
        groups = [
            {
                "groupId": 1,
                "psrs": [{
                    "hostnum": 1, "cabin": "C", "ssr": "UM",
                    "needCared": "N", "oldSeat": {"seatNum": "1A"},
                    "mandatoryRule": {"sameRowNoOtherSSR": "Y"},
                    "optionRule": [],
                }],
            },
            {
                "groupId": 2,
                "psrs": [{
                    "hostnum": 1, "cabin": "Y", "ssr": "UM",
                    "needCared": "N", "oldSeat": {"seatNum": "1B"},
                    "mandatoryRule": {}, "optionRule": [],
                }],
            },
        ]
        violations, unassigned, detail = (
            evaluator.count_hard_constraint_violations(
                seats, groups, {(1, 1): "1A", (2, 1): "1B"}, {}
            )
        )
        self.assertEqual(unassigned, 0)
        self.assertEqual(violations, 0)
        self.assertEqual(detail["same_row_ssr_conflict"], 0)

    def test_flagged_ssr_row_blocks_a_second_passenger_of_any_ssr_type(self):
        seats = {
            seat.seat_id: seat
            for seat in (
                final.Seat(make_seat("1A", 1, col="A")),
                final.Seat(make_seat("1B", 1, col="B")),
                final.Seat(make_seat("1C", 1, col="C")),
            )
        }
        flagged = final.Passenger({
            "hostnum": 1, "cabin": "Y", "ssr": "UM",
            "oldSeat": {"seatNum": "1A", "seatValue": ""},
            "mandatoryRule": {"sameRowNoOtherSSR": "Y"},
        })
        first_bsct = final.Passenger({
            "hostnum": 2, "cabin": "Y", "ssr": "BSCT",
            "oldSeat": {"seatNum": "1B", "seatValue": ""},
            "mandatoryRule": {},
        })
        second_bsct = final.Passenger({
            "hostnum": 3, "cabin": "Y", "ssr": "BSCT",
            "oldSeat": {"seatNum": "1C", "seatValue": ""},
            "mandatoryRule": {},
        })
        self.assertFalse(
            final.is_seat_feasible(
                seats["1C"], second_bsct,
                {"1A", "1B"},
                {"1A": flagged, "1B": first_bsct},
                set(), seats,
            )
        )

    def test_group_compactness_uses_physical_centroid_distance(self):
        left = final.Seat(make_seat("10A", 10, col="A"))
        right = final.Seat(make_seat("10F", 10, col="F"))
        below = final.Seat(make_seat("11A", 11, col="A"))
        final._SEAT_X = {left.seat_id: 0.0, right.seat_id: 5.0, below.seat_id: 0.0}
        config = {
            "algorithm": {
                "group_centroid_x_factor": 1.0,
                "group_centroid_y_factor": 1.0,
            },
            "seat_geometry": {"row_spacing": 1.5},
        }

        same_row = final.calc_group_compactness([left, right], config)
        two_rows = final.calc_group_compactness([left, below], config)

        self.assertAlmostEqual(same_row, 2.5)
        self.assertAlmostEqual(two_rows, 0.75)
        self.assertLess(two_rows, same_row)

    def test_group_compactness_is_maximum_deviation_from_centroid(self):
        seats = {
            col: final.Seat(make_seat(f"1{col}", 1, col=col))
            for col in ("A", "B", "C")
        }
        final._SEAT_X = {"1A": 0.0, "1B": 1.0, "1C": 2.0}
        final._SEAT_SUBROW = {sid: (1, 0) for sid in ("1A", "1B", "1C")}
        final._SEAT_ROW_INDEX = {"1A": 0, "1B": 1, "1C": 2}
        final._SEAT_ROW_NEIGHBORS = {"1A": ["1B"], "1B": ["1A", "1C"], "1C": ["1B"]}
        final._GROUP_COMPACT_CACHE = {}
        config = {"algorithm": {
            "group_centroid_x_factor": 1.0,
            "group_centroid_y_factor": 1.0,
        }}
        contiguous = final.calc_group_compactness([seats["A"], seats["B"]], config)
        with_hole = final.calc_group_compactness([seats["A"], seats["C"]], config)
        asymmetric = final.calc_group_compactness(
            [seats["A"], seats["B"], seats["C"]], config
        )
        self.assertEqual(contiguous, 0.5)
        self.assertEqual(with_hole, 1.0)
        self.assertEqual(asymmetric, 1.0)

    def test_joint_rescue_assigns_failed_ssr_with_caregiver(self):
        raw_seats = [make_seat(f"1{col}", 1, col=col) for col in ("A", "B", "C")]
        seats = {raw["seatId"]: final.Seat(raw) for raw in raw_seats}
        final._SEAT_NEIGHBORS = {"1A": ["1B"], "1B": ["1A", "1C"], "1C": ["1B"]}
        final._SEAT_ROW_NEIGHBORS = dict(final._SEAT_NEIGHBORS)
        final._SEAT_SUBROW = {sid: (1, 0) for sid in seats}
        final._SEAT_X = {"1A": 0.0, "1B": 1.0, "1C": 2.0}
        final._OLD_SEATS = dict(seats)
        final._OLD_SEAT_X = dict(final._SEAT_X)
        config = {"ssr_rules": {"CHD": {
            "allow_exit_row": False,
            "requires_caregiver": True,
            "caregiver_allow_cross_aisle": False,
        }}, "algorithm": {"paired_rescue_option_cap": 6}}
        final._ACTIVE_CONFIG = config
        group = final.Group({
            "groupId": 1, "groupType": "careType", "PNR": "ABC123",
            "psrs": [
                {"hostnum": 1, "cabin": "Y", "ssr": "CHD", "needCared": "Y", "oldSeat": {"seatNum": "1A", "seatValue": ""}},
                {"hostnum": 2, "cabin": "Y", "ssr": "", "needCared": "N", "oldSeat": {"seatNum": "1B", "seatValue": ""}},
            ],
        })
        context = final.AssignmentContext(seats)
        sorted_seats = {
            (1, passenger.hostnum): list(seats.values())
            for passenger in group.passengers
        }
        values = {sid: 0.0 for sid in seats}
        weights = {"w_s": 0.0, "w_v": 0.0, "w_p": 0.0, "w_b": 0.0}
        diagnostics = final.rescue_failed_paired_ssrs(
            [group], context, sorted_seats, values, weights, config, {})
        self.assertEqual(diagnostics["rescued_count"], 1)
        self.assertEqual(len(context.assigned_seats), 2)
        assigned = set(context.assigned_seats.values())
        self.assertTrue(any(set(pair).issubset(assigned) for pair in (("1A", "1B"), ("1B", "1C"))))

    def test_joint_rescue_rebuilds_two_cared_passengers_around_one_adult(self):
        raw_seats = [
            make_seat("1A", 1, col="A"),
            make_seat("1B", 1, col="B"),
            make_seat("2A", 2, col="A"),
            make_seat("2B", 2, col="B"),
            make_seat("2C", 2, col="C"),
        ]
        raw_seats[0]["hasBassinet"] = True
        raw_seats[2]["hasBassinet"] = True
        seats = {raw["seatId"]: final.Seat(raw) for raw in raw_seats}
        final._SEAT_NEIGHBORS = {
            "1A": ["1B"], "1B": ["1A"],
            "2A": ["2B"], "2B": ["2A", "2C"], "2C": ["2B"],
        }
        final._SEAT_ROW_NEIGHBORS = dict(final._SEAT_NEIGHBORS)
        final._SEAT_SUBROW = {
            "1A": (1, 0), "1B": (1, 0),
            "2A": (2, 0), "2B": (2, 0), "2C": (2, 0),
        }
        final._SEAT_X = {
            "1A": 0.0, "1B": 1.0,
            "2A": 0.0, "2B": 1.0, "2C": 2.0,
        }
        final._OLD_SEATS = dict(seats)
        final._OLD_SEAT_X = dict(final._SEAT_X)
        config = {
            "ssr_rules": {
                "BSCT": {
                    "allow_exit_row": False,
                    "requires_caregiver": True,
                    "requires_bassinet": True,
                    "caregiver_allow_cross_aisle": False,
                },
                "CHD": {
                    "allow_exit_row": False,
                    "requires_caregiver": True,
                    "caregiver_allow_cross_aisle": False,
                },
            },
            "algorithm": {
                "paired_rescue_option_cap": 6,
                "paired_joint_rebuild_candidate_cap": 10,
                "paired_joint_rebuild_node_limit": 1000,
            },
        }
        final._ACTIVE_CONFIG = config
        group = final.Group({
            "groupId": 1, "groupType": "careType", "PNR": "MULTI",
            "psrs": [
                {
                    "hostnum": 1, "cabin": "Y", "ssr": "BSCT",
                    "needCared": "Y",
                    "oldSeat": {"seatNum": "1A", "seatValue": ""},
                },
                {
                    "hostnum": 2, "cabin": "Y", "ssr": "",
                    "needCared": "N",
                    "oldSeat": {"seatNum": "1B", "seatValue": ""},
                },
                {
                    "hostnum": 3, "cabin": "Y", "ssr": "CHD",
                    "needCared": "Y",
                    "oldSeat": {"seatNum": "2C", "seatValue": ""},
                },
            ],
        })
        context = final.AssignmentContext(seats)
        adult = group.passengers[1]
        bassinet = group.passengers[0]
        self.assertTrue(context.assign_passenger(adult, "1B", 1))
        self.assertTrue(
            context.assign_passenger(
                bassinet, "1A", 1, exclude_block_seats={"1B"}
            )
        )
        sorted_seats = {
            (1, passenger.hostnum): list(seats.values())
            for passenger in group.passengers
        }
        diagnostics = final.rescue_failed_paired_ssrs(
            [group],
            context,
            sorted_seats,
            {seat_id: 0.0 for seat_id in seats},
            {
                "w_s": 0.0, "w_v": 0.0, "w_p": 0.0,
                "w_b": 0.0,
            },
            config,
            {},
        )

        self.assertEqual(diagnostics.get("joint_rebuilds"), 1)
        self.assertEqual(
            set(context.assigned_seats.values()),
            {"2A", "2B", "2C"},
        )

    def test_preassigned_seat_must_match_passenger_cabin(self):
        passenger = {"cabin": "C", "ssr": ""}

        from src.passenger_data_generator import (
            Seat as GeneratedSeat,
            seat_compatible_with_passenger,
        )

        self.assertTrue(seat_compatible_with_passenger(GeneratedSeat.from_dict(make_seat("1A", 1, "Business")), passenger))
        self.assertFalse(seat_compatible_with_passenger(GeneratedSeat.from_dict(make_seat("5A", 5, "Economy")), passenger))

    def test_blocked_seat_records_responsible_passenger(self):
        seats = {
            seat_id: final.Seat(make_seat(seat_id, 1, col=seat_id[-1]))
            for seat_id in ("1A", "1B", "1C")
        }
        final._SEAT_NEIGHBORS = {"1A": ["1B"], "1B": ["1A", "1C"], "1C": ["1B"]}
        final._SEAT_ROW_NEIGHBORS = dict(final._SEAT_NEIGHBORS)
        passenger = final.Passenger({
            "hostnum": 2,
            "cabin": "Y",
            "oldSeat": {"seatNum": "1B", "seatValue": ""},
            "mandatoryRule": {"needBothSideEmpty": "Y"},
        })
        context = final.AssignmentContext(seats)

        self.assertTrue(context.assign_passenger(passenger, "1B", group_id=7))
        self.assertEqual(
            final.build_blocked_by(context),
            {"1A": [(7, 2)], "1C": [(7, 2)]},
        )

    def test_double_side_empty_requires_two_real_neighbors(self):
        seats = {
            seat_id: final.Seat(make_seat(seat_id, 1, col=seat_id[-1]))
            for seat_id in ("1A", "1B")
        }
        final._SEAT_NEIGHBORS = {"1A": ["1B"], "1B": ["1A"]}
        final._ACTIVE_CONFIG = {"mandatory_rules": {"require_two_real_neighbors": True}}
        passenger = final.Passenger({
            "hostnum": 1,
            "cabin": "Y",
            "oldSeat": {"seatNum": "1A", "seatValue": ""},
            "mandatoryRule": {"needBothSideEmpty": "Y"},
        })
        self.assertFalse(final.is_seat_feasible(
            seats["1A"], passenger, set(), {}, set(), seats))

    def test_double_side_empty_can_count_cross_aisle_neighbor(self):
        seats = {
            seat_id: final.Seat({
                **make_seat(seat_id, 1, col=seat_id[-1]),
                "isAisle": seat_id in {"1C", "1D"},
            })
            for seat_id in ("1A", "1C", "1D", "1F")
        }
        final._SEAT_NEIGHBORS = {
            "1A": ["1C"], "1C": ["1A"], "1D": ["1F"], "1F": ["1D"]
        }
        final._SEAT_ROW_NEIGHBORS = {
            "1A": ["1C"], "1C": ["1A", "1D"],
            "1D": ["1C", "1F"], "1F": ["1D"],
        }
        final._ACTIVE_CONFIG = {"mandatory_rules": {
            "require_two_real_neighbors": True,
            "both_side_empty_allow_cross_aisle": True,
        }}
        passenger = final.Passenger({
            "hostnum": 1, "cabin": "Y",
            "oldSeat": {"seatNum": "1C", "seatValue": ""},
            "mandatoryRule": {"needBothSideEmpty": "Y"},
        })
        context = final.AssignmentContext(seats)

        self.assertTrue(context.assign_passenger(passenger, "1C", group_id=1))
        self.assertEqual(set(final.build_blocked_by(context)), {"1A", "1D"})

    def test_ssr_matrix_controls_exit_eligibility(self):
        exit_seat = final.Seat({**make_seat("12A", 12), "isExitRow": True})
        seats = {exit_seat.seat_id: exit_seat}
        final._ACTIVE_CONFIG = {
            "ssr_rules": {
                "CHD": {"allow_exit_row": False},
                "EXST": {"allow_exit_row": True},
            }
        }
        child = final.Passenger({
            "hostnum": 1, "cabin": "Y", "ssr": "CHD",
            "oldSeat": {"seatNum": "12A", "seatValue": ""},
        })
        extra = final.Passenger({
            "hostnum": 2, "cabin": "Y", "ssr": "EXST",
            "oldSeat": {"seatNum": "12A", "seatValue": ""},
        })
        self.assertFalse(final.is_seat_feasible(exit_seat, child, set(), {}, set(), seats))
        self.assertTrue(final.is_seat_feasible(exit_seat, extra, set(), {}, set(), seats))

    def test_reserved_precheck_reports_duplicate_before_assignment(self):
        raw_seat = make_seat("1A", 1)
        seats = {"1A": final.Seat(raw_seat)}
        final._SEAT_NEIGHBORS = {"1A": []}
        final._SEAT_ROW_NEIGHBORS = {"1A": []}
        final._SEAT_SUBROW = {"1A": (1, 0)}
        final._ACTIVE_CONFIG = {}
        groups = [
            final.Group({
                "groupId": gid, "groupType": "communityType", "PNR": f"PNR{gid}",
                "psrs": [{
                    "hostnum": 1, "cabin": "Y",
                    "oldSeat": {"seatNum": "1A", "seatValue": ""},
                    "newSeat": {"seatNum": "1A"},
                }],
            })
            for gid in (1, 2)
        ]
        diagnostics, invalid = final.precheck_reserved_seats(groups, seats, {})
        self.assertEqual(diagnostics["counts"]["duplicate_seats"], 1)
        self.assertEqual(invalid, {(1, 1), (2, 1)})

    def test_final_returns_explainable_score_using_old_seatmap(self):
        old_seats = [make_seat("10A", 10)]
        new_seats = [make_seat("1A", 1)]
        groups = [{
            "groupId": 1, "groupType": "communityType", "PNR": "ABC123",
            "psrs": [{
                "hostnum": 1, "cabin": "Y",
                "oldSeat": {"seatNum": "10A", "seatValue": ""},
                "mandatoryRule": {}, "optionRule": [],
            }],
        }]
        config = {
            "weights": {"w_s": -1.0, "w_v": -0.5, "w_p": -0.8, "w_c": -3.0, "w_b": -0.3},
            "algorithm": {"prioritize_front": True, "front_penalty_reduction": 0.1, "back_penalty_factor": 0.2},
            "penalty": {"unassigned": 300, "violation": 500, "time_factor": 0},
            "seat_geometry": {}, "seat_value": {}, "mandatory_rules": {"require_two_real_neighbors": True},
        }
        result, module_score = final.run_allocation(
            new_seats, old_seats, groups, config["weights"], config)
        self.assertEqual(result["assigned_seats"][(1, 1)], "1A")
        self.assertEqual(result["total_score"], module_score)
        self.assertEqual(result["score_detail"]["combined_score"], module_score)
        self.assertLess(result["score_detail"]["score_s"], 0)
        self.assertNotIn("score_class", result["score_detail"])
        self.assertNotIn("score_cabin_match", result["score_detail"])

    def test_reverse_50_edge_rebuilds_protected_fixed_care_group(self):
        config = json.loads(
            FORMAL24_CONFIG.read_text(encoding="utf-8")
        )
        old_seats = json.loads(
            (PROJECT_ROOT / "data" / "3-3seatmap.json").read_text(
                encoding="utf-8"
            )
        )["seats"]
        new_seats = json.loads(
            (PROJECT_ROOT / "data" / "3-4-3seatmap.json").read_text(
                encoding="utf-8"
            )
        )["seats"]
        groups = json.loads(
            (
                FORMAL24_ROOT
                / "reverse"
                / "50_edge_groups.json"
            ).read_text(encoding="utf-8")
        )["groups"]

        result, _ = final.run_allocation(
            new_seats, old_seats, groups, config["weights"], config
        )

        self.assertEqual(result["score_detail"]["violations"], 0)
        self.assertEqual(result["score_detail"]["unassigned_count"], 0)
        # The regenerated case no longer relies on a particular random group
        # number.  Its protected, fixed care group must still be rebuilt as a
        # whole while both fixed passengers retain their specified seats.
        self.assertEqual(result["assigned_seats"][(6, 1)], "19H")
        self.assertEqual(result["assigned_seats"][(6, 2)], "19E")
        validation = result["score_detail"]["soft_score_validation"]
        self.assertTrue(validation["passed"])
        self.assertLessEqual(
            validation["absolute_error"], validation["tolerance"]
        )


class VisualizationTests(unittest.TestCase):
    @staticmethod
    def row(labels, aisle_labels):
        return [
            {"seatId": f"1{label}", "row": 1, "col": label, "isAisle": label in aisle_labels}
            for label in labels
        ]

    def test_aisle_breaks_for_222_layout_do_not_split_middle_pair(self):
        seats = self.row(["A", "C", "D", "G", "H", "K"], {"C", "D", "G", "H"})
        self.assertEqual(visualize.infer_aisle_breaks(seats), [1, 3])

    def test_aisle_breaks_for_33_and_343_layouts(self):
        narrow = self.row(["A", "B", "C", "D", "E", "F"], {"C", "D"})
        wide = self.row(
            ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K"],
            {"C", "D", "H", "J"},
        )
        self.assertEqual(visualize.infer_aisle_breaks(narrow), [2])
        self.assertEqual(visualize.infer_aisle_breaks(wide), [2, 7])

    def test_group_fill_colors_are_stable_and_distinct(self):
        self.assertEqual(visualize.group_fill_color(35), visualize.group_fill_color(35))
        self.assertNotEqual(visualize.group_fill_color(35), visualize.group_fill_color(36))


class BatchDatasetTests(unittest.TestCase):
    size_labels = ("50", "100", "150", "full")
    difficulties = ("normal", "stress", "edge")
    required_ssrs = {"CHD", "WCHR", "BLND", "BSCT", "UM", "EXST", "CBBG"}

    def test_all_24_datasets_have_homogeneous_cabin_groups(self):
        paths = sorted(FORMAL24_ROOT.glob("*/*_groups.json"))
        self.assertEqual(len(paths), 24)
        for direction in ("forward", "reverse"):
            for size_label in self.size_labels:
                for difficulty in self.difficulties:
                    path = (
                        FORMAL24_ROOT / direction
                        / f"{size_label}_{difficulty}_groups.json"
                    )
                    groups = json.loads(
                        path.read_text(encoding="utf-8")
                    )["groups"]
                    for group in groups:
                        cabins = {
                            evaluator.expected_cabin_class(passenger)
                            for passenger in group["psrs"]
                        }
                        self.assertEqual(
                            len(cabins), 1,
                            (path.name, group["groupId"], cabins),
                        )

    def test_batch_datasets_have_required_coverage(self):
        fixed_by_size = {}
        for size_label in self.size_labels:
            fixed_by_size[size_label] = {}
            for difficulty in self.difficulties:
                path = (
                    FORMAL24_ROOT
                    / "forward"
                    / f"{size_label}_{difficulty}_groups.json"
                )
                payload = json.loads(path.read_text(encoding="utf-8"))
                groups = payload["groups"]
                passengers = [p for group in groups for p in group["psrs"]]
                present_ssrs = {p.get("ssr") for p in passengers if p.get("ssr")}
                fixed = sum(bool(p.get("newSeat", {}).get("seatNum")) for p in passengers)

                self.assertEqual(
                    payload.get("travelerCount", len(passengers)),
                    len(passengers),
                )
                self.assertEqual(
                    payload.get("passengerCount"),
                    payload.get("targetSeatDemand"),
                )
                if size_label.isdigit():
                    self.assertEqual(
                        payload.get("passengerCount", len(passengers)),
                        int(size_label),
                    )
                else:
                    self.assertEqual(payload.get("sizeLabel"), "full")
                    self.assertGreaterEqual(
                        payload["requestedPassengerCount"],
                        payload["passengerCapacity"],
                    )
                    self.assertEqual(
                        payload["passengerCount"],
                        payload["passengerCapacity"],
                    )
                self.assertLessEqual(max(len(group["psrs"]) for group in groups), 10)
                self.assertEqual(present_ssrs, self.required_ssrs)
                fixed_by_size[size_label][difficulty] = fixed

        for fixed_counts in fixed_by_size.values():
            self.assertGreater(fixed_counts["normal"], fixed_counts["stress"])
            self.assertGreater(fixed_counts["stress"], fixed_counts["edge"])

    @unittest.skip("requires generated benchmark artifacts intentionally excluded from this repository")
    def test_batch_result_directory_contains_only_named_comparison_images(self):
        result_dir = (
            PROJECT_ROOT / "outputs" / "benchmarks" / "batch_visualizations"
        )
        actual = {path.name for path in result_dir.iterdir() if path.is_file()}
        self.assertTrue(actual)
        for filename in actual:
            self.assertRegex(
                filename,
                r"^(?:\d+|full)_(?:normal|stress|edge)_result\.png$",
            )
            stem = filename.removesuffix("_result.png")
            self.assertTrue(
                (PROJECT_ROOT / "data" / f"{stem}_groups.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
