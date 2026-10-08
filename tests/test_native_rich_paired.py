import copy
import unittest

from tests import test_native_rich_repair as repair_tests


class NativeRichPairedTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    replay = repair_tests.NativeRichRepairTests.replay
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_public_paired_stage_matches_python(self):
        for case in self.cases[:11]:
            seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            passengers = [p for g in case["groupsData"] for p in g["psrs"]]
            initial = [[i, p["newSeat"]["seatNum"]] for i, p in enumerate(passengers) if p.get("newSeat")]
            with self.subTest(case=case["id"]):
                self.replay(case, initial, [seats] * len(passengers), paired_algorithm={})

    def test_assigned_and_unassigned_cared_caregiver_combinations(self):
        case = self.synthetic([(101, {"ssr": "BSCT"}), (101, {})])
        seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
        for initial in ([], [[0, "1A"]], [[1, "1B"]], [[0, "1A"], [1, "1B"]], [[0, "1A"], [1, "2B"]]):
            with self.subTest(initial=initial):
                self.replay(case, initial, [seats] * 2, paired_algorithm={})

    def test_fixed_unsatisfied_cared_passenger_is_retained(self):
        case = self.synthetic([(101, {"ssr": "BSCT", "newSeat": {"seatNum": "1A"}}),
                               (101, {"newSeat": {"seatNum": "2B"}})])
        seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
        result = self.replay(case, [[0, "1A"], [1, "2B"]], [seats] * 2, paired_algorithm={})
        self.assertEqual(result["assignments"], ["1A", "2B"])
        self.assertEqual(result["paired_added"], 0)

    def test_partner_seat_is_excluded_from_protection_blocks(self):
        for rule in ("needSingleSideEmpty", "needBothSideEmpty"):
            case = self.synthetic([(101, {"needCared": "Y", "mandatoryRule": {rule: "Y"}}), (101, {})])
            seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            with self.subTest(rule=rule):
                self.replay(case, [], [seats] * 2, paired_algorithm={})

    def test_pair_search_stops_after_500_feasible_cared_seats(self):
        case = self.synthetic([(101, {"needCared": "Y"}), (101, {})])
        prototype = copy.deepcopy(case["newSeatmapData"]["seats"][:6])
        expanded = []
        for row in range(1, 102):
            for source in prototype:
                seat = dict(source, row=row, seatId=f"{row}{source['col']}")
                expanded.append(seat)
        case["newSeatmapData"]["seats"] = expanded
        ranked = [s["seatId"] for s in reversed(expanded)]
        result = self.replay(case, [], [ranked] * 2, paired_algorithm={})
        self.assertEqual(result["paired_added"], 2)
        self.assertIn(result["assignments"][0], ranked[:500])
        self.assertNotEqual(result["assignments"][0], "1A")
