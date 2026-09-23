import ast
import copy
import unittest
from pathlib import Path

from src import heuristic_seat_allocator as rich
from tests import test_native_rich_repair as repair_tests
from tests.test_native_rich_pipeline import construction_prefix


def python_vnd_prefix(include_group_rebuild=False, include_caregiver_rebuild=False):
    if include_caregiver_rebuild:
        return rich.improve_assignment_with_safe_neighborhoods
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "improve_assignment_with_safe_neighborhoods")
    if include_group_rebuild:
        stop = next(i for i, n in enumerate(function.body) if isinstance(n, ast.FunctionDef)
                    and n.name == "global_baby_score")
    else:
        stop = next(i for i, n in enumerate(function.body) if isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Name) and n.target.id == "keys_by_group")
    function.body = function.body[:stop] + ast.parse("return diagnostics").body
    namespace = dict(rich.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 "frozen_vnd_prefix", "exec"), namespace)
    return namespace[function.name]


class NativeRichVndTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    replay = repair_tests.NativeRichRepairTests.replay
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_public_states_match_python_ordinary_vnd(self):
        prefix = construction_prefix()
        swaps = cycles = rebuilds = 0
        for case in self.cases[:11]:
            passengers = [(g["groupId"], p) for g in case["groupsData"] for p in g["psrs"]]
            seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            config = copy.deepcopy(self.config)
            config["algorithm"].update(business_time_limit_seconds=120.0, adaptive_stage_budgets=False,
                                       construction_time_budget=60.0, small_group_dfs_time_limit=20.0)
            constructed = prefix(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                 case["groupsData"], config["weights"], config)
            context = constructed["context"]
            indices = {(g, p["hostnum"]): i for i, (g, p) in enumerate(passengers)}
            initial = []
            for key, seat in context.assigned_seats.items():
                entry = [indices[key], seat]
                blocks = context.assigned_blocked.get(key, ())
                if len(blocks) == 1:
                    entry.append(next(iter(blocks)))
                initial.append(entry)
            for reverse in (False, True):
                with self.subTest(case=case["id"], reverse=reverse):
                    result = self.replay(case, initial, [seats[::-1] if reverse else seats] * len(passengers), vnd_algorithm={})
                    swaps += result["vnd"]["swaps"]
                    cycles += result["vnd"]["cycles"]
                    rebuilds += result["vnd"]["group_rebuilds"]
        self.assertGreater(swaps, 0)
        self.assertGreater(cycles, 0)
        if isinstance(self, NativeRichVndGroupTests):
            self.assertGreater(rebuilds, 0)

    def test_cap_minimum_epsilon_and_preference_rules(self):
        case = self.synthetic([(301, {}), (302, {}), (303, {})])
        for group in case["groupsData"]:
            for p in group["psrs"]:
                p["optionRule"] = [{"nearToilet": "Y", "weight": 2.0}, {"nearToilet": "N", "weight": 3.0}]
        for epsilon in (1e-9, 1000.0):
            with self.subTest(epsilon=epsilon):
                result = self.replay(case, [[0, "2C"], [1, "2B"], [2, "2A"]],
                            [["1A", "1B", "1C", "2A", "2B", "2C"]] * 3,
                            vnd_algorithm={"local_search_candidate_cap": 1,
                                           "local_search_cycle_candidate_cap": 1,
                                           "local_search_epsilon": epsilon})
                if epsilon < 1.0:
                    self.assertGreater(result["vnd"]["one_opt"], 0)
                else:
                    self.assertEqual(result["vnd"]["accepted_moves"], 0)


class NativeRichVndGroupTests(NativeRichVndTests):
    def replay(self, *args, **kwargs):
        kwargs["vnd_group_rebuild"] = True
        return repair_tests.NativeRichRepairTests.replay(self, *args, **kwargs)


class NativeRichVndCaregiverTests(NativeRichVndGroupTests):
    def test_caregiver_rebuild_accepts_joint_row_exchange(self):
        for ssr in ("BLND", "BSCT"):
            with self.subTest(ssr=ssr):
                case = self.synthetic([(101, {"ssr": ssr}), (101, {}), (202, {}), (202, {}), (303, {})])
                for seatmap in ("oldSeatmapData", "newSeatmapData"):
                    next(s for s in case[seatmap]["seats"] if s["seatId"] == "2A")["hasBassinet"] = True
                desired = ["2A", "2B", "1A", "1B", "4A"]
                passengers = [p for g in case["groupsData"] for p in g["psrs"]]
                for passenger, seat in zip(passengers, desired):
                    passenger["oldSeat"]["seatNum"] = seat
                # Singleton rankings keep ordinary moves at their current positions;
                # the joint permutation must move the caregiver and cared passenger.
                # The fifth passenger contributes a baby interaction outside the pair.
                initial = [[1, "1B"], [0, "1A"], [2, "2A"], [3, "2B"], [4, "4A"]]
                result = self.replay(case, initial, [[s] for s in ["1A", "1B", "2A", "2B", "4A"]], vnd_algorithm={})
                self.assertGreater(result["vnd"]["caregiver_rebuilds"], 0)
                self.assertEqual(result["assignments"], desired)

    def replay(self, *args, **kwargs):
        kwargs["vnd_group_rebuild"] = True
        kwargs["vnd_caregiver_rebuild"] = True
        return repair_tests.NativeRichRepairTests.replay(self, *args, **kwargs)
