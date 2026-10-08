"""问题实例：把原始 JSON 输入翻译成数学模型中的集合、参数与可行域。

本模块只做“建模信息”的整理，不含任何搜索逻辑。所有业务语义均以
``allocation_evaluator`` 为准：座位拓扑、单个旅客的软分、同行组紧凑度、
婴儿影响、SSR 规则与陪护者定义都直接调用评估器函数，保证列成本与
独立评估器逐项一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src import allocation_evaluator as evaluator

PassengerKey = Tuple[int, int]
Location = Tuple[str, str, Any]  # ("row"|"subrow", cabin, row 或 (row, subrow))


@dataclass
class Passenger:
    key: PassengerKey
    gid: int
    hostnum: int
    cabin: str
    ssr: str
    rule: Dict[str, Any]
    fixed_seat: Optional[str]
    both_side: bool
    single_side: bool
    needs_caregiver: bool
    caregiver_cross_aisle: bool
    is_caregiver: bool
    row_flag: bool
    subrow_flag: bool
    is_infant: bool
    raw: dict
    domain: List[str] = field(default_factory=list)
    # 旅客坐到座位 s 的个人软分 h_ps（距离 + 价值 + 属性偏好）
    score: Dict[str, float] = field(default_factory=dict)


@dataclass
class Group:
    gid: int
    cabin: str
    passengers: List[Passenger]

    @property
    def size(self) -> int:
        return len(self.passengers)

    @property
    def caregivers(self) -> List[Passenger]:
        return [p for p in self.passengers if p.is_caregiver]


class Instance:
    """单舱位（或整机）列生成实例。"""

    def __init__(
        self,
        new_seats: Sequence[dict],
        old_seats: Sequence[dict],
        groups_data: Sequence[dict],
        weights: Mapping[str, float],
        config: Mapping[str, Any],
    ) -> None:
        self.config = dict(config)
        self.weights = dict(weights)
        self.new_seats = list(new_seats)
        self.old_seats = list(old_seats)
        self.groups_data = list(groups_data)
        self.topology = evaluator.SeatTopology(self.new_seats, self.config)
        self.old_topology = evaluator.SeatTopology(self.old_seats, self.config)
        self.seat_ids: List[str] = [s["seatId"] for s in self.new_seats]
        self.seat_map = self.topology.seat_map
        self.cabin_of_seat: Dict[str, str] = {
            sid: str(seat.get("seatClass", "")) for sid, seat in self.seat_map.items()
        }
        mandatory_cfg = self.config.get("mandatory_rules", {})
        self.both_cross_aisle = bool(
            mandatory_cfg.get("both_side_empty_allow_cross_aisle", False)
        )
        self.require_two_neighbors = bool(
            mandatory_cfg.get("require_two_real_neighbors", True)
        )
        algorithm = self.config.get("algorithm", {})
        self.compact_x_factor = float(algorithm.get("group_centroid_x_factor", 1.0))
        self.compact_y_factor = float(algorithm.get("group_centroid_y_factor", 1.0))
        self.w_c = float(self.weights.get("w_c", -2.0))
        if self.w_c > 0:
            raise ValueError("紧凑度权重 w_c 必须非正，线性化才是精确的")
        if float(self.weights.get("w_b", -0.3)) > 0:
            raise ValueError("婴儿影响权重 w_b 必须非正，单侧线性化 b_uv >= I_u + O_v - 1 才是精确的")

        self.groups: List[Group] = []
        self.passengers: Dict[PassengerKey, Passenger] = {}
        self._build_groups()
        self._check_cabin_capacity()
        self._build_domains()
        self._build_ssr_locations()
        self._build_baby_pairs()

    # ------------------------------------------------------------------ 数据
    def _build_groups(self) -> None:
        mixed = evaluator.mixed_cabin_group_ids(self.groups_data)
        if mixed:
            raise ValueError(f"同行组必须舱位同质，混舱组: {mixed}")
        seen: Set[PassengerKey] = set()
        seen_gid: Set[int] = set()
        for group in self.groups_data:
            gid_raw = group.get("groupId")
            if isinstance(gid_raw, bool) or not isinstance(gid_raw, int):
                raise ValueError(f"groupId 必须是整数（评估器按原始键匹配）: {gid_raw!r}")
            gid = gid_raw
            if gid in seen_gid:
                raise ValueError(f"重复的 groupId: {gid}")
            seen_gid.add(gid)
            cabin = evaluator.group_cabin_class(group)
            if cabin is None:
                raise ValueError(f"同行组缺少唯一有效舱位: groupId={gid}")
            passengers: List[Passenger] = []
            for raw in group.get("psrs", []):
                host_raw = raw.get("hostnum")
                if isinstance(host_raw, bool) or not isinstance(host_raw, int):
                    raise ValueError(f"hostnum 必须是整数（评估器按原始键匹配）: groupId={gid}, hostnum={host_raw!r}")
                if not isinstance(raw.get("oldSeat", {}) or {}, dict):
                    raise ValueError(f"oldSeat 必须是对象: groupId={gid}, hostnum={host_raw}")
                key = (gid, host_raw)
                if key in seen:
                    raise ValueError(f"重复的 (groupId, hostnum): {key}")
                seen.add(key)
                ssr = str(raw.get("ssr") or "")
                rule = evaluator._ssr_rule(ssr, self.config)
                mandatory = raw.get("mandatoryRule", {}) or {}
                strict_care = raw.get("needCared", "N") == "Y"
                needs_caregiver = strict_care or bool(rule.get("requires_caregiver", False))
                cross = bool(rule.get("caregiver_allow_cross_aisle", False))
                if strict_care and not ssr:
                    cross = False
                fixed = (raw.get("newSeat") or {}).get("seatNum") or None
                passengers.append(Passenger(
                    key=key, gid=gid, hostnum=host_raw, cabin=cabin, ssr=ssr,
                    rule=rule, fixed_seat=fixed,
                    both_side=mandatory.get("needBothSideEmpty", "N") == "Y",
                    single_side=mandatory.get("needSingleSideEmpty", "N") == "Y",
                    needs_caregiver=needs_caregiver, caregiver_cross_aisle=cross,
                    is_caregiver=evaluator._is_adult_caregiver(raw),
                    row_flag=bool(ssr) and mandatory.get("sameRowNoOtherSSR", "N") == "Y",
                    subrow_flag=bool(ssr) and mandatory.get("sameSubRowNoOtherSSR", "N") == "Y",
                    is_infant=(ssr == "BSCT"),
                    raw=raw,
                ))
                self.passengers[key] = passengers[-1]
            self.groups.append(Group(gid=gid, cabin=cabin, passengers=passengers))
        self.group_by_id: Dict[int, Group] = {g.gid: g for g in self.groups}

    def _check_cabin_capacity(self) -> None:
        """第 4.10 节：每个舱位的真实旅客 + 固定保护空座需求不得超过该舱目标座位数。"""
        demand: Dict[str, int] = {}
        for p in self.passengers.values():
            both = 2 if (p.both_side and self.require_two_neighbors) else 0
            single = 1 if (p.single_side and not p.both_side) else 0
            demand[p.cabin] = demand.get(p.cabin, 0) + 1 + both + single
        capacity: Dict[str, int] = {}
        for c in self.cabin_of_seat.values():
            capacity[c] = capacity.get(c, 0) + 1
        for cabin, need in demand.items():
            if need > capacity.get(cabin, 0):
                raise ValueError(
                    f"舱位 {cabin} 的资源需求 {need} 超过目标机型容量 {capacity.get(cabin, 0)}"
                )

    def _seat_ok_for(self, p: Passenger, seat: dict) -> bool:
        """第 4.3–4.5 节：舱位、固定座位、SSR 座位属性。"""
        sid = seat["seatId"]
        if str(seat.get("seatClass", "")) != p.cabin:
            return False
        if p.fixed_seat and sid != p.fixed_seat:
            return False
        if seat.get("isExitRow", False) and not p.rule.get("allow_exit_row", False):
            return False
        if p.rule.get("requires_bassinet", False) and not seat.get("hasBassinet", False):
            return False
        if p.rule.get("requires_aisle", False) and not seat.get("isAisle", False):
            return False
        if p.both_side and self.require_two_neighbors:
            if len(self.both_side_neighbors(sid)) != 2:
                return False
        return True

    def _build_domains(self) -> None:
        for p in self.passengers.values():
            domain = [
                sid for sid, seat in self.seat_map.items() if self._seat_ok_for(p, seat)
            ]
            if p.fixed_seat:
                if p.fixed_seat not in self.seat_map:
                    raise ValueError(f"固定座位不存在: passenger={p.key}, seat={p.fixed_seat}")
                if not domain:
                    raise ValueError(
                        f"固定座位不满足舱位/SSR/保护规则: passenger={p.key}, seat={p.fixed_seat}"
                    )
            p.domain = domain
            for sid in domain:
                comp = evaluator.individual_assignment_components(
                    p.raw, self.old_topology, self.topology, sid, self.weights, self.config
                )
                p.score[sid] = comp["distance"] + comp["value"] + comp["preference"]
        # 固定座位互斥，且不得落在另一名固定双侧保护旅客的确定性保护空座上（§4.4）
        owners: Dict[str, PassengerKey] = {}
        for p in self.passengers.values():
            if p.fixed_seat:
                if p.fixed_seat in owners:
                    raise ValueError(
                        f"两名旅客固定到同一座位: {owners[p.fixed_seat]} 与 {p.key} -> {p.fixed_seat}"
                    )
                owners[p.fixed_seat] = p.key
        for p in self.passengers.values():
            if p.fixed_seat and p.both_side:
                for t in self.both_side_neighbors(p.fixed_seat):
                    if t in owners:
                        raise ValueError(
                            f"固定旅客 {p.key} 的双侧保护空座 {t} 被另一名固定旅客 {owners[t]} 占用"
                        )

    # ------------------------------------------------------------------ 拓扑
    def both_side_neighbors(self, sid: str) -> List[str]:
        """双侧保护空座候选 N(s)。"""
        return self.topology.row_neighbors(sid, allow_cross_aisle=self.both_cross_aisle)

    def single_side_neighbors(self, sid: str) -> List[str]:
        """单侧保护空座候选 N^0(s)（不跨过道）。"""
        return self.topology.subrow_neighbors(sid)

    def caregiver_neighbors(self, p: Passenger, sid: str) -> List[str]:
        return self.topology.row_neighbors(sid, allow_cross_aisle=p.caregiver_cross_aisle)

    def row_location(self, sid: str) -> Location:
        seat = self.seat_map[sid]
        return ("row", str(seat.get("seatClass", "")), int(seat["row"]))

    def subrow_location(self, sid: str) -> Location:
        seat = self.seat_map[sid]
        return ("subrow", str(seat.get("seatClass", "")), self.topology.subrow[sid])

    def x(self, sid: str) -> float:
        return self.topology.x[sid]

    def y(self, sid: str) -> float:
        return self.topology.y[sid]

    # ------------------------------------------------------------- 条件 SSR
    def _build_ssr_locations(self) -> None:
        """第 4.9 节：条件隔离的位置资源与每种 SSR 类型的总人数 M。"""
        self.ssr_types: List[str] = sorted({p.ssr for p in self.passengers.values() if p.ssr})
        self.ssr_count_by_cabin: Dict[Tuple[str, str], int] = {}
        for p in self.passengers.values():
            if p.ssr:
                k = (p.cabin, p.ssr)
                self.ssr_count_by_cabin[k] = self.ssr_count_by_cabin.get(k, 0) + 1
        # 可能被激活的位置：任一带标志旅客可到达的排 / 子排
        self.flag_locations: Set[Location] = set()
        for p in self.passengers.values():
            for sid in p.domain:
                if p.row_flag:
                    self.flag_locations.add(self.row_location(sid))
                if p.subrow_flag:
                    self.flag_locations.add(self.subrow_location(sid))
        self.flag_groups: Set[int] = {
            p.gid for p in self.passengers.values() if p.row_flag or p.subrow_flag
        }

    def location_of(self, sid: str, kind: str) -> Location:
        return self.row_location(sid) if kind == "row" else self.subrow_location(sid)

    def big_m(self, loc: Location, ssr: str) -> int:
        return self.ssr_count_by_cabin.get((loc[1], ssr), 0)

    # ---------------------------------------------------------------- 婴儿
    def _build_baby_pairs(self) -> None:
        """第 5.5 节：婴儿座位 u 对其他组占座 v 的影响系数 B(u,v)。"""
        infant_seats: Set[str] = set()
        for p in self.passengers.values():
            if p.is_infant:
                infant_seats.update(p.domain)
        self.baby_pairs: Dict[Tuple[str, str], float] = {}
        if not infant_seats:
            return
        for u in infant_seats:
            for v in self.seat_ids:
                if v == u:
                    continue
                score = evaluator.baby_interference_score(
                    u, v, self.topology, self.weights, self.config
                )
                if abs(score) > 1e-12:
                    self.baby_pairs[(u, v)] = float(score)
        self.baby_pairs_by_infant_seat: Dict[str, List[Tuple[str, str]]] = {}
        self.baby_pairs_by_other_seat: Dict[str, List[Tuple[str, str]]] = {}
        for (u, v) in self.baby_pairs:
            self.baby_pairs_by_infant_seat.setdefault(u, []).append((u, v))
            self.baby_pairs_by_other_seat.setdefault(v, []).append((u, v))

    # ------------------------------------------------------------ 独立评分
    def compactness_score(self, seat_ids: Sequence[str]) -> float:
        """同行组紧凑度软分 F_c 的该组部分（已乘权重）。"""
        return self.w_c * evaluator.group_compactness_penalty(
            list(seat_ids), self.topology, self.config
        )

    def column_score(self, group: Group, assignment: Mapping[PassengerKey, str]) -> float:
        """一个同行组完整方案的局部软分：个人项 + 紧凑度（不含跨组婴儿项）。"""
        total = sum(group_p.score[assignment[group_p.key]] for group_p in group.passengers)
        total += self.compactness_score([assignment[p.key] for p in group.passengers])
        return total
