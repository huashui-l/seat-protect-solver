import importlib.util
import json
import multiprocessing as mp
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

DEFAULT_SSR_RULE = {
    "allow_exit_row": False,
    "requires_caregiver": False,
    "caregiver_allow_cross_aisle": False,
    "requires_aisle": False,
    "requires_bassinet": False,
}
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


def expected_cabin_class(passenger: Mapping[str, Any]) -> Optional[str]:
    """Return the immutable target seat class declared by the booking."""
    raw = str(passenger.get("cabin", "")).strip()
    if not raw:
        return None
    return CABIN_CLASS_MAP.get(raw.upper(), raw)


def group_cabin_class(group: Mapping[str, Any]) -> Optional[str]:
    """Return a group's unique booking cabin, or ``None`` when mixed/empty."""
    classes = {
        expected_cabin_class(passenger)
        for passenger in group.get("psrs", [])
    }
    classes.discard(None)
    return next(iter(classes)) if len(classes) == 1 else None


def mixed_cabin_group_ids(groups: Sequence[Mapping[str, Any]]) -> List[int]:
    """Return non-empty groups whose passengers declare multiple cabins."""
    mixed: List[int] = []
    for group in groups:
        classes = {
            expected_cabin_class(passenger)
            for passenger in group.get("psrs", [])
        }
        classes.discard(None)
        if len(classes) > 1:
            mixed.append(int(group.get("groupId", -1)))
    return mixed


def _ssr_rule(ssr: str, config: dict) -> dict:
    rule = dict(DEFAULT_SSR_RULE)
    if not ssr:
        rule["allow_exit_row"] = True
        return rule
    rule.update(config.get("ssr_rules", {}).get(ssr, {}))
    return rule


def _seat_col_key(seat: dict) -> int:
    col = str(seat.get("col", ""))
    return ord(col[0]) if col else 10**9


class SeatTopology:
    """根据真实座位表建立排、子排、相邻关系及物理坐标。"""

    def __init__(self, seats_data: List[dict], config: Optional[dict] = None):
        config = config or {}
        geometry = config.get("seat_geometry", {})
        self.seat_spacing = float(geometry.get("seat_spacing", 1.0))
        self.aisle_gap = float(geometry.get("aisle_gap", 0.8))
        self.row_spacing = float(geometry.get("row_spacing", 1.5))

        self.seat_map: Dict[str, dict] = {s["seatId"]: s for s in seats_data}
        self.row_seats: Dict[int, List[dict]] = defaultdict(list)
        for seat in seats_data:
            self.row_seats[int(seat["row"])].append(seat)
        for row in self.row_seats:
            self.row_seats[row].sort(key=_seat_col_key)

        self.subrow: Dict[str, Tuple[int, int]] = {}
        self.index_in_row: Dict[str, int] = {}
        self.x: Dict[str, float] = {}
        self.y: Dict[str, float] = {}

        for row, seats in self.row_seats.items():
            breaks: Set[int] = set()
            break_index = 0
            while break_index < len(seats) - 1:
                if seats[break_index].get("isAisle", False) and seats[break_index + 1].get("isAisle", False):
                    breaks.add(break_index)
                    break_index += 2
                else:
                    break_index += 1
            subrow_id = 0
            raw_x: List[float] = []
            current_x = 0.0
            for i, seat in enumerate(seats):
                seat_id = seat["seatId"]
                self.subrow[seat_id] = (row, subrow_id)
                self.index_in_row[seat_id] = i
                raw_x.append(current_x)
                if i < len(seats) - 1:
                    step = self.seat_spacing
                    if i in breaks:
                        step += self.aisle_gap
                        subrow_id += 1
                    current_x += step

            center = (raw_x[0] + raw_x[-1]) / 2.0 if raw_x else 0.0
            for seat, xpos in zip(seats, raw_x):
                seat_id = seat["seatId"]
                self.x[seat_id] = xpos - center
                self.y[seat_id] = row * self.row_spacing

    def same_subrow(self, seat_a: str, seat_b: str) -> bool:
        return self.subrow.get(seat_a) == self.subrow.get(seat_b)

    def row_neighbors(self, seat_id: str, allow_cross_aisle: bool = True) -> List[str]:
        seat = self.seat_map.get(seat_id)
        if not seat:
            return []
        row = int(seat["row"])
        seats = self.row_seats[row]
        idx = self.index_in_row[seat_id]
        result: List[str] = []
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(seats):
                other_id = seats[j]["seatId"]
                if allow_cross_aisle or self.same_subrow(seat_id, other_id):
                    result.append(other_id)
        return result

    def subrow_neighbors(self, seat_id: str) -> List[str]:
        return self.row_neighbors(seat_id, allow_cross_aisle=False)

    def distance(self, seat_a: str, seat_b: str) -> Tuple[float, float]:
        return (
            abs(self.x[seat_a] - self.x[seat_b]),
            abs(self.y[seat_a] - self.y[seat_b]),
        )


def _passenger_index(groups_data: List[dict]) -> Dict[Tuple[int, int], dict]:
    index: Dict[Tuple[int, int], dict] = {}
    for group in groups_data:
        gid = group["groupId"]
        for passenger in group.get("psrs", []):
            index[(gid, passenger["hostnum"])] = passenger
    return index


def _is_adult_caregiver(passenger: dict) -> bool:
    """输入没有年龄字段时，以“无 SSR 且自身不需要照顾”作为成人代理定义。"""
    mandatory = passenger.get("mandatoryRule", {}) or {}
    return (
        not passenger.get("ssr")
        and passenger.get("needCared", "N") != "Y"
        and mandatory.get("needBothSideEmpty", "N") != "Y"
        and mandatory.get("needSingleSideEmpty", "N") != "Y"
    )


def count_hard_constraint_violations(
    seats_data: List[dict],
    groups_data: List[dict],
    assigned_seats: Dict[Tuple[int, int], str],
    config: Optional[dict] = None,
) -> Tuple[int, int, Dict[str, Any]]:
    config = config or {}
    topology = SeatTopology(seats_data, config)
    passenger_map = _passenger_index(groups_data)
    total_passengers = len(passenger_map)

    detail = {
        "invalid_output_key": 0,
        "invalid_passenger": 0,
        "invalid_seat": 0,
        "duplicate_seats": 0,
        "reserved_mismatch": 0,
        "reserved_unassigned": 0,
        "ssr_exit_violation": 0,
        "ssr_bassinet_requirement_violation": 0,
        "ssr_aisle_requirement_violation": 0,
        "cabin_mismatch_violation": 0,
        "mixed_cabin_group_violation": 0,
        "caregiver_missing": 0,
        "need_both_empty_violation": 0,
        "need_single_empty_violation": 0,
        "empty_resource_shared": 0,
        "same_subrow_ssr_conflict": 0,
        "same_row_ssr_conflict": 0,
    }

    violations = 0
    mixed_group_ids = mixed_cabin_group_ids(groups_data)
    violations += len(mixed_group_ids)
    detail["mixed_cabin_group_violation"] = len(mixed_group_ids)
    detail["mixed_cabin_group_ids"] = mixed_group_ids
    valid_assignments: Dict[Tuple[int, int], str] = {}
    seat_owner: Dict[str, Tuple[int, int]] = {}

    if not isinstance(assigned_seats, dict):
        return 1, total_passengers, {**detail, "invalid_output_key": 1}

    for raw_key, seat_id in assigned_seats.items():
        if not (
            isinstance(raw_key, tuple)
            and len(raw_key) == 2
            and isinstance(seat_id, str)
        ):
            violations += 1
            detail["invalid_output_key"] += 1
            continue

        key = (raw_key[0], raw_key[1])
        passenger = passenger_map.get(key)
        if passenger is None:
            violations += 1
            detail["invalid_passenger"] += 1
            continue
        if seat_id not in topology.seat_map:
            violations += 1
            detail["invalid_seat"] += 1
            continue
        if key in valid_assignments:
            violations += 1
            detail["invalid_output_key"] += 1
            continue
        if seat_id in seat_owner:
            violations += 1
            detail["duplicate_seats"] += 1
            continue

        valid_assignments[key] = seat_id
        seat_owner[seat_id] = key

    occupied_set = set(seat_owner)

    reserved_seats: Dict[Tuple[int, int], str] = {}
    for key, passenger in passenger_map.items():
        new_seat = passenger.get("newSeat") or {}
        if new_seat.get("seatNum"):
            reserved_seats[key] = new_seat["seatNum"]

    # newSeat is an immutable assignment rather than a preference.  A fixed
    # passenger omitted from the output therefore violates a hard constraint.
    for key in reserved_seats:
        if key not in valid_assignments:
            violations += 1
            detail["reserved_unassigned"] += 1

    subrow_ssr_count: Dict[Tuple[str, Any], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    row_ssr_count: Dict[Tuple[str, int], Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    protected_empty_seats: Set[str] = set()
    single_empty_candidates: List[Tuple[Tuple[int, int], Tuple[str, ...]]] = []
    for key, seat_id in valid_assignments.items():
        passenger = passenger_map[key]
        seat = topology.seat_map[seat_id]
        rule = _ssr_rule(passenger.get("ssr", ""), config)

        expected_class = expected_cabin_class(passenger)
        if (
            expected_class is not None
            and seat.get("seatClass") != expected_class
        ):
            violations += 1
            detail["cabin_mismatch_violation"] += 1

        if key in reserved_seats and reserved_seats[key] != seat_id:
            violations += 1
            detail["reserved_mismatch"] += 1

        if seat.get("isExitRow", False) and not rule.get("allow_exit_row", False):
            violations += 1
            detail["ssr_exit_violation"] += 1

        if rule.get("requires_bassinet", False) and not seat.get("hasBassinet", False):
            violations += 1
            detail["ssr_bassinet_requirement_violation"] += 1

        if rule.get("requires_aisle", False) and not seat.get("isAisle", False):
            violations += 1
            detail["ssr_aisle_requirement_violation"] += 1

        ssr = passenger.get("ssr")
        if ssr:
            row = int(seat["row"])
            cabin = str(seat.get("seatClass", ""))
            subrow_ssr_count[(cabin, topology.subrow[seat_id])][ssr] += 1
            row_ssr_count[(cabin, row)][ssr] += 1

    for key, seat_id in valid_assignments.items():
        passenger = passenger_map[key]
        gid, hostnum = key
        mandatory = passenger.get("mandatoryRule", {}) or {}

        if mandatory.get("needBothSideEmpty", "N") == "Y":
            mandatory_config = config.get("mandatory_rules", {})
            allow_cross = bool(
                mandatory_config.get("both_side_empty_allow_cross_aisle", False)
            )
            neighbors = topology.row_neighbors(
                seat_id, allow_cross_aisle=allow_cross
            )
            require_two = mandatory_config.get("require_two_real_neighbors", True)
            if require_two and len(neighbors) != 2:
                violations += 1
                detail["need_both_empty_violation"] += 1
            occupied_neighbors = [sid for sid in neighbors if sid in occupied_set]
            if occupied_neighbors:
                violations += len(occupied_neighbors)
                detail["need_both_empty_violation"] += len(occupied_neighbors)
            if (
                (not require_two or len(neighbors) == 2)
                and not occupied_neighbors
            ):
                shared = protected_empty_seats.intersection(neighbors)
                if shared:
                    violations += len(shared)
                    detail["need_both_empty_violation"] += len(shared)
                    detail["empty_resource_shared"] += len(shared)
                protected_empty_seats.update(neighbors)

        if mandatory.get("needSingleSideEmpty", "N") == "Y":
            neighbors = topology.subrow_neighbors(seat_id)
            empty_neighbors = tuple(
                neighbor for neighbor in neighbors
                if neighbor not in occupied_set
            )
            if not empty_neighbors:
                violations += 1
                detail["need_single_empty_violation"] += 1
            else:
                single_empty_candidates.append((key, empty_neighbors))

        rule = _ssr_rule(passenger.get("ssr", ""), config)
        needs_strict_care = passenger.get("needCared", "N") == "Y"
        needs_general_companion = bool(rule.get("requires_caregiver", False))
        if needs_strict_care or needs_general_companion:
            allow_cross_aisle = bool(rule.get("caregiver_allow_cross_aisle", False))
            if needs_strict_care and not passenger.get("ssr"):
                allow_cross_aisle = False
            candidate_ids = topology.row_neighbors(
                seat_id, allow_cross_aisle=allow_cross_aisle
            )
            found = False
            for other_seat_id in candidate_ids:
                other_key = seat_owner.get(other_seat_id)
                if other_key is None or other_key[0] != gid or other_key[1] == hostnum:
                    continue
                other_passenger = passenger_map[other_key]
                if not _is_adult_caregiver(other_passenger):
                    continue
                found = True
                break
            if not found:
                violations += 1
                detail["caregiver_missing"] += 1

    # 真实业务中每一个保护空座只能服务一个留空需求。双侧留空先固定
    # 占用其全部邻座；单侧留空再通过二分图匹配选择互不重复的空邻座。
    empty_owner: Dict[str, int] = {}

    def augment_single_empty(
        demand_index: int, visited: Set[str]
    ) -> bool:
        for empty_seat in single_empty_candidates[demand_index][1]:
            if (
                empty_seat in protected_empty_seats
                or empty_seat in visited
            ):
                continue
            visited.add(empty_seat)
            previous = empty_owner.get(empty_seat)
            if (
                previous is None
                or augment_single_empty(previous, visited)
            ):
                empty_owner[empty_seat] = demand_index
                return True
        return False

    for demand_index in sorted(
        range(len(single_empty_candidates)),
        key=lambda index: len(single_empty_candidates[index][1]),
    ):
        if not augment_single_empty(demand_index, set()):
            violations += 1
            detail["need_single_empty_violation"] += 1
            detail["empty_resource_shared"] += 1

    subrows_to_check: Set[Tuple[str, Any]] = set()
    rows_to_check: Set[Tuple[str, int]] = set()
    for key, seat_id in valid_assignments.items():
        passenger = passenger_map[key]
        mandatory = passenger.get("mandatoryRule", {}) or {}
        seat = topology.seat_map[seat_id]
        cabin = str(seat.get("seatClass", ""))
        if passenger.get("ssr") and mandatory.get("sameSubRowNoOtherSSR", "N") == "Y":
            subrows_to_check.add((cabin, topology.subrow[seat_id]))
        if passenger.get("ssr") and mandatory.get("sameRowNoOtherSSR", "N") == "Y":
            rows_to_check.add((cabin, int(seat["row"])))

    for subrow in subrows_to_check:
        for count in subrow_ssr_count[subrow].values():
            if count > 1:
                violations += count - 1
                detail["same_subrow_ssr_conflict"] += count - 1

    for row in rows_to_check:
        for count in row_ssr_count[row].values():
            if count > 1:
                violations += count - 1
                detail["same_row_ssr_conflict"] += count - 1

    assigned_valid_count = len(valid_assignments)
    unassigned_count = max(0, total_passengers - assigned_valid_count)
    detail["unassigned_passenger_keys"] = sorted(
        key for key in passenger_map if key not in valid_assignments
    )
    return violations, unassigned_count, detail


def _numeric_seat_value(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _derived_seat_value(seat: dict, config: dict) -> float:
    seat_cfg = config.get("seat_value", {})
    value = 0.0
    if seat.get("seatClass") == "Business":
        value += float(seat_cfg.get("business_base", 50))
    if seat.get("extraLegroom", False):
        value += float(seat_cfg.get("extra_legroom", 20))
    if seat.get("hasBassinet", False):
        value += float(seat_cfg.get("bassinet", 10))
    explicit = _numeric_seat_value(seat.get("seatValue", seat.get("value")))
    return explicit if explicit is not None else value


def _near_toilet(seat: dict) -> bool:
    return bool(seat.get("nearToilet", seat.get("isNearToilet", False)))


def individual_assignment_components(
    passenger: Mapping[str, Any],
    old_topology: "SeatTopology",
    new_topology: "SeatTopology",
    new_seat_id: str,
    weights: Mapping[str, float],
    config: Mapping[str, Any],
) -> Dict[str, float]:
    """返回单个旅客换座产生的距离、价值和偏好得分。"""
    zero = {"distance": 0.0, "value": 0.0, "preference": 0.0}
    old_seat_id = passenger.get("oldSeat", {}).get("seatNum")
    if old_seat_id not in old_topology.seat_map:
        return zero
    old_seat = old_topology.seat_map[old_seat_id]
    new_seat = new_topology.seat_map[new_seat_id]
    algorithm = config.get("algorithm", {})
    prioritize_front = bool(algorithm.get("prioritize_front", True))
    front_factor = (
        float(algorithm.get("front_penalty_reduction", 0.1))
        if prioritize_front else 1.0
    )
    back_factor = (
        float(algorithm.get("back_penalty_factor", 0.2))
        if prioritize_front else 1.0
    )
    row_diff = int(new_seat["row"]) - int(old_seat["row"])
    row_penalty = (
        abs(row_diff) * front_factor
        if row_diff < 0 else row_diff * back_factor
    )
    distance = float(weights.get("w_s", -1.0)) * (
        abs(old_topology.x[old_seat_id] - new_topology.x[new_seat_id])
        + row_penalty
    )
    old_explicit = _numeric_seat_value(
        passenger.get("oldSeat", {}).get("seatValue")
    )
    old_value = (
        old_explicit
        if old_explicit is not None
        else _derived_seat_value(old_seat, config)
    )
    value = float(weights.get("w_v", -0.5)) * abs(
        _derived_seat_value(new_seat, config) - old_value
    )
    mismatch = sum(
        bool(new_seat.get(attr, False)) != bool(old_seat.get(attr, False))
        for attr in ("isWindow", "isAisle", "isExitRow", "hasBassinet")
    )
    if _near_toilet(new_seat) != _near_toilet(old_seat):
        mismatch += 1
    preference = float(weights.get("w_p", -0.8)) * mismatch
    for rule in passenger.get("optionRule", []) or []:
        if "nearToilet" not in rule:
            continue
        desired = str(rule.get("nearToilet", "Y")).upper() == "Y"
        if _near_toilet(new_seat) != desired:
            preference += float(
                weights.get("w_t", weights.get("w_p", -0.8))
            ) * float(rule.get("weight", 1.0))
    return {
        "distance": distance,
        "value": value,
        "preference": preference,
    }


def group_compactness_penalty(
    seat_ids: Sequence[str],
    topology: "SeatTopology",
    config: Mapping[str, Any],
) -> float:
    """按PDF模型计算旅客到同行组重心的最大横/纵绝对偏差。"""
    if len(seat_ids) <= 1:
        return 0.0
    algorithm = config.get("algorithm", {})
    xs = [topology.x[seat_id] for seat_id in seat_ids]
    ys = [topology.y[seat_id] for seat_id in seat_ids]
    centroid_x = sum(xs) / len(xs)
    centroid_y = sum(ys) / len(ys)
    max_x_deviation = max(abs(value - centroid_x) for value in xs)
    max_y_deviation = max(abs(value - centroid_y) for value in ys)
    return (
        float(algorithm.get("group_centroid_x_factor", 1.0))
        * max_x_deviation
        + float(algorithm.get("group_centroid_y_factor", 1.0))
        * max_y_deviation
    )


def baby_interference_score(
    infant_seat_id: str,
    other_seat_id: str,
    topology: "SeatTopology",
    weights: Mapping[str, float],
    config: Mapping[str, Any],
) -> float:
    """返回婴儿座位对一个非同行占座旅客的软约束得分。"""
    if infant_seat_id == other_seat_id:
        return 0.0
    infant_row = int(topology.seat_map[infant_seat_id]["row"])
    other_row = int(topology.seat_map[other_seat_id]["row"])
    if (
        topology.seat_map[infant_seat_id].get("seatClass")
        != topology.seat_map[other_seat_id].get("seatClass")
    ):
        return 0.0
    distance = 1.0 + abs(
        topology.x[infant_seat_id] - topology.x[other_seat_id]
    )
    weight = float(weights.get("w_b", -0.3))
    if topology.subrow[other_seat_id] == topology.subrow[infant_seat_id]:
        return weight / distance
    if abs(other_row - infant_row) == 1:
        return (
            weight
            * float(
                config.get("algorithm", {}).get(
                    "baby_front_back_factor", 1.0
                )
            )
            / distance
        )
    return 0.0


class IncrementalSoftScorer:
    """统一计算完整软评分及受影响同行组的精确增量。"""

    def __init__(
        self,
        new_seats_data: List[dict],
        old_seats_data: List[dict],
        groups_data: List[dict],
        weights: Mapping[str, float],
        config: Mapping[str, Any],
    ):
        self.new_topology = SeatTopology(new_seats_data, dict(config))
        self.old_topology = SeatTopology(old_seats_data, dict(config))
        self.passenger_map = _passenger_index(groups_data)
        self.weights = weights
        self.config = config
        self.group_keys: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        for key in self.passenger_map:
            self.group_keys[key[0]].append(key)
        self.individual_cache: Dict[
            Tuple[Tuple[int, int], str], Dict[str, float]
        ] = {}
        self.compact_cache: Dict[Tuple[str, ...], float] = {}

    def _individual(
        self, key: Tuple[int, int], seat_id: str
    ) -> Dict[str, float]:
        cache_key = (key, seat_id)
        if cache_key not in self.individual_cache:
            self.individual_cache[cache_key] = (
                individual_assignment_components(
                    self.passenger_map[key],
                    self.old_topology,
                    self.new_topology,
                    seat_id,
                    self.weights,
                    self.config,
                )
            )
        return self.individual_cache[cache_key]

    def _compactness(self, seat_ids: Sequence[str]) -> float:
        cache_key = tuple(sorted(seat_ids))
        if cache_key not in self.compact_cache:
            self.compact_cache[cache_key] = (
                float(self.weights.get("w_c", -2.0))
                * group_compactness_penalty(
                    seat_ids, self.new_topology, self.config
                )
            )
        return self.compact_cache[cache_key]

    def components(
        self,
        assigned_seats: Mapping[Tuple[int, int], str],
        affected_groups: Optional[Set[int]] = None,
    ) -> Dict[str, float]:
        """
        返回完整评分分项；指定 ``affected_groups`` 时，只返回所有会随这些
        同行组变化的项。两个方案的该结果之差就是精确局部增量。
        """
        valid = {
            key: seat_id
            for key, seat_id in assigned_seats.items()
            if (
                key in self.passenger_map
                and seat_id in self.new_topology.seat_map
            )
        }
        include_all = affected_groups is None
        affected = affected_groups or set()
        result = {
            "score_s": 0.0,
            "score_v": 0.0,
            "score_p": 0.0,
            "score_c": 0.0,
            "score_b": 0.0,
        }
        component_names = {
            "distance": "score_s",
            "value": "score_v",
            "preference": "score_p",
        }
        for key, seat_id in valid.items():
            if include_all or key[0] in affected:
                for name, value in self._individual(key, seat_id).items():
                    result[component_names[name]] += value

        for group_id, keys in self.group_keys.items():
            if not include_all and group_id not in affected:
                continue
            seat_ids = [valid[key] for key in keys if key in valid]
            if seat_ids:
                result["score_c"] += self._compactness(seat_ids)

        infant_items = [
            (key, seat_id)
            for key, seat_id in valid.items()
            if self.passenger_map[key].get("ssr") == "BSCT"
        ]
        for infant_key, infant_seat in infant_items:
            for other_key, other_seat in valid.items():
                if (
                    other_key[0] == infant_key[0]
                    or (
                        not include_all
                        and infant_key[0] not in affected
                        and other_key[0] not in affected
                    )
                ):
                    continue
                result["score_b"] += baby_interference_score(
                    infant_seat,
                    other_seat,
                    self.new_topology,
                    self.weights,
                    self.config,
                )
        result["total_soft_score"] = sum(result.values())
        return result

    def delta(
        self,
        old_assignments: Mapping[Tuple[int, int], str],
        new_assignments: Mapping[Tuple[int, int], str],
        affected_groups: Set[int],
    ) -> float:
        old_score = self.components(
            old_assignments, affected_groups
        )["total_soft_score"]
        new_score = self.components(
            new_assignments, affected_groups
        )["total_soft_score"]
        return new_score - old_score


def calculate_soft_score(
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    assigned_seats: Dict[Tuple[int, int], str],
    weights: Dict[str, float],
    config: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    detail = IncrementalSoftScorer(
        new_seats_data,
        old_seats_data,
        groups_data,
        weights,
        config,
    ).components(assigned_seats)
    return detail["total_soft_score"], detail


def calculate_combined_score(
    soft_score: float,
    unassigned_count: int,
    violations: int,
    elapsed: float,
    config: Dict[str, Any],
) -> Tuple[float, float]:
    """汇总质量、违规、未分配和运行时间惩罚。"""
    penalty = config.get("penalty", {})
    unassigned_penalty = float(penalty.get("unassigned", 1000.0))
    violation_penalty = float(penalty.get("violation", 800.0))
    time_factor = float(penalty.get("time_factor", 0.0))
    score_lower_bound = float(penalty.get("score_lower_bound", -999999.0))
    time_penalty = time_factor * max(0.0, elapsed)
    combined_score = (
        soft_score
        - unassigned_penalty * unassigned_count
        - violation_penalty * violations
        - time_penalty
    )
    return max(combined_score, score_lower_bound), time_penalty


def _allocation_worker(
    queue: mp.Queue,
    path_user_py: str,
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    weights: Dict[str, float],
    config: Dict[str, Any],
) -> None:
    try:
        spec = importlib.util.spec_from_file_location("user_module", path_user_py)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载模块")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "run_allocation"):
            raise RuntimeError("缺少 run_allocation 函数")
        result, module_score = module.run_allocation(
            new_seats_data, old_seats_data, groups_data, weights, config
        )
        queue.put(("ok", result, module_score))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _run_with_timeout(
    path_user_py: str,
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    weights: Dict[str, float],
    config: Dict[str, Any],
    timeout_seconds: float,
):
    ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
    queue: mp.Queue = ctx.Queue()
    proc = ctx.Process(
        target=_allocation_worker,
        args=(queue, path_user_py, new_seats_data, old_seats_data, groups_data, weights, config),
    )
    start = time.monotonic()
    proc.start()
    proc.join(timeout_seconds)
    elapsed = time.monotonic() - start

    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        return "timeout", None, None, elapsed
    if queue.empty():
        return "error", "WorkerError", "子进程没有返回结果", elapsed
    payload = queue.get()
    if payload[0] == "ok":
        return "ok", payload[1], payload[2], elapsed
    return "error", payload[1], payload[2], elapsed


def evaluate(path_user_py: str) -> Dict[str, Any]:
    result_metrics: Dict[str, Any] = {}
    try:
        source_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(source_dir)
        config_path = os.path.join(project_root, "config.json")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.json 不存在")

        with open(config_path, "r", encoding="utf-8") as file:
            config = json.load(file)

        data_cfg = config.get("data", {})
        new_seatmap_path = os.path.join(project_root, data_cfg.get("seatmap", "data/seatmap.json"))
        old_seatmap_path = os.path.join(project_root, data_cfg.get("oldseatmap", data_cfg.get("seatmap", "data/seatmap.json")))
        groups_path = os.path.join(project_root, data_cfg.get("groups", "data/groups.json"))

        for path in (new_seatmap_path, old_seatmap_path, groups_path):
            if not os.path.exists(path):
                raise FileNotFoundError(f"数据文件不存在: {path}")

        with open(new_seatmap_path, "r", encoding="utf-8") as file:
            new_seats_data = json.load(file)["seats"]
        with open(old_seatmap_path, "r", encoding="utf-8") as file:
            old_seats_data = json.load(file)["seats"]
        with open(groups_path, "r", encoding="utf-8") as file:
            groups_data = json.load(file)["groups"]

        weights = config.get("weights", {})
        max_time = float(config.get("evaluation", {}).get("max_time_seconds", 60))
        status, result, module_score, elapsed = _run_with_timeout(
            path_user_py,
            new_seats_data,
            old_seats_data,
            groups_data,
            weights,
            config,
            max_time,
        )

        if status == "timeout":
            return {
                "validity": 0.0,
                "combined_score": -1e9,
                "error_info": {
                    "error": f"运行超时（>{max_time:.3f}s）",
                    "error_type": "TimeoutError",
                    "elapsed": elapsed,
                },
            }
        if status == "error":
            return {
                "validity": 0.0,
                "combined_score": -1e9,
                "error_info": {
                    "error": module_score,
                    "error_type": result,
                    "elapsed": elapsed,
                },
            }
        if not isinstance(result, dict):
            raise TypeError("run_allocation 的第一个返回值必须是 dict")

        assigned_seats = result.get("assigned_seats", {})
        violations, unassigned_count, violation_detail = count_hard_constraint_violations(
            new_seats_data, groups_data, assigned_seats, config
        )
        soft_score, soft_detail = calculate_soft_score(
            new_seats_data,
            old_seats_data,
            groups_data,
            assigned_seats,
            weights,
            config,
        )

        combined_score, time_penalty = calculate_combined_score(
            soft_score,
            unassigned_count,
            violations,
            elapsed,
            config,
        )

        result_metrics["validity"] = 1.0 if violations == 0 else 0.0
        result_metrics["combined_score"] = combined_score
        result_metrics["score_seat_change"] = soft_detail["score_s"]
        result_metrics["score_seat_value"] = soft_detail["score_v"]
        result_metrics["score_attribute_match"] = soft_detail["score_p"]
        result_metrics["score_group_compactness"] = soft_detail["score_c"]
        result_metrics["score_baby_impact"] = soft_detail["score_b"]
        result_metrics["total_soft_score"] = soft_detail["total_soft_score"]
        result_metrics["time_penalty"] = time_penalty
        result_metrics["unassigned_count"] = unassigned_count
        result_metrics["violations"] = violations
        result_metrics["elapsed"] = elapsed
        result_metrics["module_score"] = module_score
        result_metrics["module_score_detail"] = result.get("score_detail", {})
        result_metrics["diagnostics"] = result.get("diagnostics", {})
        result_metrics["error_info"] = {} if violations == 0 else {
            "module_score": module_score,
            "elapsed": elapsed,
            "violation_detail": violation_detail,
            "assigned_count": len(assigned_seats) if isinstance(assigned_seats, dict) else 0,
            "total_passengers": sum(len(g.get("psrs", [])) for g in groups_data),
            "status": "penalized",
        }
        return result_metrics

    except Exception as exc:
        return {
            "validity": 0.0,
            "combined_score": -1e9,
            "error_info": {
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    default_algorithm = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "heuristic_seat_allocator.py"
    )
    parser.add_argument("user_file", nargs="?", default=default_algorithm)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.user_file), indent=2, ensure_ascii=False))
