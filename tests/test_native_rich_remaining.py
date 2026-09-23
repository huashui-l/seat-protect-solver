import unittest
from unittest.mock import patch

from tests import test_native_rich_repair as repair_tests


class NativeRichRemainingTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    replay = repair_tests.NativeRichRepairTests.replay

    def test_omitted_candidate_caps_use_python_defaults(self):
        case = self.cases[0]
        seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
        count = sum(len(g["psrs"]) for g in case["groupsData"])
        with patch.dict(self.config["algorithm"], {}, clear=True):
            self.replay(case, [], [seats] * count, construct_algorithm={
                "small_group_dfs_node_limit": 300, "small_group_dfs_time_limit": 20.0})

    def test_retry_caps_and_expired_stage_preserve_python_behavior(self):
        case = next(c for c in self.cases if c["id"] == "shrink_small_blockers")
        seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
        passengers = [p for g in case["groupsData"] for p in g["psrs"]]
        for algorithm in (
            {"candidate_cap": 1, "candidate_cap_retry": 2, "candidate_cap_full_retry": 8},
            {"small_group_dfs_node_limit": 0},
            {"small_group_dfs_time_limit": 0.0},
            {"stage3_time_budget": 0.0},
        ):
            with self.subTest(algorithm=algorithm):
                self.replay(case, [], [seats] * len(passengers), construct_algorithm={
                    "small_group_dfs_node_limit": 300, "small_group_dfs_time_limit": 20.0,
                    **algorithm})

    def test_public_remaining_construction_matches_python(self):
        for case in self.cases[:11]:
            seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            passengers = [p for g in case["groupsData"] for p in g["psrs"]]
            initial = [[i, p["newSeat"]["seatNum"]] for i, p in enumerate(passengers) if p.get("newSeat")]
            for enabled in (True, False):
                with self.subTest(case=case["id"], dfs=enabled):
                    self.replay(case, initial, [seats] * len(passengers), construct_algorithm={
                        "small_group_dfs_enabled": enabled,
                        "small_group_dfs_time_limit": 20.0,
                        "small_group_dfs_node_limit": 300,
                        "candidate_cap": 8, "candidate_cap_retry": 12, "candidate_cap_full_retry": 16,
                        "beam_width_small": 12, "beam_width_medium": 12, "beam_width_large": 12,
                        "beam_moves_small": 8, "beam_moves_medium": 8, "beam_moves_large": 8,
                    })
