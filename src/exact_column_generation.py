#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""与 allocation_evaluator.py 等价的两层精确列生成与 branch-and-price。

这是项目的列生成实现：

* MP1 的列是一个同行组的完整方案；所有普通旅客都必须分配真实座位；
* ``newSeat`` 旅客必须安排到指定座位，并在建模前全局预占资源；
* MP2 在完整可行座位/留空域上定价，不读取截断候选池；
* 所有同行组统一由完整域 HiGHS MILP 精确定价；
* MP1 在每个分支节点重新精确定价，只有整棵树关闭后才报告整数最优；
* 任一节点达到时间或节点上限时，``proven_optimal`` 必须为 False。

统一的MP2把同行跨度、缺失邻接、空洞、陪护关系和同组婴儿影响
全部精确线性化。MP1每轮先做快速多列定价；只有快速阶段没有改善列后，才对
所有同行组执行精确定价，且全部证明不存在负检验数列时才认证完整列空间LP界。
"""

from __future__ import annotations

import argparse
import copy
import heapq
import itertools
import json
import math
import os
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import highspy
import numpy as np
from scipy.optimize import linear_sum_assignment

try:
    from . import allocation_evaluator as evaluator
    from . import heuristic_seat_allocator as heuristic_allocator
except ImportError:
    import allocation_evaluator as evaluator
    import heuristic_seat_allocator as heuristic_allocator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PassengerKey = Tuple[int, int]
SsrResource = Tuple[str, Any, str]  # ("row"/"subrow", location, ssr)
BabyPair = Tuple[str, str]
PlacementSignature = Tuple[int, str, Tuple[str, ...]]


def _progress(
    config: Mapping[str, Any], message: str, *, detail: bool = False,
) -> None:
    """输出带时间戳的进度。

    ``detail`` 用于逐组定价等高频信息，默认关闭，避免长任务产生几十
    MB 日志；错误、节点摘要和认证结果仍始终输出。
    """
    cfg = config.get("column_generation", {})
    if (
        bool(cfg.get("show_progress", True))
        and (
            not detail
            or bool(cfg.get("verbose_pricing_progress", False))
        )
    ):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}", flush=True)


def _new_highs() -> highspy.Highs:
    """创建单线程对偶单纯形 HiGHS 实例，允许组级并行且避免过度订阅。"""
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.setOptionValue("random_seed", 0)
    solver.setOptionValue("threads", 1)
    solver.setOptionValue("solver", "simplex")
    solver.setOptionValue("simplex_strategy", 1)
    return solver


def _json_safe(value: Any) -> Any:
    """把元组键及 NumPy 标量递归转换成 JSON 可序列化对象。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


@dataclass(frozen=True)
class Placement:
    """MP2的一列：某位旅客的真实座位选择及其独占资源。"""
    passenger_index: int
    passenger_key: PassengerKey
    seat_id: str
    blocked: Tuple[str, ...]
    resources: Tuple[str, ...]
    individual_cost: float
    ssr_resources: Tuple[SsrResource, ...]
    ssr_flag_locations: Tuple[Tuple[str, Any], ...]
    is_infant: bool

    @property
    def signature(self) -> PlacementSignature:
        """返回用于定价、分支和去重的稳定列标识。"""
        return self.passenger_index, self.seat_id, self.blocked


@dataclass(frozen=True)
class ExactPattern:
    """MP1的一列：一个同行组的完整整数保护方案及全部主问题系数。"""
    group_id: int
    placements: Tuple[Placement, ...]
    assignments: Tuple[Tuple[PassengerKey, str], ...]
    blocked_by: Tuple[Tuple[str, PassengerKey], ...]
    seat_resources: FrozenSet[str]
    ssr_all: Tuple[Tuple[SsrResource, int], ...]
    ssr_flagged: Tuple[Tuple[SsrResource, int], ...]
    infant_seats: FrozenSet[str]
    occupied_seats: FrozenSet[str]
    master_cost: float

    @property
    def signature(self) -> Tuple[PlacementSignature, ...]:
        """用组内全部旅客放置唯一标识方案。"""
        return tuple(item.signature for item in self.placements)


@dataclass
class MasterDuals:
    """MP1传给MP2的全部对偶族，字段与主问题行逐一对应。"""
    group: Dict[int, float] = field(default_factory=dict)
    seat: Dict[str, float] = field(default_factory=dict)
    # 条件SSR激活行按“同行组+位置+受限SSR类型”区分。
    ssr_flag: Dict[tuple, float] = field(default_factory=dict)
    ssr_all: Dict[SsrResource, float] = field(default_factory=dict)
    baby_lower: Dict[BabyPair, float] = field(default_factory=dict)
    baby_infant_upper: Dict[BabyPair, float] = field(default_factory=dict)
    baby_occupant_upper: Dict[BabyPair, float] = field(default_factory=dict)


def _serialize_master_duals(duals: MasterDuals) -> Dict[str, list]:
    """Convert all dual families to stable JSON records for pricing replay."""
    def records(values: Mapping[Any, float]) -> list:
        return [
            {
                "key": list(key) if isinstance(key, tuple) else key,
                "value": float(value),
            }
            for key, value in sorted(
                values.items(), key=lambda item: repr(item[0])
            )
        ]

    return {
        "group": records(duals.group),
        "seat": records(duals.seat),
        "ssr_flag": records(duals.ssr_flag),
        "ssr_all": records(duals.ssr_all),
        "baby_lower": records(duals.baby_lower),
        "baby_infant_upper": records(duals.baby_infant_upper),
        "baby_occupant_upper": records(duals.baby_occupant_upper),
    }


def _deserialize_master_duals(payload: Mapping[str, list]) -> MasterDuals:
    """Restore a pricing replay snapshot produced by `_serialize_master_duals`."""
    def freeze(value: Any) -> Any:
        return tuple(freeze(item) for item in value) if isinstance(value, list) else value

    def mapping(name: str, *, tuple_key: bool) -> Dict[Any, float]:
        result: Dict[Any, float] = {}
        for record in payload.get(name, []):
            key = record["key"]
            if tuple_key:
                key = freeze(key)
            result[key] = float(record["value"])
        return result

    return MasterDuals(
        group={int(key): value for key, value in mapping(
            "group", tuple_key=False
        ).items()},
        seat={str(key): value for key, value in mapping(
            "seat", tuple_key=False
        ).items()},
        ssr_flag=mapping("ssr_flag", tuple_key=True),
        ssr_all=mapping("ssr_all", tuple_key=True),
        baby_lower=mapping("baby_lower", tuple_key=True),
        baby_infant_upper=mapping(
            "baby_infant_upper", tuple_key=True
        ),
        baby_occupant_upper=mapping(
            "baby_occupant_upper", tuple_key=True
        ),
    )


def _smooth_master_duals(
    current: MasterDuals,
    previous: Optional[MasterDuals],
    alpha: float,
) -> MasterDuals:
    """
    对全部 MP1 对偶族做指数平滑，仅供启发式候选列发现。

    完整列空间认证始终使用 ``current``，因此平滑不会改变 LP 上界。
    """
    if previous is None or alpha >= 1.0:
        return current
    alpha = min(1.0, max(0.0, float(alpha)))

    def blend(
        current_values: Mapping[Any, float],
        previous_values: Mapping[Any, float],
    ) -> Dict[Any, float]:
        keys = current_values.keys() | previous_values.keys()
        return {
            key: (
                alpha * float(current_values.get(key, 0.0))
                + (1.0 - alpha) * float(previous_values.get(key, 0.0))
            )
            for key in keys
        }

    return MasterDuals(
        group=blend(current.group, previous.group),
        seat=blend(current.seat, previous.seat),
        ssr_flag=blend(current.ssr_flag, previous.ssr_flag),
        ssr_all=blend(current.ssr_all, previous.ssr_all),
        baby_lower=blend(current.baby_lower, previous.baby_lower),
        baby_infant_upper=blend(
            current.baby_infant_upper, previous.baby_infant_upper
        ),
        baby_occupant_upper=blend(
            current.baby_occupant_upper, previous.baby_occupant_upper
        ),
    )


@dataclass
class PricingResult:
    """一次同行组精确定价的最优列、证明状态及性能统计。"""
    patterns: Tuple[ExactPattern, ...]
    reduced_cost: float
    proven_optimal: bool
    nodes: int
    priced_placements: int
    elapsed: float
    termination: str
    complete_domain_mip_solves: int
    # 完整MP2最小检验数的安全下界。未获得有效证明界时保持 -inf；
    # 该值与当前找到的可行列检验数不同，后者是上界。
    reduced_cost_lower_bound: float = -math.inf
    dfs_nodes: int = 0
    dfs_elapsed: float = 0.0
    equivalence_error_detail: Optional[Dict[str, Any]] = None
    dfs_incumbent_seeded: bool = False
    dfs_bound_prunes: int = 0
    dfs_resource_prunes: int = 0
    dfs_symmetry_prunes: int = 0
    dfs_symmetry_classes: int = 0
    label_dp_used: bool = False
    label_dp_attempted: bool = False
    label_dp_states: int = 0
    label_dp_type_count: int = 0
    label_dp_reason: Optional[str] = None
    dfs_exact_certificate: bool = False
    dfs_exact_certificate_seconds_saved: float = 0.0
    mip_avoided_after_dfs_certificate: bool = False
    singleton_pricing_calls: int = 0
    pair_pricing_calls: int = 0
    specialized_pricing_elapsed: float = 0.0
    pairs_examined: int = 0
    pairs_resource_feasible: int = 0
    pairs_exact_feasible: int = 0
    pair_rc_seconds: float = 0.0
    pair_heap_seconds: float = 0.0
    pair_pattern_materialization_seconds: float = 0.0
    pair_patterns_materialized: int = 0
    pair_patterns_returned: int = 0
    pair_pricing_seconds: float = 0.0
    mp2_model_build_seconds: float = 0.0
    mp2_solve_seconds: float = 0.0
    mp2_objective_update_seconds: float = 0.0
    mp2_model_builds: int = 0
    mp2_model_reuses: int = 0
    historical_reprice_calls: int = 0
    historical_patterns_checked: int = 0
    historical_negative_hits: int = 0
    historical_columns_returned: int = 0
    dfs_calls_avoided_by_history: int = 0
    historical_reprice_seconds: float = 0.0
    dfs_negative_patterns_seen: int = 0
    dfs_unique_negative_patterns: int = 0
    dfs_patterns_returned: int = 0
    dfs_seconds_per_returned_column: float = 0.0
    dfs_workspace_builds: int = 0
    dfs_workspace_reuses: int = 0
    dfs_workspace_build_seconds: float = 0.0
    dfs_dynamic_refresh_seconds: float = 0.0

    @property
    def best_pattern(self) -> Optional[ExactPattern]:
        """返回检验数最小的首个方案；无可行方案时返回 ``None``。"""
        return self.patterns[0] if self.patterns else None


@dataclass
class GroupPricingCache:
    """同一同行组跨MP1迭代复用的完整放置域和稀疏几何模板。"""
    all_options: List[List[Placement]]
    universe: List[Placement]
    seat_ids: Tuple[str, ...]
    row_coordinate: Dict[str, float]
    row_big_m: float
    x_coordinate: Dict[str, float]
    x_big_m: float
    adjacency_edges: Tuple[Tuple[str, str], ...]
    hole_specs: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...]
    # 上一轮可行整数放置在对偶变化后仍是合法的 MP2 MIP start。
    historical_mip_start: Optional[Tuple[PlacementSignature, ...]] = None
    mip_start_submissions: int = 0
    label_dp_attempted: bool = False
    label_dp_states: int = 0
    label_dp_type_count: int = 0
    label_dp_reason: Optional[str] = None
    complete_domain_mip_elapsed: float = 0.0
    complete_domain_mip_calls: int = 0
    persistent_mp2: Any = None
    strict_lower_bound: Optional[float] = None
    strict_lower_bound_duals: Optional[MasterDuals] = None
    strict_lower_bound_anchors: List[Tuple[float, MasterDuals]] = field(
        default_factory=list
    )
    # Patterns are evaluator-validated complete columns. Their reduced costs
    # are always recomputed under the current duals before reuse.
    historical_patterns: Dict[
        Tuple[PlacementSignature, ...], ExactPattern
    ] = field(default_factory=dict)
    # Only dual-independent DFS structures live here. Cost bounds, dominance
    # states and certificates are rebuilt for every pricing call.
    dfs_workspace: Any = None


@dataclass
class AdaptiveDualStabilizer:
    """Wentges式自适应对偶平滑状态。

    平滑对偶只负责产生候选列；每次停止认证仍用当前RMP原始对偶做
    完整定价，因此该机制只改变收敛路径，不改变最终LP证书。
    """

    alpha: float = 0.7
    minimum_alpha: float = 0.15
    maximum_alpha: float = 0.95
    success_factor: float = 0.85
    null_step_increment: float = 0.10
    adaptive: bool = True
    center: Optional[MasterDuals] = None
    best_rmp_cost: float = math.inf
    serious_steps: int = 0
    null_steps: int = 0
    alpha_history: List[float] = field(default_factory=list)

    def pricing_duals(
        self, current: MasterDuals, rmp_cost: float,
    ) -> MasterDuals:
        tolerance = 1e-9 * max(1.0, abs(float(rmp_cost)))
        if self.center is None or rmp_cost < self.best_rmp_cost - tolerance:
            self.center = current
            self.best_rmp_cost = float(rmp_cost)
            self.serious_steps += 1
        self.alpha_history.append(float(self.alpha))
        return _smooth_master_duals(current, self.center, self.alpha)

    def observe(self, generated_columns: bool) -> None:
        if not self.adaptive:
            return
        if generated_columns:
            self.alpha = max(
                self.minimum_alpha, self.alpha * self.success_factor
            )
        else:
            self.null_steps += 1
            self.alpha = min(
                self.maximum_alpha,
                self.alpha + self.null_step_increment,
            )


@dataclass(frozen=True)
class CompactnessCosts:
    """PDF重心最大偏差目标及其安全矩形下界系数。"""

    centroid_x: float
    centroid_y: float

    @property
    def row_span(self) -> float:
        # 任意点集到重心的最大纵向偏差至少为纵向极差的一半。
        return self.centroid_y / 2.0

    @property
    def column_span(self) -> float:
        # 任意点集到重心的最大横向偏差至少为横向极差的一半。
        return self.centroid_x / 2.0

@dataclass(frozen=True)
class FixedSeatContext:
    """固定座位预处理结果。

    ``newSeat`` 表示已经在目标机型上安排且不可改变。确定的双侧留空资源
    可在所有组定价前全局预占；单侧留空的左右方向仍由该旅客所在组的
    MP2 决定。
    """

    owner_by_seat: Mapping[str, PassengerKey]
    deterministic_blocked: FrozenSet[str]

    @property
    def globally_reserved(self) -> FrozenSet[str]:
        return frozenset(self.owner_by_seat) | self.deterministic_blocked


@dataclass
class MasterResult:
    """一次MP1 LP求解结果，包括活动方案值和完整行对偶。"""
    feasible: bool
    objective_cost: float = math.inf
    pattern_values: Dict[Tuple[int, tuple], float] = field(default_factory=dict)
    duals: MasterDuals = field(default_factory=MasterDuals)
    artificial_value: float = 0.0


def _limits(config: Mapping[str, Any]) -> dict:
    """读取branch-and-price限制，并为缺省项提供保守默认值。"""
    cfg = config.get("column_generation", {})
    return {
        "mp2_node_limit": int(cfg.get("mp2_node_limit", 2_000_000)),
        "mp2_time_limit": float(cfg.get("mp2_time_limit", 120.0)),
        "mp1_node_limit": int(cfg.get("mp1_node_limit", 10_000)),
        "total_time_limit": float(cfg.get("total_time_limit", 3600.0)),
        "tolerance": float(cfg.get("tolerance", 1e-7)),
        "pricing_workers": max(
            1,
            int(cfg.get("pricing_workers", min(4, os.cpu_count() or 1))),
        ),
    }


def _validate_proof_assumptions(
    weights: Mapping[str, float], config: Mapping[str, Any]
) -> None:
    """验证MP2下界推导所依赖的符号条件。

    MP2 LP为提高速度会删除非负的同行紧凑度成本，并用婴儿成本的乐观
    常数作为下界。只有相关权重和系数满足下面的符号条件时，分支剪枝
    才具备数学证明效力；不满足时必须拒绝运行，而不是输出伪最优证明。
    """
    algorithm = config.get("algorithm", {})
    checks = {
        "weights.w_c must be <= 0": float(weights.get("w_c", -2.0)) <= 0,
        "weights.w_b must be <= 0": float(weights.get("w_b", -0.3)) <= 0,
        "algorithm.group_centroid_x_factor must be >= 0": (
            float(algorithm.get("group_centroid_x_factor", 1.0)) >= 0
        ),
        "algorithm.group_centroid_y_factor must be >= 0": (
            float(algorithm.get("group_centroid_y_factor", 1.0)) >= 0
        ),
        "algorithm.baby_front_back_factor must be >= 0": (
            float(algorithm.get("baby_front_back_factor", 1.0)) >= 0
        ),
        "penalty.unassigned must be >= 0": (
            float(config.get("penalty", {}).get("unassigned", 300.0)) >= 0
        ),
    }
    failed = [message for message, valid in checks.items() if not valid]
    if failed:
        raise ValueError(
            "精确branch-and-price的下界假设不成立: " + "; ".join(failed)
        )


def _mandatory(passenger: dict) -> dict:
    """规范化旅客强制规则，防止异常输入类型扩散到定价逻辑。"""
    value = passenger.get("mandatoryRule", {}) or {}
    return value if isinstance(value, dict) else {}


def _caregiver_cross_aisle(
    passenger: Mapping[str, Any], config: Mapping[str, Any]
) -> Optional[bool]:
    """返回陪护邻接是否允许跨过道；不需要陪护时返回 ``None``。"""
    rule = evaluator._ssr_rule(str(passenger.get("ssr", "")), config)
    strict = passenger.get("needCared", "N") == "Y"
    if not strict and not rule.get("requires_caregiver", False):
        return None
    if strict and not passenger.get("ssr"):
        return False
    return bool(rule.get("caregiver_allow_cross_aisle", False))


def _ssr_resources(
    passenger: Mapping[str, Any],
    seat_id: str,
    topology: evaluator.SeatTopology,
) -> Tuple[SsrResource, SsrResource]:
    """返回一个已分配SSR旅客在MP1占用的排与小排资源。"""
    ssr = str(passenger.get("ssr", ""))
    return (
        ("row", int(topology.seat_map[seat_id]["row"]), ssr),
        ("subrow", topology.subrow[seat_id], ssr),
    )


def _preprocess_fixed_seats(
    groups: Sequence[dict],
    topology: evaluator.SeatTopology,
    config: Mapping[str, Any],
) -> FixedSeatContext:
    """校验并预占目标机型上不可改变的固定座位。

    这里仅预占对所有方案都确定的资源。固定旅客的单侧留空方向仍是
    组内决策，不能提前任意选择左侧或右侧。
    """
    owner_by_seat: Dict[str, PassengerKey] = {}
    fixed_items: List[Tuple[PassengerKey, dict, str]] = []
    mandatory_cfg = config.get("mandatory_rules", {})
    both_cross = bool(
        mandatory_cfg.get("both_side_empty_allow_cross_aisle", False)
    )
    require_two = bool(mandatory_cfg.get("require_two_real_neighbors", True))

    for group in groups:
        gid = int(group["groupId"])
        for passenger in group.get("psrs", []):
            seat_id = (passenger.get("newSeat") or {}).get("seatNum")
            if not seat_id:
                continue
            key = (gid, int(passenger["hostnum"]))
            if seat_id not in topology.seat_map:
                raise ValueError(
                    f"固定座位不存在: passenger={key}, seat={seat_id}"
                )
            if seat_id in owner_by_seat:
                raise ValueError(
                    f"固定座位重复占用: seat={seat_id}, "
                    f"passengers={owner_by_seat[seat_id]},{key}"
                )
            seat = topology.seat_map[seat_id]
            rule = evaluator._ssr_rule(passenger.get("ssr", ""), config)
            if seat.get("isExitRow", False) and not rule.get(
                "allow_exit_row", False
            ):
                raise ValueError(
                    f"固定座位违反出口排限制: passenger={key}, seat={seat_id}"
                )
            if rule.get("requires_bassinet", False) and not seat.get(
                "hasBassinet", False
            ):
                raise ValueError(
                    f"固定座位不满足摇篮要求: passenger={key}, seat={seat_id}"
                )
            if rule.get("requires_aisle", False) and not seat.get(
                "isAisle", False
            ):
                raise ValueError(
                    f"固定座位不满足过道要求: passenger={key}, seat={seat_id}"
                )
            owner_by_seat[seat_id] = key
            fixed_items.append((key, passenger, seat_id))

    deterministic_blocked: Set[str] = set()
    occupied = set(owner_by_seat)
    for key, passenger, seat_id in fixed_items:
        mandatory = _mandatory(passenger)
        if mandatory.get("needBothSideEmpty", "N") != "Y":
            continue
        neighbors = topology.row_neighbors(
            seat_id, allow_cross_aisle=both_cross
        )
        if require_two and len(neighbors) != 2:
            raise ValueError(
                f"固定旅客双侧留空缺少两个真实邻座: "
                f"passenger={key}, seat={seat_id}"
            )
        if not neighbors:
            raise ValueError(
                f"固定旅客双侧留空没有邻座: passenger={key}, seat={seat_id}"
            )
        conflicts = occupied.intersection(neighbors)
        if conflicts:
            raise ValueError(
                f"固定座位与固定双侧留空冲突: passenger={key}, "
                f"seat={seat_id}, conflicts={sorted(conflicts)}"
            )
        deterministic_blocked.update(neighbors)

    for key, passenger, seat_id in fixed_items:
        mandatory = _mandatory(passenger)
        if mandatory.get("needSingleSideEmpty", "N") == "Y":
            neighbors = topology.subrow_neighbors(seat_id)
            if not neighbors or all(neighbor in occupied for neighbor in neighbors):
                raise ValueError(
                    f"固定旅客单侧留空没有可用邻座: "
                    f"passenger={key}, seat={seat_id}"
                )
        allow_cross = _caregiver_cross_aisle(passenger, config)
        if allow_cross is not None:
            group = next(
                group for group in groups if int(group["groupId"]) == key[0]
            )
            adults = [
                other for other in group.get("psrs", [])
                if int(other["hostnum"]) != key[1]
                and evaluator._is_adult_caregiver(other)
            ]
            if not adults:
                raise ValueError(
                    f"固定受照顾旅客没有合格陪护者: passenger={key}"
                )
            fixed_adults = [
                other for other in adults
                if (other.get("newSeat") or {}).get("seatNum")
            ]
            if len(fixed_adults) == len(adults):
                neighbors = set(topology.row_neighbors(
                    seat_id, allow_cross_aisle=allow_cross
                ))
                if not any(
                    (adult.get("newSeat") or {}).get("seatNum") in neighbors
                    for adult in fixed_adults
                ):
                    raise ValueError(
                        f"固定受照顾旅客与固定陪护者不相邻: passenger={key}"
                    )

    # 任一标记SSR会激活该位置“每种SSR至多1人”的评分器语义。
    for kind in ("row", "subrow"):
        flagged_locations: Set[Any] = set()
        counts: Dict[Any, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for _, passenger, seat_id in fixed_items:
            ssr = passenger.get("ssr", "")
            if not ssr:
                continue
            location = (
                int(topology.seat_map[seat_id]["row"])
                if kind == "row" else topology.subrow[seat_id]
            )
            counts[location][ssr] += 1
            mandatory_key = (
                "sameRowNoOtherSSR"
                if kind == "row" else "sameSubRowNoOtherSSR"
            )
            if _mandatory(passenger).get(mandatory_key, "N") == "Y":
                flagged_locations.add(location)
        for location in flagged_locations:
            conflicts = {
                ssr: count for ssr, count in counts[location].items()
                if count > 1
            }
            if conflicts:
                raise ValueError(
                    f"固定SSR条件{kind}冲突: location={location}, "
                    f"counts={conflicts}"
                )

    return FixedSeatContext(
        owner_by_seat=dict(owner_by_seat),
        deterministic_blocked=frozenset(deterministic_blocked),
    )


def _placement_options(
    group: dict,
    new_topology: evaluator.SeatTopology,
    old_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    fixed_context: Optional[FixedSeatContext] = None,
) -> List[List[Placement]]:
    """生成完整放置列：所有真实可行座位及所有合法的保护空座选择。"""
    gid = int(group["groupId"])
    mandatory_cfg = config.get("mandatory_rules", {})
    both_cross = bool(mandatory_cfg.get("both_side_empty_allow_cross_aisle", False))
    require_two = bool(mandatory_cfg.get("require_two_real_neighbors", True))
    result: List[List[Placement]] = []
    fixed_context = fixed_context or FixedSeatContext({}, frozenset())
    globally_reserved = fixed_context.globally_reserved

    for pi, passenger in enumerate(group.get("psrs", [])):
        key = (gid, int(passenger["hostnum"]))
        ssr_rule = evaluator._ssr_rule(passenger.get("ssr", ""), config)
        fixed = (passenger.get("newSeat") or {}).get("seatNum")
        # 数据生成阶段已经保证总占用量不超过机型容量；普通旅客也必须就座。
        options: List[Placement] = []
        mandatory = _mandatory(passenger)
        both = mandatory.get("needBothSideEmpty", "N") == "Y"
        single = mandatory.get("needSingleSideEmpty", "N") == "Y"

        for seat_id, seat in new_topology.seat_map.items():
            if fixed and seat_id != fixed:
                continue
            if not fixed and seat_id in globally_reserved:
                continue
            expected_class = evaluator.expected_cabin_class(passenger)
            if (
                expected_class is not None
                and seat.get("seatClass") != expected_class
            ):
                continue
            if seat.get("isExitRow", False) and not ssr_rule.get("allow_exit_row", False):
                continue
            if ssr_rule.get("requires_bassinet", False) and not seat.get("hasBassinet", False):
                continue
            if ssr_rule.get("requires_aisle", False) and not seat.get("isAisle", False):
                continue

            blocked_modes: List[Tuple[str, ...]] = [()]
            if both:
                neighbors = new_topology.row_neighbors(seat_id, allow_cross_aisle=both_cross)
                if require_two and len(neighbors) != 2:
                    continue
                if not neighbors:
                    continue
                if any(
                    neighbor in fixed_context.owner_by_seat
                    for neighbor in neighbors
                ):
                    continue
                blocked_modes = [tuple(sorted(neighbors))]
            elif single:
                neighbors = new_topology.subrow_neighbors(seat_id)
                if not neighbors:
                    continue
                # 留空资源不能是另一名固定旅客已经占用的座位。
                blocked_modes = [
                    (neighbor,) for neighbor in neighbors
                    if neighbor not in fixed_context.owner_by_seat
                ]
                if not blocked_modes:
                    continue

            individual_cost = -sum(
                evaluator.individual_assignment_components(
                    passenger, old_topology, new_topology, seat_id,
                    weights, config,
                ).values()
            )
            ssr = passenger.get("ssr", "")
            ssr_resources = (
                _ssr_resources(passenger, seat_id, new_topology)
                if ssr
                else ()
            )
            flag_locations = tuple(
                resource[:2]
                for resource in ssr_resources
                if (
                    resource[0] == "row"
                    and mandatory.get("sameRowNoOtherSSR", "N") == "Y"
                ) or (
                    resource[0] == "subrow"
                    and mandatory.get("sameSubRowNoOtherSSR", "N") == "Y"
                )
            )
            for blocked in blocked_modes:
                resources = tuple(sorted({seat_id, *blocked}))
                options.append(Placement(
                    pi,
                    key,
                    seat_id,
                    tuple(sorted(blocked)),
                    resources,
                    individual_cost,
                    tuple(ssr_resources),
                    flag_locations,
                    ssr == "BSCT",
                ))
        if not options:
            raise ValueError(
                f"旅客没有任何合法放置: passenger={key}"
            )
        result.append(options)
    return result


def _build_group_pricing_cache(
    group: dict,
    new_topology: evaluator.SeatTopology,
    old_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    fixed_context: Optional[FixedSeatContext] = None,
) -> GroupPricingCache:
    """一次构造组定价的静态域和矩阵关系，后续MP1迭代只更新目标系数。"""
    all_options = _placement_options(
        group, new_topology, old_topology, weights, config, fixed_context
    )
    universe = [option for options in all_options for option in options]

    # 仅为该组实际能够到达的座位建立跨度、邻接和空洞结构。
    reachable = {option.seat_id for option in universe}
    seat_ids = tuple(
        seat for seat in new_topology.seat_map if seat in reachable
    )
    min_row = min(
        (new_topology.y[seat] for seat in seat_ids),
        default=0.0,
    )
    row_coordinate = {
        seat: float(new_topology.y[seat] - min_row)
        for seat in seat_ids
    }
    row_big_m = max(row_coordinate.values(), default=0.0)
    min_x = min(
        (new_topology.x[seat] for seat in seat_ids), default=0.0
    )
    x_coordinate = {
        seat: float(new_topology.x[seat] - min_x) for seat in seat_ids
    }
    x_big_m = max(x_coordinate.values(), default=0.0)
    adjacency_edges = tuple(sorted({
        tuple(sorted((seat, neighbor)))
        for seat in seat_ids
        for neighbor in new_topology.row_neighbors(
            seat, allow_cross_aisle=True
        )
        if seat != neighbor
    }))
    hole_specs: List[Tuple[str, Tuple[str, ...], Tuple[str, ...]]] = []
    for row_seats in new_topology.row_seats.values():
        ordered = tuple(seat["seatId"] for seat in row_seats)
        for index in range(1, len(ordered) - 1):
            middle = ordered[index]
            left = tuple(seat for seat in ordered[:index] if seat in reachable)
            right = tuple(
                seat for seat in ordered[index + 1:] if seat in reachable
            )
            if not left or not right:
                continue
            hole_specs.append((middle, left, right))
    return GroupPricingCache(
        all_options=all_options,
        universe=universe,
        seat_ids=seat_ids,
        row_coordinate=row_coordinate,
        row_big_m=row_big_m,
        x_coordinate=x_coordinate,
        x_big_m=x_big_m,
        adjacency_edges=adjacency_edges,
        hole_specs=tuple(hole_specs),
    )


def _caregiver_ok(
    group: dict, placements: Sequence[Placement], topology: evaluator.SeatTopology,
    config: Dict[str, Any],
) -> bool:
    """检查一个整数同行组方案是否满足所有需要陪护旅客的邻接规则。"""
    by_index = {p.passenger_index: p for p in placements}
    passengers = group.get("psrs", [])
    for pi, passenger in enumerate(passengers):
        placement = by_index[pi]
        allow_cross = _caregiver_cross_aisle(passenger, config)
        if allow_cross is None:
            continue
        neighbors = set(topology.row_neighbors(placement.seat_id, allow_cross_aisle=allow_cross))
        found = any(
            qi != pi
            and by_index[qi].seat_id in neighbors
            and evaluator._is_adult_caregiver(other)
            for qi, other in enumerate(passengers)
        )
        if not found:
            return False
    return True


def _compactness_costs(
    weights: Mapping[str, float],
    config: Mapping[str, Any],
    *,
    phase_one: bool = False,
) -> CompactnessCosts:
    """生成PDF模型中横、纵重心最大偏差的非负成本。"""
    algorithm = config.get("algorithm", {})
    scale = 0.0 if phase_one else -float(weights.get("w_c", -2.0))
    return CompactnessCosts(
        centroid_x=scale * float(
            algorithm.get("group_centroid_x_factor", 1.0)
        ),
        centroid_y=scale * float(
            algorithm.get("group_centroid_y_factor", 1.0)
        ),
    )


def _baby_pairs(
    topology: evaluator.SeatTopology, groups: Sequence[dict],
    weights: Mapping[str, float], config: Mapping[str, Any],
) -> Dict[BabyPair, float]:
    """返回 evaluator 中每个有序“婴儿座位—其他座位”的正成本。"""
    if not any(p.get("ssr") == "BSCT" for g in groups for p in g.get("psrs", [])):
        return {}
    result: Dict[BabyPair, float] = {}
    for u in topology.seat_map:
        for v in topology.seat_map:
            score = evaluator.baby_interference_score(
                u, v, topology, weights, config
            )
            if score:
                result[(u, v)] = -score
    return result


def _pattern_from_placements(
    group: dict, placements: Sequence[Placement], topology: evaluator.SeatTopology,
    weights: Dict[str, float], config: Dict[str, Any], baby_cost: Mapping[BabyPair, float],
) -> ExactPattern:
    """把MP2整数放置组合转换成带完整MP1系数和精确成本的方案列。"""
    gid = int(group["groupId"])
    assignments = tuple(sorted(
        (placement.passenger_key, placement.seat_id)
        for placement in placements
    ))
    occupied = frozenset(seat for _, seat in assignments)
    resources = frozenset(r for placement in placements for r in placement.resources)
    blocked_by = tuple(sorted(
        (seat, placement.passenger_key)
        for placement in placements for seat in placement.blocked
    ))
    ssr_all: Dict[SsrResource, int] = defaultdict(int)
    ssr_flagged: Dict[SsrResource, int] = defaultdict(int)
    active_ssr_types = tuple(
        config.get("_column_generation_active_ssr_types")
        or config.get("ssr_rules", {}).keys()
    )
    infant: Set[str] = set()
    for placement in placements:
        for resource in placement.ssr_resources:
            ssr_all[resource] += 1
        for kind, location in placement.ssr_flag_locations:
            # 评分器语义：该位置一旦出现任一标记旅客，每一种SSR类型
            # 都至多1人，而不只是标记旅客自身类型。
            for active_ssr in active_ssr_types:
                ssr_flagged[(kind, location, active_ssr)] = 1
        if placement.is_infant:
            infant.add(placement.seat_id)
    same_pairs = frozenset((u, v) for u in infant for v in occupied if (u, v) in baby_cost)
    individual_score = -sum(p.individual_cost for p in placements)
    compact = float(weights.get("w_c", -2.0)) * (
        evaluator.group_compactness_penalty(
            [seat for _, seat in assignments], topology, config
        )
    )
    local_score = individual_score + compact
    # y_uv 会把同组占用也暂时计为婴儿影响；列成本中的负修正精确抵消它。
    master_cost = -local_score - sum(baby_cost[pair] for pair in same_pairs)
    return ExactPattern(
        group_id=gid,
        placements=tuple(sorted(placements, key=lambda p: p.passenger_index)),
        assignments=assignments,
        blocked_by=blocked_by,
        seat_resources=resources,
        ssr_all=tuple(sorted(ssr_all.items(), key=str)),
        ssr_flagged=tuple(sorted(ssr_flagged.items(), key=str)),
        infant_seats=frozenset(infant),
        occupied_seats=occupied,
        master_cost=master_cost,
    )


def _master_column_reduced_cost(
    pattern: ExactPattern,
    duals: MasterDuals,
    baby_pairs: Iterable[BabyPair],
    *,
    phase_one: bool = False,
) -> float:
    """用MP1全部行对偶计算方案列在Phase-I或原目标下的检验数。"""
    value = (
        0.0 if phase_one else pattern.master_cost
    ) - duals.group.get(pattern.group_id, 0.0)
    value -= sum(duals.seat.get(seat, 0.0) for seat in pattern.seat_resources)
    for resource, count in pattern.ssr_flagged:
        value -= duals.ssr_flag.get(
            (pattern.group_id, *resource), 0.0
        ) * count
    for resource, count in pattern.ssr_all:
        value -= duals.ssr_all.get(resource, 0.0) * count
    for pair in baby_pairs:
        u, v = pair
        infant = int(u in pattern.infant_seats)
        occupied = int(v in pattern.occupied_seats)
        value -= duals.baby_lower.get(pair, 0.0) * (infant + occupied)
        value -= duals.baby_infant_upper.get(pair, 0.0) * infant
        value -= duals.baby_occupant_upper.get(pair, 0.0) * occupied
    return value


def _safe_master_cost_lower_bound(
    rmp_cost: float,
    group_ids: Iterable[int],
    pricing_lower_bounds: Mapping[int, float],
) -> Optional[float]:
    """用各组检验数下界修复RMP对偶，返回完整列空间成本下界。

    每个同行组凸性行的右端均为1。若第 ``g`` 组所有列的检验数
    下界为 ``lb_g``，将该组凸性对偶下调 ``min(0, lb_g)`` 后即可
    得到完整列空间可行对偶。任一组缺少有限下界时不能声称安全界。
    """
    if not math.isfinite(rmp_cost):
        return None
    correction = 0.0
    for group_id in group_ids:
        lower_bound = pricing_lower_bounds.get(int(group_id))
        if lower_bound is None or not math.isfinite(lower_bound):
            return None
        correction += min(0.0, float(lower_bound))
    return float(rmp_cost) + correction


def _rank_negative_pricing_bounds(
    pricing_lower_bounds: Mapping[int, float],
    *,
    tolerance: float = 0.0,
) -> List[Tuple[int, float]]:
    """按各组对安全上界修正量的贡献从大到小排序。"""
    return sorted(
        (
            (int(group_id), float(lower_bound))
            for group_id, lower_bound in pricing_lower_bounds.items()
            if math.isfinite(lower_bound) and lower_bound < -tolerance
        ),
        key=lambda item: (item[1], item[0]),
    )


def _transport_pricing_lower_bound(
    group: Mapping[str, Any],
    cache: GroupPricingCache,
    old_duals: MasterDuals,
    new_duals: MasterDuals,
    old_lower_bound: float,
) -> float:
    """把旧对偶下的严格MP2下界安全转换到新对偶。

    对任意列 ``r_new = r_old - (pi_new-pi_old) a``。这里对各系数族
    的正对偶增量取覆盖完整组模式域的上界；因此结果可能变松，但不会
    高估新对偶下的最小检验数。
    """
    if not math.isfinite(old_lower_bound):
        return -math.inf
    gid = int(group["groupId"])
    decrease = (
        float(new_duals.group.get(gid, 0.0))
        - float(old_duals.group.get(gid, 0.0))
    )
    resource_count = _fixed_seat_resource_demand([group])
    seat_deltas = sorted(
        (
            float(new_duals.seat.get(seat_id, 0.0))
            - float(old_duals.seat.get(seat_id, 0.0))
            for seat_id in (set(old_duals.seat) | set(new_duals.seat))
        ),
        reverse=True,
    )
    decrease += sum(
        max(0.0, delta) for delta in seat_deltas[:resource_count]
    )
    passenger_count = len(group.get("psrs", []))
    possible_ssr = {
        resource
        for option in cache.universe
        for resource in option.ssr_resources
    }
    for resource in possible_ssr:
        delta = (
            float(new_duals.ssr_all.get(resource, 0.0))
            - float(old_duals.ssr_all.get(resource, 0.0))
        )
        decrease += passenger_count * max(0.0, delta)
    possible_flags = {
        (gid, str(kind), location)
        for option in cache.universe
        for kind, location in option.ssr_flag_locations
    }
    for resource in possible_flags:
        delta = (
            float(new_duals.ssr_flag.get(resource, 0.0))
            - float(old_duals.ssr_flag.get(resource, 0.0))
        )
        decrease += passenger_count * max(0.0, delta)

    possible_seats = set(cache.seat_ids)
    possible_infant_seats = {
        option.seat_id for option in cache.universe if option.is_infant
    }
    baby_keys = (
        set(old_duals.baby_lower) | set(new_duals.baby_lower)
        | set(old_duals.baby_infant_upper)
        | set(new_duals.baby_infant_upper)
        | set(old_duals.baby_occupant_upper)
        | set(new_duals.baby_occupant_upper)
    )
    for pair in baby_keys:
        infant_seat, occupant_seat = pair
        lower_coefficient = int(infant_seat in possible_infant_seats) + int(
            occupant_seat in possible_seats
        )
        if lower_coefficient:
            delta = (
                float(new_duals.baby_lower.get(pair, 0.0))
                - float(old_duals.baby_lower.get(pair, 0.0))
            )
            decrease += lower_coefficient * max(0.0, delta)
        if infant_seat in possible_infant_seats:
            delta = (
                float(new_duals.baby_infant_upper.get(pair, 0.0))
                - float(old_duals.baby_infant_upper.get(pair, 0.0))
            )
            decrease += max(0.0, delta)
        if occupant_seat in possible_seats:
            delta = (
                float(new_duals.baby_occupant_upper.get(pair, 0.0))
                - float(old_duals.baby_occupant_upper.get(pair, 0.0))
            )
            decrease += max(0.0, delta)
    return float(old_lower_bound) - decrease


def _reduced_cost_audit(
    pattern: ExactPattern,
    duals: MasterDuals,
    baby_pairs: Iterable[BabyPair],
    *,
    phase_one: bool = False,
) -> Dict[str, Any]:
    """分解一个方案列的检验数，专用于MP2目标不一致诊断。

    正常定价仍走轻量的 ``_master_column_reduced_cost``。只有一致性检查
    失败时才构造逐项明细，避免在数千次定价中产生额外字典和日志开销。
    """
    objective = 0.0 if phase_one else float(pattern.master_cost)
    group_dual = float(duals.group.get(pattern.group_id, 0.0))
    seat_duals = {
        seat: float(duals.seat.get(seat, 0.0))
        for seat in sorted(pattern.seat_resources)
    }
    ssr_flag_duals = {
        str((pattern.group_id, *resource)): {
            "coefficient": int(count),
            "dual": float(
                duals.ssr_flag.get((pattern.group_id, *resource), 0.0)
            ),
        }
        for resource, count in pattern.ssr_flagged
    }
    ssr_all_duals = {
        str(resource): {
            "coefficient": int(count),
            "dual": float(duals.ssr_all.get(resource, 0.0)),
        }
        for resource, count in pattern.ssr_all
    }

    baby_lower_active: Dict[str, Dict[str, float]] = {}
    baby_infant_active: Dict[str, Dict[str, float]] = {}
    baby_occupant_active: Dict[str, Dict[str, float]] = {}
    baby_lower_contribution = 0.0
    baby_infant_contribution = 0.0
    baby_occupant_contribution = 0.0
    for pair in baby_pairs:
        infant_seat, occupant_seat = pair
        infant = int(infant_seat in pattern.infant_seats)
        occupant = int(occupant_seat in pattern.occupied_seats)
        lower_coefficient = infant + occupant
        if lower_coefficient:
            dual = float(duals.baby_lower.get(pair, 0.0))
            if abs(dual) > 1e-15:
                baby_lower_active[str(pair)] = {
                    "coefficient": lower_coefficient, "dual": dual
                }
            baby_lower_contribution -= dual * lower_coefficient
        if infant:
            dual = float(duals.baby_infant_upper.get(pair, 0.0))
            if abs(dual) > 1e-15:
                baby_infant_active[str(pair)] = {
                    "coefficient": infant, "dual": dual
                }
            baby_infant_contribution -= dual * infant
        if occupant:
            dual = float(duals.baby_occupant_upper.get(pair, 0.0))
            if abs(dual) > 1e-15:
                baby_occupant_active[str(pair)] = {
                    "coefficient": occupant, "dual": dual
                }
            baby_occupant_contribution -= dual * occupant

    components = {
        "master_objective": objective,
        "group_dual_contribution": -group_dual,
        "seat_dual_contribution": -sum(seat_duals.values()),
        "ssr_flag_dual_contribution": -sum(
            item["coefficient"] * item["dual"]
            for item in ssr_flag_duals.values()
        ),
        "ssr_all_dual_contribution": -sum(
            item["coefficient"] * item["dual"]
            for item in ssr_all_duals.values()
        ),
        "baby_lower_dual_contribution": baby_lower_contribution,
        "baby_infant_upper_dual_contribution": baby_infant_contribution,
        "baby_occupant_upper_dual_contribution": baby_occupant_contribution,
    }
    return {
        "component_contributions": components,
        "component_sum": float(sum(components.values())),
        "active_duals": {
            "group": group_dual,
            "seat": seat_duals,
            "ssr_flag": ssr_flag_duals,
            "ssr_all": ssr_all_duals,
            "baby_lower": baby_lower_active,
            "baby_infant_upper": baby_infant_active,
            "baby_occupant_upper": baby_occupant_active,
        },
    }


def _model_equivalence_gap(
    model_objective: float,
    recomputed_rc: float,
    group_dual: float,
    *,
    solver_optimal: bool,
    tolerance: float,
) -> Optional[float]:
    """返回需要报告的MP2目标误差；限时/无穷目标不参与严格认证。"""
    if not solver_optimal or not math.isfinite(model_objective):
        return None
    error = abs(
        (model_objective - group_dual) - recomputed_rc
    )
    return error if error > tolerance else None


def _aggregate_baby_duals(
    baby_pairs: Iterable[BabyPair],
    duals: MasterDuals,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """按婴儿座位和占座座位聚合MP1婴儿约束对偶。

    MP1三类婴儿行在方案列中的系数均为正，因此这里使用对偶之和；
    放置成本函数再统一减去聚合值，集中维护符号逻辑。
    """
    infant_dual: Dict[str, float] = defaultdict(float)
    occupant_dual: Dict[str, float] = defaultdict(float)
    for infant_seat, occupant_seat in baby_pairs:
        pair = (infant_seat, occupant_seat)
        infant_dual[infant_seat] += (
            duals.baby_lower.get(pair, 0.0)
            + duals.baby_infant_upper.get(pair, 0.0)
        )
        occupant_dual[occupant_seat] += (
            duals.baby_lower.get(pair, 0.0)
            + duals.baby_occupant_upper.get(pair, 0.0)
        )
    return dict(infant_dual), dict(occupant_dual)


def _same_group_baby_cost_upper_bound(
    cache: GroupPricingCache,
    baby_cost: Mapping[BabyPair, float],
    passenger_count: int,
) -> float:
    """Upper-bound the baby correction achievable by one group pattern.

    A pattern occupies exactly ``passenger_count`` real seats.  For a fixed
    infant seat, at most that many infant--occupant pairs can therefore be
    active.  Summing the largest such costs and then the largest values for
    the number of infant passengers is a relaxation, but is much tighter than
    activating every candidate pair simultaneously.
    """
    if not baby_cost or passenger_count <= 0:
        return 0.0
    infant_passenger_count = sum(
        any(option.is_infant for option in options)
        for options in cache.all_options
    )
    if infant_passenger_count <= 0:
        return 0.0
    possible_infant_seats = {
        option.seat_id for option in cache.universe if option.is_infant
    }
    possible_occupant_seats = set(cache.seat_ids)
    per_infant = []
    for infant_seat in possible_infant_seats:
        costs = sorted(
            (
                max(0.0, float(cost))
                for (u, v), cost in baby_cost.items()
                if u == infant_seat and v in possible_occupant_seats
            ),
            reverse=True,
        )
        per_infant.append(sum(costs[:passenger_count]))
    per_infant.sort(reverse=True)
    return float(sum(per_infant[:infant_passenger_count]))


def _aggregate_flagged_location_duals(
    duals: MasterDuals,
) -> Dict[Tuple[int, str, Any], float]:
    """按同行组和位置聚合所有条件 SSR 行对偶。

    一个方案在同一 ``(kind, location)`` 上只激活一次，但该次激活会同时
    出现在所有活动 SSR 类型对应的 MP1 行中，因此先把这些行对偶求和。
    """
    flagged_duals: Dict[Tuple[int, str, Any], float] = defaultdict(float)
    for resource, value in duals.ssr_flag.items():
        flagged_duals[
            (int(resource[0]), str(resource[1]), resource[2])
        ] += float(value)
    return dict(flagged_duals)


def _placement_base_reduced_cost(
    option: Placement,
    duals: MasterDuals,
    infant_dual: Mapping[str, float],
    occupant_dual: Mapping[str, float],
    *,
    phase_one: bool,
) -> float:
    """计算可按单个旅客放置累加的 MP2 基础成本和对偶贡献。

    条件 SSR 激活成本是“位置首次激活”成本，不能在这里按旅客重复计算；
    DFS 和完整域 MIP 分别通过状态掩码和 OR 变量处理该非加性项。
    """
    value = 0.0 if phase_one else float(option.individual_cost)

    # 占用座位和每一份保护空座需求都消耗一个独占座位资源。
    # option.resources 已去重，因此与 MP1 方案列的座位系数完全一致。
    for resource in option.resources:
        value -= duals.seat.get(resource, 0.0)

    for resource in option.ssr_resources:
        value -= duals.ssr_all.get(resource, 0.0)
    value -= occupant_dual.get(option.seat_id, 0.0)
    if option.is_infant:
        value -= infant_dual.get(option.seat_id, 0.0)
    return value


def _skeleton_assignment_lower_bound(
    group: dict,
    cache: GroupPricingCache,
    weights: Mapping[str, float],
    config: Mapping[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    *,
    max_combinations: int = 100_000,
    max_window_skeletons: int = 256,
    top_k_window_skeletons: int = 0,
) -> Optional[Dict[str, Any]]:
    """Return a strict pricing bound for mixed protected/ordinary groups.

    Constrained passengers are enumerated as a small skeleton.  Remaining
    ordinary passengers are assigned to distinct free seats by a linear
    assignment relaxation.  Their average distance outside the skeleton
    rectangle is a lower bound on the final row/column span extension.
    Dropping adjacency, hole and caregiver penalties only relaxes the model.
    """
    started = time.perf_counter()
    gid = int(group["groupId"])
    special: List[int] = []
    ordinary: List[int] = []
    for passenger_index, options in enumerate(cache.all_options):
        is_ordinary = all(
            option.resources == (option.seat_id,)
            and not option.ssr_resources
            and not option.ssr_flag_locations
            and not option.is_infant
            for option in options
        )
        (ordinary if is_ordinary else special).append(passenger_index)
    combination_count = math.prod(
        len(cache.all_options[index]) for index in special
    )
    if not ordinary or combination_count > max(1, int(max_combinations)):
        return None

    infant_dual, occupant_dual = _aggregate_baby_duals(baby_cost, duals)
    base_cost = {
        option.signature: _placement_base_reduced_cost(
            option, duals, infant_dual, occupant_dual, phase_one=False
        )
        for option in cache.universe
    }
    seat_ids = list(cache.seat_ids)
    seat_index = {seat_id: index for index, seat_id in enumerate(seat_ids)}
    seat_rows = np.asarray(
        [cache.row_coordinate[seat_id] for seat_id in seat_ids],
        dtype=np.float64,
    )
    seat_columns = np.asarray(
        [cache.x_coordinate[seat_id] for seat_id in seat_ids],
        dtype=np.float64,
    )
    ordinary_cost = np.full(
        (len(ordinary), len(seat_ids)), math.inf, dtype=np.float64
    )
    ordinary_option_by_seat: Dict[Tuple[int, str], Placement] = {}
    for row, passenger_index in enumerate(ordinary):
        for option in cache.all_options[passenger_index]:
            column = seat_index.get(option.seat_id)
            if (
                column is not None
                and base_cost[option.signature]
                < ordinary_cost[row, column]
            ):
                ordinary_cost[row, column] = min(
                    ordinary_cost[row, column],
                    base_cost[option.signature],
                )
                ordinary_option_by_seat[
                    (passenger_index, option.seat_id)
                ] = option
    if any(not np.isfinite(row).any() for row in ordinary_cost):
        return None

    flagged_duals = _aggregate_flagged_location_duals(duals)
    compactness = _compactness_costs(weights, config)
    baby_relaxation = -_same_group_baby_cost_upper_bound(
        cache, baby_cost, len(group.get("psrs", []))
    )
    assignment_cache: Dict[tuple, float] = {}
    best = math.inf
    best_candidate_placements: Optional[Tuple[Placement, ...]] = None
    feasible_skeletons = 0
    empty_skeleton_span = 0.0
    empty_window_bound = math.inf
    empty_window_placements: Optional[Tuple[Placement, ...]] = None
    unique_rows = sorted(set(seat_rows.tolist()))
    unique_columns = sorted(set(seat_columns.tolist()))

    def window_assignment(
        unavailable: FrozenSet[str],
        min_row: float,
        max_row: float,
        min_column: float,
        max_column: float,
    ) -> Tuple[float, Optional[Tuple[Placement, ...]]]:
        """Minimize ordinary assignment plus the exact rectangle span."""
        window_bound = math.inf
        window_placements: Optional[Tuple[Placement, ...]] = None
        unavailable_mask = np.asarray([
            seat_id not in unavailable for seat_id in seat_ids
        ])
        for window_min_row in unique_rows:
            if window_min_row > min_row:
                continue
            for window_max_row in unique_rows:
                if window_max_row < max_row:
                    continue
                row_mask = (
                    (seat_rows >= window_min_row)
                    & (seat_rows <= window_max_row)
                )
                for window_min_column in unique_columns:
                    if window_min_column > min_column:
                        continue
                    for window_max_column in unique_columns:
                        if window_max_column < max_column:
                            continue
                        available = np.flatnonzero(
                            unavailable_mask
                            & row_mask
                            & (seat_columns >= window_min_column)
                            & (seat_columns <= window_max_column)
                        )
                        if len(available) < len(ordinary):
                            continue
                        try:
                            matched_rows, matched_columns = (
                                linear_sum_assignment(
                                    ordinary_cost[:, available]
                                )
                            )
                        except ValueError:
                            continue
                        matching = float(ordinary_cost[
                            matched_rows, available[matched_columns]
                        ].sum())
                        value = (
                            matching
                            + compactness.row_span
                            * (window_max_row - window_min_row)
                            + compactness.column_span
                            * (window_max_column - window_min_column)
                        )
                        if math.isfinite(value) and value < window_bound:
                            window_bound = value
                            window_placements = tuple(
                                ordinary_option_by_seat[
                                    (
                                        ordinary[row],
                                        seat_ids[
                                            available[matched_columns[index]]
                                        ],
                                    )
                                ]
                                for index, row in enumerate(matched_rows)
                            )
        return window_bound, window_placements

    top_k = max(0, int(top_k_window_skeletons))
    ranked_large_skeletons: List[Tuple[float, int, Dict[str, Any]]] = []
    skeleton_serial = 0
    if not special:
        empty_skeleton_span = math.inf
        required = len(ordinary)
        for min_row in unique_rows:
            for max_row in unique_rows:
                if max_row < min_row:
                    continue
                row_mask = (seat_rows >= min_row) & (seat_rows <= max_row)
                for min_column in unique_columns:
                    for max_column in unique_columns:
                        if max_column < min_column:
                            continue
                        capacity = int(np.count_nonzero(
                            row_mask
                            & (seat_columns >= min_column)
                            & (seat_columns <= max_column)
                        ))
                        if capacity >= required:
                            empty_skeleton_span = min(
                                empty_skeleton_span,
                                compactness.row_span * (max_row - min_row)
                                + compactness.column_span
                                * (max_column - min_column),
                            )
                            available = np.flatnonzero(
                                row_mask
                                & (seat_columns >= min_column)
                                & (seat_columns <= max_column)
                            )
                            try:
                                matched_rows, matched_columns = (
                                    linear_sum_assignment(
                                        ordinary_cost[:, available]
                                    )
                                )
                            except ValueError:
                                continue
                            window_matching = float(ordinary_cost[
                                matched_rows,
                                available[matched_columns],
                            ].sum())
                            if math.isfinite(window_matching):
                                window_value = (
                                    window_matching
                                    + compactness.row_span
                                    * (max_row - min_row)
                                    + compactness.column_span
                                    * (max_column - min_column)
                                )
                                if window_value < empty_window_bound:
                                    empty_window_bound = window_value
                                    empty_window_placements = tuple(
                                        ordinary_option_by_seat[
                                            (
                                                ordinary[row],
                                                seat_ids[
                                                    available[
                                                        matched_columns[index]
                                                    ]
                                                ],
                                            )
                                        ]
                                        for index, row in enumerate(
                                            matched_rows
                                        )
                                    )
    for skeleton in itertools.product(
        *(cache.all_options[index] for index in special)
    ):
        resources = [
            resource for option in skeleton for resource in option.resources
        ]
        if len(resources) != len(set(resources)):
            continue
        feasible_skeletons += 1
        unavailable = frozenset(resources)
        rows = [cache.row_coordinate[option.seat_id] for option in skeleton]
        columns = [cache.x_coordinate[option.seat_id] for option in skeleton]
        min_row = min(rows) if rows else None
        max_row = max(rows) if rows else None
        min_column = min(columns) if columns else None
        max_column = max(columns) if columns else None
        cache_key = (
            unavailable, min_row, max_row, min_column, max_column
        )
        matching_cost = assignment_cache.get(cache_key)
        if matching_cost is None:
            available = np.asarray([
                index for index, seat_id in enumerate(seat_ids)
                if seat_id not in unavailable
            ], dtype=np.int32)
            if len(available) < len(ordinary):
                matching_cost = math.inf
            else:
                matrix = ordinary_cost[:, available].copy()
                if skeleton:
                    row_extension = np.maximum.reduce((
                        min_row - seat_rows[available],
                        np.zeros(len(available)),
                        seat_rows[available] - max_row,
                    ))
                    column_extension = np.maximum.reduce((
                        min_column - seat_columns[available],
                        np.zeros(len(available)),
                        seat_columns[available] - max_column,
                    ))
                    matrix += (
                        compactness.row_span * row_extension
                        + compactness.column_span * column_extension
                    ) / float(len(ordinary))
                try:
                    matched_rows, matched_columns = (
                        linear_sum_assignment(matrix)
                    )
                    matching_cost = float(
                        matrix[matched_rows, matched_columns].sum()
                    )
                except ValueError:
                    matching_cost = math.inf
                if not math.isfinite(matching_cost):
                    matching_cost = math.inf
            assignment_cache[cache_key] = matching_cost
        if not math.isfinite(matching_cost):
            continue

        active_flags = {
            (str(kind), location)
            for option in skeleton
            for kind, location in option.ssr_flag_locations
        }
        skeleton_window_placements: Optional[
            Tuple[Placement, ...]
        ] = None
        candidate = (
            sum(base_cost[option.signature] for option in skeleton)
            - sum(
                flagged_duals.get((gid, kind, location), 0.0)
                for kind, location in active_flags
            )
            + matching_cost
            + (
                compactness.row_span * (max_row - min_row)
                + compactness.column_span * (max_column - min_column)
                if skeleton else empty_skeleton_span
            )
            + baby_relaxation
            - float(duals.group.get(gid, 0.0))
        )
        if not skeleton and math.isfinite(empty_window_bound):
            candidate = max(
                candidate,
                empty_window_bound
                + baby_relaxation
                - float(duals.group.get(gid, 0.0)),
            )
        elif skeleton and combination_count <= max_window_skeletons:
            skeleton_window_bound, skeleton_window_placements = (
                window_assignment(
                    unavailable,
                    min_row, max_row, min_column, max_column,
                )
            )
            if math.isfinite(skeleton_window_bound):
                candidate = max(
                    candidate,
                    sum(
                        base_cost[option.signature]
                        for option in skeleton
                    )
                    - sum(
                        flagged_duals.get((gid, kind, location), 0.0)
                        for kind, location in active_flags
                    )
                    + skeleton_window_bound
                    + baby_relaxation
                    - float(duals.group.get(gid, 0.0)),
                )
        elif skeleton and top_k > 0:
            fixed_cost = (
                sum(base_cost[option.signature] for option in skeleton)
                - sum(
                    flagged_duals.get((gid, kind, location), 0.0)
                    for kind, location in active_flags
                )
                + baby_relaxation
                - float(duals.group.get(gid, 0.0))
            )
            record = {
                "lower_bound": candidate,
                "skeleton": tuple(skeleton),
                "unavailable": unavailable,
                "min_row": min_row,
                "max_row": max_row,
                "min_column": min_column,
                "max_column": max_column,
                "fixed_cost": fixed_cost,
            }
            skeleton_serial += 1
            entry = (-candidate, skeleton_serial, record)
            if len(ranked_large_skeletons) < top_k + 1:
                heapq.heappush(ranked_large_skeletons, entry)
            elif candidate < -ranked_large_skeletons[0][0]:
                heapq.heapreplace(ranked_large_skeletons, entry)
        if candidate < best:
            best = candidate
            if not skeleton:
                best_candidate_placements = empty_window_placements
            elif skeleton_window_placements is not None:
                best_candidate_placements = tuple(skeleton) + (
                    skeleton_window_placements
                )
            else:
                # Large skeleton spaces skip rectangle enumeration, but the
                # assignment relaxation still yields a concrete all-different
                # seating plan.  Rebuild it only for a new best skeleton so
                # we do not retain thousands of placement tuples in cache.
                available = np.asarray([
                    index for index, seat_id in enumerate(seat_ids)
                    if seat_id not in unavailable
                ], dtype=np.int32)
                matrix = ordinary_cost[:, available].copy()
                row_extension = np.maximum.reduce((
                    min_row - seat_rows[available],
                    np.zeros(len(available)),
                    seat_rows[available] - max_row,
                ))
                column_extension = np.maximum.reduce((
                    min_column - seat_columns[available],
                    np.zeros(len(available)),
                    seat_columns[available] - max_column,
                ))
                matrix += (
                    compactness.row_span * row_extension
                    + compactness.column_span * column_extension
                ) / float(len(ordinary))
                try:
                    matched_rows, matched_columns = (
                        linear_sum_assignment(matrix)
                    )
                    best_candidate_placements = tuple(skeleton) + tuple(
                        ordinary_option_by_seat[
                            (
                                ordinary[row],
                                seat_ids[
                                    available[matched_columns[index]]
                                ],
                            )
                        ]
                        for index, row in enumerate(matched_rows)
                    )
                except ValueError:
                    best_candidate_placements = None

    top_k_evaluated = 0
    top_k_boundary = None
    if combination_count > max_window_skeletons and ranked_large_skeletons:
        ranked = sorted(
            (record for _, _, record in ranked_large_skeletons),
            key=lambda record: record["lower_bound"],
        )
        strengthened: List[
            Tuple[float, Optional[Tuple[Placement, ...]]]
        ] = []
        for record in ranked[:top_k]:
            window_bound, placements = window_assignment(
                record["unavailable"],
                record["min_row"], record["max_row"],
                record["min_column"], record["max_column"],
            )
            if math.isfinite(window_bound):
                strengthened.append((
                    record["fixed_cost"] + window_bound,
                    record["skeleton"] + (placements or ()),
                ))
            else:
                strengthened.append((record["lower_bound"], None))
            top_k_evaluated += 1
        if len(ranked) > top_k:
            top_k_boundary = float(ranked[top_k]["lower_bound"])
            strengthened.append((top_k_boundary, None))
        if strengthened:
            best, refined_placements = min(
                strengthened, key=lambda item: item[0]
            )
            if refined_placements is not None:
                best_candidate_placements = refined_placements

    if not math.isfinite(best):
        return None
    return {
        "lower_bound": float(best),
        "seconds": time.perf_counter() - started,
        "combinations": int(combination_count),
        "feasible_skeletons": int(feasible_skeletons),
        "matching_cache_entries": len(assignment_cache),
        "top_k_window_skeletons_requested": top_k,
        "top_k_window_skeletons_evaluated": top_k_evaluated,
        "top_k_unevaluated_boundary": top_k_boundary,
        "special_passengers": special,
        "ordinary_passengers": ordinary,
        "candidate_placements": best_candidate_placements,
    }


def _pricing_column_limit(
    pricing_cfg: Mapping[str, Any], *, exact: bool,
) -> int:
    return max(
        1,
        int(pricing_cfg.get(
            "exact_pricing_columns_per_group" if exact
            else "quick_pricing_columns_per_group",
            pricing_cfg.get("pricing_columns_per_group", 4 if exact else 3),
        )),
    )


def _price_group_exact_dfs(
    group: dict,
    new_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    forced_placements: Optional[Set[PlacementSignature]],
    forbidden_placements: Optional[Set[PlacementSignature]],
    deadline: Optional[float],
    cache: GroupPricingCache,
    *,
    exact: bool,
    phase_one: bool,
    stop_on_negative: bool,
) -> PricingResult:
    """使用资源位图和安全下界精确搜索一个同行组的MP2。

    搜索完整关闭时可以直接认证最小检验数；达到DFS自身限制时只保留已
    找到的负列，由调用方转交HiGHS继续认证。
    """
    started = time.perf_counter()
    gid = int(group["groupId"])
    passengers = group.get("psrs", [])
    pricing_cfg = config.get("column_generation", {})
    tolerance = _limits(config)["tolerance"]
    pool_limit = (
        1 if pricing_cfg.get("benchmark_legacy_pricing", False)
        else _pricing_column_limit(pricing_cfg, exact=exact)
    )
    forced = forced_placements or set()
    forbidden = forbidden_placements or set()
    forced_by_passenger = {
        signature[0]: signature for signature in forced
    }
    if len(forced_by_passenger) != len(forced):
        return PricingResult(
            patterns=(), reduced_cost=math.inf, proven_optimal=True,
            nodes=0, priced_placements=0,
            elapsed=time.perf_counter() - started,
            termination="infeasible_branch", complete_domain_mip_solves=0,
        )

    dfs_time_limit = max(
        0.0,
        float(pricing_cfg.get(
            "dfs_exact_time_limit" if exact
            else "dfs_discovery_time_limit",
            15.0 if exact else 1.0,
        )),
    )
    large_group_threshold = max(
        2, int(pricing_cfg.get("dfs_large_group_min_size", 9))
    )
    if exact and len(passengers) >= large_group_threshold:
        dfs_time_limit = max(
            dfs_time_limit,
            float(pricing_cfg.get(
                "dfs_large_group_exact_time_limit", 15.0
            )),
        )
    local_deadline = min(
        started + dfs_time_limit,
        deadline if deadline is not None else math.inf,
    )
    node_limit = max(
        1, int(pricing_cfg.get("dfs_node_limit", 2000000))
    )
    if exact and len(passengers) >= large_group_threshold:
        node_limit = max(
            node_limit,
            int(pricing_cfg.get(
                "dfs_large_group_node_limit", 5000000
            )),
        )

    workspace_started = time.perf_counter()
    workspace = cache.dfs_workspace
    workspace_builds = 0
    workspace_reuses = 0
    if workspace is None:
        seat_bit = {
            seat: 1 << index
            for index, seat in enumerate(new_topology.seat_map)
        }
        flag_locations = sorted({
            (str(kind), location)
            for option in cache.universe
            for kind, location in option.ssr_flag_locations
        }, key=str)
        flag_bit = {
            location: 1 << index
            for index, location in enumerate(flag_locations)
        }
        row_values = sorted(set(cache.row_coordinate.values()))
        x_values = sorted(set(cache.x_coordinate.values()))
        row_value_index = {
            value: index for index, value in enumerate(row_values)
        }
        x_value_index = {
            value: index for index, value in enumerate(x_values)
        }
        seat_count = np.zeros(
            (len(row_values), len(x_values)), dtype=np.int16
        )
        for seat in cache.seat_ids:
            seat_count[
                row_value_index[cache.row_coordinate[seat]],
                x_value_index[cache.x_coordinate[seat]],
            ] += 1
        caregiver_specs: List[Tuple[int, bool, Tuple[int, ...]]] = []
        for passenger_index, passenger in enumerate(passengers):
            allow_cross = _caregiver_cross_aisle(passenger, config)
            if allow_cross is None:
                continue
            caregivers = tuple(
                other_index
                for other_index, other in enumerate(passengers)
                if (
                    other_index != passenger_index
                    and evaluator._is_adult_caregiver(other)
                )
            )
            caregiver_specs.append(
                (passenger_index, allow_cross, caregivers)
            )
        workspace = {
            "seat_bit": seat_bit,
            "resource_mask": {
                option.signature: sum(
                    seat_bit[seat] for seat in option.resources
                )
                for option in cache.universe
            },
            "flag_locations": flag_locations,
            "flag_bit": flag_bit,
            "option_flag_bits": {
                option.signature: tuple(
                    flag_bit[(str(kind), location)]
                    for kind, location in option.ssr_flag_locations
                )
                for option in cache.universe
            },
            "row_values": row_values,
            "x_values": x_values,
            "row_value_index": row_value_index,
            "x_value_index": x_value_index,
            "seat_prefix": seat_count.cumsum(axis=0).cumsum(axis=1),
            "caregiver_specs": caregiver_specs,
            "option_by_signature": {
                option.signature: option for option in cache.universe
            },
        }
        workspace["option_flag_mask"] = {
            signature: sum(bits)
            for signature, bits in workspace["option_flag_bits"].items()
        }
        if not pricing_cfg.get("benchmark_legacy_pricing", False):
            cache.dfs_workspace = workspace
        workspace_builds = 1
    else:
        workspace_reuses = 1
    workspace_seconds = time.perf_counter() - workspace_started
    seat_bit = workspace["seat_bit"]
    resource_mask = workspace["resource_mask"]
    flag_locations = workspace["flag_locations"]
    flag_bit = workspace["flag_bit"]
    option_flag_bits = workspace["option_flag_bits"]
    option_flag_mask = workspace["option_flag_mask"]
    dynamic_refresh_started = time.perf_counter()
    baby_infant_dual, baby_occupant_dual = _aggregate_baby_duals(
        baby_cost, duals
    )
    flagged_duals = _aggregate_flagged_location_duals(duals)

    base_cost: Dict[PlacementSignature, float] = {
        option.signature: _placement_base_reduced_cost(
            option,
            duals,
            baby_infant_dual,
            baby_occupant_dual,
            phase_one=phase_one,
        )
        for option in cache.universe
    }
    flag_cost = {
        flag_bit[location]: -flagged_duals.get(
            (gid, location[0], location[1]), 0.0
        )
        for location in flag_locations
    }
    domains: Dict[int, List[Placement]] = {}
    for passenger_index, options in enumerate(cache.all_options):
        required = forced_by_passenger.get(passenger_index)
        domain = [
            option for option in options
            if option.signature not in forbidden
            and (required is None or option.signature == required)
        ]
        if not domain:
            return PricingResult(
                patterns=(), reduced_cost=math.inf, proven_optimal=True,
                nodes=0, priced_placements=len(cache.universe),
                elapsed=time.perf_counter() - started,
                termination="infeasible_branch",
                complete_domain_mip_solves=0,
            )
        domain.sort(key=lambda option: (
            base_cost[option.signature],
            len(option.resources),
            option.seat_id,
            option.blocked,
        ))
        domains[passenger_index] = domain

    # 固定、保护空座、陪护等受限旅客优先，随后按完整域大小排序。
    def passenger_priority(passenger_index: int) -> tuple:
        passenger = passengers[passenger_index]
        mandatory = _mandatory(passenger)
        return (
            passenger_index not in forced_by_passenger,
            not (
                mandatory.get("needBothSideEmpty", "N") == "Y"
                or mandatory.get("needSingleSideEmpty", "N") == "Y"
            ),
            _caregiver_cross_aisle(passenger, config) is None,
            len(domains[passenger_index]),
            passenger_index,
        )

    order = sorted(range(len(passengers)), key=passenger_priority)
    historical = set(cache.historical_mip_start or ())
    branch_domains = {
        passenger_index: sorted(
            domains[passenger_index],
            key=lambda option: (
                option.signature not in historical,
                base_cost[option.signature],
                option.seat_id,
            ),
        )
        for passenger_index in order
    }

    compactness = _compactness_costs(
        weights, config, phase_one=phase_one
    )
    row_coordinate = cache.row_coordinate
    x_coordinate = cache.x_coordinate
    group_dual = float(duals.group.get(gid, 0.0))

    # 任一完整方案必须把全部旅客装入一个覆盖其占座集合的行列矩形。
    # 忽略旅客域、保护空座和SSR只会放宽容量，因此“可容纳全组的最便宜
    # 矩形”是行/列跨度成本的安全下界。四维前后缀最小值使节点查询为O(1)。
    row_values = workspace["row_values"]
    x_values = workspace["x_values"]
    row_value_index = workspace["row_value_index"]
    x_value_index = workspace["x_value_index"]
    prefix = workspace["seat_prefix"]

    def rectangle_capacity(
        row_low: int, row_high: int, x_low: int, x_high: int,
    ) -> int:
        value = int(prefix[row_high, x_high])
        if row_low:
            value -= int(prefix[row_low - 1, x_high])
        if x_low:
            value -= int(prefix[row_high, x_low - 1])
        if row_low and x_low:
            value += int(prefix[row_low - 1, x_low - 1])
        return value

    rectangle_exact_cost = np.full(
        (
            len(row_values), len(row_values),
            len(x_values), len(x_values),
        ),
        np.inf,
        dtype=np.float64,
    )
    required_seats = len(passengers)
    for row_low in range(len(row_values)):
        for row_high in range(row_low, len(row_values)):
            row_cost = (
                compactness.row_span
                * (row_values[row_high] - row_values[row_low])
            )
            for x_low in range(len(x_values)):
                for x_high in range(x_low, len(x_values)):
                    if rectangle_capacity(
                        row_low, row_high, x_low, x_high
                    ) >= required_seats:
                        rectangle_exact_cost[
                            row_low, row_high, x_low, x_high
                        ] = row_cost + (
                            compactness.column_span
                            * (x_values[x_high] - x_values[x_low])
                        )
    rectangle_span_bound = np.minimum.accumulate(
        rectangle_exact_cost, axis=0
    )
    rectangle_span_bound = np.minimum.accumulate(
        rectangle_span_bound[:, ::-1, :, :], axis=1
    )[:, ::-1, :, :]
    rectangle_span_bound = np.minimum.accumulate(
        rectangle_span_bound, axis=2
    )
    rectangle_span_bound = np.minimum.accumulate(
        rectangle_span_bound[:, :, :, ::-1], axis=3
    )[:, :, :, ::-1]
    root_rectangle_bound = float(np.min(rectangle_span_bound))

    # 将每名剩余旅客在同一候选矩形内的最小放置成本与跨度成本结合，
    # 避免把“各自最便宜但彼此相距很远”的座位错误地同时用于松弛下界。
    passenger_rectangle_cost: Dict[int, np.ndarray] = {}
    for passenger_index in order:
        grid = np.full(
            (len(row_values), len(x_values)),
            np.inf,
            dtype=np.float64,
        )
        for option in domains[passenger_index]:
            row_index = row_value_index[row_coordinate[option.seat_id]]
            x_index = x_value_index[x_coordinate[option.seat_id]]
            grid[row_index, x_index] = min(
                grid[row_index, x_index],
                base_cost[option.signature],
            )
        rectangle_minimum = np.full_like(rectangle_exact_cost, np.inf)
        for row_low in range(len(row_values)):
            for row_high in range(row_low, len(row_values)):
                column_minimum = np.min(
                    grid[row_low:row_high + 1, :], axis=0
                )
                for x_low in range(len(x_values)):
                    running_minimum = math.inf
                    for x_high in range(x_low, len(x_values)):
                        running_minimum = min(
                            running_minimum,
                            float(column_minimum[x_high]),
                        )
                        rectangle_minimum[
                            row_low, row_high, x_low, x_high
                        ] = running_minimum
        passenger_rectangle_cost[passenger_index] = rectangle_minimum

    suffix_rectangle_bound: List[np.ndarray] = []
    for depth in range(len(order) + 1):
        combined = rectangle_exact_cost.copy()
        for passenger_index in order[depth:]:
            combined = combined + passenger_rectangle_cost[passenger_index]
        combined = np.minimum.accumulate(combined, axis=0)
        combined = np.minimum.accumulate(
            combined[:, ::-1, :, :], axis=1
        )[:, ::-1, :, :]
        combined = np.minimum.accumulate(combined, axis=2)
        combined = np.minimum.accumulate(
            combined[:, :, :, ::-1], axis=3
        )[:, :, :, ::-1]
        suffix_rectangle_bound.append(combined)

    # 同组婴儿修正在原目标中可能为负。把所有潜在配对都暂时视为可同时
    # 激活得到保守下界；这可能偏松，但绝不会错误剪枝。
    baby_relaxation = 0.0
    if not phase_one and baby_cost:
        baby_relaxation = -_same_group_baby_cost_upper_bound(
            cache, baby_cost, len(passengers)
        )

    selected: List[Optional[Placement]] = [None] * len(passengers)
    best_pattern: Optional[ExactPattern] = None
    best_rc = math.inf
    negative_patterns: Dict[
        Tuple[PlacementSignature, ...], ExactPattern
    ] = {}
    negative_signatures_seen: Set[Tuple[PlacementSignature, ...]] = set()
    negative_patterns_seen = 0
    nodes = 0
    aborted_reason: Optional[str] = None
    state_best_cost: Dict[tuple, float] = {}
    negative_stop_at: Optional[float] = None
    caregiver_specs = workspace["caregiver_specs"]

    def retain_negative(pattern: ExactPattern) -> None:
        """Keep only the best configured columns while counting unique hits."""
        nonlocal negative_patterns_seen
        negative_patterns_seen += 1
        negative_signatures_seen.add(pattern.signature)
        negative_patterns[pattern.signature] = pattern
        if len(negative_patterns) > pool_limit:
            worst_signature = max(
                negative_patterns,
                key=lambda signature: _master_column_reduced_cost(
                    negative_patterns[signature], duals, baby_cost,
                    phase_one=phase_one,
                ),
            )
            del negative_patterns[worst_signature]

    # A historical solution (normally the heuristic or the best current RMP
    # column under the latest duals) is a genuine DFS incumbent, not merely a
    # branch-order hint.  Rebuild and validate it before using its objective as
    # an upper cutoff.  This keeps certification exact while often pruning a
    # large part of the tree immediately.
    use_historical_incumbent = bool(
        pricing_cfg.get("dfs_use_historical_incumbent", True)
    )
    historical_by_passenger = {
        signature[0]: signature
        for signature in (
            cache.historical_mip_start or ()
            if use_historical_incumbent else ()
        )
    }
    incumbent_seeded = False
    if len(historical_by_passenger) == len(passengers):
        option_by_signature = workspace["option_by_signature"]
        historical_options = [
            option_by_signature.get(historical_by_passenger[index])
            for index in range(len(passengers))
        ]
        if all(option is not None for option in historical_options):
            incumbent_options = [
                option for option in historical_options if option is not None
            ]
            incumbent_resources = [
                resource
                for option in incumbent_options
                for resource in option.resources
            ]
            if (
                len(incumbent_resources) == len(set(incumbent_resources))
                and _caregiver_ok(
                    group, incumbent_options, new_topology, config
                )
            ):
                incumbent_pattern = _pattern_from_placements(
                    group, incumbent_options, new_topology, weights,
                    config, baby_cost,
                )
                best_pattern = incumbent_pattern
                best_rc = _master_column_reduced_cost(
                    incumbent_pattern, duals, baby_cost,
                    phase_one=phase_one,
                )
                if best_rc < -tolerance:
                    retain_negative(incumbent_pattern)
                incumbent_seeded = True

    # Exact symmetry breaking is safe only for truly interchangeable
    # passengers: identical feasible physical placements, costs, SSR resource
    # coefficients and caregiver role.  Keep their physical placements in
    # passenger-index order, retaining one representative of every permutation.
    def physical_option_key(option: Placement) -> tuple:
        return (
            option.seat_id,
            option.blocked,
            option.resources,
            round(float(option.individual_cost), 12),
            option.ssr_resources,
            option.ssr_flag_locations,
            option.is_infant,
        )

    symmetry_class: Dict[int, Tuple[int, ...]] = {}
    symmetry_buckets: Dict[tuple, List[int]] = defaultdict(list)
    if bool(pricing_cfg.get("dfs_symmetry_breaking_enabled", True)):
        # Most production passengers differ by old seat and therefore cannot
        # be interchangeable.  A cheap raw-data fingerprint avoids building
        # and sorting thousands of physical option descriptors for singleton
        # classes.  The detailed option fingerprint below remains the final
        # correctness check for every non-singleton candidate bucket.
        raw_buckets: Dict[str, List[int]] = defaultdict(list)
        for passenger_index in range(len(passengers)):
            passenger = passengers[passenger_index]
            raw_passenger = {
                key: value for key, value in passenger.items()
                if key != "hostnum"
            }
            raw_buckets[json.dumps(
                raw_passenger,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )].append(passenger_index)
        for raw_bucket in raw_buckets.values():
            if len(raw_bucket) < 2:
                continue
            for passenger_index in raw_bucket:
                passenger = passengers[passenger_index]
                descriptor = (
                    tuple(sorted(
                        physical_option_key(option)
                        for option in domains[passenger_index]
                    )),
                    _caregiver_cross_aisle(passenger, config),
                    evaluator._is_adult_caregiver(passenger),
                )
                symmetry_buckets[descriptor].append(passenger_index)
    for bucket in symmetry_buckets.values():
        if len(bucket) > 1:
            members = tuple(sorted(bucket))
            for passenger_index in members:
                symmetry_class[passenger_index] = members

    symmetry_rank: Dict[PlacementSignature, int] = {}
    for members in {members for members in symmetry_class.values()}:
        representative = members[0]
        ordered_keys = sorted({
            physical_option_key(option)
            for option in domains[representative]
        })
        rank_by_key = {
            option_key: rank
            for rank, option_key in enumerate(ordered_keys)
        }
        for passenger_index in members:
            for option in domains[passenger_index]:
                symmetry_rank[option.signature] = rank_by_key[
                    physical_option_key(option)
                ]

    def symmetry_ok(passenger_index: int, option: Placement) -> bool:
        members = symmetry_class.get(passenger_index)
        if members is None:
            return True
        rank = symmetry_rank[option.signature]
        for other_index in members:
            other = selected[other_index]
            if other is None:
                continue
            other_rank = symmetry_rank[other.signature]
            if other_index < passenger_index and other_rank > rank:
                return False
            if other_index > passenger_index and other_rank < rank:
                return False
        return True

    symmetry_class_count = len({members for members in symmetry_class.values()})
    bound_prunes = 0
    resource_prunes = 0
    symmetry_prunes = 0
    if stop_on_negative and best_rc < -tolerance:
        negative_stop_at = min(
            local_deadline,
            time.perf_counter() + max(
                0.0,
                float(pricing_cfg.get(
                    "dfs_negative_refinement_time", 0.25
                )),
            ),
        )

    def caregiver_still_possible(used_mask: int) -> bool:
        """已放置受照顾旅客必须仍有已选或未来可选的相邻成人。"""
        for cared_index, allow_cross, caregivers in caregiver_specs:
            cared = selected[cared_index]
            if cared is None:
                continue
            neighbors = set(new_topology.row_neighbors(
                cared.seat_id, allow_cross_aisle=allow_cross
            ))
            possible = False
            for caregiver_index in caregivers:
                chosen = selected[caregiver_index]
                if chosen is not None:
                    if chosen.seat_id in neighbors:
                        possible = True
                        break
                    continue
                if any(
                    option.seat_id in neighbors
                    and not (
                        resource_mask[option.signature] & used_mask
                    )
                    for option in domains[caregiver_index]
                ):
                    possible = True
                    break
            if not possible:
                return False
        return True

    def caregiver_dominance_state() -> Tuple[int, ...]:
        """Return the caregiver information that affects future choices."""
        state: List[int] = []
        for cared_index, allow_cross, caregivers in caregiver_specs:
            cared = selected[cared_index]
            if cared is None:
                state.append(-1)
                continue
            neighbor_mask = sum(
                seat_bit[seat_id]
                for seat_id in new_topology.row_neighbors(
                    cared.seat_id, allow_cross_aisle=allow_cross
                )
            )
            satisfied = any(
                selected[caregiver_index] is not None
                and seat_bit[selected[caregiver_index].seat_id]
                & neighbor_mask
                for caregiver_index in caregivers
            )
            state.append(0 if satisfied else neighbor_mask)
        return tuple(state)

    def lower_bound(
        depth: int,
        used_mask: int,
        active_flag_mask: int,
        additive_cost: float,
        min_row: Optional[float],
        max_row: Optional[float],
        min_x: Optional[float],
        max_x: Optional[float],
    ) -> float:
        # MP1 上界行的理论对偶使激活成本非负。仍把所有尚未激活的负成本
        # 各放松一次，以抵御求解器数值噪声或未来行方向变化，保持剪枝安全。
        remaining_negative_flag_cost = sum(
            cost
            for bit, cost in flag_cost.items()
            if cost < 0.0 and not active_flag_mask & bit
        )
        constant = (
            additive_cost
            + remaining_negative_flag_cost
            + baby_relaxation
            - group_dual
        )
        if min_row is not None and max_row is not None:
            indexes = (
                row_value_index[min_row],
                row_value_index[max_row],
                x_value_index[min_x],
                x_value_index[max_x],
            )
            span_bound = float(rectangle_span_bound[indexes])
            coupled_bound = float(
                suffix_rectangle_bound[depth][indexes]
            )
        else:
            span_bound = root_rectangle_bound
            coupled_bound = float(
                np.min(suffix_rectangle_bound[depth])
            )
        independent_bound = constant + span_bound
        for passenger_index in order[depth:]:
            minimum = math.inf
            for option in domains[passenger_index]:
                if not resource_mask[option.signature] & used_mask:
                    minimum = base_cost[option.signature]
                    break
            if not math.isfinite(minimum):
                return math.inf
            independent_bound += minimum
        return max(
            independent_bound,
            constant + coupled_bound,
        )

    def search(
        depth: int,
        used_mask: int,
        occupied_mask: int,
        infant_mask: int,
        active_flag_mask: int,
        additive_cost: float,
        min_row: Optional[float],
        max_row: Optional[float],
        min_x: Optional[float],
        max_x: Optional[float],
    ) -> bool:
        """返回True表示已按“找到任意负列”规则提前结束。"""
        nonlocal nodes, aborted_reason, best_pattern, best_rc
        nonlocal negative_stop_at, bound_prunes, resource_prunes
        nonlocal symmetry_prunes
        nodes += 1
        if (
            negative_stop_at is not None
            and time.perf_counter() >= negative_stop_at
        ):
            # This is deliberate multi-column collection after a negative
            # column was found, never a proof that the remaining tree is empty.
            aborted_reason = "dfs_negative_refinement_limit"
            return True
        if nodes > node_limit:
            aborted_reason = "dfs_node_limit"
            return True
        if time.perf_counter() >= local_deadline:
            aborted_reason = "dfs_time_limit"
            return True
        # 陪护状态只需记录“尚未满足时可接受的邻座位图”。加上深度和资源
        # 位图后，未来可行域完全一致，因此陪护组也可安全使用状态支配。
        state = (
            depth,
            used_mask,
            occupied_mask,
            infant_mask,
            active_flag_mask,
            caregiver_dominance_state(),
        )
        previous_cost = state_best_cost.get(state)
        if (
            previous_cost is not None
            and previous_cost <= additive_cost + tolerance
        ):
            return False
        state_best_cost[state] = additive_cost

        bound = lower_bound(
            depth, used_mask, active_flag_mask, additive_cost,
            min_row, max_row, min_x, max_x,
        )
        if stop_on_negative:
            cutoff = -tolerance
        elif best_rc < -tolerance:
            # A complete exact traversal may retain several useful negative
            # columns. The kth retained cost is a weaker (therefore safe)
            # pruning cutoff than the best cost and still proves the optimum.
            cutoff = (
                -tolerance
                if len(negative_patterns) < pool_limit
                else max(
                    _master_column_reduced_cost(
                        pattern, duals, baby_cost, phase_one=phase_one
                    )
                    for pattern in negative_patterns.values()
                ) - tolerance
            )
        else:
            cutoff = best_rc - tolerance
        if bound >= cutoff:
            bound_prunes += 1
            return False
        if depth == len(order):
            chosen = [
                option for option in selected if option is not None
            ]
            if len(chosen) != len(passengers) or not _caregiver_ok(
                group, chosen, new_topology, config
            ):
                return False
            pattern = _pattern_from_placements(
                group, chosen, new_topology, weights, config, baby_cost
            )
            rc = _master_column_reduced_cost(
                pattern, duals, baby_cost, phase_one=phase_one
            )
            if rc < best_rc:
                best_rc = rc
                best_pattern = pattern
                cache.historical_mip_start = pattern.signature
                if (
                    stop_on_negative
                    and rc < -tolerance
                    and negative_stop_at is None
                ):
                    negative_stop_at = min(
                        local_deadline,
                        time.perf_counter() + max(
                            0.0,
                            float(pricing_cfg.get(
                                "dfs_negative_refinement_time", 0.25
                            )),
                        ),
                    )
            if rc < -tolerance:
                retain_negative(pattern)
            return False

        passenger_index = order[depth]
        for option in branch_domains[passenger_index]:
            mask = resource_mask[option.signature]
            if mask & used_mask:
                resource_prunes += 1
                continue
            if not symmetry_ok(passenger_index, option):
                symmetry_prunes += 1
                continue
            selected[passenger_index] = option
            if not caregiver_still_possible(used_mask | mask):
                selected[passenger_index] = None
                continue
            row = row_coordinate[option.seat_id]
            x = x_coordinate[option.seat_id]
            newly_active_flag_cost = sum(
                flag_cost[bit]
                for bit in option_flag_bits[option.signature]
                if not active_flag_mask & bit
            )
            stopped = search(
                depth + 1,
                used_mask | mask,
                occupied_mask | seat_bit[option.seat_id],
                (
                    infant_mask | seat_bit[option.seat_id]
                    if option.is_infant else infant_mask
                ),
                active_flag_mask | option_flag_mask[option.signature],
                additive_cost
                + base_cost[option.signature]
                + newly_active_flag_cost,
                row if min_row is None else min(min_row, row),
                row if max_row is None else max(max_row, row),
                x if min_x is None else min(min_x, x),
                x if max_x is None else max(max_x, x),
            )
            selected[passenger_index] = None
            if stopped:
                return True
        return False

    # 即使搜索在任意深度超时，根节点松弛仍是整个完整定价域的严格下界。
    dynamic_refresh_seconds = time.perf_counter() - dynamic_refresh_started
    root_reduced_cost_lower_bound = lower_bound(
        0, 0, 0, 0.0, None, None, None, None
    )
    search(0, 0, 0, 0, 0, 0.0, None, None, None, None)
    complete = aborted_reason is None
    termination = (
        (
            "dfs_negative"
            if aborted_reason == "dfs_negative_refinement_limit"
            else aborted_reason
        )
        or ("dfs_negative" if best_rc < -tolerance else "dfs_optimal")
    )
    patterns = tuple(sorted(
        negative_patterns.values(),
        key=lambda pattern: _master_column_reduced_cost(
            pattern, duals, baby_cost, phase_one=phase_one
        ),
    )[:pool_limit])
    if not patterns and best_pattern is not None:
        patterns = (best_pattern,)
    _progress(
        config,
        f"MP2 group={gid} DFS finished ({termination}): "
        f"passengers={len(passengers)}, placements={len(cache.universe)}, "
        f"nodes={nodes}, elapsed={time.perf_counter() - started:.3f}s, "
        f"reduced_cost="
        f"{'none' if not math.isfinite(best_rc) else f'{best_rc:.6f}'}",
        detail=True,
    )
    elapsed = time.perf_counter() - started
    return PricingResult(
        patterns=patterns,
        reduced_cost=best_rc,
        proven_optimal=complete,
        nodes=nodes,
        priced_placements=len(cache.universe),
        elapsed=elapsed,
        termination=termination,
        complete_domain_mip_solves=0,
        reduced_cost_lower_bound=(
            best_rc if complete else root_reduced_cost_lower_bound
        ),
        dfs_nodes=nodes,
        dfs_elapsed=elapsed,
        dfs_incumbent_seeded=incumbent_seeded,
        dfs_bound_prunes=bound_prunes,
        dfs_resource_prunes=resource_prunes,
        dfs_symmetry_prunes=symmetry_prunes,
        dfs_symmetry_classes=symmetry_class_count,
        dfs_negative_patterns_seen=negative_patterns_seen,
        dfs_unique_negative_patterns=len(negative_signatures_seen),
        dfs_patterns_returned=len(patterns),
        dfs_seconds_per_returned_column=(
            elapsed / len(patterns) if patterns else 0.0
        ),
        dfs_workspace_builds=workspace_builds,
        dfs_workspace_reuses=workspace_reuses,
        dfs_workspace_build_seconds=(
            workspace_seconds if workspace_builds else 0.0
        ),
        dfs_dynamic_refresh_seconds=dynamic_refresh_seconds,
    )


def _add_sparse_row(
    solver: highspy.Highs,
    lower: float,
    upper: float,
    coefficients: Mapping[int, float],
) -> None:
    """向 HiGHS 追加一条稀疏行，并丢弃数值为零的系数。"""
    items = [(int(col), float(value)) for col, value in coefficients.items()
             if abs(value) > 1e-15]
    indexes = np.asarray([col for col, _ in items], dtype=np.int32)
    values = np.asarray([value for _, value in items], dtype=np.float64)
    status = solver.addRow(lower, upper, len(items), indexes, values)
    if status != highspy.HighsStatus.kOk:
        raise RuntimeError("MP2 addRow failed")


def _price_group_complete_domain_mip(
    group: dict,
    new_topology: evaluator.SeatTopology,
    old_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    forced_placements: Optional[Set[PlacementSignature]] = None,
    forbidden_placements: Optional[Set[PlacementSignature]] = None,
    deadline: Optional[float] = None,
    pricing_cache: Optional[GroupPricingCache] = None,
    *,
    exact: bool = True,
    phase_one: bool = False,
    stop_on_negative: bool = False,
) -> PricingResult:
    """在完整旅客—座位域上一次建立并求解精确 MP2 MILP。

    全部可行放置列一次物化，陪护、同行紧凑度和同组婴儿修正均在同一
    个 MILP 中精确表达，
    由 HiGHS 原生分支定界关闭。模型使用的是完整域，不受启发式候选数
    ``candidate_cap`` 限制。
    """
    started = time.perf_counter()
    limits = _limits(config)
    pricing_cfg = config.get("column_generation", {})
    gid = int(group["groupId"])
    passengers = group.get("psrs", [])
    cache = pricing_cache or _build_group_pricing_cache(
        group, new_topology, old_topology, weights, config
    )
    all_options = cache.all_options
    universe = cache.universe
    universe_signatures = {
        option.signature for option in universe
    }
    forced_placements = forced_placements or set()
    forbidden_placements = forbidden_placements or set()
    forced_passengers = [signature[0] for signature in forced_placements]
    if (
        forced_placements.intersection(forbidden_placements)
        or len(forced_passengers) != len(set(forced_passengers))
        or any(
            signature not in universe_signatures
            for signature in forced_placements
        )
    ):
        return PricingResult(
            patterns=(), reduced_cost=math.inf, proven_optimal=True,
            nodes=0, priced_placements=0,
            elapsed=time.perf_counter() - started,
            termination="infeasible_branch", complete_domain_mip_solves=0,
        )
    local_deadline = min(
        started + limits["mp2_time_limit"],
        deadline if deadline is not None else math.inf,
    )
    if not exact:
        local_deadline = min(
            local_deadline,
            started + float(
                config.get("column_generation", {}).get(
                    "heuristic_mp2_time_limit", 2.0
                )
            ),
        )

    def deadline_result() -> PricingResult:
        """模型尚未求解即到全局/组时限时安全退出，不伪造检验数界。"""
        return PricingResult(
            patterns=(),
            reduced_cost=math.inf,
            proven_optimal=False,
            nodes=0,
            priced_placements=len(universe),
            elapsed=time.perf_counter() - started,
            termination="mp2_time_limit",
            complete_domain_mip_solves=0,
        )

    if time.perf_counter() >= local_deadline:
        return deadline_result()
    if limits["mp2_node_limit"] <= 0:
        return PricingResult(
            patterns=(), reduced_cost=math.inf, proven_optimal=False,
            nodes=0, priced_placements=0, elapsed=time.perf_counter() - started,
            termination="mp2_node_limit", complete_domain_mip_solves=0,
        )

    baby_infant_dual, baby_occupant_dual = _aggregate_baby_duals(
        baby_cost, duals
    )
    flagged_duals = _aggregate_flagged_location_duals(duals)

    persistent = cache.persistent_mp2
    if persistent is not None and persistent["baby_pairs"] != frozenset(baby_cost):
        cache.persistent_mp2 = None
        persistent = None
    if persistent is not None and not bool(
        pricing_cfg.get("benchmark_legacy_pricing", False)
    ):
        return _reuse_complete_domain_mip(
            persistent, group, new_topology, weights, config, duals,
            baby_cost, forced_placements, forbidden_placements,
            local_deadline, cache, exact=exact, phase_one=phase_one,
            stop_on_negative=stop_on_negative,
        )

    solver = _new_highs()
    model_build_started = time.perf_counter()
    remaining = max(0.001, local_deadline - time.perf_counter())
    solver.setOptionValue("time_limit", remaining)
    node_limit = limits["mp2_node_limit"]
    if not exact:
        heuristic_cfg = config.get("column_generation", {})
        node_limit = min(
            node_limit, int(heuristic_cfg.get("heuristic_mp2_node_limit", 200))
        )
        solver.setOptionValue(
            "time_limit",
            min(
                remaining,
                float(heuristic_cfg.get("heuristic_mp2_time_limit", 2.0)),
            ),
        )
    solver.setOptionValue("mip_max_nodes", int(node_limit))
    solver.setOptionValue("mip_rel_gap", 0.0)
    if stop_on_negative:
        # MP2 模型目标尚未减去同行组选择行对偶。达到该目标即可确认
        # 当前整数方案是真实负列；此时只返回候选，不声称完成最优性证明。
        target_margin = max(
            10.0 * limits["tolerance"],
            float(
                config.get("column_generation", {}).get(
                    "negative_pricing_target_margin", 1e-6
                )
            ),
        )
        solver.setOptionValue(
            "objective_target",
            float(duals.group.get(gid, 0.0)) - target_margin,
        )

    empty_i = np.empty(0, dtype=np.int32)
    empty_v = np.empty(0, dtype=np.float64)

    def add_variable(
        objective: float, lower: float, upper: float, *,
        integer: bool = False,
    ) -> int:
        status = solver.addCol(
            float(objective), float(lower), float(upper),
            0, empty_i, empty_v,
        )
        if status != highspy.HighsStatus.kOk:
            raise RuntimeError("MP2 addCol failed")
        column = solver.getNumCol() - 1
        if integer:
            solver.changeColIntegrality(column, highspy.HighsVarType.kInteger)
        return column

    placement_col: Dict[tuple, int] = {}
    for option_index, option in enumerate(universe):
        if (
            option_index % 128 == 0
            and time.perf_counter() >= local_deadline
        ):
            return deadline_result()
        forced = option.signature in forced_placements
        forbidden = option.signature in forbidden_placements
        placement_col[option.signature] = add_variable(
            _placement_base_reduced_cost(
                option,
                duals,
                baby_infant_dual,
                baby_occupant_dual,
                phase_one=phase_one,
            ),
            1.0 if forced else 0.0,
            0.0 if forbidden else 1.0,
            integer=True,
        )

    # 条件 SSR 行按同行组和位置激活一次。使用 OR 变量连接该位置的所有
    # 标记旅客放置，避免两名旅客位于同一排/小排时重复计算激活对偶。
    flagged_location_columns: Dict[
        Tuple[str, Any], Set[int]
    ] = defaultdict(set)
    flagged_active_col: Dict[Tuple[str, Any], int] = {}
    for option in universe:
        column = placement_col[option.signature]
        for kind, location in option.ssr_flag_locations:
            flagged_location_columns[(str(kind), location)].add(column)
    for (kind, location), columns in flagged_location_columns.items():
        if time.perf_counter() >= local_deadline:
            return deadline_result()
        active = add_variable(
            -flagged_duals.get((gid, kind, location), 0.0),
            0.0,
            1.0,
        )
        flagged_active_col[(kind, location)] = active
        for column in columns:
            _add_sparse_row(
                solver,
                0.0,
                highspy.kHighsInf,
                {active: 1.0, column: -1.0},
            )
        relation = {active: 1.0}
        relation.update({column: -1.0 for column in columns})
        _add_sparse_row(
            solver,
            -highspy.kHighsInf,
            0.0,
            relation,
        )

    # 每位旅客恰选一个真实座位放置。
    for options in all_options:
        if time.perf_counter() >= local_deadline:
            return deadline_result()
        _add_sparse_row(
            solver, 1.0, 1.0,
            {placement_col[option.signature]: 1.0 for option in options},
        )

    # 每个普通占座和每一份保护空座需求都独占该座位资源。
    seat_occupancy: Dict[str, Dict[int, float]] = defaultdict(dict)
    blocked_occupancy: Dict[str, Dict[int, float]] = defaultdict(dict)
    infant_occupancy: Dict[str, Dict[int, float]] = defaultdict(dict)
    for option in universe:
        column = placement_col[option.signature]
        for blocked_seat in option.blocked:
            blocked_occupancy[blocked_seat][column] = 1.0
        seat_occupancy[option.seat_id][column] = 1.0
        if passengers[option.passenger_index].get("ssr") == "BSCT":
            infant_occupancy[option.seat_id][column] = 1.0
    resource_seats = set(seat_occupancy).union(blocked_occupancy)
    for seat in resource_seats:
        if time.perf_counter() >= local_deadline:
            return deadline_result()
        capacity: Dict[int, float] = {}
        for expression in (
            seat_occupancy.get(seat, {}),
            blocked_occupancy.get(seat, {}),
        ):
            for column, value in expression.items():
                capacity[column] = capacity.get(column, 0.0) + value
        _add_sparse_row(
            solver, -highspy.kHighsInf, 1.0, capacity
        )

    compactness = _compactness_costs(
        weights, config, phase_one=phase_one
    )
    compactness_objective_cols: List[Tuple[int, float]] = []
    phase_two_compactness = _compactness_costs(
        weights, config, phase_one=False
    )
    reachable = cache.seat_ids
    # PDF模型：以全组座位坐标的均值为重心，分别最小化所有旅客到
    # 重心的最大横向和纵向绝对偏差。坐标平移不影响该目标，因此使用
    # GroupPricingCache中的归一化坐标可减小数值尺度。
    if reachable and len(passengers) > 1:
        coordinates = (
            (
                cache.x_coordinate, compactness.centroid_x,
                phase_two_compactness.centroid_x, cache.x_big_m,
            ),
            (
                cache.row_coordinate, compactness.centroid_y,
                phase_two_compactness.centroid_y, cache.row_big_m,
            ),
        )
        passenger_count = float(len(passengers))
        for coordinate, objective_cost, phase_two_cost, upper_bound in coordinates:
            max_deviation = add_variable(
                objective_cost, 0.0, max(0.0, upper_bound)
            )
            compactness_objective_cols.append((max_deviation, phase_two_cost))
            mean_expression: Dict[int, float] = {}
            passenger_expressions: List[Dict[int, float]] = [
                {} for _ in passengers
            ]
            for option in universe:
                column = placement_col[option.signature]
                value = float(coordinate[option.seat_id])
                mean_expression[column] = value / passenger_count
                passenger_expressions[option.passenger_index][column] = value
            for passenger_expression in passenger_expressions:
                positive: Dict[int, float] = {max_deviation: 1.0}
                negative: Dict[int, float] = {max_deviation: 1.0}
                for column, value in mean_expression.items():
                    positive[column] = positive.get(column, 0.0) + value
                    negative[column] = negative.get(column, 0.0) - value
                for column, value in passenger_expression.items():
                    positive[column] = positive.get(column, 0.0) - value
                    negative[column] = negative.get(column, 0.0) + value
                _add_sparse_row(
                    solver, 0.0, highspy.kHighsInf, positive
                )
                _add_sparse_row(
                    solver, 0.0, highspy.kHighsInf, negative
                )

    # 陪护约束直接进入LP/MIP，不再在整数解后逐个no-good排除。
    for pi, passenger in enumerate(passengers):
        if time.perf_counter() >= local_deadline:
            return deadline_result()
        allow_cross = _caregiver_cross_aisle(passenger, config)
        if allow_cross is None:
            continue
        caregivers = [
            qi for qi, other in enumerate(passengers)
            if qi != pi and evaluator._is_adult_caregiver(other)
        ]
        for option in all_options[pi]:
            neighbors = set(new_topology.row_neighbors(
                option.seat_id, allow_cross_aisle=allow_cross
            ))
            coefficients: Dict[int, float] = {
                placement_col[option.signature]: 1.0
            }
            for qi in caregivers:
                for caregiver_option in all_options[qi]:
                    if caregiver_option.seat_id in neighbors:
                        column = placement_col[caregiver_option.signature]
                        coefficients[column] = coefficients.get(column, 0.0) - 1.0
            _add_sparse_row(
                solver, -highspy.kHighsInf, 0.0, coefficients
            )

    # 同组婴儿—占座影响精确线性化，替代旧的全局乐观常数。
    baby_auxiliary_col: Dict[BabyPair, int] = {}
    for pair_index, ((infant_seat, occupant_seat), cost) in enumerate(
        baby_cost.items()
    ):
        if (
            pair_index % 256 == 0
            and time.perf_counter() >= local_deadline
        ):
            return deadline_result()
        infant_expression = infant_occupancy.get(infant_seat)
        occupant_expression = seat_occupancy.get(occupant_seat)
        if not infant_expression or not occupant_expression:
            continue
        z = add_variable(
            0.0 if phase_one else -float(cost), 0.0, 1.0,
        )
        baby_auxiliary_col[(infant_seat, occupant_seat)] = z
        _add_sparse_row(
            solver, -highspy.kHighsInf, 0.0,
            {z: 1.0, **{
                col: -value for col, value in infant_expression.items()
            }},
        )
        _add_sparse_row(
            solver, -highspy.kHighsInf, 0.0,
            {z: 1.0, **{
                col: -value for col, value in occupant_expression.items()
            }},
        )
        lower_relation: Dict[int, float] = {z: 1.0}
        for column, value in infant_expression.items():
            lower_relation[column] = lower_relation.get(column, 0.0) - value
        for column, value in occupant_expression.items():
            lower_relation[column] = lower_relation.get(column, 0.0) - value
        _add_sparse_row(
            solver, -1.0, highspy.kHighsInf, lower_relation
        )

    model_build_seconds = time.perf_counter() - model_build_started
    cache.persistent_mp2 = {
        "solver": solver,
        "placement_col": placement_col,
        "flagged_active_col": flagged_active_col,
        "compactness_objective_cols": compactness_objective_cols,
        "baby_auxiliary_col": baby_auxiliary_col,
        "base_row_count": solver.getNumRow(),
        "baby_pairs": frozenset(baby_cost),
    }
    historical_start = cache.historical_mip_start
    if historical_start is not None:
        start_set = set(historical_start)
        start_is_compatible = (
            len(historical_start) == len(passengers)
            and start_set.issubset(universe_signatures)
            and not start_set.intersection(forbidden_placements)
            and forced_placements.issubset(start_set)
        )
        if start_is_compatible:
            start_columns = np.asarray(
                [placement_col[signature] for signature in historical_start],
                dtype=np.int32,
            )
            start_values = np.ones(len(start_columns), dtype=np.float64)
            if (
                solver.setSolution(
                    len(start_columns), start_columns, start_values
                )
                == highspy.HighsStatus.kOk
            ):
                cache.mip_start_submissions += 1

    _progress(
        config,
        f"MP2 group={gid} complete-domain {'exact' if exact else 'quick'} "
        f"MILP: passengers={len(passengers)}, placements={len(universe)}, "
        f"rows={solver.getNumRow()}, columns={solver.getNumCol()}",
        detail=True,
    )

    patterns_found: List[ExactPattern] = []
    equivalence_error_detail: Optional[Dict[str, Any]] = None
    first_reduced_cost_lower_bound = -math.inf
    nodes = 0
    optimal_first = False
    termination = "optimal"
    complete_solves = 0
    pricing_cfg = config.get("column_generation", {})
    pool_limit = max(
        1,
        int(
            pricing_cfg.get(
                "exact_pricing_columns_per_group" if exact
                else "quick_pricing_columns_per_group",
                pricing_cfg.get(
                    "pricing_columns_per_group",
                    4 if exact else 3,
                ),
            )
        ),
    )

    for pool_index in range(pool_limit):
        remaining = local_deadline - time.perf_counter()
        if remaining <= 0:
            termination = "mp2_time_limit"
            break
        solver.setOptionValue("time_limit", max(0.001, remaining))
        run_status = solver.run()
        if run_status not in (
            highspy.HighsStatus.kOk,
            highspy.HighsStatus.kWarning,
        ):
            termination = "mp2_solver_failure"
            break
        status = solver.getModelStatus()
        info = solver.getInfo()
        if pool_index == 0:
            mip_dual_bound = float(
                getattr(info, "mip_dual_bound", -math.inf)
            )
            if math.isfinite(mip_dual_bound):
                # MP2模型目标不含同行组凸性行对偶，转成真实检验数界。
                first_reduced_cost_lower_bound = (
                    mip_dual_bound - duals.group.get(gid, 0.0)
                )
        nodes += max(0, int(getattr(info, "mip_node_count", 0)))
        is_optimal = status == highspy.HighsModelStatus.kOptimal
        target_reached = (
            status == highspy.HighsModelStatus.kObjectiveTarget
        )
        if pool_index == 0:
            optimal_first = is_optimal
        if is_optimal:
            complete_solves += 1
        elif status == highspy.HighsModelStatus.kInfeasible:
            if pool_index == 0:
                optimal_first = True
            break
        elif target_reached:
            termination = "negative_objective_target"
        else:
            status_text = solver.modelStatusToString(status).lower()
            termination = (
                "mp2_time_limit" if "time" in status_text
                else "mp2_node_limit" if "iteration" in status_text
                or "node" in status_text
                else "mp2_solver_limit"
            )

        solution = solver.getSolution()
        chosen: List[Placement] = []
        for options in all_options:
            selected = [
                option for option in options
                if solution.col_value[placement_col[option.signature]] > 0.5
            ]
            if len(selected) != 1:
                chosen = []
                break
            chosen.append(selected[0])
        if not chosen or not _caregiver_ok(
            group, chosen, new_topology, config
        ):
            if pool_index == 0:
                optimal_first = False
                termination = "mp2_solution_extraction_error"
            break
        pattern = _pattern_from_placements(
            group, chosen, new_topology, weights, config, baby_cost
        )
        rc = _master_column_reduced_cost(
            pattern, duals, baby_cost, phase_one=phase_one
        )
        model_objective = float(info.objective_function_value)
        model_rc = model_objective - duals.group.get(gid, 0.0)
        equivalence_tolerance = max(
            float(pricing_cfg.get(
                "model_equivalence_tolerance", 1e-4
            )),
            100.0 * limits["tolerance"],
        )
        # 限时解的离散放置可以作为启发式列保留，但连续紧凑度辅助变量
        # 可能尚未抛光，甚至 objective_function_value 为 Infinity。此时
        # 重建方案检验数仍可用于筛列，却不能拿二者做模型等价性证明。
        equivalence_gap = _model_equivalence_gap(
            model_objective,
            rc,
            duals.group.get(gid, 0.0),
            # 快速阶段只负责提供候选列，统一使用重算检验数筛列；
            # 只有精确定价阶段才把模型目标误差视为认证失败。
            solver_optimal=is_optimal and exact,
            tolerance=equivalence_tolerance,
        )
        if equivalence_gap is not None:
            # 完整域MILP目标必须与最终方案检验数逐项一致。任何偏差都
            # 表示线性化或对偶映射漏项，此时不得用MIP状态作最优性证明。
            audit = _reduced_cost_audit(
                pattern, duals, baby_cost, phase_one=phase_one
            )
            placements_detail = [
                {
                    "passenger": [
                        int(option.passenger_key[0]),
                        int(option.passenger_key[1]),
                    ],
                    "seat": option.seat_id,
                    "blocked": list(option.blocked),
                    "individual_cost": float(option.individual_cost),
                }
                for option in chosen
            ]
            equivalence_error_detail = {
                "group_id": gid,
                "pricing_mode": "exact" if exact else "quick",
                "phase_one": phase_one,
                "pool_index": pool_index,
                "solver_status": solver.modelStatusToString(status),
                "model_objective_before_group_dual": float(
                    info.objective_function_value
                ),
                "model_rc": model_rc,
                "recomputed_rc": rc,
                "absolute_error": equivalence_gap,
                "tolerance": equivalence_tolerance,
                "selected_placements": placements_detail,
                "selected_seats": [option.seat_id for option in chosen],
                "blocked_seats": sorted({
                    seat for option in chosen for seat in option.blocked
                }),
                **audit,
            }
            _progress(
                config,
                "MP2 model equivalence error detail: "
                + json.dumps(
                    _json_safe(equivalence_error_detail),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            termination = "mp2_model_equivalence_error"
            optimal_first = False
            first_reduced_cost_lower_bound = -math.inf
            break
        cache.historical_mip_start = pattern.signature
        if not patterns_found or rc < -limits["tolerance"]:
            patterns_found.append(pattern)
        if not is_optimal or rc >= -limits["tolerance"]:
            break
        # 在同一个完整域模型上追加no-good，收集多个不同负检验数列。
        selected_columns = {
            placement_col[option.signature]: 1.0 for option in chosen
        }
        _add_sparse_row(
            solver, -highspy.kHighsInf, float(len(chosen) - 1),
            selected_columns,
        )

    unique_patterns = tuple(sorted({
        pattern.signature: pattern for pattern in patterns_found
    }.values(), key=lambda pattern: _master_column_reduced_cost(
        pattern, duals, baby_cost, phase_one=phase_one
    )))
    best_pattern = (
        unique_patterns[0] if unique_patterns else None
    )
    best_rc = (
        _master_column_reduced_cost(
            best_pattern, duals, baby_cost, phase_one=phase_one
        )
        if best_pattern is not None else math.inf
    )
    if optimal_first and math.isfinite(best_rc):
        # 最优整数方案经过独立检验数重算后，可使用更稳定的重算值。
        first_reduced_cost_lower_bound = best_rc
    _progress(
        config,
        f"MP2 group={gid} finished ({termination}): "
        f"elapsed={time.perf_counter() - started:.1f}s, nodes={nodes}, "
        f"patterns={len(unique_patterns)}, reduced_cost="
        f"{'none' if not math.isfinite(best_rc) else f'{best_rc:.6f}'}",
        detail=True,
    )
    total_elapsed = time.perf_counter() - started
    cache.complete_domain_mip_elapsed += total_elapsed
    cache.complete_domain_mip_calls += 1
    return PricingResult(
        patterns=unique_patterns,
        reduced_cost=best_rc,
        proven_optimal=optimal_first,
        nodes=nodes,
        priced_placements=len(universe),
        elapsed=total_elapsed,
        termination=termination,
        complete_domain_mip_solves=complete_solves,
        reduced_cost_lower_bound=first_reduced_cost_lower_bound,
        equivalence_error_detail=equivalence_error_detail,
        mp2_model_build_seconds=model_build_seconds,
        mp2_solve_seconds=max(0.0, total_elapsed - model_build_seconds),
        mp2_model_builds=1,
    )


def _reuse_complete_domain_mip(
    model: Dict[str, Any],
    group: dict,
    new_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    forced_placements: Set[PlacementSignature],
    forbidden_placements: Set[PlacementSignature],
    local_deadline: float,
    cache: GroupPricingCache,
    *,
    exact: bool,
    phase_one: bool,
    stop_on_negative: bool,
) -> PricingResult:
    """Reprice an existing per-group MP2 matrix by changing costs and bounds."""
    started = time.perf_counter()
    solver: highspy.Highs = model["solver"]
    placement_col: Dict[PlacementSignature, int] = model["placement_col"]
    base_row_count = int(model["base_row_count"])
    extra_rows = solver.getNumRow() - base_row_count
    if extra_rows > 0:
        indexes = np.arange(base_row_count, solver.getNumRow(), dtype=np.int32)
        status = solver.deleteRows(len(indexes), indexes)
        if status != highspy.HighsStatus.kOk:
            raise RuntimeError("MP2 persistent no-good row cleanup failed")

    limits = _limits(config)
    pricing_cfg = config.get("column_generation", {})
    gid = int(group["groupId"])
    universe = cache.universe
    all_options = cache.all_options
    baby_infant_dual, baby_occupant_dual = _aggregate_baby_duals(
        baby_cost, duals
    )
    flagged_duals = _aggregate_flagged_location_duals(duals)
    update_started = time.perf_counter()
    for option in universe:
        column = placement_col[option.signature]
        solver.changeColCost(column, _placement_base_reduced_cost(
            option, duals, baby_infant_dual, baby_occupant_dual,
            phase_one=phase_one,
        ))
        solver.changeColBounds(
            column,
            1.0 if option.signature in forced_placements else 0.0,
            0.0 if option.signature in forbidden_placements else 1.0,
        )
    for (kind, location), column in model["flagged_active_col"].items():
        solver.changeColCost(
            column, -flagged_duals.get((gid, kind, location), 0.0)
        )
    for column, phase_two_cost in model["compactness_objective_cols"]:
        solver.changeColCost(column, 0.0 if phase_one else phase_two_cost)
    for pair, column in model["baby_auxiliary_col"].items():
        solver.changeColCost(column, 0.0 if phase_one else -float(baby_cost[pair]))
    objective_update_seconds = time.perf_counter() - update_started

    remaining = max(0.001, local_deadline - time.perf_counter())
    solver.setOptionValue("time_limit", remaining)
    node_limit = limits["mp2_node_limit"]
    if not exact:
        node_limit = min(
            node_limit, int(pricing_cfg.get("heuristic_mp2_node_limit", 200))
        )
        solver.setOptionValue("time_limit", min(
            remaining, float(pricing_cfg.get("heuristic_mp2_time_limit", 2.0))
        ))
    solver.setOptionValue("mip_max_nodes", int(node_limit))
    solver.setOptionValue("mip_rel_gap", 0.0)
    target = -highspy.kHighsInf
    if stop_on_negative:
        target_margin = max(
            10.0 * limits["tolerance"],
            float(pricing_cfg.get("negative_pricing_target_margin", 1e-6)),
        )
        target = float(duals.group.get(gid, 0.0)) - target_margin
    solver.setOptionValue("objective_target", target)

    historical_start = cache.historical_mip_start
    if historical_start is not None:
        start_set = set(historical_start)
        if (
            len(historical_start) == len(group.get("psrs", []))
            and start_set.issubset(placement_col)
            and not start_set.intersection(forbidden_placements)
            and forced_placements.issubset(start_set)
        ):
            columns = np.asarray(
                [placement_col[signature] for signature in historical_start],
                dtype=np.int32,
            )
            if solver.setSolution(
                len(columns), columns, np.ones(len(columns), dtype=np.float64)
            ) == highspy.HighsStatus.kOk:
                cache.mip_start_submissions += 1

    patterns_found: List[ExactPattern] = []
    equivalence_error_detail: Optional[Dict[str, Any]] = None
    first_reduced_cost_lower_bound = -math.inf
    nodes = 0
    optimal_first = False
    termination = "optimal"
    complete_solves = 0
    pool_limit = max(1, int(pricing_cfg.get(
        "exact_pricing_columns_per_group" if exact
        else "quick_pricing_columns_per_group",
        pricing_cfg.get("pricing_columns_per_group", 4 if exact else 3),
    )))
    solve_started = time.perf_counter()
    for pool_index in range(pool_limit):
        remaining = local_deadline - time.perf_counter()
        if remaining <= 0:
            termination = "mp2_time_limit"
            break
        solver.setOptionValue("time_limit", max(0.001, remaining))
        run_status = solver.run()
        if run_status not in (highspy.HighsStatus.kOk, highspy.HighsStatus.kWarning):
            termination = "mp2_solver_failure"
            break
        status = solver.getModelStatus()
        info = solver.getInfo()
        if pool_index == 0:
            mip_dual_bound = float(getattr(info, "mip_dual_bound", -math.inf))
            if math.isfinite(mip_dual_bound):
                first_reduced_cost_lower_bound = (
                    mip_dual_bound - duals.group.get(gid, 0.0)
                )
        nodes += max(0, int(getattr(info, "mip_node_count", 0)))
        is_optimal = status == highspy.HighsModelStatus.kOptimal
        target_reached = status == highspy.HighsModelStatus.kObjectiveTarget
        if pool_index == 0:
            optimal_first = is_optimal
        if is_optimal:
            complete_solves += 1
        elif status == highspy.HighsModelStatus.kInfeasible:
            if pool_index == 0:
                optimal_first = True
            break
        elif target_reached:
            termination = "negative_objective_target"
        else:
            status_text = solver.modelStatusToString(status).lower()
            termination = (
                "mp2_time_limit" if "time" in status_text
                else "mp2_node_limit" if "iteration" in status_text or "node" in status_text
                else "mp2_solver_limit"
            )
        solution = solver.getSolution()
        chosen: List[Placement] = []
        for options in all_options:
            selected = [
                option for option in options
                if solution.col_value[placement_col[option.signature]] > 0.5
            ]
            if len(selected) != 1:
                chosen = []
                break
            chosen.append(selected[0])
        if not chosen or not _caregiver_ok(group, chosen, new_topology, config):
            if pool_index == 0:
                optimal_first = False
                termination = "mp2_solution_extraction_error"
            break
        pattern = _pattern_from_placements(
            group, chosen, new_topology, weights, config, baby_cost
        )
        rc = _master_column_reduced_cost(
            pattern, duals, baby_cost, phase_one=phase_one
        )
        model_objective = float(info.objective_function_value)
        model_rc = model_objective - duals.group.get(gid, 0.0)
        equivalence_tolerance = max(
            float(pricing_cfg.get("model_equivalence_tolerance", 1e-4)),
            100.0 * limits["tolerance"],
        )
        equivalence_gap = _model_equivalence_gap(
            model_objective, rc, duals.group.get(gid, 0.0),
            solver_optimal=is_optimal and exact,
            tolerance=equivalence_tolerance,
        )
        if equivalence_gap is not None:
            equivalence_error_detail = {
                "group_id": gid,
                "pricing_mode": "exact" if exact else "quick",
                "phase_one": phase_one,
                "pool_index": pool_index,
                "solver_status": solver.modelStatusToString(status),
                "model_objective_before_group_dual": model_objective,
                "model_rc": model_rc,
                "recomputed_rc": rc,
                "absolute_error": equivalence_gap,
                "tolerance": equivalence_tolerance,
                "selected_seats": [option.seat_id for option in chosen],
                "blocked_seats": sorted({
                    seat for option in chosen for seat in option.blocked
                }),
                **_reduced_cost_audit(
                    pattern, duals, baby_cost, phase_one=phase_one
                ),
            }
            termination = "mp2_model_equivalence_error"
            optimal_first = False
            first_reduced_cost_lower_bound = -math.inf
            break
        cache.historical_mip_start = pattern.signature
        if not patterns_found or rc < -limits["tolerance"]:
            patterns_found.append(pattern)
        if not is_optimal or rc >= -limits["tolerance"]:
            break
        _add_sparse_row(
            solver, -highspy.kHighsInf, float(len(chosen) - 1),
            {placement_col[option.signature]: 1.0 for option in chosen},
        )
    solve_seconds = time.perf_counter() - solve_started
    unique_patterns = tuple(sorted({
        pattern.signature: pattern for pattern in patterns_found
    }.values(), key=lambda pattern: _master_column_reduced_cost(
        pattern, duals, baby_cost, phase_one=phase_one
    )))
    best_pattern = unique_patterns[0] if unique_patterns else None
    best_rc = (
        _master_column_reduced_cost(
            best_pattern, duals, baby_cost, phase_one=phase_one
        ) if best_pattern is not None else math.inf
    )
    if optimal_first and math.isfinite(best_rc):
        first_reduced_cost_lower_bound = best_rc
    total_elapsed = time.perf_counter() - started
    cache.complete_domain_mip_elapsed += total_elapsed
    cache.complete_domain_mip_calls += 1
    return PricingResult(
        patterns=unique_patterns, reduced_cost=best_rc,
        proven_optimal=optimal_first, nodes=nodes,
        priced_placements=len(universe), elapsed=total_elapsed,
        termination=termination, complete_domain_mip_solves=complete_solves,
        reduced_cost_lower_bound=first_reduced_cost_lower_bound,
        equivalence_error_detail=equivalence_error_detail,
        mp2_solve_seconds=solve_seconds,
        mp2_objective_update_seconds=objective_update_seconds,
        mp2_model_reuses=1,
    )


def _price_group_homogeneous_row_label_dp(
    group: dict,
    new_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    cache: GroupPricingCache,
    *,
    phase_one: bool,
    deadline: Optional[float],
) -> Optional[PricingResult]:
    """按排位图和旅客成本类型计数求解一个受限但精确的MP2。

    无耦合旅客按“相同物理域、相同逐座约化成本”归为一类。每排用
    位图表示占用资源，标签记录各类型累计人数、首末排和横向包络；
    同状态只保留成本最低标签。状态过多或存在耦合约束时安全回退。
    """
    started = time.perf_counter()
    passengers = group.get("psrs", [])
    gid = int(group["groupId"])
    pricing_cfg = config.get("column_generation", {})
    benchmark_legacy = bool(pricing_cfg.get("benchmark_legacy_pricing", False))
    threshold = max(2, int(pricing_cfg.get("row_label_dp_min_group_size", 9)))
    cache.label_dp_attempted = False
    cache.label_dp_states = 0
    cache.label_dp_type_count = 0
    cache.label_dp_reason = None
    if not bool(pricing_cfg.get("row_label_dp_enabled", True)):
        cache.label_dp_reason = "disabled"
        return None
    if len(passengers) < threshold:
        cache.label_dp_reason = "below_group_size_threshold"
        return None
    cache.label_dp_attempted = True
    if benchmark_legacy and baby_cost:
        cache.label_dp_reason = "legacy_global_baby_cost_gate"
        return None
    if any(
        passenger.get("ssr") == "BSCT" for passenger in passengers
    ):
        cache.label_dp_reason = "group_infant_coupling"
        return None
    if any(
        passenger.get("ssr")
        or passenger.get("needCared", "N") == "Y"
        or _caregiver_cross_aisle(passenger, config) is not None
        for passenger in passengers
    ):
        cache.label_dp_reason = "group_ssr_or_caregiver_coupling"
        return None

    # A baby elsewhere in the cabin does not couple this group's choices.
    # Its MP1 occupant-side dual is a per-seat additive placement term.
    _, baby_occupant_dual = _aggregate_baby_duals(baby_cost, duals)

    option_maps: List[Dict[str, Placement]] = []
    for options in cache.all_options:
        mapping: Dict[str, Placement] = {}
        for option in options:
            if (
                option.blocked
                or option.resources != (option.seat_id,)
                or option.ssr_resources
                or option.ssr_flag_locations
                or option.is_infant
            ):
                cache.label_dp_reason = "protected_or_coupled_placement"
                return None
            if option.seat_id in mapping:
                cache.label_dp_reason = "duplicate_seat_option"
                return None
            mapping[option.seat_id] = option
        option_maps.append(mapping)
    if not option_maps:
        cache.label_dp_reason = "empty_placement_domain"
        return None
    common_seats = set(option_maps[0])
    if any(set(mapping) != common_seats for mapping in option_maps[1:]):
        cache.label_dp_reason = "different_physical_domains"
        return None
    tolerance = max(1e-9, _limits(config)["tolerance"])
    cost_vectors: List[Tuple[float, ...]] = []
    ordered_seats = tuple(sorted(common_seats))
    for mapping in option_maps:
        cost_vectors.append(tuple(
            _placement_base_reduced_cost(
                mapping[seat_id], duals, {}, baby_occupant_dual,
                phase_one=phase_one
            )
            for seat_id in ordered_seats
        ))
    type_members: List[List[int]] = []
    type_costs: List[Dict[str, float]] = []
    for passenger_index, vector in enumerate(cost_vectors):
        matched = None
        for type_index, costs in enumerate(type_costs):
            if all(
                abs(vector[index] - costs[seat_id]) <= tolerance
                for index, seat_id in enumerate(ordered_seats)
            ):
                matched = type_index
                break
        if matched is None:
            matched = len(type_members)
            type_members.append([])
            type_costs.append(dict(zip(ordered_seats, vector)))
        type_members[matched].append(passenger_index)
    type_count = len(type_members)
    cache.label_dp_type_count = type_count
    maximum_types = max(
        1, int(pricing_cfg.get("row_label_dp_max_cost_types", 4))
    )
    label_mode = "cost_type_row_bitmap"
    if type_count > maximum_types:
        subset_enabled = bool(
            pricing_cfg.get("row_label_dp_passenger_subset_enabled", True)
        )
        candidate_rows = {
            int(new_topology.seat_map[seat_id]["row"])
            for seat_id in common_seats
        }
        subset_within_limits = (
            type_count <= max(
                1, int(pricing_cfg.get(
                    "row_label_dp_passenger_subset_max_passengers", 10
                ))
            )
            and len(candidate_rows) <= max(
                1, int(pricing_cfg.get(
                    "row_label_dp_passenger_subset_max_rows", 3
                ))
            )
            and len(common_seats) <= max(
                1, int(pricing_cfg.get(
                    "row_label_dp_passenger_subset_max_seats", 18
                ))
            )
        )
        if not subset_enabled or not subset_within_limits:
            cache.label_dp_reason = "too_many_cost_types"
            return None
        # Every cost type may now contain just one passenger.  The binary
        # count vector is then exactly a passenger-subset label; row masks
        # continue to enforce seat resources without changing the model.
        label_mode = "passenger_subset_row_bitmap"
    required_by_type = tuple(len(members) for members in type_members)

    rows = sorted({
        int(new_topology.seat_map[seat_id]["row"])
        for seat_id in common_seats
    })
    seats_by_row = {
        row: tuple(
            seat["seatId"]
            for seat in new_topology.row_seats[row]
            if seat["seatId"] in common_seats
        )
        for row in rows
    }
    compactness = _compactness_costs(weights, config, phase_one=phase_one)
    row_choices: Dict[
        int,
        List[
            Tuple[
                Tuple[int, ...], float, float, float, float, float,
                Tuple[Tuple[str, ...], ...],
            ]
        ],
    ] = {}
    required = len(passengers)
    state_limit_key = (
        "row_label_dp_passenger_subset_state_limit"
        if label_mode == "passenger_subset_row_bitmap"
        else "row_label_dp_state_limit"
    )
    state_limit = max(
        1000, int(pricing_cfg.get(
            state_limit_key,
            pricing_cfg.get("row_label_dp_state_limit", 500000),
        ))
    )
    local_deadline = min(
        deadline if deadline is not None else math.inf,
        started + max(
            0.01, float(pricing_cfg.get("row_label_dp_time_limit", 10.0))
        ),
    )
    states_seen = 0
    for row in rows:
        row_seats = seats_by_row[row]
        # (各类型人数, 占座位图) -> (逐座成本, 各类型所选座位)
        partial: Dict[
            Tuple[Tuple[int, ...], int],
            Tuple[float, Tuple[Tuple[str, ...], ...]],
        ] = {
            ((0,) * type_count, 0): (
                0.0, tuple(() for _ in range(type_count))
            )
        }
        for seat_index, seat_id in enumerate(row_seats):
            next_partial = dict(partial)
            for (counts, mask), (cost, selected_by_type) in partial.items():
                if sum(counts) >= required:
                    continue
                for type_index in range(type_count):
                    if counts[type_index] >= required_by_type[type_index]:
                        continue
                    new_counts = list(counts)
                    new_counts[type_index] += 1
                    new_selected = list(selected_by_type)
                    new_selected[type_index] = (
                        *new_selected[type_index], seat_id
                    )
                    key = (tuple(new_counts), mask | (1 << seat_index))
                    candidate = cost + type_costs[type_index][seat_id]
                    previous = next_partial.get(key)
                    if previous is None or candidate < previous[0] - tolerance:
                        next_partial[key] = (candidate, tuple(new_selected))
            partial = next_partial
            states_seen += len(partial)
            if time.perf_counter() >= local_deadline:
                cache.label_dp_states = states_seen
                cache.label_dp_reason = "time_limit"
                return None
            if states_seen > state_limit:
                cache.label_dp_states = states_seen
                cache.label_dp_reason = "state_limit"
                return None
        choices = []
        for (counts, mask), (cost, selected_by_type) in partial.items():
            selected = tuple(
                seat_id for index, seat_id in enumerate(row_seats)
                if mask & (1 << index)
            )
            if not selected:
                choices.append((
                    counts, 0.0, math.inf, -math.inf, 0.0, 0.0,
                    selected_by_type,
                ))
                continue
            xs = [cache.x_coordinate[seat_id] for seat_id in selected]
            ys = [cache.row_coordinate[seat_id] for seat_id in selected]
            choices.append((
                counts,
                cost,
                min(xs), max(xs),
                round(sum(xs), 12), round(sum(ys), 12),
                selected_by_type,
            ))
        row_choices[row] = choices

    # state -> (additive cost, selected seats). Empty labels use None bounds.
    labels: Dict[
        Tuple[
            Tuple[int, ...], Optional[float], Optional[float],
            Optional[float], Optional[float], float, float,
        ],
        Tuple[float, Tuple[Tuple[str, ...], ...]],
    ] = {
        ((0,) * type_count, None, None, None, None, 0.0, 0.0): (
            0.0, tuple(() for _ in range(type_count))
        )
    }
    states_seen += 1
    for row in rows:
        if time.perf_counter() >= local_deadline:
            cache.label_dp_states = states_seen
            cache.label_dp_reason = "time_limit"
            return None
        next_labels = {}
        for state, (cost, chosen_so_far) in labels.items():
            counts, min_y, max_y, min_x, max_x, sum_x, sum_y = state
            for (
                added, added_cost, choice_min_x, choice_max_x,
                choice_sum_x, choice_sum_y, selected,
            ) in row_choices[row]:
                new_counts = tuple(
                    counts[index] + added[index]
                    for index in range(type_count)
                )
                if any(
                    new_counts[index] > required_by_type[index]
                    for index in range(type_count)
                ):
                    continue
                added_total = sum(added)
                if added_total:
                    first_selected_seat = next(
                        seat_id
                        for selected_type in selected
                        for seat_id in selected_type
                    )
                    row_y = cache.row_coordinate[first_selected_seat]
                    new_state = (
                        new_counts,
                        row_y if min_y is None else min(min_y, row_y),
                        row_y if max_y is None else max(max_y, row_y),
                        choice_min_x if min_x is None else min(min_x, choice_min_x),
                        choice_max_x if max_x is None else max(max_x, choice_max_x),
                        round(sum_x + choice_sum_x, 12),
                        round(sum_y + choice_sum_y, 12),
                    )
                else:
                    new_state = state
                candidate_cost = cost + added_cost
                previous = next_labels.get(new_state)
                if previous is None or candidate_cost < previous[0] - tolerance:
                    combined = tuple(
                        chosen_so_far[index] + selected[index]
                        for index in range(type_count)
                    )
                    next_labels[new_state] = (
                        candidate_cost, combined
                    )
        labels = next_labels
        states_seen += len(labels)
        if states_seen > state_limit:
            cache.label_dp_states = states_seen
            cache.label_dp_reason = "state_limit"
            return None

    best: Optional[
        Tuple[float, Tuple[Tuple[str, ...], ...]]
    ] = None
    for state, (additive, selected) in labels.items():
        counts, min_y, max_y, min_x, max_x, sum_x, sum_y = state
        if counts != required_by_type or min_y is None:
            continue
        centroid_x = sum_x / required
        centroid_y = sum_y / required
        max_x_deviation = max(
            max_x - centroid_x, centroid_x - min_x
        )
        max_y_deviation = max(
            max_y - centroid_y, centroid_y - min_y
        )
        value = (
            additive
            + compactness.centroid_x * max_x_deviation
            + compactness.centroid_y * max_y_deviation
            - float(duals.group.get(gid, 0.0))
        )
        if best is None or value < best[0]:
            best = (value, selected)
    if best is None:
        return PricingResult(
            patterns=(), reduced_cost=math.inf, proven_optimal=True,
            nodes=states_seen, priced_placements=len(cache.universe),
            elapsed=time.perf_counter() - started,
            termination="label_dp_infeasible", complete_domain_mip_solves=0,
            reduced_cost_lower_bound=math.inf,
            label_dp_used=True, label_dp_states=states_seen,
            label_dp_attempted=True, label_dp_type_count=type_count,
            label_dp_reason=label_mode,
        )
    placements: List[Placement] = []
    for type_index, members in enumerate(type_members):
        selected_seats = sorted(
            best[1][type_index],
            key=lambda seat_id: (
                int(new_topology.seat_map[seat_id]["row"]),
                new_topology.index_in_row[seat_id],
            ),
        )
        placements.extend(
            option_maps[passenger_index][seat_id]
            for passenger_index, seat_id in zip(members, selected_seats)
        )
    placements.sort(key=lambda placement: placement.passenger_index)
    pattern = _pattern_from_placements(
        group, placements, new_topology, weights, config, baby_cost
    )
    reduced_cost = _master_column_reduced_cost(
        pattern, duals, baby_cost, phase_one=phase_one
    )
    if abs(reduced_cost - best[0]) > max(1e-5, 100 * tolerance):
        cache.label_dp_states = states_seen
        cache.label_dp_reason = "objective_audit_mismatch"
        return None
    cache.historical_mip_start = pattern.signature
    cache.label_dp_states = states_seen
    cache.label_dp_reason = label_mode
    return PricingResult(
        patterns=(pattern,), reduced_cost=reduced_cost, proven_optimal=True,
        nodes=states_seen, priced_placements=len(cache.universe),
        elapsed=time.perf_counter() - started,
        termination="label_dp_optimal", complete_domain_mip_solves=0,
        reduced_cost_lower_bound=reduced_cost,
        label_dp_used=True, label_dp_states=states_seen,
        label_dp_attempted=True, label_dp_type_count=type_count,
        label_dp_reason=label_mode,
    )


def _pair_exact_reduced_cost(
    group_id: int,
    placements: Tuple[Placement, ...],
    topology: evaluator.SeatTopology,
    weights: Mapping[str, float],
    config: Mapping[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    base_cost: Mapping[PlacementSignature, float],
    flagged_duals: Mapping[Tuple[int, str, Any], float],
    *,
    phase_one: bool,
) -> float:
    """Return the exact MP1 reduced cost for a feasible small-group tuple."""
    value = sum(base_cost[option.signature] for option in placements)
    value -= duals.group.get(group_id, 0.0)
    active_flag_locations = {
        (str(kind), location)
        for option in placements
        for kind, location in option.ssr_flag_locations
    }
    value -= sum(
        flagged_duals.get((group_id, kind, location), 0.0)
        for kind, location in active_flag_locations
    )
    if phase_one:
        return value
    occupied = {option.seat_id for option in placements}
    infant = {option.seat_id for option in placements if option.is_infant}
    value -= float(weights.get("w_c", -2.0)) * (
        evaluator.group_compactness_penalty(
            list(occupied), topology, config
        )
    )
    value -= sum(
        baby_cost.get((infant_seat, occupied_seat), 0.0)
        for infant_seat in infant
        for occupied_seat in occupied
    )
    return value


def _price_group_small_exact(
    group: dict,
    new_topology: evaluator.SeatTopology,
    weights: Dict[str, float],
    config: Dict[str, Any],
    duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    forced_placements: Optional[Set[PlacementSignature]],
    forbidden_placements: Optional[Set[PlacementSignature]],
    cache: GroupPricingCache,
    *,
    phase_one: bool,
) -> PricingResult:
    """Exactly price one- and two-passenger groups without a generic MILP."""
    started = time.perf_counter()
    passengers = group.get("psrs", [])
    size = len(passengers)
    if size not in (1, 2):
        raise ValueError("small exact pricing requires a size-1 or size-2 group")
    forced = forced_placements or set()
    forbidden = forbidden_placements or set()
    universe_signatures = {option.signature for option in cache.universe}
    forced_by_passenger: Dict[int, PlacementSignature] = {}
    invalid_branch = bool(forced.intersection(forbidden))
    for signature in forced:
        passenger_index = int(signature[0])
        invalid_branch = invalid_branch or (
            signature not in universe_signatures
            or passenger_index in forced_by_passenger
        )
        forced_by_passenger[passenger_index] = signature
    domains: List[List[Placement]] = []
    for passenger_index, options in enumerate(cache.all_options):
        required = forced_by_passenger.get(passenger_index)
        domain = [
            option for option in options
            if option.signature not in forbidden
            and (required is None or option.signature == required)
        ]
        invalid_branch = invalid_branch or not domain
        domains.append(domain)
    elapsed = time.perf_counter() - started
    call_fields = {
        "singleton_pricing_calls": int(size == 1),
        "pair_pricing_calls": int(size == 2),
        "specialized_pricing_elapsed": elapsed,
        "pair_pricing_seconds": elapsed if size == 2 else 0.0,
    }
    if invalid_branch:
        return PricingResult(
            patterns=(), reduced_cost=math.inf, proven_optimal=True,
            nodes=0, priced_placements=len(cache.universe), elapsed=elapsed,
            termination="infeasible_branch", complete_domain_mip_solves=0,
            reduced_cost_lower_bound=math.inf, **call_fields,
        )

    seat_bit = {
        seat: 1 << index for index, seat in enumerate(new_topology.seat_map)
    }
    resource_mask = {
        option.signature: sum(seat_bit[seat] for seat in option.resources)
        for option in cache.universe
    }
    tolerance = _limits(config)["tolerance"]
    pool_limit = max(1, int(config.get("column_generation", {}).get(
        "exact_pricing_columns_per_group",
        config.get("column_generation", {}).get("pricing_columns_per_group", 4),
    )))
    infant_dual, occupant_dual = _aggregate_baby_duals(baby_cost, duals)
    base_cost = {
        option.signature: _placement_base_reduced_cost(
            option, duals, infant_dual, occupant_dual,
            phase_one=phase_one,
        )
        for option in cache.universe
    }
    flagged_duals = _aggregate_flagged_location_duals(duals)
    gid = int(group["groupId"])

    combinations: Iterable[Tuple[Placement, ...]]
    if size == 1:
        combinations = ((option,) for option in domains[0])
    else:
        combinations = (
            (left, right)
            for left in domains[0] for right in domains[1]
            if not (
                resource_mask[left.signature] & resource_mask[right.signature]
            )
        )
    nodes = 0
    order = 0
    exact_feasible = 0
    rc_seconds = 0.0
    heap_seconds = 0.0
    best: Optional[Tuple[float, int, Tuple[Placement, ...]]] = None
    negative: List[Tuple[float, int, Tuple[Placement, ...]]] = []
    for placements in combinations:
        nodes += 1
        if not _caregiver_ok(group, placements, new_topology, config):
            continue
        exact_feasible += 1
        rc_started = time.perf_counter()
        reduced_cost = _pair_exact_reduced_cost(
            gid, placements, new_topology, weights, config, duals,
            baby_cost, base_cost, flagged_duals, phase_one=phase_one,
        )
        rc_seconds += time.perf_counter() - rc_started
        heap_started = time.perf_counter()
        candidate = (reduced_cost, order, placements)
        order += 1
        if best is None or candidate[:2] < best[:2]:
            best = candidate
        if candidate[0] < -tolerance:
            negative.append(candidate)
            negative.sort(key=lambda item: item[:2])
            if len(negative) > pool_limit:
                negative.pop()
        heap_seconds += time.perf_counter() - heap_started

    selected = negative or ([best] if best is not None else [])
    materialization_started = time.perf_counter()
    materialized: List[ExactPattern] = []
    for expected_rc, _, placements in selected:
        pattern = _pattern_from_placements(
            group, placements, new_topology, weights, config, baby_cost
        )
        audited_rc = _master_column_reduced_cost(
            pattern, duals, baby_cost, phase_one=phase_one
        )
        if abs(audited_rc - expected_rc) > tolerance:
            raise RuntimeError(
                "small exact pricing objective mismatch: "
                f"group={gid}, enumerated={expected_rc:.12f}, "
                f"master={audited_rc:.12f}"
            )
        materialized.append(pattern)
    materialization_seconds = time.perf_counter() - materialization_started
    patterns = tuple(materialized)
    best_pattern = patterns[0] if patterns else None
    best_rc = best[0] if best is not None else math.inf
    elapsed = time.perf_counter() - started
    cache.historical_mip_start = best_pattern.signature if best_pattern else None
    return PricingResult(
        patterns=patterns, reduced_cost=best_rc, proven_optimal=True,
        nodes=nodes, priced_placements=len(cache.universe), elapsed=elapsed,
        termination="singleton_exact" if size == 1 else "pair_exact",
        complete_domain_mip_solves=0, reduced_cost_lower_bound=best_rc,
        singleton_pricing_calls=int(size == 1),
        pair_pricing_calls=int(size == 2),
        specialized_pricing_elapsed=elapsed,
        pairs_examined=(len(domains[0]) * len(domains[1])) if size == 2 else 0,
        pairs_resource_feasible=nodes if size == 2 else 0,
        pairs_exact_feasible=exact_feasible if size == 2 else 0,
        pair_rc_seconds=rc_seconds if size == 2 else 0.0,
        pair_heap_seconds=heap_seconds if size == 2 else 0.0,
        pair_pattern_materialization_seconds=(
            materialization_seconds if size == 2 else 0.0
        ),
        pair_patterns_materialized=len(materialized) if size == 2 else 0,
        pair_patterns_returned=len(patterns) if size == 2 else 0,
        pair_pricing_seconds=elapsed if size == 2 else 0.0,
    )


def price_group_exact(
    group: dict, new_topology: evaluator.SeatTopology,
    old_topology: evaluator.SeatTopology, weights: Dict[str, float],
    config: Dict[str, Any], duals: MasterDuals,
    baby_cost: Mapping[BabyPair, float],
    forced_placements: Set[PlacementSignature] | None = None,
    forbidden_placements: Set[PlacementSignature] | None = None,
    deadline: Optional[float] = None,
    pricing_cache: Optional[GroupPricingCache] = None,
    existing_pattern_signatures: Optional[
        Set[Tuple[PlacementSignature, ...]]
    ] = None,
    *,
    exact: bool = True,
    phase_one: bool = False,
    stop_on_negative: bool = False,
) -> PricingResult:
    """大组优先使用精确DFS定价，未关闭搜索树时由HiGHS兜底认证。"""
    cache = pricing_cache or _build_group_pricing_cache(
        group, new_topology, old_topology, weights, config
    )
    pricing_cfg = config.get("column_generation", {})
    benchmark_legacy = bool(pricing_cfg.get("benchmark_legacy_pricing", False))
    cache.label_dp_attempted = False
    cache.label_dp_states = 0
    cache.label_dp_type_count = 0
    cache.label_dp_reason = "branch_restriction"
    if not benchmark_legacy and len(group.get("psrs", [])) in (1, 2):
        result = _price_group_small_exact(
            group, new_topology, weights, config, duals, baby_cost,
            forced_placements, forbidden_placements, cache,
            phase_one=phase_one,
        )
        for pattern in result.patterns:
            cache.historical_patterns[pattern.signature] = pattern
        return result

    history_started = time.perf_counter()
    history_enabled = (
        not benchmark_legacy
        and bool(pricing_cfg.get("historical_pattern_repricing_enabled", True))
    )
    existing_signatures = existing_pattern_signatures or set()
    forced = forced_placements or set()
    forbidden = forbidden_placements or set()
    historical_candidates = [
        pattern for pattern in (
            cache.historical_patterns.values() if history_enabled else ()
        )
        if pattern.signature not in existing_signatures
        and forced.issubset(set(pattern.signature))
        and not forbidden.intersection(pattern.signature)
    ]
    historical_patterns_checked = len(historical_candidates)
    tolerance = _limits(config)["tolerance"]
    historical_negative_all = [
        (
            _master_column_reduced_cost(
                pattern, duals, baby_cost, phase_one=phase_one
            ),
            pattern,
        )
        for pattern in historical_candidates
    ]
    historical_negative_all = [
        item for item in historical_negative_all if item[0] < -tolerance
    ]
    historical_negative = historical_negative_all
    historical_negative.sort(key=lambda item: item[0])
    historical_negative = historical_negative[
        :_pricing_column_limit(pricing_cfg, exact=exact)
    ]
    historical_reprice_seconds = time.perf_counter() - history_started

    def finalize(result: PricingResult, *, avoided_dfs: bool = False) -> PricingResult:
        result.label_dp_attempted = cache.label_dp_attempted
        result.label_dp_states = max(
            result.label_dp_states, cache.label_dp_states
        )
        result.label_dp_type_count = cache.label_dp_type_count
        result.label_dp_reason = cache.label_dp_reason
        result.historical_reprice_calls += int(history_enabled)
        result.historical_patterns_checked += historical_patterns_checked
        result.historical_negative_hits += len(historical_negative_all)
        result.historical_reprice_seconds += (
            historical_reprice_seconds if history_enabled else 0.0
        )
        if avoided_dfs:
            result.historical_columns_returned += len(result.patterns)
            result.dfs_calls_avoided_by_history += 1
        for pattern in result.patterns:
            cache.historical_patterns[pattern.signature] = pattern
        return result

    use_dfs = (
        bool(pricing_cfg.get("dfs_pricing_enabled", True))
        and len(group.get("psrs", [])) >= max(
            1, int(pricing_cfg.get("dfs_min_group_size", 3))
        )
    )
    if historical_negative and (not exact or stop_on_negative):
        cache.label_dp_reason = "historical_reprice"
        patterns = tuple(pattern for _, pattern in historical_negative)
        return finalize(PricingResult(
            patterns=patterns,
            reduced_cost=historical_negative[0][0],
            proven_optimal=False,
            nodes=0,
            priced_placements=historical_patterns_checked,
            elapsed=historical_reprice_seconds,
            termination="historical_negative",
            complete_domain_mip_solves=0,
        ), avoided_dfs=use_dfs)

    if not forced_placements and not forbidden_placements:
        label_result = _price_group_homogeneous_row_label_dp(
            group, new_topology, weights, config, duals, baby_cost, cache,
            phase_one=phase_one, deadline=deadline,
        )
        if label_result is not None:
            return finalize(label_result)
    dfs_result: Optional[PricingResult] = None
    if use_dfs:
        dfs_result = _price_group_exact_dfs(
            group,
            new_topology,
            weights,
            config,
            duals,
            baby_cost,
            forced_placements,
            forbidden_placements,
            deadline,
            cache,
            exact=exact,
            phase_one=phase_one,
            stop_on_negative=stop_on_negative,
        )
        if (
            (
                dfs_result.proven_optimal
                and (
                    not benchmark_legacy
                    or not exact
                    or bool(pricing_cfg.get("dfs_certification_enabled", False))
                )
            )
            or (
                dfs_result.best_pattern is not None
                and dfs_result.reduced_cost < -_limits(config)["tolerance"]
            )
        ):
            if exact and dfs_result.proven_optimal:
                dfs_result.dfs_exact_certificate = True
                dfs_result.mip_avoided_after_dfs_certificate = True
                if cache.complete_domain_mip_calls:
                    dfs_result.dfs_exact_certificate_seconds_saved = (
                        cache.complete_domain_mip_elapsed
                        / cache.complete_domain_mip_calls
                    )
            return finalize(dfs_result)

    mip_result = _price_group_complete_domain_mip(
        group, new_topology, old_topology, weights, config, duals,
        baby_cost, forced_placements, forbidden_placements,
        deadline, cache,
        exact=exact, phase_one=phase_one,
        stop_on_negative=stop_on_negative,
    )
    if dfs_result is not None:
        mip_result.elapsed += dfs_result.elapsed
        mip_result.nodes += dfs_result.nodes
        mip_result.dfs_nodes += dfs_result.dfs_nodes
        mip_result.dfs_elapsed += dfs_result.dfs_elapsed
        mip_result.priced_placements = max(
            mip_result.priced_placements,
            dfs_result.priced_placements,
        )
        mip_result.dfs_incumbent_seeded = (
            dfs_result.dfs_incumbent_seeded
        )
        mip_result.dfs_bound_prunes = dfs_result.dfs_bound_prunes
        mip_result.dfs_resource_prunes = dfs_result.dfs_resource_prunes
        mip_result.dfs_symmetry_prunes = dfs_result.dfs_symmetry_prunes
        mip_result.dfs_symmetry_classes = dfs_result.dfs_symmetry_classes
        mip_result.dfs_negative_patterns_seen = (
            dfs_result.dfs_negative_patterns_seen
        )
        mip_result.dfs_unique_negative_patterns = (
            dfs_result.dfs_unique_negative_patterns
        )
        mip_result.dfs_patterns_returned = dfs_result.dfs_patterns_returned
        mip_result.dfs_seconds_per_returned_column = (
            dfs_result.dfs_seconds_per_returned_column
        )
        mip_result.dfs_workspace_builds = dfs_result.dfs_workspace_builds
        mip_result.dfs_workspace_reuses = dfs_result.dfs_workspace_reuses
        mip_result.dfs_workspace_build_seconds = (
            dfs_result.dfs_workspace_build_seconds
        )
        mip_result.dfs_dynamic_refresh_seconds = (
            dfs_result.dfs_dynamic_refresh_seconds
        )
        # 两个算法都覆盖同一个完整定价域时，它们的下界最大值仍是严格
        # 下界。HiGHS尚未建立有效界时，DFS根松弛可避免退回 ``-inf``。
        if math.isfinite(dfs_result.reduced_cost_lower_bound):
            mip_result.reduced_cost_lower_bound = max(
                mip_result.reduced_cost_lower_bound,
                dfs_result.reduced_cost_lower_bound,
            )
    return finalize(mip_result)


def _fixed_seat_resource_demand(groups: Sequence[dict]) -> int:
    """返回每个完整列必然消耗的座位资源总数。

    每名真实旅客消耗一个座位；单侧/双侧保护分别固定再消耗一个/两个
    独占空座。若未来引入可变数量的保护语义，应在这里拒绝等式强化。
    """
    demand = 0
    for group in groups:
        for passenger in group.get("psrs", []):
            demand += 1
            mandatory = _mandatory(passenger)
            if mandatory.get("needBothSideEmpty", "N") == "Y":
                demand += 2
            elif mandatory.get("needSingleSideEmpty", "N") == "Y":
                demand += 1
    return demand


class PersistentExactMaster:
    """同一MP1分支节点内持久化的受限主问题。

    结构变量和约束只建立一次；列生成得到的新同行组方案通过 ``addCol``
    追加，HiGHS 因而能够沿用上一次LP的单纯形基。
    """

    def __init__(
        self,
        groups: Sequence[dict],
        topology: evaluator.SeatTopology,
        baby_cost: Mapping[BabyPair, float],
        forced_placements: Mapping[int, Set[PlacementSignature]],
        forbidden_placements: Mapping[int, Set[PlacementSignature]],
    ):
        self.baby_cost = dict(baby_cost)
        self.baby_pairs_by_infant_seat: Dict[str, List[BabyPair]] = defaultdict(list)
        self.baby_pairs_by_occupant_seat: Dict[str, List[BabyPair]] = defaultdict(list)
        for pair in self.baby_cost:
            self.baby_pairs_by_infant_seat[pair[0]].append(pair)
            self.baby_pairs_by_occupant_seat[pair[1]].append(pair)
        self.forced_placements = {
            int(gid): set(signatures)
            for gid, signatures in forced_placements.items()
        }
        self.forbidden_placements = {
            int(gid): set(signatures)
            for gid, signatures in forbidden_placements.items()
        }
        self.group_ids = [int(group["groupId"]) for group in groups]
        self.fixed_seat_resource_demand = _fixed_seat_resource_demand(groups)
        self.full_load_partitioning_enabled = (
            self.fixed_seat_resource_demand == len(topology.seat_map)
        )
        self.ssr_M: Dict[SsrResource, int] = defaultdict(int)
        self.flag_capable: Dict[str, Set[int]] = {
            "row": set(), "subrow": set()
        }
        for group in groups:
            gid = int(group["groupId"])
            for passenger in group.get("psrs", []):
                ssr = passenger.get("ssr")
                if not ssr:
                    continue
                mandatory = _mandatory(passenger)
                if mandatory.get("sameRowNoOtherSSR", "N") == "Y":
                    self.flag_capable["row"].add(gid)
                if mandatory.get("sameSubRowNoOtherSSR", "N") == "Y":
                    self.flag_capable["subrow"].add(gid)
                for row in topology.row_seats:
                    self.ssr_M[("row", row, ssr)] += 1
                for subrow in set(topology.subrow.values()):
                    self.ssr_M[("subrow", subrow, ssr)] += 1

        self.group_rows = {
            gid: index for index, gid in enumerate(self.group_ids)
        }
        next_row = len(self.group_rows)
        self.seat_rows: Dict[str, int] = {}
        for seat in topology.seat_map:
            self.seat_rows[seat] = next_row
            next_row += 1
        self.ssr_flag_rows: Dict[tuple, int] = {}
        self.ssr_all_rows: Dict[SsrResource, int] = {}
        for resource in sorted(self.ssr_M, key=str):
            for gid in sorted(self.flag_capable[resource[0]]):
                self.ssr_flag_rows[(gid, *resource)] = next_row
                next_row += 1
            self.ssr_all_rows[resource] = next_row
            next_row += 1
        self.baby_lower_rows: Dict[BabyPair, int] = {}
        self.baby_i_rows: Dict[BabyPair, int] = {}
        self.baby_o_rows: Dict[BabyPair, int] = {}
        for pair in sorted(self.baby_cost):
            self.baby_lower_rows[pair] = next_row
            next_row += 1
            self.baby_i_rows[pair] = next_row
            next_row += 1
            self.baby_o_rows[pair] = next_row
            next_row += 1

        lower = np.full(next_row, -highspy.kHighsInf)
        upper = np.full(next_row, highspy.kHighsInf)
        for row in self.group_rows.values():
            lower[row] = upper[row] = 1.0
        for row in self.seat_rows.values():
            upper[row] = 1.0
        for row in self.ssr_flag_rows.values():
            upper[row] = 0.0
        for resource, M in self.ssr_M.items():
            upper[self.ssr_all_rows[resource]] = float(M + 1)
        for pair in self.baby_cost:
            upper[self.baby_lower_rows[pair]] = 1.0
            lower[self.baby_i_rows[pair]] = 0.0
            lower[self.baby_o_rows[pair]] = 0.0

        self.solver = _new_highs()
        self.phase_one = True
        self.solver.addRows(
            next_row, lower, upper, 0,
            np.zeros(next_row, dtype=np.int32),
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.float64),
        )
        for resource, M in self.ssr_M.items():
            flag_rows = [
                row for key, row in self.ssr_flag_rows.items()
                if key[1:] == resource
            ]
            rows = np.asarray(
                [*flag_rows, self.ssr_all_rows[resource]], dtype=np.int32
            )
            self.solver.addCol(
                0.0, 0.0, 1.0, len(rows), rows,
                np.asarray(
                    [*([-1.0] * len(flag_rows)), float(M)],
                    dtype=np.float64,
                ),
            )
        self.baby_columns: Dict[BabyPair, int] = {}
        for pair in sorted(self.baby_cost):
            rows = np.asarray(
                [
                    self.baby_lower_rows[pair],
                    self.baby_i_rows[pair],
                    self.baby_o_rows[pair],
                ],
                dtype=np.int32,
            )
            self.solver.addCol(
                0.0, 0.0, 1.0, 3, rows,
                np.full(3, -1.0, dtype=np.float64),
            )
            self.baby_columns[pair] = self.solver.getNumCol() - 1
        # Phase-I人工列使受限主问题在任何分支节点都可解。只有完成
        # 全组精确定价后人工列仍为正，才可证明完整列空间在该节点不可行。
        self.artificial_columns: List[int] = []
        artificial_cost = 1.0
        for gid in self.group_ids:
            row = np.asarray([self.group_rows[gid]], dtype=np.int32)
            self.solver.addCol(
                artificial_cost, 0.0, 1.0, 1, row,
                np.ones(1, dtype=np.float64),
            )
            self.artificial_columns.append(self.solver.getNumCol() - 1)
        self.pattern_columns: Dict[int, ExactPattern] = {}
        self.pattern_keys: Set[Tuple[int, tuple]] = set()
        self.incremental_batches = 0

    def _allowed(self, pattern: ExactPattern) -> bool:
        signature_set = set(pattern.signature)
        if self.forbidden_placements.get(
            pattern.group_id, set()
        ).intersection(signature_set):
            return False
        return self.forced_placements.get(
            pattern.group_id, set()
        ).issubset(signature_set)

    def add_patterns(self, patterns: Iterable[ExactPattern]) -> int:
        """追加尚未存在且符合当前MP1分支限制的方案列。"""
        additions = 0
        for pattern in patterns:
            key = (pattern.group_id, pattern.signature)
            if key in self.pattern_keys or not self._allowed(pattern):
                continue
            coefficients: Dict[int, float] = defaultdict(float)
            coefficients[self.group_rows[pattern.group_id]] = 1.0
            for seat in pattern.seat_resources:
                coefficients[self.seat_rows[seat]] += 1.0
            for resource, count in pattern.ssr_flagged:
                row = self.ssr_flag_rows.get(
                    (pattern.group_id, *resource)
                )
                if row is not None:
                    coefficients[row] += count
            for resource, count in pattern.ssr_all:
                row = self.ssr_all_rows.get(resource)
                if row is not None:
                    coefficients[row] += count
            relevant_baby_pairs: Set[BabyPair] = set()
            for seat in pattern.infant_seats:
                relevant_baby_pairs.update(
                    self.baby_pairs_by_infant_seat.get(seat, ())
                )
            for seat in pattern.occupied_seats:
                relevant_baby_pairs.update(
                    self.baby_pairs_by_occupant_seat.get(seat, ())
                )
            for pair in relevant_baby_pairs:
                infant_seat, occupant_seat = pair
                infant = int(infant_seat in pattern.infant_seats)
                occupant = int(occupant_seat in pattern.occupied_seats)
                if infant or occupant:
                    coefficients[self.baby_lower_rows[pair]] += infant + occupant
                    coefficients[self.baby_i_rows[pair]] += infant
                    coefficients[self.baby_o_rows[pair]] += occupant
            rows = np.asarray(list(coefficients), dtype=np.int32)
            values = np.asarray(
                [coefficients[row] for row in coefficients], dtype=np.float64
            )
            status = self.solver.addCol(
                0.0 if self.phase_one else pattern.master_cost,
                0.0, highspy.kHighsInf,
                len(rows), rows, values,
            )
            if status != highspy.HighsStatus.kOk:
                raise RuntimeError("MP1 addCol failed")
            column = self.solver.getNumCol() - 1
            self.pattern_columns[column] = pattern
            self.pattern_keys.add(key)
            additions += 1
        if additions:
            self.incremental_batches += 1
        return additions

    def start_phase_two(self) -> None:
        """固定人工列为零并恢复原始MP1目标。"""
        if not self.phase_one:
            return
        for column in self.artificial_columns:
            self.solver.changeColBounds(column, 0.0, 0.0)
        for pair, column in self.baby_columns.items():
            self.solver.changeColCost(column, float(self.baby_cost[pair]))
        for column, pattern in self.pattern_columns.items():
            self.solver.changeColCost(column, float(pattern.master_cost))
        # 满载且每列资源基数固定时，组等式与座位<=1已经蕴含每个座位
        # 恰好使用一次。显式写成集合划分等式不会删去可行解，却能减少
        # 退化并稳定座位行对偶。Phase-I保持<=1，避免人工列启动困难。
        if self.full_load_partitioning_enabled:
            for row in self.seat_rows.values():
                self.solver.changeRowBounds(row, 1.0, 1.0)
        self.phase_one = False

    def solve(self) -> MasterResult:
        run_status = self.solver.run()
        if run_status not in (
            highspy.HighsStatus.kOk,
            highspy.HighsStatus.kWarning,
        ):
            return MasterResult(False)
        status = self.solver.getModelStatus()
        if status == highspy.HighsModelStatus.kInfeasible:
            return MasterResult(False)
        if status != highspy.HighsModelStatus.kOptimal:
            raise RuntimeError(
                f"MP1 LP失败: {self.solver.modelStatusToString(status)}"
            )
        solution = self.solver.getSolution()
        info = self.solver.getInfo()
        row_dual = solution.row_dual
        duals = MasterDuals(
            group={
                gid: float(row_dual[row])
                for gid, row in self.group_rows.items()
            },
            seat={
                seat: float(row_dual[row])
                for seat, row in self.seat_rows.items()
            },
            ssr_flag={
                resource: float(row_dual[row])
                for resource, row in self.ssr_flag_rows.items()
            },
            ssr_all={
                resource: float(row_dual[row])
                for resource, row in self.ssr_all_rows.items()
            },
            baby_lower={
                pair: float(row_dual[row])
                for pair, row in self.baby_lower_rows.items()
            },
            baby_infant_upper={
                pair: float(row_dual[row])
                for pair, row in self.baby_i_rows.items()
            },
            baby_occupant_upper={
                pair: float(row_dual[row])
                for pair, row in self.baby_o_rows.items()
            },
        )
        values = {
            (pattern.group_id, pattern.signature): float(
                solution.col_value[column]
            )
            for column, pattern in self.pattern_columns.items()
            if solution.col_value[column] > 1e-10
        }
        return MasterResult(
            True, float(info.objective_function_value), values, duals,
            sum(
                max(0.0, float(solution.col_value[column]))
                for column in self.artificial_columns
            ),
        )

    def solve_restricted_integer(
        self, time_limit: float,
    ) -> Tuple[Optional[List[ExactPattern]], str]:
        """在当前已生成列上短时求整数主问题，作为节点内 primal heuristic。

        这个解只用于更新 incumbent；它不参与节点下界或最优性认证。
        调用后模型不再恢复为 LP，因此应在节点完成精确定价后、分支前使用。
        """
        if self.phase_one:
            return None, "phase_one"
        for column in self.pattern_columns:
            self.solver.changeColIntegrality(
                column, highspy.HighsVarType.kInteger
            )
        self.solver.setOptionValue("time_limit", max(0.001, float(time_limit)))
        self.solver.setOptionValue("mip_rel_gap", 0.0)
        status = self.solver.run()
        if status not in (
            highspy.HighsStatus.kOk,
            highspy.HighsStatus.kWarning,
        ):
            return None, "solver_failure"
        model_status = self.solver.getModelStatus()
        solution = self.solver.getSolution()
        if not bool(getattr(solution, "value_valid", True)):
            return None, self.solver.modelStatusToString(model_status)
        selected = [
            pattern
            for column, pattern in self.pattern_columns.items()
            if solution.col_value[column] > 0.5
        ]
        if (
            len(selected) != len(self.group_ids)
            or {pattern.group_id for pattern in selected}
            != set(self.group_ids)
        ):
            return None, self.solver.modelStatusToString(model_status)
        return selected, self.solver.modelStatusToString(model_status)


def _assemble(patterns: Sequence[ExactPattern]) -> Tuple[Dict[PassengerKey, str], Dict[str, List[PassengerKey]]]:
    """把选中的同行组方案合并为公共分配结果和留空责任映射。"""
    assignments: Dict[PassengerKey, str] = {}
    blocked: Dict[str, List[PassengerKey]] = defaultdict(list)
    for pattern in patterns:
        assignments.update(dict(pattern.assignments))
        for seat, owner in pattern.blocked_by:
            blocked[seat].append(owner)
    return assignments, dict(blocked)


def _evaluate_integer_patterns(
    patterns: Sequence[ExactPattern],
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    weights: Mapping[str, float],
    config: Mapping[str, Any],
) -> Tuple[float, int, int]:
    """返回整数方案的等价最小化成本、不可行计数和未分配人数。

    普通旅客必须全部就座，因此未分配人数也计入不可行计数。
    """
    assignments, _ = _assemble(patterns)
    violations, unassigned, _ = evaluator.count_hard_constraint_violations(
        new_seats_data, groups_data, assignments, config
    )
    soft, _ = evaluator.calculate_soft_score(
        new_seats_data, old_seats_data, groups_data, assignments, weights, config
    )
    cost = -soft
    return cost, violations + unassigned, unassigned


def _choose_placement_branch(
    lp: MasterResult,
    patterns: Mapping[Tuple[int, tuple], ExactPattern],
    tolerance: float,
) -> Optional[Tuple[int, PlacementSignature, float]]:
    """从方案列的分数解聚合旅客—放置边际，并选最接近 0.5 的变量。

    对该变量分支会同时约束现有列和未来定价列，比仅禁止一个完整组方案
    更能改变子节点松弛。
    """
    marginal: Dict[Tuple[int, PlacementSignature], float] = defaultdict(float)
    for key, value in lp.pattern_values.items():
        pattern = patterns[key]
        for placement in pattern.placements:
            marginal[(pattern.group_id, placement.signature)] += value
    candidates = [
        (gid, signature, value)
        for (gid, signature), value in marginal.items()
        if tolerance < value < 1.0 - tolerance
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            abs(item[2] - 0.5),
            -len(item[1][2]),
            str((item[0], item[1])),
        ),
    )


def _build_heuristic_warm_start(
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    weights: Dict[str, float],
    config: Dict[str, Any],
    new_topology: evaluator.SeatTopology,
    old_topology: evaluator.SeatTopology,
    baby_cost: Mapping[BabyPair, float],
    fixed_context: FixedSeatContext,
    heuristic_result: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[ExactPattern], float, Dict[str, Any]]:
    """运行启发式算法，并把其可行分配精确转换为每组一个MP1方案列。"""
    started = time.perf_counter()
    frozen_input = heuristic_result is not None
    if heuristic_result is None:
        try:
            heuristic_result, _ = heuristic_allocator.run_allocation(
                new_seats_data,
                old_seats_data,
                copy.deepcopy(groups_data),
                weights,
                config,
            )
        except Exception as exc:
            return [], math.inf, {
                "source": "heuristic_seat_allocator.py",
                "accepted": False,
                "rejection_reason": "heuristic_execution_error",
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_seconds": time.perf_counter() - started,
            }
    raw_assignments = heuristic_result.get("assigned_seats", {})
    assignments: Dict[PassengerKey, str] = {
        (int(key[0]), int(key[1])): str(seat)
        for key, seat in raw_assignments.items()
        if isinstance(key, (tuple, list)) and len(key) == 2
    }
    violations, unassigned, violation_detail = evaluator.count_hard_constraint_violations(
        new_seats_data, groups_data, assignments, config
    )
    soft_score, _ = evaluator.calculate_soft_score(
        new_seats_data, old_seats_data, groups_data, assignments, weights, config
    )
    score, _ = evaluator.calculate_combined_score(
        soft_score, unassigned, violations, 0.0, config
    )
    diagnostics: Dict[str, Any] = {
        "source": (
            "frozen_heuristic_result" if frozen_input
            else "heuristic_seat_allocator.py"
        ),
        "frozen_input": frozen_input,
        "frozen_identity_hash": (
            heuristic_result.get("_frozen_warm_start_hash")
            if frozen_input else None
        ),
        "accepted": False,
        "score_without_time_penalty": score,
        # Keep the exact warm-start allocation so an offline report can
        # attribute the column-generation improvement to groups/passengers.
        # This is diagnostic output only and is never used by MP1 or MP2.
        "assignments": assignments,
        "assigned": len(assignments),
        "unassigned": unassigned,
        "violations": violations,
        "violation_detail": violation_detail,
        "elapsed_seconds": time.perf_counter() - started,
    }
    if violations or unassigned:
        diagnostics["rejection_reason"] = (
            "hard_constraint_violation"
            if violations
            else "unassigned_passengers"
        )
        return [], math.inf, diagnostics

    blocked_for_owner: Dict[PassengerKey, Set[str]] = defaultdict(set)
    for seat_id, owners in (heuristic_result.get("blocked_by", {}) or {}).items():
        for owner in owners or []:
            if isinstance(owner, (tuple, list)) and len(owner) == 2:
                blocked_for_owner[(int(owner[0]), int(owner[1]))].add(str(seat_id))

    patterns: List[ExactPattern] = []
    for group in groups_data:
        gid = int(group["groupId"])
        all_options = _placement_options(
            group, new_topology, old_topology, weights, config, fixed_context
        )
        selected: List[Placement] = []
        for passenger_index, passenger in enumerate(group.get("psrs", [])):
            key = (gid, int(passenger["hostnum"]))
            seat_id = assignments.get(key)
            expected_blocked = blocked_for_owner.get(key, set())
            candidates = [
                option
                for option in all_options[passenger_index]
                if option.seat_id == seat_id
                and set(option.blocked) == expected_blocked
            ]
            if not candidates:
                diagnostics["rejection_reason"] = (
                    f"cannot_translate_passenger_{gid}_{key[1]}"
                )
                return [], math.inf, diagnostics
            selected.append(candidates[0])
        if not _caregiver_ok(group, selected, new_topology, config):
            diagnostics["rejection_reason"] = f"caregiver_rule_group_{gid}"
            return [], math.inf, diagnostics
        patterns.append(
            _pattern_from_placements(
                group, selected, new_topology, weights, config, baby_cost
            )
        )

    rebuilt_assignments, _ = _assemble(patterns)
    if rebuilt_assignments != assignments:
        diagnostics["rejection_reason"] = "assignment_translation_mismatch"
        return [], math.inf, diagnostics
    diagnostics["accepted"] = True
    diagnostics["elapsed_seconds"] = time.perf_counter() - started
    # MP1内部最小化成本与无时间惩罚、零违规的评分恰好互为相反数。
    return patterns, -score, diagnostics


def _validate_input_identity(groups_data: Sequence[dict]) -> None:
    """拒绝会使 MP1、MP2 和输出映射产生歧义的组及旅客标识。"""
    group_ids = [int(group["groupId"]) for group in groups_data]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("groups_data 中存在重复 groupId")
    for group in groups_data:
        group_id = int(group["groupId"])
        passengers = group.get("psrs", [])
        if not passengers:
            raise ValueError(f"groupId={group_id} 不允许为空")
        hostnums = [
            int(passenger["hostnum"]) for passenger in passengers
        ]
        if len(hostnums) != len(set(hostnums)):
            raise ValueError(
                f"groupId={group_id} 存在重复 hostnum"
            )


def _run_column_generation_single_cabin(
    new_seats_data: List[dict], old_seats_data: List[dict], groups_data: List[dict],
    weights: Dict[str, float], config: Dict[str, Any],
    frozen_warm_start_result: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], float]:
    """运行两层精确算法；只有完整关闭 MP1/MP2 分支树才给出最优性证明。"""
    config = copy.deepcopy(config)
    config["_column_generation_active_ssr_types"] = sorted({
        str(passenger.get("ssr"))
        for group in groups_data
        for passenger in group.get("psrs", [])
        if passenger.get("ssr")
    })
    _validate_proof_assumptions(weights, config)
    started = time.perf_counter(); limits = _limits(config)
    _validate_input_identity(groups_data)
    hard_deadline = started + limits["total_time_limit"]
    final_rounding_reserve = min(
        0.25 * limits["total_time_limit"],
        max(
            0.0,
            float(
                config.get("column_generation", {}).get(
                    "restricted_mip_time_limit", 10.0
                )
            ),
        ),
    )
    final_root_bound_reserve = min(
        max(
            0.0,
            float(
                config.get("column_generation", {}).get(
                    "final_root_bound_reserve_fraction", 0.10
                )
            ),
        ) * limits["total_time_limit"],
        max(
            0.0,
            float(
                config.get("column_generation", {}).get(
                    "final_root_bound_reserve_seconds", 30.0
                )
            ),
        ),
    )
    # Pricing must stop early enough to leave the requested final integer-RMP
    # budget.  Otherwise a total-time-limit exit consumes the entire budget
    # and the generated column pool can never be converted into a solution.
    deadline = (
        hard_deadline - final_rounding_reserve - final_root_bound_reserve
    )
    new_topology = evaluator.SeatTopology(new_seats_data, config)
    old_topology = evaluator.SeatTopology(old_seats_data, config)
    fixed_context = _preprocess_fixed_seats(
        groups_data, new_topology, config
    )
    baby_cost = _baby_pairs(new_topology, groups_data, weights, config)
    group_by_id = {int(g["groupId"]): g for g in groups_data}
    fixed_seat_resource_demand = _fixed_seat_resource_demand(groups_data)
    full_load_partitioning_eligible = (
        fixed_seat_resource_demand == len(new_topology.seat_map)
    )
    _progress(
        config,
        f"building reusable MP2 group caches; pricing_workers="
        f"{limits['pricing_workers']}",
    )
    pricing_caches = {
        gid: _build_group_pricing_cache(
            group, new_topology, old_topology, weights, config, fixed_context
        )
        for gid, group in group_by_id.items()
    }
    _progress(
        config,
        f"start exact column generation: passengers="
        f"{sum(len(g.get('psrs', [])) for g in groups_data)}, "
        f"groups={len(group_by_id)}, seats={len(new_seats_data)}, "
        f"mp2_time_limit={limits['mp2_time_limit']:.0f}s, "
        f"total_time_limit={limits['total_time_limit']:.0f}s",
    )
    patterns: Dict[Tuple[int, tuple], ExactPattern] = {}
    pricing_stats = {
        "calls": 0,
        "quick_calls": 0,
        "exact_calls": 0,
        "nodes": 0,
        "dfs_nodes": 0,
        "dfs_seconds": 0.0,
        "dfs_calls": 0,
        "dfs_completed_calls": 0,
        "dfs_fallback_calls": 0,
        "dfs_incumbent_seeded_calls": 0,
        "dfs_bound_prunes": 0,
        "dfs_resource_prunes": 0,
        "dfs_symmetry_prunes": 0,
        "dfs_symmetry_classes": 0,
        "dfs_negative_patterns_seen": 0,
        "dfs_unique_negative_patterns": 0,
        "dfs_patterns_returned": 0,
        "dfs_seconds_per_returned_column": 0.0,
        "dfs_workspace_builds": 0,
        "dfs_workspace_reuses": 0,
        "dfs_workspace_build_seconds": 0.0,
        "dfs_dynamic_refresh_seconds": 0.0,
        "historical_reprice_calls": 0,
        "historical_patterns_checked": 0,
        "historical_negative_hits": 0,
        "historical_columns_returned": 0,
        "dfs_calls_avoided_by_history": 0,
        "historical_reprice_seconds": 0.0,
        "dfs_exact_certificates": 0,
        "dfs_exact_certificate_seconds_saved": 0.0,
        "mip_avoided_after_dfs_certificate": 0,
        "singleton_pricing_calls": 0,
        "singleton_pricing_seconds": 0.0,
        "pair_pricing_calls": 0,
        "specialized_pricing_seconds": 0.0,
        "pairs_examined": 0,
        "pairs_resource_feasible": 0,
        "pairs_exact_feasible": 0,
        "pair_rc_seconds": 0.0,
        "pair_heap_seconds": 0.0,
        "pair_pattern_materialization_seconds": 0.0,
        "pair_patterns_materialized": 0,
        "pair_patterns_returned": 0,
        "pair_pricing_seconds": 0.0,
        "mp2_model_build_seconds": 0.0,
        "mp2_solve_seconds": 0.0,
        "mp2_objective_update_seconds": 0.0,
        "mp2_model_builds": 0,
        "mp2_model_reuses": 0,
        "label_dp_calls": 0,
        "label_dp_attempts": 0,
        "label_dp_states": 0,
        "label_dp_reason_counts": {},
        "label_dp_max_cost_types": 0,
        "priced_placements": 0,
        "complete_domain_mip_solves": 0,
        "parallel_workers": limits["pricing_workers"],
        "termination_counts": {},
        "group_stats": {},
        "model_equivalence_errors": [],
        "seconds": 0.0,
        "patterns_returned": 0,
        "patterns_added": 0,
        "columns_kept_from_incomplete_round": 0,
        "mp1_incremental_add_batches": 0,
        "repeated_exact_group_calls": 0,
        "transported_bound_checks": 0,
        "transported_anchor_evaluations": 0,
        "transported_max_anchors_per_group": 0,
        "transported_nonnegative_skips": 0,
        "escalated_exact_rounds": 0,
        "escalated_exact_groups": 0,
        "escalated_exact_patterns": 0,
        "escalated_exact_seconds": 0.0,
        "escalated_exact_time_limits": [],
        "adaptive_dual_stabilization": {},
    }

    def record_pricing(
        group_id: int, priced: PricingResult, *, exact_round: bool,
    ) -> None:
        """统一累计初始化、快速和精确定价统计。"""
        pricing_stats["calls"] += 1
        pricing_stats["exact_calls" if exact_round else "quick_calls"] += 1
        pricing_stats["nodes"] += priced.nodes
        pricing_stats["dfs_nodes"] += priced.dfs_nodes
        pricing_stats["dfs_seconds"] += priced.dfs_elapsed
        pricing_stats["dfs_calls"] += int(
            bool(priced.dfs_workspace_builds or priced.dfs_workspace_reuses)
        )
        pricing_stats["dfs_incumbent_seeded_calls"] += int(
            priced.dfs_incumbent_seeded
        )
        pricing_stats["dfs_bound_prunes"] += priced.dfs_bound_prunes
        pricing_stats["dfs_resource_prunes"] += priced.dfs_resource_prunes
        pricing_stats["dfs_symmetry_prunes"] += priced.dfs_symmetry_prunes
        pricing_stats["dfs_symmetry_classes"] += priced.dfs_symmetry_classes
        pricing_stats["dfs_negative_patterns_seen"] += (
            priced.dfs_negative_patterns_seen
        )
        pricing_stats["dfs_unique_negative_patterns"] += (
            priced.dfs_unique_negative_patterns
        )
        pricing_stats["dfs_patterns_returned"] += priced.dfs_patterns_returned
        pricing_stats["dfs_workspace_builds"] += priced.dfs_workspace_builds
        pricing_stats["dfs_workspace_reuses"] += priced.dfs_workspace_reuses
        pricing_stats["dfs_workspace_build_seconds"] += (
            priced.dfs_workspace_build_seconds
        )
        pricing_stats["dfs_dynamic_refresh_seconds"] += (
            priced.dfs_dynamic_refresh_seconds
        )
        pricing_stats["historical_reprice_calls"] += (
            priced.historical_reprice_calls
        )
        pricing_stats["historical_patterns_checked"] += (
            priced.historical_patterns_checked
        )
        pricing_stats["historical_negative_hits"] += (
            priced.historical_negative_hits
        )
        pricing_stats["historical_columns_returned"] += (
            priced.historical_columns_returned
        )
        pricing_stats["dfs_calls_avoided_by_history"] += (
            priced.dfs_calls_avoided_by_history
        )
        pricing_stats["historical_reprice_seconds"] += (
            priced.historical_reprice_seconds
        )
        if pricing_stats["dfs_patterns_returned"]:
            pricing_stats["dfs_seconds_per_returned_column"] = (
                pricing_stats["dfs_seconds"]
                / pricing_stats["dfs_patterns_returned"]
            )
        pricing_stats["dfs_exact_certificates"] += int(
            priced.dfs_exact_certificate
        )
        pricing_stats["dfs_exact_certificate_seconds_saved"] += (
            priced.dfs_exact_certificate_seconds_saved
        )
        pricing_stats["mip_avoided_after_dfs_certificate"] += int(
            priced.mip_avoided_after_dfs_certificate
        )
        pricing_stats["singleton_pricing_calls"] += priced.singleton_pricing_calls
        if priced.singleton_pricing_calls:
            pricing_stats["singleton_pricing_seconds"] += (
                priced.specialized_pricing_elapsed
            )
        pricing_stats["pair_pricing_calls"] += priced.pair_pricing_calls
        pricing_stats["specialized_pricing_seconds"] += (
            priced.specialized_pricing_elapsed
        )
        pricing_stats["pairs_examined"] += priced.pairs_examined
        pricing_stats["pairs_resource_feasible"] += priced.pairs_resource_feasible
        pricing_stats["pairs_exact_feasible"] += priced.pairs_exact_feasible
        pricing_stats["pair_rc_seconds"] += priced.pair_rc_seconds
        pricing_stats["pair_heap_seconds"] += priced.pair_heap_seconds
        pricing_stats["pair_pattern_materialization_seconds"] += (
            priced.pair_pattern_materialization_seconds
        )
        pricing_stats["pair_patterns_materialized"] += (
            priced.pair_patterns_materialized
        )
        pricing_stats["pair_patterns_returned"] += priced.pair_patterns_returned
        pricing_stats["pair_pricing_seconds"] += priced.pair_pricing_seconds
        pricing_stats["mp2_model_build_seconds"] += priced.mp2_model_build_seconds
        pricing_stats["mp2_solve_seconds"] += priced.mp2_solve_seconds
        pricing_stats["mp2_objective_update_seconds"] += (
            priced.mp2_objective_update_seconds
        )
        pricing_stats["mp2_model_builds"] += priced.mp2_model_builds
        pricing_stats["mp2_model_reuses"] += priced.mp2_model_reuses
        pricing_stats["label_dp_calls"] += int(priced.label_dp_used)
        pricing_stats["label_dp_attempts"] += int(
            priced.label_dp_attempted
        )
        pricing_stats["label_dp_states"] += priced.label_dp_states
        pricing_stats["label_dp_max_cost_types"] = max(
            pricing_stats["label_dp_max_cost_types"],
            priced.label_dp_type_count,
        )
        label_reason = priced.label_dp_reason or "not_considered"
        pricing_stats["label_dp_reason_counts"][label_reason] = (
            pricing_stats["label_dp_reason_counts"].get(label_reason, 0) + 1
        )
        if priced.termination.startswith("dfs_"):
            pricing_stats["dfs_completed_calls"] += 1
        elif priced.dfs_nodes:
            pricing_stats["dfs_fallback_calls"] += 1
        pricing_stats["priced_placements"] += priced.priced_placements
        pricing_stats["complete_domain_mip_solves"] += (
            priced.complete_domain_mip_solves
        )
        pricing_stats["termination_counts"][priced.termination] = (
            pricing_stats["termination_counts"].get(priced.termination, 0) + 1
        )
        pricing_stats["seconds"] += priced.elapsed
        pricing_stats["patterns_returned"] += len(priced.patterns)
        group_stats = pricing_stats["group_stats"].setdefault(
            str(group_id),
            {
                "calls": 0,
                "quick_calls": 0,
                "seconds": 0.0,
                "max_call_seconds": 0.0,
                "dfs_seconds": 0.0,
                "dfs_nodes": 0,
                "dfs_calls": 0,
                "mip_solves": 0,
                "exact_calls": 0,
                "repeated_exact_calls": 0,
                "exact_certificates": 0,
                "nonnegative_certificates": 0,
                "patterns_returned": 0,
                "negative_patterns_returned": 0,
                "dfs_bound_prunes": 0,
                "dfs_resource_prunes": 0,
                "dfs_symmetry_prunes": 0,
                "dfs_negative_patterns_seen": 0,
                "dfs_unique_negative_patterns": 0,
                "dfs_patterns_returned": 0,
                "dfs_seconds_per_returned_column": 0.0,
                "dfs_workspace_builds": 0,
                "dfs_workspace_reuses": 0,
                "dfs_workspace_build_seconds": 0.0,
                "dfs_dynamic_refresh_seconds": 0.0,
                "historical_reprice_calls": 0,
                "historical_patterns_checked": 0,
                "historical_negative_hits": 0,
                "historical_columns_returned": 0,
                "dfs_calls_avoided_by_history": 0,
                "historical_reprice_seconds": 0.0,
                "label_dp_calls": 0,
                "label_dp_attempts": 0,
                "label_dp_max_cost_types": 0,
                "label_dp_reason_counts": {},
                "dfs_exact_certificates": 0,
                "dfs_exact_certificate_seconds_saved": 0.0,
                "mip_avoided_after_dfs_certificate": 0,
                "singleton_pricing_calls": 0,
                "singleton_pricing_seconds": 0.0,
                "pair_pricing_calls": 0,
                "specialized_pricing_seconds": 0.0,
                "pairs_examined": 0,
                "pairs_resource_feasible": 0,
                "pairs_exact_feasible": 0,
                "pair_rc_seconds": 0.0,
                "pair_heap_seconds": 0.0,
                "pair_pattern_materialization_seconds": 0.0,
                "pair_patterns_materialized": 0,
                "pair_patterns_returned": 0,
                "pair_pricing_seconds": 0.0,
                "mp2_model_build_seconds": 0.0,
                "mp2_solve_seconds": 0.0,
                "mp2_objective_update_seconds": 0.0,
                "mp2_model_builds": 0,
                "mp2_model_reuses": 0,
                "termination_counts": {},
            },
        )
        group_stats["calls"] += 1
        group_stats["quick_calls"] += int(not exact_round)
        group_stats["seconds"] += priced.elapsed
        group_stats["max_call_seconds"] = max(
            group_stats["max_call_seconds"], priced.elapsed
        )
        group_stats["dfs_seconds"] += priced.dfs_elapsed
        group_stats["dfs_nodes"] += priced.dfs_nodes
        group_stats["dfs_calls"] += int(
            bool(priced.dfs_workspace_builds or priced.dfs_workspace_reuses)
        )
        group_stats["dfs_bound_prunes"] += priced.dfs_bound_prunes
        group_stats["dfs_resource_prunes"] += priced.dfs_resource_prunes
        group_stats["dfs_symmetry_prunes"] += priced.dfs_symmetry_prunes
        for field in (
            "dfs_negative_patterns_seen", "dfs_unique_negative_patterns",
            "dfs_patterns_returned", "dfs_workspace_builds",
            "dfs_workspace_reuses", "historical_reprice_calls",
            "historical_patterns_checked", "historical_negative_hits",
            "historical_columns_returned", "dfs_calls_avoided_by_history",
        ):
            group_stats[field] += getattr(priced, field)
        group_stats["dfs_workspace_build_seconds"] += (
            priced.dfs_workspace_build_seconds
        )
        group_stats["dfs_dynamic_refresh_seconds"] += (
            priced.dfs_dynamic_refresh_seconds
        )
        group_stats["historical_reprice_seconds"] += (
            priced.historical_reprice_seconds
        )
        if group_stats["dfs_patterns_returned"]:
            group_stats["dfs_seconds_per_returned_column"] = (
                group_stats["dfs_seconds"]
                / group_stats["dfs_patterns_returned"]
            )
        group_stats["mip_solves"] += priced.complete_domain_mip_solves
        group_stats["patterns_returned"] += len(priced.patterns)
        if priced.reduced_cost < -limits["tolerance"]:
            group_stats["negative_patterns_returned"] += len(priced.patterns)
        group_stats["dfs_exact_certificates"] += int(priced.dfs_exact_certificate)
        group_stats["dfs_exact_certificate_seconds_saved"] += (
            priced.dfs_exact_certificate_seconds_saved
        )
        group_stats["mip_avoided_after_dfs_certificate"] += int(
            priced.mip_avoided_after_dfs_certificate
        )
        group_stats["singleton_pricing_calls"] += priced.singleton_pricing_calls
        if priced.singleton_pricing_calls:
            group_stats["singleton_pricing_seconds"] += (
                priced.specialized_pricing_elapsed
            )
        group_stats["pair_pricing_calls"] += priced.pair_pricing_calls
        group_stats["specialized_pricing_seconds"] += (
            priced.specialized_pricing_elapsed
        )
        group_stats["pairs_examined"] += priced.pairs_examined
        group_stats["pairs_resource_feasible"] += priced.pairs_resource_feasible
        group_stats["pairs_exact_feasible"] += priced.pairs_exact_feasible
        group_stats["pair_rc_seconds"] += priced.pair_rc_seconds
        group_stats["pair_heap_seconds"] += priced.pair_heap_seconds
        group_stats["pair_pattern_materialization_seconds"] += (
            priced.pair_pattern_materialization_seconds
        )
        group_stats["pair_patterns_materialized"] += (
            priced.pair_patterns_materialized
        )
        group_stats["pair_patterns_returned"] += priced.pair_patterns_returned
        group_stats["pair_pricing_seconds"] += priced.pair_pricing_seconds
        group_stats["mp2_model_build_seconds"] += priced.mp2_model_build_seconds
        group_stats["mp2_solve_seconds"] += priced.mp2_solve_seconds
        group_stats["mp2_objective_update_seconds"] += (
            priced.mp2_objective_update_seconds
        )
        group_stats["mp2_model_builds"] += priced.mp2_model_builds
        group_stats["mp2_model_reuses"] += priced.mp2_model_reuses
        group_stats["label_dp_calls"] += int(priced.label_dp_used)
        group_stats["label_dp_attempts"] += int(priced.label_dp_attempted)
        group_stats["label_dp_max_cost_types"] = max(
            group_stats["label_dp_max_cost_types"],
            priced.label_dp_type_count,
        )
        group_stats["label_dp_reason_counts"][label_reason] = (
            group_stats["label_dp_reason_counts"].get(label_reason, 0) + 1
        )
        if exact_round:
            if group_stats["exact_calls"]:
                group_stats["repeated_exact_calls"] += 1
                pricing_stats["repeated_exact_group_calls"] += 1
            group_stats["exact_calls"] += 1
            group_stats["exact_certificates"] += int(priced.proven_optimal)
            group_stats["nonnegative_certificates"] += int(
                (
                    priced.proven_optimal
                    or math.isfinite(priced.reduced_cost_lower_bound)
                )
                and priced.reduced_cost_lower_bound >= -limits["tolerance"]
            )
        group_stats["termination_counts"][priced.termination] = (
            group_stats["termination_counts"].get(priced.termination, 0) + 1
        )
        if priced.equivalence_error_detail is not None:
            pricing_stats["model_equivalence_errors"].append(
                priced.equivalence_error_detail
            )

    # 将启发式完整分配转换成MP1方案列，并作为全局整数incumbent。
    # 先由独立评分器复核；若失败，则由 MP1 Phase-I 人工列启动列生成，
    # 不再构造包含未分配旅客或跨组资源冲突的伪可行初始解。
    _progress(config, "running heuristic_seat_allocator warm start")
    warm_patterns, warm_cost, warm_start_diagnostics = _build_heuristic_warm_start(
        new_seats_data,
        old_seats_data,
        groups_data,
        weights,
        config,
        new_topology,
        old_topology,
        baby_cost,
        fixed_context,
        heuristic_result=frozen_warm_start_result,
    )
    if warm_start_diagnostics["accepted"]:
        for pattern in warm_patterns:
            patterns[(pattern.group_id, pattern.signature)] = pattern
            pricing_caches[
                pattern.group_id
            ].historical_mip_start = pattern.signature
            pricing_caches[pattern.group_id].historical_patterns[
                pattern.signature
            ] = pattern
        initial_incumbent_patterns = list(warm_patterns)
        initial_incumbent_cost = warm_cost
        _progress(
            config,
            f"heuristic warm start accepted: score="
            f"{warm_start_diagnostics['score_without_time_penalty']:.6f}, "
            f"assigned={warm_start_diagnostics['assigned']}, "
            f"unassigned={warm_start_diagnostics['unassigned']}, "
            f"elapsed={warm_start_diagnostics['elapsed_seconds']:.1f}s",
        )
    else:
        initial_incumbent_patterns = []
        initial_incumbent_cost = math.inf
        _progress(
            config,
            f"heuristic warm start rejected: "
            f"reason={warm_start_diagnostics.get('rejection_reason', 'unknown')}; "
            "starting MP1 Phase-I without an integer incumbent",
        )

    # MP1 的 Phase-I 人工列保证初始 RMP 可解；真实列均由完整域定价产生。
    termination = "optimal"
    _progress(
        config,
        "start MP1 from heuristic columns or Phase-I artificial columns",
    )

    search_cfg = config.get("column_generation", {})
    dive_max_nodes = max(0, int(search_cfg.get("mp1_dive_max_nodes", 32)))
    restricted_mip_frequency = max(
        0, int(search_cfg.get("restricted_mip_frequency", 10))
    )
    restricted_mip_time_limit = max(
        0.0, float(search_cfg.get("restricted_mip_time_limit", 10.0))
    )
    # 节点=(LP下界, serial, depth, 强制放置, 禁止放置)。
    # frontier保留全局最有希望下界；dive_stack沿incumbent方向短程DFS。
    Node = Tuple[
        float,
        int,
        int,
        Dict[int, Set[PlacementSignature]],
        Dict[int, Set[PlacementSignature]],
    ]
    frontier: List[Node] = []
    dive_stack: List[Node] = []
    heapq.heappush(frontier, (-math.inf, 0, 0, {}, {}))
    serial = 1; master_nodes = 0; cg_iterations = 0
    # 初始整数可行解始终满足固定座位，限时也不会返回空的“未知解”。
    incumbent_patterns: List[ExactPattern] = list(initial_incumbent_patterns)
    incumbent_cost = initial_incumbent_cost
    all_nodes_certified = True
    complete_root_lp_cost: Optional[float] = None
    root_lp_certified = False
    safe_root_lp_upper_bound: Optional[float] = None
    safe_root_gap_absolute: Optional[float] = None
    safe_root_gap_relative: Optional[float] = None
    safe_gap_target_reached = False
    safe_pricing_lower_bounds: Dict[int, float] = {}
    safe_bound_source: Optional[str] = None
    latest_root_lp_cost: Optional[float] = None
    latest_root_duals: Optional[MasterDuals] = None
    final_root_bound_sweep = {
        "attempted": False,
        "complete": False,
        "groups": 0,
        "finite_lower_bounds": 0,
        "historical_anchor_groups": 0,
        "historical_anchor_improved_groups": 0,
        "historical_anchor_evaluations": 0,
        "assignment_bound_groups": 0,
        "assignment_bound_improved_groups": 0,
        "assignment_bound_seconds": 0.0,
        "assignment_bound_details": [],
        "assignment_candidate_columns": 0,
        "assignment_negative_columns": 0,
        "correction_before_refinement": None,
        "correction_after_refinement": None,
        "refinement_enabled": False,
        "refinement_candidates": 0,
        "refinement_attempted_groups": [],
        "refinement_total_attempts": 0,
        "refinement_group_details": [],
        "refinement_dfs_calls": 0,
        "refinement_mip_calls": 0,
        "refinement_exact_groups": 0,
        "refinement_nonnegative_groups": 0,
        "refinement_improved_groups": 0,
        "refinement_seconds": 0.0,
        "recovered_negative_columns": 0,
        "recovery_rmp_attempted": False,
        "recovery_rmp_selected": False,
        "recovery_rmp_cost_before": None,
        "recovery_rebuilt_baseline_cost": None,
        "recovery_rebuilt_candidate_reduced_costs": {},
        "recovery_added_columns": 0,
        "recovery_total_pool_columns": 0,
        "recovery_solver_column_duals": {},
        "recovery_formula_column_duals": {},
        "recovery_rmp_cost_after": None,
        "recovery_rmp_seconds": 0.0,
        "recovery_rmp_started_after_deadline": False,
        "seconds": 0.0,
    }
    equivalence_checks = 0; max_equivalence_error = 0.0
    placement_branches = 0
    dive_nodes = 0
    dive_steps = 0
    max_depth = 0
    restricted_mip_stats = {
        "calls": 0,
        "feasible_solutions": 0,
        "incumbent_improvements": 0,
        "rejected_solutions": 0,
        "seconds": 0.0,
    }
    pricing_executor = ThreadPoolExecutor(
        max_workers=limits["pricing_workers"],
        thread_name_prefix="mp2-pricing",
    )
    root_cg_started = time.perf_counter()
    root_cg_elapsed_seconds: Optional[float] = None
    root_cg_trajectory: List[Dict[str, Any]] = []
    previous_root_round: Dict[str, Any] = {
        "kind": "initial",
        "patterns_returned": 0,
        "patterns_added": 0,
        "minimum_reduced_cost": None,
    }

    while frontier or dive_stack:
        if master_nodes >= limits["mp1_node_limit"]:
            termination = "mp1_node_limit"
            all_nodes_certified = False
            break
        if time.perf_counter() >= deadline:
            termination = "total_time_limit"
            all_nodes_certified = False
            break
        if dive_stack and dive_steps < dive_max_nodes:
            node_bound, _, depth, forced, forbidden = dive_stack.pop()
            dive_steps += 1
            dive_nodes += 1
        else:
            # 周期性回到全局最好下界；未访问的dive节点不会丢失。
            while dive_stack:
                heapq.heappush(frontier, dive_stack.pop())
            node_bound, _, depth, forced, forbidden = heapq.heappop(frontier)
        if depth > 0 and root_cg_elapsed_seconds is None:
            root_cg_elapsed_seconds = time.perf_counter() - root_cg_started
            dive_steps = 0
        if node_bound >= incumbent_cost - limits["tolerance"]:
            continue
        master_nodes += 1
        max_depth = max(max_depth, depth)
        _progress(
            config,
            f"MP1 node={master_nodes}, depth={depth}: "
            f"pending_nodes={len(frontier) + len(dive_stack)}, "
            f"patterns={len(patterns)}, incumbent_score={-incumbent_cost:.6f}",
        )
        master = PersistentExactMaster(
            groups_data, new_topology, baby_cost, forced, forbidden
        )
        master.add_patterns(patterns.values())
        node_certified = False
        lp = MasterResult(False)
        # 启发式部分定价：首轮扫描全部组，之后优先继续最近产生负列的组。
        # 一旦这些“有希望组”不再产列，立即精确定价所有未认证组。
        promising_group_ids: Set[int] = set(group_by_id)
        stabilization_cfg = config.get("column_generation", {})
        stabilization_mode = str(
            stabilization_cfg.get("dual_stabilization_mode", "adaptive")
        ).strip().lower()
        if stabilization_mode not in {"adaptive", "fixed"}:
            raise ValueError(
                "column_generation.dual_stabilization_mode必须为"
                "adaptive或fixed"
            )
        stabilizer = AdaptiveDualStabilizer(
            alpha=float(stabilization_cfg.get("dual_smoothing_alpha", 0.7)),
            minimum_alpha=float(stabilization_cfg.get(
                "dual_stabilization_min_alpha", 0.15
            )),
            maximum_alpha=float(stabilization_cfg.get(
                "dual_stabilization_max_alpha", 0.95
            )),
            success_factor=float(stabilization_cfg.get(
                "dual_stabilization_success_factor", 0.85
            )),
            null_step_increment=float(stabilization_cfg.get(
                "dual_stabilization_null_step_increment", 0.10
            )),
            adaptive=stabilization_mode == "adaptive",
        )

        while True:
            if time.perf_counter() >= deadline:
                termination = "total_time_limit"
                all_nodes_certified = False
                break
            lp = master.solve()
            if not lp.feasible:
                termination = "mp1_lp_failure"
                all_nodes_certified = False
                break
            if (
                master.phase_one
                and lp.artificial_value <= limits["tolerance"]
            ):
                master.start_phase_two()
                stabilizer.center = None
                stabilizer.best_rmp_cost = math.inf
                _progress(
                    config,
                    f"MP1 node={master_nodes}: Phase-I feasible; "
                    "switch to original objective",
                )
                continue

            if depth == 0 and not master.phase_one:
                latest_root_lp_cost = float(lp.objective_cost)
                latest_root_duals = copy.deepcopy(lp.duals)
                current_score_bound = -float(lp.objective_cost)
                current_incumbent = -float(incumbent_cost)
                current_gap = max(0.0, current_score_bound - current_incumbent)
                root_cg_trajectory.append({
                    "elapsed_seconds": time.perf_counter() - root_cg_started,
                    "cg_iterations": cg_iterations,
                    "restricted_master_cost": float(lp.objective_cost),
                    "restricted_master_score_estimate": current_score_bound,
                    "incumbent_score": current_incumbent,
                    "incumbent_to_restricted_master_difference_absolute": current_gap,
                    "incumbent_to_restricted_master_difference_relative": (
                        current_gap / max(1.0, abs(current_incumbent))
                    ),
                    "pattern_count": len(patterns),
                    "previous_pricing_round": dict(previous_root_round),
                })

            discovery_duals = stabilizer.pricing_duals(
                lp.duals, lp.objective_cost
            )
            # Seed every group DFS with the best currently known RMP column
            # under the exact current duals.  This is a valid primal pricing
            # incumbent and can tighten DFS pruning without changing its
            # lower bounds or certificate.
            best_rmp_pattern_by_group: Dict[int, ExactPattern] = {}
            best_rmp_rc_by_group: Dict[int, float] = {}
            for (pattern_group_id, _), pattern in patterns.items():
                if not master._allowed(pattern):
                    continue
                reduced_cost = _master_column_reduced_cost(
                    pattern,
                    lp.duals,
                    baby_cost,
                    phase_one=master.phase_one,
                )
                if (
                    pattern_group_id not in best_rmp_rc_by_group
                    or reduced_cost
                    < best_rmp_rc_by_group[pattern_group_id]
                ):
                    best_rmp_rc_by_group[pattern_group_id] = reduced_cost
                    best_rmp_pattern_by_group[pattern_group_id] = pattern
            for pattern_group_id, pattern in best_rmp_pattern_by_group.items():
                pricing_caches[
                    pattern_group_id
                ].historical_mip_start = pattern.signature
                pricing_caches[pattern_group_id].historical_patterns[
                    pattern.signature
                ] = pattern
            pricing_jobs = [
                (group_index, gid, group)
                for group_index, (gid, group) in enumerate(
                    group_by_id.items(), start=1
                )
            ]
            def run_round(
                jobs: Sequence[Tuple[int, int, dict]],
                *,
                exact_round: bool,
                pricing_duals: MasterDuals,
                allow_certification: bool,
                stop_on_negative: bool,
                pricing_config: Optional[Dict[str, Any]] = None,
            ) -> Tuple[
                List[ExactPattern], bool, Optional[str], bool, Set[int],
                Dict[int, float],
            ]:
                """并行运行一轮定价。

                返回（新负列、该轮是否完整认证、未完成原因、是否发生异常）。
                即使某个MP2超时，其他组已找到的合法负列也会被保留。
                """
                label = "exact" if exact_round else "quick"
                active_pricing_config = pricing_config or config
                future_to_job: Dict[
                    Future[PricingResult], Tuple[int, int]
                ] = {}
                for group_index, gid, group in jobs:
                    _progress(
                        active_pricing_config,
                        f"MP1 node={master_nodes}, CG iteration={cg_iterations + 1}, "
                        f"submit {label} pricing "
                        f"[{group_index}/{len(group_by_id)}]: group={gid}",
                        detail=True,
                    )
                    future = pricing_executor.submit(
                        price_group_exact,
                        group,
                        new_topology,
                        old_topology,
                        weights,
                        active_pricing_config,
                        pricing_duals,
                        baby_cost,
                        forced.get(gid, set()),
                        forbidden.get(gid, set()),
                        deadline,
                        pricing_caches[gid],
                        existing_pattern_signatures={
                            signature
                            for pattern_gid, signature in patterns
                            if pattern_gid == gid
                        },
                        exact=exact_round,
                        phase_one=master.phase_one,
                        stop_on_negative=stop_on_negative,
                    )
                    future_to_job[future] = (group_index, gid)

                priced_by_group: Dict[int, PricingResult] = {}
                failed = False
                for future in as_completed(future_to_job):
                    _, gid = future_to_job[future]
                    try:
                        priced_by_group[gid] = future.result()
                    except Exception as exc:
                        failed = True
                        _progress(
                            config,
                            f"MP1 {label} pricing failed: group={gid}, "
                            f"error={type(exc).__name__}: {exc}",
                        )

                additions: List[ExactPattern] = []
                round_certified = (
                    exact_round and allow_certification and not failed
                )
                incomplete_reason: Optional[str] = (
                    "parallel_pricing_error" if failed else None
                )
                certified_nonnegative: Set[int] = set()
                pricing_lower_bounds: Dict[int, float] = {}
                for _, gid, _ in jobs:
                    priced = priced_by_group.get(gid)
                    if priced is None:
                        round_certified = False
                        continue
                    record_pricing(
                        gid, priced, exact_round=exact_round
                    )
                    if exact_round:
                        pricing_lower_bounds[gid] = (
                            priced.reduced_cost_lower_bound
                        )
                        if (
                            bool(config.get("column_generation", {}).get(
                                "transported_pricing_bounds_enabled", True
                            ))
                            and
                            not master.phase_one
                            and not forced
                            and not any(forbidden.values())
                            and math.isfinite(
                                priced.reduced_cost_lower_bound
                            )
                        ):
                            pricing_caches[gid].strict_lower_bound = (
                                priced.reduced_cost_lower_bound
                            )
                            pricing_caches[
                                gid
                            ].strict_lower_bound_duals = copy.deepcopy(
                                pricing_duals
                            )
                            anchor_cache = pricing_caches[gid]
                            anchor = (
                                float(priced.reduced_cost_lower_bound),
                                copy.deepcopy(pricing_duals),
                            )
                            if (
                                anchor_cache.strict_lower_bound_anchors
                                and anchor_cache.strict_lower_bound_anchors[-1][1]
                                == pricing_duals
                            ):
                                previous_bound, _ = (
                                    anchor_cache.strict_lower_bound_anchors[-1]
                                )
                                anchor_cache.strict_lower_bound_anchors[-1] = (
                                    max(previous_bound, anchor[0]), anchor[1]
                                )
                            else:
                                anchor_cache.strict_lower_bound_anchors.append(
                                    anchor
                                )
                            maximum_anchors = max(
                                1,
                                int(config.get("column_generation", {}).get(
                                    "transported_pricing_bound_anchor_limit", 8
                                )),
                            )
                            if (
                                len(anchor_cache.strict_lower_bound_anchors)
                                > maximum_anchors
                            ):
                                del anchor_cache.strict_lower_bound_anchors[
                                    :-maximum_anchors
                                ]
                    _progress(
                        config,
                        f"MP1 {label} pricing completed: group={gid}, "
                        f"termination={priced.termination}, nodes={priced.nodes}, "
                        f"patterns={len(priced.patterns)}, elapsed={priced.elapsed:.1f}s, "
                        f"reduced_cost="
                        f"{'none' if not math.isfinite(priced.reduced_cost) else f'{priced.reduced_cost:.6f}'}",
                        detail=True,
                    )
                    lower_bound_certifies_nonnegative = (
                        math.isfinite(priced.reduced_cost_lower_bound)
                        and priced.reduced_cost_lower_bound
                        >= -limits["tolerance"]
                    )
                    pricing_certified = (
                        priced.proven_optimal
                        or lower_bound_certifies_nonnegative
                    )
                    if (
                        exact_round
                        and allow_certification
                        and not pricing_certified
                    ):
                        round_certified = False
                        incomplete_reason = priced.termination
                    if (
                        allow_certification
                        and (
                            lower_bound_certifies_nonnegative
                            or (
                                priced.proven_optimal
                                and priced.reduced_cost
                                >= -limits["tolerance"]
                            )
                        )
                    ):
                        certified_nonnegative.add(gid)
                    for candidate in priced.patterns:
                        reduced_cost = _master_column_reduced_cost(
                            candidate, lp.duals, baby_cost,
                            phase_one=master.phase_one,
                        )
                        if reduced_cost >= -limits["tolerance"]:
                            continue
                        key = (gid, candidate.signature)
                        if key not in patterns:
                            patterns[key] = candidate
                            additions.append(candidate)
                        elif exact_round and priced.proven_optimal:
                            # 精确定价返回已在RMP中的严格负列表示MP1/MP2
                            # 对偶或系数映射不一致，不能继续认证。
                            round_certified = False
                            incomplete_reason = (
                                "duplicate_negative_reduced_cost"
                            )
                _progress(
                    config,
                    f"MP2 group="
                    f"{jobs[0][1] if len(jobs) == 1 else 'all'} "
                    f"{label} round: "
                    f"groups={len(jobs)}, "
                    f"new_negative_patterns={len(additions)}, "
                    f"certified={round_certified}",
                )
                return (
                    additions, round_certified, incomplete_reason, failed,
                    certified_nonnegative, pricing_lower_bounds,
                )

            # 快速完整域MIP先找若干改善列；若某组已在相同对偶下证明
            # 最优且检验数非负，精确轮无需重建并重复求解该组。
            quick_jobs = [
                job for job in pricing_jobs
                if job[1] in promising_group_ids
            ]
            (
                quick_patterns, _, _, quick_failed,
                quick_certified_groups, _,
            ) = run_round(
                quick_jobs,
                exact_round=False,
                pricing_duals=discovery_duals,
                allow_certification=False,
                stop_on_negative=True,
            )
            if quick_failed:
                termination = "parallel_pricing_error"
                all_nodes_certified = False
                break
            if quick_patterns:
                stabilizer.observe(True)
                promising_group_ids = {
                    pattern.group_id for pattern in quick_patterns
                }
                added = master.add_patterns(quick_patterns)
                pricing_stats["patterns_added"] += added
                pricing_stats["mp1_incremental_add_batches"] = (
                    master.incremental_batches
                )
                cg_iterations += 1
                previous_root_round = {
                    "kind": "quick",
                    "patterns_returned": len(quick_patterns),
                    "patterns_added": added,
                    "minimum_reduced_cost": min(
                        _master_column_reduced_cost(
                            pattern, lp.duals, baby_cost,
                            phase_one=master.phase_one,
                        )
                        for pattern in quick_patterns
                    ),
                }
                _progress(
                    config,
                    f"MP1 node={master_nodes}, quick iteration={cg_iterations}: "
                    f"LP_cost={lp.objective_cost:.6f}, added_patterns={added}, "
                    f"total_patterns={len(patterns)}",
                )
                continue

            stabilizer.observe(False)

            # 快速阶段没有新列时，只对尚未认证的组执行精确定价。
            transported_bounds: Dict[int, float] = {}
            for _, gid, group in pricing_jobs:
                # A lower bound obtained in a restricted branch domain cannot
                # certify the unrestricted root domain (or another branch).
                # Cross-dual transport is therefore deliberately root-only.
                if (
                    not bool(config.get("column_generation", {}).get(
                        "transported_pricing_bounds_enabled", True
                    ))
                    or forced
                    or any(forbidden.values())
                ):
                    break
                cache = pricing_caches[gid]
                anchors = list(cache.strict_lower_bound_anchors)
                if not anchors and (
                    cache.strict_lower_bound is not None
                    and cache.strict_lower_bound_duals is not None
                ):
                    anchors = [(
                        cache.strict_lower_bound,
                        cache.strict_lower_bound_duals,
                    )]
                if not anchors:
                    continue
                pricing_stats["transported_bound_checks"] += 1
                pricing_stats["transported_anchor_evaluations"] += len(
                    anchors
                )
                pricing_stats["transported_max_anchors_per_group"] = max(
                    pricing_stats["transported_max_anchors_per_group"],
                    len(anchors),
                )
                transported = max(
                    _transport_pricing_lower_bound(
                        group, cache, anchor_duals, lp.duals, anchor_bound,
                    )
                    for anchor_bound, anchor_duals in anchors
                )
                transported_bounds[gid] = transported
            transported_nonnegative = {
                gid for gid, lower_bound in transported_bounds.items()
                if lower_bound >= -limits["tolerance"]
            }
            pricing_stats["transported_nonnegative_skips"] += len(
                transported_nonnegative
            )
            exact_jobs = [
                job for job in pricing_jobs
                if job[1] not in quick_certified_groups
                and job[1] not in transported_nonnegative
            ]
            if exact_jobs:
                (
                    exact_patterns,
                    exact_certified,
                    incomplete_reason,
                    failed,
                    exact_nonnegative,
                    exact_lower_bounds,
                ) = run_round(
                    exact_jobs,
                    exact_round=True,
                    pricing_duals=lp.duals,
                    allow_certification=True,
                    stop_on_negative=True,
                )
            else:
                exact_patterns = []
                exact_certified = True
                incomplete_reason = None
                failed = False
                exact_nonnegative = set()
                exact_lower_bounds = {}
            exact_lower_bounds.update({
                gid: transported_bounds[gid]
                for gid in transported_nonnegative
            })
            if failed:
                termination = "parallel_pricing_error"
                all_nodes_certified = False
                break
            if exact_patterns:
                promising_group_ids = {
                    pattern.group_id for pattern in exact_patterns
                }
                added = master.add_patterns(exact_patterns)
                pricing_stats["patterns_added"] += added
                pricing_stats["mp1_incremental_add_batches"] = (
                    master.incremental_batches
                )
                if not exact_certified:
                    pricing_stats[
                        "columns_kept_from_incomplete_round"
                    ] += added
                cg_iterations += 1
                previous_root_round = {
                    "kind": "exact",
                    "patterns_returned": len(exact_patterns),
                    "patterns_added": added,
                    "minimum_reduced_cost": min(
                        _master_column_reduced_cost(
                            pattern, lp.duals, baby_cost,
                            phase_one=master.phase_one,
                        )
                        for pattern in exact_patterns
                    ),
                    "certified": exact_certified,
                }
                _progress(
                    config,
                    f"MP1 node={master_nodes}, exact iteration={cg_iterations}: "
                    f"LP_cost={lp.objective_cost:.6f}, added_patterns={added}, "
                    f"certified={exact_certified}, total_patterns={len(patterns)}",
                )
                # 即使某组达到本轮时限，已找到的负列仍先进入MP1。
                continue
            if (
                not exact_certified
                and incomplete_reason == "negative_objective_target"
            ):
                # objective_target 可能命中已经存在于 MP1 的重复列。此时
                # 没有新列可加，也不能据此停止认证；仅在这个少见分支取消
                # 目标截断并重跑，直到找到不同负列或证明不存在负列。
                (
                    fallback_patterns,
                    exact_certified,
                    incomplete_reason,
                    failed,
                    exact_nonnegative,
                    exact_lower_bounds,
                ) = run_round(
                    exact_jobs,
                    exact_round=True,
                    pricing_duals=lp.duals,
                    allow_certification=True,
                    stop_on_negative=False,
                )
                exact_lower_bounds.update({
                    gid: transported_bounds[gid]
                    for gid in transported_nonnegative
                })
                if failed:
                    termination = "parallel_pricing_error"
                    all_nodes_certified = False
                    break
                if fallback_patterns:
                    promising_group_ids = {
                        pattern.group_id for pattern in fallback_patterns
                    }
                    added = master.add_patterns(fallback_patterns)
                    pricing_stats["patterns_added"] += added
                    pricing_stats["mp1_incremental_add_batches"] = (
                        master.incremental_batches
                    )
                    if not exact_certified:
                        pricing_stats[
                            "columns_kept_from_incomplete_round"
                        ] += added
                    cg_iterations += 1
                    previous_root_round = {
                        "kind": "exact_fallback",
                        "patterns_returned": len(fallback_patterns),
                        "patterns_added": added,
                        "minimum_reduced_cost": min(
                            _master_column_reduced_cost(
                                pattern, lp.duals, baby_cost,
                                phase_one=master.phase_one,
                            )
                            for pattern in fallback_patterns
                        ),
                        "certified": exact_certified,
                    }
                    _progress(
                        config,
                        f"MP1 node={master_nodes}, exact fallback iteration="
                        f"{cg_iterations}: added_patterns={added}, "
                        f"certified={exact_certified}",
                    )
                    continue
            if (
                not exact_certified
                and incomplete_reason in {
                    "mp2_time_limit", "dfs_time_limit", "dfs_node_limit"
                }
                and bool(config.get("column_generation", {}).get(
                    "escalate_unfinished_exact_pricing", True
                ))
                and pricing_stats["escalated_exact_rounds"] < max(
                    0, int(config.get("column_generation", {}).get(
                        "escalated_exact_pricing_max_rounds", 3
                    ))
                )
            ):
                retry_jobs = [
                    job for job in exact_jobs
                    if job[1] not in exact_nonnegative
                ]
                remaining = max(0.0, deadline - time.perf_counter())
                minimum_remaining = max(
                    1.0,
                    float(config.get("column_generation", {}).get(
                        "escalated_exact_pricing_min_remaining_seconds", 30.0
                    )),
                )
                if retry_jobs and remaining >= minimum_remaining:
                    waves = max(
                        1,
                        math.ceil(
                            len(retry_jobs) / limits["pricing_workers"]
                        ),
                    )
                    escalated_limit = min(
                        max(
                            limits["mp2_time_limit"] * 2.0,
                            0.85 * remaining / waves,
                        ),
                        max(
                            limits["mp2_time_limit"],
                            float(config.get("column_generation", {}).get(
                                "escalated_exact_pricing_max_time_limit", 120.0
                            )),
                        ),
                    )
                    escalated_config = copy.deepcopy(config)
                    escalated_config.setdefault(
                        "column_generation", {}
                    )["mp2_time_limit"] = escalated_limit
                    escalation_started = time.perf_counter()
                    (
                        escalated_patterns,
                        escalated_certified,
                        escalated_reason,
                        failed,
                        escalated_nonnegative,
                        escalated_lower_bounds,
                    ) = run_round(
                        retry_jobs,
                        exact_round=True,
                        pricing_duals=lp.duals,
                        allow_certification=True,
                        stop_on_negative=False,
                        pricing_config=escalated_config,
                    )
                    pricing_stats["escalated_exact_rounds"] += 1
                    pricing_stats["escalated_exact_groups"] += len(
                        retry_jobs
                    )
                    pricing_stats["escalated_exact_seconds"] += (
                        time.perf_counter() - escalation_started
                    )
                    pricing_stats["escalated_exact_time_limits"].append(
                        escalated_limit
                    )
                    exact_lower_bounds.update(escalated_lower_bounds)
                    exact_nonnegative.update(escalated_nonnegative)
                    exact_certified = escalated_certified
                    incomplete_reason = escalated_reason
                    if failed:
                        termination = "parallel_pricing_error"
                        all_nodes_certified = False
                        break
                    if escalated_patterns:
                        promising_group_ids = {
                            pattern.group_id
                            for pattern in escalated_patterns
                        }
                        added = master.add_patterns(escalated_patterns)
                        pricing_stats["patterns_added"] += added
                        pricing_stats["escalated_exact_patterns"] += added
                        pricing_stats["mp1_incremental_add_batches"] = (
                            master.incremental_batches
                        )
                        cg_iterations += 1
                        previous_root_round = {
                            "kind": "escalated_exact",
                            "patterns_returned": len(escalated_patterns),
                            "patterns_added": added,
                            "minimum_reduced_cost": min(
                                _master_column_reduced_cost(
                                    pattern, lp.duals, baby_cost,
                                    phase_one=master.phase_one,
                                )
                                for pattern in escalated_patterns
                            ),
                            "certified": escalated_certified,
                        }
                        _progress(
                            config,
                            f"MP1 node={master_nodes}, escalated exact "
                            f"iteration={cg_iterations}: groups="
                            f"{len(retry_jobs)}, time_limit="
                            f"{escalated_limit:.1f}s, added_patterns={added}, "
                            f"certified={escalated_certified}",
                        )
                        continue
            # 即使部分MP2未关闭，只要每组都返回了严格检验数下界，仍可
            # 通过调整同行组凸性对偶得到完整列空间的安全得分上界。
            if (
                not master.phase_one
                and not forced
                and not any(forbidden.values())
            ):
                safe_cost_lower_bound = _safe_master_cost_lower_bound(
                    lp.objective_cost,
                    group_by_id,
                    exact_lower_bounds,
                )
                heuristic_score = warm_start_diagnostics.get(
                    "score_without_time_penalty"
                )
                if (
                    safe_cost_lower_bound is not None
                    and heuristic_score is not None
                ):
                    candidate_upper = -safe_cost_lower_bound
                    numeric_tolerance = max(
                        1e-5, 100.0 * limits["tolerance"]
                    )
                    if (
                        candidate_upper
                        >= float(heuristic_score) - numeric_tolerance
                    ):
                        candidate_upper = max(
                            candidate_upper, float(heuristic_score)
                        )
                        if (
                            safe_root_lp_upper_bound is None
                            or candidate_upper < safe_root_lp_upper_bound
                        ):
                            safe_root_lp_upper_bound = candidate_upper
                            safe_pricing_lower_bounds = dict(
                                exact_lower_bounds
                            )
                            safe_bound_source = (
                                "exact_or_timeout_pricing_lower_bounds"
                            )
                        safe_root_gap_absolute = max(
                            0.0,
                            safe_root_lp_upper_bound
                            - float(heuristic_score),
                        )
                        safe_root_gap_relative = (
                            safe_root_gap_absolute
                            / max(1.0, abs(float(heuristic_score)))
                        )
                        target_percent = max(
                            0.0,
                            float(
                                config.get("column_generation", {}).get(
                                    "safe_gap_stop_percent", 2.0
                                )
                            ),
                        )
                        _progress(
                            config,
                            f"safe root bound={safe_root_lp_upper_bound:.6f}, "
                            f"heuristic_gap="
                            f"{100.0 * safe_root_gap_relative:.4f}%",
                        )
                        if (
                            100.0 * safe_root_gap_relative
                            <= target_percent + 1e-12
                            and not exact_certified
                        ):
                            safe_gap_target_reached = True
                            termination = "safe_gap_target_reached"
                            all_nodes_certified = False
                            break
            if not exact_certified:
                termination = incomplete_reason or "mp2_not_certified"
                all_nodes_certified = False
                break
            node_certified = True
            cg_iterations += 1
            previous_root_round = {
                "kind": "certification",
                "patterns_returned": 0,
                "patterns_added": 0,
                "minimum_reduced_cost": None,
                "certified": True,
            }
            _progress(
                config,
                f"MP1 node={master_nodes}, certification iteration="
                f"{cg_iterations}: LP_cost={lp.objective_cost:.6f}, "
                "all groups have no negative reduced-cost pattern",
            )
            break

        pricing_stats["adaptive_dual_stabilization"] = {
            "mode": stabilization_mode,
            "alpha_final": stabilizer.alpha,
            "alpha_history": stabilizer.alpha_history,
            "serious_steps": stabilizer.serious_steps,
            "null_steps": stabilizer.null_steps,
        }

        if not all_nodes_certified:
            break
        if not lp.feasible:
            break
        if not node_certified:
            all_nodes_certified = False
            termination = "mp1_node_not_certified"
            break
        if lp.artificial_value > limits["tolerance"]:
            # Phase-I人工列在完整精确定价后仍为正，才说明这个MP1分支
            # 节点在完整列空间中不可行，可以安全关闭。
            _progress(
                config,
                f"MP1 node={master_nodes} certified infeasible: "
                f"phase1_artificial={lp.artificial_value:.6g}",
            )
            if not forced and not any(forbidden.values()):
                termination = "root_master_infeasible"
            continue
        # 根节点完成全部同行组精确定价后，必须先记录完整列空间LP界。
        # 即使该界已经等于整数incumbent并可立即剪枝，认证字段仍应保留。
        if not forced and not any(forbidden.values()):
            complete_root_lp_cost = lp.objective_cost
            root_lp_certified = True
        if lp.objective_cost >= incumbent_cost - limits["tolerance"]:
            continue

        branch_choice = _choose_placement_branch(
            lp, patterns, limits["tolerance"]
        )
        if branch_choice is None:
            selected = [patterns[key] for key, value in lp.pattern_values.items() if value > 0.5]
            raw_evaluator_cost, violations, _ = _evaluate_integer_patterns(
                selected, new_seats_data, old_seats_data, groups_data,
                weights, config,
            )
            equivalence_checks += 1
            error = abs(raw_evaluator_cost - lp.objective_cost)
            max_equivalence_error = max(max_equivalence_error, error)
            if violations != 0:
                # 所有硬约束都应已由MP2方案或MP1资源行表达。整数主问题仍有
                # 违规意味着模型漏约束，不能静默丢弃该点并关闭分支节点。
                all_nodes_certified = False
                root_lp_certified = False
                termination = "model_hard_constraint_mismatch"
                break
            if violations == 0 and error > 1e-5:
                all_nodes_certified = False
                root_lp_certified = False
                termination = "model_equivalence_error"
                break
            if violations == 0 and raw_evaluator_cost < incumbent_cost:
                incumbent_cost = raw_evaluator_cost
                incumbent_patterns = selected
            continue

        # 在根节点和周期节点上，用当前已生成列求一个短时整数RMP。
        # 这是纯primal heuristic：只改善incumbent，不改变认证LP下界。
        should_round = (
            restricted_mip_time_limit > 0.0
            and (
                master_nodes == 1
                or (
                    restricted_mip_frequency > 0
                    and master_nodes % restricted_mip_frequency == 0
                )
            )
        )
        if should_round:
            rounding_started = time.perf_counter()
            restricted_mip_stats["calls"] += 1
            rounded, rounded_status = master.solve_restricted_integer(
                min(
                    restricted_mip_time_limit,
                    max(0.001, deadline - time.perf_counter()),
                )
            )
            restricted_mip_stats["seconds"] += (
                time.perf_counter() - rounding_started
            )
            if rounded is not None:
                rounded_cost, rounded_violations, _ = (
                    _evaluate_integer_patterns(
                        rounded, new_seats_data, old_seats_data,
                        groups_data, weights, config,
                    )
                )
                if rounded_violations == 0:
                    restricted_mip_stats["feasible_solutions"] += 1
                    if rounded_cost < incumbent_cost - limits["tolerance"]:
                        incumbent_cost = rounded_cost
                        incumbent_patterns = rounded
                        restricted_mip_stats["incumbent_improvements"] += 1
                        _progress(
                            config,
                            f"MP1 node={master_nodes} restricted-MIP improved "
                            f"incumbent_score={-incumbent_cost:.6f}",
                        )
                else:
                    restricted_mip_stats["rejected_solutions"] += 1
                    _progress(
                        config,
                        f"MP1 node={master_nodes} restricted-MIP solution "
                        f"rejected: hard_violations={rounded_violations}",
                    )
            else:
                _progress(
                    config,
                    f"MP1 node={master_nodes} restricted-MIP found no "
                    f"integer solution ({rounded_status})",
                    detail=True,
                )
            if lp.objective_cost >= incumbent_cost - limits["tolerance"]:
                continue

        gid, signature, branch_value = branch_choice
        force_child = {
            key: set(value) for key, value in forced.items()
        }
        force_child.setdefault(gid, set()).add(signature)
        forbid_child = {
            key: set(value) for key, value in forbidden.items()
        }
        forbid_child.setdefault(gid, set()).add(signature)
        force_node: Node = (
            lp.objective_cost, serial, depth + 1,
            force_child,
            {key: set(value) for key, value in forbidden.items()},
        )
        serial += 1
        forbid_node: Node = (
            lp.objective_cost, serial, depth + 1,
            {key: set(value) for key, value in forced.items()},
            forbid_child,
        )
        serial += 1
        placement_branches += 1

        incumbent_group = next(
            (
                pattern for pattern in incumbent_patterns
                if pattern.group_id == gid
            ),
            None,
        )
        prefer_force = (
            incumbent_group is not None
            and signature in incumbent_group.signature
        )
        preferred = force_node if prefer_force else forbid_node
        alternative = forbid_node if prefer_force else force_node
        heapq.heappush(frontier, alternative)
        if dive_max_nodes > 0:
            dive_stack.append(preferred)
        else:
            heapq.heappush(frontier, preferred)
        _progress(
            config,
            f"MP1 branch: group={gid}, placement={signature}, "
            f"LP_value={branch_value:.6f}, preferred="
            f"{'force' if prefer_force else 'forbid'}",
            detail=True,
        )

    if root_cg_elapsed_seconds is None:
        root_cg_elapsed_seconds = time.perf_counter() - root_cg_started

    # 若常规定价在一次完整原始对偶扫描结束前耗尽预算，使用最近一次
    # 根RMP对偶只计算各组DFS根松弛。该轮不搜索、不加列，所得界可能
    # 较宽，但每个组的检验数下界均覆盖完整定价域，因此仍可构造严格U。
    if (
        safe_root_lp_upper_bound is None
        and latest_root_lp_cost is not None
        and latest_root_duals is not None
        and final_root_bound_reserve > 0.0
    ):
        sweep_started = time.perf_counter()
        final_root_bound_sweep["attempted"] = True
        bound_config = copy.deepcopy(config)
        bound_pricing_cfg = bound_config.setdefault(
            "column_generation", {}
        )
        bound_pricing_cfg["dfs_exact_time_limit"] = 0.0
        bound_pricing_cfg["dfs_node_limit"] = 1
        sweep_deadline = hard_deadline - final_rounding_reserve
        futures: Dict[Future[PricingResult], int] = {}
        for gid, group in group_by_id.items():
            futures[pricing_executor.submit(
                _price_group_exact_dfs,
                group,
                new_topology,
                weights,
                bound_config,
                latest_root_duals,
                baby_cost,
                set(),
                set(),
                sweep_deadline,
                pricing_caches[gid],
                exact=True,
                phase_one=False,
                stop_on_negative=False,
            )] = gid
        root_bounds: Dict[int, float] = {}
        for future in as_completed(futures):
            gid = futures[future]
            try:
                priced = future.result()
            except Exception:
                continue
            final_root_bound_sweep["groups"] += 1
            if math.isfinite(priced.reduced_cost_lower_bound):
                root_bounds[gid] = priced.reduced_cost_lower_bound
                final_root_bound_sweep["finite_lower_bounds"] += 1
        # The zero-node DFS root relaxation is only one valid lower bound.
        # Earlier exact rounds may provide much stronger strict bounds at
        # different dual vectors.  Transport every retained anchor to the
        # latest root dual and keep the maximum safe bound group by group.
        for gid, group in group_by_id.items():
            cache = pricing_caches[gid]
            anchors = list(cache.strict_lower_bound_anchors)
            if not anchors and (
                cache.strict_lower_bound is not None
                and cache.strict_lower_bound_duals is not None
            ):
                anchors = [(
                    cache.strict_lower_bound,
                    cache.strict_lower_bound_duals,
                )]
            if not anchors:
                continue
            final_root_bound_sweep["historical_anchor_groups"] += 1
            final_root_bound_sweep[
                "historical_anchor_evaluations"
            ] += len(anchors)
            transported = max(
                _transport_pricing_lower_bound(
                    group, cache, anchor_duals, latest_root_duals,
                    anchor_bound,
                )
                for anchor_bound, anchor_duals in anchors
            )
            previous = root_bounds.get(gid, -math.inf)
            if transported > previous + limits["tolerance"]:
                root_bounds[gid] = transported
                final_root_bound_sweep[
                    "historical_anchor_improved_groups"
                ] += 1

        recovered_patterns: Dict[Tuple[int, tuple], ExactPattern] = {}

        # Large mixed groups are often hard because a few protected/SSR
        # passengers interact with many ordinary passengers.  Enumerate only
        # that small skeleton, while enforcing distinct seats for everyone
        # else through a polynomial assignment relaxation.
        matching_enabled = bool(bound_pricing_cfg.get(
            "assignment_bound_enabled", True
        ))
        matching_min_size = max(2, int(bound_pricing_cfg.get(
            "assignment_bound_min_group_size", 2
        )))
        matching_max_combinations = max(1, int(bound_pricing_cfg.get(
            "assignment_bound_max_skeleton_combinations", 100_000
        )))
        matching_max_window_skeletons = max(0, int(
            bound_pricing_cfg.get(
                "assignment_bound_max_window_skeletons", 256
            )
        ))
        matching_top_k_window_skeletons = max(0, int(
            bound_pricing_cfg.get(
                "assignment_bound_top_k_window_skeletons", 256
            )
        ))
        matching_started = time.perf_counter()
        if matching_enabled:
            for gid, group in sorted(
                group_by_id.items(),
                key=lambda item: root_bounds.get(
                    int(item[0]), math.inf
                ),
            ):
                if len(group.get("psrs", [])) < matching_min_size:
                    continue
                if root_bounds.get(gid, 0.0) >= -limits["tolerance"]:
                    break
                if time.perf_counter() >= sweep_deadline:
                    break
                stats = _skeleton_assignment_lower_bound(
                    group,
                    pricing_caches[gid],
                    weights,
                    bound_config,
                    latest_root_duals,
                    baby_cost,
                    max_combinations=matching_max_combinations,
                    max_window_skeletons=(
                        matching_max_window_skeletons
                    ),
                    top_k_window_skeletons=(
                        matching_top_k_window_skeletons
                    ),
                )
                if stats is None:
                    continue
                candidate_placements = stats.pop(
                    "candidate_placements", None
                )
                final_root_bound_sweep[
                    "assignment_bound_groups"
                ] += 1
                previous = root_bounds.get(gid, -math.inf)
                lower_bound = float(stats["lower_bound"])
                improved = lower_bound > previous + limits["tolerance"]
                if improved:
                    root_bounds[gid] = lower_bound
                    final_root_bound_sweep[
                        "assignment_bound_improved_groups"
                    ] += 1
                candidate_reduced_cost = None
                candidate_added = False
                candidate_pattern: Optional[ExactPattern] = None
                if candidate_placements:
                    candidate_trials = [tuple(candidate_placements)]
                    if not _caregiver_ok(
                        group, candidate_placements,
                        new_topology, bound_config,
                    ):
                        by_index = {
                            option.passenger_index: option
                            for option in candidate_placements
                        }
                        neighbor_seats: Set[str] = set()
                        for passenger_index, passenger in enumerate(
                            group.get("psrs", [])
                        ):
                            allow_cross = _caregiver_cross_aisle(
                                passenger, bound_config
                            )
                            if allow_cross is None:
                                continue
                            neighbor_seats.update(
                                new_topology.row_neighbors(
                                    by_index[passenger_index].seat_id,
                                    allow_cross_aisle=allow_cross,
                                )
                            )
                        for passenger_index, passenger in enumerate(
                            group.get("psrs", [])
                        ):
                            if not evaluator._is_adult_caregiver(passenger):
                                continue
                            retained = [
                                option for option in candidate_placements
                                if option.passenger_index != passenger_index
                            ]
                            used_resources = {
                                resource
                                for option in retained
                                for resource in option.resources
                            }
                            for option in pricing_caches[gid].all_options[
                                passenger_index
                            ]:
                                if (
                                    option.seat_id not in neighbor_seats
                                    or used_resources.intersection(
                                        option.resources
                                    )
                                ):
                                    continue
                                trial = tuple(retained + [option])
                                if _caregiver_ok(
                                    group, trial,
                                    new_topology, bound_config,
                                ):
                                    candidate_trials.append(trial)
                    best_candidate_cost = math.inf
                    for trial in candidate_trials:
                        if not _caregiver_ok(
                            group, trial, new_topology, bound_config
                        ):
                            continue
                        pattern = _pattern_from_placements(
                            group, trial, new_topology, weights,
                            bound_config, baby_cost,
                        )
                        reduced_cost = _master_column_reduced_cost(
                            pattern, latest_root_duals, baby_cost,
                            phase_one=False,
                        )
                        if reduced_cost < best_candidate_cost:
                            best_candidate_cost = reduced_cost
                            candidate_pattern = pattern
                    candidate_reduced_cost = (
                        best_candidate_cost
                        if candidate_pattern is not None else None
                    )
                if candidate_pattern is not None:
                    final_root_bound_sweep[
                        "assignment_candidate_columns"
                    ] += 1
                    key = (
                        candidate_pattern.group_id,
                        candidate_pattern.signature,
                    )
                    if (
                        candidate_reduced_cost < -limits["tolerance"]
                        and key not in patterns
                    ):
                        patterns[key] = candidate_pattern
                        recovered_patterns[key] = candidate_pattern
                        candidate_added = True
                        final_root_bound_sweep[
                            "assignment_negative_columns"
                        ] += 1
                final_root_bound_sweep[
                    "assignment_bound_details"
                ].append({
                    "group_id": gid,
                    "passenger_count": len(group.get("psrs", [])),
                    "lower_bound_before": previous,
                    "lower_bound_after": max(previous, lower_bound),
                    "improved": improved,
                    "candidate_reduced_cost": candidate_reduced_cost,
                    "candidate_added": candidate_added,
                    **stats,
                })
        final_root_bound_sweep["assignment_bound_seconds"] = (
            time.perf_counter() - matching_started
        )

        initial_correction = -sum(
            min(0.0, bound) for bound in root_bounds.values()
        )
        final_root_bound_sweep["correction_before_refinement"] = (
            initial_correction
        )
        refinement_enabled = bool(bound_pricing_cfg.get(
            "final_bound_refinement_enabled", True
        ))
        final_root_bound_sweep["refinement_enabled"] = refinement_enabled
        ranked_bounds = _rank_negative_pricing_bounds(
            root_bounds, tolerance=limits["tolerance"]
        )
        final_root_bound_sweep["refinement_candidates"] = len(ranked_bounds)
        refinement_limit = max(0, int(bound_pricing_cfg.get(
            "final_bound_refinement_group_limit", 8
        )))
        refinement_dfs_seconds = max(0.0, float(bound_pricing_cfg.get(
            "final_bound_refinement_dfs_seconds", 6.0
        )))
        refinement_mip_seconds = max(0.0, float(bound_pricing_cfg.get(
            "final_bound_refinement_mip_seconds", 6.0
        )))
        refinement_node_limit = max(1, int(bound_pricing_cfg.get(
            "final_bound_refinement_dfs_node_limit", 2_000_000
        )))
        refinement_max_attempts = max(1, int(bound_pricing_cfg.get(
            "final_bound_refinement_max_attempts_per_group", 3
        )))
        refinement_budget_growth = max(1.0, float(bound_pricing_cfg.get(
            "final_bound_refinement_budget_growth", 2.0
        )))
        refinement_max_dfs_seconds = max(
            refinement_dfs_seconds,
            float(bound_pricing_cfg.get(
                "final_bound_refinement_max_dfs_seconds", 30.0
            )),
        )
        refinement_max_mip_seconds = max(
            refinement_mip_seconds,
            float(bound_pricing_cfg.get(
                "final_bound_refinement_max_mip_seconds", 30.0
            )),
        )
        refinement_started = time.perf_counter()
        recovery_rmp_reserve = max(
            0.0,
            float(
                config.get("column_generation", {}).get(
                    "final_bound_recovery_rmp_reserve_seconds", 3.0
                )
            ),
        )
        refinement_deadline = max(
            time.perf_counter(), sweep_deadline - recovery_rmp_reserve
        )
        if refinement_enabled and refinement_limit > 0:
            refinement_queue = [
                gid for gid, _ in ranked_bounds[:refinement_limit]
            ]
            refinement_attempts: Dict[int, int] = defaultdict(int)
            refinement_improved_group_ids: Set[int] = set()
            while refinement_queue:
                gid = refinement_queue.pop(0)
                old_bound = root_bounds[gid]
                remaining = refinement_deadline - time.perf_counter()
                if remaining <= 0.01:
                    break
                attempt_number = refinement_attempts[gid] + 1
                refinement_attempts[gid] = attempt_number
                growth = refinement_budget_growth ** (attempt_number - 1)
                attempt_dfs_seconds = min(
                    refinement_max_dfs_seconds,
                    refinement_dfs_seconds * growth,
                )
                attempt_mip_seconds = min(
                    refinement_max_mip_seconds,
                    refinement_mip_seconds * growth,
                )
                group = group_by_id[gid]
                per_group_deadline = min(
                    refinement_deadline,
                    time.perf_counter()
                    + attempt_dfs_seconds
                    + attempt_mip_seconds,
                )
                refinement_config = copy.deepcopy(config)
                refinement_cfg = refinement_config.setdefault(
                    "column_generation", {}
                )
                dfs_budget = min(attempt_dfs_seconds, remaining)
                refinement_cfg["dfs_exact_time_limit"] = dfs_budget
                refinement_cfg["dfs_large_group_exact_time_limit"] = (
                    dfs_budget
                )
                refinement_cfg["dfs_node_limit"] = refinement_node_limit
                refinement_cfg["dfs_large_group_node_limit"] = (
                    refinement_node_limit
                )
                final_root_bound_sweep[
                    "refinement_attempted_groups"
                ].append(gid)
                final_root_bound_sweep["refinement_total_attempts"] += 1
                dfs_result = _price_group_exact_dfs(
                    group,
                    new_topology,
                    weights,
                    refinement_config,
                    latest_root_duals,
                    baby_cost,
                    set(),
                    set(),
                    min(
                        per_group_deadline,
                        time.perf_counter() + dfs_budget,
                    ),
                    pricing_caches[gid],
                    exact=True,
                    phase_one=False,
                    stop_on_negative=False,
                )
                final_root_bound_sweep["refinement_dfs_calls"] += 1
                record_pricing(gid, dfs_result, exact_round=True)
                refined_bound = max(
                    old_bound, dfs_result.reduced_cost_lower_bound
                )
                group_exact = dfs_result.proven_optimal
                mip_result: Optional[PricingResult] = None
                if (
                    not group_exact
                    and attempt_mip_seconds > 0.0
                    and per_group_deadline - time.perf_counter() > 0.01
                ):
                    mip_config = copy.deepcopy(refinement_config)
                    mip_config.setdefault("column_generation", {})[
                        "mp2_time_limit"
                    ] = min(
                        attempt_mip_seconds,
                        per_group_deadline - time.perf_counter(),
                    )
                    mip_result = _price_group_complete_domain_mip(
                        group,
                        new_topology,
                        old_topology,
                        weights,
                        mip_config,
                        latest_root_duals,
                        baby_cost,
                        set(),
                        set(),
                        per_group_deadline,
                        pricing_caches[gid],
                        exact=True,
                        phase_one=False,
                        stop_on_negative=False,
                    )
                    final_root_bound_sweep["refinement_mip_calls"] += 1
                    record_pricing(gid, mip_result, exact_round=True)
                    refined_bound = max(
                        refined_bound,
                        mip_result.reduced_cost_lower_bound,
                    )
                    group_exact = mip_result.proven_optimal
                for pricing_result in (dfs_result, mip_result):
                    if pricing_result is None:
                        continue
                    for candidate in pricing_result.patterns:
                        reduced_cost = _master_column_reduced_cost(
                            candidate,
                            latest_root_duals,
                            baby_cost,
                            phase_one=False,
                        )
                        key = (candidate.group_id, candidate.signature)
                        if (
                            reduced_cost < -limits["tolerance"]
                            and key not in patterns
                        ):
                            patterns[key] = candidate
                            recovered_patterns[key] = candidate
                root_bounds[gid] = refined_bound
                if refined_bound > old_bound + limits["tolerance"]:
                    refinement_improved_group_ids.add(gid)
                if group_exact:
                    final_root_bound_sweep["refinement_exact_groups"] += 1
                if refined_bound >= -limits["tolerance"]:
                    final_root_bound_sweep[
                        "refinement_nonnegative_groups"
                    ] += 1
                final_root_bound_sweep["refinement_group_details"].append({
                    "group_id": gid,
                    "attempt": attempt_number,
                    "passenger_count": len(group.get("psrs", [])),
                    "lower_bound_before": old_bound,
                    "lower_bound_after": refined_bound,
                    "correction_reduction": max(
                        0.0,
                        min(0.0, refined_bound) - min(0.0, old_bound),
                    ),
                    "exact": group_exact,
                    "nonnegative": (
                        refined_bound >= -limits["tolerance"]
                    ),
                    "dfs_termination": dfs_result.termination,
                    "dfs_seconds": dfs_result.elapsed,
                    "dfs_nodes": dfs_result.dfs_nodes,
                    "dfs_lower_bound": (
                        dfs_result.reduced_cost_lower_bound
                    ),
                    "mip_termination": (
                        None if mip_result is None else mip_result.termination
                    ),
                    "mip_seconds": (
                        0.0 if mip_result is None else mip_result.elapsed
                    ),
                    "mip_lower_bound": (
                        None
                        if mip_result is None
                        else mip_result.reduced_cost_lower_bound
                    ),
                })
                if (
                    not group_exact
                    and refined_bound < -limits["tolerance"]
                    and attempt_number < refinement_max_attempts
                    and refinement_deadline - time.perf_counter() > 0.01
                ):
                    refinement_queue.append(gid)
                _progress(
                    config,
                    "final bound refinement group="
                    f"{gid}, lower_bound={old_bound:.6f}->"
                    f"{refined_bound:.6f}, exact={group_exact}",
                    detail=True,
                )
            final_root_bound_sweep["refinement_improved_groups"] = len(
                refinement_improved_group_ids
            )
        final_root_bound_sweep["recovered_negative_columns"] = len(
            recovered_patterns
        )
        pricing_stats["patterns_added"] += len(recovered_patterns)
        final_root_bound_sweep["refinement_seconds"] = (
            time.perf_counter() - refinement_started
        )
        final_root_bound_sweep["correction_after_refinement"] = -sum(
            min(0.0, bound) for bound in root_bounds.values()
        )
        # Final pricing may discover genuine negative columns.  Re-solve a
        # fresh restricted root LP with them, then transport every strict
        # group bound to the new dual vector.  Both the old and transported
        # corrections are valid; retain whichever gives the tighter full-
        # column-space cost lower bound.  The recovered columns also remain
        # available to the final integer restricted master.
        if recovered_patterns:
            recovery_rmp_started = time.perf_counter()
            final_root_bound_sweep["recovery_rmp_attempted"] = True
            final_root_bound_sweep["recovery_rmp_started_after_deadline"] = (
                recovery_rmp_started > sweep_deadline
            )
            final_root_bound_sweep["recovery_rmp_cost_before"] = (
                latest_root_lp_cost
            )
            baseline_master = PersistentExactMaster(
                groups_data, new_topology, baby_cost, {}, {}
            )
            baseline_master.add_patterns(
                pattern for key, pattern in patterns.items()
                if key not in recovered_patterns
            )
            baseline_lp = baseline_master.solve()
            if (
                baseline_lp.feasible
                and baseline_lp.artificial_value <= limits["tolerance"]
            ):
                baseline_master.start_phase_two()
                baseline_lp = baseline_master.solve()
            if baseline_lp.feasible and not baseline_master.phase_one:
                final_root_bound_sweep[
                    "recovery_rebuilt_baseline_cost"
                ] = baseline_lp.objective_cost
                final_root_bound_sweep[
                    "recovery_rebuilt_candidate_reduced_costs"
                ] = {
                    str(key): _master_column_reduced_cost(
                        pattern,
                        baseline_lp.duals,
                        baby_cost,
                        phase_one=False,
                    )
                    for key, pattern in recovered_patterns.items()
                }
            recovery_master = PersistentExactMaster(
                groups_data, new_topology, baby_cost, {}, {}
            )
            final_root_bound_sweep["recovery_total_pool_columns"] = (
                recovery_master.add_patterns(patterns.values())
            )
            final_root_bound_sweep["recovery_added_columns"] = len(
                recovered_patterns
            )
            recovery_lp = recovery_master.solve()
            if (
                recovery_lp.feasible
                and recovery_lp.artificial_value <= limits["tolerance"]
            ):
                recovery_master.start_phase_two()
                recovery_lp = recovery_master.solve()
            if recovery_lp.feasible and not recovery_master.phase_one:
                final_root_bound_sweep["recovery_rmp_cost_after"] = (
                    recovery_lp.objective_cost
                )
                recovery_solution = recovery_master.solver.getSolution()
                recovered_column_by_key = {
                    (pattern.group_id, pattern.signature): column
                    for column, pattern
                    in recovery_master.pattern_columns.items()
                    if (pattern.group_id, pattern.signature)
                    in recovered_patterns
                }
                final_root_bound_sweep[
                    "recovery_solver_column_duals"
                ] = {
                    str(key): float(recovery_solution.col_dual[column])
                    for key, column in recovered_column_by_key.items()
                }
                final_root_bound_sweep[
                    "recovery_formula_column_duals"
                ] = {
                    str(key): _master_column_reduced_cost(
                        recovered_patterns[key], recovery_lp.duals,
                        baby_cost, phase_one=False,
                    )
                    for key in recovered_column_by_key
                }
                transported_recovery_bounds = {
                    gid: _transport_pricing_lower_bound(
                        group_by_id[gid],
                        pricing_caches[gid],
                        latest_root_duals,
                        recovery_lp.duals,
                        lower_bound,
                    )
                    for gid, lower_bound in root_bounds.items()
                }
                old_safe_cost = _safe_master_cost_lower_bound(
                    latest_root_lp_cost, group_by_id, root_bounds
                )
                recovery_safe_cost = _safe_master_cost_lower_bound(
                    recovery_lp.objective_cost,
                    group_by_id,
                    transported_recovery_bounds,
                )
                if (
                    recovery_safe_cost is not None
                    and (
                        old_safe_cost is None
                        or recovery_safe_cost
                        > old_safe_cost + limits["tolerance"]
                    )
                ):
                    latest_root_lp_cost = recovery_lp.objective_cost
                    latest_root_duals = recovery_lp.duals
                    root_bounds = transported_recovery_bounds
                    final_root_bound_sweep["recovery_rmp_selected"] = True
                    final_root_bound_sweep[
                        "correction_after_refinement"
                    ] = -sum(
                        min(0.0, bound) for bound in root_bounds.values()
                    )
            final_root_bound_sweep["recovery_rmp_seconds"] = (
                time.perf_counter() - recovery_rmp_started
            )
        safe_cost_lower_bound = _safe_master_cost_lower_bound(
            latest_root_lp_cost, group_by_id, root_bounds
        )
        if safe_cost_lower_bound is not None:
            candidate_upper = -safe_cost_lower_bound
            feasible_score = -incumbent_cost
            numeric_tolerance = max(1e-5, 100.0 * limits["tolerance"])
            if candidate_upper >= feasible_score - numeric_tolerance:
                safe_root_lp_upper_bound = max(
                    candidate_upper, feasible_score
                )
                safe_pricing_lower_bounds = root_bounds
                safe_bound_source = (
                    "final_root_relaxation_history_and_targeted_refinement"
                    if final_root_bound_sweep[
                        "refinement_attempted_groups"
                    ]
                    else "final_root_relaxation_plus_transported_history"
                )
                heuristic_score = warm_start_diagnostics.get(
                    "score_without_time_penalty"
                )
                if heuristic_score is not None:
                    safe_root_gap_absolute = max(
                        0.0,
                        safe_root_lp_upper_bound - float(heuristic_score),
                    )
                    safe_root_gap_relative = (
                        safe_root_gap_absolute
                        / max(1.0, abs(float(heuristic_score)))
                    )
                final_root_bound_sweep["complete"] = True
                _progress(
                    config,
                    "final root-relaxation sweep produced safe bound="
                    f"{safe_root_lp_upper_bound:.6f}",
                )
        final_root_bound_sweep["seconds"] = (
            time.perf_counter() - sweep_started
        )

    pricing_executor.shutdown(wait=True, cancel_futures=True)

    # Pricing may stop before the root LP is certified, or the regular root
    # restricted MIP may time out without an incumbent.  The generated column
    # pool is still useful for obtaining a complete primal solution.  Make one
    # final, explicitly non-certifying integer-RMP attempt before returning.
    # This cannot improve or create an LP certificate; it only supplies a
    # feasible allocation for diagnostics and heuristic comparison.
    remaining_for_rounding = hard_deadline - time.perf_counter()
    if (
        restricted_mip_time_limit > 0.0
        and patterns
        and remaining_for_rounding > 0.001
    ):
        final_rounding_started = time.perf_counter()
        restricted_mip_stats["calls"] += 1
        final_master = PersistentExactMaster(
            groups_data, new_topology, baby_cost, {}, {}
        )
        final_master.add_patterns(patterns.values())
        final_lp = final_master.solve()
        if (
            final_lp.feasible
            and final_lp.artificial_value <= limits["tolerance"]
        ):
            final_master.start_phase_two()
            final_lp = final_master.solve()
        mip_time_available = hard_deadline - time.perf_counter()
        if (
            final_lp.feasible
            and not final_master.phase_one
            and mip_time_available > 0.001
        ):
            rounded, rounded_status = final_master.solve_restricted_integer(
                min(restricted_mip_time_limit, mip_time_available)
            )
        else:
            rounded = None
            rounded_status = (
                "phase_one_infeasible"
                if final_master.phase_one
                else "no_time_after_final_lp"
            )
        restricted_mip_stats["seconds"] += (
            time.perf_counter() - final_rounding_started
        )
        if rounded is not None:
            rounded_cost, rounded_violations, _ = _evaluate_integer_patterns(
                rounded,
                new_seats_data,
                old_seats_data,
                groups_data,
                weights,
                config,
            )
            if rounded_violations == 0:
                restricted_mip_stats["feasible_solutions"] += 1
                if rounded_cost < incumbent_cost - limits["tolerance"]:
                    incumbent_cost = rounded_cost
                    incumbent_patterns = rounded
                    restricted_mip_stats["incumbent_improvements"] += 1
                    _progress(
                        config,
                        "final restricted-MIP improved "
                        f"incumbent_score={-incumbent_cost:.6f}",
                    )
            else:
                restricted_mip_stats["rejected_solutions"] += 1
                _progress(
                    config,
                    "final restricted-MIP solution rejected: "
                    f"hard_violations={rounded_violations}",
                )
        else:
            _progress(
                config,
                "final restricted-MIP found no integer solution "
                f"({rounded_status})",
            )

    proven = (
        all_nodes_certified
        and root_lp_certified
        and complete_root_lp_cost is not None
        and not frontier
        and not dive_stack
        and math.isfinite(incumbent_cost)
    )
    if not math.isfinite(incumbent_cost):
        raise RuntimeError(
            "列生成在限制内未找到所有普通旅客均就座的完整可行解；"
            "为避免返回未分配旅客，已停止输出结果"
        )
    assignments, blocked_by = _assemble(incumbent_patterns)
    violations, unassigned, violation_detail = evaluator.count_hard_constraint_violations(
        new_seats_data, groups_data, assignments, config
    )
    soft, soft_detail = evaluator.calculate_soft_score(
        new_seats_data, old_seats_data, groups_data, assignments, weights, config
    )
    score, _ = evaluator.calculate_combined_score(
        soft, unassigned, violations, 0.0, config
    )
    score_detail = {**soft_detail, "unassigned_count": unassigned, "violations": violations,
                    "violation_detail": violation_detail, "time_penalty": 0.0,
                    "combined_score": score}
    complete_lp_bound = (
        max(
            -complete_root_lp_cost,
            float(config.get("penalty", {}).get("score_lower_bound", -math.inf)),
        )
        if complete_root_lp_cost is not None and root_lp_certified else None
    )
    lp_gap_absolute = None if complete_lp_bound is None else complete_lp_bound - score
    lp_gap_relative = (
        None
        if lp_gap_absolute is None
        else lp_gap_absolute / max(1.0, abs(score))
    )
    result = {
        "assigned_seats": assignments,
        "occupied": sorted(set(assignments.values())),
        "blocked_by": blocked_by,
        "score_detail": score_detail,
        "total_score": score,
        "diagnostics": {
            "method": (
                "placement_branch_and_price_with_guided_dfs_and_"
                "restricted_master_rounding"
            ),
            "objective_equivalent_to": "allocation_evaluator.py (elapsed fixed to zero)",
            "proven_optimal": proven,
            "termination": "optimal" if proven else termination,
            "complete_column_space_lp_bound": complete_lp_bound,
            "complete_column_space_lp_bound_certified": root_lp_certified,
            "complete_column_space_lp_gap_absolute": lp_gap_absolute,
            "complete_column_space_lp_gap_relative": lp_gap_relative,
            "safe_column_space_lp_bound": safe_root_lp_upper_bound,
            "safe_column_space_lp_bound_certified": (
                safe_root_lp_upper_bound is not None
            ),
            "safe_column_space_lp_bound_source": safe_bound_source,
            "safe_heuristic_gap_absolute": safe_root_gap_absolute,
            "safe_heuristic_gap_relative": safe_root_gap_relative,
            "safe_gap_target_percent": float(
                config.get("column_generation", {}).get(
                    "safe_gap_stop_percent", 2.0
                )
            ),
            "safe_gap_target_reached": safe_gap_target_reached,
            "safe_pricing_lower_bounds": {
                str(group_id): lower_bound
                for group_id, lower_bound in sorted(
                    safe_pricing_lower_bounds.items()
                )
            },
            "final_root_pricing_snapshot": (
                None
                if latest_root_duals is None
                else {
                    "restricted_master_cost": latest_root_lp_cost,
                    "duals": _serialize_master_duals(latest_root_duals),
                }
            ),
            "final_root_bound_sweep": final_root_bound_sweep,
            "integer_optimal_score": score if proven else None,
            "heuristic_warm_start": warm_start_diagnostics,
            "master_nodes": master_nodes,
            "pending_master_nodes": len(frontier) + len(dive_stack),
            "master_search": {
                "branching_variable": "passenger_placement_marginal",
                "node_order": (
                    "incumbent-guided DFS diving with periodic best-bound "
                    "re-anchoring"
                ),
                "placement_branches": placement_branches,
                "dive_nodes": dive_nodes,
                "dive_max_nodes_before_reanchor": dive_max_nodes,
                "max_depth": max_depth,
                "restricted_master_integer_heuristic": restricted_mip_stats,
                "final_integer_rounding_reserve_seconds": (
                    final_rounding_reserve
                ),
                "final_root_bound_reserve_seconds": (
                    final_root_bound_reserve
                ),
            },
            "column_generation_iterations": cg_iterations,
            "root_cg_elapsed_seconds": root_cg_elapsed_seconds,
            "root_cg_trajectory": root_cg_trajectory,
            "full_load_set_partitioning": {
                "eligible": full_load_partitioning_eligible,
                "enabled_in_phase_two": full_load_partitioning_eligible,
                "fixed_seat_resource_demand": fixed_seat_resource_demand,
                "available_seats": len(new_topology.seat_map),
                "legality_basis": (
                    "every group column has fixed passenger plus protected-seat "
                    "resource cardinality"
                ),
            },
            "generated_group_patterns": len(patterns),
            "fixed_seat_preprocessing": {
                "mandatory_assignments": len(
                    fixed_context.owner_by_seat
                ),
                "deterministic_blocked_seats": len(
                    fixed_context.deterministic_blocked
                ),
                "unassigned_option_removed": True,
                "global_reserved_domain_filter": True,
            },
            "mp1_dual_families_passed_to_mp2": [
                "group_selection", "seat_capacity", "conditional_ssr_flag",
                "conditional_ssr_all", "baby_lower", "baby_infant_upper",
                "baby_occupant_upper",
            ],
            "mp2_pricing_domain": "all feasible passenger-seat-and-empty-side placements",
            "mp2_integrality_method": (
                "DFS candidate discovery with complete-domain HiGHS MILP "
                "certification"
            ),
            "mp2_lower_bound_terms": [
                "individual_and_all_MP1_dual_costs",
                "row_span_max_min_linearization",
                "column_span_max_min_linearization",
                "missing_adjacency_linearization",
                "row_hole_linearization",
                "exact_same_group_baby_pair_linearization",
                "exact_caregiver_placement_linking",
            ],
            "mp2_branching_strategy": (
                "HiGHS native MIP branching on complete-domain placement variables"
            ),
            "mp2_safe_domain_reduction": (
                "fixed-seat global reservation, reachable-seat induced geometry, "
                "and forced-placement conflict propagation"
            ),
            "protected_empty_capacity_formulation": (
                "occupied placements plus every protected-empty demand <= 1 "
                "for each seat; protected demands cannot be shared"
            ),
            "large_group_dfs_pricing": {
                "enabled": bool(
                    config.get("column_generation", {}).get(
                        "dfs_pricing_enabled", True
                    )
                ),
                "minimum_group_size": int(
                    config.get("column_generation", {}).get(
                        "dfs_min_group_size", 3
                    )
                ),
                "node_limit": int(
                    config.get("column_generation", {}).get(
                        "dfs_node_limit", 2000000
                    )
                ),
                "discovery_time_limit": float(
                    config.get("column_generation", {}).get(
                        "dfs_discovery_time_limit", 1.0
                    )
                ),
                "exact_time_limit": float(
                    config.get("column_generation", {}).get(
                        "dfs_exact_time_limit", 15.0
                    )
                ),
                "certification_enabled": bool(
                    config.get("column_generation", {}).get(
                        "dfs_certification_enabled", False
                    )
                ),
                "certification_rule": (
                    "complete-domain HiGHS MILP certifies pricing; DFS "
                    "is candidate discovery unless explicitly enabled"
                ),
            },
            "mp2_model_reuse": (
                "cached complete placement/geometry templates and historical "
                "integer MIP starts; multiple negative patterns are collected "
                "by incremental no-good rows"
            ),
            "mp2_group_cache": {
                "groups": len(pricing_caches),
                "historical_mip_start_submissions": sum(
                    cache.mip_start_submissions
                    for cache in pricing_caches.values()
                ),
                "reused_items": [
                    "placement_domain",
                    "placement_ssr_and_infant_coefficients",
                    "reachable_geometry_and_sparse_relationships",
                    "historical_integer_placement",
                ],
            },
            "negative_column_early_stop": {
                "enabled_for_candidate_discovery": True,
                "objective_target_margin": float(
                    config.get("column_generation", {}).get(
                        "negative_pricing_target_margin", 1e-6
                    )
                ),
                "full_optimal_solve_required_for_certification": True,
            },
            "dual_smoothing": {
                "candidate_discovery_only": True,
                "alpha_current_dual": float(
                    config.get("column_generation", {}).get(
                        "dual_smoothing_alpha", 0.7
                    )
                ),
                "certification_uses_raw_mp1_duals": True,
            },
            "parallel_group_pricing": {
                "workers": limits["pricing_workers"],
                "highs_threads_per_worker": 1,
            },
            "mp1_lp_reuse": (
                "persistent RMP per branch node with incremental addCol and "
                "dual-simplex basis reuse"
            ),
            "mp1_pricing_schedule": (
                "promising-group quick multi-column pricing first; exact "
                "pricing of every still-uncertified group before LP certification"
            ),
            "mp1_integrality_method": (
                "branch-and-price on original passenger-placement decisions; "
                "every proof node receives exact complete-domain re-pricing"
            ),
            "equivalence_audit": {
                "integer_master_solutions_checked": equivalence_checks,
                "max_absolute_raw_score_error": max_equivalence_error,
                "passed": max_equivalence_error <= 1e-5,
            },
            "pricing": pricing_stats,
            "solver": {"name": "HiGHS", "version": highspy.Highs().version()},
            "elapsed_seconds": time.perf_counter() - started,
        },
    }
    _progress(
        config,
        f"finished: termination={'optimal' if proven else termination}, "
        f"proven_optimal={proven}, score={score:.6f}, "
        f"lp_bound={'none' if complete_lp_bound is None else f'{complete_lp_bound:.6f}'}, "
        f"elapsed={time.perf_counter() - started:.1f}s",
    )
    return result, score


def run_column_generation(
    new_seats_data: List[dict], old_seats_data: List[dict],
    groups_data: List[dict], weights: Dict[str, float],
    config: Dict[str, Any],
    frozen_warm_start_by_cabin: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
) -> Tuple[Dict[str, Any], float]:
    """按舱位独立运行列生成，并严格合并各舱整数解与LP上界。"""
    # Preserve the established validation order and error semantics before
    # attempting to classify groups by cabin.
    _validate_input_identity(groups_data)
    mixed = evaluator.mixed_cabin_group_ids(groups_data)
    if mixed:
        raise ValueError(f"同行组必须舱位同质，混舱组: {mixed}")
    groups_by_cabin: Dict[str, List[dict]] = defaultdict(list)
    for group in groups_data:
        cabin = evaluator.group_cabin_class(group)
        if cabin is None:
            raise ValueError(
                f"同行组缺少唯一有效舱位: groupId={group.get('groupId')}"
            )
        groups_by_cabin[cabin].append(group)
    if not groups_by_cabin:
        raise ValueError("没有可定价的同行组")

    column_cfg = config.get("column_generation", {})
    if not bool(column_cfg.get("cabin_decomposition_enabled", True)):
        only_cabin = next(iter(groups_by_cabin))
        return _run_column_generation_single_cabin(
            new_seats_data, old_seats_data, groups_data, weights, config,
            (frozen_warm_start_by_cabin or {}).get(only_cabin),
        )
    target_by_cabin = {
        cabin: [
            seat for seat in new_seats_data
            if seat.get("seatClass") == cabin
        ]
        for cabin in groups_by_cabin
    }
    old_by_cabin = {
        cabin: [
            seat for seat in old_seats_data
            if seat.get("seatClass") == cabin
        ]
        for cabin in groups_by_cabin
    }
    missing = [
        cabin for cabin, seats in target_by_cabin.items() if not seats
    ]
    if missing:
        raise ValueError(f"目标机型缺少旅客所需舱位: {missing}")
    if len(groups_by_cabin) == 1:
        only_cabin = next(iter(groups_by_cabin))
        return _run_column_generation_single_cabin(
            new_seats_data, old_seats_data, groups_data, weights, config,
            (frozen_warm_start_by_cabin or {}).get(only_cabin),
        )

    started = time.perf_counter()
    total_limit = max(
        0.1, float(column_cfg.get("total_time_limit", 60.0))
    )
    deadline = started + total_limit
    minimum = min(
        total_limit / len(groups_by_cabin),
        max(0.1, float(column_cfg.get("cabin_min_time_seconds", 5.0))),
    )
    cabins = sorted(
        groups_by_cabin,
        key=lambda cabin: (len(target_by_cabin[cabin]), cabin),
    )
    target_total = sum(len(target_by_cabin[cabin]) for cabin in cabins)
    merged_assignments: Dict[PassengerKey, str] = {}
    merged_blocked: Dict[str, Any] = {}
    cabin_runs: Dict[str, Dict[str, Any]] = {}

    for index, cabin in enumerate(cabins):
        remaining = max(0.1, deadline - time.perf_counter())
        future_count = len(cabins) - index - 1
        if future_count == 0:
            cabin_limit = remaining
        else:
            proportional = total_limit * (
                len(target_by_cabin[cabin]) / max(1, target_total)
            )
            cabin_limit = min(
                max(minimum, proportional),
                max(0.1, remaining - future_count * minimum),
            )
        cabin_config = copy.deepcopy(config)
        local_column = cabin_config.setdefault("column_generation", {})
        local_column["total_time_limit"] = cabin_limit
        local_column["mp2_time_limit"] = min(
            float(local_column.get("mp2_time_limit", cabin_limit)),
            cabin_limit,
        )
        local_column["restricted_mip_time_limit"] = min(
            float(local_column.get("restricted_mip_time_limit", 10.0)),
            cabin_limit,
        )
        local_algorithm = cabin_config.setdefault("algorithm", {})
        local_algorithm["business_time_limit_seconds"] = min(
            float(local_algorithm.get("business_time_limit_seconds", 5.0)),
            max(0.1, cabin_limit * 0.25),
        )
        cabin_started = time.perf_counter()
        cabin_result, _ = _run_column_generation_single_cabin(
            target_by_cabin[cabin],
            old_by_cabin[cabin],
            groups_by_cabin[cabin],
            weights,
            cabin_config,
            (frozen_warm_start_by_cabin or {}).get(cabin),
        )
        elapsed = time.perf_counter() - cabin_started
        merged_assignments.update(cabin_result["assigned_seats"])
        merged_blocked.update(cabin_result.get("blocked_by", {}))
        cabin_runs[cabin] = {
            "time_budget_seconds": cabin_limit,
            "elapsed_seconds": elapsed,
            "traveler_count": sum(
                len(group.get("psrs", []))
                for group in groups_by_cabin[cabin]
            ),
            "target_seats": len(target_by_cabin[cabin]),
            "result_score": cabin_result["score_detail"][
                "total_soft_score"
            ],
            "diagnostics": cabin_result.get("diagnostics", {}),
        }

    elapsed = time.perf_counter() - started
    violations, unassigned, violation_detail = (
        evaluator.count_hard_constraint_violations(
            new_seats_data, groups_data, merged_assignments, config
        )
    )
    soft, soft_detail = evaluator.calculate_soft_score(
        new_seats_data,
        old_seats_data,
        groups_data,
        merged_assignments,
        weights,
        config,
    )
    if violations or unassigned:
        raise RuntimeError(
            "分舱列生成合并结果不合法: "
            f"violations={violations}, unassigned={unassigned}, "
            f"detail={violation_detail}"
        )
    score, _ = evaluator.calculate_combined_score(
        soft, unassigned, violations, 0.0, config
    )
    diagnostics_by_cabin = {
        cabin: run["diagnostics"] for cabin, run in cabin_runs.items()
    }
    exact_certified = all(
        bool(diag.get("complete_column_space_lp_bound_certified", False))
        and diag.get("complete_column_space_lp_bound") is not None
        for diag in diagnostics_by_cabin.values()
    )
    exact_bound = (
        sum(
            float(diag["complete_column_space_lp_bound"])
            for diag in diagnostics_by_cabin.values()
        )
        if exact_certified else None
    )
    per_cabin_safe: Dict[str, float] = {}
    for cabin, diag in diagnostics_by_cabin.items():
        if (
            diag.get("complete_column_space_lp_bound_certified", False)
            and diag.get("complete_column_space_lp_bound") is not None
        ):
            per_cabin_safe[cabin] = float(
                diag["complete_column_space_lp_bound"]
            )
        elif (
            diag.get("safe_column_space_lp_bound_certified", False)
            and diag.get("safe_column_space_lp_bound") is not None
        ):
            per_cabin_safe[cabin] = float(
                diag["safe_column_space_lp_bound"]
            )
    safe_certified = len(per_cabin_safe) == len(cabin_runs)
    safe_bound = sum(per_cabin_safe.values()) if safe_certified else None
    effective_bound = exact_bound if exact_certified else safe_bound
    gap = None if effective_bound is None else effective_bound - score
    safe_gap_target_percent = float(
        column_cfg.get("safe_gap_stop_percent", 2.0)
    )
    safe_gap_target_reached = bool(
        not exact_certified
        and safe_certified
        and gap is not None
        and 100.0 * gap / max(1.0, abs(score))
        <= safe_gap_target_percent + 1e-12
    )
    pricing = {
        "calls": sum(
            int(diag.get("pricing", {}).get("calls", 0))
            for diag in diagnostics_by_cabin.values()
        ),
        "exact_calls": sum(
            int(diag.get("pricing", {}).get("exact_calls", 0))
            for diag in diagnostics_by_cabin.values()
        ),
        "repeated_exact_group_calls": sum(
            int(diag.get("pricing", {}).get("repeated_exact_group_calls", 0))
            for diag in diagnostics_by_cabin.values()
        ),
        "by_cabin": {
            cabin: diag.get("pricing", {})
            for cabin, diag in diagnostics_by_cabin.items()
        },
        "group_stats": {
            f"{cabin}:{group_id}": values
            for cabin, diag in diagnostics_by_cabin.items()
            for group_id, values in diag.get("pricing", {}).get(
                "group_stats", {}
            ).items()
        },
        "adaptive_dual_stabilization": {
            "per_cabin": {
                cabin: diag.get("pricing", {}).get(
                    "adaptive_dual_stabilization", {}
                )
                for cabin, diag in diagnostics_by_cabin.items()
            }
        },
    }
    warm_by_cabin = {
        cabin: diag.get("heuristic_warm_start", {})
        for cabin, diag in diagnostics_by_cabin.items()
    }
    warm_accepted = all(
        bool(warm.get("accepted", False))
        for warm in warm_by_cabin.values()
    )
    warm_scores = [
        warm.get("score_without_time_penalty")
        for warm in warm_by_cabin.values()
    ]
    merged_warm_start = {
        "accepted": warm_accepted,
        "score_without_time_penalty": (
            sum(float(value) for value in warm_scores)
            if warm_accepted and all(value is not None for value in warm_scores)
            else None
        ),
        "elapsed_seconds": sum(
            float(warm.get("elapsed_seconds", 0.0) or 0.0)
            for warm in warm_by_cabin.values()
        ),
        "frozen_input": all(
            bool(warm.get("frozen_input", False))
            for warm in warm_by_cabin.values()
        ),
        "frozen_identity_hashes": {
            cabin: warm.get("frozen_identity_hash")
            for cabin, warm in warm_by_cabin.items()
        },
        "rejection_reason": (
            None if warm_accepted else {
                cabin: warm.get("rejection_reason")
                for cabin, warm in warm_by_cabin.items()
                if not warm.get("accepted", False)
            }
        ),
        "per_cabin": warm_by_cabin,
    }
    result = {
        "assigned_seats": merged_assignments,
        "occupied": sorted(set(merged_assignments.values())),
        "blocked_by": merged_blocked,
        "score_detail": {
            **soft_detail,
            "unassigned_count": unassigned,
            "violations": violations,
            "violation_detail": violation_detail,
            "time_penalty": 0.0,
            "combined_score": score,
        },
        "total_score": score,
        "diagnostics": {
            "method": "independent_cabin_column_generation",
            "objective_equivalent_to": (
                "sum of independent cabin LPs; allocation_evaluator.py"
            ),
            "proven_optimal": all(
                bool(diag.get("proven_optimal", False))
                for diag in diagnostics_by_cabin.values()
            ),
            "termination": (
                "optimal" if all(
                    diag.get("termination") == "optimal"
                    for diag in diagnostics_by_cabin.values()
                ) else "cabin_subproblem_limit"
            ),
            "complete_column_space_lp_bound": exact_bound,
            "complete_column_space_lp_bound_certified": exact_certified,
            "complete_column_space_lp_gap_absolute": (
                None if exact_bound is None else exact_bound - score
            ),
            "complete_column_space_lp_gap_relative": (
                None if exact_bound is None
                else (exact_bound - score) / max(1.0, abs(score))
            ),
            "safe_column_space_lp_bound": safe_bound,
            "safe_column_space_lp_bound_certified": safe_certified,
            "safe_column_space_lp_bound_source": (
                "sum_of_per_cabin_exact_or_safe_bounds"
                if safe_certified else None
            ),
            "safe_heuristic_gap_absolute": gap,
            "safe_heuristic_gap_relative": (
                None if gap is None else gap / max(1.0, abs(score))
            ),
            "safe_gap_target_percent": safe_gap_target_percent,
            "safe_gap_target_reached": safe_gap_target_reached,
            "integer_optimal_score": (
                score if all(
                    bool(diag.get("proven_optimal", False))
                    for diag in diagnostics_by_cabin.values()
                ) else None
            ),
            "master_nodes": sum(
                int(diag.get("master_nodes", 0))
                for diag in diagnostics_by_cabin.values()
            ),
            "column_generation_iterations": sum(
                int(diag.get("column_generation_iterations", 0))
                for diag in diagnostics_by_cabin.values()
            ),
            "root_cg_elapsed_seconds": sum(
                float(diag.get("root_cg_elapsed_seconds", 0.0))
                for diag in diagnostics_by_cabin.values()
            ),
            "root_cg_trajectory_by_cabin": {
                cabin: diag.get("root_cg_trajectory", [])
                for cabin, diag in diagnostics_by_cabin.items()
            },
            "final_root_pricing_snapshot_by_cabin": {
                cabin: diag.get("final_root_pricing_snapshot")
                for cabin, diag in diagnostics_by_cabin.items()
            },
            "generated_group_patterns": sum(
                int(diag.get("generated_group_patterns", 0))
                for diag in diagnostics_by_cabin.values()
            ),
            "pricing": pricing,
            "cabin_decomposition": {
                "enabled": True,
                "solve_order": cabins,
                "total_time_limit_seconds": total_limit,
                "economy_receives_remaining_time": True,
                "cabins": cabin_runs,
            },
            "full_load_set_partitioning": {
                "per_cabin": {
                    cabin: diag.get("full_load_set_partitioning", {})
                    for cabin, diag in diagnostics_by_cabin.items()
                }
            },
            "final_root_bound_sweep": {
                "per_cabin": {
                    cabin: diag.get("final_root_bound_sweep", {})
                    for cabin, diag in diagnostics_by_cabin.items()
                }
            },
            "heuristic_warm_start": merged_warm_start,
            "master_search": {
                "restricted_master_integer_heuristic": {
                    "per_cabin": {
                        cabin: diag.get("master_search", {}).get(
                            "restricted_master_integer_heuristic", {}
                        )
                        for cabin, diag in diagnostics_by_cabin.items()
                    }
                },
                "per_cabin": {
                    cabin: diag.get("master_search", {})
                    for cabin, diag in diagnostics_by_cabin.items()
                }
            },
            "equivalence_audit": {
                "passed": all(
                    bool(diag.get("equivalence_audit", {}).get("passed", False))
                    for diag in diagnostics_by_cabin.values()
                ),
                "per_cabin": {
                    cabin: diag.get("equivalence_audit", {})
                    for cabin, diag in diagnostics_by_cabin.items()
                },
            },
            "elapsed_seconds": elapsed,
        },
    }
    return result, score


def main() -> None:
    """读取配置数据，运行精确列生成并可选写出完整JSON结果。"""
    parser = argparse.ArgumentParser(
        description="运行与 allocation_evaluator 等价的混合精确列生成与branch-and-price"
    )
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.json")
    parser.add_argument(
        "--groups",
        type=Path,
        help="覆盖 config.data.groups，便于复用同一配置分析指定算例",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mp2-time-limit", type=float, help="覆盖单次MP2定价时限（秒）")
    parser.add_argument("--mp2-node-limit", type=int, help="覆盖单次MP2节点上限")
    parser.add_argument("--mp1-node-limit", type=int, help="覆盖MP1分支节点上限")
    parser.add_argument("--total-time-limit", type=float, help="覆盖总求解时限（秒）")
    parser.add_argument(
        "--mp1-dive-max-nodes", type=int,
        help="沿incumbent方向连续DFS节点数；0表示纯best-bound",
    )
    parser.add_argument(
        "--restricted-mip-frequency", type=int,
        help="每隔多少个MP1节点运行一次整数RMP启发式；0表示仅根节点",
    )
    parser.add_argument(
        "--restricted-mip-time-limit", type=float,
        help="单次节点整数RMP启发式时限（秒）；0表示关闭",
    )
    parser.add_argument(
        "--pricing-workers",
        type=int,
        help="并行同行组定价工作线程数；每个HiGHS实例固定使用一个线程",
    )
    parser.add_argument("--quiet", action="store_true", help="关闭进度提示，仅打印最终摘要")
    parser.add_argument(
        "--verbose-pricing-progress", action="store_true",
        help="输出逐同行组MP2提交、完成和心跳详情",
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    limit_overrides = {
        "mp2_time_limit": args.mp2_time_limit,
        "mp2_node_limit": args.mp2_node_limit,
        "mp1_node_limit": args.mp1_node_limit,
        "total_time_limit": args.total_time_limit,
        "mp1_dive_max_nodes": args.mp1_dive_max_nodes,
        "restricted_mip_frequency": args.restricted_mip_frequency,
        "restricted_mip_time_limit": args.restricted_mip_time_limit,
        "pricing_workers": args.pricing_workers,
    }
    limit_config = config.setdefault("column_generation", {})
    for key, value in limit_overrides.items():
        if value is not None:
            limit_config[key] = value
    if args.quiet:
        limit_config["show_progress"] = False
    if args.verbose_pricing_progress:
        limit_config["verbose_pricing_progress"] = True
    data = config["data"]
    if args.groups is not None:
        groups_path = args.groups.resolve()
    else:
        groups_path = PROJECT_ROOT / data["groups"]
    new = json.loads((PROJECT_ROOT / data["seatmap"]).read_text(encoding="utf-8"))["seats"]
    old = json.loads((PROJECT_ROOT / data["oldseatmap"]).read_text(encoding="utf-8"))["seats"]
    groups = json.loads(groups_path.read_text(encoding="utf-8"))["groups"]
    result, _ = run_column_generation(
        new, old, groups, config.get("weights", {}), config
    )
    print(json.dumps(_json_safe(result["diagnostics"]), ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(_json_safe(result), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
