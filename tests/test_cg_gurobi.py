"""Gurobi 直接定价列生成的正确性回归。

- 小规模穷举：全局最优 vs 列生成的 LP 上界 U 与列池整数解 I；
- 公开验证集：11 个合法算例均得到完整合法解，列成本与独立评估器一致；
- 与已知参考值对照：旧实现证明过的最优值必须与新 U 一致。
"""
from __future__ import annotations

import itertools
import json
import unittest
from pathlib import Path

try:
    import gurobipy  # noqa: F401
    HAVE_GUROBI = True
except ImportError:  # pragma: no cover
    HAVE_GUROBI = False

from src import aircraft_seatmap_generator as gen
from src import allocation_evaluator as evaluator
from tests.general_validation_case_factory import materialize_cases

ROOT = Path(__file__).resolve().parents[1]


def _config():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    cfg["cg_gurobi"] = {"total_time_limit": 120, "pricing_time_limit": 30,
                        "integer_time_limit": 20, "log": False, "pricing_workers": 2,
                        "warm_start": "greedy"}
    return cfg


def _seatmap(rows, labels, bassinet_rows=(), exit_rows=()):
    cabin = gen.CabinConfig.from_dict({
        "name": "Economy", "startRow": 1, "endRow": rows, "seatLabels": labels,
        "aisleAfter": [labels[len(labels) // 2 - 1]] if len(labels) >= 4 else [labels[-1]],
        "seatClass": "Economy", "bassinetRows": list(bassinet_rows), "exitRows": list(exit_rows),
        "extraLegroomRows": [], "toiletRows": [],
    })
    return gen.generate_aircraft("BF", [cabin], [])


def _pax(i, old, ssr="", cared="N", both="N", single="N", rowflag="N", subflag="N", new=None):
    p = {"hostnum": i, "cabin": "Economy", "ssr": ssr, "needCared": cared,
         "oldSeat": {"seatNum": old, "seatValue": ""},
         "mandatoryRule": {"needBothSideEmpty": both, "needSingleSideEmpty": single,
                           "sameSubRowNoOtherSSR": subflag, "sameRowNoOtherSSR": rowflag},
         "optionRule": []}
    if new:
        p["newSeat"] = {"seatNum": new}
    return p


def _groups(*specs):
    return [{"groupId": gid, "groupType": "communityType", "PNR": f"T-{gid}", "psrs": psrs}
            for gid, psrs in specs]


def _brute_force(sm, groups, cfg):
    seats = [s["seatId"] for s in sm["seats"]]
    keys = [(g["groupId"], p["hostnum"]) for g in groups for p in g["psrs"]]
    best = float("-inf")
    for combo in itertools.permutations(seats, len(keys)):
        a = dict(zip(keys, combo))
        v, u, _ = evaluator.count_hard_constraint_violations(sm["seats"], groups, a, cfg)
        if v or u:
            continue
        s, _ = evaluator.calculate_soft_score(sm["seats"], sm["seats"], groups, a, cfg["weights"], cfg)
        best = max(best, s)
    return best


@unittest.skipUnless(HAVE_GUROBI, "gurobipy 不可用")
class BruteForceParityTests(unittest.TestCase):
    SCENARIOS = {
        "ordinary": (_seatmap(2, ["A", "B", "C"]), _groups(
            (1, [_pax(1, "1A"), _pax(2, "1B"), _pax(3, "2C")]), (2, [_pax(1, "2A"), _pax(2, "1C")]))),
        "protection": (_seatmap(2, ["A", "B", "C", "D"]), _groups(
            (1, [_pax(1, "1A", both="Y"), _pax(2, "2D")]), (2, [_pax(1, "2A", single="Y")]))),
        "caregiver_exit_row": (_seatmap(2, ["A", "B", "C"], exit_rows=(2,)), _groups(
            (1, [_pax(1, "1A", "CHD"), _pax(2, "1B")]), (2, [_pax(1, "2A"), _pax(2, "2B")]))),
        "conditional_ssr": (_seatmap(2, ["A", "B", "C"]), _groups(
            (1, [_pax(1, "1A", "UM", rowflag="Y")]), (2, [_pax(1, "1B", "UM")]),
            (3, [_pax(1, "2C"), _pax(2, "2B")]))),
        "baby_cross_group": (_seatmap(2, ["A", "B", "C"], bassinet_rows=(1,)), _groups(
            (1, [_pax(1, "1A", "BSCT"), _pax(2, "1B")]), (2, [_pax(1, "2A"), _pax(2, "2B")]),
            (3, [_pax(1, "1C")]))),
        "fixed_and_protection": (_seatmap(2, ["A", "B", "C", "D"]), _groups(
            (1, [_pax(1, "1B", both="Y", new="1B"), _pax(2, "2A")]), (2, [_pax(1, "2C"), _pax(2, "2D")]))),
    }

    def test_lp_bound_dominates_and_integer_matches_brute_force(self):
        from src.cg_gurobi import solve
        cfg = _config()
        for name, (sm, groups) in self.SCENARIOS.items():
            with self.subTest(scenario=name):
                best = _brute_force(sm, groups, cfg)
                result = solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
                self.assertEqual("complete", result["status"])
                self.assertEqual((0, 0), (result["hard_violations"], result["unassigned"]))
                self.assertTrue(result["lp_bound_certified"])
                self.assertGreaterEqual(result["lp_bound"], best - 1e-6)
                self.assertLessEqual(result["total_score"], best + 1e-6)
                self.assertAlmostEqual(best, result["total_score"], places=6)


@unittest.skipUnless(HAVE_GUROBI, "gurobipy 不可用")
class PublicFixtureTests(unittest.TestCase):
    # 旧实现（HiGHS 分支定价）证明过的最优值；新实现的 U 必须逐位一致
    LEGACY_PROVEN = {
        "shrink_small_blockers": -61.15,
        "large_groups_9_10": -75.78333333333333,
        "caregiver_and_ssr": -8.198590225563912,
        "full_resource_protection_heavy": -30.8,
    }
    # 旧实现经分支定价证明的整数最优值；新实现须以 proven_optimal 复现
    LEGACY_PROVEN_INTEGER = {
        "full_resource_shrink": -116.79,
        "dense_full_resource_special_chain": -122.025,
    }

    def test_all_valid_cases_complete_and_consistent(self):
        from src.cg_gurobi import solve
        cfg = _config()
        for case in materialize_cases():
            if not case["expectedValid"]:
                continue
            with self.subTest(case=case["id"]):
                result = solve(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                               case["groupsData"], cfg["weights"], cfg)
                self.assertEqual("complete", result["status"])
                self.assertEqual((0, 0), (result["hard_violations"], result["unassigned"]))
                score, _ = evaluator.calculate_soft_score(
                    case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                    case["groupsData"], result["assigned_seats"], cfg["weights"], cfg)
                self.assertAlmostEqual(score, result["total_score"], places=8)
                if result["lp_bound"] is not None:
                    self.assertGreaterEqual(result["lp_bound"], result["total_score"] - 1e-6)
                ref = self.LEGACY_PROVEN.get(case["id"])
                if ref is not None:
                    self.assertTrue(result["lp_bound_certified"])
                    self.assertAlmostEqual(ref, result["lp_bound"], places=6)
                ref_int = self.LEGACY_PROVEN_INTEGER.get(case["id"])
                if ref_int is not None:
                    self.assertTrue(result["proven_optimal"])
                    self.assertAlmostEqual(ref_int, result["total_score"], places=6)

    def test_invalid_cases_are_rejected(self):
        from src.cg_gurobi import solve
        cfg = _config()
        for case in materialize_cases():
            if case["expectedValid"]:
                continue
            with self.subTest(case=case["id"]):
                with self.assertRaises(ValueError):
                    solve(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                          case["groupsData"], cfg["weights"], cfg)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_GUROBI, "gurobipy 不可用")
class AuditRegressionTests(unittest.TestCase):
    """2026-09-24 对抗式审计确认的缺陷回归。"""

    def _cfg(self, **over):
        cfg = _config()
        cfg["cg_gurobi"].update({"warm_start": "greedy", "pricing_workers": 1, **over})
        return cfg

    def test_same_occupancy_permutations_are_branched_not_closed(self):
        """同座位集合的旅客排列在 LP 中分数混合时，分支定价必须继续分支而不是当作整数解。"""
        from src.cg_gurobi import solve
        cfg = self._cfg()
        cab = gen.CabinConfig.from_dict({"name": "Economy", "startRow": 1, "endRow": 2, "seatLabels": ["A", "B", "C"],
                                         "aisleAfter": [], "seatClass": "Economy", "bassinetRows": [], "exitRows": [],
                                         "extraLegroomRows": [], "toiletRows": []})
        sm = gen.generate_aircraft("BF", [cab], [])
        groups = _groups((1, [_pax(1, "1A", "UM", rowflag="Y"), _pax(2, "2A")]),
                         (2, [_pax(1, "1C", "UM", new="1C")]))
        best = _brute_force(sm, groups, cfg)
        r = solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        self.assertEqual("complete", r["status"])
        self.assertEqual((0, 0), (r["hard_violations"], r["unassigned"]))
        self.assertAlmostEqual(best, r["total_score"], places=6)
        self.assertTrue(r["proven_optimal"])

    def test_old_seat_in_other_cabin_is_scored_like_the_evaluator(self):
        """分舱求解必须用整张旧座位图评分。"""
        from src.cg_gurobi import solve
        cfg = self._cfg()
        biz = gen.CabinConfig.from_dict({"name": "Business", "startRow": 1, "endRow": 2, "seatLabels": ["A", "B", "C", "D"],
                                         "aisleAfter": ["B"], "seatClass": "Business", "bassinetRows": [], "exitRows": [],
                                         "extraLegroomRows": [], "toiletRows": []})
        eco = gen.CabinConfig.from_dict({"name": "Economy", "startRow": 3, "endRow": 4, "seatLabels": ["A", "B", "C", "D"],
                                         "aisleAfter": ["B"], "seatClass": "Economy", "bassinetRows": [], "exitRows": [],
                                         "extraLegroomRows": [], "toiletRows": []})
        sm = gen.generate_aircraft("BF", [biz, eco], [])
        p = _pax(1, "4D"); p["cabin"] = "C"
        groups = _groups((1, [p]))
        r = solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        self.assertEqual("complete", r["status"])
        score, _ = evaluator.calculate_soft_score(sm["seats"], sm["seats"], groups, r["assigned_seats"], cfg["weights"], cfg)
        self.assertAlmostEqual(score, r["total_score"], places=8)
        # 单人组：穷举所有商务舱座位即全局最优
        best = max(evaluator.calculate_soft_score(sm["seats"], sm["seats"], groups, {(1, 1): s["seatId"]}, cfg["weights"], cfg)[0]
                   for s in sm["seats"] if s["seatClass"] == "Business")
        self.assertAlmostEqual(best, r["total_score"], places=6)
        self.assertTrue(r["proven_optimal"])

    def test_certified_infeasible_input_is_reported_as_infeasible(self):
        from src.cg_gurobi import solve
        cfg = self._cfg()
        sm = _seatmap(1, ["A", "B", "C", "D"])
        groups = _groups((1, [_pax(1, "1B", both="Y", new="1B")]), (2, [_pax(1, "1C", new="1C")]))
        with self.assertRaises(ValueError):   # 固定座位落在另一固定双侧旅客的保护空座上：输入层拒绝
            solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        # 容量必要条件（§4.10）在输入层拒绝：两名双侧保护旅客需要 6 个资源 > 4 座
        groups = _groups((1, [_pax(1, "1B", both="Y")]), (2, [_pax(1, "1A", both="Y")]))
        with self.assertRaises(ValueError):
            solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        # 容量满足但结构不可行（CHD 需同组成人陪护而组内无人）：Phase I 认证不可行并贯通到顶层
        groups = _groups((1, [_pax(1, "1A", "CHD")]), (2, [_pax(1, "1C")]))
        r = solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        self.assertEqual("infeasible", r["status"])
        self.assertIsNone(r["total_score"])

    def test_non_integer_ids_are_rejected(self):
        from src.cg_gurobi import solve
        cfg = self._cfg()
        sm = _seatmap(1, ["A", "B", "C"])
        groups = _groups(("1", [_pax(1, "1A")]))
        with self.assertRaises(ValueError):
            solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        groups = _groups((1, [_pax("1", "1A")]))
        with self.assertRaises(ValueError):
            solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)

    def test_tiny_budget_still_returns_complete_solution(self):
        from src.cg_gurobi import solve
        cfg = self._cfg(total_time_limit=2.0, integer_time_limit=1.0)
        sm = _seatmap(2, ["A", "B", "C"])
        groups = _groups((1, [_pax(1, "1A"), _pax(2, "1B"), _pax(3, "2C")]), (2, [_pax(1, "2A"), _pax(2, "1C")]))
        r = solve(sm["seats"], sm["seats"], groups, cfg["weights"], cfg)
        self.assertEqual("complete", r["status"])
        self.assertEqual((0, 0), (r["hard_violations"], r["unassigned"]))
