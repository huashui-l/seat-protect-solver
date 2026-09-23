import unittest

from tests import test_native_rich_repair as repair_tests


class NativeRichRescueTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    replay = repair_tests.NativeRichRepairTests.replay
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_public_rescue_states_match_python(self):
        for case in self.cases[:11]:
            seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            passengers = [p for g in case["groupsData"] for p in g["psrs"]]
            initial = [[i, p["newSeat"]["seatNum"]] for i, p in enumerate(passengers) if p.get("newSeat")]
            with self.subTest(case=case["id"]):
                self.replay(case, initial, [seats] * len(passengers), rescue_algorithm={})

    def test_fixed_cared_neighbor_displacement(self):
        case = self.synthetic([(401, {"ssr": "BSCT", "newSeat": {"seatNum": "1A"}}),
                               (401, {}), (909, {})])
        result = self.replay(case, [[0, "1A"], [1, "2A"], [2, "1B"]],
                             [["1A"], ["1B"], ["2B"]], rescue_algorithm={})
        self.assertEqual(result["assignments"], ["1A", "1B", "2B"])
        self.assertEqual(result["rescue"]["rescued"], [[401, 1]])

    def test_fixed_neighbor_cannot_be_displaced(self):
        case = self.synthetic([(401, {"ssr": "BSCT", "newSeat": {"seatNum": "1A"}}),
                               (401, {}), (909, {"newSeat": {"seatNum": "1B"}})])
        initial = [[0, "1A"], [1, "2A"], [2, "1B"]]
        result = self.replay(case, initial, [["1A"], ["1B"], ["2B"]], rescue_algorithm={})
        self.assertEqual(result["assignments"], ["1A", "2A", "1B"])
        self.assertEqual(result["rescue"]["unresolved"], [[401, 1]])

    def test_joint_rebuild_permutation_and_node_limit_restore(self):
        case = self.synthetic([(401, {"ssr": "BLND"}), (401, {}), (401, {"ssr": "BSCT"})])
        for limit in (1, 50000):
            with self.subTest(node_limit=limit):
                result = self.replay(case, [[0, "1A"], [1, "1B"]],
                                     [["1C"], ["1B"], ["1A"]], rescue_algorithm={
                                         "paired_rescue_option_cap": 0,
                                         "paired_joint_rebuild_node_limit": limit,
                                     })
                self.assertEqual(result["rescue"]["joint_rebuilds"], int(limit > 1))
                self.assertEqual(result["assignments"], ["1C", "1B", "1A"] if limit > 1 else ["1A", "1B", None])

    def test_maximum_pair_count_precedes_cost_and_one_adult_can_serve_two(self):
        case = self.synthetic([(401, {"ssr": "BSCT"}), (401, {"ssr": "BLND"}), (401, {})])
        result = self.replay(case, [], [["1A"], ["1C"], ["1B"]], rescue_algorithm={})
        self.assertEqual(result["rescue"]["rescued"], [[401, 1], [401, 2]])
        self.assertEqual(result["assignments"], ["1A", "1C", "1B"])
