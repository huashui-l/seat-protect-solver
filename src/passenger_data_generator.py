#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合理的座位保护测试数据生成器 v3.2

设计目标：
1. 生成的数据在语义上自洽，避免互相冲突的硬约束；
2. 先生成 SSR、陪护和保护规则，再构造可行旧座位状态；
3. 需要照顾的旅客必定生成同行成人，并安排在同排相邻座位；
4. needBothSideEmpty / needSingleSideEmpty 不与 caregiver 相邻要求同时出现；
5. 预分配新座位只从目标机型中选择，并在生成阶段验证；
6. 实际旅客及其额外占座/保护留空需求不超过目标机型座位数；
7. 输出格式兼容 allocation_evaluator.py / heuristic_seat_allocator.py。

默认仍使用 3-4-3 旧机型和 3-3 新机型；也可通过
--old-seatmap / --new-seatmap 可传入 aircraft_seatmap_generator.py 生成的任意布局。

运行：
python src/passenger_data_generator.py
python src/passenger_data_generator.py --passengers 150 --scenario normal --seed 40
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import statistics
import string
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent


CABIN_CLASS_MAP = {
    "F": "First",
    "A": "First",
    "C": "Business",
    "J": "Business",
    "W": "PremiumEconomy",
    "P": "PremiumEconomy",
    "Y": "Economy",
    "E": "Economy",
}
CABIN_CODE_BY_CLASS = {
    "First": "F",
    "Business": "C",
    "PremiumEconomy": "W",
    "Economy": "Y",
}

# 规则矩阵：只生成明确不冲突的组合。
# 注意：UM 是否需要成人陪同在业务上可能存在不同定义；
# 当前业务配置中 UM 不要求成人陪同，因此设 needCared=N。
SSR_PROFILES = {
    "CHD": {
        "allow_exit_row": False,
        "needCared": "Y",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "N",
        "requires_aisle": False,
        "requires_bassinet": False,
    },
    "WCHR": {
        "allow_exit_row": False,
        "needCared": "Y",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "N",
        "requires_aisle": True,
        "requires_bassinet": False,
    },
    "BLND": {
        "allow_exit_row": False,
        "needCared": "Y",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "N",
        "requires_aisle": False,
        "requires_bassinet": False,
    },
    "BSCT": {
        "allow_exit_row": False,
        "needCared": "Y",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "N",
        "requires_aisle": False,
        "requires_bassinet": True,
    },
    "UM": {
        "allow_exit_row": False,
        "needCared": "N",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "N",
        "requires_aisle": False,
        "requires_bassinet": False,
    },
    "EXST": {
        "allow_exit_row": True,
        "needCared": "N",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "Y",
        "requires_aisle": False,
        "requires_bassinet": False,
    },
    "CBBG": {
        "allow_exit_row": True,
        "needCared": "N",
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "Y",
        "requires_aisle": False,
        "requires_bassinet": False,
    },
}

SCENARIOS = {
    "normal": {
        "ssr_ratio": 0.07,
        "preassign_ratio": 0.20,
        "option_rule_ratio": 0.25,
        "same_subrow_rule_ratio": 0.10,
        "same_row_rule_ratio": 0.05,
        "group_size_weights": {
            1: 0.40, 2: 0.34, 3: 0.13, 4: 0.08, 5: 0.03,
            6: 0.015, 7: 0.003, 8: 0.001, 9: 0.0005, 10: 0.0005,
        },
        "both_side_empty_ratio": 0.01,
        "minimum_both_side_empty": 1,
        "ssr_weights": {
            "CHD": 0.28, "WCHR": 0.18, "BLND": 0.10, "BSCT": 0.10,
            "UM": 0.12, "EXST": 0.12, "CBBG": 0.10,
        },
        "synthetic_old_assignment_profile": {
            "compact": 0.35, "same_row": 0.25,
            "nearby": 0.25, "scattered": 0.15,
        },
    },
    "stress": {
        "ssr_ratio": 0.15,
        "preassign_ratio": 0.10,
        "option_rule_ratio": 0.40,
        "same_subrow_rule_ratio": 0.25,
        "same_row_rule_ratio": 0.15,
        "group_size_weights": {
            1: 0.23, 2: 0.25, 3: 0.17, 4: 0.13, 5: 0.08,
            6: 0.05, 7: 0.035, 8: 0.025, 9: 0.02, 10: 0.01,
        },
        "both_side_empty_ratio": 0.02,
        "minimum_both_side_empty": 2,
        "ssr_weights": {
            "CHD": 0.25, "WCHR": 0.20, "BLND": 0.10, "BSCT": 0.12,
            "UM": 0.08, "EXST": 0.13, "CBBG": 0.12,
        },
        "synthetic_old_assignment_profile": {
            "compact": 0.25, "same_row": 0.25,
            "nearby": 0.25, "scattered": 0.25,
        },
    },
    "edge": {
        "ssr_ratio": 0.22,
        "preassign_ratio": 0.05,
        "option_rule_ratio": 0.50,
        "same_subrow_rule_ratio": 0.35,
        "same_row_rule_ratio": 0.25,
        "group_size_weights": {
            1: 0.13, 2: 0.18, 3: 0.16, 4: 0.14, 5: 0.11,
            6: 0.09, 7: 0.065, 8: 0.055, 9: 0.04, 10: 0.03,
        },
        "both_side_empty_ratio": 0.03,
        "minimum_both_side_empty": 3,
        "ssr_weights": {
            "CHD": 0.20, "WCHR": 0.20, "BLND": 0.10, "BSCT": 0.18,
            "UM": 0.07, "EXST": 0.13, "CBBG": 0.12,
        },
        "synthetic_old_assignment_profile": {
            "compact": 0.15, "same_row": 0.20,
            "nearby": 0.25, "scattered": 0.40,
        },
    },
}

GENERATOR_VERSION = "generator-v3.2"
OLD_ASSIGNMENT_RESTARTS = 20
GROUP_PLACEMENT_ATTEMPTS = 120
SPECIAL_ROLE_RESAMPLE_ATTEMPTS = 20
SPECIAL_PATTERN_SAMPLES = 96
SPECIAL_PATTERN_LIMIT = 64
SPECIAL_WITNESS_NODE_LIMIT = 50000


@dataclass(frozen=True)
class Seat:
    seat_id: str
    row: int
    col: str
    is_window: bool
    is_aisle: bool
    is_exit: bool
    has_bassinet: bool
    seat_class: str
    near_toilet: bool

    @classmethod
    def from_dict(cls, d: dict) -> "Seat":
        return cls(
            seat_id=d["seatId"],
            row=int(d["row"]),
            col=d["col"],
            is_window=bool(d.get("isWindow", False)),
            is_aisle=bool(d.get("isAisle", False)),
            is_exit=bool(d.get("isExitRow", False)),
            has_bassinet=bool(d.get("hasBassinet", False)),
            seat_class=d.get("seatClass", "Economy"),
            near_toilet=bool(d.get("nearToilet", d.get("isNearToilet", False))),
        )


class SeatTopology:
    def __init__(self, seat_dicts: Sequence[dict]):
        if not seat_dicts:
            raise ValueError("座位图 seats 不能为空")
        seat_ids = [str(d.get("seatId", "")) for d in seat_dicts]
        if any(not seat_id for seat_id in seat_ids):
            raise ValueError("座位图存在缺少 seatId 的座位")
        duplicates = sorted(
            seat_id for seat_id, count in Counter(seat_ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(f"座位图存在重复 seatId: {duplicates[:10]}")

        self.seats: Dict[str, Seat] = {d["seatId"]: Seat.from_dict(d) for d in seat_dicts}
        self.rows: Dict[int, List[Seat]] = defaultdict(list)
        for seat in self.seats.values():
            self.rows[seat.row].append(seat)
        for row in self.rows:
            self.rows[row].sort(key=lambda s: seat_label_number(s.col))
            labels = [seat.col for seat in self.rows[row]]
            if len(labels) != len(set(labels)):
                raise ValueError(f"第 {row} 排存在重复座位列")

        self.subrow_id: Dict[str, Tuple[int, int]] = {}
        self.row_index: Dict[str, int] = {}
        for row, seats in self.rows.items():
            sid = 0
            for i, seat in enumerate(seats):
                self.subrow_id[seat.seat_id] = (row, sid)
                self.row_index[seat.seat_id] = i
                if i + 1 < len(seats) and seat.is_aisle and seats[i + 1].is_aisle:
                    sid += 1

    def adjacent(self, seat_id: str, allow_cross_aisle: bool = False) -> List[str]:
        seat = self.seats[seat_id]
        row_seats = self.rows[seat.row]
        idx = self.row_index[seat_id]
        result = []
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(row_seats):
                other = row_seats[j]
                # 陪护默认不跨过道；双侧留空可显式启用排内跨过道邻接。
                if allow_cross_aisle or not (seat.is_aisle and other.is_aisle):
                    result.append(other.seat_id)
        return result

    def seat_value(self, seat_id: str) -> str:
        s = self.seats[seat_id]
        if s.is_exit:
            return "E"
        if s.is_window:
            return "W"
        if s.is_aisle:
            return "A"
        return ""


def seat_label_number(label: str) -> int:
    """把 A..Z、AA..ZZ 转成稳定序号，避免布局被单字符列号限制。"""
    text = str(label).strip().upper()
    if not text or any(not ("A" <= char <= "Z") for char in text):
        raise ValueError(f"无效座位列号: {label!r}")
    value = 0
    for char in text:
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def weighted_choice(rng: random.Random, weights: Dict):
    items = list(weights)
    vals = [weights[x] for x in items]
    return rng.choices(items, weights=vals, k=1)[0]


def random_pnr(rng: random.Random) -> str:
    return "".join(rng.choices(string.ascii_uppercase + string.digits, k=6))


def default_mandatory() -> dict:
    return {
        "needBothSideEmpty": "N",
        "needSingleSideEmpty": "N",
        "sameSubRowNoOtherSSR": "N",
        "sameRowNoOtherSSR": "N",
    }


def generate_option_rules(rng: random.Random, probability: float) -> List[dict]:
    if rng.random() >= probability:
        return []
    rules = []
    if rng.random() < 0.55:
        rules.append({"nearToilet": rng.choice(["Y", "N"]), "weight": f"{rng.uniform(0.2, 1.0):.1f}"})
    if rng.random() < 0.75:
        rules.append({"优先属性一致": "Y", "weight": f"{rng.uniform(0.4, 1.0):.1f}"})
    return rules


def sample_group_sizes(rng: random.Random, total: int, weights: Dict[int, float]) -> List[int]:
    sizes: List[int] = []
    remaining = total
    while remaining > 0:
        allowed = {k: v for k, v in weights.items() if k <= remaining}
        size = weighted_choice(rng, allowed)
        sizes.append(size)
        remaining -= size
    return sizes


def compact_blocks(topology: SeatTopology, size: int, free: Set[str]) -> List[List[str]]:
    """生成紧凑旧座位块：优先同排，再允许连续两排。"""
    blocks: List[List[str]] = []

    # 同排：使用按列排序后的滑动窗口，允许跨过道，但整体连续。
    for seats in topology.rows.values():
        ids = [s.seat_id for s in seats]
        for i in range(0, len(ids) - size + 1):
            block = ids[i:i + size]
            if all(x in free for x in block):
                blocks.append(block)

    if blocks or size <= 3:
        return blocks

    # 大组：在相邻两排中选择尽量均衡的两块。
    rows = sorted(topology.rows)
    for r1, r2 in zip(rows, rows[1:]):
        ids1 = [s.seat_id for s in topology.rows[r1] if s.seat_id in free]
        ids2 = [s.seat_id for s in topology.rows[r2] if s.seat_id in free]
        for n1 in range(max(1, size - len(ids2)), min(len(ids1), size - 1) + 1):
            n2 = size - n1
            if n2 <= 0 or n2 > len(ids2):
                continue
            for i in range(len(ids1) - n1 + 1):
                for j in range(len(ids2) - n2 + 1):
                    blocks.append(ids1[i:i+n1] + ids2[j:j+n2])
    return blocks


def make_passenger(
    hostnum: int,
    seat_id: str,
    topology: SeatTopology,
    rng: random.Random,
    option_rule_ratio: float,
) -> dict:
    seat = topology.seats[seat_id]
    passenger = {
        "hostnum": hostnum,
        "cabin": CABIN_CODE_BY_CLASS.get(seat.seat_class, seat.seat_class),
        "oldSeat": {
            "seatNum": seat_id,
            "seatValue": topology.seat_value(seat_id),
        },
        "needCared": "N",
        "ssr": "",
        "mandatoryRule": default_mandatory(),
        "optionRule": generate_option_rules(rng, option_rule_ratio),
    }
    # 厕所偏好默认反映旅客原座位属性，避免完全随机制造偏好。
    for rule in passenger["optionRule"]:
        if "nearToilet" in rule:
            rule["nearToilet"] = "Y" if seat.near_toilet else "N"
    return passenger


def choose_ssr_candidate(
    group: dict,
    topology: SeatTopology,
    ssr: str,
    used_row_ssr: Dict[int, Set[str]],
    used_subrow_ssr: Dict[Tuple[int, int], Set[str]],
    target_topology: Optional[SeatTopology] = None,
    cabin_slack: Optional[Counter] = None,
) -> Optional[int]:
    profile = SSR_PROFILES[ssr]
    target_topology = target_topology or topology
    if profile.get("needCared") == "Y" and any(
        passenger.get("needCared") == "Y"
        for passenger in group.get("psrs", [])
    ):
        # 随机测试数据避免在同一同行组中叠加多个独立陪护关系；这种
        # 极少见结构应由专项数据验证，不能无意中主导通用规模实验。
        return None
    for idx, p in enumerate(group["psrs"]):
        if p.get("ssr"):
            continue
        candidate_seat_id = p["oldSeat"]["seatNum"]
        # 已经承担相邻照顾职责的普通旅客不能再被改成 SSR。
        is_existing_caregiver = any(
            q.get("needCared") == "Y"
            and q.get("ssr")
            and candidate_seat_id in topology.adjacent(q["oldSeat"]["seatNum"])
            for q in group["psrs"]
        )
        if is_existing_caregiver:
            continue
        seat = topology.seats[candidate_seat_id]
        if not seat_compatible_with_ssr(seat, ssr):
            continue
        cabin_class = cabin_class_for_passenger(p)
        extra_demand = (
            2 if profile.get("needBothSideEmpty") == "Y"
            else 1 if profile.get("needSingleSideEmpty") == "Y"
            else 0
        )
        if cabin_slack is not None and cabin_slack[cabin_class] < extra_demand:
            continue
        target_candidates = [
            candidate for candidate in target_topology.seats.values()
            if candidate.seat_class == cabin_class
            and seat_compatible_with_ssr(candidate, ssr)
        ]
        if profile.get("needSingleSideEmpty") == "Y":
            target_candidates = [
                candidate for candidate in target_candidates
                if target_topology.adjacent(candidate.seat_id)
            ]
            if not topology.adjacent(seat.seat_id):
                continue
        if profile.get("needBothSideEmpty") == "Y":
            allow_cross = bool(
                profile.get("both_side_empty_allow_cross_aisle", True)
            )
            target_candidates = [
                candidate for candidate in target_candidates
                if len(target_topology.adjacent(
                    candidate.seat_id, allow_cross_aisle=allow_cross
                )) == 2
            ]
            if len(topology.adjacent(
                seat.seat_id, allow_cross_aisle=allow_cross
            )) != 2:
                continue
        if not target_candidates:
            continue
        if profile["needCared"] == "Y":
            # 必须有同组普通成人占据同排、同子排、物理相邻座位。
            adjacent = set(topology.adjacent(seat.seat_id))
            has_caregiver = any(
                j != idx
                and q.get("ssr", "") == ""
                and q["oldSeat"]["seatNum"] in adjacent
                for j, q in enumerate(group["psrs"])
            )
            if not has_caregiver:
                continue
        row = seat.row
        subrow = topology.subrow_id[seat.seat_id]
        if ssr in used_row_ssr[row] or ssr in used_subrow_ssr[subrow]:
            continue
        return idx
    return None


def assign_ssrs(
    groups: List[dict],
    topology: SeatTopology,
    rng: random.Random,
    cfg: dict,
    target_special: int,
    target_topology: Optional[SeatTopology] = None,
) -> int:
    used_row_ssr: Dict[int, Set[str]] = defaultdict(set)
    used_subrow_ssr: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
    target = target_topology or topology
    cabin_slack = cabin_seat_capacity(target)
    cabin_slack.subtract(cabin_seat_demand(groups))
    assigned = 0

    group_order = list(range(len(groups)))
    rng.shuffle(group_order)

    def try_assign(ssr: str) -> bool:
        nonlocal assigned
        candidates = group_order[:]
        rng.shuffle(candidates)
        for group_index in candidates:
            g = groups[group_index]
            idx = choose_ssr_candidate(
                g, topology, ssr, used_row_ssr, used_subrow_ssr,
                target_topology, cabin_slack,
            )
            if idx is None:
                continue
            p = g["psrs"][idx]
            if p.get("ssr"):
                continue

            profile = SSR_PROFILES[ssr]
            p["ssr"] = ssr
            p["needCared"] = profile["needCared"]
            p["mandatoryRule"]["needBothSideEmpty"] = profile["needBothSideEmpty"]
            p["mandatoryRule"]["needSingleSideEmpty"] = profile["needSingleSideEmpty"]
            cabin_class = cabin_class_for_passenger(p)
            if profile["needBothSideEmpty"] == "Y":
                cabin_slack[cabin_class] -= 2
            elif profile["needSingleSideEmpty"] == "Y":
                cabin_slack[cabin_class] -= 1
            p["mandatoryRule"]["sameSubRowNoOtherSSR"] = (
                "Y" if rng.random() < cfg["same_subrow_rule_ratio"] else "N"
            )
            p["mandatoryRule"]["sameRowNoOtherSSR"] = (
                "Y" if rng.random() < cfg["same_row_rule_ratio"] else "N"
            )

            seat = topology.seats[p["oldSeat"]["seatNum"]]
            used_row_ssr[seat.row].add(ssr)
            used_subrow_ssr[topology.subrow_id[seat.seat_id]].add(ssr)
            assigned += 1
            return True
        return False

    # 先放置受限程度高的类型，保证每类 SSR 至少出现一次。
    required_ssrs = ["BSCT", "WCHR", "CHD", "BLND", "EXST", "CBBG", "UM"]
    for ssr in required_ssrs:
        if not try_assign(ssr):
            raise RuntimeError(f"无法为测试数据放置必需 SSR 类型: {ssr}")

    target_special = max(target_special, len(required_ssrs))
    attempts = 0
    max_attempts = max(1000, target_special * 100)
    while assigned < target_special and attempts < max_attempts:
        attempts += 1
        ssr = weighted_choice(rng, cfg["ssr_weights"])
        try_assign(ssr)

    # 随机隔离标志不能要求目标机型提供不存在的 SSR 几何资源。例如窄体
    # 目标机型的经济舱摇篮位可能全部位于同一排；若生成两个经济舱 BSCT，
    # 其中任意一人再要求“同排不得有其他 BSCT”，数据便会结构性不可行。
    # 当同类同舱旅客数超过兼容座位的排/子排数量时，统一关闭相应的随机
    # 附加隔离标志；SSR 核心座位属性与陪护规则仍然保留。
    passengers_by_domain: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for group in groups:
        for passenger in group["psrs"]:
            ssr = passenger.get("ssr", "")
            if ssr:
                passengers_by_domain[(
                    ssr, cabin_class_for_passenger(passenger)
                )].append(passenger)
    row_isolation_globally_unsafe = False
    subrow_isolation_globally_unsafe = False
    for (ssr, cabin_class), passengers in passengers_by_domain.items():
        compatible = [
            seat for seat in target.seats.values()
            if seat.seat_class == cabin_class
            and seat_compatible_with_ssr(seat, ssr)
        ]
        compatible_rows = {seat.row for seat in compatible}
        compatible_subrows = {
            target.subrow_id[seat.seat_id] for seat in compatible
        }
        if len(passengers) > len(compatible_rows):
            row_isolation_globally_unsafe = True
            for passenger in passengers:
                passenger["mandatoryRule"]["sameRowNoOtherSSR"] = "N"
        if len(passengers) > len(compatible_subrows):
            subrow_isolation_globally_unsafe = True
            for passenger in passengers:
                passenger["mandatoryRule"]["sameSubRowNoOtherSSR"] = "N"
    # 条件隔离的业务语义是：任一标志会激活所在位置，并令该位置内
    # 每一种 SSR 都至多一人。因此某类 SSR 被迫复用兼容排/子排时，
    # 其他类型的随机标志也可能在该位置意外激活矛盾。生成数据采用
    # 保守策略，统一关闭该层随机隔离；这不影响 SSR 核心限制。
    if row_isolation_globally_unsafe or subrow_isolation_globally_unsafe:
        for group in groups:
            for passenger in group["psrs"]:
                if row_isolation_globally_unsafe:
                    passenger["mandatoryRule"]["sameRowNoOtherSSR"] = "N"
                if subrow_isolation_globally_unsafe:
                    passenger["mandatoryRule"]["sameSubRowNoOtherSSR"] = "N"

    return assigned


def _is_caregiver_for_group(
    passenger: dict,
    group: dict,
    topology: SeatTopology,
) -> bool:
    """判断普通旅客是否承担同组需照顾旅客的相邻陪护职责。"""
    old_id = passenger["oldSeat"]["seatNum"]
    return any(
        cared is not passenger
        and cared.get("needCared") == "Y"
        and cared.get("ssr")
        and old_id in topology.adjacent(cared["oldSeat"]["seatNum"])
        for cared in group["psrs"]
    )


def protected_seat_modes(
    passenger: dict,
    group: dict,
    source_topology: SeatTopology,
    target_topology: SeatTopology,
    cfg: dict,
) -> Tuple[int, ...]:
    """
    返回旅客可安全生成的保护座位数（0、1 或 2）。

    单侧留空只认同一子排的物理邻座；双侧留空可按配置跨过道。
    源、目标机型必须同时存在满足舱等与 SSR 基础限制的位置。
    """
    mandatory = passenger.get("mandatoryRule", {})
    if (
        passenger.get("ssr")
        or passenger.get("needCared") == "Y"
        or mandatory.get("needBothSideEmpty", "N") == "Y"
        or mandatory.get("needSingleSideEmpty", "N") == "Y"
        or _is_caregiver_for_group(passenger, group, source_topology)
    ):
        return ()

    old_id = passenger["oldSeat"]["seatNum"]
    source_has_single = bool(source_topology.adjacent(old_id))
    target_has_single = any(
        seat_compatible_with_passenger(seat, passenger)
        and bool(target_topology.adjacent(seat.seat_id))
        for seat in target_topology.seats.values()
    )
    modes: List[int] = []
    if source_has_single and target_has_single:
        modes.append(1)

    allow_cross = bool(cfg.get("both_side_empty_allow_cross_aisle", True))
    source_has_double = (
        len(source_topology.adjacent(old_id, allow_cross_aisle=allow_cross)) == 2
    )
    target_has_double = any(
        seat_compatible_with_passenger(seat, passenger)
        and len(
            target_topology.adjacent(
                seat.seat_id, allow_cross_aisle=allow_cross
            )
        )
        == 2
        for seat in target_topology.seats.values()
    )
    if source_has_double and target_has_double:
        modes.append(2)
    return tuple(modes)


def assign_both_side_empty_rules(
    groups: List[dict],
    topology: SeatTopology,
    rng: random.Random,
    cfg: dict,
    target_topology: Optional[SeatTopology] = None,
) -> int:
    """为普通旅客生成双侧留空规则，并保证新旧机型都具备物理可行位置。"""
    passengers = [p for group in groups for p in group["psrs"]]
    target = max(
        int(cfg.get("minimum_both_side_empty", 1)),
        round(len(passengers) * float(cfg.get("both_side_empty_ratio", 0.0))),
    )
    if target <= 0:
        return 0

    target_topology = target_topology or topology
    cabin_slack = Counter(
        seat.seat_class for seat in target_topology.seats.values()
    )
    cabin_slack.subtract(cabin_seat_demand(groups))
    candidates: List[Tuple[dict, str]] = []
    for group in groups:
        for passenger in group["psrs"]:
            cabin_class = cabin_class_for_passenger(passenger)
            if (
                2 in protected_seat_modes(
                    passenger, group, topology, target_topology, cfg
                )
                and cabin_slack[cabin_class] >= 2
            ):
                candidates.append((passenger, cabin_class))

    rng.shuffle(candidates)
    assigned = 0
    for passenger, cabin_class in candidates:
        if assigned >= target:
            break
        if cabin_slack[cabin_class] < 2:
            continue
        passenger["mandatoryRule"]["needBothSideEmpty"] = "Y"
        passenger["mandatoryRule"]["needSingleSideEmpty"] = "N"
        cabin_slack[cabin_class] -= 2
        assigned += 1
    if assigned < target:
        raise RuntimeError(
            f"舱位容量内可生成双侧留空规则的旅客不足："
            f"需要 {target}，仅生成 {assigned}"
        )
    return target


def add_protected_seat_demand(
    groups: List[dict],
    topology: SeatTopology,
    target_topology: SeatTopology,
    additional_seats: int,
    rng: random.Random,
    cfg: dict,
) -> bool:
    """增加单侧/双侧留空规则，使计入人数的额外占座恰好达到指定值。"""
    remaining = int(additional_seats)
    if remaining < 0:
        return False
    if remaining == 0:
        return True

    target_capacity = Counter(
        seat.seat_class for seat in target_topology.seats.values()
    )
    current_demand = cabin_seat_demand(groups)
    cabin_names = tuple(sorted(target_capacity))
    cabin_index = {name: index for index, name in enumerate(cabin_names)}
    slack = tuple(
        target_capacity[name] - current_demand[name]
        for name in cabin_names
    )
    if any(value < 0 for value in slack):
        return False

    candidates: List[Tuple[dict, Tuple[int, ...], int]] = []
    for group in groups:
        for passenger in group["psrs"]:
            modes = protected_seat_modes(
                passenger, group, topology, target_topology, cfg
            )
            cabin_class = cabin_class_for_passenger(passenger)
            index = cabin_index.get(cabin_class)
            if modes and index is not None and slack[index] > 0:
                candidates.append((passenger, modes, index))

    rng.shuffle(candidates)
    # 用小型多维计数DP选择保护规则，保证总缺口和每个舱位的剩余容量
    # 同时满足。每名旅客只能选择0、1或2个保护座位。
    zero_usage = (0,) * len(cabin_names)
    states: Dict[Tuple[int, Tuple[int, ...]], Tuple[Tuple[int, ...], ...]] = {
        (0, zero_usage): ()
    }
    for candidate_index, (_, modes, class_index) in enumerate(candidates):
        updated = dict(states)
        for (used_total, usage), choices in states.items():
            for amount in modes:
                next_total = used_total + amount
                if next_total > remaining:
                    continue
                next_usage = list(usage)
                next_usage[class_index] += amount
                if next_usage[class_index] > slack[class_index]:
                    continue
                key = (next_total, tuple(next_usage))
                updated.setdefault(
                    key, choices + ((candidate_index, amount),)
                )
        states = updated

    solution = next(
        (
            choices for (used_total, _), choices in states.items()
            if used_total == remaining
        ),
        None,
    )
    if solution is None:
        return False
    for candidate_index, amount in solution:
        passenger = candidates[candidate_index][0]
        if amount == 2:
            passenger["mandatoryRule"]["needBothSideEmpty"] = "Y"
            passenger["mandatoryRule"]["needSingleSideEmpty"] = "N"
        else:
            passenger["mandatoryRule"]["needSingleSideEmpty"] = "Y"
            passenger["mandatoryRule"]["needBothSideEmpty"] = "N"
    return True


def group_type_for(psrs: List[dict]) -> str:
    if any(p.get("needCared") == "Y" for p in psrs):
        return "careType"
    if len(psrs) == 2:
        return "coupleType"
    return "communityType"


def passenger_count(groups: Sequence[dict]) -> int:
    """返回实际生成的旅客记录数；所有占座 SSR 旅客均包含在内。"""
    return sum(len(group.get("psrs", [])) for group in groups)


def cabin_class_for_passenger(passenger: dict) -> str:
    """Return the immutable seat class declared by the passenger record."""
    raw = str(passenger.get("cabin", "")).strip()
    return CABIN_CLASS_MAP.get(raw.upper(), raw)


def cabin_seat_capacity(topology: SeatTopology) -> Counter:
    return Counter(seat.seat_class for seat in topology.seats.values())


def transferable_seat_capacity(
    source_topology: SeatTopology,
    target_topology: SeatTopology,
) -> int:
    """Total demand possible without moving any passenger between cabins."""
    source = cabin_seat_capacity(source_topology)
    target = cabin_seat_capacity(target_topology)
    return sum(min(source[name], target[name]) for name in source.keys() | target.keys())


def cabin_seat_demand(groups: Sequence[dict]) -> Counter:
    """Count traveler and protected-seat demand separately for every cabin."""
    demand: Counter = Counter()
    for group in groups:
        for passenger in group.get("psrs", []):
            mandatory = passenger.get("mandatoryRule", {}) or {}
            amount = 1
            if mandatory.get("needBothSideEmpty", "N") == "Y":
                amount += 2
            if mandatory.get("needSingleSideEmpty", "N") == "Y":
                amount += 1
            demand[cabin_class_for_passenger(passenger)] += amount
    return demand


def test_case_size_label(
    requested_passengers: int,
    actual_count: int,
    passenger_capacity: int,
) -> str:
    """
    返回测试用例文件名中的规模字段。

    未达到机型容量时使用实际生成人数；请求人数达到或超过容量时使用
    ``full``。实际人数始终另外写入 ``passengerCount``。
    """
    requested = int(requested_passengers)
    actual = int(actual_count)
    capacity = int(passenger_capacity)
    if requested <= 0 or actual <= 0 or capacity <= 0:
        raise ValueError("requested, actual and capacity must be positive")
    if actual > capacity:
        raise ValueError("actual passenger count cannot exceed capacity")
    return "full" if requested >= capacity else str(actual)


def protected_seat_count(groups: Sequence[dict]) -> int:
    """返回旅客之外必须独占的单侧/双侧保护座位数。"""
    count = 0
    for group in groups:
        for passenger in group.get("psrs", []):
            mandatory = passenger.get("mandatoryRule", {}) or {}
            if mandatory.get("needBothSideEmpty", "N") == "Y":
                count += 2
            if mandatory.get("needSingleSideEmpty", "N") == "Y":
                count += 1
    return count


def target_seat_demand(groups: Sequence[dict]) -> int:
    """计算目标机型总座位需求：旅客座位加不可共享的保护座位。"""
    return passenger_count(groups) + protected_seat_count(groups)


def assign_group_cabins(
    sizes: Sequence[int],
    cabin_capacity: Counter,
    rng: random.Random,
) -> List[str]:
    """Assign each whole group to one cabin without exceeding cabin capacity."""
    cabins = [
        cabin for cabin, capacity in sorted(
            cabin_capacity.items(), key=lambda item: (item[1], item[0])
        )
        if capacity > 0
    ]
    if not cabins or sum(sizes) > sum(cabin_capacity.values()):
        raise RuntimeError("同行组人数超过可用分舱容量")

    unassigned = set(range(len(sizes)))
    assignment: List[Optional[str]] = [None] * len(sizes)
    remaining_capacity = Counter(cabin_capacity)
    for cabin_index, cabin in enumerate(cabins[:-1]):
        future_cabins = cabins[cabin_index + 1:]
        remaining_total = sum(sizes[index] for index in unassigned)
        other_capacity = sum(
            remaining_capacity[name]
            for name in future_cabins
        )
        lower = max(0, remaining_total - other_capacity)
        upper = min(remaining_capacity[cabin], remaining_total)
        desired = round(
            remaining_total
            * remaining_capacity[cabin]
            / max(
                1,
                remaining_capacity[cabin]
                + sum(remaining_capacity[name] for name in future_cabins),
            )
        )

        reachable: Dict[int, Tuple[int, ...]] = {0: ()}
        indexes = list(unassigned)
        rng.shuffle(indexes)
        for index in indexes:
            size = int(sizes[index])
            for used, chosen in sorted(
                list(reachable.items()), reverse=True
            ):
                new_used = used + size
                if new_used <= upper and new_used not in reachable:
                    reachable[new_used] = (*chosen, index)
        feasible = [used for used in reachable if lower <= used <= upper]
        if not feasible:
            raise RuntimeError(
                f"无法将完整同行组装入{cabin}舱容量{upper}"
            )
        best_distance = min(abs(used - desired) for used in feasible)
        best_values = [
            used for used in feasible if abs(used - desired) == best_distance
        ]
        selected_used = rng.choice(best_values)
        for index in reachable[selected_used]:
            assignment[index] = cabin
            unassigned.remove(index)
        remaining_capacity[cabin] -= selected_used

    last_cabin = cabins[-1]
    last_demand = sum(sizes[index] for index in unassigned)
    if last_demand > remaining_capacity[last_cabin]:
        raise RuntimeError(
            f"完整同行组使{last_cabin}舱需求{last_demand}超过容量"
        )
    for index in unassigned:
        assignment[index] = last_cabin
    if any(cabin is None for cabin in assignment):
        raise AssertionError("group cabin assignment incomplete")
    return [str(cabin) for cabin in assignment]


@dataclass
class OldAssignmentState:
    """Explicit source-seat state used only by the constructive sampler."""

    occupied_old: Set[str]
    protected_empty_old: Set[str]
    free_old: Set[str]

    @classmethod
    def empty(cls, topology: SeatTopology) -> "OldAssignmentState":
        return cls(set(), set(), set(topology.seats))

    def commit(self, occupied: Set[str], protected: Set[str]) -> None:
        if occupied & protected:
            raise AssertionError("occupied/protected source resources overlap")
        claimed = occupied | protected
        if not claimed <= self.free_old:
            raise AssertionError("source resource was claimed more than once")
        self.occupied_old.update(occupied)
        self.protected_empty_old.update(protected)
        self.free_old.difference_update(claimed)


@dataclass(frozen=True)
class SpecialPattern:
    group_id: int
    assignments: Tuple[Tuple[int, str], ...]
    caregiver_seats: Tuple[str, ...]
    protected_empty: frozenset[str]
    consumed_physical: frozenset[str]
    ssr_row_usage: Tuple[Tuple[Tuple[str, int, str], int], ...]
    ssr_subrow_usage: Tuple[Tuple[Tuple[str, Tuple[int, int], str], int], ...]
    flagged_rows: frozenset[Tuple[str, int]]
    flagged_subrows: frozenset[Tuple[str, Tuple[int, int]]]


@dataclass(frozen=True)
class SpecialWitnessResult:
    patterns: Optional[Dict[int, SpecialPattern]]
    search_nodes: int
    search_budget_exhausted: bool
    per_group_pattern_counts: Dict[int, int]


class GeneratorConstructionError(RuntimeError):
    """Generation failure with audit fields preserved for callers/tests."""

    def __init__(self, reason: str, diagnostics: dict):
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics


def _is_special_group(group: dict) -> bool:
    caregiver_hosts = set(group.get("_caregiverHostnums", []))
    return bool(caregiver_hosts) or any(
        passenger.get("ssr")
        or passenger.get("needCared") == "Y"
        or passenger["mandatoryRule"].get("needSingleSideEmpty") == "Y"
        or passenger["mandatoryRule"].get("needBothSideEmpty") == "Y"
        or passenger["mandatoryRule"].get("sameRowNoOtherSSR") == "Y"
        or passenger["mandatoryRule"].get("sameSubRowNoOtherSSR") == "Y"
        for passenger in group["psrs"]
    )


def _make_special_pattern(
    group: dict,
    topology: SeatTopology,
    assignments: Dict[int, str],
    protected: Set[str],
) -> Optional[SpecialPattern]:
    by_host = {passenger["hostnum"]: passenger for passenger in group["psrs"]}
    row_usage: Counter = Counter()
    subrow_usage: Counter = Counter()
    flagged_rows: Set[Tuple[str, int]] = set()
    flagged_subrows: Set[Tuple[str, Tuple[int, int]]] = set()
    for hostnum, seat_id in assignments.items():
        passenger = by_host[hostnum]
        ssr = passenger.get("ssr")
        if not ssr:
            continue
        seat = topology.seats[seat_id]
        row_key = (seat.seat_class, seat.row)
        subrow_key = (seat.seat_class, topology.subrow_id[seat_id])
        row_usage[(*row_key, ssr)] += 1
        subrow_usage[(*subrow_key, ssr)] += 1
        mandatory = passenger["mandatoryRule"]
        if mandatory.get("sameRowNoOtherSSR") == "Y":
            flagged_rows.add(row_key)
        if mandatory.get("sameSubRowNoOtherSSR") == "Y":
            flagged_subrows.add(subrow_key)
    if any(
        count > 1 and key[:2] in flagged_rows
        for key, count in row_usage.items()
    ) or any(
        count > 1 and key[:2] in flagged_subrows
        for key, count in subrow_usage.items()
    ):
        return None
    caregiver_hosts = set(group.get("_caregiverHostnums", []))
    occupied = set(assignments.values())
    return SpecialPattern(
        group_id=int(group["groupId"]),
        assignments=tuple(sorted(assignments.items())),
        caregiver_seats=tuple(sorted(
            seat_id for hostnum, seat_id in assignments.items()
            if hostnum in caregiver_hosts
        )),
        protected_empty=frozenset(protected),
        consumed_physical=frozenset(occupied | protected),
        ssr_row_usage=tuple(sorted(row_usage.items())),
        ssr_subrow_usage=tuple(sorted(subrow_usage.items())),
        flagged_rows=frozenset(flagged_rows),
        flagged_subrows=frozenset(flagged_subrows),
    )


def enumerate_special_patterns(
    group: dict,
    topology: SeatTopology,
    rng: random.Random,
    cfg: dict,
) -> List[SpecialPattern]:
    """Sample a fixed-size, deterministic set of internally legal hard patterns."""
    if not _is_special_group(group):
        return []
    cabin = cabin_class_for_passenger(group["psrs"][0])
    by_host = {passenger["hostnum"]: passenger for passenger in group["psrs"]}
    caregiver_hosts = sorted(set(group.get("_caregiverHostnums", [])))
    cared = sorted(
        (p for p in group["psrs"] if p.get("needCared") == "Y"),
        key=lambda passenger: passenger["hostnum"],
    )
    if len(caregiver_hosts) < len(cared):
        return []

    # Each role option is (host->seat assignments, protected-empty seats,
    # caregiver seats).  Atomic options make caregiver and protection
    # resources impossible to split during later combination.
    roles: List[List[Tuple[Tuple[Tuple[int, str], ...], frozenset[str], Tuple[str, ...]]]] = []
    handled_hosts: Set[int] = set()
    allow_cross = bool(cfg.get("both_side_empty_allow_cross_aisle", True))
    for passenger, caregiver_host in zip(cared, caregiver_hosts):
        caregiver = by_host.get(caregiver_host)
        if caregiver is None or caregiver.get("ssr") or caregiver.get("needCared") == "Y":
            return []
        options = []
        cross_aisle = bool(SSR_PROFILES[passenger["ssr"]].get(
            "caregiver_allow_cross_aisle", False
        ))
        for seat_id in sorted(topology.seats):
            seat = topology.seats[seat_id]
            if seat.seat_class != cabin or not seat_compatible_with_passenger(
                seat, passenger
            ):
                continue
            for neighbor in topology.adjacent(
                seat_id, allow_cross_aisle=cross_aisle
            ):
                neighbor_seat = topology.seats[neighbor]
                if (
                    neighbor_seat.seat_class == cabin
                    and seat_compatible_with_passenger(neighbor_seat, caregiver)
                ):
                    options.append((
                        tuple(sorted((
                            (passenger["hostnum"], seat_id),
                            (caregiver_host, neighbor),
                        ))),
                        frozenset(),
                        (neighbor,),
                    ))
        if not options:
            return []
        roles.append(options)
        handled_hosts.update((passenger["hostnum"], caregiver_host))

    for passenger in sorted(group["psrs"], key=lambda value: value["hostnum"]):
        hostnum = passenger["hostnum"]
        if hostnum in handled_hosts:
            continue
        mandatory = passenger["mandatoryRule"]
        double = mandatory.get("needBothSideEmpty") == "Y"
        single = mandatory.get("needSingleSideEmpty") == "Y"
        if not (passenger.get("ssr") or double or single):
            continue
        options = []
        for seat_id in sorted(topology.seats):
            seat = topology.seats[seat_id]
            if seat.seat_class != cabin or not seat_compatible_with_passenger(
                seat, passenger
            ):
                continue
            if double:
                neighbors = topology.adjacent(
                    seat_id, allow_cross_aisle=allow_cross
                )
                if len(neighbors) != 2 or any(
                    topology.seats[value].seat_class != cabin for value in neighbors
                ):
                    continue
                options.append((
                    ((hostnum, seat_id),), frozenset(neighbors), (),
                ))
            elif single:
                for neighbor in topology.adjacent(seat_id):
                    if topology.seats[neighbor].seat_class == cabin:
                        options.append((
                            ((hostnum, seat_id),), frozenset((neighbor,)), (),
                        ))
            else:
                options.append((((hostnum, seat_id),), frozenset(), ()))
        if not options:
            return []
        roles.append(options)
        handled_hosts.add(hostnum)

    roles.sort(key=len)
    found: Dict[
        Tuple[Tuple[Tuple[int, str], ...], Tuple[str, ...]], SpecialPattern
    ] = {}
    for _ in range(SPECIAL_PATTERN_SAMPLES):
        starts = [rng.randrange(len(options)) for options in roles]

        assignments: Dict[int, str] = {}
        occupied: Set[str] = set()
        protected: Set[str] = set()

        def place(index: int) -> Optional[SpecialPattern]:
            if index == len(roles):
                return _make_special_pattern(
                    group, topology, assignments, protected
                )
            options = roles[index]
            start = starts[index]
            for offset in range(len(options)):
                option_assignments, option_protected, _ = options[
                    (start + offset) % len(options)
                ]
                option_occupied = {seat_id for _, seat_id in option_assignments}
                if (
                    len(option_occupied) != len(option_assignments)
                    or option_occupied & (occupied | protected)
                    or set(option_protected) & (occupied | protected | option_occupied)
                ):
                    continue
                for hostnum, seat_id in option_assignments:
                    assignments[hostnum] = seat_id
                occupied.update(option_occupied)
                protected.update(option_protected)
                result = place(index + 1)
                if result is not None:
                    return result
                protected.difference_update(option_protected)
                occupied.difference_update(option_occupied)
                for hostnum, _ in option_assignments:
                    assignments.pop(hostnum, None)
            return None

        pattern = place(0)
        if pattern is None:
            break
        key = (pattern.assignments, tuple(sorted(pattern.protected_empty)))
        found.setdefault(key, pattern)
        if len(found) >= SPECIAL_PATTERN_LIMIT:
            break
    return list(found.values())


def _special_pattern_compatible(
    pattern: SpecialPattern,
    consumed: Set[str],
    row_usage: Counter,
    subrow_usage: Counter,
    flagged_rows: Set[Tuple[str, int]],
    flagged_subrows: Set[Tuple[str, Tuple[int, int]]],
) -> bool:
    if not pattern.consumed_physical.isdisjoint(consumed):
        return False
    if not (
        flagged_rows or flagged_subrows
        or pattern.flagged_rows or pattern.flagged_subrows
    ):
        return True
    combined_flagged_rows = flagged_rows | set(pattern.flagged_rows)
    combined_flagged_subrows = flagged_subrows | set(pattern.flagged_subrows)
    for key, count in pattern.ssr_row_usage:
        if row_usage[key] + count > 1 and key[:2] in combined_flagged_rows:
            return False
    for key, count in pattern.ssr_subrow_usage:
        if subrow_usage[key] + count > 1 and key[:2] in combined_flagged_subrows:
            return False
    # A newly activated isolation location must also validate SSR types that
    # are present only in the already selected patterns.
    if any(
        count > 1 and key[:2] in combined_flagged_rows
        for key, count in row_usage.items()
    ) or any(
        count > 1 and key[:2] in combined_flagged_subrows
        for key, count in subrow_usage.items()
    ):
        return False
    return True


def find_joint_special_witness(
    patterns_by_group: Dict[int, Sequence[SpecialPattern]],
    node_limit: int = SPECIAL_WITNESS_NODE_LIMIT,
) -> SpecialWitnessResult:
    """Find one compatible pattern per group using bounded deterministic DFS."""
    counts = {
        int(group_id): len(patterns)
        for group_id, patterns in patterns_by_group.items()
    }
    if any(count == 0 for count in counts.values()):
        return SpecialWitnessResult(None, 0, False, counts)
    order = sorted(
        patterns_by_group,
        key=lambda group_id: (
            len(patterns_by_group[group_id]),
            -max(
                (len(pattern.consumed_physical) for pattern in patterns_by_group[group_id]),
                default=0,
            ),
            int(group_id),
        ),
    )
    selected: Dict[int, SpecialPattern] = {}
    consumed: Set[str] = set()
    row_usage: Counter = Counter()
    subrow_usage: Counter = Counter()
    flagged_rows: Set[Tuple[str, int]] = set()
    flagged_subrows: Set[Tuple[str, Tuple[int, int]]] = set()
    nodes = 0
    exhausted = False

    def search(index: int) -> bool:
        nonlocal nodes, exhausted
        if index == len(order):
            return True
        group_id = order[index]
        for pattern in patterns_by_group[group_id]:
            nodes += 1
            if nodes > node_limit:
                exhausted = True
                return False
            if not _special_pattern_compatible(
                pattern, consumed, row_usage, subrow_usage,
                flagged_rows, flagged_subrows,
            ):
                continue
            selected[group_id] = pattern
            consumed.update(pattern.consumed_physical)
            for key, count in pattern.ssr_row_usage:
                row_usage[key] += count
            for key, count in pattern.ssr_subrow_usage:
                subrow_usage[key] += count
            old_flagged_rows = set(flagged_rows)
            old_flagged_subrows = set(flagged_subrows)
            flagged_rows.update(pattern.flagged_rows)
            flagged_subrows.update(pattern.flagged_subrows)
            has_future_domain = all(
                any(
                    _special_pattern_compatible(
                        future_pattern, consumed, row_usage, subrow_usage,
                        flagged_rows, flagged_subrows,
                    )
                    for future_pattern in patterns_by_group[future_group_id]
                )
                for future_group_id in order[index + 1:]
            )
            if has_future_domain and search(index + 1):
                return True
            flagged_rows.clear()
            flagged_rows.update(old_flagged_rows)
            flagged_subrows.clear()
            flagged_subrows.update(old_flagged_subrows)
            for key, count in pattern.ssr_row_usage:
                row_usage[key] -= count
            for key, count in pattern.ssr_subrow_usage:
                subrow_usage[key] -= count
            consumed.difference_update(pattern.consumed_physical)
            selected.pop(group_id, None)
            if exhausted:
                return False
        return False

    found = search(0)
    return SpecialWitnessResult(
        dict(selected) if found else None,
        nodes,
        exhausted,
        counts,
    )


def build_joint_special_witness(
    groups: Sequence[dict],
    topology: SeatTopology,
    rng: random.Random,
    cfg: dict,
) -> SpecialWitnessResult:
    patterns_by_group = {
        int(group["groupId"]): enumerate_special_patterns(
            group, topology, rng, cfg
        )
        for group in groups if _is_special_group(group)
    }
    return find_joint_special_witness(patterns_by_group)


def make_unseated_passenger(
    hostnum: int, cabin_class: str, rng: random.Random,
    option_rule_ratio: float,
) -> dict:
    """Create a passenger before any source seat is sampled."""
    return {
        "hostnum": hostnum,
        "cabin": CABIN_CODE_BY_CLASS.get(cabin_class, cabin_class),
        "needCared": "N",
        "ssr": "",
        "mandatoryRule": default_mandatory(),
        "optionRule": generate_option_rules(rng, option_rule_ratio),
    }


def _ssr_position_exists(
    topology: SeatTopology, cabin_class: str, ssr: str,
) -> bool:
    profile = SSR_PROFILES[ssr]
    for seat in topology.seats.values():
        if seat.seat_class != cabin_class or not seat_compatible_with_ssr(seat, ssr):
            continue
        if profile.get("needSingleSideEmpty") == "Y" and not topology.adjacent(seat.seat_id):
            continue
        if profile.get("needCared") == "Y" and not any(
            topology.seats[neighbor].seat_class == cabin_class
            for neighbor in topology.adjacent(
                seat.seat_id,
                bool(profile.get("caregiver_allow_cross_aisle", False)),
            )
        ):
            continue
        return True
    return False


def _assign_ssr_metadata(
    groups: List[dict], source: SeatTopology, target: SeatTopology,
    rng: random.Random, cfg: dict, target_special: int,
) -> int:
    """Assign SSR/caregiver semantics before creating any oldSeat value."""
    assigned = 0

    def try_assign(ssr: str) -> bool:
        nonlocal assigned
        profile = SSR_PROFILES[ssr]
        candidates = []
        for group in groups:
            cabin_class = cabin_class_for_passenger(group["psrs"][0])
            if not (
                _ssr_position_exists(source, cabin_class, ssr)
                and _ssr_position_exists(target, cabin_class, ssr)
            ):
                continue
            if profile["needCared"] == "Y" and group.get("_caregiverHostnums"):
                continue
            reserved = set(group.get("_caregiverHostnums", []))
            for passenger in group["psrs"]:
                if not passenger.get("ssr") and passenger["hostnum"] not in reserved:
                    candidates.append((group, passenger))
        rng.shuffle(candidates)
        for group, passenger in candidates:
            caregiver = None
            if profile["needCared"] == "Y":
                caregiver_candidates = [
                    value for value in group["psrs"]
                    if value is not passenger
                    and not value.get("ssr")
                    and value["hostnum"] not in set(group.get("_caregiverHostnums", []))
                ]
                if not caregiver_candidates:
                    continue
                caregiver = rng.choice(caregiver_candidates)
            passenger["ssr"] = ssr
            passenger["needCared"] = profile["needCared"]
            passenger["mandatoryRule"]["needBothSideEmpty"] = profile[
                "needBothSideEmpty"
            ]
            passenger["mandatoryRule"]["needSingleSideEmpty"] = profile[
                "needSingleSideEmpty"
            ]
            passenger["mandatoryRule"]["sameSubRowNoOtherSSR"] = (
                "Y" if rng.random() < cfg["same_subrow_rule_ratio"] else "N"
            )
            passenger["mandatoryRule"]["sameRowNoOtherSSR"] = (
                "Y" if rng.random() < cfg["same_row_rule_ratio"] else "N"
            )
            if caregiver is not None:
                group.setdefault("_caregiverHostnums", []).append(
                    caregiver["hostnum"]
                )
            assigned += 1
            return True
        return False

    required = ("BSCT", "WCHR", "CHD", "BLND", "EXST", "CBBG", "UM")
    for ssr in required:
        if not try_assign(ssr):
            raise RuntimeError(f"无法在旧座位生成前构造必需 SSR/caregiver: {ssr}")
    attempts = 0
    target_special = max(target_special, len(required))
    while assigned < target_special and attempts < max(1000, target_special * 100):
        attempts += 1
        try_assign(weighted_choice(rng, cfg["ssr_weights"]))
    return assigned


def _protection_geometry_exists(
    topology: SeatTopology, cabin_class: str, amount: int, cfg: dict,
) -> bool:
    allow_cross = bool(cfg.get("both_side_empty_allow_cross_aisle", True))
    return any(
        seat.seat_class == cabin_class
        and (
            bool(topology.adjacent(seat.seat_id)) if amount == 1
            else len(topology.adjacent(
                seat.seat_id, allow_cross_aisle=allow_cross
            )) == 2
        )
        for seat in topology.seats.values()
    )


def _assign_protection_metadata(
    groups: List[dict], source: SeatTopology, target: SeatTopology,
    rng: random.Random, cfg: dict, resource_target: int,
) -> bool:
    """Choose all protection rules before old-assignment placement."""
    if target_seat_demand(groups) > resource_target:
        return False
    source_capacity = cabin_seat_capacity(source)
    target_capacity = cabin_seat_capacity(target)
    cabin_slack = Counter({
        cabin: min(source_capacity[cabin], target_capacity[cabin])
        for cabin in source_capacity.keys() | target_capacity.keys()
    })
    cabin_slack.subtract(cabin_seat_demand(groups))
    if any(value < 0 for value in cabin_slack.values()):
        return False
    candidates = []
    for group in groups:
        caregivers = set(group.get("_caregiverHostnums", []))
        for passenger in group["psrs"]:
            if (
                passenger.get("ssr")
                or passenger.get("needCared") == "Y"
                or passenger["hostnum"] in caregivers
            ):
                continue
            cabin = cabin_class_for_passenger(passenger)
            modes = tuple(
                amount for amount in (1, 2)
                if _protection_geometry_exists(source, cabin, amount, cfg)
                and _protection_geometry_exists(target, cabin, amount, cfg)
            )
            if modes:
                candidates.append((passenger, cabin, modes))
    rng.shuffle(candidates)

    required_both = max(
        int(cfg.get("minimum_both_side_empty", 0)),
        round(passenger_count(groups) * float(cfg.get("both_side_empty_ratio", 0.0))),
    )
    remaining_target = resource_target - target_seat_demand(groups)
    selected_ids = set()
    both_assigned = 0
    for index, (passenger, cabin, modes) in enumerate(candidates):
        if both_assigned >= required_both:
            break
        if 2 not in modes or remaining_target < 2 or cabin_slack[cabin] < 2:
            continue
        passenger["mandatoryRule"]["needBothSideEmpty"] = "Y"
        passenger["mandatoryRule"]["needSingleSideEmpty"] = "N"
        cabin_slack[cabin] -= 2
        remaining_target -= 2
        selected_ids.add(index)
        both_assigned += 1
    if both_assigned < required_both:
        return False

    cabin_names = tuple(sorted(cabin_slack))
    cabin_index = {name: index for index, name in enumerate(cabin_names)}
    choices = [
        value for index, value in enumerate(candidates) if index not in selected_ids
    ]
    zero = (0,) * len(cabin_names)
    states: Dict[Tuple[int, Tuple[int, ...]], Tuple[Tuple[int, int], ...]] = {
        (0, zero): ()
    }
    for candidate_index, (_, cabin, modes) in enumerate(choices):
        updated = dict(states)
        for (used, usage), selected in states.items():
            for amount in modes:
                next_used = used + amount
                if next_used > remaining_target:
                    continue
                next_usage = list(usage)
                index = cabin_index[cabin]
                next_usage[index] += amount
                if next_usage[index] > cabin_slack[cabin]:
                    continue
                updated.setdefault(
                    (next_used, tuple(next_usage)),
                    (*selected, (candidate_index, amount)),
                )
        states = updated
    solution = next(
        (value for (used, _), value in states.items() if used == remaining_target),
        None,
    )
    if solution is None:
        return False
    for candidate_index, amount in solution:
        passenger = choices[candidate_index][0]
        key = "needBothSideEmpty" if amount == 2 else "needSingleSideEmpty"
        passenger["mandatoryRule"][key] = "Y"
    return target_seat_demand(groups) == resource_target


def _normalize_target_ssr_isolation(
    groups: List[dict], target: SeatTopology,
) -> None:
    by_domain: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for group in groups:
        for passenger in group["psrs"]:
            if passenger.get("ssr"):
                by_domain[(
                    passenger["ssr"], cabin_class_for_passenger(passenger)
                )].append(passenger)
    disable_row = False
    disable_subrow = False
    for (ssr, cabin), passengers in by_domain.items():
        compatible = [
            seat for seat in target.seats.values()
            if seat.seat_class == cabin and seat_compatible_with_ssr(seat, ssr)
        ]
        disable_row |= len(passengers) > len({seat.row for seat in compatible})
        disable_subrow |= len(passengers) > len({
            target.subrow_id[seat.seat_id] for seat in compatible
        })
    for group in groups:
        for passenger in group["psrs"]:
            if disable_row:
                passenger["mandatoryRule"]["sameRowNoOtherSSR"] = "N"
            if disable_subrow:
                passenger["mandatoryRule"]["sameSubRowNoOtherSSR"] = "N"


def _source_ssr_isolation_valid(
    groups: Sequence[dict], topology: SeatTopology,
    proposed: Sequence[Tuple[dict, str]] = (),
) -> bool:
    proposed_by_identity = {id(passenger): seat for passenger, seat in proposed}
    row_counts: Counter = Counter()
    subrow_counts: Counter = Counter()
    flagged_rows = set()
    flagged_subrows = set()
    for group in groups:
        for passenger in group["psrs"]:
            ssr = passenger.get("ssr")
            if not ssr:
                continue
            seat_id = proposed_by_identity.get(id(passenger))
            if seat_id is None:
                seat_id = passenger.get("oldSeat", {}).get("seatNum")
            if not seat_id:
                continue
            seat = topology.seats[seat_id]
            row_key = (seat.seat_class, seat.row)
            subrow_key = (seat.seat_class, topology.subrow_id[seat_id])
            row_counts[(*row_key, ssr)] += 1
            subrow_counts[(*subrow_key, ssr)] += 1
            mandatory = passenger.get("mandatoryRule", {})
            if mandatory.get("sameRowNoOtherSSR", "N") == "Y":
                flagged_rows.add(row_key)
            if mandatory.get("sameSubRowNoOtherSSR", "N") == "Y":
                flagged_subrows.add(subrow_key)
    return not any(
        count > 1 and key[:2] in flagged_rows
        for key, count in row_counts.items()
    ) and not any(
        count > 1 and key[:2] in flagged_subrows
        for key, count in subrow_counts.items()
    )


def _ordinary_seats_for_mode(
    topology: SeatTopology, free: Set[str], cabin: str, count: int,
    anchors: Sequence[str], mode: str, rng: random.Random,
) -> Optional[List[str]]:
    if count == 0:
        return []
    available = [
        seat.seat_id for seat in topology.seats.values()
        if seat.seat_id in free and seat.seat_class == cabin
    ]
    if len(available) < count:
        return None
    anchor_rows = [topology.seats[seat_id].row for seat_id in anchors]
    if mode == "compact" and not anchors:
        blocks = [
            block for block in compact_blocks(topology, count, free)
            if all(topology.seats[seat_id].seat_class == cabin for seat_id in block)
        ]
        return rng.choice(blocks) if blocks else None
    if mode == "same_row":
        rows = list(topology.rows)
        rng.shuffle(rows)
        for row in rows:
            if anchor_rows and any(value != row for value in anchor_rows):
                continue
            candidates = [
                seat_id for seat_id in available if topology.seats[seat_id].row == row
            ]
            if len(candidates) >= count:
                return rng.sample(candidates, count)
        return None
    if mode in {"compact", "nearby"}:
        centers = sorted(set(anchor_rows) or {topology.seats[s].row for s in available})
        rng.shuffle(centers)
        max_span = 1 if mode == "compact" else 2
        for center in centers:
            candidates = [
                seat_id for seat_id in available
                if abs(topology.seats[seat_id].row - center) <= max_span
            ]
            combined_rows = anchor_rows + [topology.seats[s].row for s in candidates]
            if len(candidates) >= count and (
                not combined_rows or max(combined_rows) - min(combined_rows) <= 2 * max_span
            ):
                candidates.sort(key=lambda seat_id: (
                    abs(topology.seats[seat_id].row - center),
                    seat_label_number(topology.seats[seat_id].col),
                ))
                window = candidates[:min(len(candidates), max(count, count * 3))]
                return rng.sample(window, count)
        return None
    rng.shuffle(available)
    if count >= 2:
        broad = [
            values for values in (
                rng.sample(available, count) for _ in range(20)
            )
            if max(topology.seats[s].row for s in values)
            - min(topology.seats[s].row for s in values) >= 3
        ]
        if broad:
            return rng.choice(broad)
    return available[:count]


def _place_group_old_assignment(
    group: dict, groups: Sequence[dict], topology: SeatTopology,
    state: OldAssignmentState, rng: random.Random, cfg: dict,
) -> bool:
    cabin = cabin_class_for_passenger(group["psrs"][0])
    profile = cfg["synthetic_old_assignment_profile"]
    allow_cross = bool(cfg.get("both_side_empty_allow_cross_aisle", True))
    caregiver_hosts = set(group.get("_caregiverHostnums", []))
    by_host = {passenger["hostnum"]: passenger for passenger in group["psrs"]}
    for _ in range(GROUP_PLACEMENT_ATTEMPTS):
        free = set(state.free_old)
        assigned: Dict[int, str] = {}
        protected: Set[str] = set()

        def occupy(passenger: dict, seat_id: str) -> None:
            assigned[passenger["hostnum"]] = seat_id
            free.remove(seat_id)

        failed = False
        cared = [p for p in group["psrs"] if p.get("needCared") == "Y"]
        for passenger in cared:
            caregiver_candidates = [
                by_host[host] for host in caregiver_hosts
                if host not in assigned and not by_host[host].get("ssr")
            ]
            pairs = []
            for seat_id in sorted(free):
                seat = topology.seats[seat_id]
                if seat.seat_class != cabin or not seat_compatible_with_passenger(seat, passenger):
                    continue
                for neighbor in topology.adjacent(
                    seat_id,
                    bool(SSR_PROFILES[passenger["ssr"]].get(
                        "caregiver_allow_cross_aisle", False
                    )),
                ):
                    if neighbor in free and topology.seats[neighbor].seat_class == cabin:
                        for caregiver in caregiver_candidates:
                            pairs.append((seat_id, neighbor, caregiver))
            if not pairs:
                failed = True
                break
            seat_id, neighbor, caregiver = rng.choice(pairs)
            occupy(passenger, seat_id)
            occupy(caregiver, neighbor)
        if failed:
            continue

        double = [
            p for p in group["psrs"]
            if p["mandatoryRule"].get("needBothSideEmpty") == "Y"
            and p["hostnum"] not in assigned
        ]
        for passenger in double:
            options = []
            for seat_id in sorted(free):
                seat = topology.seats[seat_id]
                neighbors = topology.adjacent(
                    seat_id, allow_cross_aisle=allow_cross
                )
                if (
                    seat.seat_class == cabin
                    and seat_compatible_with_passenger(seat, passenger)
                    and len(neighbors) == 2
                    and set(neighbors) <= free
                ):
                    options.append((seat_id, set(neighbors)))
            if not options:
                failed = True
                break
            seat_id, neighbors = rng.choice(options)
            occupy(passenger, seat_id)
            protected.update(neighbors)
            free.difference_update(neighbors)
        if failed:
            continue

        single = [
            p for p in group["psrs"]
            if p["mandatoryRule"].get("needSingleSideEmpty") == "Y"
            and p["hostnum"] not in assigned
        ]
        for passenger in single:
            options = []
            for seat_id in sorted(free):
                seat = topology.seats[seat_id]
                if seat.seat_class != cabin or not seat_compatible_with_passenger(seat, passenger):
                    continue
                for neighbor in topology.adjacent(seat_id):
                    if neighbor in free and topology.seats[neighbor].seat_class == cabin:
                        options.append((seat_id, neighbor))
            if not options:
                failed = True
                break
            seat_id, neighbor = rng.choice(options)
            occupy(passenger, seat_id)
            protected.add(neighbor)
            free.remove(neighbor)
        if failed:
            continue

        other_ssr = [
            p for p in group["psrs"] if p.get("ssr") and p["hostnum"] not in assigned
        ]
        for passenger in other_ssr:
            options = [
                seat_id for seat_id in sorted(free)
                if topology.seats[seat_id].seat_class == cabin
                and seat_compatible_with_passenger(topology.seats[seat_id], passenger)
            ]
            if not options:
                failed = True
                break
            occupy(passenger, rng.choice(options))
        if failed:
            continue

        remaining = [
            p for p in group["psrs"] if p["hostnum"] not in assigned
        ]
        mode = weighted_choice(rng, profile)
        seats = _ordinary_seats_for_mode(
            topology, free, cabin, len(remaining), list(assigned.values()), mode, rng
        )
        if seats is None:
            continue
        for passenger, seat_id in zip(remaining, seats):
            occupy(passenger, seat_id)
        proposed = [(by_host[host], seat_id) for host, seat_id in assigned.items()]
        if not _source_ssr_isolation_valid(groups, topology, proposed):
            continue

        occupied = set(assigned.values())
        state.commit(occupied, protected)
        for host, seat_id in assigned.items():
            by_host[host]["oldSeat"] = {
                "seatNum": seat_id,
                "seatValue": topology.seat_value(seat_id),
            }
        return True
    return False


def _apply_source_special_witness(
    groups: Sequence[dict],
    topology: SeatTopology,
    state: OldAssignmentState,
    witness: Dict[int, SpecialPattern],
) -> None:
    by_group = {int(group["groupId"]): group for group in groups}
    for group_id in sorted(witness):
        pattern = witness[group_id]
        group = by_group[group_id]
        by_host = {
            passenger["hostnum"]: passenger for passenger in group["psrs"]
        }
        occupied = {seat_id for _, seat_id in pattern.assignments}
        state.commit(occupied, set(pattern.protected_empty))
        for hostnum, seat_id in pattern.assignments:
            by_host[hostnum]["oldSeat"] = {
                "seatNum": seat_id,
                "seatValue": topology.seat_value(seat_id),
            }


def _complete_witness_group_old_assignment(
    group: dict,
    topology: SeatTopology,
    state: OldAssignmentState,
    rng: random.Random,
    cfg: dict,
) -> bool:
    remaining = [
        passenger for passenger in group["psrs"]
        if not passenger.get("oldSeat", {}).get("seatNum")
    ]
    if not remaining:
        return True
    cabin = cabin_class_for_passenger(group["psrs"][0])
    anchors = [
        passenger["oldSeat"]["seatNum"] for passenger in group["psrs"]
        if passenger.get("oldSeat", {}).get("seatNum")
    ]
    profile = cfg["synthetic_old_assignment_profile"]
    for _ in range(GROUP_PLACEMENT_ATTEMPTS):
        mode = weighted_choice(rng, profile)
        seats = _ordinary_seats_for_mode(
            topology, set(state.free_old), cabin, len(remaining),
            anchors, mode, rng,
        )
        if seats is None:
            continue
        state.commit(set(seats), set())
        for passenger, seat_id in zip(remaining, seats):
            passenger["oldSeat"] = {
                "seatNum": seat_id,
                "seatValue": topology.seat_value(seat_id),
            }
        return True
    return False


def assign_old_seats_constructively(
    groups: List[dict], topology: SeatTopology, rng: random.Random, cfg: dict,
    source_witness: Optional[Dict[int, SpecialPattern]] = None,
) -> OldAssignmentState:
    """Construct a hard-feasible randomized source assignment with restarts."""
    order = sorted(groups, key=lambda group: (
        -sum(
            5 * (p["mandatoryRule"].get("needBothSideEmpty") == "Y")
            + 4 * (p["mandatoryRule"].get("needSingleSideEmpty") == "Y")
            + 4 * (p.get("needCared") == "Y")
            + 2 * bool(p.get("ssr"))
            for p in group["psrs"]
        ),
        -len(group["psrs"]),
        int(group["groupId"]),
    ))
    for _ in range(OLD_ASSIGNMENT_RESTARTS):
        for group in groups:
            for passenger in group["psrs"]:
                passenger.pop("oldSeat", None)
        state = OldAssignmentState.empty(topology)
        if source_witness is not None:
            _apply_source_special_witness(
                groups, topology, state, source_witness
            )
        completed = True
        for group in order:
            if source_witness is not None and int(group["groupId"]) in source_witness:
                placed = _complete_witness_group_old_assignment(
                    group, topology, state, rng, cfg
                )
            else:
                placed = _place_group_old_assignment(
                    group, groups, topology, state, rng, cfg
                )
            if not placed:
                completed = False
                break
        if completed:
            errors = validate_source_state(groups, topology, cfg)
            if not errors:
                for group in groups:
                    group.pop("_caregiverHostnums", None)
                return state
    raise RuntimeError("ordinary_old_assignment_completion_failed")


def _generate_groups_exact_count(
    topology: SeatTopology,
    total_passengers: int,
    rng: random.Random,
    cfg: dict,
    target_topology: Optional[SeatTopology] = None,
    target_seat_demand: Optional[int] = None,
    diagnostics_out: Optional[dict] = None,
) -> List[dict]:
    sizes = sample_group_sizes(rng, total_passengers, cfg["group_size_weights"])
    source_capacity = cabin_seat_capacity(topology)
    target_capacity = cabin_seat_capacity(target_topology or topology)
    remaining_cabin_capacity = Counter({
        name: min(source_capacity[name], target_capacity[name])
        for name in source_capacity.keys() | target_capacity.keys()
    })
    transferable_capacity = sum(remaining_cabin_capacity.values())
    if (
        target_seat_demand == transferable_capacity
        and total_passengers < transferable_capacity
    ):
        # 满载时把保护空座的余量优先留在最大舱位，较小舱位尽量由
        # 真实旅客填满。否则随机旧座位可能在小舱位留下只能靠保护
        # 空座填充的缺口，制造虽未超总量但极难甚至不可行的数据。
        protection_gap = transferable_capacity - total_passengers
        for cabin_class, _ in sorted(
            remaining_cabin_capacity.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            reduction = min(
                protection_gap, remaining_cabin_capacity[cabin_class]
            )
            remaining_cabin_capacity[cabin_class] -= reduction
            protection_gap -= reduction
            if protection_gap == 0:
                break
    if total_passengers > sum(remaining_cabin_capacity.values()):
        raise RuntimeError("旅客数超过不换舱条件下的可转移容量")
    group_cabins = assign_group_cabins(
        sizes, remaining_cabin_capacity, rng
    )
    base_groups: List[dict] = []

    for gid, (size, group_cabin) in enumerate(
        zip(sizes, group_cabins), 1
    ):
        psrs = [
            make_unseated_passenger(
                i + 1, group_cabin, rng, cfg["option_rule_ratio"]
            )
            for i in range(size)
        ]
        base_groups.append({
            "groupId": gid,
            "groupType": group_type_for(psrs),
            "PNR": random_pnr(rng),
            "psrs": psrs,
        })

    special_target = round(total_passengers * cfg["ssr_ratio"])
    target = target_topology or topology
    resource_target = int(target_seat_demand or total_passengers)
    diagnostics = {
        "source_special_group_count": 0,
        "target_special_group_count": 0,
        "source_per_group_legal_pattern_counts": {},
        "target_per_group_legal_pattern_counts": {},
        "source_witness_search_nodes": 0,
        "target_witness_search_nodes": 0,
        "source_witness_search_budget_exhausted": 0,
        "target_witness_search_budget_exhausted": 0,
        "special_role_resample_attempts": 0,
        "no_source_joint_special_witness": 0,
        "no_target_joint_special_witness": 0,
        "special_resample_exhausted": False,
        "ordinary_old_assignment_completion_failed": 0,
        "role_metadata_assignment_failed": 0,
    }
    groups: List[dict] = []
    for role_attempt in range(1, SPECIAL_ROLE_RESAMPLE_ATTEMPTS + 1):
        diagnostics["special_role_resample_attempts"] = role_attempt
        groups = copy.deepcopy(base_groups)
        try:
            _assign_ssr_metadata(
                groups, topology, target, rng, cfg, special_target
            )
        except RuntimeError:
            diagnostics["role_metadata_assignment_failed"] += 1
            continue
        if not _assign_protection_metadata(
            groups, topology, target, rng, cfg, resource_target,
        ):
            diagnostics["role_metadata_assignment_failed"] += 1
            continue
        _normalize_target_ssr_isolation(groups, target)
        if resource_target == transferable_seat_capacity(topology, target):
            # This is the pre-existing full-demand contract, applied before
            # witness construction so source/target proofs describe the data
            # that will actually be returned.
            for group in groups:
                for passenger in group["psrs"]:
                    passenger["mandatoryRule"]["sameSubRowNoOtherSSR"] = "N"
                    passenger["mandatoryRule"]["sameRowNoOtherSSR"] = "N"

        source_result = build_joint_special_witness(
            groups, topology, rng, cfg
        )
        diagnostics["source_special_group_count"] = len(
            source_result.per_group_pattern_counts
        )
        diagnostics["source_per_group_legal_pattern_counts"] = {
            str(group_id): count for group_id, count in sorted(
                source_result.per_group_pattern_counts.items()
            )
        }
        diagnostics["source_witness_search_nodes"] = source_result.search_nodes
        if source_result.search_budget_exhausted:
            diagnostics["source_witness_search_budget_exhausted"] += 1
        if source_result.patterns is None:
            diagnostics["no_source_joint_special_witness"] += 1
            continue

        target_result = build_joint_special_witness(
            groups, target, rng, cfg
        )
        diagnostics["target_special_group_count"] = len(
            target_result.per_group_pattern_counts
        )
        diagnostics["target_per_group_legal_pattern_counts"] = {
            str(group_id): count for group_id, count in sorted(
                target_result.per_group_pattern_counts.items()
            )
        }
        diagnostics["target_witness_search_nodes"] = target_result.search_nodes
        if target_result.search_budget_exhausted:
            diagnostics["target_witness_search_budget_exhausted"] += 1
        if target_result.patterns is None:
            diagnostics["no_target_joint_special_witness"] += 1
            continue

        try:
            assign_old_seats_constructively(
                groups, topology, rng, cfg, source_result.patterns
            )
        except RuntimeError as exc:
            if str(exc) != "ordinary_old_assignment_completion_failed":
                raise
            diagnostics["ordinary_old_assignment_completion_failed"] += 1
            continue
        if diagnostics_out is not None:
            diagnostics_out.clear()
            diagnostics_out.update(diagnostics)
        break
    else:
        diagnostics["special_resample_exhausted"] = True
        if diagnostics_out is not None:
            diagnostics_out.clear()
            diagnostics_out.update(diagnostics)
        raise GeneratorConstructionError(
            "special_resample_exhausted", diagnostics
        )

    present_ssrs = {p.get("ssr") for g in groups for p in g["psrs"] if p.get("ssr")}
    missing_ssrs = set(SSR_PROFILES) - present_ssrs
    if missing_ssrs:
        raise RuntimeError(f"生成结果缺少 SSR 类型: {sorted(missing_ssrs)}")

    for g in groups:
        g["groupType"] = group_type_for(g["psrs"])
    return groups


def generate_groups(
    topology: SeatTopology,
    total_passengers: int,
    rng: random.Random,
    cfg: dict,
    target_topology: Optional[SeatTopology] = None,
    diagnostics_out: Optional[dict] = None,
) -> List[dict]:
    """
    生成计入占座/保护留空后严格等于请求值（或机型上限）的数据。

    ``total_passengers`` 包含真实旅客及保护座位：单侧留空计 1，双侧
    留空计 2。请求超过新旧机型较小容量时先截断到容量；随后搜索最大的
    真实旅客数，并补足保护座位，使总计数严格等于截断后的目标值。
    """
    if total_passengers <= 0:
        raise ValueError("旅客数必须为正整数")

    target = target_topology or topology
    target_capacity = transferable_seat_capacity(topology, target)
    counted_passengers = min(int(total_passengers), target_capacity)
    initial_rng_state = rng.getstate()

    traveler_count = counted_passengers
    last_diagnostics: dict = {}
    while traveler_count > 0:
        rng.setstate(initial_rng_state)
        attempt_diagnostics: dict = {}
        try:
            groups = _generate_groups_exact_count(
                topology,
                traveler_count,
                rng,
                cfg,
                target_topology=target_topology,
                target_seat_demand=counted_passengers,
                diagnostics_out=attempt_diagnostics,
            )
        except RuntimeError:
            last_diagnostics = attempt_diagnostics
            traveler_count -= 1
            continue
        current_demand = target_seat_demand(groups)
        if current_demand <= counted_passengers:
            deficit = counted_passengers - current_demand
            if add_protected_seat_demand(
                groups,
                topology,
                target,
                deficit,
                rng,
                cfg,
            ):
                if target_seat_demand(groups) != counted_passengers:
                    raise AssertionError("counted passenger demand mismatch")
                if counted_passengers == target_capacity:
                    # 满载没有缓冲座位。保留 SSR 的核心座位/陪护规则，但
                    # 关闭随机附加的同行外 SSR 排/小排隔离，避免生成只能靠
                    # 空闲容量化解的组合。
                    for group in groups:
                        for passenger in group["psrs"]:
                            mandatory = passenger["mandatoryRule"]
                            mandatory["sameSubRowNoOtherSSR"] = "N"
                            mandatory["sameRowNoOtherSSR"] = "N"
                if diagnostics_out is not None:
                    diagnostics_out.clear()
                    diagnostics_out.update(attempt_diagnostics)
                    diagnostics_out["traveler_count"] = traveler_count
                    diagnostics_out["requested_resource_demand"] = counted_passengers
                return groups
        # 保护规则数量随随机组结构并非严格单调，逐人递减避免跳过更大的
        # 可行真实旅客数。
        traveler_count -= 1

    if diagnostics_out is not None:
        diagnostics_out.clear()
        diagnostics_out.update(last_diagnostics)
    raise GeneratorConstructionError(
        "目标机型没有足够容量生成任何旅客", last_diagnostics
    )


def seat_compatible_with_ssr(seat: Seat, ssr: str) -> bool:
    """集中判断 SSR 对座位属性的基础限制。"""
    if not ssr:
        return True
    profile = SSR_PROFILES.get(ssr, {})
    if seat.is_exit and not profile.get("allow_exit_row", False):
        return False
    if profile.get("requires_aisle", False) and not seat.is_aisle:
        return False
    if profile.get("requires_bassinet", False) and not seat.has_bassinet:
        return False
    return True


def seat_compatible_with_passenger(seat: Seat, p: dict) -> bool:
    expected_class = CABIN_CLASS_MAP.get(str(p.get("cabin", "")).upper())
    if expected_class is not None and seat.seat_class != expected_class:
        return False
    return seat_compatible_with_ssr(seat, p.get("ssr", ""))


def compatible_unoccupied_seats(
    topology: SeatTopology,
    passenger: dict,
    occupied: Set[str],
) -> List[Seat]:
    """返回尚未占用且满足旅客舱等、SSR 基础限制的座位。"""
    return [
        seat
        for seat in topology.seats.values()
        if seat.seat_id not in occupied
        and seat_compatible_with_passenger(seat, passenger)
    ]


def target_candidate_score(old: Seat, new: Seat) -> Tuple:
    return (
        abs(old.row - new.row),
        abs(seat_label_number(old.col) - seat_label_number(new.col)),
        int(old.is_window != new.is_window),
        int(old.is_aisle != new.is_aisle),
    )


def _care_group_feasible_assignment(
    group: dict,
    old_topology: SeatTopology,
    new_topology: SeatTopology,
    occupied: Set[str],
) -> Optional[Dict[int, str]]:
    """返回满足固定座位、SSR 基础域和陪护邻接的整组可行安排。"""
    passengers = group["psrs"]
    cared = [p for p in passengers if p.get("needCared") == "Y"]
    caregivers = [
        p
        for p in passengers
        if (
            p.get("needCared") != "Y"
            and p.get("ssr", "") == ""
            and p.get("mandatoryRule", {}).get(
                "needSingleSideEmpty", "N"
            )
            != "Y"
            and p.get("mandatoryRule", {}).get(
                "needBothSideEmpty", "N"
            )
            != "Y"
        )
    ]
    if cared and not caregivers:
        return None

    own_fixed = {
        p["newSeat"]["seatNum"]
        for p in passengers
        if p.get("newSeat", {}).get("seatNum") in new_topology.seats
    }
    unavailable = occupied - own_fixed
    domains: Dict[int, List[str]] = {}
    for p in passengers:
        fixed_seat = p.get("newSeat", {}).get("seatNum")
        if fixed_seat:
            candidates = (
                [fixed_seat]
                if (
                    fixed_seat not in unavailable
                    and fixed_seat in new_topology.seats
                    and seat_compatible_with_passenger(
                        new_topology.seats[fixed_seat], p
                    )
                )
                else []
            )
        else:
            old = old_topology.seats[p["oldSeat"]["seatNum"]]
            candidates = [
                seat.seat_id
                for seat in compatible_unoccupied_seats(
                    new_topology, p, unavailable
                )
            ]
            candidates.sort(
                key=lambda seat_id: target_candidate_score(
                    old, new_topology.seats[seat_id]
                )
            )
        if not candidates:
            return None
        domains[p["hostnum"]] = candidates

    assignments: Dict[int, str] = {}
    used: Set[str] = set(unavailable)
    for p in passengers:
        fixed_seat = p.get("newSeat", {}).get("seatNum")
        if not fixed_seat:
            continue
        if fixed_seat in used:
            return None
        assignments[p["hostnum"]] = fixed_seat
        used.add(fixed_seat)

    cared.sort(
        key=lambda p: (
            p["hostnum"] not in assignments,
            len(domains[p["hostnum"]]),
        )
    )

    def place_cared(index: int) -> bool:
        if index >= len(cared):
            return True
        passenger = cared[index]
        passenger_id = passenger["hostnum"]
        passenger_was_assigned = passenger_id in assignments
        passenger_seats = (
            [assignments[passenger_id]]
            if passenger_was_assigned
            else domains[passenger_id]
        )

        for caregiver in caregivers:
            caregiver_id = caregiver["hostnum"]
            caregiver_was_assigned = caregiver_id in assignments
            caregiver_seats = (
                [assignments[caregiver_id]]
                if caregiver_was_assigned
                else domains[caregiver_id]
            )
            for caregiver_seat in caregiver_seats:
                if (
                    not caregiver_was_assigned
                    and caregiver_seat in used
                ):
                    continue
                neighbor_set = set(
                    new_topology.adjacent(caregiver_seat)
                )
                for passenger_seat in passenger_seats:
                    if passenger_seat not in neighbor_set:
                        continue
                    if (
                        not passenger_was_assigned
                        and passenger_seat in used
                    ):
                        continue

                    if not caregiver_was_assigned:
                        assignments[caregiver_id] = caregiver_seat
                        used.add(caregiver_seat)
                    if not passenger_was_assigned:
                        assignments[passenger_id] = passenger_seat
                        used.add(passenger_seat)

                    if place_cared(index + 1):
                        return True

                    if not passenger_was_assigned:
                        used.remove(passenger_seat)
                        del assignments[passenger_id]
                    if not caregiver_was_assigned:
                        used.remove(caregiver_seat)
                        del assignments[caregiver_id]
        return False

    if not place_cared(0):
        return None

    remaining = [
        p for p in passengers if p["hostnum"] not in assignments
    ]
    remaining.sort(key=lambda p: len(domains[p["hostnum"]]))

    def place_remaining(index: int) -> bool:
        if index >= len(remaining):
            return True
        passenger = remaining[index]
        hostnum = passenger["hostnum"]
        for seat_id in domains[hostnum]:
            if seat_id in used:
                continue
            assignments[hostnum] = seat_id
            used.add(seat_id)
            if place_remaining(index + 1):
                return True
            used.remove(seat_id)
            del assignments[hostnum]
        return False

    return assignments if place_remaining(0) else None


def generate_preassignments(
    groups: List[dict],
    old_topology: SeatTopology,
    new_topology: SeatTopology,
    rng: random.Random,
    ratio: float,
) -> int:
    """
    生成不可变新座位。
    为避免制造无解实例：
    - 只预分配普通旅客、UM，或约束可独立判断的 SSR；
    - needCared=Y 的旅客只有在能同时固定其照顾者时才固定；
    - needSingleSideEmpty 的旅客默认不预分配。
    """
    target = round(passenger_count(groups) * ratio)
    occupied: Set[str] = set()
    assigned = 0

    group_order = groups[:]
    rng.shuffle(group_order)

    for g in group_order:
        if assigned >= target:
            break

        # 先构造整组可行安排，再从中抽取固定成员。这样普通陪护者可以单独
        # 固定，但同组需陪护旅客仍至少保留一份满足 SSR 与邻接约束的安排。
        cared = [
            p for p in g["psrs"]
            if p.get("needCared") == "Y" and "newSeat" not in p
        ]
        if cared:
            witness = _care_group_feasible_assignment(
                g, old_topology, new_topology, occupied
            )
            if witness:
                remaining_target = target - assigned
                caregivers = [
                    p
                    for p in g["psrs"]
                    if (
                        p.get("needCared") != "Y"
                        and p.get("ssr", "") == ""
                        and p.get("mandatoryRule", {}).get(
                            "needSingleSideEmpty", "N"
                        ) != "Y"
                        and p.get("mandatoryRule", {}).get(
                            "needBothSideEmpty", "N"
                        ) != "Y"
                        and p["hostnum"] in witness
                    )
                ]
                other_members = [
                    p
                    for p in g["psrs"]
                    if (
                        p not in caregivers
                        and p.get("mandatoryRule", {}).get(
                            "needSingleSideEmpty", "N"
                        ) != "Y"
                        and p.get("mandatoryRule", {}).get(
                            "needBothSideEmpty", "N"
                        ) != "Y"
                    )
                ]
                rng.shuffle(caregivers)
                rng.shuffle(other_members)
                for p in (caregivers + other_members)[:remaining_target]:
                    seat_id = witness[p["hostnum"]]
                    p["newSeat"] = {"seatNum": seat_id}
                    occupied.add(seat_id)
                    assigned += 1

        if assigned >= target:
            break

        if cared:
            continue

        # 再处理不需要照顾且无空侧规则的旅客。
        others = [
            p for p in g["psrs"]
            if "newSeat" not in p
            and p.get("needCared") != "Y"
            and p.get("mandatoryRule", {}).get("needSingleSideEmpty") != "Y"
            and p.get("mandatoryRule", {}).get("needBothSideEmpty") != "Y"
        ]
        rng.shuffle(others)
        for p in others:
            if assigned >= target:
                break
            old = old_topology.seats[p["oldSeat"]["seatNum"]]
            candidates = compatible_unoccupied_seats(
                new_topology, p, occupied
            )
            if not candidates:
                continue
            candidates.sort(key=lambda s: target_candidate_score(old, s))
            chosen = rng.choice(candidates[:min(8, len(candidates))])
            p["newSeat"] = {"seatNum": chosen.seat_id}
            occupied.add(chosen.seat_id)
            assigned += 1

    return assigned


def _source_state_from_groups(
    groups: Sequence[dict], topology: SeatTopology, cfg: Optional[dict] = None,
) -> Tuple[OldAssignmentState, List[str]]:
    """Reconstruct and independently audit occupied/protected/free old seats."""
    errors: List[str] = []
    occupied: Set[str] = set()
    passenger_entries = []
    for group in groups:
        for passenger in group.get("psrs", []):
            key = f"group={group.get('groupId')}, hostnum={passenger.get('hostnum')}"
            seat_id = passenger.get("oldSeat", {}).get("seatNum")
            if not seat_id:
                errors.append(f"{key}: 缺少 oldSeat")
                continue
            if seat_id not in topology.seats:
                errors.append(f"{key}: 旧座位不存在 {seat_id}")
                continue
            if seat_id in occupied:
                errors.append(f"{key}: 旧座位重复 {seat_id}")
            occupied.add(seat_id)
            passenger_entries.append((group, passenger, seat_id, key))
            seat = topology.seats[seat_id]
            if not seat_compatible_with_passenger(seat, passenger):
                errors.append(f"{key}: 旧座位不满足舱等或 SSR 基础约束")

    allow_cross = bool(
        (cfg or {}).get("both_side_empty_allow_cross_aisle", True)
    )
    protected: Set[str] = set()
    single_demands: List[Tuple[str, List[str]]] = []
    for group, passenger, seat_id, key in passenger_entries:
        mandatory = passenger.get("mandatoryRule", {}) or {}
        both = mandatory.get("needBothSideEmpty", "N") == "Y"
        single = mandatory.get("needSingleSideEmpty", "N") == "Y"
        if both and single:
            errors.append(f"{key}: 双侧和单侧保护不能同时启用")
        if both:
            neighbors = topology.adjacent(
                seat_id, allow_cross_aisle=allow_cross
            )
            if len(neighbors) != 2:
                errors.append(f"{key}: 双侧留空旅客没有两个真实邻座")
                continue
            unavailable = set(neighbors) & occupied
            if unavailable:
                errors.append(
                    f"{key}: 双侧保护邻座实际被占用 {sorted(unavailable)}"
                )
            shared = set(neighbors) & protected
            if shared:
                errors.append(
                    f"{key}: 保护空座被多个需求共享 {sorted(shared)}"
                )
            protected.update(neighbors)
        if single:
            neighbors = [
                neighbor for neighbor in topology.adjacent(seat_id)
                if neighbor not in occupied
            ]
            if not neighbors:
                errors.append(f"{key}: 单侧保护没有实际未占用邻座")
            single_demands.append((key, neighbors))

        if passenger.get("needCared") == "Y":
            adjacent = set(topology.adjacent(seat_id))
            caregiver_ok = any(
                other is not passenger
                and other.get("ssr", "") == ""
                and other.get("needCared", "N") != "Y"
                and other.get("mandatoryRule", {}).get(
                    "needBothSideEmpty", "N"
                ) != "Y"
                and other.get("mandatoryRule", {}).get(
                    "needSingleSideEmpty", "N"
                ) != "Y"
                and other.get("oldSeat", {}).get("seatNum") in adjacent
                for other in group.get("psrs", [])
            )
            if not caregiver_ok:
                errors.append(f"{key}: 旧状态缺少同组真实相邻合格 caregiver")

    empty_owner: Dict[str, int] = {}

    def assign_single(index: int, visited: Set[str]) -> bool:
        for seat_id in single_demands[index][1]:
            if seat_id in protected or seat_id in visited:
                continue
            visited.add(seat_id)
            previous = empty_owner.get(seat_id)
            if previous is None or assign_single(previous, visited):
                empty_owner[seat_id] = index
                return True
        return False

    for demand_index in sorted(
        range(len(single_demands)),
        key=lambda index: len(single_demands[index][1]),
    ):
        if not assign_single(demand_index, set()):
            errors.append(
                f"{single_demands[demand_index][0]}: "
                "单侧保护空座不存在或被其他保护需求共享"
            )
    protected.update(empty_owner)
    overlap = occupied & protected
    if overlap:
        errors.append(f"旧状态 occupied/protected 相交: {sorted(overlap)}")
    if not _source_ssr_isolation_valid(groups, topology):
        errors.append("旧状态 conditional SSR row/subrow 规则冲突")
    state = OldAssignmentState(
        occupied_old=occupied,
        protected_empty_old=protected,
        free_old=set(topology.seats) - occupied - protected,
    )
    return state, errors


def validate_source_state(
    groups: Sequence[dict], old_topology: SeatTopology,
    cfg: Optional[dict] = None,
) -> List[str]:
    """Validate old-assignment semantics independently of target new seats."""
    return _source_state_from_groups(groups, old_topology, cfg)[1]


def _is_contiguous_group(
    seat_ids: Sequence[str], topology: SeatTopology,
) -> bool:
    if not seat_ids:
        return False
    rows = {topology.seats[seat_id].row for seat_id in seat_ids}
    if len(rows) != 1:
        return False
    indexes = sorted(topology.row_index[seat_id] for seat_id in seat_ids)
    return indexes == list(range(indexes[0], indexes[-1] + 1))


def generator_diagnostics(
    groups: Sequence[dict], old_topology: SeatTopology,
    cfg: Optional[dict] = None,
) -> dict:
    """Return audit-only diagnostics; these values are not solver features."""
    state, errors = _source_state_from_groups(groups, old_topology, cfg)
    if errors:
        raise ValueError("invalid source state: " + "; ".join(errors[:5]))
    spans = []
    mode_counts: Counter = Counter()
    contiguous = 0
    same_row = 0
    for group in groups:
        seat_ids = [p["oldSeat"]["seatNum"] for p in group.get("psrs", [])]
        rows = [old_topology.seats[seat_id].row for seat_id in seat_ids]
        span = max(rows) - min(rows)
        spans.append(span)
        is_contiguous = _is_contiguous_group(seat_ids, old_topology)
        contiguous += is_contiguous
        same_row += span == 0
        if is_contiguous:
            mode_counts["compact"] += 1
        elif span == 0:
            mode_counts["same_row"] += 1
        elif span <= 2:
            mode_counts["nearby"] += 1
        else:
            mode_counts["scattered"] += 1
    capacities = cabin_seat_capacity(old_topology)
    occupied_by_cabin = Counter(
        old_topology.seats[seat_id].seat_class
        for seat_id in state.occupied_old
    )
    protected_by_cabin = Counter(
        old_topology.seats[seat_id].seat_class
        for seat_id in state.protected_empty_old
    )
    ordered_spans = sorted(spans)
    p90 = ordered_spans[
        max(0, math.ceil(0.9 * len(ordered_spans)) - 1)
    ] if ordered_spans else 0
    return {
        "generatorVersion": GENERATOR_VERSION,
        "profileLabel": "synthetic old-assignment profile",
        "configuredProfile": dict(
            (cfg or {}).get("synthetic_old_assignment_profile", {})
        ),
        "travelerCount": passenger_count(groups),
        "protectedSeatCount": protected_seat_count(groups),
        "resourceDemand": target_seat_demand(groups),
        "oldRealOccupied": len(state.occupied_old),
        "oldProtectedEmpty": len(state.protected_empty_old),
        "oldOrdinaryEmpty": len(state.free_old),
        "cabinLoadFactors": {
            cabin: {
                "capacity": capacity,
                "occupied": occupied_by_cabin[cabin],
                "protectedEmpty": protected_by_cabin[cabin],
                "resourceLoadFactor": (
                    (occupied_by_cabin[cabin] + protected_by_cabin[cabin])
                    / capacity if capacity else 0.0
                ),
            }
            for cabin, capacity in sorted(capacities.items())
        },
        "groupPlacementModeCounts": {
            mode: mode_counts[mode]
            for mode in ("compact", "same_row", "nearby", "scattered")
        },
        "contiguousGroupRatio": contiguous / len(groups) if groups else 0.0,
        "sameRowRatio": same_row / len(groups) if groups else 0.0,
        "rowSpanMedian": statistics.median(spans) if spans else 0.0,
        "rowSpanP90": p90,
    }


def validate_case(
    groups: List[dict],
    old_topology: SeatTopology,
    new_topology: SeatTopology,
    cfg: Optional[dict] = None,
) -> List[str]:
    errors: List[str] = validate_source_state(groups, old_topology, cfg)
    for group in groups:
        cabins = {
            cabin_class_for_passenger(passenger)
            for passenger in group.get("psrs", [])
        }
        if len(cabins) > 1:
            errors.append(
                f"同行组 {group.get('groupId')} 含多个舱位: "
                f"{sorted(cabins)}"
            )
    old_seen: Set[str] = set()
    new_seen: Set[str] = set()
    demand = target_seat_demand(groups)
    if demand > len(new_topology.seats):
        errors.append(
            f"目标座位需求 {demand} 超过目标机型容量 "
            f"{len(new_topology.seats)}"
        )
    target_cabin_capacity = cabin_seat_capacity(new_topology)
    for cabin_class, cabin_demand in cabin_seat_demand(groups).items():
        capacity = target_cabin_capacity[cabin_class]
        if cabin_demand > capacity:
            errors.append(
                f"{cabin_class}舱位需求 {cabin_demand} "
                f"超过目标机型该舱位容量 {capacity}"
            )
    allow_cross = bool(
        (cfg or {}).get("both_side_empty_allow_cross_aisle", True)
    )
    globally_fixed = {
        p["newSeat"]["seatNum"]
        for g in groups
        for p in g["psrs"]
        if p.get("newSeat", {}).get("seatNum") in new_topology.seats
    }

    ssr_by_cabin: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for group in groups:
        for passenger in group["psrs"]:
            ssr = passenger.get("ssr", "")
            if ssr:
                ssr_by_cabin[(
                    ssr, cabin_class_for_passenger(passenger)
                )].append(passenger)
    for (ssr, cabin_class), passengers in ssr_by_cabin.items():
        compatible = [
            seat for seat in new_topology.seats.values()
            if seat.seat_class == cabin_class
            and seat_compatible_with_ssr(seat, ssr)
        ]
        row_count = len({seat.row for seat in compatible})
        subrow_count = len({
            new_topology.subrow_id[seat.seat_id]
            for seat in compatible
        })
        if (
            len(passengers) > row_count
            and any(
                passenger.get("mandatoryRule", {}).get(
                    "sameRowNoOtherSSR", "N"
                ) == "Y"
                for passenger in passengers
            )
        ):
            errors.append(
                f"{cabin_class}舱{ssr}同排隔离需求超过目标机型"
                f"兼容排资源 {row_count}"
            )
        if (
            len(passengers) > subrow_count
            and any(
                passenger.get("mandatoryRule", {}).get(
                    "sameSubRowNoOtherSSR", "N"
                ) == "Y"
                for passenger in passengers
            )
        ):
            errors.append(
                f"{cabin_class}舱{ssr}同子排隔离需求超过目标机型"
                f"兼容子排资源 {subrow_count}"
            )

    for g in groups:
        if not g["psrs"]:
            errors.append(f"group {g['groupId']} 为空")
            continue

        for p in g["psrs"]:
            key = f"group={g['groupId']}, hostnum={p['hostnum']}"
            old_id = p.get("oldSeat", {}).get("seatNum")
            if not old_id:
                continue
            if old_id not in old_topology.seats:
                errors.append(f"{key}: 旧座位不存在 {old_id}")
                continue
            if old_id in old_seen:
                errors.append(f"{key}: 旧座位重复 {old_id}")
            old_seen.add(old_id)

            mandatory = p.get("mandatoryRule", {})
            both = mandatory.get("needBothSideEmpty", "N") == "Y"
            single = mandatory.get("needSingleSideEmpty", "N") == "Y"

            if both and single:
                errors.append(f"{key}: needBothSideEmpty 与 needSingleSideEmpty 同时为 Y")
            if p.get("needCared") == "Y" and (both or single):
                errors.append(f"{key}: 需要照顾与旁侧留空规则冲突")
            if both:
                neighbors = old_topology.adjacent(
                    old_id, allow_cross_aisle=allow_cross
                )
                if len(neighbors) != 2:
                    errors.append(f"{key}: 双侧留空旅客没有两个真实邻座")
            if single and not old_topology.adjacent(old_id):
                errors.append(f"{key}: 单侧留空旅客没有同侧物理邻座")

            ssr_profile = SSR_PROFILES.get(p.get("ssr", ""), {})
            if (
                ssr_profile.get("needCared") == "Y"
                and p.get("needCared") != "Y"
            ):
                errors.append(f"{key}: {p.get('ssr')} 应设置 needCared=Y")

            if p.get("needCared") == "Y":
                adj = set(old_topology.adjacent(old_id))
                caregiver_ok = any(
                    q["hostnum"] != p["hostnum"]
                    and q.get("ssr", "") == ""
                    and q.get("mandatoryRule", {}).get("needBothSideEmpty", "N") != "Y"
                    and q.get("mandatoryRule", {}).get("needSingleSideEmpty", "N") != "Y"
                    and q["oldSeat"]["seatNum"] in adj
                    for q in g["psrs"]
                )
                if not caregiver_ok:
                    errors.append(f"{key}: 缺少同组相邻普通成人")

            if not seat_compatible_with_passenger(
                old_topology.seats[old_id], p
            ):
                errors.append(f"{key}: 旧座位不满足舱等或 SSR 基础约束")

            if "newSeat" in p:
                new_id = p["newSeat"]["seatNum"]
                if new_id not in new_topology.seats:
                    errors.append(f"{key}: 新座位不存在 {new_id}")
                elif new_id in new_seen:
                    errors.append(f"{key}: 新座位重复 {new_id}")
                else:
                    new_seen.add(new_id)
                    if not seat_compatible_with_passenger(new_topology.seats[new_id], p):
                        errors.append(f"{key}: 新座位不满足舱位或 SSR 基础约束")
                    if both and len(
                        new_topology.adjacent(
                            new_id, allow_cross_aisle=allow_cross
                        )
                    ) != 2:
                        errors.append(
                            f"{key}: 固定新座位不能提供两个真实邻座"
                        )
                    if single and not new_topology.adjacent(new_id):
                        errors.append(
                            f"{key}: 固定新座位没有同侧物理邻座"
                        )
        if any(p.get("needCared") == "Y" for p in g["psrs"]):
            witness = _care_group_feasible_assignment(
                g, old_topology, new_topology, globally_fixed
            )
            if witness is None:
                errors.append(
                    f"group={g['groupId']}: "
                    "固定座位使该陪护组不存在完整可行安排"
                )

    return errors


def load_seatmap(path: Path) -> SeatTopology:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是 JSON 对象")
    seats = data.get("seats")
    if not isinstance(seats, list):
        raise ValueError(f"{path} 中缺少 seats 数组")
    return SeatTopology(seats)


def main() -> None:
    parser = argparse.ArgumentParser(description="为任意兼容座位图生成可复现的旅客组数据")
    parser.add_argument(
        "--old-seatmap", default=str(PROJECT_ROOT / "data" / "3-4-3seatmap.json"),
        help="旧机型座位图路径（不限制具体布局）",
    )
    parser.add_argument(
        "--new-seatmap", default=str(PROJECT_ROOT / "data" / "3-3seatmap.json"),
        help="目标机型座位图路径（不限制具体布局）",
    )
    parser.add_argument(
        "--output",
        help=(
            "输出路径；省略时写入 data/generator_v3_2/<人数>_groups.json，"
            "请求达到机型容量时使用 generator_v3_2/full_groups.json"
        ),
    )
    parser.add_argument("--passengers", type=int, default=150)
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--seed", type=int, default=40)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    old_topology = load_seatmap(Path(args.old_seatmap))
    new_topology = load_seatmap(Path(args.new_seatmap))
    cfg = SCENARIOS[args.scenario]

    joint_diagnostics: dict = {}
    groups = generate_groups(
        old_topology, args.passengers, rng, cfg, target_topology=new_topology,
        diagnostics_out=joint_diagnostics,
    )
    preassigned = generate_preassignments(
        groups, old_topology, new_topology, rng, cfg["preassign_ratio"]
    )

    errors = validate_case(groups, old_topology, new_topology, cfg)
    if errors:
        preview = "\n".join(f"- {x}" for x in errors[:30])
        raise RuntimeError(
            f"生成结果未通过自检，共 {len(errors)} 个问题：\n{preview}"
        )

    traveler_count = passenger_count(groups)
    counted_passengers = target_seat_demand(groups)
    passenger_capacity = transferable_seat_capacity(
        old_topology, new_topology
    )
    size_label = test_case_size_label(
        args.passengers,
        counted_passengers,
        passenger_capacity,
    )
    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT / "data" / "generator_v3_2" / f"{size_label}_groups.json"
    )
    diagnostics = generator_diagnostics(groups, old_topology, cfg)
    output = {
        "generatorVersion": GENERATOR_VERSION,
        "requestedPassengerCount": args.passengers,
        "passengerCount": counted_passengers,
        "travelerCount": traveler_count,
        "passengerCapacity": passenger_capacity,
        "sizeLabel": size_label,
        "targetSeatDemand": counted_passengers,
        "generatorDiagnostics": diagnostics,
        "jointSpecialResourceDiagnostics": joint_diagnostics,
        "groups": groups,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    actual_special = sum(
        1 for g in groups for p in g["psrs"] if p.get("ssr")
    )
    actual_both_empty = sum(
        p.get("mandatoryRule", {}).get("needBothSideEmpty") == "Y"
        for g in groups for p in g["psrs"]
    )
    print(f"输出文件：{output_path}")
    print(f"场景：{args.scenario}，随机种子：{args.seed}")
    if counted_passengers != args.passengers:
        print(
            f"容量调整：请求 {args.passengers} 人，"
            f"实际计入 {counted_passengers} 人"
        )
    print(f"同行组：{len(groups)}")
    print(f"真实旅客数：{traveler_count}")
    print(f"计入人数：{counted_passengers}")
    print(
        f"目标座位需求（含占座/保护留空）："
        f"{counted_passengers}/{len(new_topology.seats)}"
    )
    print(f"SSR 旅客：{actual_special}")
    print(f"双侧留空旅客：{actual_both_empty}")
    if actual_both_empty == 0 and cfg.get("minimum_both_side_empty", 0) > 0:
        print("提示：旧机型或目标机型没有双侧真实邻座，已跳过不可满足的双侧留空规则")
    print(f"预分配旅客：{preassigned}")
    print("数据一致性检查：通过")


if __name__ == "__main__":
    main()
