# ==============================================================================
# BLOCK A START: 飞机座位分配系统
# 包含座位、乘客、组数据结构，以及基于规则的座位分配算法
#==============================================================================
from typing import (
    List,
    Set,
    Dict,
    Tuple,
    Optional,
    Any,
    FrozenSet,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
import time
import heapq
import json
import itertools
import copy
from collections import defaultdict

# ---------- 常量定义 ----------
DEFAULT_SSR_RULE = {
    'allow_exit_row': False,
    'requires_caregiver': False,
    'caregiver_allow_cross_aisle': False,
    'requires_aisle': False,
    'requires_bassinet': False,
}
CABIN_CLASS_MAP = {
    'F': 'First',
    'A': 'First',
    'C': 'Business',
    'J': 'Business',
    'W': 'PremiumEconomy',
    'P': 'PremiumEconomy',
    'Y': 'Economy',
    'E': 'Economy',
}

# 由 run_allocation 根据真实座位表初始化，避免依赖连续列字母。
_SEAT_NEIGHBORS: Dict[str, List[str]] = {}
_SEAT_ROW_NEIGHBORS: Dict[str, List[str]] = {}
_SEAT_SUBROW: Dict[str, Tuple[int, int]] = {}
_SEAT_X: Dict[str, float] = {}
_SEAT_ROW_INDEX: Dict[str, int] = {}
_GROUP_COMPACT_CACHE: Dict[Tuple[str, ...], float] = {}
_OLD_SEATS: Dict[str, 'Seat'] = {}
_OLD_SEAT_X: Dict[str, float] = {}
_OLD_SEAT_OWNER_REGRET: Dict[Tuple[int, int], float] = {}
_ACTIVE_CONFIG: Dict[str, Any] = {}


def seat_label_number(label: str) -> int:
    """把 A、B、…、Z、AA 等座位列号转换为稳定的自然顺序。"""
    text = str(label).strip().upper()
    if not text or not text.isalpha():
        raise ValueError(f"无效座位列号: {label!r}")
    value = 0
    for char in text:
        value = value * 26 + ord(char) - ord("A") + 1
    return value


class FixedSeatPrecheckError(ValueError):
    """固定座位硬约束导致输入不可行。"""

    def __init__(self, diagnostics: Dict[str, Any]):
        self.diagnostics = diagnostics
        summary = {
            'counts': diagnostics.get('counts', {}),
            'invalid_passenger_keys': diagnostics.get(
                'invalid_passenger_keys', []
            ),
            'invalid_seats': diagnostics.get('invalid_seats', []),
            'duplicate_seats': diagnostics.get('duplicate_seats', []),
            'hard_constraint_conflicts': diagnostics.get(
                'hard_constraint_conflicts', []
            ),
            'group_conflicts': diagnostics.get('group_conflicts', []),
        }
        super().__init__(
            "固定座位预检查失败，输入不可行: "
            + json.dumps(summary, ensure_ascii=False, sort_keys=True)
        )


class IncompleteAllocationError(RuntimeError):
    """The heuristic could not produce the required complete solution."""

    def __init__(self, violations: int, unassigned: int, detail: Dict[str, Any]):
        self.violations = int(violations)
        self.unassigned = int(unassigned)
        self.detail = detail
        super().__init__(
            "heuristic did not produce a complete feasible allocation: "
            f"violations={self.violations}, unassigned={self.unassigned}"
        )


# ==============================================================================
# 数据模型
# ==============================================================================
class Seat:
    """座位信息"""
    def __init__(self, data: dict):
        self.seat_id = data['seatId']          # 座位ID，如 "12A"
        self.row = data['row']                 # 排号
        self.col = data['col']                 # 列号，字母 'A'~'O'
        self.is_window = data['isWindow']      # 是否靠窗
        self.is_aisle = data['isAisle']        # 是否靠过道
        self.is_exit_row = data['isExitRow']   # 是否在紧急出口排
        self.has_bassinet = data['hasBassinet'] # 是否有婴儿摇篮位
        self.extra_legroom = data['extraLegroom'] # 是否有额外腿部空间
        self.seat_class = data['seatClass']    # 舱位等级（如 'Business'）
        self.seat_value = data.get('seatValue') # 座位价值（可选）
        self.near_toilet = bool(data.get('nearToilet', data.get('isNearToilet', False)))


class Passenger:
    """乘客信息，包含座位偏好和强制规则"""
    def __init__(self, data: dict):
        self.hostnum = data['hostnum']              # 乘客编号（组内唯一）
        self.cabin = data['cabin']                  # 舱位
        self.old_seat_num = data['oldSeat']['seatNum'] # 原座位ID
        self.old_seat_value = data['oldSeat'].get('seatValue', '') # 原座位价值
        self.new_seat_num = data.get('newSeat', {}).get('seatNum', None) # 新预留座位（如果有）
        self.need_cared = data.get('needCared', 'N') == 'Y' # 是否需要照顾（SSR乘客与看护者必须在同一子排且相邻）
        self.ssr = data.get('ssr', '')              # 特殊服务类型，如 'WCHR', 'BSCT' 等
        self.option_rules = data.get('optionRule', []) or []

        # 强制规则（mandatoryRule）
        mandatory = data.get('mandatoryRule', {})
        self.need_both_empty = mandatory.get('needBothSideEmpty', 'N') == 'Y'   # 两侧必须为空
        self.need_single_empty = mandatory.get('needSingleSideEmpty', 'N') == 'Y' # 至少一侧为空
        # 同行其他SSR乘客不能在同一子排（相邻且非两过道）
        self.same_subrow_no_other = mandatory.get('sameSubRowNoOtherSSR', 'N') == 'Y'
        # 同行其他SSR乘客不能在同一排
        self.same_row_no_other = mandatory.get('sameRowNoOtherSSR', 'N') == 'Y'


class Group:
    """预订组（PNR），包含一组乘客"""
    def __init__(self, data: dict):
        self.group_id = data['groupId']     # 组ID（全局唯一）
        self.passengers = [Passenger(p) for p in data['psrs']] # 组内乘客列表


# ---------- 辅助函数 ----------
def same_subrow(seat1: Seat, seat2: Seat) -> bool:
    return _SEAT_SUBROW.get(seat1.seat_id) == _SEAT_SUBROW.get(seat2.seat_id)


def get_adjacent_seat_ids(seat: Seat) -> List[str]:
    """返回同一小排中的真实左右邻座，不跨过道、不构造不存在的列。"""
    return list(_SEAT_NEIGHBORS.get(seat.seat_id, []))


def get_both_side_neighbor_ids(seat: Seat) -> List[str]:
    """返回双侧留空使用的排内邻座；可按配置把隔过道紧邻座计作一侧。"""
    allow_cross = bool(
        _ACTIVE_CONFIG.get('mandatory_rules', {}).get(
            'both_side_empty_allow_cross_aisle', False
        )
    )
    mapping = _SEAT_ROW_NEIGHBORS if allow_cross else _SEAT_NEIGHBORS
    return list(mapping.get(seat.seat_id, []))


def get_row_neighbor_ids(seat: Seat, allow_cross_aisle: bool) -> List[str]:
    mapping = _SEAT_ROW_NEIGHBORS if allow_cross_aisle else _SEAT_NEIGHBORS
    return list(mapping.get(seat.seat_id, []))


def get_ssr_rule(ssr: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rule = dict(DEFAULT_SSR_RULE)
    if not ssr:
        rule['allow_exit_row'] = True
        return rule
    active_config = config if config is not None else _ACTIVE_CONFIG
    configured = active_config.get('ssr_rules', {}).get(ssr, {})
    rule.update(configured)
    return rule


def passenger_requires_caregiver(
    passenger: Passenger, config: Optional[Dict[str, Any]] = None
) -> bool:
    return bool(
        passenger.need_cared
        or get_ssr_rule(passenger.ssr, config).get('requires_caregiver', False)
    )


def is_adult_caregiver(passenger: Passenger) -> bool:
    """与独立 evaluator 一致的无年龄字段成人陪护代理定义。"""
    return bool(
        not passenger.ssr
        and not passenger.need_cared
        and not passenger.need_both_empty
        and not passenger.need_single_empty
    )


def caregiver_satisfied(
    group: Group,
    passenger: Passenger,
    context: "AssignmentContext",
    config: Dict[str, Any],
) -> bool:
    """检查已就座的需陪护旅客旁是否已有同组成人陪护者。"""
    key = (group.group_id, passenger.hostnum)
    seat_id = context.assigned_seats.get(key)
    if seat_id is None:
        return False
    allow_cross = bool(
        get_ssr_rule(passenger.ssr, config).get(
            "caregiver_allow_cross_aisle", False
        )
    )
    neighbors = set(
        get_row_neighbor_ids(context.seats[seat_id], allow_cross)
    )
    return any(
        other.hostnum != passenger.hostnum
        and is_adult_caregiver(other)
        and context.assigned_seats.get(
            (group.group_id, other.hostnum)
        )
        in neighbors
        for other in group.passengers
    )


def build_seat_topology(
    seats_data: List[dict], config: Dict[str, Any]
) -> Tuple[
    Dict[str, List[str]], Dict[str, List[str]],
    Dict[str, Tuple[int, int]], Dict[str, float], Dict[str, int]
]:
    """按真实排布局构造不跨过道邻接、排内邻接、小排、坐标和排内索引。"""
    rows: Dict[int, List[dict]] = {}
    for raw in seats_data:
        rows.setdefault(int(raw['row']), []).append(raw)
    seat_spacing = float(config.get('seat_geometry', {}).get('seat_spacing', 1.0))
    aisle_gap = float(config.get('seat_geometry', {}).get('aisle_gap', 0.8))
    neighbors: Dict[str, List[str]] = {}
    row_neighbors: Dict[str, List[str]] = {}
    subrows: Dict[str, Tuple[int, int]] = {}
    xs: Dict[str, float] = {}
    indexes: Dict[str, int] = {}

    for row, row_seats in rows.items():
        row_seats.sort(
            key=lambda raw: seat_label_number(raw['col'])
        )
        # 一个座位只能位于一条过道的一侧，避免2-2-2中间座位误判出第三条过道。
        breaks: Set[int] = set()
        i = 0
        while i < len(row_seats) - 1:
            if row_seats[i].get('isAisle', False) and row_seats[i + 1].get('isAisle', False):
                breaks.add(i)
                i += 2
            else:
                i += 1

        subrow_id = 0
        current_x = 0.0
        raw_x: List[float] = []
        for i, raw in enumerate(row_seats):
            sid = raw['seatId']
            indexes[sid] = i
            subrows[sid] = (row, subrow_id)
            raw_x.append(current_x)
            if i < len(row_seats) - 1:
                current_x += seat_spacing + (aisle_gap if i in breaks else 0.0)
                if i in breaks:
                    subrow_id += 1
        center = (raw_x[0] + raw_x[-1]) / 2.0 if raw_x else 0.0
        for raw, xpos in zip(row_seats, raw_x):
            xs[raw['seatId']] = xpos - center
        for i, raw in enumerate(row_seats):
            sid = raw['seatId']
            all_adjacent: List[str] = []
            same_subrow_adjacent: List[str] = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(row_seats):
                    other_id = row_seats[j]['seatId']
                    all_adjacent.append(other_id)
                    if subrows[sid] == subrows[other_id]:
                        same_subrow_adjacent.append(other_id)
            row_neighbors[sid] = all_adjacent
            neighbors[sid] = same_subrow_adjacent
    return neighbors, row_neighbors, subrows, xs, indexes

def is_seat_feasible(
    seat: Seat,
    passenger: Passenger,
    occupied: Set[str],           # 已被分配的座位ID集合
    seat_ssr_map: Dict[str, Passenger], # 座位到已分配SSR旅客的映射
    blocked: Set[str],            # 被逻辑锁定的座位（如为满足"一侧为空"而预留）
    seats: Dict[str, Seat],       # 所有座位字典
    exclude_occupied: Optional[Set[str]] = None  # 在检查时忽略的占用座位（如当前组内另一名正在分配的乘客）
) -> bool:
    """
    检查给定座位是否满足该乘客的所有硬性约束
    """
    if exclude_occupied is None:
        exclude_occupied = set()

    # 座位不能已被其他人占用（除非在排除列表中）
    if seat.seat_id in occupied and seat.seat_id not in exclude_occupied:
        return False
    # 不能是被逻辑锁定的座位
    if seat.seat_id in blocked:
        return False
    raw_cabin = str(passenger.cabin).strip()
    expected_class = CABIN_CLASS_MAP.get(raw_cabin.upper(), raw_cabin)
    if expected_class and seat.seat_class != expected_class:
        return False
    ssr_rule = get_ssr_rule(passenger.ssr)
    if seat.is_exit_row and not ssr_rule.get('allow_exit_row', False):
        return False
    if ssr_rule.get('requires_bassinet', False) and not seat.has_bassinet:
        return False
    if ssr_rule.get('requires_aisle', False) and not seat.is_aisle:
        return False
    # 需要两侧为空
    if passenger.need_both_empty:
        adj_ids = get_both_side_neighbor_ids(seat)
        mandatory_config = _ACTIVE_CONFIG.get('mandatory_rules', {})
        if mandatory_config.get('require_two_real_neighbors', True) and len(adj_ids) != 2:
            return False
        for adj_id in adj_ids:
            if (adj_id in occupied and adj_id not in exclude_occupied) or adj_id in blocked:
                return False
    # 需要至少一侧为空
    if passenger.need_single_empty:
        adj_ids = get_adjacent_seat_ids(seat)
        if not adj_ids:
            return False
        has_empty = False
        for adj_id in adj_ids:
            if (adj_id not in occupied or adj_id in exclude_occupied) and adj_id not in blocked:
                has_empty = True
                break
        if not has_empty:
            return False

    # 任一带隔离标志的 SSR 会激活所在排/子排；激活后该位置内每种
    # SSR 至多一人。这与独立评估器及列生成的 conditional SSR
    # 资源语义一致，不能只比较当前旅客与“同类型且带标志”的旅客。
    if passenger.ssr:
        assigned_at_location: List[Tuple[Seat, Passenger]] = []
        for assigned_id, assigned_passenger in seat_ssr_map.items():
            if assigned_id in exclude_occupied:
                continue
            assigned_seat = seats.get(assigned_id)
            if assigned_seat and assigned_passenger.ssr:
                assigned_at_location.append(
                    (assigned_seat, assigned_passenger)
                )

        def activated_location_conflict(
            same_location: Callable[[Seat], bool],
            passenger_flag: bool,
            flag_name: str,
        ) -> bool:
            located = [
                assigned_passenger
                for assigned_seat, assigned_passenger
                in assigned_at_location
                if same_location(assigned_seat)
            ]
            active = passenger_flag or any(
                bool(getattr(assigned_passenger, flag_name))
                for assigned_passenger in located
            )
            if not active:
                return False
            counts: Dict[str, int] = defaultdict(int)
            for assigned_passenger in located:
                counts[assigned_passenger.ssr] += 1
            counts[passenger.ssr] += 1
            return any(count > 1 for count in counts.values())

        if activated_location_conflict(
            lambda assigned_seat: (
                assigned_seat.row == seat.row
                and same_subrow(seat, assigned_seat)
            ),
            passenger.same_subrow_no_other,
            "same_subrow_no_other",
        ):
            return False
        if activated_location_conflict(
            lambda assigned_seat: assigned_seat.row == seat.row,
            passenger.same_row_no_other,
            "same_row_no_other",
        ):
            return False
    return True


# ---------- 座位分配上下文 ----------
class AssignmentContext:
    """
    管理整个分配过程的状态：
    - 已占用座位、已分配映射、SSR座位映射
    - 逻辑锁定座位（为满足"需空侧"而临时锁定）
    - BSCT座位缓存、排内分配记录（用于紧凑度计算）
    """
    def __init__(self, seats: Dict[str, Seat]):
        self.seats = seats
        self.occupied: Set[str] = set()                     # 最终占用的座位ID
        self.assigned_seats: Dict[Tuple[int, int], str] = {} # (组ID, 乘客编号) -> 座位ID
        self.seat_ssr_map: Dict[str, Passenger] = {}        # 座位ID -> SSR旅客
        self.blocked_counts: Dict[str, int] = {}            # 逻辑锁定计数（支持多重锁定）
        self.blocked: Set[str] = set()                      # 逻辑锁定的座位集合
        self.assigned_blocked: Dict[Tuple[int, int], Set[str]] = {} # 记录为哪个乘客锁定了哪些座位
        self.bsct_seats: Set[str] = set()                   # 已分配的婴儿摇篮座位集合
        self.row_assignments: Dict[int, List[Tuple[int, str]]] = {} # 排号 -> [(组ID, 座位ID)]
        self.owner_group_by_seat: Dict[str, int] = {}

    def add_block(self, seat_id: str):
        """增加一次逻辑锁定"""
        self.blocked_counts[seat_id] = self.blocked_counts.get(seat_id, 0) + 1
        self.blocked.add(seat_id)

    def remove_block(self, seat_id: str):
        """减少一次逻辑锁定，计数为0时释放"""
        if seat_id in self.blocked_counts:
            self.blocked_counts[seat_id] -= 1
            if self.blocked_counts[seat_id] <= 0:
                del self.blocked_counts[seat_id]
                self.blocked.discard(seat_id)

    def assign_passenger(self, passenger: Passenger, seat_id: str, group_id: int,
                         exclude_block_seats: Optional[Set[str]] = None,
                         chosen_block: Optional[str] = None) -> bool:
        """
        将乘客分配到座位，同时处理两侧/单侧为空的锁定逻辑。
        exclude_block_seats: 在锁定相邻座位时不应锁定的座位（例如组内另一名已分配乘客的座位）
        chosen_block: 当乘客只需单侧空时，指定锁定哪一侧（由beam search确定）
        """
        if exclude_block_seats is None:
            exclude_block_seats = set()
        seat = self.seats.get(seat_id)
        if not seat:
            return False

        # 再次检查可行性（考虑当前上下文和排除集）
        if not is_seat_feasible(seat, passenger, self.occupied, self.seat_ssr_map,
                                self.blocked, self.seats, exclude_occupied=exclude_block_seats):
            return False

        # 记录分配
        self.occupied.add(seat_id)
        self.assigned_seats[(group_id, passenger.hostnum)] = seat_id
        self.owner_group_by_seat[seat_id] = group_id
        if passenger.ssr:
            self.seat_ssr_map[seat_id] = passenger
            if passenger.ssr == 'BSCT':
                self.bsct_seats.add(seat_id)

        # 更新排内分配记录（用于后续紧凑度计算）
        row = seat.row
        if row not in self.row_assignments:
            self.row_assignments[row] = []
        self.row_assignments[row].append((group_id, seat_id))

        added_blocked = set()

        if passenger.need_both_empty:
            # 两侧都需要为空：锁定所有相邻座位（排除指定座位）
            for adj in get_both_side_neighbor_ids(seat):
                if adj not in self.occupied and adj not in exclude_block_seats:
                    self.add_block(adj)
                    added_blocked.add(adj)
        elif passenger.need_single_empty:
            # 至少一侧为空：优先使用chosen_block，否则找一个可用侧
            found = False
            if chosen_block and chosen_block not in self.occupied and chosen_block not in self.blocked and chosen_block not in exclude_block_seats:
                self.add_block(chosen_block)
                added_blocked.add(chosen_block)
                found = True
            else:
                for adj in get_adjacent_seat_ids(seat):
                    if adj not in self.occupied and adj not in self.blocked and adj not in exclude_block_seats:
                        self.add_block(adj)
                        added_blocked.add(adj)
                        found = True
                        break
            if not found:
                # 无法满足单侧为空，回滚分配
                self.occupied.remove(seat_id)
                self.assigned_seats.pop((group_id, passenger.hostnum), None)
                self.owner_group_by_seat.pop(seat_id, None)
                if passenger.ssr:
                    self.seat_ssr_map.pop(seat_id, None)
                    if passenger.ssr == 'BSCT':
                        self.bsct_seats.discard(seat_id)
                if row in self.row_assignments:
                    if (group_id, seat_id) in self.row_assignments[row]:
                        self.row_assignments[row].remove((group_id, seat_id))
                return False

        if added_blocked:
            self.assigned_blocked[(group_id, passenger.hostnum)] = added_blocked
        return True

    def remove_assignment(self, group_id: int, hostnum: int):
        """移除一个乘客的分配，同时释放其锁定的座位"""
        key = (group_id, hostnum)
        if key in self.assigned_seats:
            seat_id = self.assigned_seats[key]
            seat = self.seats.get(seat_id)
            self.occupied.remove(seat_id)
            self.assigned_seats.pop(key, None)
            self.owner_group_by_seat.pop(seat_id, None)
            ssr_passenger = self.seat_ssr_map.pop(seat_id, None)
            if ssr_passenger and ssr_passenger.ssr == 'BSCT':
                self.bsct_seats.discard(seat_id)
            if seat:
                row_list = self.row_assignments.get(seat.row, [])
                if (group_id, seat_id) in row_list:
                    row_list.remove((group_id, seat_id))
            # 释放该乘客锁定的座位
            blocked_to_remove = self.assigned_blocked.pop(key, set())
            for b in blocked_to_remove:
                self.remove_block(b)


# ---------- 座位排序相关函数 ----------
def calc_seat_value(seat: Seat, config: Optional[Dict[str, Any]] = None) -> float:
    """计算座位的客观价值（用于近似原座位价值）"""
    config = config or {}
    if seat.seat_value is not None:
        try:
            return float(seat.seat_value)
        except ValueError:
            pass
    val = 0.0
    seat_value_config = config.get('seat_value', {})
    if seat.seat_class == 'Business':
        val += float(seat_value_config.get('business_base', 50))
    if seat.extra_legroom:
        val += float(seat_value_config.get('extra_legroom', 20))
    if seat.has_bassinet:
        val += float(seat_value_config.get('bassinet', 10))
    return val


def calc_group_compactness(group_seats: List[Seat], config: Dict[str, Any]) -> float:
    """与PDF及独立评分器一致的组重心最大偏差惩罚。"""
    if len(group_seats) <= 1:
        return 0.0
    cache_key = tuple(sorted(seat.seat_id for seat in group_seats))
    if cache_key in _GROUP_COMPACT_CACHE:
        return _GROUP_COMPACT_CACHE[cache_key]
    algorithm = config.get('algorithm', {})
    x_factor = float(algorithm.get('group_centroid_x_factor', 1.0))
    y_factor = float(algorithm.get('group_centroid_y_factor', 1.0))
    row_spacing = float(config.get('seat_geometry', {}).get('row_spacing', 1.5))
    xs = [
        _SEAT_X.get(
            seat.seat_id, float(seat_label_number(seat.col))
        )
        for seat in group_seats
    ]
    ys = [seat.row * row_spacing for seat in group_seats]
    centroid_x = sum(xs) / len(xs)
    centroid_y = sum(ys) / len(ys)
    result = (
        x_factor * max(abs(value - centroid_x) for value in xs)
        + y_factor * max(abs(value - centroid_y) for value in ys)
    )
    _GROUP_COMPACT_CACHE[cache_key] = result
    return result


def calc_seat_sort_key(p: Passenger, seat: Seat,
                       seat_values: Dict[str, float],
                       weights: Dict[str, float], config: Dict[str, Any],
                       context: Optional[AssignmentContext] = None,
                       group_id: int = -1,
                       old_seat_owners: Optional[Dict[str, Tuple[int, int]]] = None) -> float:
    """
    为乘客和候选座位计算一个排序分数，分数越低越好（因为最终取反）。
    分数基于：
    - 与原座位的位置距离和属性差异
    - 与组内已分配座位的紧凑度（组紧凑度）
    - 与摇篮乘客的干扰（避免与别组的摇篮乘客在同一排太近）
    - 抢占其他乘客原座位的惩罚
    权重来自 weights 字典。
    """
    old_seat = _OLD_SEATS.get(p.old_seat_num)
    old_val = 0.0
    # 获取原座位价值
    if p.old_seat_value is not None and p.old_seat_value != '':
        try:
            old_val = float(p.old_seat_value)
        except ValueError:
            if old_seat:
                old_val = calc_seat_value(old_seat, config)
    elif old_seat:
        old_val = calc_seat_value(old_seat, config)
    new_val = seat_values.get(seat.seat_id, 0.0)
    val_diff = abs(new_val - old_val)

    w_s = weights.get('w_s', -1.0)   # 空间距离权重
    w_v = weights.get('w_v', -0.5)   # 价值差异权重
    w_p = weights.get('w_p', -0.8)   # 属性变化惩罚权重
    w_b = weights.get('w_b', -0.3)   # 摇篮影响权重

    score = w_v * val_diff

    if old_seat:
        # prioritize_front 开启时，分别按配置降低前移和后移的排距惩罚。
        algorithm_config = config.get('algorithm', {})
        prioritize_front = bool(algorithm_config.get('prioritize_front', True))
        front_factor = float(algorithm_config.get('front_penalty_reduction', 0.1)) if prioritize_front else 1.0
        back_factor = float(algorithm_config.get('back_penalty_factor', 0.2)) if prioritize_front else 1.0
        dx = abs(
            _SEAT_X.get(
                seat.seat_id, float(seat_label_number(seat.col))
            )
            - _OLD_SEAT_X.get(
                old_seat.seat_id,
                float(seat_label_number(old_seat.col)),
            )
        )
        dy = old_seat.row - seat.row  # 正数表示新座位在前方（排号更小）
        if dy >= 0:
            dist = dy * front_factor + dx
        else:
            dist = abs(dy) * back_factor + dx
        # 属性变化：窗户、过道、出口排、摇篮
        attr_penalty = 0
        if seat.is_window != old_seat.is_window: attr_penalty += 1
        if seat.is_aisle != old_seat.is_aisle: attr_penalty += 1
        if seat.is_exit_row != old_seat.is_exit_row: attr_penalty += 1
        if seat.has_bassinet != old_seat.has_bassinet: attr_penalty += 1
        if seat.near_toilet != old_seat.near_toilet: attr_penalty += 1
        score += w_s * dist + w_p * attr_penalty

    # optionRule 中的厕所偏好。w_t 未配置时沿用属性权重。
    w_t = weights.get('w_t', w_p)
    for rule in p.option_rules:
        if 'nearToilet' not in rule:
            continue
        desired = str(rule.get('nearToilet', 'Y')).upper() == 'Y'
        try:
            rule_weight = float(rule.get('weight', 1.0))
        except (TypeError, ValueError):
            rule_weight = 1.0
        if seat.near_toilet != desired:
            score += w_t * rule_weight

    if old_seat_owners is not None:
        current_key = (group_id, p.hostnum)
        owner = old_seat_owners.get(seat.seat_id)
        if owner is not None and owner != current_key:
            pressure = float(
                config.get('algorithm', {}).get(
                    'old_seat_reservation_pressure', 1.0
                )
            )
            score -= pressure * max(
                0.0, _OLD_SEAT_OWNER_REGRET.get(owner, 0.0)
            )

    # 干扰项：与别组摇篮旅客同小排或正前后排，距离越近惩罚越大。
    if context is not None and group_id != -1:
        impact = 0.0
        p_x = _SEAT_X.get(
            seat.seat_id,
            float(seat_label_number(seat.col) - 1),
        )
        front_back_factor = float(config.get('algorithm', {}).get('baby_front_back_factor', 1.0))
        if p.ssr == 'BSCT':
            for other_row, assignments in context.row_assignments.items():
                for other_gid, other_seat_id in assignments:
                    if other_gid == group_id:
                        continue
                    other_seat = context.seats.get(other_seat_id)
                    if not other_seat:
                        continue
                    other_x = _SEAT_X.get(
                        other_seat_id,
                        float(seat_label_number(other_seat.col) - 1),
                    )
                    if _SEAT_SUBROW.get(other_seat_id) == _SEAT_SUBROW.get(seat.seat_id):
                        impact += 1.0 / (1 + abs(p_x - other_x))
                    elif abs(other_row - seat.row) == 1:
                        impact += front_back_factor / (1 + abs(p_x - other_x))
        else:
            for bsct_seat_id in context.bsct_seats:
                bsct_seat = context.seats.get(bsct_seat_id)
                if not bsct_seat:
                    continue
                # 如果是同组，忽略
                if (
                    context.owner_group_by_seat.get(bsct_seat_id)
                    == group_id
                ):
                    continue
                other_x = _SEAT_X.get(
                    bsct_seat.seat_id,
                    float(seat_label_number(bsct_seat.col) - 1),
                )
                if _SEAT_SUBROW.get(bsct_seat_id) == _SEAT_SUBROW.get(seat.seat_id):
                    impact += 1.0 / (1 + abs(p_x - other_x))
                elif abs(bsct_seat.row - seat.row) == 1:
                    impact += front_back_factor / (1 + abs(p_x - other_x))
        score += w_b * impact

    return -score  # 返回负值，使排序升序时分数小的排在前面


def build_passenger_sorted_seats(groups: List[Group],
                                 seats: Dict[str, Seat],
                                 seat_values: Dict[str, float],
                                 weights: Dict[str, float],
                                 config: Dict[str, Any],
                                 context: AssignmentContext,
                                 old_seat_owners: Dict[str, Tuple[int, int]]) -> Dict[Tuple[int, int], List[Seat]]:
    """
    为每个乘客生成按偏好排序的座位列表
    """
    sorted_seats = {}
    for g in groups:
        for p in g.passengers:
            key = (g.group_id, p.hostnum)
            sorted_seats[key] = sorted(
                seats.values(),
                key=lambda s: calc_seat_sort_key(p, s, seat_values, weights, config, context, g.group_id, old_seat_owners)
            )
    return sorted_seats


def compute_old_seat_owner_regret(
    groups: List[Group],
    seats: Dict[str, Seat],
    seat_values: Dict[str, float],
    weights: Dict[str, float],
    config: Dict[str, Any],
    context: AssignmentContext,
) -> Dict[Tuple[int, int], float]:
    """估计原座位主人失去该座位后的最好替代损失。"""
    regrets: Dict[Tuple[int, int], float] = {}
    for group in groups:
        for passenger in group.passengers:
            key = (group.group_id, passenger.hostnum)
            own_seat = seats.get(passenger.old_seat_num)
            if own_seat is None or not is_seat_feasible(
                own_seat,
                passenger,
                context.occupied,
                context.seat_ssr_map,
                context.blocked,
                context.seats,
            ):
                regrets[key] = 0.0
                continue
            own_cost = calc_seat_sort_key(
                passenger,
                own_seat,
                seat_values,
                weights,
                config,
                context,
                group.group_id,
                None,
            )
            alternative_cost = min(
                (
                    calc_seat_sort_key(
                        passenger,
                        seat,
                        seat_values,
                        weights,
                        config,
                        context,
                        group.group_id,
                        None,
                    )
                    for seat in seats.values()
                    if seat.seat_id != own_seat.seat_id
                    and is_seat_feasible(
                        seat,
                        passenger,
                        context.occupied,
                        context.seat_ssr_map,
                        context.blocked,
                        context.seats,
                    )
                ),
                default=own_cost,
            )
            regrets[key] = max(
                0.0, alternative_cost - own_cost
            )
    return regrets


# ---------- 分配阶段函数 ----------
def precheck_reserved_seats(
    groups: List[Group], seats: Dict[str, Seat], config: Dict[str, Any]
) -> Tuple[Dict[str, Any], Set[Tuple[int, int]]]:
    """在提交任何固定座位前，全量检查无效、重复、硬约束和同行组冲突。"""
    diagnostics: Dict[str, Any] = {
        'invalid_seats': [],
        'duplicate_seats': [],
        'hard_constraint_conflicts': [],
        'group_conflicts': [],
        'semantics': {
            'both_side_empty': config.get('mandatory_rules', {}).get(
                'both_side_empty_semantics', 'empty_adjacent_seats')
        },
    }
    invalid_keys: Set[Tuple[int, int]] = set()
    passenger_by_key = {
        (g.group_id, p.hostnum): p for g in groups for p in g.passengers
    }
    group_by_id = {g.group_id: g for g in groups}
    owners_by_seat: Dict[str, List[Tuple[int, int]]] = {}
    for key, passenger in passenger_by_key.items():
        if passenger.new_seat_num:
            owners_by_seat.setdefault(passenger.new_seat_num, []).append(key)

    for seat_id, keys in owners_by_seat.items():
        if seat_id not in seats:
            for key in keys:
                invalid_keys.add(key)
                diagnostics['invalid_seats'].append({
                    'passenger': key, 'seat': seat_id, 'reason': 'seat_not_found'
                })
        if len(keys) > 1:
            invalid_keys.update(keys)
            diagnostics['duplicate_seats'].append({
                'seat': seat_id, 'passengers': keys
            })

    valid_fixed: Dict[Tuple[int, int], str] = {}
    for key, passenger in passenger_by_key.items():
        seat_id = passenger.new_seat_num
        if not seat_id or key in invalid_keys or seat_id not in seats:
            continue
        seat = seats[seat_id]
        if not is_seat_feasible(seat, passenger, set(), {}, set(), seats):
            invalid_keys.add(key)
            diagnostics['hard_constraint_conflicts'].append({
                'passenger': key,
                'seat': seat_id,
                'reason': 'individual_hard_constraint',
            })
        else:
            valid_fixed[key] = seat_id

    fixed_owner = {seat_id: key for key, seat_id in valid_fixed.items()}
    for key, seat_id in list(valid_fixed.items()):
        passenger = passenger_by_key[key]
        seat = seats[seat_id]
        single_neighbors = get_adjacent_seat_ids(seat)
        both_neighbors = get_both_side_neighbor_ids(seat)
        occupied_neighbors = [sid for sid in both_neighbors if sid in fixed_owner]
        if passenger.need_both_empty and occupied_neighbors:
            invalid_keys.add(key)
            issue = {
                'passenger': key, 'seat': seat_id,
                'reason': 'both_side_empty_conflicts_with_fixed',
                'conflicting_passengers': [fixed_owner[sid] for sid in occupied_neighbors],
            }
            target = 'group_conflicts' if any(owner[0] == key[0] for owner in issue['conflicting_passengers']) else 'hard_constraint_conflicts'
            diagnostics[target].append(issue)
        if (passenger.need_single_empty and single_neighbors
                and all(sid in fixed_owner for sid in single_neighbors)):
            invalid_keys.add(key)
            diagnostics['hard_constraint_conflicts'].append({
                'passenger': key, 'seat': seat_id,
                'reason': 'single_side_empty_has_no_free_fixed_neighbor',
            })

    # 只有当该组所有可用陪护者都已固定时，才能确定固定位置构成陪护冲突。
    for key, seat_id in list(valid_fixed.items()):
        passenger = passenger_by_key[key]
        if not passenger_requires_caregiver(passenger, config):
            continue
        caregivers = [
            p for p in group_by_id[key[0]].passengers
            if not p.ssr and not p.need_both_empty and not p.need_single_empty
        ]
        if not caregivers:
            invalid_keys.add(key)
            diagnostics['group_conflicts'].append({
                'passenger': key, 'seat': seat_id,
                'reason': 'no_eligible_caregiver_in_group',
            })
        elif all(cg.new_seat_num for cg in caregivers):
            allow_cross = bool(get_ssr_rule(passenger.ssr, config).get('caregiver_allow_cross_aisle', False))
            neighbor_ids = set(get_row_neighbor_ids(seats[seat_id], allow_cross))
            if not any(cg.new_seat_num in neighbor_ids for cg in caregivers):
                invalid_keys.add(key)
                diagnostics['group_conflicts'].append({
                    'passenger': key, 'seat': seat_id,
                    'reason': 'all_fixed_caregivers_are_not_adjacent',
                })

    fixed_items = list(valid_fixed.items())
    for location_kind, flag_name in (
        ("subrow", "same_subrow_no_other"),
        ("row", "same_row_no_other"),
    ):
        by_location: Dict[Any, List[Tuple[Tuple[int, int], str, Passenger]]] = (
            defaultdict(list)
        )
        for key, seat_id in fixed_items:
            passenger = passenger_by_key[key]
            if not passenger.ssr:
                continue
            seat = seats[seat_id]
            location = (
                _SEAT_SUBROW[seat_id]
                if location_kind == "subrow" else seat.row
            )
            by_location[location].append((key, seat_id, passenger))
        for location, located in by_location.items():
            flagged = [
                item for item in located
                if bool(getattr(item[2], flag_name))
            ]
            if not flagged:
                continue
            by_type: Dict[str, List[Tuple[Tuple[int, int], str, Passenger]]] = (
                defaultdict(list)
            )
            for item in located:
                by_type[item[2].ssr].append(item)
            duplicates = [
                item
                for same_type in by_type.values()
                if len(same_type) > 1
                for item in same_type
            ]
            if not duplicates:
                continue
            conflict_items = {
                (item[0], item[1]) for item in [*flagged, *duplicates]
            }
            conflict_keys = sorted(key for key, _ in conflict_items)
            invalid_keys.update(conflict_keys)
            diagnostics['hard_constraint_conflicts'].append({
                'passengers': conflict_keys,
                'seats': sorted(seat_id for _, seat_id in conflict_items),
                'location': location,
                'reason': f'fixed_conditional_ssr_{location_kind}_conflict',
            })

    diagnostics['invalid_passenger_keys'] = sorted(invalid_keys)
    diagnostics['has_errors'] = bool(invalid_keys)
    diagnostics['counts'] = {
        name: len(diagnostics[name])
        for name in ('invalid_seats', 'duplicate_seats',
                     'hard_constraint_conflicts', 'group_conflicts')
    }
    return diagnostics, invalid_keys


def assign_reserved_seats(
    groups: List[Group], context: AssignmentContext,
    invalid_keys: Optional[Set[Tuple[int, int]]] = None
):
    """
    阶段1：分配所有乘客的预留座位（new_seat_num）
    """
    invalid_keys = invalid_keys or set()
    if invalid_keys:
        raise ValueError(
            f"不能跳过 {len(invalid_keys)} 名固定座位旅客；请先处理预检查错误"
        )
    fixed_seat_ids = {
        p.new_seat_num
        for g in groups
        for p in g.passengers
        if p.new_seat_num
    }
    for g in groups:
        for p in g.passengers:
            if not p.new_seat_num:
                continue
            chosen_block = None
            if p.need_single_empty:
                # If one side is another passenger's immutable seat, reserve
                # the other side explicitly instead of depending on input order.
                seat = context.seats[p.new_seat_num]
                chosen_block = next(
                    (
                        neighbor
                        for neighbor in get_adjacent_seat_ids(seat)
                        if neighbor not in fixed_seat_ids
                    ),
                    None,
                )
            if not context.assign_passenger(
                p, p.new_seat_num, g.group_id, chosen_block=chosen_block
            ):
                raise RuntimeError(
                    "固定座位提交失败: "
                    f"groupId={g.group_id}, hostnum={p.hostnum}, seat={p.new_seat_num}"
                )


def assign_paired_ssrs(groups: List[Group], context: AssignmentContext,
                       passenger_sorted_seats: Dict[Tuple[int, int], List[Seat]],
                       seat_values: Dict[str, float], weights: Dict[str, float],
                       config: Dict[str, Any],
                       old_seat_owners: Dict[str, Tuple[int, int]],
                       deadline: Optional[float] = None) -> int:
    """
    阶段2：分配需要特殊照顾的乘客（SSR）及其看护者。
    看护者是无SSR的普通乘客（cg_ps）。
    处理逻辑：
    1. 如果SSR乘客已分配，尝试在相邻位置安排未分配的看护者。
    2. 如果SSR乘客未分配，尝试将其安排在已分配看护者旁边，或同时寻找一对相邻座位。
    """
    w_c = weights.get('w_c', -2.0)  # 组紧凑度权重
    assigned_before = len(context.assigned_seats)
    paired_groups = sorted(
        groups,
        key=lambda group: (
            0
            if any(
                passenger.ssr == "BSCT"
                and passenger_requires_caregiver(passenger, config)
                for passenger in group.passengers
            )
            else 1,
            -sum(
                passenger_requires_caregiver(passenger, config)
                for passenger in group.passengers
            ),
            group.group_id,
        ),
    )
    for g in paired_groups:
        if deadline is not None and time.perf_counter() >= deadline:
            break
        ssr_ps = [p for p in g.passengers if passenger_requires_caregiver(p, config)]
        if not ssr_ps:
            continue
        cg_ps = [
            p for p in g.passengers
            if is_adult_caregiver(p)
        ]  # 留空规则旅客不能同时承担相邻陪护职责

        for p in ssr_ps:
            if deadline is not None and time.perf_counter() >= deadline:
                break
            # ---------- 情况1：SSR乘客已分配（可能由于预留座位） ----------
            if (g.group_id, p.hostnum) in context.assigned_seats:
                p_seat = context.seats[context.assigned_seats[(g.group_id, p.hostnum)]]
                satisfied = False
                # 检查是否已有相邻的看护者
                for cg in cg_ps:
                    if (g.group_id, cg.hostnum) in context.assigned_seats:
                        cg_seat = context.seats[context.assigned_seats[(g.group_id, cg.hostnum)]]
                        allow_cross = bool(get_ssr_rule(p.ssr, config).get('caregiver_allow_cross_aisle', False))
                        if cg_seat.seat_id in get_row_neighbor_ids(p_seat, allow_cross):
                            satisfied = True
                            break
                if satisfied:
                    continue

                # 尝试在已分配SSR旁边安排一个未分配的看护者
                best_cg = None
                best_cg_seat = None
                best_cg_score = float('inf')
                unassigned_cgs = [cg for cg in cg_ps if (g.group_id, cg.hostnum) not in context.assigned_seats]
                for cg in unassigned_cgs:
                    allow_cross = bool(get_ssr_rule(p.ssr, config).get('caregiver_allow_cross_aisle', False))
                    for adj_id in get_row_neighbor_ids(p_seat, allow_cross):
                        adj_seat = context.seats.get(adj_id)
                        if not adj_seat:
                            continue
                        if is_seat_feasible(adj_seat, cg, context.occupied, context.seat_ssr_map,
                                            context.blocked, context.seats, exclude_occupied={p_seat.seat_id}):
                            score = calc_seat_sort_key(cg, adj_seat, seat_values, weights, config,
                                                       context, g.group_id, old_seat_owners)
                            if score < best_cg_score:
                                best_cg_score = score
                                best_cg = cg
                                best_cg_seat = adj_id
                if best_cg:
                    context.assign_passenger(best_cg, best_cg_seat, g.group_id,
                                             exclude_block_seats={p_seat.seat_id})
                    continue

                # 如果还是无法配对，且该SSR不是预留座位，则移除其分配
                if not p.new_seat_num:
                    context.remove_assignment(g.group_id, p.hostnum)
                else:
                    continue  # 预留座位保留，即使暂时无法配对

            # ---------- 情况2：SSR乘客未分配 ----------
            # 计算当前组已分配座位的边界（用于紧凑度计算）
            group_assigned = [context.seats[sid] for (gid, hid), sid in context.assigned_seats.items()
                              if gid == g.group_id]
            old_comp = calc_group_compactness(group_assigned, config)

            # 2a. 优先尝试将SSR安排在已分配看护者旁边
            best_adj = None
            best_adj_score = float('inf')
            best_adj_cg_seat_id = None
            for cg in cg_ps:
                if (g.group_id, cg.hostnum) in context.assigned_seats:
                    cg_seat = context.seats[context.assigned_seats[(g.group_id, cg.hostnum)]]
                    allow_cross = bool(get_ssr_rule(p.ssr, config).get('caregiver_allow_cross_aisle', False))
                    for adj_id in get_row_neighbor_ids(cg_seat, allow_cross):
                        adj_seat = context.seats.get(adj_id)
                        if not adj_seat:
                            continue
                        if is_seat_feasible(adj_seat, p, context.occupied, context.seat_ssr_map,
                                            context.blocked, context.seats, exclude_occupied={cg_seat.seat_id}):
                            score = calc_seat_sort_key(p, adj_seat, seat_values, weights, config,
                                                       context, g.group_id, old_seat_owners)
                            # 加上紧凑度变化惩罚
                            if group_assigned:
                                new_comp = calc_group_compactness(group_assigned + [adj_seat], config)
                                score += -w_c * (new_comp - old_comp)
                            if score < best_adj_score:
                                best_adj_score = score
                                best_adj = adj_id
                                best_adj_cg_seat_id = cg_seat.seat_id
            if best_adj:
                if context.assign_passenger(p, best_adj, g.group_id,
                                            exclude_block_seats={best_adj_cg_seat_id}):
                    continue

            # 2b. 同时寻找一对相邻座位（SSR + 未分配看护者）
            unassigned_cgs = [cg for cg in cg_ps if (g.group_id, cg.hostnum) not in context.assigned_seats]
            if not unassigned_cgs:
                continue

            candidates = []
            feasible_count = 0
            # 遍历SSR的排序座位列表（最多考虑前500个可行座位）
            for seat_p in passenger_sorted_seats[(g.group_id, p.hostnum)]:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if not is_seat_feasible(seat_p, p, context.occupied, context.seat_ssr_map,
                                        context.blocked, context.seats):
                    continue

                feasible_count += 1
                p_score = calc_seat_sort_key(p, seat_p, seat_values, weights, config,
                                             context, g.group_id, old_seat_owners)
                # 检查每个相邻座位
                allow_cross = bool(get_ssr_rule(p.ssr, config).get('caregiver_allow_cross_aisle', False))
                for adj_id in get_row_neighbor_ids(seat_p, allow_cross):
                    adj_seat = context.seats.get(adj_id)
                    if not adj_seat:
                        continue

                    for cg in unassigned_cgs:
                        if not is_seat_feasible(adj_seat, cg, context.occupied, context.seat_ssr_map,
                                                context.blocked, context.seats, exclude_occupied={seat_p.seat_id}):
                            continue
                        cg_score = calc_seat_sort_key(cg, adj_seat, seat_values, weights, config,
                                                      context, g.group_id, old_seat_owners)

                        # 计算紧凑度变化
                        comp_penalty = 0.0
                        if group_assigned:
                            new_comp = calc_group_compactness(
                                group_assigned + [seat_p, adj_seat], config)
                            comp_penalty = -w_c * (new_comp - old_comp)

                        total_score = p_score + cg_score + comp_penalty
                        candidates.append((total_score, seat_p.seat_id, adj_id, cg))

                if feasible_count >= 500:
                    break

            candidates.sort(key=lambda x: x[0])
            for _, p_seat_id, cg_seat_id, cg in candidates:
                if (g.group_id, cg.hostnum) in context.assigned_seats:
                    continue  # 看护者可能已被先前的候选人分配
                # 先分配看护者，再分配SSR（互相排除对方的占用）
                if context.assign_passenger(cg, cg_seat_id, g.group_id, exclude_block_seats={p_seat_id}):
                    if context.assign_passenger(p, p_seat_id, g.group_id, exclude_block_seats={cg_seat_id}):
                        break
                    else:
                        # 分配SSR失败，回滚看护者
                        context.remove_assignment(g.group_id, cg.hostnum)
    return max(0, len(context.assigned_seats) - assigned_before)


def rescue_failed_paired_ssrs(
    groups: List[Group], context: AssignmentContext,
    passenger_sorted_seats: Dict[Tuple[int, int], List[Seat]],
    seat_values: Dict[str, float], weights: Dict[str, float],
    config: Dict[str, Any], old_seat_owners: Dict[str, Tuple[int, int]],
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """对阶段2遗留的配对SSR按组进行有限联合回溯，不移动其他组旅客。"""
    option_cap = int(config.get('algorithm', {}).get('paired_rescue_option_cap', 12))
    diagnostics = {'attempted': [], 'rescued': [], 'unresolved': []}
    passenger_by_key = {
        (active_group.group_id, active_passenger.hostnum):
            active_passenger
        for active_group in groups
        for active_passenger in active_group.passengers
    }

    def rebuild_incomplete_care_group(group: Group) -> bool:
        """联合重排一个未完成的陪护组，允许同一成人同时照顾两侧旅客。"""
        group_keys = [
            (group.group_id, passenger.hostnum)
            for passenger in group.passengers
        ]
        original = {
            key: context.assigned_seats[key]
            for key in group_keys
            if key in context.assigned_seats
        }
        for key in original:
            context.remove_assignment(*key)

        candidate_cap = max(
            24,
            int(
                config.get("algorithm", {}).get(
                    "paired_joint_rebuild_candidate_cap", 80
                )
            ),
        )
        node_limit = max(
            1,
            int(
                config.get("algorithm", {}).get(
                    "paired_joint_rebuild_node_limit", 50_000
                )
            ),
        )
        candidates = {}
        for passenger in group.passengers:
            if passenger.new_seat_num:
                fixed_seat = context.seats.get(passenger.new_seat_num)
                candidates[passenger.hostnum] = (
                    [fixed_seat] if fixed_seat is not None else []
                )
            else:
                candidates[passenger.hostnum] = passenger_sorted_seats[
                    (group.group_id, passenger.hostnum)
                ][:candidate_cap]
        adult_caregivers = [
            passenger
            for passenger in group.passengers
            if is_adult_caregiver(passenger)
        ]
        selected: Dict[int, str] = {}
        solution: Optional[Dict[int, str]] = None
        nodes = 0

        def care_requirements_satisfied() -> bool:
            return all(
                not passenger_requires_caregiver(passenger, config)
                or caregiver_satisfied(group, passenger, context, config)
                for passenger in group.passengers
            )

        def partial_care_position_feasible(
            passenger: Passenger,
            seat: Seat,
        ) -> bool:
            if len(adult_caregivers) != 1:
                return True
            sole_caregiver = adult_caregivers[0]
            if passenger is sole_caregiver:
                for cared in group.passengers:
                    cared_sid = selected.get(cared.hostnum)
                    if (
                        cared_sid is None
                        or not passenger_requires_caregiver(cared, config)
                    ):
                        continue
                    allow_cross = bool(
                        get_ssr_rule(cared.ssr, config).get(
                            "caregiver_allow_cross_aisle", False
                        )
                    )
                    if seat.seat_id not in get_row_neighbor_ids(
                        context.seats[cared_sid], allow_cross
                    ):
                        return False
            elif passenger_requires_caregiver(passenger, config):
                caregiver_sid = selected.get(sole_caregiver.hostnum)
                if caregiver_sid is not None:
                    allow_cross = bool(
                        get_ssr_rule(passenger.ssr, config).get(
                            "caregiver_allow_cross_aisle", False
                        )
                    )
                    if caregiver_sid not in get_row_neighbor_ids(
                        seat, allow_cross
                    ):
                        return False
            return True

        def search(remaining: List[Passenger]) -> None:
            nonlocal nodes, solution
            if solution is not None:
                return
            nodes += 1
            if (
                nodes > node_limit
                or (
                    deadline is not None
                    and time.perf_counter() >= deadline
                )
            ):
                return
            if not remaining:
                if care_requirements_satisfied():
                    solution = dict(selected)
                return

            feasible_by_passenger: List[
                Tuple[int, Passenger, List[Seat]]
            ] = []
            for passenger in remaining:
                feasible = [
                    seat
                    for seat in candidates[passenger.hostnum]
                    if is_seat_feasible(
                        seat,
                        passenger,
                        context.occupied,
                        context.seat_ssr_map,
                        context.blocked,
                        context.seats,
                    )
                    and partial_care_position_feasible(
                        passenger, seat
                    )
                ]
                feasible_by_passenger.append(
                    (len(feasible), passenger, feasible)
                )
            _, passenger, feasible_seats = min(
                feasible_by_passenger,
                key=lambda item: (
                    item[0],
                    not passenger_requires_caregiver(item[1], config),
                    item[1].hostnum,
                ),
            )
            if not feasible_seats:
                return

            next_remaining = [
                item for item in remaining if item is not passenger
            ]
            for seat in feasible_seats:
                if context.assign_passenger(
                    passenger, seat.seat_id, group.group_id
                ):
                    selected[passenger.hostnum] = seat.seat_id
                    search(next_remaining)
                    context.remove_assignment(
                        group.group_id, passenger.hostnum
                    )
                    del selected[passenger.hostnum]
                    if solution is not None:
                        return

        search(list(group.passengers))

        def restore(
            assignments: Mapping[Tuple[int, int], str],
        ) -> bool:
            restored = True
            ordered = sorted(
                assignments,
                key=lambda key: passenger_requires_caregiver(
                    passenger_by_key[key], config
                ),
            )
            for key in ordered:
                restored = (
                    context.assign_passenger(
                        passenger_by_key[key],
                        assignments[key],
                        key[0],
                    )
                    and restored
                )
            return restored

        if solution is None:
            restore(original)
            return False

        proposed = {
            (group.group_id, hostnum): seat_id
            for hostnum, seat_id in solution.items()
        }
        if restore(proposed):
            return True
        for key in proposed:
            if key in context.assigned_seats:
                context.remove_assignment(*key)
        restore(original)
        return False

    for group in groups:
        if deadline is not None and time.perf_counter() >= deadline:
            break
        fixed_unsatisfied = [
            p
            for p in group.passengers
            if (
                passenger_requires_caregiver(p, config)
                and p.new_seat_num
                and (group.group_id, p.hostnum)
                in context.assigned_seats
                and not caregiver_satisfied(
                    group, p, context, config
                )
            )
        ]
        for passenger in fixed_unsatisfied:
            key = (group.group_id, passenger.hostnum)
            diagnostics['attempted'].append(key)
            passenger_sid = context.assigned_seats[key]
            allow_cross = bool(
                get_ssr_rule(passenger.ssr, config).get(
                    'caregiver_allow_cross_aisle', False
                )
            )
            candidates = []
            owner_by_seat = {
                seat_id: owner_key
                for owner_key, seat_id
                in context.assigned_seats.items()
            }
            for caregiver in group.passengers:
                if not is_adult_caregiver(caregiver):
                    continue
                caregiver_key = (group.group_id, caregiver.hostnum)
                old_sid = context.assigned_seats.get(caregiver_key)
                if caregiver.new_seat_num and old_sid is not None:
                    continue
                for caregiver_sid in get_row_neighbor_ids(
                    context.seats[passenger_sid], allow_cross
                ):
                    displaced_key = owner_by_seat.get(caregiver_sid)
                    displaced_new_sid = None
                    if (
                        displaced_key is not None
                        and caregiver_sid != old_sid
                    ):
                        displaced = passenger_by_key[displaced_key]
                        if (
                            displaced.new_seat_num
                            or displaced.ssr
                            or displaced.need_cared
                            or displaced.need_both_empty
                            or displaced.need_single_empty
                        ):
                            continue
                        displaced_new_sid = next(
                            (
                                candidate.seat_id
                                for candidate in passenger_sorted_seats[
                                    displaced_key
                                ]
                                if (
                                    candidate.seat_id
                                    != caregiver_sid
                                    and is_seat_feasible(
                                        candidate,
                                        displaced,
                                        context.occupied,
                                        context.seat_ssr_map,
                                        context.blocked,
                                        context.seats,
                                    )
                                )
                            ),
                            None,
                        )
                        if displaced_new_sid is None:
                            continue
                    seat = context.seats[caregiver_sid]
                    score = calc_seat_sort_key(
                        caregiver, seat, seat_values,
                        weights, config, context, group.group_id,
                        old_seat_owners,
                    )
                    candidates.append(
                        (
                            score,
                            caregiver,
                            caregiver_sid,
                            old_sid,
                            displaced_key,
                            displaced_new_sid,
                        )
                    )

            rescued = False
            for (
                _,
                caregiver,
                caregiver_sid,
                old_sid,
                displaced_key,
                displaced_new_sid,
            ) in sorted(
                candidates, key=lambda item: item[0]
            ):
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if old_sid is not None:
                    context.remove_assignment(
                        group.group_id, caregiver.hostnum
                    )
                displaced = (
                    passenger_by_key[displaced_key]
                    if displaced_key is not None else None
                )
                if displaced_key is not None:
                    context.remove_assignment(*displaced_key)
                caregiver_ok = context.assign_passenger(
                    caregiver,
                    caregiver_sid,
                    group.group_id,
                    exclude_block_seats={passenger_sid},
                )
                displaced_ok = bool(
                    displaced is None
                    or (
                        caregiver_ok
                        and context.assign_passenger(
                            displaced,
                            displaced_new_sid,
                            displaced_key[0],
                        )
                    )
                )
                if caregiver_ok and displaced_ok:
                    rescued = True
                    diagnostics['rescued'].append(key)
                    break
                if caregiver_ok:
                    context.remove_assignment(
                        group.group_id, caregiver.hostnum
                    )
                if displaced is not None:
                    context.assign_passenger(
                        displaced, caregiver_sid, displaced_key[0]
                    )
                if old_sid is not None:
                    context.assign_passenger(
                        caregiver, old_sid, group.group_id
                    )
            if not rescued:
                diagnostics['unresolved'].append(key)

        tasks = [
            p for p in group.passengers
            if passenger_requires_caregiver(p, config)
            and (group.group_id, p.hostnum) not in context.assigned_seats
        ]
        if not tasks:
            continue
        diagnostics['attempted'].extend((group.group_id, p.hostnum) for p in tasks)
        best_plan: List[Tuple[Passenger, Passenger, str, Optional[str]]] = []
        best_cost = float('inf')
        nodes = 0

        def options_for(passenger: Passenger):
            options = []
            allow_cross = bool(get_ssr_rule(passenger.ssr, config).get('caregiver_allow_cross_aisle', False))
            caregivers = [
                p for p in group.passengers
                if is_adult_caregiver(p)
            ]
            for caregiver in caregivers:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                caregiver_key = (group.group_id, caregiver.hostnum)
                if caregiver_key in context.assigned_seats:
                    caregiver_sid = context.assigned_seats[caregiver_key]
                    caregiver_seat = context.seats[caregiver_sid]
                    for passenger_sid in get_row_neighbor_ids(caregiver_seat, allow_cross):
                        passenger_seat = context.seats[passenger_sid]
                        if is_seat_feasible(
                            passenger_seat, passenger, context.occupied,
                            context.seat_ssr_map, context.blocked, context.seats,
                            exclude_occupied={caregiver_sid},
                        ):
                            cost = calc_seat_sort_key(
                                passenger, passenger_seat, seat_values,
                                weights, config, context, group.group_id, old_seat_owners)
                            options.append((cost, caregiver, passenger_sid, None))
                    continue

                for passenger_seat in passenger_sorted_seats[(group.group_id, passenger.hostnum)]:
                    if (
                        deadline is not None
                        and time.perf_counter() >= deadline
                    ):
                        break
                    if not is_seat_feasible(
                        passenger_seat, passenger, context.occupied,
                        context.seat_ssr_map, context.blocked, context.seats,
                    ):
                        continue
                    for caregiver_sid in get_row_neighbor_ids(passenger_seat, allow_cross):
                        caregiver_seat = context.seats[caregiver_sid]
                        if not is_seat_feasible(
                            caregiver_seat, caregiver, context.occupied,
                            context.seat_ssr_map, context.blocked, context.seats,
                            exclude_occupied={passenger_seat.seat_id},
                        ):
                            continue
                        cost = (
                            calc_seat_sort_key(
                                passenger, passenger_seat, seat_values,
                                weights, config, context, group.group_id, old_seat_owners)
                            + calc_seat_sort_key(
                                caregiver, caregiver_seat, seat_values,
                                weights, config, context, group.group_id, old_seat_owners)
                        )
                        options.append((cost, caregiver, passenger_seat.seat_id, caregiver_sid))
                    if len(options) >= option_cap * 3:
                        break
            return heapq.nsmallest(option_cap, options, key=lambda item: item[0])

        def search(index: int, plan, cost: float):
            nonlocal best_plan, best_cost, nodes
            nodes += 1
            if (
                nodes > 1000
                or (
                    deadline is not None
                    and time.perf_counter() >= deadline
                )
            ):
                return
            if index >= len(tasks):
                if len(plan) > len(best_plan) or (len(plan) == len(best_plan) and cost < best_cost):
                    best_plan = list(plan)
                    best_cost = cost
                return
            passenger = tasks[index]
            # 跳过分支保证无完整方案时仍返回最大可配对数。
            search(index + 1, plan, cost)
            for option_cost, caregiver, passenger_sid, caregiver_sid in options_for(passenger):
                assigned_caregiver_now = caregiver_sid is not None
                if assigned_caregiver_now:
                    if not context.assign_passenger(
                        caregiver, caregiver_sid, group.group_id,
                        exclude_block_seats={passenger_sid}):
                        continue
                    actual_caregiver_sid = caregiver_sid
                else:
                    actual_caregiver_sid = context.assigned_seats[(group.group_id, caregiver.hostnum)]
                if context.assign_passenger(
                    passenger, passenger_sid, group.group_id,
                    exclude_block_seats={actual_caregiver_sid}):
                    plan.append((passenger, caregiver, passenger_sid, caregiver_sid))
                    search(index + 1, plan, cost + option_cost)
                    plan.pop()
                    context.remove_assignment(group.group_id, passenger.hostnum)
                if assigned_caregiver_now:
                    context.remove_assignment(group.group_id, caregiver.hostnum)

        search(0, [], 0.0)
        for passenger, caregiver, passenger_sid, caregiver_sid in best_plan:
            if caregiver_sid is not None:
                context.assign_passenger(
                    caregiver, caregiver_sid, group.group_id,
                    exclude_block_seats={passenger_sid})
            actual_caregiver_sid = context.assigned_seats[(group.group_id, caregiver.hostnum)]
            context.assign_passenger(
                passenger, passenger_sid, group.group_id,
                exclude_block_seats={actual_caregiver_sid})
            diagnostics['rescued'].append((group.group_id, passenger.hostnum))

        remaining_tasks = [
            passenger
            for passenger in tasks
            if (
                group.group_id,
                passenger.hostnum,
            ) not in context.assigned_seats
        ]
        if remaining_tasks and rebuild_incomplete_care_group(group):
            diagnostics.setdefault("joint_rebuilds", 0)
            diagnostics["joint_rebuilds"] += 1
            diagnostics["rescued"].extend(
                (group.group_id, passenger.hostnum)
                for passenger in remaining_tasks
            )

        for passenger in tasks:
            key = (group.group_id, passenger.hostnum)
            if key not in context.assigned_seats:
                diagnostics['unresolved'].append(key)
        for passenger in fixed_unsatisfied:
            key = (group.group_id, passenger.hostnum)
            if (
                not caregiver_satisfied(
                    group, passenger, context, config
                )
                and key not in diagnostics['unresolved']
            ):
                diagnostics['unresolved'].append(key)
    diagnostics['attempted_count'] = len(diagnostics['attempted'])
    diagnostics['rescued_count'] = len(diagnostics['rescued'])
    diagnostics['unresolved_count'] = len(diagnostics['unresolved'])
    return diagnostics


def assign_remaining_passengers(groups: List[Group], context: AssignmentContext,
                                passenger_sorted_seats: Dict[Tuple[int, int], List[Seat]],
                                weights: Dict[str, float], config: Dict[str, Any],
                                seat_values: Dict[str, float],
                                old_seat_owners: Dict[str, Tuple[int, int]],
                                global_deadline: Optional[float] = None
                                ) -> Dict[str, Any]:
    """
    阶段3：分配剩余乘客（非SSR且尚未分配）。
    使用有限束搜索（beam search）以组为单位进行优化，考虑组内紧凑度。
    """
    w_c = weights.get('w_c', -2.0)
    seat_adj_map = {
        s_id: [adj for adj in get_adjacent_seat_ids(seat) if adj in context.seats]
        for s_id, seat in context.seats.items()
    }
    seat_both_adj_map = {
        s_id: [adj for adj in get_both_side_neighbor_ids(seat) if adj in context.seats]
        for s_id, seat in context.seats.items()
    }
    # 给阶段3设置独立时间预算；接近截止时间时保留当前最好状态并转入下一组。
    stage3_budget = float(config.get('algorithm', {}).get('stage3_time_budget', 20.0))
    deadline = time.perf_counter() + max(0.0, stage3_budget)
    if global_deadline is not None:
        deadline = min(deadline, global_deadline)
    diagnostics = {
        "groups_considered": 0,
        "dfs_attempted": 0,
        "dfs_succeeded": 0,
        "dfs_nodes": 0,
        "dfs_seconds": 0.0,
        "beam_groups": 0,
        "transaction_failures": 0,
    }

    # 组优先级：已有成员分配的组优先处理
    def group_priority(g: Group) -> Tuple[int, int, int, int, int]:
        assigned_count = sum(1 for p in g.passengers if (g.group_id, p.hostnum) in context.assigned_seats)
        remaining = [
            p
            for p in g.passengers
            if (
                not passenger_requires_caregiver(p, config)
                and (g.group_id, p.hostnum)
                not in context.assigned_seats
            )
        ]
        domain_sizes = [
            sum(
                is_seat_feasible(
                    seat,
                    passenger,
                    context.occupied,
                    context.seat_ssr_map,
                    context.blocked,
                    context.seats,
                )
                for seat in passenger_sorted_seats[
                    (g.group_id, passenger.hostnum)
                ]
            )
            for passenger in remaining
        ]
        protected_demand = sum(
            2 if p.need_both_empty else 1 if p.need_single_empty else 0
            for p in remaining
        )
        caregiver_demand = sum(
            passenger_requires_caregiver(p, config)
            for p in g.passengers
        )
        return (
            0 if assigned_count > 0 else 1,  # 已有分配优先
            min(domain_sizes, default=10**9),
            -protected_demand,
            -caregiver_demand,
            -len(g.passengers),
        )

    sorted_groups = sorted(groups, key=group_priority)

    for g in sorted_groups:
        # 需要陪护的SSR只通过配对/联合搜索提交，避免产生无陪护的硬约束违规。
        unassigned_ps = [p for p in g.passengers
                         if not passenger_requires_caregiver(p, config)
                         and (g.group_id, p.hostnum) not in context.assigned_seats]
        if not unassigned_ps:
            continue
        diagnostics["groups_considered"] += 1

        # 排序：先处理约束强的（需要空侧的），再按原座位位置
        def get_old_seat_pos(p):
            if p.old_seat_num in context.seats:
                s = context.seats[p.old_seat_num]
                return (s.row, seat_label_number(s.col))
            return (999, 999)

        unassigned_ps.sort(key=lambda p: (
            not p.need_both_empty,
            not p.need_single_empty,
            get_old_seat_pos(p)
        ))

        # 为每个乘客预计算每个座位的可行性及基础分数（缓存以加速）
        base_k_cache = {}
        candidate_rankings: Dict[int, List[Tuple[float, str]]] = {}
        # 静态候选剪枝：每位乘客只保留基础评分最好的若干座位。
        candidate_cap = int(config.get('algorithm', {}).get('candidate_cap', 48))
        candidate_cap_retry = max(
            candidate_cap,
            int(
                config.get('algorithm', {}).get(
                    'candidate_cap_retry', max(64, candidate_cap * 2)
                )
            ),
        )
        candidate_cap_full_retry = max(
            candidate_cap_retry,
            int(
                config.get('algorithm', {}).get(
                    'candidate_cap_full_retry',
                    max(128, candidate_cap_retry * 2),
                )
            ),
        )
        for p in unassigned_ps:
            candidates = []
            for s in context.seats.values():
                if s.seat_id in context.occupied or context.blocked_counts.get(s.seat_id, 0) > 0:
                    continue
                if is_seat_feasible(s, p, context.occupied, context.seat_ssr_map,
                                    context.blocked, context.seats):
                    k = calc_seat_sort_key(
                        p, s, seat_values, weights, config,
                        context, g.group_id, old_seat_owners)
                    candidates.append((k, s.seat_id))
            ranked = heapq.nsmallest(
                candidate_cap_full_retry,
                candidates,
                key=lambda x: x[0],
            )
            candidate_rankings[p.hostnum] = ranked
            base_k_cache[p.hostnum] = {
                sid: k for k, sid in ranked[:candidate_cap]
            }

        def construction_regret(passenger: Passenger) -> float:
            costs = sorted(base_k_cache[passenger.hostnum].values())
            return costs[1] - costs[0] if len(costs) >= 2 else float("inf")

        unassigned_ps.sort(key=lambda p: (
            not p.need_both_empty,
            not p.need_single_empty,
            len(base_k_cache[p.hostnum]),
            -construction_regret(p),
            get_old_seat_pos(p),
        ))

        # 计算组内已分配座位的初始边界（用于紧凑度）
        initial_group_seat_ids: List[str] = []
        for gp in g.passengers:
            if (g.group_id, gp.hostnum) in context.assigned_seats:
                s_id = context.assigned_seats[(g.group_id, gp.hostnum)]
                if s_id in context.seats:
                    initial_group_seat_ids.append(s_id)

        # Beam state 定义
        class BeamState:
            __slots__ = ['assigned_dict', 'occupied', 'blocked_counts',
                         'local_ssr_map', 'score',
                         'assigned_count', 'comp']
            def __init__(self, assigned_dict, occupied, blocked_counts,
                         local_ssr_map, score,
                         assigned_count):
                self.assigned_dict = assigned_dict      # {hostnum: (seat_id, chosen_block)}
                self.occupied = occupied                 # 占用座位集合（包含本次束搜索临时占用）
                self.blocked_counts = blocked_counts     # 锁定计数
                self.local_ssr_map = local_ssr_map
                self.score = score                       # 基础分数累积
                self.assigned_count = assigned_count     # 已分配人数
                compact_seat_ids = initial_group_seat_ids + [
                    seat_id for seat_id, _ in assigned_dict.values()
                ]
                self.comp = calc_group_compactness(
                    [context.seats[sid] for sid in compact_seat_ids if sid in context.seats],
                    config,
                )

        # 初始状态：无人分配，继承当前全局上下文（但复制了占用和锁定，不影响外部）
        initial_state = BeamState(
            assigned_dict={},
            occupied=context.occupied.copy(),
            blocked_counts=context.blocked_counts.copy(),
            local_ssr_map={},
            score=0.0,
            assigned_count=0,
        )
        beam = [initial_state]

        def local_ssr_feasible(
            passenger: Passenger,
            seat_id: str,
            local_ssr_map: Dict[str, Passenger],
        ) -> bool:
            """检查同一 Beam 分支中新加入的 SSR 排/小排约束。"""
            if not passenger.ssr:
                return True
            seat = context.seats[seat_id]
            located = [
                (context.seats[assigned_id], assigned_passenger)
                for assigned_id, assigned_passenger
                in local_ssr_map.items()
                if assigned_passenger.ssr
            ]
            for same_location, own_flag, flag_name in (
                (
                    lambda assigned_seat: (
                        assigned_seat.row == seat.row
                        and same_subrow(seat, assigned_seat)
                    ),
                    passenger.same_subrow_no_other,
                    "same_subrow_no_other",
                ),
                (
                    lambda assigned_seat: assigned_seat.row == seat.row,
                    passenger.same_row_no_other,
                    "same_row_no_other",
                ),
            ):
                local_passengers = [
                    assigned_passenger
                    for assigned_seat, assigned_passenger in located
                    if same_location(assigned_seat)
                ]
                if own_flag or any(
                    bool(getattr(p, flag_name))
                    for p in local_passengers
                ):
                    counts: Dict[str, int] = defaultdict(int)
                    for p in local_passengers:
                        counts[p.ssr] += 1
                    counts[passenger.ssr] += 1
                    if any(count > 1 for count in counts.values()):
                        return False
            return True

        def commit_group_candidate(
            assignments: Dict[int, Tuple[str, Optional[str]]],
        ) -> bool:
            committed: List[int] = []
            passenger_by_host = {
                passenger.hostnum: passenger
                for passenger in unassigned_ps
            }
            for hostnum, (seat_id, chosen_block) in assignments.items():
                passenger = passenger_by_host[hostnum]
                if not context.assign_passenger(
                    passenger,
                    seat_id,
                    g.group_id,
                    chosen_block=chosen_block,
                ):
                    for committed_host in reversed(committed):
                        context.remove_assignment(
                            g.group_id, committed_host
                        )
                    diagnostics["transaction_failures"] += 1
                    return False
                committed.append(hostnum)
            return True

        group_size = len(unassigned_ps)
        algorithm = config.get('algorithm', {})
        dfs_enabled = bool(
            algorithm.get('small_group_dfs_enabled', True)
        )
        dfs_max_size = max(
            1, int(algorithm.get('small_group_dfs_max_size', 4))
        )
        dfs_node_limit = max(
            1, int(algorithm.get('small_group_dfs_node_limit', 20_000))
        )
        dfs_time_limit = max(
            0.0,
            float(
                algorithm.get('small_group_dfs_time_limit', 0.02)
            ),
        )
        dfs_solution: Optional[
            Dict[int, Tuple[str, Optional[str]]]
        ] = None

        if (
            dfs_enabled
            and group_size <= dfs_max_size
            and dfs_time_limit > 0.0
            and time.perf_counter() < deadline
        ):
            diagnostics["dfs_attempted"] += 1
            dfs_started = time.perf_counter()
            dfs_deadline = min(
                deadline, time.perf_counter() + dfs_time_limit
            )
            dfs_nodes = 0
            best_dfs_cost = float('inf')
            selected: Dict[int, Tuple[str, Optional[str]]] = {}
            used = set(context.occupied)
            blocked_counts = context.blocked_counts.copy()
            local_ssr_map: Dict[str, Passenger] = {}

            def fixed_caregivers_satisfied() -> bool:
                seat_by_host = {
                    passenger.hostnum: context.assigned_seats.get(
                        (g.group_id, passenger.hostnum)
                    )
                    for passenger in g.passengers
                }
                seat_by_host.update({
                    hostnum: assignment[0]
                    for hostnum, assignment in selected.items()
                })
                for cared in g.passengers:
                    cared_seat = seat_by_host.get(cared.hostnum)
                    if (
                        cared_seat is None
                        or not passenger_requires_caregiver(
                            cared, config
                        )
                    ):
                        continue
                    allow_cross = bool(
                        get_ssr_rule(cared.ssr, config).get(
                            'caregiver_allow_cross_aisle', False
                        )
                    )
                    neighbors = set(
                        get_row_neighbor_ids(
                            context.seats[cared_seat], allow_cross
                        )
                    )
                    if not any(
                        is_adult_caregiver(other)
                        and seat_by_host.get(other.hostnum) in neighbors
                        for other in g.passengers
                    ):
                        return False
                return True

            def dfs_search(depth: int, base_cost: float) -> None:
                nonlocal dfs_nodes, best_dfs_cost, dfs_solution
                dfs_nodes += 1
                if (
                    dfs_nodes > dfs_node_limit
                    or time.perf_counter() >= dfs_deadline
                ):
                    return
                if depth >= group_size:
                    if not fixed_caregivers_satisfied():
                        return
                    seat_ids = initial_group_seat_ids + [
                        assignment[0]
                        for assignment in selected.values()
                    ]
                    compactness = calc_group_compactness(
                        [context.seats[sid] for sid in seat_ids],
                        config,
                    )
                    total_cost = base_cost - w_c * compactness
                    if total_cost < best_dfs_cost:
                        best_dfs_cost = total_cost
                        dfs_solution = dict(selected)
                    return

                passenger = unassigned_ps[depth]
                for seat_cost, seat_id in candidate_rankings[
                    passenger.hostnum
                ][:active_dfs_candidate_cap]:
                    if (
                        seat_id in used
                        or blocked_counts.get(seat_id, 0) > 0
                        or not local_ssr_feasible(
                            passenger, seat_id, local_ssr_map
                        )
                    ):
                        continue
                    if passenger.need_both_empty:
                        neighbors = seat_both_adj_map[seat_id]
                        if any(
                            neighbor in used
                            or blocked_counts.get(neighbor, 0) > 0
                            for neighbor in neighbors
                        ):
                            continue
                        block_choices = [neighbors]
                    elif passenger.need_single_empty:
                        available = [
                            neighbor
                            for neighbor in seat_adj_map[seat_id]
                            if (
                                neighbor not in used
                                and blocked_counts.get(neighbor, 0) == 0
                            )
                        ]
                        block_choices = [[neighbor] for neighbor in available]
                    else:
                        block_choices = [[]]

                    for block_choice in block_choices:
                        used.add(seat_id)
                        for blocked_seat in block_choice:
                            blocked_counts[blocked_seat] = (
                                blocked_counts.get(blocked_seat, 0) + 1
                            )
                        if passenger.ssr:
                            local_ssr_map[seat_id] = passenger
                        selected[passenger.hostnum] = (
                            seat_id,
                            (
                                block_choice[0]
                                if passenger.need_single_empty
                                and block_choice
                                else None
                            ),
                        )
                        dfs_search(
                            depth + 1,
                            base_cost + seat_cost,
                        )
                        del selected[passenger.hostnum]
                        if passenger.ssr:
                            del local_ssr_map[seat_id]
                        for blocked_seat in block_choice:
                            blocked_counts[blocked_seat] -= 1
                            if blocked_counts[blocked_seat] <= 0:
                                del blocked_counts[blocked_seat]
                        used.remove(seat_id)

            retry_caps = list(dict.fromkeys([
                candidate_cap,
                candidate_cap_retry,
                candidate_cap_full_retry,
            ]))
            for active_dfs_candidate_cap in retry_caps:
                dfs_search(0, 0.0)
                if dfs_solution is not None:
                    break
                if (
                    dfs_nodes > dfs_node_limit
                    or time.perf_counter() >= dfs_deadline
                ):
                    break
            diagnostics["dfs_nodes"] += dfs_nodes
            diagnostics["dfs_seconds"] += (
                time.perf_counter() - dfs_started
            )

        if dfs_solution is not None and commit_group_candidate(
            dfs_solution
        ):
            diagnostics["dfs_succeeded"] += 1
            continue

        # 自适应束宽：小组保留更多搜索，大组优先保证运行时间。
        diagnostics["beam_groups"] += 1
        if group_size <= 2:
            B = int(algorithm.get("beam_width_small", 24))
            N = int(algorithm.get("beam_moves_small", 20))
        elif group_size <= 5:
            B = int(algorithm.get("beam_width_medium", 40))
            N = int(algorithm.get("beam_moves_medium", 20))
        else:
            B = int(algorithm.get("beam_width_large", 96))
            N = int(algorithm.get("beam_moves_large", 28))
        B = max(1, B)
        N = max(1, N)

        def get_eval_score(st: BeamState) -> Tuple[int, float]:
            return (
                len(unassigned_ps) - st.assigned_count,
                st.score - w_c * st.comp,
            )

        # 逐个乘客进行束搜索
        for p in unassigned_ps:
            if time.perf_counter() >= deadline:
                break
            new_beam = []
            for state in beam:
                # 选项1：不分配该乘客（跳过）
                new_beam.append(BeamState(
                    assigned_dict=state.assigned_dict,
                    occupied=state.occupied,
                    blocked_counts=state.blocked_counts,
                    local_ssr_map=state.local_ssr_map,
                    score=state.score,
                    assigned_count=state.assigned_count,
                ))

                # 收集可行座位
                feasible_moves = []
                for s_id, base_k in base_k_cache[p.hostnum].items():
                    if s_id in state.occupied or state.blocked_counts.get(s_id, 0) > 0:
                        continue
                    if not local_ssr_feasible(
                        p, s_id, state.local_ssr_map
                    ):
                        continue
                    # 检查空侧约束在当前局部状态下的可行性
                    feasible = True
                    if p.need_both_empty:
                        for adj in seat_both_adj_map[s_id]:
                            if adj in state.occupied or state.blocked_counts.get(adj, 0) > 0:
                                feasible = False
                                break
                    if feasible and p.need_single_empty:
                        adj_ids = seat_adj_map[s_id]
                        if not adj_ids:
                            feasible = False
                        else:
                            has_empty = False
                            for adj in adj_ids:
                                if adj not in state.occupied and state.blocked_counts.get(adj, 0) == 0:
                                    has_empty = True
                                    break
                            if not has_empty:
                                feasible = False
                    if feasible:
                        # 计算紧凑度变化
                        comp_penalty = 0.0
                        current_ids = initial_group_seat_ids + [
                            seat_id for seat_id, _ in state.assigned_dict.values()
                        ]
                        new_comp = calc_group_compactness(
                            [context.seats[sid] for sid in current_ids + [s_id]],
                            config,
                        )
                        comp_penalty = -w_c * (new_comp - state.comp)
                        feasible_moves.append((
                            base_k + comp_penalty,
                            s_id,
                            base_k,
                        ))

                # 只提取前N项，避免对全部候选做完整排序。
                best_moves = heapq.nsmallest(N, feasible_moves, key=lambda x: x[0])
                for _, s_id, base_k in best_moves:
                    seat = context.seats[s_id]
                    new_occupied = state.occupied.copy()
                    new_occupied.add(s_id)

                    # 根据空侧约束生成锁定的选项
                    blocks_to_add_list = []
                    if p.need_both_empty:
                        blks = [adj for adj in seat_both_adj_map[s_id] if adj not in new_occupied]
                        blocks_to_add_list = [blks]
                    elif p.need_single_empty:
                        available_adjs = [adj for adj in seat_adj_map[s_id]
                                          if adj not in new_occupied
                                          and state.blocked_counts.get(adj, 0) == 0]
                        # 每个可用的相邻座位都是一个分支
                        blocks_to_add_list = [[adj] for adj in available_adjs]
                    else:
                        blocks_to_add_list = [[]]

                    for block_choice in blocks_to_add_list:
                        temp_blocked_counts = state.blocked_counts.copy()
                        for b in block_choice:
                            temp_blocked_counts[b] = temp_blocked_counts.get(b, 0) + 1

                        new_assigned_dict = state.assigned_dict.copy()
                        chosen_block = block_choice[0] if p.need_single_empty and block_choice else None
                        new_assigned_dict[p.hostnum] = (s_id, chosen_block)
                        new_local_ssr_map = state.local_ssr_map
                        if p.ssr:
                            new_local_ssr_map = state.local_ssr_map.copy()
                            new_local_ssr_map[s_id] = p

                        new_state = BeamState(
                            assigned_dict=new_assigned_dict,
                            occupied=new_occupied,
                            blocked_counts=temp_blocked_counts,
                            local_ssr_map=new_local_ssr_map,
                            score=state.score + base_k,
                            assigned_count=state.assigned_count + 1,
                        )
                        new_beam.append(new_state)

            # 相同资源和 SSR 状态只保留字典序目标更好的路径。
            dominant_states: Dict[tuple, BeamState] = {}
            for st in new_beam:
                state_key = (
                    frozenset(st.occupied),
                    frozenset(st.blocked_counts),
                    tuple(sorted(
                        (
                            seat_id,
                            passenger.ssr,
                            passenger.same_subrow_no_other,
                            passenger.same_row_no_other,
                        )
                        for seat_id, passenger
                        in st.local_ssr_map.items()
                    )),
                )
                previous = dominant_states.get(state_key)
                if (
                    previous is None
                    or get_eval_score(st) < get_eval_score(previous)
                ):
                    dominant_states[state_key] = st
            unique_beam = list(dominant_states.values())

            # 评估状态：基础分数 - w_c * 紧凑度 + 未分配人数惩罚
            beam = heapq.nsmallest(B, unique_beam, key=get_eval_score)
            if not beam:
                beam = [initial_state]

        # 事务化提交；任一步失败都撤销本状态已提交的旅客，再尝试下一状态。
        for candidate_state in sorted(beam, key=get_eval_score):
            if commit_group_candidate(
                candidate_state.assigned_dict
            ):
                break
    return diagnostics


def build_blocked_by(context: AssignmentContext) -> Dict[str, List[Tuple[int, int]]]:
    """把“旅客 -> 为其留空的座位”反转为便于输出和可视化的映射。"""
    blocked_by: Dict[str, List[Tuple[int, int]]] = {}
    for passenger_key, seat_ids in context.assigned_blocked.items():
        for seat_id in seat_ids:
            blocked_by.setdefault(seat_id, []).append(passenger_key)
    return blocked_by


def repair_unassigned_by_local_relocation(
    groups: List[Group],
    context: AssignmentContext,
    passenger_sorted_seats: Dict[Tuple[int, int], List[Seat]],
    config: Dict[str, Any],
    global_deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """
    通过小规模重排修复贪婪阶段遗留的未分配旅客。

    高客座率下，普通旅客可能先占用单/双侧留空所需的连续资源块。这里
    只搬动非固定、无 SSR、无留空规则且当前不承担陪护职责的普通旅客，
    为未分配旅客释放完整资源块，再用有界 DFS 将被搬旅客回填到其他
    空座。固定座位和所有复杂硬约束旅客均不参与搬动。
    """
    algorithm = config.get("algorithm", {})
    node_limit = max(0, int(algorithm.get("final_repair_node_limit", 20_000)))
    time_limit = max(
        0.0, float(algorithm.get("final_repair_time_limit", 2.0))
    )
    diagnostics = {
        "attempted": 0,
        "repaired": 0,
        "unresolved": 0,
        "search_nodes": 0,
        "seconds": 0.0,
    }
    if node_limit == 0 or time_limit == 0.0:
        return diagnostics

    started = time.perf_counter()
    deadline = started + time_limit
    if global_deadline is not None:
        deadline = min(deadline, global_deadline)
    passenger_by_key = {
        (group.group_id, passenger.hostnum): passenger
        for group in groups
        for passenger in group.passengers
    }

    # 已经为同行 SSR 提供相邻位置的普通旅客不能搬走，否则会修复一个
    # 未分配问题却破坏陪护硬约束。
    active_caregivers: Set[Tuple[int, int]] = set()
    for group in groups:
        for passenger in group.passengers:
            key = (group.group_id, passenger.hostnum)
            seat_id = context.assigned_seats.get(key)
            if (
                seat_id is None
                or not passenger_requires_caregiver(passenger, config)
            ):
                continue
            allow_cross = bool(
                get_ssr_rule(passenger.ssr, config).get(
                    "caregiver_allow_cross_aisle", False
                )
            )
            neighbors = set(
                get_row_neighbor_ids(context.seats[seat_id], allow_cross)
            )
            for caregiver in group.passengers:
                caregiver_key = (group.group_id, caregiver.hostnum)
                if (
                    not caregiver.ssr
                    and not caregiver.need_both_empty
                    and not caregiver.need_single_empty
                    and context.assigned_seats.get(caregiver_key) in neighbors
                ):
                    active_caregivers.add(caregiver_key)

    def movable(key: Tuple[int, int]) -> bool:
        passenger = passenger_by_key[key]
        return (
            passenger.new_seat_num is None
            and not passenger.ssr
            and not passenger.need_cared
            and not passenger.need_both_empty
            and not passenger.need_single_empty
            and key not in active_caregivers
        )

    def relocate(
        movers: List[Tuple[Tuple[int, int], str]], index: int,
        final_validator: Optional[Callable[[], bool]] = None,
    ) -> bool:
        if index >= len(movers):
            return final_validator is None or final_validator()
        if (
            diagnostics["search_nodes"] >= node_limit
            or time.perf_counter() >= deadline
        ):
            return False

        # 动态选择当前合法座位最少的搬迁旅客。满载时固定顺序很容易先让
        # 普通旅客占掉摇篮/陪护等稀缺资源，并在深层才发现死路；MRV让
        # 完整性修复优先处理真正受限的旅客。
        best_position = index
        best_candidates: Optional[List[Seat]] = None
        for candidate_position in range(index, len(movers)):
            candidate_key = movers[candidate_position][0]
            candidate_passenger = passenger_by_key[candidate_key]
            feasible = [
                seat
                for seat in passenger_sorted_seats.get(
                    candidate_key, context.seats.values()
                )
                if is_seat_feasible(
                    seat,
                    candidate_passenger,
                    context.occupied,
                    context.seat_ssr_map,
                    context.blocked,
                    context.seats,
                )
            ]
            if not feasible:
                return False
            if best_candidates is None or len(feasible) < len(best_candidates):
                best_position = candidate_position
                best_candidates = feasible
        movers[index], movers[best_position] = (
            movers[best_position], movers[index]
        )
        key, _ = movers[index]
        passenger = passenger_by_key[key]
        for seat in best_candidates or ():
            diagnostics["search_nodes"] += 1
            if context.assign_passenger(
                passenger, seat.seat_id, key[0]
            ):
                if relocate(movers, index + 1, final_validator):
                    movers[index], movers[best_position] = (
                        movers[best_position], movers[index]
                    )
                    return True
                context.remove_assignment(*key)
            if (
                diagnostics["search_nodes"] >= node_limit
                or time.perf_counter() >= deadline
            ):
                break
        movers[index], movers[best_position] = (
            movers[best_position], movers[index]
        )
        return False

    missing_keys = [
        key for key in passenger_by_key if key not in context.assigned_seats
    ]

    def repair_missing_caregiver(
        missing_key: Tuple[int, int], missing: Passenger,
    ) -> bool:
        """Open an adjacent seat pair and relocate its ordinary occupants."""
        group = next(
            group for group in groups if group.group_id == missing_key[0]
        )
        caregivers = [
            passenger for passenger in group.passengers
            if is_adult_caregiver(passenger)
            and passenger.new_seat_num is None
        ]
        allow_cross = bool(
            get_ssr_rule(missing.ssr, config).get(
                "caregiver_allow_cross_aisle", False
            )
        )
        for missing_seat in passenger_sorted_seats.get(
            missing_key, context.seats.values()
        ):
            if time.perf_counter() >= deadline:
                return False
            for caregiver in caregivers:
                caregiver_key = (missing_key[0], caregiver.hostnum)
                original_caregiver_seat = context.assigned_seats.get(
                    caregiver_key
                )
                for caregiver_seat_id in get_row_neighbor_ids(
                    missing_seat, allow_cross
                ):
                    if time.perf_counter() >= deadline:
                        return False
                    owner_by_seat = {
                        seat_id: key
                        for key, seat_id in context.assigned_seats.items()
                    }
                    conflict_keys = {
                        owner_by_seat[seat_id]
                        for seat_id in (
                            missing_seat.seat_id, caregiver_seat_id
                        )
                        if seat_id in owner_by_seat
                        and owner_by_seat[seat_id] != caregiver_key
                    }
                    # A missing cared passenger can require permuting the
                    # entire care group (for example BLND currently occupies
                    # the only bassinet seat needed by BSCT).  Release every
                    # non-fixed, non-protection member of that group so the
                    # relocation DFS can perform this internal permutation.
                    conflict_keys.update(
                        key
                        for key in context.assigned_seats
                        if key[0] == missing_key[0]
                        and key not in (missing_key, caregiver_key)
                        and passenger_by_key[key].new_seat_num is None
                        and not passenger_by_key[key].need_both_empty
                        and not passenger_by_key[key].need_single_empty
                    )
                    def care_group_movable(key: Tuple[int, int]) -> bool:
                        passenger = passenger_by_key[key]
                        return (
                            movable(key)
                            or (
                                key[0] == missing_key[0]
                                and passenger.new_seat_num is None
                                and not passenger.need_both_empty
                                and not passenger.need_single_empty
                            )
                        )

                    if any(
                        not care_group_movable(key)
                        for key in conflict_keys
                    ):
                        continue
                    movers = [
                        (key, context.assigned_seats[key])
                        for key in sorted(
                            conflict_keys,
                            key=lambda candidate_key: (
                                len(passenger_sorted_seats.get(
                                    candidate_key, ()
                                )),
                                not passenger_requires_caregiver(
                                    passenger_by_key[candidate_key], config
                                ),
                                not bool(
                                    passenger_by_key[candidate_key].ssr
                                ),
                                candidate_key,
                            ),
                        )
                    ]
                    if original_caregiver_seat is not None:
                        context.remove_assignment(*caregiver_key)
                    for key, _ in movers:
                        context.remove_assignment(*key)

                    caregiver_ok = context.assign_passenger(
                        caregiver,
                        caregiver_seat_id,
                        missing_key[0],
                        exclude_block_seats={missing_seat.seat_id},
                    )
                    missing_ok = bool(
                        caregiver_ok
                        and context.assign_passenger(
                            missing,
                            missing_seat.seat_id,
                            missing_key[0],
                            exclude_block_seats={caregiver_seat_id},
                        )
                    )
                    def all_care_satisfied() -> bool:
                        return all(
                            not passenger_requires_caregiver(
                                passenger, config
                            )
                            or caregiver_satisfied(
                                group, passenger, context, config
                            )
                            for passenger in group.passengers
                            if (
                                group.group_id, passenger.hostnum
                            ) in context.assigned_seats
                        )
                    if missing_ok and relocate(
                        movers, 0, all_care_satisfied
                    ):
                        return True

                    if missing_key in context.assigned_seats:
                        context.remove_assignment(*missing_key)
                    if caregiver_key in context.assigned_seats:
                        context.remove_assignment(*caregiver_key)
                    for key, _ in movers:
                        if key in context.assigned_seats:
                            context.remove_assignment(*key)
                    for key, original_seat in movers:
                        if not context.assign_passenger(
                            passenger_by_key[key], original_seat, key[0]
                        ):
                            raise RuntimeError(
                                "陪护局部重排回滚失败: "
                                f"groupId={key[0]}, hostnum={key[1]}"
                            )
                    if original_caregiver_seat is not None:
                        if not context.assign_passenger(
                            caregiver,
                            original_caregiver_seat,
                            caregiver_key[0],
                        ):
                            raise RuntimeError(
                                "陪护者回滚失败: "
                                f"groupId={caregiver_key[0]}, "
                                f"hostnum={caregiver_key[1]}"
                            )
        return False

    for missing_key in missing_keys:
        if (
            diagnostics["search_nodes"] >= node_limit
            or time.perf_counter() >= deadline
        ):
            break
        diagnostics["attempted"] += 1
        missing = passenger_by_key[missing_key]
        if passenger_requires_caregiver(missing, config):
            if repair_missing_caregiver(missing_key, missing):
                diagnostics["repaired"] += 1
            continue
        repaired = False

        for seat in passenger_sorted_seats.get(
            missing_key, context.seats.values()
        ):
            block_options: List[Optional[str]]
            if missing.need_both_empty:
                neighbors = get_both_side_neighbor_ids(seat)
                if (
                    config.get("mandatory_rules", {}).get(
                        "require_two_real_neighbors", True
                    )
                    and len(neighbors) != 2
                ):
                    continue
                if any(neighbor in context.blocked for neighbor in neighbors):
                    continue
                block_options = [None]
                resource_seats = {seat.seat_id, *neighbors}
            elif missing.need_single_empty:
                available_neighbors = [
                    neighbor
                    for neighbor in get_adjacent_seat_ids(seat)
                    if neighbor not in context.blocked
                ]
                if not available_neighbors:
                    continue
                block_options = available_neighbors
                resource_seats = set()
            else:
                block_options = [None]
                resource_seats = {seat.seat_id}

            for chosen_block in block_options:
                required = (
                    {seat.seat_id, chosen_block}
                    if chosen_block is not None
                    else resource_seats
                )
                owner_by_seat = {
                    assigned_seat: key
                    for key, assigned_seat in context.assigned_seats.items()
                }
                conflict_keys = {
                    owner_by_seat[resource]
                    for resource in required
                    if resource in owner_by_seat
                }
                if any(not movable(key) for key in conflict_keys):
                    continue

                movers = [
                    (key, context.assigned_seats[key])
                    for key in sorted(conflict_keys)
                ]
                for key, _ in movers:
                    context.remove_assignment(*key)

                assigned_missing = context.assign_passenger(
                    missing,
                    seat.seat_id,
                    missing_key[0],
                    chosen_block=chosen_block,
                )
                if assigned_missing and relocate(movers, 0):
                    diagnostics["repaired"] += 1
                    repaired = True
                    break

                if assigned_missing:
                    context.remove_assignment(*missing_key)
                for key, _ in movers:
                    if key in context.assigned_seats:
                        context.remove_assignment(*key)
                for key, original_seat in movers:
                    restored = context.assign_passenger(
                        passenger_by_key[key], original_seat, key[0]
                    )
                    if not restored:
                        raise RuntimeError(
                            "局部重排回滚失败: "
                            f"groupId={key[0]}, hostnum={key[1]}"
                        )
            if repaired:
                break

    diagnostics["unresolved"] = sum(
        key not in context.assigned_seats for key in passenger_by_key
    )
    diagnostics["seconds"] = time.perf_counter() - started
    return diagnostics


# ---------- 主入口 ----------
def improve_assignment_with_safe_neighborhoods(
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    groups: List[Group],
    context: AssignmentContext,
    passenger_sorted_seats: Dict[Tuple[int, int], List[Seat]],
    weights: Dict[str, float],
    config: Dict[str, Any],
    deadline: float,
) -> Dict[str, Any]:
    """在全局截止时间前，用保持硬约束可行的移动和换座邻域改善软评分。"""
    started = time.perf_counter()
    diagnostics = {
        "passes": 0,
        "evaluated_moves": 0,
        "accepted_moves": 0,
        "score_improvement": 0.0,
        "local_branching": {},
        "seconds": 0.0,
        "stopped_by_deadline": False,
    }
    if started >= deadline:
        diagnostics["stopped_by_deadline"] = True
        return diagnostics

    try:
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import allocation_evaluator as evaluator_module

    passenger_objects = {
        (group.group_id, passenger.hostnum): passenger
        for group in groups
        for passenger in group.passengers
    }
    passenger_raw = {
        (int(group["groupId"]), int(passenger["hostnum"])): passenger
        for group in groups_data
        for passenger in group["psrs"]
    }

    # 实际承担陪护职责的普通旅客不可移动，否则会破坏相邻约束。
    active_caregivers: Set[Tuple[int, int]] = set()
    for group in groups:
        for passenger in group.passengers:
            cared_key = (group.group_id, passenger.hostnum)
            cared_seat_id = context.assigned_seats.get(cared_key)
            if (
                cared_seat_id is None
                or not passenger_requires_caregiver(passenger, config)
            ):
                continue
            allow_cross = bool(
                get_ssr_rule(passenger.ssr, config).get(
                    "caregiver_allow_cross_aisle", False
                )
            )
            neighbors = set(
                get_row_neighbor_ids(
                    context.seats[cared_seat_id], allow_cross
                )
            )
            for caregiver in group.passengers:
                caregiver_key = (group.group_id, caregiver.hostnum)
                if (
                    not caregiver.ssr
                    and not caregiver.need_both_empty
                    and not caregiver.need_single_empty
                    and context.assigned_seats.get(caregiver_key) in neighbors
                ):
                    active_caregivers.add(caregiver_key)

    movable_keys = [
        key
        for key, passenger in passenger_objects.items()
        if (
            key in context.assigned_seats
            and passenger.new_seat_num is None
            and not passenger.ssr
            and not passenger.need_cared
            and not passenger.need_both_empty
            and not passenger.need_single_empty
            and key not in active_caregivers
        )
    ]
    movable_key_set = set(movable_keys)
    if not movable_keys:
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics

    new_topology = evaluator_module.SeatTopology(new_seats_data, config)
    old_topology = evaluator_module.SeatTopology(old_seats_data, config)
    w_c = float(weights.get("w_c", -2.0))
    candidate_cap = max(
        4, int(config.get("algorithm", {}).get("local_search_candidate_cap", 32))
    )
    epsilon = float(
        config.get("algorithm", {}).get("local_search_epsilon", 1e-9)
    )
    bsct_items = [
        (key[0], seat_id)
        for key, seat_id in context.assigned_seats.items()
        if passenger_objects[key].ssr == "BSCT"
    ]
    individual_cache: Dict[Tuple[Tuple[int, int], str], float] = {}
    baby_cache: Dict[Tuple[int, str], float] = {}

    def individual_score(key: Tuple[int, int], seat_id: str) -> float:
        cache_key = (key, seat_id)
        if cache_key not in individual_cache:
            parts = evaluator_module.individual_assignment_components(
                passenger_raw[key], old_topology, new_topology, seat_id,
                weights, config,
            )
            individual_cache[cache_key] = sum(parts.values())
        return individual_cache[cache_key]

    def baby_score(group_id: int, seat_id: str) -> float:
        cache_key = (group_id, seat_id)
        if cache_key not in baby_cache:
            baby_cache[cache_key] = sum(
                evaluator_module.baby_interference_score(
                    bsct_seat_id, seat_id, new_topology, weights, config
                )
                for bsct_group_id, bsct_seat_id in bsct_items
                if bsct_group_id != group_id
            )
        return baby_cache[cache_key]

    def passenger_score(key: Tuple[int, int], seat_id: str) -> float:
        return individual_score(key, seat_id) + baby_score(key[0], seat_id)

    group_seats: Dict[int, List[str]] = {}
    for (group_id, _), seat_id in context.assigned_seats.items():
        group_seats.setdefault(group_id, []).append(seat_id)
    compact_cache: Dict[Tuple[str, ...], float] = {}

    def compact_score(seat_ids: List[str]) -> float:
        cache_key = tuple(sorted(seat_ids))
        if cache_key not in compact_cache:
            compact_cache[cache_key] = (
                w_c
                * evaluator_module.group_compactness_penalty(
                    seat_ids, new_topology, config
                )
            )
        return compact_cache[cache_key]

    owner_by_seat = {
        seat_id: key for key, seat_id in context.assigned_seats.items()
    }

    def replace_group_seat(
        group_id: int, old_seat: str, new_seat: str
    ) -> List[str]:
        result = list(group_seats[group_id])
        result[result.index(old_seat)] = new_seat
        return result

    # First-improvement VND：接受一步后立即从新解继续，避免维护庞大候选池。
    while time.perf_counter() < deadline:
        diagnostics["passes"] += 1
        accepted = False
        movable_keys.sort(
            key=lambda key: passenger_score(
                key, context.assigned_seats[key]
            )
        )
        for key in movable_keys:
            if time.perf_counter() >= deadline:
                break
            old_seat = context.assigned_seats[key]
            group_id = key[0]
            old_group_score = compact_score(group_seats[group_id])
            current_passenger_score = passenger_score(key, old_seat)

            for candidate in passenger_sorted_seats[key][:candidate_cap]:
                if time.perf_counter() >= deadline:
                    break
                new_seat = candidate.seat_id
                if new_seat == old_seat or new_seat in context.blocked:
                    continue
                other_key = owner_by_seat.get(new_seat)
                if other_key is not None and other_key not in movable_key_set:
                    continue
                diagnostics["evaluated_moves"] += 1

                if other_key is None:
                    new_group_seats = replace_group_seat(
                        group_id, old_seat, new_seat
                    )
                    delta = (
                        passenger_score(key, new_seat)
                        - current_passenger_score
                        + compact_score(new_group_seats)
                        - old_group_score
                    )
                    if delta <= epsilon:
                        continue
                    context.remove_assignment(*key)
                    if not context.assign_passenger(
                        passenger_objects[key], new_seat, group_id
                    ):
                        context.assign_passenger(
                            passenger_objects[key], old_seat, group_id
                        )
                        continue
                    owner_by_seat.pop(old_seat)
                    owner_by_seat[new_seat] = key
                    group_seats[group_id] = new_group_seats
                else:
                    other_old_seat = new_seat
                    other_group_id = other_key[0]
                    other_group_score = compact_score(
                        group_seats[other_group_id]
                    )
                    if other_group_id == group_id:
                        compact_delta = 0.0
                        new_group_seats = group_seats[group_id]
                        other_new_group_seats = new_group_seats
                    else:
                        new_group_seats = replace_group_seat(
                            group_id, old_seat, other_old_seat
                        )
                        other_new_group_seats = replace_group_seat(
                            other_group_id, other_old_seat, old_seat
                        )
                        compact_delta = (
                            compact_score(new_group_seats)
                            - old_group_score
                            + compact_score(other_new_group_seats)
                            - other_group_score
                        )
                    delta = (
                        passenger_score(key, other_old_seat)
                        - current_passenger_score
                        + passenger_score(other_key, old_seat)
                        - passenger_score(other_key, other_old_seat)
                        + compact_delta
                    )
                    if delta <= epsilon:
                        continue
                    context.remove_assignment(*key)
                    context.remove_assignment(*other_key)
                    first_ok = context.assign_passenger(
                        passenger_objects[key], other_old_seat, group_id
                    )
                    second_ok = first_ok and context.assign_passenger(
                        passenger_objects[other_key],
                        old_seat,
                        other_group_id,
                    )
                    if not second_ok:
                        if first_ok:
                            context.remove_assignment(*key)
                        context.assign_passenger(
                            passenger_objects[key], old_seat, group_id
                        )
                        context.assign_passenger(
                            passenger_objects[other_key],
                            other_old_seat,
                            other_group_id,
                        )
                        continue
                    owner_by_seat[other_old_seat] = key
                    owner_by_seat[old_seat] = other_key
                    if other_group_id != group_id:
                        group_seats[group_id] = new_group_seats
                        group_seats[other_group_id] = other_new_group_seats

                diagnostics["accepted_moves"] += 1
                diagnostics["score_improvement"] += delta
                accepted = True
                break
            if accepted:
                break
        if not accepted:
            break

    # 两人换座的局部最优仍可能被三人循环交换打破。该邻域等价于一次很小的
    # ruin-and-recreate：释放三个普通旅客，再按循环顺序重建，不触碰保护资源。
    cycle_candidate_cap = max(
        4,
        int(
            config.get("algorithm", {}).get(
                "local_search_cycle_candidate_cap", 12
            )
        ),
    )
    while time.perf_counter() < deadline:
        accepted_cycle = False
        for first_key in movable_keys:
            if time.perf_counter() >= deadline:
                break
            first_seat = context.assigned_seats[first_key]
            for second_target in passenger_sorted_seats[first_key][
                :cycle_candidate_cap
            ]:
                second_key = owner_by_seat.get(second_target.seat_id)
                if (
                    second_key is None
                    or second_key == first_key
                    or second_key not in movable_key_set
                ):
                    continue
                second_seat = context.assigned_seats[second_key]
                for third_target in passenger_sorted_seats[second_key][
                    :cycle_candidate_cap
                ]:
                    if time.perf_counter() >= deadline:
                        break
                    third_key = owner_by_seat.get(third_target.seat_id)
                    if (
                        third_key is None
                        or third_key in (first_key, second_key)
                        or third_key not in movable_key_set
                    ):
                        continue
                    third_seat = context.assigned_seats[third_key]
                    diagnostics["evaluated_moves"] += 1
                    cycle = (
                        (first_key, first_seat, second_seat),
                        (second_key, second_seat, third_seat),
                        (third_key, third_seat, first_seat),
                    )
                    delta = sum(
                        passenger_score(key, target)
                        - passenger_score(key, source)
                        for key, source, target in cycle
                    )
                    affected_groups = {key[0] for key, _, _ in cycle}
                    replacement_by_group: Dict[int, Dict[str, str]] = {}
                    for key, source, target in cycle:
                        replacement_by_group.setdefault(key[0], {})[
                            source
                        ] = target
                    new_group_lists: Dict[int, List[str]] = {}
                    for affected_group in affected_groups:
                        replacements = replacement_by_group[affected_group]
                        new_group_lists[affected_group] = [
                            replacements.get(seat_id, seat_id)
                            for seat_id in group_seats[affected_group]
                        ]
                        delta += (
                            compact_score(new_group_lists[affected_group])
                            - compact_score(group_seats[affected_group])
                        )
                    if delta <= epsilon:
                        continue

                    for key, _, _ in cycle:
                        context.remove_assignment(*key)
                    assigned_cycle: List[Tuple[int, int]] = []
                    for key, _, target in cycle:
                        if context.assign_passenger(
                            passenger_objects[key], target, key[0]
                        ):
                            assigned_cycle.append(key)
                        else:
                            break
                    if len(assigned_cycle) != 3:
                        for key in assigned_cycle:
                            context.remove_assignment(*key)
                        for key, source, _ in cycle:
                            context.assign_passenger(
                                passenger_objects[key], source, key[0]
                            )
                        continue

                    for key, source, target in cycle:
                        owner_by_seat[target] = key
                    for affected_group in affected_groups:
                        group_seats[affected_group] = new_group_lists[
                            affected_group
                        ]
                    diagnostics["accepted_moves"] += 1
                    diagnostics["score_improvement"] += delta
                    accepted_cycle = True
                    break
                if accepted_cycle:
                    break
            if accepted_cycle:
                break
        if not accepted_cycle:
            break

    # 相关同行组联合重建：把两个完全可移动同行组的座位合并后重新分区，
    # 用位掩码动态规划求每个分区内的最佳旅客—座位匹配。它能直接修复
    # 单人移动、两人换座和三人循环都无法改善的“两个组互相穿插”状态。
    keys_by_group: Dict[int, List[Tuple[int, int]]] = {}
    for key in context.assigned_seats:
        keys_by_group.setdefault(key[0], []).append(key)
    rebuild_groups = [
        group_id
        for group_id, keys in keys_by_group.items()
        if keys and all(key in movable_key_set for key in keys)
    ]
    related_partner_cap = max(
        2,
        int(
            config.get("algorithm", {}).get(
                "local_search_related_group_cap", 8
            )
        ),
    )
    related_candidate_cap = max(
        cycle_candidate_cap,
        int(
            config.get("algorithm", {}).get(
                "local_search_related_candidate_cap", 64
            )
        ),
    )

    def best_matching(
        keys: List[Tuple[int, int]], seats: Tuple[str, ...]
    ) -> Tuple[float, Dict[Tuple[int, int], str]]:
        # dp[mask] 表示已为前 bit_count(mask) 位旅客选好座位的最佳得分。
        size = len(keys)
        dp = [-float("inf")] * (1 << size)
        parent: List[Optional[Tuple[int, int]]] = [None] * (1 << size)
        dp[0] = 0.0
        for mask in range(1 << size):
            passenger_index = mask.bit_count()
            if passenger_index >= size or dp[mask] == -float("inf"):
                continue
            key = keys[passenger_index]
            for seat_index, seat_id in enumerate(seats):
                bit = 1 << seat_index
                if mask & bit:
                    continue
                next_mask = mask | bit
                value = dp[mask] + passenger_score(key, seat_id)
                if value > dp[next_mask]:
                    dp[next_mask] = value
                    parent[next_mask] = (mask, seat_index)
        assignment: Dict[Tuple[int, int], str] = {}
        mask = (1 << size) - 1
        while mask:
            previous_mask, seat_index = parent[mask]  # type: ignore[misc]
            passenger_index = previous_mask.bit_count()
            assignment[keys[passenger_index]] = seats[seat_index]
            mask = previous_mask
        return dp[-1], assignment

    while time.perf_counter() < deadline:
        accepted_rebuild = False
        rebuild_groups.sort(
            key=lambda group_id: compact_score(group_seats[group_id])
        )
        for first_group in rebuild_groups:
            if time.perf_counter() >= deadline:
                break
            first_keys = keys_by_group[first_group]
            first_rows = [
                context.seats[context.assigned_seats[key]].row
                for key in first_keys
            ]
            first_center = sum(first_rows) / len(first_rows)
            preferred_partners = {
                owner_by_seat[candidate.seat_id][0]
                for key in first_keys
                for candidate in passenger_sorted_seats[key][
                    :related_candidate_cap
                ]
                if candidate.seat_id in owner_by_seat
                and owner_by_seat[candidate.seat_id][0] != first_group
            }
            partners = sorted(
                (
                    group_id
                    for group_id in rebuild_groups
                    if group_id != first_group
                ),
                key=lambda group_id: (
                    0 if group_id in preferred_partners else 1,
                    abs(
                        first_center
                        - sum(
                            context.seats[
                                context.assigned_seats[key]
                            ].row
                            for key in keys_by_group[group_id]
                        )
                        / len(keys_by_group[group_id])
                    ),
                ),
            )[:related_partner_cap]
            for second_group in partners:
                second_keys = keys_by_group[second_group]
                all_seats = tuple(
                    context.assigned_seats[key]
                    for key in first_keys + second_keys
                )
                first_size = len(first_keys)
                if len(all_seats) > 12:
                    continue
                current_value = (
                    sum(
                        passenger_score(key, context.assigned_seats[key])
                        for key in first_keys + second_keys
                    )
                    + compact_score(group_seats[first_group])
                    + compact_score(group_seats[second_group])
                )
                best_value = current_value
                best_assignments: Optional[
                    Dict[Tuple[int, int], str]
                ] = None
                full_mask = (1 << len(all_seats)) - 1
                for mask in range(1, full_mask):
                    if time.perf_counter() >= deadline:
                        break
                    if mask.bit_count() != first_size:
                        continue
                    first_partition = tuple(
                        seat_id
                        for index, seat_id in enumerate(all_seats)
                        if mask & (1 << index)
                    )
                    second_partition = tuple(
                        seat_id
                        for index, seat_id in enumerate(all_seats)
                        if not mask & (1 << index)
                    )
                    first_match_score, first_assignment = best_matching(
                        first_keys, first_partition
                    )
                    second_match_score, second_assignment = best_matching(
                        second_keys, second_partition
                    )
                    value = (
                        first_match_score
                        + second_match_score
                        + compact_score(list(first_partition))
                        + compact_score(list(second_partition))
                    )
                    diagnostics["evaluated_moves"] += 1
                    if value > best_value + epsilon:
                        best_value = value
                        best_assignments = {
                            **first_assignment,
                            **second_assignment,
                        }
                if best_assignments is None:
                    continue

                original_assignments = {
                    key: context.assigned_seats[key]
                    for key in first_keys + second_keys
                }
                for key in original_assignments:
                    context.remove_assignment(*key)
                assigned_rebuild: List[Tuple[int, int]] = []
                for key, seat_id in best_assignments.items():
                    if context.assign_passenger(
                        passenger_objects[key], seat_id, key[0]
                    ):
                        assigned_rebuild.append(key)
                    else:
                        break
                if len(assigned_rebuild) != len(original_assignments):
                    for key in assigned_rebuild:
                        context.remove_assignment(*key)
                    for key, seat_id in original_assignments.items():
                        context.assign_passenger(
                            passenger_objects[key], seat_id, key[0]
                        )
                    continue
                for key, seat_id in best_assignments.items():
                    owner_by_seat[seat_id] = key
                group_seats[first_group] = [
                    best_assignments[key] for key in first_keys
                ]
                group_seats[second_group] = [
                    best_assignments[key] for key in second_keys
                ]
                diagnostics["accepted_moves"] += 1
                diagnostics["score_improvement"] += (
                    best_value - current_value
                )
                accepted_rebuild = True
                break
            if accepted_rebuild:
                break
        if not accepted_rebuild:
            break

    # 陪护组容易占用普通同行组中间的连续座位。对一个陪护组和一个相邻普通组
    # 做小规模精确重建；联合人数上限为 7，最多枚举 7! 个排列。
    def global_baby_score(
        assignments: Dict[Tuple[int, int], str]
    ) -> float:
        value = 0.0
        infant_items = [
            (key[0], seat_id)
            for key, seat_id in assignments.items()
            if passenger_objects[key].ssr == "BSCT"
        ]
        for infant_group, infant_seat in infant_items:
            for other_key, other_seat in assignments.items():
                if other_key[0] == infant_group:
                    continue
                value += evaluator_module.baby_interference_score(
                    infant_seat, other_seat, new_topology, weights, config
                )
        return value

    caregiver_groups = [
        group_id
        for group_id, keys in keys_by_group.items()
        if (
            any(
                passenger_requires_caregiver(
                    passenger_objects[key], config
                )
                for key in keys
            )
            and all(
                passenger_objects[key].new_seat_num is None
                and not passenger_objects[key].need_both_empty
                and not passenger_objects[key].need_single_empty
                for key in keys
            )
        )
    ]
    baseline_baby = global_baby_score(dict(context.assigned_seats))
    for caregiver_group in caregiver_groups:
        if time.perf_counter() >= deadline:
            break
        caregiver_keys = keys_by_group[caregiver_group]
        caregiver_center = sum(
            context.seats[context.assigned_seats[key]].row
            for key in caregiver_keys
        ) / len(caregiver_keys)
        preferred_partners = {
            owner_by_seat[candidate.seat_id][0]
            for key in caregiver_keys
            for candidate in passenger_sorted_seats[key][
                :related_candidate_cap
            ]
            if candidate.seat_id in owner_by_seat
            and owner_by_seat[candidate.seat_id][0] != caregiver_group
        }
        ordinary_partners = sorted(
            (
                group_id
                for group_id in rebuild_groups
                if group_id != caregiver_group
                and len(caregiver_keys) + len(keys_by_group[group_id]) <= 7
            ),
            key=lambda group_id: (
                0 if group_id in preferred_partners else 1,
                abs(
                    caregiver_center
                    - sum(
                        context.seats[context.assigned_seats[key]].row
                        for key in keys_by_group[group_id]
                    )
                    / len(keys_by_group[group_id])
                ),
            ),
        )[:related_partner_cap]
        for ordinary_group in ordinary_partners:
            if time.perf_counter() >= deadline:
                break
            ordinary_keys = keys_by_group[ordinary_group]
            joint_keys = caregiver_keys + ordinary_keys
            joint_seats = tuple(
                context.assigned_seats[key] for key in joint_keys
            )
            current_value = (
                sum(
                    individual_score(key, context.assigned_seats[key])
                    for key in joint_keys
                )
                + compact_score(group_seats[caregiver_group])
                + compact_score(group_seats[ordinary_group])
                + baseline_baby
            )
            best_value = current_value
            best_assignment: Optional[Dict[Tuple[int, int], str]] = None
            external_occupied = set(joint_seats)
            for permutation in itertools.permutations(joint_seats):
                if time.perf_counter() >= deadline:
                    break
                proposal = dict(zip(joint_keys, permutation))
                feasible = True
                for key, seat_id in proposal.items():
                    passenger = passenger_objects[key]
                    if not is_seat_feasible(
                        context.seats[seat_id],
                        passenger,
                        context.occupied,
                        context.seat_ssr_map,
                        context.blocked,
                        context.seats,
                        exclude_occupied=external_occupied,
                    ):
                        feasible = False
                        break
                if not feasible:
                    continue
                for key in caregiver_keys:
                    passenger = passenger_objects[key]
                    if not passenger_requires_caregiver(passenger, config):
                        continue
                    allow_cross = bool(
                        get_ssr_rule(passenger.ssr, config).get(
                            "caregiver_allow_cross_aisle", False
                        )
                    )
                    neighbor_ids = set(
                        get_row_neighbor_ids(
                            context.seats[proposal[key]], allow_cross
                        )
                    )
                    if not any(
                        not passenger_objects[other_key].ssr
                        and proposal[other_key] in neighbor_ids
                        for other_key in caregiver_keys
                        if other_key != key
                    ):
                        feasible = False
                        break
                if not feasible:
                    continue

                candidate_assignments = dict(context.assigned_seats)
                candidate_assignments.update(proposal)
                value = (
                    sum(
                        individual_score(key, proposal[key])
                        for key in joint_keys
                    )
                    + compact_score(
                        [proposal[key] for key in caregiver_keys]
                    )
                    + compact_score(
                        [proposal[key] for key in ordinary_keys]
                    )
                    + global_baby_score(candidate_assignments)
                )
                diagnostics["evaluated_moves"] += 1
                if value > best_value + epsilon:
                    best_value = value
                    best_assignment = proposal
            if best_assignment is None:
                continue

            originals = {
                key: context.assigned_seats[key] for key in joint_keys
            }
            for key in joint_keys:
                context.remove_assignment(*key)
            # 先放普通成员，再放需陪护 SSR，保证提交时相邻座位已存在。
            assignment_order = sorted(
                joint_keys,
                key=lambda key: passenger_requires_caregiver(
                    passenger_objects[key], config
                ),
            )
            assigned_joint: List[Tuple[int, int]] = []
            for key in assignment_order:
                if context.assign_passenger(
                    passenger_objects[key], best_assignment[key], key[0]
                ):
                    assigned_joint.append(key)
                else:
                    break
            if len(assigned_joint) != len(joint_keys):
                for key in assigned_joint:
                    context.remove_assignment(*key)
                for key in assignment_order:
                    context.assign_passenger(
                        passenger_objects[key], originals[key], key[0]
                    )
                continue
            for key, seat_id in best_assignment.items():
                owner_by_seat[seat_id] = key
            group_seats[caregiver_group] = [
                best_assignment[key] for key in caregiver_keys
            ]
            group_seats[ordinary_group] = [
                best_assignment[key] for key in ordinary_keys
            ]
            baseline_baby = global_baby_score(dict(context.assigned_seats))
            diagnostics["accepted_moves"] += 1
            diagnostics["score_improvement"] += best_value - current_value
            break

    diagnostics["stopped_by_deadline"] = time.perf_counter() >= deadline
    diagnostics["seconds"] = time.perf_counter() - started
    return diagnostics


def improve_with_multigroup_lns(
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    groups: List[Group],
    context: AssignmentContext,
    passenger_sorted_seats: Dict[Tuple[int, int], List[Seat]],
    weights: Dict[str, float],
    config: Dict[str, Any],
    deadline: float,
    elite_pattern_recorder: Optional[
        Callable[
            [int, Tuple[Tuple[Tuple[int, int], str], ...], float, str],
            None,
        ]
    ] = None,
) -> Dict[str, Any]:
    """对2～8个冲突同行组执行带局部整数主问题的大邻域重建。"""
    started = time.perf_counter()
    diagnostics = {
        "enabled": bool(
            config.get("algorithm", {}).get(
                "enable_conflict_component_lns", False
            )
        ),
        "components_tested": 0,
        "max_tested_component_size": 0,
        "tested_group_components": [],
        "accepted_components": [],
        "options_generated": 0,
        "search_nodes": 0,
        "accepted_rebuilds": 0,
        "accepted_worsening": 0,
        "stagnation_rounds": 0,
        "dynamic_reorders": 0,
        "ejection_chains_generated": 0,
        "ejection_chains_accepted": 0,
        "score_improvement": 0.0,
        "seconds": 0.0,
        "stopped_by_deadline": False,
    }
    if not diagnostics["enabled"]:
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics
    if started >= deadline:
        diagnostics["stopped_by_deadline"] = True
        return diagnostics

    try:
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import allocation_evaluator as evaluator_module

    passenger_objects = {
        (group.group_id, passenger.hostnum): passenger
        for group in groups
        for passenger in group.passengers
    }
    passenger_raw = {
        (int(group["groupId"]), int(passenger["hostnum"])): passenger
        for group in groups_data
        for passenger in group["psrs"]
    }

    # 陪护者虽然没有 SSR，但仍属于不可移动资源。
    active_caregivers: Set[Tuple[int, int]] = set()
    for group in groups:
        for passenger in group.passengers:
            key = (group.group_id, passenger.hostnum)
            seat_id = context.assigned_seats.get(key)
            if (
                seat_id is None
                or not passenger_requires_caregiver(passenger, config)
            ):
                continue
            allow_cross = bool(
                get_ssr_rule(passenger.ssr, config).get(
                    "caregiver_allow_cross_aisle", False
                )
            )
            neighbors = set(
                get_row_neighbor_ids(context.seats[seat_id], allow_cross)
            )
            for other in group.passengers:
                other_key = (group.group_id, other.hostnum)
                if (
                    not other.ssr
                    and context.assigned_seats.get(other_key) in neighbors
                ):
                    active_caregivers.add(other_key)

    keys_by_group: Dict[int, List[Tuple[int, int]]] = {}
    for key in context.assigned_seats:
        keys_by_group.setdefault(key[0], []).append(key)
    algorithm = config.get("algorithm", {})
    lns_group_size_limit = max(
        2,
        int(
            config.get("algorithm", {}).get(
                "multigroup_pattern_group_size_limit", 6
            )
        ),
    )
    eligible_groups = {
        group_id
        for group_id, keys in keys_by_group.items()
        if keys
        and len(keys) <= lns_group_size_limit
        and all(
            passenger_objects[key].new_seat_num is None
            and not passenger_objects[key].need_both_empty
            and not passenger_objects[key].need_single_empty
            for key in keys
        )
    }
    if len(eligible_groups) < 2:
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics

    related_seat_cap = max(
        16, int(algorithm.get("multigroup_related_seat_cap", 80))
    )
    related_group_cap = max(
        3, int(algorithm.get("multigroup_related_group_cap", 10))
    )
    min_component_size = min(
        8, max(2, int(algorithm.get("multigroup_min_component_size", 2)))
    )
    configured_max_component_size = int(
        algorithm.get("multigroup_max_component_size", 0)
    )
    # 0表示不再用固定组数截断，而由可用同行组数、旅客总数和时限共同
    # 限制。正数仍可作为生产保护阀，但默认邻域会在停滞后逐级扩大。
    max_component_size = min(
        len(eligible_groups),
        max(
            min_component_size,
            configured_max_component_size
            if configured_max_component_size > 0
            else len(eligible_groups),
        ),
    )
    preferred_component_size = min(
        max_component_size,
        max(
            min_component_size,
            int(algorithm.get("multigroup_component_size", 6)),
        ),
    )
    diagnostics["adaptive_component_sizes"] = {
        "minimum": min_component_size,
        "initial": preferred_component_size,
        "maximum": max_component_size,
        "configured_maximum": configured_max_component_size,
    }
    option_limit = max(
        10, int(algorithm.get("multigroup_option_limit", 60))
    )
    mip_solve_limit = max(
        1, int(algorithm.get("multigroup_mip_solve_limit", 200))
    )
    passenger_limit = max(
        6, int(algorithm.get("multigroup_passenger_limit", 14))
    )
    root_limit = max(
        1, int(algorithm.get("multigroup_root_limit", 12))
    )
    free_seat_cap = max(
        0, int(algorithm.get("multigroup_free_seat_cap", 5))
    )
    local_mip_time_limit = max(
        0.05, float(algorithm.get("multigroup_mip_time_limit", 0.75))
    )
    late_history_length = max(
        2, int(algorithm.get("multigroup_late_history_length", 8))
    )
    allowed_drop = max(
        0.0, float(algorithm.get("multigroup_allowed_drop", 20.0))
    )
    stagnation_limit = max(
        1, int(algorithm.get("multigroup_stagnation_rounds", 3))
    )
    dynamic_pool_growth = max(
        1, int(algorithm.get("multigroup_dynamic_pool_growth", 12))
    )
    epsilon = float(algorithm.get("local_search_epsilon", 1e-9))
    new_topology = evaluator_module.SeatTopology(new_seats_data, config)
    old_topology = evaluator_module.SeatTopology(old_seats_data, config)
    repair_metrics = _group_repair_metrics(
        groups_data,
        context.assigned_seats,
        new_topology,
        weights,
        config,
        old_topology,
    )
    diagnostics["repair_queue"] = [
        repair_metrics[group_id]
        for group_id in sorted(
            eligible_groups,
            key=lambda gid: _repair_priority_key(repair_metrics[gid]),
        )[:20]
    ]
    w_c = float(weights.get("w_c", -2.0))
    incremental_scorer = evaluator_module.IncrementalSoftScorer(
        new_seats_data,
        old_seats_data,
        groups_data,
        weights,
        config,
    )
    current_soft_score = incremental_scorer.components(
        context.assigned_seats
    )["total_soft_score"]

    individual_cache: Dict[Tuple[Tuple[int, int], str], float] = {}
    baby_cache: Dict[Tuple[int, str], float] = {}
    bsct_items = [
        (key[0], seat_id)
        for key, seat_id in context.assigned_seats.items()
        if passenger_objects[key].ssr == "BSCT"
    ]

    def passenger_score(key: Tuple[int, int], seat_id: str) -> float:
        cache_key = (key, seat_id)
        if cache_key not in individual_cache:
            parts = evaluator_module.individual_assignment_components(
                passenger_raw[key],
                old_topology,
                new_topology,
                seat_id,
                weights,
                config,
            )
            individual_cache[cache_key] = sum(parts.values())
        baby_key = (key[0], seat_id)
        if baby_key not in baby_cache:
            baby_cache[baby_key] = sum(
                evaluator_module.baby_interference_score(
                    infant_seat,
                    seat_id,
                    new_topology,
                    weights,
                    config,
                )
                for infant_group, infant_seat in bsct_items
                if infant_group != key[0]
            )
        return individual_cache[cache_key] + baby_cache[baby_key]

    compact_cache: Dict[Tuple[str, ...], float] = {}

    def compact_score(seats: Tuple[str, ...]) -> float:
        cache_key = tuple(sorted(seats))
        if cache_key not in compact_cache:
            compact_cache[cache_key] = (
                w_c
                * evaluator_module.group_compactness_penalty(
                    seats, new_topology, config
                )
            )
        return compact_cache[cache_key]

    def best_matching(
        keys: List[Tuple[int, int]], seats: Tuple[str, ...]
    ) -> Tuple[float, Tuple[str, ...]]:
        size = len(keys)
        dp = [-float("inf")] * (1 << size)
        parent: List[Optional[Tuple[int, int]]] = [None] * (1 << size)
        dp[0] = 0.0
        for mask in range(1 << size):
            if mask % 128 == 0 and time.perf_counter() >= deadline:
                diagnostics["stopped_by_deadline"] = True
                return -float("inf"), ()
            passenger_index = mask.bit_count()
            if passenger_index >= size or dp[mask] == -float("inf"):
                continue
            for seat_index, seat_id in enumerate(seats):
                bit = 1 << seat_index
                if mask & bit:
                    continue
                next_mask = mask | bit
                value = (
                    dp[mask]
                    + passenger_score(keys[passenger_index], seat_id)
                )
                if value > dp[next_mask]:
                    dp[next_mask] = value
                    parent[next_mask] = (mask, seat_index)
        assignment = [""] * size
        mask = (1 << size) - 1
        while mask:
            previous, seat_index = parent[mask]  # type: ignore[misc]
            assignment[previous.bit_count()] = seats[seat_index]
            mask = previous
        return dp[-1], tuple(assignment)

    def best_group_assignment(
        group_id: int,
        seats: Tuple[str, ...],
        released_seats: FrozenSet[str],
    ) -> Tuple[float, Tuple[str, ...]]:
        keys = keys_by_group[group_id]
        if all(
            not passenger_objects[key].ssr
            and not passenger_requires_caregiver(
                passenger_objects[key], config
            )
            for key in keys
        ):
            return best_matching(keys, seats)

        best_score = -float("inf")
        best_assignment: Tuple[str, ...] = ()
        for permutation_index, assignment in enumerate(
            itertools.permutations(seats)
        ):
            if (
                permutation_index % 128 == 0
                and time.perf_counter() >= deadline
            ):
                diagnostics["stopped_by_deadline"] = True
                break
            feasible = True
            proposal = dict(zip(keys, assignment))
            for key, seat_id in proposal.items():
                passenger = passenger_objects[key]
                if not is_seat_feasible(
                    context.seats[seat_id],
                    passenger,
                    context.occupied,
                    context.seat_ssr_map,
                    context.blocked,
                    context.seats,
                    exclude_occupied=set(released_seats),
                ):
                    feasible = False
                    break
            if not feasible:
                continue
            for key in keys:
                passenger = passenger_objects[key]
                if not passenger_requires_caregiver(passenger, config):
                    continue
                allow_cross = bool(
                    get_ssr_rule(passenger.ssr, config).get(
                        "caregiver_allow_cross_aisle", False
                    )
                )
                neighbors = set(
                    get_row_neighbor_ids(
                        context.seats[proposal[key]], allow_cross
                    )
                )
                if not any(
                    other_key != key
                    and not passenger_objects[other_key].ssr
                    and proposal[other_key] in neighbors
                    for other_key in keys
                ):
                    feasible = False
                    break
            if not feasible:
                continue
            score = sum(
                passenger_score(key, proposal[key]) for key in keys
            )
            if score > best_score:
                best_score = score
                best_assignment = assignment
        return best_score, best_assignment

    def group_options(
        group_id: int, seat_pool: Tuple[str, ...]
    ) -> List[Tuple[float, FrozenSet[str], Tuple[str, ...]]]:
        keys = keys_by_group[group_id]
        heap: List[
            Tuple[float, int, FrozenSet[str], Tuple[str, ...]]
        ] = []
        sequence = 0
        released_seats = frozenset(seat_pool)
        group_size = len(keys)
        candidate_subsets: Set[Tuple[str, ...]] = set()
        current_seats = tuple(
            context.assigned_seats[key] for key in keys
        )
        if set(current_seats).issubset(released_seats):
            candidate_subsets.add(tuple(sorted(current_seats)))

        # 每个中心座位只在一个小几何邻域内组合，避免 C(|pool|, |group|)
        # 的完整枚举；同行组的优秀方案通常由相邻或近邻座位构成。
        neighborhood_size = min(
            len(seat_pool),
            group_size
            + max(
                1,
                int(
                    algorithm.get(
                        "multigroup_neighborhood_extra_seats", 2
                    )
                ),
            ),
        )
        for center_index, center in enumerate(seat_pool):
            if (
                center_index % 8 == 0
                and time.perf_counter() >= deadline
            ):
                diagnostics["stopped_by_deadline"] = True
                break
            center_row = int(new_topology.seat_map[center]["row"])
            nearest = sorted(
                seat_pool,
                key=lambda seat_id: (
                    abs(
                        int(new_topology.seat_map[seat_id]["row"])
                        - center_row
                    ),
                    abs(
                        new_topology.x[seat_id]
                        - new_topology.x[center]
                    ),
                    seat_id,
                ),
            )[:neighborhood_size]
            for combination_index, seats in enumerate(
                itertools.combinations(nearest, group_size)
            ):
                if (
                    combination_index % 128 == 0
                    and time.perf_counter() >= deadline
                ):
                    diagnostics["stopped_by_deadline"] = True
                    break
                if center in seats:
                    candidate_subsets.add(tuple(sorted(seats)))

        for seats in candidate_subsets:
            if time.perf_counter() >= deadline:
                break
            _, assignment = best_group_assignment(
                group_id, seats, released_seats
            )
            if not assignment:
                continue
            proposal = dict(context.assigned_seats)
            proposal.update(zip(keys, assignment))
            # 组模式的排序分直接来自统一评分器，包含该组作为BSCT组或
            # 其他BSCT影响对象时的全部跨组增量。
            score = incremental_scorer.components(
                proposal, {group_id}
            )["total_soft_score"]
            item = (score, sequence, frozenset(seats), assignment)
            sequence += 1
            if len(heap) < option_limit:
                heapq.heappush(heap, item)
            elif score > heap[0][0]:
                heapq.heapreplace(heap, item)
        diagnostics["options_generated"] += len(heap)
        ranked_options = [
            (score, seat_set, assignment)
            for score, _, seat_set, assignment in sorted(
                heap, reverse=True
            )
        ]
        if elite_pattern_recorder is not None:
            record_limit = max(
                1,
                int(
                    algorithm.get(
                        "elite_patterns_from_pricing_per_call", 3
                    )
                ),
            )
            for score, _, assignment in ranked_options[:record_limit]:
                elite_pattern_recorder(
                    group_id,
                    tuple(zip(keys, assignment)),
                    score,
                    "lns_generated",
                )
        return ranked_options

    owner_by_seat = {
        seat_id: key for key, seat_id in context.assigned_seats.items()
    }

    def solve_pattern_master(
        component: Tuple[int, ...],
        options: Dict[
            int,
            List[Tuple[float, FrozenSet[str], Tuple[str, ...]]],
        ],
        root: int,
    ) -> Dict[int, Tuple[str, ...]]:
        """用小规模HiGHS集合划分模型组合各组完整放置方案。"""
        try:
            import highspy
            import numpy as np
        except ImportError:
            return {}

        solver = highspy.Highs()
        solver.setOptionValue("output_flag", False)
        solver.setOptionValue("threads", 1)
        solver.setOptionValue("random_seed", 0)
        solver.setOptionValue(
            "time_limit",
            min(
                local_mip_time_limit,
                max(0.01, deadline - time.perf_counter()),
            ),
        )
        solver.setOptionValue("mip_rel_gap", 0.0)
        empty_i = np.empty(0, dtype=np.int32)
        empty_v = np.empty(0, dtype=np.float64)

        group_row: Dict[int, int] = {}
        for group_id in component:
            solver.addRow(1.0, 1.0, 0, empty_i, empty_v)
            group_row[group_id] = solver.getNumRow() - 1
        all_pattern_seats = sorted(
            {
                seat_id
                for group_id in component
                for _, seats, _ in options[group_id]
                for seat_id in seats
            }
        )
        seat_row: Dict[str, int] = {}
        for seat_id in all_pattern_seats:
            solver.addRow(
                -highspy.kHighsInf, 1.0, 0, empty_i, empty_v
            )
            seat_row[seat_id] = solver.getNumRow() - 1

        columns: List[
            Tuple[int, Tuple[str, ...]]
        ] = []
        root_current = tuple(
            context.assigned_seats[key] for key in keys_by_group[root]
        )
        for group_id in component:
            for score, seats, assignment in options[group_id]:
                # 强制根组离开当前放置，使历史接受机制可以跨越局部最优。
                if group_id == root and assignment == root_current:
                    continue
                indexes = np.asarray(
                    [group_row[group_id]]
                    + [seat_row[seat_id] for seat_id in seats],
                    dtype=np.int32,
                )
                values = np.ones(len(indexes), dtype=np.float64)
                solver.addCol(
                    -float(score),
                    0.0,
                    1.0,
                    len(indexes),
                    indexes,
                    values,
                )
                column = solver.getNumCol() - 1
                solver.changeColIntegrality(
                    column, highspy.HighsVarType.kInteger
                )
                columns.append((group_id, assignment))
        if solver.getNumCol() == 0:
            return {}
        solver.run()
        solution = solver.getSolution()
        choice: Dict[int, Tuple[str, ...]] = {}
        for column, value in enumerate(solution.col_value):
            if value > 0.5:
                group_id, assignment = columns[column]
                choice[group_id] = assignment
        return choice if len(choice) == len(component) else {}

    initial_soft_score = current_soft_score
    best_soft_score = current_soft_score
    best_context = copy.deepcopy(context)
    late_history = [
        current_soft_score - allowed_drop
        for _ in range(late_history_length)
    ]
    history_index = 0
    root_cursor = 0
    stagnation_count = 0

    def dynamically_ranked_candidates(
        key: Tuple[int, int],
        group_seats: Dict[int, Tuple[str, ...]],
        pool_cap: int,
    ) -> List[Seat]:
        """按当前同行组形状重排候选座位，避免一直沿用构造阶段的静态顺序。"""
        group_id = key[0]
        current_seat = context.assigned_seats[key]
        other_seats = tuple(
            seat_id
            for seat_id in group_seats[group_id]
            if seat_id != current_seat
        )
        candidates = passenger_sorted_seats[key][
            : min(len(passenger_sorted_seats[key]), pool_cap)
        ]
        diagnostics["dynamic_reorders"] += 1
        return sorted(
            candidates,
            key=lambda seat: (
                -(
                    passenger_score(key, seat.seat_id)
                    + compact_score(other_seats + (seat.seat_id,))
                ),
                seat.seat_id,
            ),
        )

    # 每次接受后重建冲突图；停滞时扩大候选池并轮换根组和邻域规模。
    while (
        time.perf_counter() < deadline
        and diagnostics["search_nodes"] < mip_solve_limit
    ):
        group_seats = {
            group_id: tuple(
                context.assigned_seats[key]
                for key in keys_by_group[group_id]
            )
            for group_id in eligible_groups
        }
        current_metrics = _group_repair_metrics(
            groups_data,
            context.assigned_seats,
            new_topology,
            weights,
            config,
            old_topology,
        )
        roots = sorted(
            eligible_groups,
            key=lambda group_id: _repair_priority_key(
                current_metrics[group_id]
            ),
        )
        if roots:
            offset = root_cursor % len(roots)
            roots = roots[offset:] + roots[:offset]
        accepted = False
        seen_components: Set[Tuple[int, ...]] = set()
        dynamic_candidate_cache: Dict[Tuple[int, int], List[Seat]] = {}
        dynamic_cap = related_seat_cap + (
            stagnation_count * dynamic_pool_growth
        )

        def candidates_for(key: Tuple[int, int]) -> List[Seat]:
            if stagnation_count == 0:
                return passenger_sorted_seats[key][:related_seat_cap]
            if key not in dynamic_candidate_cache:
                dynamic_candidate_cache[key] = (
                    dynamically_ranked_candidates(
                        key, group_seats, dynamic_cap
                    )
                )
            return dynamic_candidate_cache[key]

        def build_ejection_chain(root: int) -> Tuple[int, ...]:
            """沿有利座位的当前占用者递归扩展一条同行组换座链。"""
            chain = [root]
            frontier = [root]
            while frontier and len(chain) < max_component_size:
                source_group = frontier.pop(0)
                pressure: Dict[int, float] = {}
                for key in keys_by_group[source_group]:
                    current_seat = context.assigned_seats[key]
                    current_value = passenger_score(key, current_seat)
                    for rank, candidate in enumerate(
                        candidates_for(key)[:related_seat_cap]
                    ):
                        owner = owner_by_seat.get(candidate.seat_id)
                        if (
                            owner is None
                            or owner[0] not in eligible_groups
                            or owner[0] in chain
                        ):
                            continue
                        gain = max(
                            0.0,
                            passenger_score(key, candidate.seat_id)
                            - current_value,
                        )
                        pressure[owner[0]] = (
                            pressure.get(owner[0], 0.0)
                            + gain
                            + 1.0 / (rank + 1)
                        )
                if not pressure:
                    break
                next_group = max(
                    pressure,
                    key=lambda group_id: (
                        pressure[group_id],
                        -group_id,
                    ),
                )
                chain.append(next_group)
                frontier.append(next_group)
            if len(chain) >= min_component_size:
                diagnostics["ejection_chains_generated"] += 1
            return tuple(chain)

        ejection_signatures: Set[Tuple[int, ...]] = set()
        for root in roots[:root_limit]:
            conflict_counts: Dict[int, int] = {}
            for key in keys_by_group[root]:
                for candidate in candidates_for(key):
                    owner = owner_by_seat.get(candidate.seat_id)
                    if (
                        owner is not None
                        and owner[0] in eligible_groups
                        and owner[0] != root
                    ):
                        conflict_counts[owner[0]] = (
                            conflict_counts.get(owner[0], 0) + 1
                        )
            related = [
                group_id
                for group_id, _ in sorted(
                    conflict_counts.items(),
                    key=lambda item: (
                        -item[1],
                        float(current_metrics[item[0]]["priority_loss"]),
                        item[0],
                    ),
                )[:related_group_cap]
            ]
            ejection_chain = build_ejection_chain(root)
            if len(ejection_chain) >= min_component_size:
                ejection_signatures.add(tuple(sorted(ejection_chain)))
            if stagnation_count == 0:
                component_seeds = (
                    [ejection_chain[1:]]
                    if len(ejection_chain) >= min_component_size
                    else []
                )
                component_seeds.extend(
                    itertools.combinations(related, 2)
                )
                size_cycle = [preferred_component_size]
            else:
                component_seeds = [
                    (group_id,) for group_id in related
                ] + list(itertools.combinations(related, 2))
                size_cycle = list(
                    range(min_component_size, max_component_size + 1)
                )
                shift = stagnation_count % len(size_cycle)
                size_cycle = size_cycle[shift:] + size_cycle[:shift]

            for seed_index, seed_groups in enumerate(component_seeds):
                if (
                    time.perf_counter() >= deadline
                    or diagnostics["search_nodes"] >= mip_solve_limit
                ):
                    break
                target_size = size_cycle[
                    seed_index % len(size_cycle)
                ]
                component_members = [root, *seed_groups]
                component_members.extend(
                    group_id
                    for group_id in related
                    if group_id not in component_members
                )
                component = tuple(
                    component_members[:target_size]
                )
                if len(component) < min_component_size:
                    continue
                component_signature = tuple(sorted(component))
                if (
                    len(ejection_chain) >= min_component_size
                    and seed_groups == ejection_chain[1:]
                ):
                    ejection_signatures.add(component_signature)
                if component_signature in seen_components:
                    continue
                seen_components.add(component_signature)
                component_keys = [
                    key
                    for group_id in component
                    for key in keys_by_group[group_id]
                ]
                if len(component_keys) > passenger_limit:
                    continue
                diagnostics["components_tested"] += 1
                diagnostics["max_tested_component_size"] = max(
                    diagnostics["max_tested_component_size"],
                    len(component),
                )
                if (
                    len(diagnostics["tested_group_components"]) < 20
                    and component
                    not in diagnostics["tested_group_components"]
                ):
                    diagnostics["tested_group_components"].append(component)
                occupied_pool = tuple(
                    context.assigned_seats[key] for key in component_keys
                )
                free_frequency: Dict[str, int] = {}
                occupied_set = set(context.occupied)
                for key in component_keys:
                    for candidate in candidates_for(key):
                        seat_id = candidate.seat_id
                        if (
                            seat_id not in occupied_set
                            and seat_id not in context.blocked
                        ):
                            free_frequency[seat_id] = (
                                free_frequency.get(seat_id, 0) + 1
                            )
                extra_free = tuple(
                    seat_id
                    for seat_id, _ in sorted(
                        free_frequency.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:free_seat_cap]
                )
                seat_pool = occupied_pool + extra_free
                options = {}
                for group_id in component:
                    if time.perf_counter() >= deadline:
                        diagnostics["stopped_by_deadline"] = True
                        break
                    options[group_id] = group_options(
                        group_id, seat_pool
                    )
                if len(options) != len(component):
                    break
                if any(not value for value in options.values()):
                    continue
                best_choice = solve_pattern_master(
                    component, options, root
                )
                diagnostics["search_nodes"] += 1
                if not best_choice:
                    continue

                originals = {
                    key: context.assigned_seats[key]
                    for key in component_keys
                }
                before_assignments = dict(context.assigned_seats)
                for key in component_keys:
                    context.remove_assignment(*key)
                rebuilt: List[Tuple[int, int]] = []
                for group_id in component:
                    proposed_items = list(
                        zip(
                            keys_by_group[group_id],
                            best_choice[group_id],
                        )
                    )
                    proposed_items.sort(
                        key=lambda item: passenger_requires_caregiver(
                            passenger_objects[item[0]], config
                        )
                    )
                    for key, seat_id in proposed_items:
                        if context.assign_passenger(
                            passenger_objects[key], seat_id, group_id
                        ):
                            rebuilt.append(key)
                        else:
                            break
                if len(rebuilt) != len(component_keys):
                    for key in rebuilt:
                        context.remove_assignment(*key)
                    for key, seat_id in originals.items():
                        context.assign_passenger(
                            passenger_objects[key], seat_id, key[0]
                        )
                    continue
                violations, unassigned, _ = (
                    evaluator_module.count_hard_constraint_violations(
                        new_seats_data,
                        groups_data,
                        context.assigned_seats,
                        config,
                    )
                )
                rebuilt_soft_score = (
                    current_soft_score
                    + incremental_scorer.delta(
                        before_assignments,
                        context.assigned_seats,
                        set(component),
                    )
                )
                acceptance_threshold = late_history[history_index]
                acceptable = rebuilt_soft_score > current_soft_score + epsilon
                if allowed_drop > epsilon:
                    acceptable = acceptable or (
                        rebuilt_soft_score
                        >= acceptance_threshold - epsilon
                    )
                late_history[history_index] = current_soft_score
                history_index = (
                    history_index + 1
                ) % late_history_length
                if violations or unassigned or not acceptable:
                    for key in component_keys:
                        if key in context.assigned_seats:
                            context.remove_assignment(*key)
                    restore_order = sorted(
                        originals,
                        key=lambda key: passenger_requires_caregiver(
                            passenger_objects[key], config
                        ),
                    )
                    for key in restore_order:
                        context.assign_passenger(
                            passenger_objects[key], originals[key], key[0]
                        )
                    continue
                previous_soft_score = current_soft_score
                owner_by_seat = {
                    seat_id: key
                    for key, seat_id in context.assigned_seats.items()
                }
                diagnostics["accepted_rebuilds"] += 1
                if component_signature in ejection_signatures:
                    diagnostics["ejection_chains_accepted"] += 1
                if rebuilt_soft_score < previous_soft_score - epsilon:
                    diagnostics["accepted_worsening"] += 1
                diagnostics["score_improvement"] += (
                    rebuilt_soft_score - previous_soft_score
                )
                current_soft_score = rebuilt_soft_score
                if rebuilt_soft_score > best_soft_score + epsilon:
                    best_soft_score = rebuilt_soft_score
                    best_context = copy.deepcopy(context)
                if len(diagnostics["accepted_components"]) < 20:
                    diagnostics["accepted_components"].append({
                        "groups": component,
                        "delta": rebuilt_soft_score - previous_soft_score,
                        "best_score": best_soft_score,
                    })
                root_cursor += 1
                accepted = True
                break
            if accepted:
                break
        if not accepted:
            stagnation_count += 1
            diagnostics["stagnation_rounds"] = stagnation_count
            root_cursor += root_limit + stagnation_count
            if stagnation_count >= stagnation_limit:
                break
        else:
            stagnation_count = 0

    if current_soft_score < best_soft_score - epsilon:
        context.__dict__ = copy.deepcopy(best_context.__dict__)
    diagnostics["score_improvement"] = (
        best_soft_score - initial_soft_score
    )
    diagnostics["best_soft_score"] = best_soft_score
    diagnostics["seconds"] = time.perf_counter() - started
    diagnostics["stopped_by_deadline"] = time.perf_counter() >= deadline
    return diagnostics


def _current_group_assignment_signature(
    context: AssignmentContext,
    passenger_keys: Sequence[Tuple[int, int]],
) -> Tuple[Tuple[int, Optional[str]], ...]:
    """Return a comparable signature even when a passenger is unassigned."""
    return tuple(
        sorted(
            (key[1], context.assigned_seats.get(key))
            for key in passenger_keys
        )
    )


def _allocation_quality(
    violations: int, unassigned: int, soft_score: float
) -> Tuple[int, int, float]:
    """Lexicographic quality: feasibility first, soft score last."""
    return (-int(violations), -int(unassigned), float(soft_score))


def _group_repair_metrics(
    groups_data: Sequence[dict],
    assignments: Mapping[Tuple[int, int], str],
    new_topology: Any,
    weights: Mapping[str, float],
    config: Mapping[str, Any],
    old_topology: Optional[Any] = None,
) -> Dict[int, Dict[str, Any]]:
    """Rank groups by repairable soft loss, then geometric dispersion."""
    try:
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import allocation_evaluator as evaluator_module

    w_c = float(weights.get("w_c", -2.0))
    metrics: Dict[int, Dict[str, Any]] = {}
    for group in groups_data:
        group_id = int(group["groupId"])
        seat_ids = [
            assignments[(group_id, int(passenger["hostnum"]))]
            for passenger in group.get("psrs", [])
            if (group_id, int(passenger["hostnum"])) in assignments
        ]
        rows = [
            int(new_topology.seat_map[seat_id]["row"])
            for seat_id in seat_ids
        ]
        compactness_penalty = evaluator_module.group_compactness_penalty(
            seat_ids, new_topology, config
        ) if seat_ids else 0.0
        compactness_score = w_c * compactness_penalty
        value_mismatch_score = 0.0
        preference_mismatch_score = 0.0
        quality_repair_active = (
            old_topology is not None
            and float(
                config.get("algorithm", {}).get(
                    "business_time_limit_seconds", 5.0
                )
            ) >= float(
                config.get("algorithm", {}).get(
                    "three_tier_min_business_time_seconds", 10.0
                )
            )
        )
        if quality_repair_active:
            for passenger in group.get("psrs", []):
                key = (group_id, int(passenger["hostnum"]))
                seat_id = assignments.get(key)
                if seat_id is None:
                    continue
                parts = evaluator_module.individual_assignment_components(
                    passenger,
                    old_topology,
                    new_topology,
                    seat_id,
                    weights,
                    config,
                )
                value_mismatch_score += float(parts["value"])
                preference_mismatch_score += float(parts["preference"])
        row_span = max(rows) - min(rows) if rows else 0
        priority_loss = -(
            compactness_score
            + value_mismatch_score
        )
        metrics[group_id] = {
            "group_id": group_id,
            "size": len(group.get("psrs", [])),
            "assigned": len(seat_ids),
            "row_span": row_span,
            "row_count": len(set(rows)),
            "compactness_penalty": compactness_penalty,
            "compactness_score": compactness_score,
            "value_mismatch_score": value_mismatch_score,
            "preference_mismatch_score": preference_mismatch_score,
            "priority_loss": priority_loss,
            "extreme_dispersion": row_span >= 2,
        }
    return metrics


def _repair_priority_key(metric: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Worst geometric/compactness defect first."""
    return (
        -float(metric.get("priority_loss", 0.0)),
        -int(metric.get("row_span", 0)),
        -float(metric.get("compactness_penalty", 0.0)),
        int(metric.get("group_id", 0)),
    )


def _patterns_have_conditional_ssr_conflict(
    left_group: int,
    left_pattern: Mapping[str, Any],
    right_group: int,
    right_pattern: Mapping[str, Any],
    passenger_objects: Mapping[Tuple[int, int], Passenger],
    seats: Mapping[str, Seat],
) -> bool:
    """Return whether two group patterns violate conditional SSR isolation."""
    def profile(
        group_id: int, pattern: Mapping[str, Any]
    ) -> Tuple[
        Dict[Tuple[int, str], int], Set[int],
        Dict[Tuple[Any, str], int], Set[Any],
    ]:
        row_counts: Dict[Tuple[int, str], int] = defaultdict(int)
        row_active: Set[int] = set()
        subrow_counts: Dict[Tuple[Any, str], int] = defaultdict(int)
        subrow_active: Set[Any] = set()
        for hostnum, seat_id in pattern.get("assignments", ()):
            passenger = passenger_objects[(group_id, int(hostnum))]
            if not passenger.ssr:
                continue
            seat = seats[seat_id]
            subrow = _SEAT_SUBROW[seat_id]
            row_counts[(seat.row, passenger.ssr)] += 1
            subrow_counts[(subrow, passenger.ssr)] += 1
            if passenger.same_row_no_other:
                row_active.add(seat.row)
            if passenger.same_subrow_no_other:
                subrow_active.add(subrow)
        return row_counts, row_active, subrow_counts, subrow_active

    left = profile(left_group, left_pattern)
    right = profile(right_group, right_pattern)
    for left_counts, left_active, right_counts, right_active in (
        (left[0], left[1], right[0], right[1]),
        (left[2], left[3], right[2], right[3]),
    ):
        active_locations = left_active | right_active
        for location in active_locations:
            ssr_types = {
                ssr for loc, ssr in left_counts if loc == location
            } | {
                ssr for loc, ssr in right_counts if loc == location
            }
            if any(
                left_counts.get((location, ssr), 0)
                + right_counts.get((location, ssr), 0) > 1
                for ssr in ssr_types
            ):
                return True
    return False


def _elite_pattern_eviction_candidate(
    patterns: Mapping[Any, Mapping[str, Any]],
) -> Optional[Any]:
    """Choose a removable pattern without ever evicting an incumbent."""
    conflict_counts: Dict[Tuple[int, ...], int] = defaultdict(int)
    for pattern in patterns.values():
        conflict_counts[tuple(pattern.get("conflict_groups", ()))] += 1
    nonpinned = [
        key for key, pattern in patterns.items()
        if not pattern.get("pinned", False)
    ]
    candidates = [
        key for key in nonpinned
        if conflict_counts[
            tuple(patterns[key].get("conflict_groups", ()))
        ] > 1
    ] or nonpinned
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda key: float(patterns[key]["local_score"]),
    )


def _elite_reference_coverage(
    groups: Sequence[Group],
    elite_pattern_store: Mapping[int, Mapping[Any, Mapping[str, Any]]],
    reference_assignments: Mapping[Tuple[int, int], str],
    reference_blocked_by: Mapping[str, Sequence[Sequence[int]]],
) -> Dict[str, Any]:
    """Audit whether every reference group placement exists in the pool."""
    blocked_by_host: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
    for seat_id, owners in reference_blocked_by.items():
        for owner in owners:
            if len(owner) == 2:
                blocked_by_host[(int(owner[0]), int(owner[1]))].add(
                    str(seat_id)
                )

    assignment_present: List[int] = []
    resource_present: List[int] = []
    missing_assignment: List[Dict[str, int]] = []
    missing_resource: List[Dict[str, int]] = []
    for group in groups:
        group_id = group.group_id
        target_assignments = tuple(sorted(
            (
                passenger.hostnum,
                reference_assignments[(group_id, passenger.hostnum)],
            )
            for passenger in group.passengers
        ))
        target_blocked = tuple(sorted(
            (
                passenger.hostnum,
                tuple(sorted(blocked_by_host.get(
                    (group_id, passenger.hostnum), set()
                ))),
            )
            for passenger in group.passengers
            if blocked_by_host.get((group_id, passenger.hostnum))
        ))
        patterns = elite_pattern_store.get(group_id, {}).values()
        assignment_matches = [
            pattern for pattern in patterns
            if tuple(pattern.get("assignments", ())) == target_assignments
        ]
        item = {"group_id": group_id, "size": len(group.passengers)}
        if assignment_matches:
            assignment_present.append(group_id)
        else:
            missing_assignment.append(item)
        if any(
            tuple(pattern.get("blocked_by_host", ())) == target_blocked
            for pattern in assignment_matches
        ):
            resource_present.append(group_id)
        else:
            missing_resource.append(item)
    return {
        "groups": len(groups),
        "assignment_present": len(assignment_present),
        "resource_present": len(resource_present),
        "all_assignment_present": len(assignment_present) == len(groups),
        "all_resource_present": len(resource_present) == len(groups),
        "missing_assignment": missing_assignment,
        "missing_resource": missing_resource,
    }


def improve_protected_multigroup_pattern_mip(
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    groups: List[Group],
    context: AssignmentContext,
    elite_pattern_store: Dict[int, Dict[Any, Dict[str, Any]]],
    scorer: Any,
    weights: Dict[str, float],
    config: Dict[str, Any],
    deadline: float,
) -> Dict[str, Any]:
    """Jointly rebuild one protected group and 1～3 blocking groups.

    Patterns carry both occupied seats and protected-empty resources.  The
    local set-partitioning MIP therefore releases and reallocates those scarce
    resources atomically instead of treating a protected group as immovable.
    """
    started = time.perf_counter()
    algorithm = config.get("algorithm", {})
    business_limit = float(
        algorithm.get("business_time_limit_seconds", 5.0)
    )
    protected_enabled = bool(
        algorithm.get("enable_protected_multigroup_pattern_mip", False)
    )
    priority_enabled = (
        bool(algorithm.get("enable_priority_multigroup_pattern_mip", False))
        and business_limit
        >= float(
            algorithm.get(
                "priority_multigroup_min_business_time_seconds", 10.0
            )
        )
    )
    diagnostics: Dict[str, Any] = {
        "enabled": protected_enabled or priority_enabled,
        "protected_roots_enabled": protected_enabled,
        "priority_roots_enabled": priority_enabled,
        "roots_considered": 0,
        "components_tested": 0,
        "tested_components": [],
        "mip_columns": 0,
        "dynamic_relocation_calls": 0,
        "dynamic_relocation_patterns": 0,
        "conditional_ssr_rows": 0,
        "accepted": 0,
        "accepted_components": [],
        "score_improvement": 0.0,
        "seconds": 0.0,
        "stopped_by_deadline": False,
    }
    if not diagnostics["enabled"] or started >= deadline:
        diagnostics["stopped_by_deadline"] = started >= deadline
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics

    try:
        import highspy
        import numpy as np
        from . import allocation_evaluator as evaluator_module
        from . import exact_column_generation as exact
    except ImportError:
        try:
            import highspy
            import numpy as np
            import allocation_evaluator as evaluator_module
            import exact_column_generation as exact
        except ImportError:
            diagnostics["enabled"] = False
            diagnostics["reason"] = "highspy_or_numpy_unavailable"
            diagnostics["seconds"] = time.perf_counter() - started
            return diagnostics

    passenger_objects = {
        (group.group_id, passenger.hostnum): passenger
        for group in groups for passenger in group.passengers
    }
    keys_by_group = {
        group.group_id: [
            (group.group_id, passenger.hostnum)
            for passenger in group.passengers
        ] for group in groups
    }
    protected_groups = {
        group.group_id for group in groups
        if any(
            passenger.need_single_empty or passenger.need_both_empty
            for passenger in group.passengers
        )
    }
    new_topology = evaluator_module.SeatTopology(new_seats_data, config)
    old_topology = evaluator_module.SeatTopology(old_seats_data, config)
    metrics = _group_repair_metrics(
        groups_data, context.assigned_seats, new_topology,
        weights, config, old_topology,
    )
    priority_groups = {
        group_id for group_id, metric in metrics.items()
        if bool(metric.get("extreme_dispersion", False))
    } if priority_enabled else set()
    root_groups = (
        (protected_groups if protected_enabled else set())
        | priority_groups
    )
    diagnostics["protected_root_count"] = len(protected_groups)
    diagnostics["priority_root_count"] = len(priority_groups)
    if not root_groups:
        diagnostics["reason"] = "no_protected_or_priority_groups"
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics
    roots = sorted(
        root_groups,
        key=lambda group_id: _repair_priority_key(metrics[group_id]),
    )[:max(1, int(algorithm.get("protected_multigroup_root_limit", 8)))]
    max_component_size = min(
        len(keys_by_group),
        max(2, int(algorithm.get("protected_multigroup_max_groups", 4))),
    )
    component_limit = max(
        1, int(algorithm.get("protected_multigroup_component_limit", 30))
    )
    option_limit = max(
        2, int(algorithm.get("protected_multigroup_options_per_group", 20))
    )
    epsilon = float(algorithm.get("local_search_epsilon", 1e-9))
    dynamic_relocation_enabled = bool(algorithm.get(
        "protected_dynamic_relocation_enabled", True
    ))
    dynamic_relocation_seconds = max(0.01, float(algorithm.get(
        "protected_dynamic_relocation_seconds", 0.08
    )))
    dynamic_relocation_columns = max(1, int(algorithm.get(
        "protected_dynamic_relocation_columns", 6
    )))
    raw_group_by_id = {
        int(group["groupId"]): group for group in groups_data
    }
    pricing_config = copy.deepcopy(config)
    pricing = pricing_config.setdefault("column_generation", {})
    pricing["quick_pricing_columns_per_group"] = dynamic_relocation_columns
    new_topology = evaluator_module.SeatTopology(
        new_seats_data, pricing_config
    )
    old_topology = evaluator_module.SeatTopology(
        old_seats_data, pricing_config
    )
    fixed_context = exact._preprocess_fixed_seats(
        groups_data, new_topology, pricing_config
    )
    baby_cost = exact._baby_pairs(
        new_topology, groups_data, weights, pricing_config
    )
    pricing_caches: Dict[int, Any] = {}

    def add_dynamic_relocation_patterns(
        group_id: int,
        outside_resources: Set[str],
        local_deadline: float,
    ) -> None:
        """Price one conflict group after the component seats are released."""
        if not dynamic_relocation_enabled or time.perf_counter() >= local_deadline:
            return
        group = raw_group_by_id[group_id]
        cache = pricing_caches.get(group_id)
        if cache is None:
            cache = exact._build_group_pricing_cache(
                group, new_topology, old_topology, weights,
                pricing_config, fixed_context,
            )
            pricing_caches[group_id] = cache
        filtered_options = [
            [
                option for option in options
                if not set(option.resources).intersection(outside_resources)
            ]
            for options in cache.all_options
        ]
        if any(not options for options in filtered_options):
            return
        released_cache = copy.copy(cache)
        released_cache.all_options = filtered_options
        released_cache.universe = [
            option for options in filtered_options for option in options
        ]
        released_seats = {
            option.seat_id for option in released_cache.universe
        }
        released_cache.seat_ids = tuple(sorted(released_seats))
        released_cache.row_coordinate = {
            seat_id: cache.row_coordinate[seat_id]
            for seat_id in released_seats
        }
        released_cache.x_coordinate = {
            seat_id: cache.x_coordinate[seat_id]
            for seat_id in released_seats
        }
        released_cache.adjacency_edges = tuple(
            (left, right) for left, right in cache.adjacency_edges
            if left in released_seats and right in released_seats
        )
        released_cache.hole_specs = tuple(
            (
                middle,
                tuple(seat for seat in left if seat in released_seats),
                tuple(seat for seat in right if seat in released_seats),
            )
            for middle, left, right in cache.hole_specs
            if middle in released_seats
            and any(seat in released_seats for seat in left)
            and any(seat in released_seats for seat in right)
        )
        call_deadline = min(
            local_deadline,
            time.perf_counter() + dynamic_relocation_seconds,
        )
        pricing["dfs_discovery_time_limit"] = max(
            0.01, call_deadline - time.perf_counter()
        )
        diagnostics["dynamic_relocation_calls"] += 1
        priced = exact._price_group_exact_dfs(
            group, new_topology, weights, pricing_config,
            exact.MasterDuals(group={group_id: 1.0e12}),
            baby_cost, set(), set(), call_deadline, released_cache,
            exact=False, phase_one=False, stop_on_negative=False,
        )
        patterns = elite_pattern_store.setdefault(group_id, {})
        for pattern in priced.patterns[:dynamic_relocation_columns]:
            blocked_by_host: Dict[int, List[str]] = {}
            for seat_id, passenger_key in pattern.blocked_by:
                blocked_by_host.setdefault(
                    int(passenger_key[1]), []
                ).append(seat_id)
            signature = tuple(sorted(
                (key[1], seat_id) for key, seat_id in pattern.assignments
            ))
            blocked_signature = tuple(sorted(
                (hostnum, tuple(sorted(seat_ids)))
                for hostnum, seat_ids in blocked_by_host.items()
            ))
            proposal = {
                key: seat_id for key, seat_id in context.assigned_seats.items()
                if key[0] != group_id
            }
            proposal.update(dict(pattern.assignments))
            pattern_key = (signature, blocked_signature)
            if pattern_key in patterns:
                continue
            occupied_seats = tuple(sorted(
                seat_id for _, seat_id in signature
            ))
            blocked_seats = tuple(sorted({
                seat_id for _, seat_ids in blocked_signature
                for seat_id in seat_ids
            }))
            patterns[pattern_key] = {
                "assignments": signature,
                "occupied_seats": occupied_seats,
                "blocked_seats": blocked_seats,
                "blocked_by_host": blocked_signature,
                "seat_resources": tuple(sorted(
                    set(occupied_seats).union(blocked_seats)
                )),
                "local_score": float(scorer.components(
                    proposal, {group_id}
                )["total_soft_score"]),
                "source": "released_component_pricing",
                "pinned": False,
            }
            diagnostics["dynamic_relocation_patterns"] += 1

    def current_resources(group_id: int) -> Set[str]:
        resources = {
            context.assigned_seats[key] for key in keys_by_group[group_id]
            if key in context.assigned_seats
        }
        for key in keys_by_group[group_id]:
            resources.update(context.assigned_blocked.get(key, set()))
        return resources

    def resource_owner_map() -> Dict[str, int]:
        owners: Dict[str, int] = {}
        for group_id in keys_by_group:
            for seat_id in current_resources(group_id):
                owners[seat_id] = group_id
        return owners

    def rebuild_candidate(
        component: Tuple[int, ...], choices: Dict[int, Any]
    ) -> Optional[AssignmentContext]:
        candidate = copy.deepcopy(context)
        for group_id in component:
            for key in keys_by_group[group_id]:
                candidate.remove_assignment(*key)
        for group_id in sorted(component):
            pattern = elite_pattern_store[group_id][choices[group_id]]
            blocked_by_host = {
                int(hostnum): tuple(seat_ids)
                for hostnum, seat_ids in pattern.get("blocked_by_host", ())
            }
            proposed = [
                ((group_id, int(hostnum)), seat_id)
                for hostnum, seat_id in pattern["assignments"]
            ]
            proposed.sort(
                key=lambda item: passenger_requires_caregiver(
                    passenger_objects[item[0]], config
                )
            )
            proposed_seats = {seat_id for _, seat_id in proposed}
            for key, seat_id in proposed:
                passenger = passenger_objects[key]
                protected = blocked_by_host.get(key[1], ())
                chosen_block = (
                    protected[0]
                    if passenger.need_single_empty and protected else None
                )
                if not candidate.assign_passenger(
                    passenger, seat_id, group_id,
                    exclude_block_seats=proposed_seats - {seat_id},
                    chosen_block=chosen_block,
                ):
                    return None
        return candidate

    initial_score = scorer.components(context.assigned_seats)["total_soft_score"]
    best_score = initial_score
    tested: Set[Tuple[int, ...]] = set()

    for root in roots:
        if time.perf_counter() >= deadline:
            break
        diagnostics["roots_considered"] += 1
        owner_by_resource = resource_owner_map()
        root_patterns = elite_pattern_store.get(root, {})
        candidate_components: List[Tuple[int, ...]] = []
        for pattern in sorted(
            root_patterns.values(),
            key=lambda item: float(item.get("local_score", -float("inf"))),
            reverse=True,
        )[:option_limit]:
            conflicts = sorted({
                owner_by_resource[seat_id]
                for seat_id in pattern.get("seat_resources", ())
                if seat_id in owner_by_resource
                and owner_by_resource[seat_id] != root
            }, key=lambda gid: (
                float(metrics[gid]["priority_loss"]), gid
            ))
            direct_component = tuple(sorted(
                [root, *conflicts[:max_component_size - 1]]
            ))
            if len(direct_component) >= 2:
                candidate_components.append(direct_component)
        for component in candidate_components:
            if (
                component in tested
                or diagnostics["components_tested"] >= component_limit
                or time.perf_counter() >= deadline
            ):
                continue
            tested.add(component)
            if any(not elite_pattern_store.get(group_id) for group_id in component):
                continue
            diagnostics["components_tested"] += 1
            diagnostics["tested_components"].append(component)
            outside_resources = set().union(*(
                current_resources(group_id)
                for group_id in keys_by_group if group_id not in component
            ))
            for conflict_group_id in component:
                if conflict_group_id != root:
                    add_dynamic_relocation_patterns(
                        conflict_group_id, outside_resources, deadline
                    )

            solver = highspy.Highs()
            solver.setOptionValue("output_flag", False)
            solver.setOptionValue("threads", 1)
            solver.setOptionValue("random_seed", 0)
            solver.setOptionValue("mip_rel_gap", 0.0)
            solver.setOptionValue(
                "time_limit", max(0.01, deadline - time.perf_counter())
            )
            empty_i = np.empty(0, dtype=np.int32)
            empty_v = np.empty(0, dtype=np.float64)
            group_rows: Dict[int, int] = {}
            for group_id in component:
                solver.addRow(1.0, 1.0, 0, empty_i, empty_v)
                group_rows[group_id] = solver.getNumRow() - 1
            resources = sorted({
                seat_id for group_id in component
                for pattern in elite_pattern_store[group_id].values()
                for seat_id in pattern.get("seat_resources", ())
                if seat_id not in outside_resources
            })
            resource_rows: Dict[str, int] = {}
            for seat_id in resources:
                solver.addRow(-highspy.kHighsInf, 1.0, 0, empty_i, empty_v)
                resource_rows[seat_id] = solver.getNumRow() - 1
            columns: List[Tuple[int, Any]] = []
            for group_id in component:
                ranked = sorted(
                    elite_pattern_store[group_id].items(),
                    key=lambda item: float(item[1].get("local_score", -float("inf"))),
                    reverse=True,
                )[:option_limit]
                for pattern_key, pattern in ranked:
                    pattern_resources = tuple(pattern.get("seat_resources", ()))
                    if not pattern_resources or set(pattern_resources) & outside_resources:
                        continue
                    indexes = np.asarray(
                        [group_rows[group_id]]
                        + [resource_rows[seat_id] for seat_id in pattern_resources],
                        dtype=np.int32,
                    )
                    values = np.ones(len(indexes), dtype=np.float64)
                    solver.addCol(
                        -float(pattern["local_score"]), 0.0, 1.0,
                        len(indexes), indexes, values,
                    )
                    column = solver.getNumCol() - 1
                    solver.changeColIntegrality(column, highspy.HighsVarType.kInteger)
                    columns.append((group_id, pattern_key))
            diagnostics["mip_columns"] += len(columns)
            if not columns:
                continue
            for left_index, (left_group, left_key) in enumerate(columns):
                left_pattern = elite_pattern_store[left_group][left_key]
                for right_index in range(left_index + 1, len(columns)):
                    right_group, right_key = columns[right_index]
                    if left_group == right_group:
                        continue
                    right_pattern = elite_pattern_store[right_group][right_key]
                    if not _patterns_have_conditional_ssr_conflict(
                        left_group,
                        left_pattern,
                        right_group,
                        right_pattern,
                        passenger_objects,
                        context.seats,
                    ):
                        continue
                    indexes = np.asarray(
                        [left_index, right_index], dtype=np.int32
                    )
                    solver.addRow(
                        -highspy.kHighsInf,
                        1.0,
                        2,
                        indexes,
                        np.ones(2, dtype=np.float64),
                    )
                    diagnostics["conditional_ssr_rows"] += 1
            solver.run()
            solution = solver.getSolution()
            choices = {
                columns[index][0]: columns[index][1]
                for index, value in enumerate(solution.col_value)
                if value > 0.5
            }
            if len(choices) != len(component):
                continue
            candidate = rebuild_candidate(component, choices)
            if candidate is None:
                continue
            violations, unassigned, _ = evaluator_module.count_hard_constraint_violations(
                new_seats_data, groups_data, candidate.assigned_seats, config
            )
            candidate_score = scorer.components(candidate.assigned_seats)[
                "total_soft_score"
            ]
            if violations or unassigned or candidate_score <= best_score + epsilon:
                continue
            delta = candidate_score - best_score
            context.__dict__ = copy.deepcopy(candidate.__dict__)
            best_score = candidate_score
            diagnostics["accepted"] += 1
            diagnostics["accepted_components"].append({
                "groups": component,
                "delta": delta,
                "test_index": diagnostics["components_tested"],
                "elapsed_seconds": time.perf_counter() - started,
            })

    diagnostics["score_improvement"] = best_score - initial_score
    diagnostics["seconds"] = time.perf_counter() - started
    diagnostics["stopped_by_deadline"] = time.perf_counter() >= deadline
    return diagnostics


def improve_with_restricted_pattern_mip(
    new_seats_data: List[dict],
    groups_data: List[dict],
    groups: List[Group],
    context: AssignmentContext,
    elite_pattern_store: Dict[
        int,
        Dict[Tuple[Tuple[int, str], ...], Dict[str, Any]],
    ],
    scorer: Any,
    config: Dict[str, Any],
    deadline: float,
) -> Dict[str, Any]:
    """在已收集的同行组模式上求一个小型全局MIP，并只接受完整可行改进。"""
    started = time.perf_counter()
    diagnostics = {
        "enabled": bool(
            config.get("algorithm", {}).get(
                "enable_restricted_pattern_mip", True
            )
        ),
        "patterns": sum(
            len(patterns)
            for patterns in elite_pattern_store.values()
        ),
        "resource_complete_patterns": sum(
            "seat_resources" in pattern
            for patterns in elite_pattern_store.values()
            for pattern in patterns.values()
        ),
        "protected_resource_patterns": sum(
            bool(pattern.get("blocked_seats"))
            for patterns in elite_pattern_store.values()
            for pattern in patterns.values()
        ),
        "solver_status": None,
        "usable_columns": 0,
        "attempts": 0,
        "invalid_candidates": 0,
        "rebuild_failures": 0,
        "hard_invalid_candidates": 0,
        "nonimproving_candidates": 0,
        "conditional_ssr_rows": 0,
        "accepted": 0,
        "accepted_attempts": [],
        "mip_start_supplied": False,
        "score_improvement": 0.0,
        "seconds": 0.0,
        "stopped_by_deadline": False,
    }
    if not diagnostics["enabled"] or started >= deadline:
        diagnostics["stopped_by_deadline"] = started >= deadline
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics

    try:
        import highspy
        import numpy as np
    except ImportError:
        diagnostics["enabled"] = False
        diagnostics["reason"] = "highspy_or_numpy_unavailable"
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics

    passenger_objects = {
        (group.group_id, passenger.hostnum): passenger
        for group in groups
        for passenger in group.passengers
    }
    keys_by_group = {
        group.group_id: [
            (group.group_id, passenger.hostnum)
            for passenger in group.passengers
        ]
        for group in groups
    }
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.setOptionValue("threads", 1)
    solver.setOptionValue("random_seed", 0)
    solver.setOptionValue("mip_rel_gap", 0.0)
    empty_i = np.empty(0, dtype=np.int32)
    empty_v = np.empty(0, dtype=np.float64)

    group_rows: Dict[int, int] = {}
    for group_id in sorted(keys_by_group):
        solver.addRow(1.0, 1.0, 0, empty_i, empty_v)
        group_rows[group_id] = solver.getNumRow() - 1
    seat_rows: Dict[str, int] = {}
    for seat_id in sorted(context.seats):
        solver.addRow(-highspy.kHighsInf, 1.0, 0, empty_i, empty_v)
        seat_rows[seat_id] = solver.getNumRow() - 1

    columns: List[Tuple[int, Any]] = []
    incumbent_columns: List[int] = []
    for group_id in sorted(keys_by_group):
        patterns = elite_pattern_store.get(group_id, {})
        for pattern_key, pattern in patterns.items():
            signature = tuple(pattern["assignments"])
            seat_ids = [seat_id for _, seat_id in signature]
            seat_resources = tuple(
                pattern.get("seat_resources", seat_ids)
            )
            if (
                len(signature) != len(keys_by_group[group_id])
                or len(set(seat_ids)) != len(seat_ids)
                or any(
                    seat_id not in context.seats
                    for seat_id in seat_ids
                )
                or len(set(seat_resources)) != len(seat_resources)
                or any(
                    seat_id not in context.seats
                    for seat_id in seat_resources
                )
            ):
                continue
            indexes = np.asarray(
                [group_rows[group_id]]
                + [seat_rows[seat_id] for seat_id in seat_resources],
                dtype=np.int32,
            )
            values = np.ones(len(indexes), dtype=np.float64)
            solver.addCol(
                -float(pattern["local_score"]),
                0.0,
                1.0,
                len(indexes),
                indexes,
                values,
            )
            column = solver.getNumCol() - 1
            solver.changeColIntegrality(
                column, highspy.HighsVarType.kInteger
            )
            columns.append((group_id, pattern_key))
            if signature == _current_group_assignment_signature(
                context, keys_by_group[group_id]
            ):
                incumbent_columns.append(column)
    if not columns:
        diagnostics["reason"] = "no_usable_patterns"
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics
    diagnostics["usable_columns"] = len(columns)

    algorithm = config.get("algorithm", {})
    local_branching_enabled = bool(
        algorithm.get("enable_pattern_local_branching", True)
    ) and len(incumbent_columns) == len(keys_by_group) and not any(
        pattern.get("source") == "structured_global_value_block"
        for patterns in elite_pattern_store.values()
        for pattern in patterns.values()
    )
    local_branching_row: Optional[int] = None
    initial_radius = max(
        1, int(algorithm.get("pattern_local_branching_initial_radius", 4))
    )
    radius_growth = max(
        1, int(algorithm.get("pattern_local_branching_radius_growth", 2))
    )
    maximum_radius = max(
        initial_radius,
        int(algorithm.get(
            "pattern_local_branching_max_radius", len(keys_by_group)
        )),
    )
    if local_branching_enabled:
        indexes = np.asarray(incumbent_columns, dtype=np.int32)
        values = np.ones(len(indexes), dtype=np.float64)
        solver.addRow(
            float(len(keys_by_group) - min(initial_radius, len(keys_by_group))),
            highspy.kHighsInf,
            len(indexes), indexes, values,
        )
        local_branching_row = solver.getNumRow() - 1
    diagnostics["local_branching"] = {
        "enabled": local_branching_enabled,
        "initial_radius": initial_radius,
        "radius_growth": radius_growth,
        "maximum_radius": maximum_radius,
        "radius_history": [],
    }
    if len(incumbent_columns) == len(keys_by_group):
        start_indexes = np.asarray(
            incumbent_columns, dtype=np.int32
        )
        start_values = np.ones(
            len(incumbent_columns), dtype=np.float64
        )
        start_status = solver.setSolution(
            len(start_indexes), start_indexes, start_values
        )
        diagnostics["mip_start_supplied"] = (
            start_status == highspy.HighsStatus.kOk
        )

    initial_score = scorer.components(
        context.assigned_seats
    )["total_soft_score"]
    try:
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import allocation_evaluator as evaluator_module
    initial_violations, initial_unassigned, _ = (
        evaluator_module.count_hard_constraint_violations(
            new_seats_data,
            groups_data,
            context.assigned_seats,
            config,
        )
    )
    best_score = initial_score
    best_quality = _allocation_quality(
        initial_violations, initial_unassigned, initial_score
    )
    max_attempts = max(
        1,
        int(
            config.get("algorithm", {}).get(
                "restricted_pattern_mip_attempts", 3
            )
        ),
    )
    for attempt_index in range(max_attempts):
        remaining = deadline - time.perf_counter()
        if remaining <= 0.01:
            break
        radius: Optional[int] = None
        if local_branching_row is not None:
            radius = min(
                len(keys_by_group), maximum_radius,
                initial_radius + attempt_index * radius_growth,
            )
            solver.changeRowBounds(
                local_branching_row,
                float(len(keys_by_group) - radius),
                highspy.kHighsInf,
            )
            diagnostics["local_branching"]["radius_history"].append(radius)
        solver.setOptionValue("time_limit", remaining)
        solver.run()
        diagnostics["solver_status"] = solver.modelStatusToString(
            solver.getModelStatus()
        )
        solution = solver.getSolution()
        selected_columns = [
            index
            for index, value in enumerate(solution.col_value)
            if value > 0.5
        ]
        if not selected_columns:
            break
        diagnostics["attempts"] += 1
        candidate_accepted = False
        choices = {
            columns[index][0]: columns[index][1]
            for index in selected_columns
        }
        if len(choices) != len(keys_by_group):
            break

        candidate_context = copy.deepcopy(context)
        changed_groups = {
            group_id
            for group_id, pattern_key in choices.items()
            if tuple(
                elite_pattern_store[group_id][pattern_key]["assignments"]
            )
            != _current_group_assignment_signature(
                context, keys_by_group[group_id]
            )
        }
        for group_id in changed_groups:
            for key in keys_by_group[group_id]:
                candidate_context.remove_assignment(*key)
        rebuilt = True
        for group_id in sorted(changed_groups):
            selected_pattern = elite_pattern_store[group_id][
                choices[group_id]
            ]
            blocked_by_host = {
                int(hostnum): tuple(seat_ids)
                for hostnum, seat_ids in selected_pattern.get(
                    "blocked_by_host", ()
                )
            }
            proposed = [
                (
                    (group_id, hostnum),
                    seat_id,
                )
                for hostnum, seat_id in selected_pattern["assignments"]
            ]
            proposed.sort(
                key=lambda item: passenger_requires_caregiver(
                    passenger_objects[item[0]], config
                )
            )
            proposed_seats = {seat_id for _, seat_id in proposed}
            for key, seat_id in proposed:
                passenger = passenger_objects[key]
                protected = blocked_by_host.get(key[1], ())
                chosen_block = (
                    protected[0]
                    if passenger.need_single_empty and protected
                    else None
                )
                if not candidate_context.assign_passenger(
                    passenger,
                    seat_id,
                    group_id,
                    exclude_block_seats=proposed_seats - {seat_id},
                    chosen_block=chosen_block,
                ):
                    rebuilt = False
                    break
            if not rebuilt:
                break

        if rebuilt:
            violations, unassigned, _ = (
                evaluator_module.count_hard_constraint_violations(
                    new_seats_data,
                    groups_data,
                    candidate_context.assigned_seats,
                    config,
                )
            )
            candidate_score = scorer.components(
                candidate_context.assigned_seats
            )["total_soft_score"]
            candidate_quality = _allocation_quality(
                violations, unassigned, candidate_score
            )
            if candidate_quality > best_quality:
                improvement = candidate_score - best_score
                context.__dict__ = copy.deepcopy(
                    candidate_context.__dict__
                )
                best_score = candidate_score
                best_quality = candidate_quality
                diagnostics["accepted"] += 1
                diagnostics["accepted_attempts"].append({
                    "attempt": attempt_index + 1,
                    "radius": radius,
                    "score_improvement": improvement,
                    "changed_groups": len(changed_groups),
                    "selected_patterns": [
                        {
                            "group_id": group_id,
                            "source": elite_pattern_store[group_id][
                                choices[group_id]
                            ]["source"],
                            "assignments": [
                                [hostnum, seat_id]
                                for hostnum, seat_id in elite_pattern_store[
                                    group_id
                                ][choices[group_id]]["assignments"]
                            ],
                            "blocked_by_host": [
                                [hostnum, list(seat_ids)]
                                for hostnum, seat_ids in elite_pattern_store[
                                    group_id
                                ][choices[group_id]].get(
                                    "blocked_by_host", ()
                                )
                            ],
                        }
                        for group_id in sorted(changed_groups)
                    ],
                })
                candidate_accepted = True
            if candidate_accepted:
                pass
            elif violations or unassigned:
                diagnostics["hard_invalid_candidates"] += 1
            else:
                diagnostics["nonimproving_candidates"] += 1
        else:
            diagnostics["rebuild_failures"] += 1
            for left_offset, left_index in enumerate(selected_columns):
                left_group, left_key = columns[left_index]
                left_pattern = elite_pattern_store[left_group][left_key]
                for right_index in selected_columns[left_offset + 1:]:
                    right_group, right_key = columns[right_index]
                    if left_group == right_group:
                        continue
                    right_pattern = elite_pattern_store[right_group][
                        right_key
                    ]
                    if not _patterns_have_conditional_ssr_conflict(
                        left_group,
                        left_pattern,
                        right_group,
                        right_pattern,
                        passenger_objects,
                        context.seats,
                    ):
                        continue
                    indexes = np.asarray(
                        [left_index, right_index], dtype=np.int32
                    )
                    solver.addRow(
                        -highspy.kHighsInf,
                        1.0,
                        2,
                        indexes,
                        np.ones(2, dtype=np.float64),
                    )
                    diagnostics["conditional_ssr_rows"] += 1
        if not candidate_accepted:
            diagnostics["invalid_candidates"] += 1
        indexes = np.asarray(selected_columns, dtype=np.int32)
        values = np.ones(len(indexes), dtype=np.float64)
        solver.addRow(
            -highspy.kHighsInf,
            float(len(selected_columns) - 1),
            len(indexes),
            indexes,
            values,
        )

    diagnostics["score_improvement"] = best_score - initial_score
    diagnostics["seconds"] = time.perf_counter() - started
    diagnostics["stopped_by_deadline"] = (
        time.perf_counter() >= deadline
    )
    return diagnostics


def generate_structured_group_patterns(
    new_seats_data: List[dict],
    old_seats_data: List[dict],
    groups_data: List[dict],
    context: AssignmentContext,
    scorer: Any,
    weights: Dict[str, float],
    config: Dict[str, Any],
    deadline: float,
    pattern_recorder: Callable[..., None],
) -> Dict[str, Any]:
    """用完整放置DFS为困难同行组生成短时、多样化候选模式。

    该阶段只发现线上受限主问题的候选列，不参与LP认证。放置域由真实
    座位拓扑生成，因此同时适用于当前六种单/双通道布局；每个模式均
    携带占座和保护空座资源。
    """
    started = time.perf_counter()
    diagnostics = {
        "enabled": bool(
            config.get("algorithm", {}).get(
                "enable_structured_pattern_generation", True
            )
        ),
        "groups_attempted": 0,
        "groups_with_patterns": 0,
        "patterns_generated": 0,
        "row_windows_attempted": 0,
        "dfs_nodes": 0,
        "seconds": 0.0,
        "stopped_by_deadline": False,
        "repair_queue": [],
        "extreme_groups_attempted": 0,
        "span_reducing_patterns": 0,
        "tier_counts": {
            "rigid": 0,
            "relaxed": 0,
            "value_block": 0,
            "global_value_block": 0,
            "rebuilt": 0,
        },
        "three_tier_active": False,
    }
    if not diagnostics["enabled"] or started >= deadline:
        diagnostics["stopped_by_deadline"] = started >= deadline
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics

    try:
        from . import exact_column_generation as exact
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import exact_column_generation as exact
        import allocation_evaluator as evaluator_module

    pricing_config = copy.deepcopy(config)
    pricing = pricing_config.setdefault("column_generation", {})
    algorithm = config.get("algorithm", {})
    per_group_limit = max(
        0.005,
        float(algorithm.get("structured_pattern_dfs_per_group", 0.08)),
    )
    pricing["dfs_discovery_time_limit"] = per_group_limit
    pricing["quick_pricing_columns_per_group"] = max(
        2, int(algorithm.get("structured_patterns_per_group", 12))
    )
    pricing_config["_column_generation_active_ssr_types"] = sorted({
        str(passenger.get("ssr"))
        for group in groups_data
        for passenger in group.get("psrs", [])
        if passenger.get("ssr")
    })
    new_topology = evaluator_module.SeatTopology(
        new_seats_data, pricing_config
    )
    old_topology = evaluator_module.SeatTopology(
        old_seats_data, pricing_config
    )
    fixed_context = exact._preprocess_fixed_seats(
        groups_data, new_topology, pricing_config
    )
    baby_cost = exact._baby_pairs(
        new_topology, groups_data, weights, pricing_config
    )
    base_min_group_size = max(
        1, int(algorithm.get("structured_pattern_min_group_size", 5))
    )
    min_group_size = base_min_group_size
    resource_demand = _seat_demand_for_budgeting(groups_data)
    traveler_demand = sum(
        len(group.get("psrs", [])) for group in groups_data
    )
    full_resource_global_blocks = (
        bool(algorithm.get("enable_global_full_resource_blocks", True))
        and float(algorithm.get("business_time_limit_seconds", 5.0)) >= 10.0
        and len(new_seats_data) < len(old_seats_data)
        and resource_demand == len(new_seats_data)
        and resource_demand > traveler_demand
    )
    if float(
        algorithm.get("business_time_limit_seconds", 5.0)
    ) >= float(
        algorithm.get(
            "structured_small_group_min_business_time_seconds", 10.0
        )
    ) and len(new_seats_data) < len(old_seats_data):
        min_group_size = min(
            min_group_size,
            max(
                1,
                int(
                    algorithm.get(
                        "structured_research_min_group_size", 2
                    )
                ),
            ),
        )
    repair_metrics = _group_repair_metrics(
        groups_data,
        context.assigned_seats,
        new_topology,
        weights,
        config,
        old_topology,
    )
    def is_primary_pattern_group(group: Mapping[str, Any]) -> bool:
        passengers = group.get("psrs", [])
        return len(passengers) >= base_min_group_size or any(
            bool(passenger.get("ssr"))
            or passenger.get("needCared", "N") == "Y"
            or (passenger.get("mandatoryRule", {}) or {}).get(
                "needSingleSideEmpty", "N"
            ) == "Y"
            or (passenger.get("mandatoryRule", {}) or {}).get(
                "needBothSideEmpty", "N"
            ) == "Y"
            for passenger in passengers
        )

    ordered_groups = sorted(
        groups_data,
        key=lambda group: (
            not is_primary_pattern_group(group),
            _repair_priority_key(
                repair_metrics[int(group["groupId"])]
            ),
            -sum(
                bool(passenger.get("ssr"))
                or passenger.get("needCared", "N") == "Y"
                or (passenger.get("mandatoryRule", {}) or {}).get(
                    "needSingleSideEmpty", "N"
                ) == "Y"
                or (passenger.get("mandatoryRule", {}) or {}).get(
                    "needBothSideEmpty", "N"
                ) == "Y"
                for passenger in group.get("psrs", [])
            ),
            -len(group.get("psrs", [])),
            int(group["groupId"]),
        ),
    )
    diagnostics["repair_queue"] = [
        repair_metrics[int(group["groupId"])]
        for group in ordered_groups[:20]
    ]
    for group in ordered_groups:
        if time.perf_counter() >= deadline:
            diagnostics["stopped_by_deadline"] = True
            break
        passengers = group.get("psrs", [])
        difficult = len(passengers) >= min_group_size or any(
            bool(passenger.get("ssr"))
            or passenger.get("needCared", "N") == "Y"
            or (passenger.get("mandatoryRule", {}) or {}).get(
                "needSingleSideEmpty", "N"
            ) == "Y"
            or (passenger.get("mandatoryRule", {}) or {}).get(
                "needBothSideEmpty", "N"
            ) == "Y"
            for passenger in passengers
        )
        if not difficult:
            continue
        group_id = int(group["groupId"])
        current_metric = repair_metrics[group_id]
        current_row_span = int(current_metric["row_span"])
        target_span = max(0, current_row_span - 2)
        if current_row_span >= 2:
            diagnostics["extreme_groups_attempted"] += 1
        diagnostics["groups_attempted"] += 1
        cache = exact._build_group_pricing_cache(
            group,
            new_topology,
            old_topology,
            weights,
            pricing_config,
            fixed_context,
        )
        seat_at = {
            (int(seat["row"]), str(seat["col"])): seat_id
            for seat_id, seat in new_topology.seat_map.items()
        }
        option_by_passenger_seat: Dict[int, Dict[str, List[Any]]] = {}
        for passenger_index, options in enumerate(cache.all_options):
            mapping: Dict[str, List[Any]] = {}
            for option in options:
                mapping.setdefault(option.seat_id, []).append(option)
            option_by_passenger_seat[passenger_index] = mapping

        def build_target_pattern(
            targets: Mapping[int, str],
        ) -> Optional[Any]:
            selected: List[Any] = []
            used_resources: Set[str] = set()

            def choose(passenger_index: int) -> bool:
                if passenger_index == len(passengers):
                    return exact._caregiver_ok(
                        group, selected, new_topology, pricing_config
                    )
                target = targets.get(passenger_index)
                if target is None:
                    return False
                for option in option_by_passenger_seat[
                    passenger_index
                ].get(target, ()):
                    resources = set(option.resources)
                    if resources & used_resources:
                        continue
                    selected.append(option)
                    used_resources.update(resources)
                    if choose(passenger_index + 1):
                        return True
                    selected.pop()
                    used_resources.difference_update(resources)
                return False

            if not choose(0):
                return None
            return exact._pattern_from_placements(
                group, selected, new_topology, weights,
                pricing_config, baby_cost,
            )

        current_targets = {
            passenger_index: context.assigned_seats.get(
                (group_id, int(passenger["hostnum"]))
            )
            for passenger_index, passenger in enumerate(passengers)
        }
        tiered_patterns: Dict[Any, Tuple[Any, str]] = {}
        three_tier_active = (
            bool(algorithm.get("enable_three_tier_patterns", True))
            and float(algorithm.get("business_time_limit_seconds", 5.0))
            >= float(
                algorithm.get(
                    "three_tier_min_business_time_seconds", 0.0
                )
            )
        )
        diagnostics["three_tier_active"] = (
            diagnostics["three_tier_active"] or three_tier_active
        )
        if (
            three_tier_active
            and all(current_targets.values())
        ):
            rigid_shift = max(
                0, int(algorithm.get("structured_rigid_shift_rows", 3))
            )
            for row_delta in range(-rigid_shift, rigid_shift + 1):
                for mirrored in (False, True):
                    targets: Dict[int, str] = {}
                    for passenger_index, seat_id in current_targets.items():
                        seat = new_topology.seat_map[seat_id]
                        target_row = int(seat["row"]) + row_delta
                        target_col = str(seat["col"])
                        if mirrored and target_row in new_topology.row_seats:
                            row_seats = new_topology.row_seats[target_row]
                            source_index = new_topology.index_in_row[seat_id]
                            if source_index < len(row_seats):
                                target_col = str(
                                    row_seats[-1 - source_index]["col"]
                                )
                        target = seat_at.get((target_row, target_col))
                        if target is None:
                            targets = {}
                            break
                        targets[passenger_index] = target
                    pattern = build_target_pattern(targets) if targets else None
                    if pattern is not None:
                        tiered_patterns[
                            (pattern.signature, pattern.blocked_by)
                        ] = (pattern, "rigid")

            # 松弛层：在刚性原形上，单独把一个子排的成员前后移动一排。
            subrows = sorted({
                new_topology.subrow[seat_id]
                for seat_id in current_targets.values()
            }, key=str)
            for subrow in subrows:
                for row_delta in (-1, 1):
                    targets = dict(current_targets)
                    valid = True
                    for passenger_index, seat_id in current_targets.items():
                        if new_topology.subrow[seat_id] != subrow:
                            continue
                        seat = new_topology.seat_map[seat_id]
                        target = seat_at.get((
                            int(seat["row"]) + row_delta,
                            str(seat["col"]),
                        ))
                        if target is None:
                            valid = False
                            break
                        targets[passenger_index] = target
                    pattern = (
                        build_target_pattern(targets) if valid else None
                    )
                    if pattern is not None:
                        tiered_patterns[
                            (pattern.signature, pattern.blocked_by)
                        ] = (pattern, "relaxed")
        group_deadline = min(
            deadline,
            time.perf_counter()
            + (
                max(per_group_limit, 0.16)
                if full_resource_global_blocks
                else per_group_limit
            ),
        )
        seats_by_row: Dict[int, Set[str]] = {}
        for option in cache.universe:
            row = int(new_topology.seat_map[option.seat_id]["row"])
            seats_by_row.setdefault(row, set()).add(option.seat_id)
        rows = sorted(seats_by_row)
        max_row_capacity = max(
            (len(seats) for seats in seats_by_row.values()),
            default=1,
        )
        protected_demand = sum(
            2
            if (passenger.get("mandatoryRule", {}) or {}).get(
                "needBothSideEmpty", "N"
            ) == "Y"
            else 1
            if (passenger.get("mandatoryRule", {}) or {}).get(
                "needSingleSideEmpty", "N"
            ) == "Y"
            else 0
            for passenger in passengers
        )
        minimum_width = max(
            1,
            (
                len(passengers)
                + protected_demand
                + max_row_capacity
                - 1
            )
            // max_row_capacity,
        )
        fixed_rows = {
            int(new_topology.seat_map[seat_id]["row"])
            for passenger in passengers
            for seat_id in [
                (passenger.get("newSeat", {}) or {}).get("seatNum")
            ]
            if seat_id in new_topology.seat_map
        }
        old_rows = [
            int(old_topology.seat_map[seat_id]["row"])
            for passenger in passengers
            for seat_id in [
                (passenger.get("oldSeat", {}) or {}).get("seatNum")
            ]
            if seat_id in old_topology.seat_map
        ]
        old_center = (
            sum(old_rows) / len(old_rows) if old_rows else float(rows[0])
        ) if rows else 0.0
        row_windows: List[Tuple[int, ...]] = []
        max_width = min(
            len(rows),
            minimum_width + int(
                algorithm.get("structured_pattern_extra_rows", 2)
            ),
        )
        for width in range(minimum_width, max_width + 1):
            for start_index in range(0, len(rows) - width + 1):
                window = tuple(rows[start_index:start_index + width])
                if fixed_rows.issubset(window):
                    row_windows.append(window)
        required_classes: Dict[str, int] = {}
        for passenger in passengers:
            cabin = str(passenger.get("cabin", "")).strip()
            required_class = CABIN_CLASS_MAP.get(cabin.upper(), cabin)
            if required_class:
                required_classes[required_class] = (
                    required_classes.get(required_class, 0) + 1
                )

        def window_class_shortage(window: Tuple[int, ...]) -> int:
            available: Dict[str, int] = {}
            for row in window:
                for seat in new_topology.row_seats.get(row, ()):
                    seat_class = str(seat.get("seatClass"))
                    available[seat_class] = available.get(seat_class, 0) + 1
            return sum(
                max(0, demand - available.get(seat_class, 0))
                for seat_class, demand in required_classes.items()
            )

        desired_values: List[Tuple[str, float]] = []
        for passenger in passengers:
            cabin = str(passenger.get("cabin", "")).strip()
            required_class = CABIN_CLASS_MAP.get(cabin.upper(), cabin)
            old_seat_id = (passenger.get("oldSeat", {}) or {}).get(
                "seatNum"
            )
            if old_seat_id not in old_topology.seat_map:
                continue
            explicit = evaluator_module._numeric_seat_value(
                (passenger.get("oldSeat", {}) or {}).get("seatValue")
            )
            old_value = (
                explicit
                if explicit is not None
                else evaluator_module._derived_seat_value(
                    old_topology.seat_map[old_seat_id], config
                )
            )
            desired_values.append((required_class, old_value))

        def window_value_mismatch(window: Tuple[int, ...]) -> float:
            if current_metric["value_mismatch_score"] >= 0.0:
                return 0.0
            available = [
                seat
                for row in window
                for seat in new_topology.row_seats.get(row, ())
            ]
            mismatch = 0.0
            for seat_class, old_value in desired_values:
                compatible = [
                    evaluator_module._derived_seat_value(seat, config)
                    for seat in available
                    if str(seat.get("seatClass")) == seat_class
                ]
                mismatch += min(
                    (abs(value - old_value) for value in compatible),
                    default=float("inf"),
                )
            return mismatch

        row_windows.sort(key=lambda window: (
            window_class_shortage(window),
            window_value_mismatch(window),
            0 if max(window) - min(window) <= target_span else 1,
            max(window) - min(window),
            abs(sum(window) / len(window) - old_center),
            len(window),
            window,
        ))
        all_row_windows = list(row_windows)
        row_windows = row_windows[:max(
            1,
            int(algorithm.get("structured_pattern_window_limit", 12)),
        )]

        ordinary_group = _is_ordinary_value_block_group(group)
        if ordinary_group and (
            current_metric["value_mismatch_score"] < 0.0
            or full_resource_global_blocks
        ):
            group_keys_ordered = [
                (group_id, int(passenger["hostnum"]))
                for passenger in passengers
            ]
            seen_blocks: Set[Tuple[str, ...]] = set()
            block_windows = (
                all_row_windows
                if full_resource_global_blocks
                else row_windows[:4]
            )
            for window in block_windows:
                if time.perf_counter() >= group_deadline:
                    break
                pool = sorted({
                    option.seat_id
                    for options in cache.all_options
                    for option in options
                    if int(new_topology.seat_map[option.seat_id]["row"])
                    in window
                })
                if len(pool) < len(passengers):
                    continue
                anchors = (
                    [
                        pool[0],
                        pool[len(pool) // 4],
                        pool[len(pool) // 2],
                        pool[(3 * len(pool)) // 4],
                        pool[-1],
                    ]
                    if full_resource_global_blocks
                    else pool
                )
                for anchor in dict.fromkeys(anchors):
                    if time.perf_counter() >= group_deadline:
                        break
                    block = tuple(sorted(
                        sorted(
                            pool,
                            key=lambda seat_id: (
                                abs(
                                    new_topology.x[seat_id]
                                    - new_topology.x[anchor]
                                )
                                + abs(
                                    new_topology.y[seat_id]
                                    - new_topology.y[anchor]
                                ),
                                abs(
                                    new_topology.y[seat_id]
                                    - new_topology.y[anchor]
                                ),
                                new_topology.x[seat_id],
                                seat_id,
                            ),
                        )[:len(passengers)]
                    ))
                    if block in seen_blocks:
                        continue
                    seen_blocks.add(block)
                    size = len(block)
                    dp = [-float("inf")] * (1 << size)
                    parent: List[Optional[Tuple[int, int]]] = [
                        None
                    ] * (1 << size)
                    dp[0] = 0.0
                    for mask in range(1 << size):
                        passenger_index = mask.bit_count()
                        if (
                            passenger_index >= size
                            or dp[mask] == -float("inf")
                        ):
                            continue
                        key = group_keys_ordered[passenger_index]
                        for seat_index, seat_id in enumerate(block):
                            bit = 1 << seat_index
                            if (
                                mask & bit
                                or seat_id not in option_by_passenger_seat[
                                    passenger_index
                                ]
                            ):
                                continue
                            next_mask = mask | bit
                            score = dp[mask] + sum(
                                scorer._individual(key, seat_id).values()
                            )
                            if score > dp[next_mask]:
                                dp[next_mask] = score
                                parent[next_mask] = (mask, seat_index)
                    mask = (1 << size) - 1
                    if parent[mask] is None:
                        continue
                    targets: Dict[int, str] = {}
                    while mask:
                        previous, seat_index = parent[mask]  # type: ignore[misc]
                        targets[previous.bit_count()] = block[seat_index]
                        mask = previous
                    pattern = build_target_pattern(targets)
                    if pattern is not None:
                        tiered_patterns[
                            (pattern.signature, pattern.blocked_by)
                        ] = (
                            pattern,
                            "global_value_block"
                            if full_resource_global_blocks
                            else "value_block",
                        )
        pattern_by_signature: Dict[Any, Tuple[Any, str]] = dict(
            tiered_patterns
        )
        per_window_limit = max(
            0.06,
            per_group_limit / max(1, len(row_windows)),
        )
        for window in row_windows:
            if time.perf_counter() >= group_deadline:
                break
            pricing["dfs_discovery_time_limit"] = per_window_limit
            allowed_rows = set(window)
            filtered_options = [
                [
                    option for option in options
                    if int(new_topology.seat_map[option.seat_id]["row"])
                    in allowed_rows
                ]
                for options in cache.all_options
            ]
            if any(not options for options in filtered_options):
                continue
            window_cache = copy.copy(cache)
            window_cache.all_options = filtered_options
            window_cache.universe = [
                option
                for options in filtered_options
                for option in options
            ]
            window_seats = {
                option.seat_id for option in window_cache.universe
            }
            window_cache.seat_ids = tuple(sorted(window_seats))
            window_cache.row_coordinate = {
                seat_id: cache.row_coordinate[seat_id]
                for seat_id in window_seats
            }
            window_cache.x_coordinate = {
                seat_id: cache.x_coordinate[seat_id]
                for seat_id in window_seats
            }
            window_cache.adjacency_edges = tuple(
                (left, right)
                for left, right in cache.adjacency_edges
                if left in window_seats and right in window_seats
            )
            window_cache.hole_specs = tuple(
                (
                    middle,
                    tuple(seat for seat in left if seat in window_seats),
                    tuple(seat for seat in right if seat in window_seats),
                )
                for middle, left, right in cache.hole_specs
                if middle in window_seats
                and any(seat in window_seats for seat in left)
                and any(seat in window_seats for seat in right)
            )
            diagnostics["row_windows_attempted"] += 1
            priced = exact._price_group_exact_dfs(
                group,
                new_topology,
                weights,
                pricing_config,
                exact.MasterDuals(group={group_id: 1.0e12}),
                baby_cost,
                set(),
                set(),
                min(group_deadline, time.perf_counter() + per_window_limit),
                window_cache,
                exact=False,
                phase_one=False,
                stop_on_negative=False,
            )
            diagnostics["dfs_nodes"] += priced.dfs_nodes
            for pattern in priced.patterns:
                pattern_by_signature[
                    (pattern.signature, pattern.blocked_by)
                ] = (pattern, "rebuilt")
        accepted = 0
        group_keys = {
            (group_id, int(passenger["hostnum"]))
            for passenger in passengers
        }
        for pattern, tier in pattern_by_signature.values():
            proposal = {
                key: seat_id
                for key, seat_id in context.assigned_seats.items()
                if key not in group_keys
            }
            proposal.update(dict(pattern.assignments))
            local_score = scorer.components(
                proposal, {group_id}
            )["total_soft_score"]
            pattern_rows = [
                int(new_topology.seat_map[seat_id]["row"])
                for _, seat_id in pattern.assignments
            ]
            pattern_span = (
                max(pattern_rows) - min(pattern_rows)
                if pattern_rows else 0
            )
            if current_row_span >= 2 and pattern_span <= target_span:
                diagnostics["span_reducing_patterns"] += 1
            blocked_by_host: Dict[int, List[str]] = {}
            for seat_id, passenger_key in pattern.blocked_by:
                blocked_by_host.setdefault(
                    int(passenger_key[1]), []
                ).append(seat_id)
            pattern_recorder(
                group_id,
                tuple(pattern.assignments),
                local_score,
                f"structured_{tier}",
                blocked_by_host,
                tier == "global_value_block",
            )
            diagnostics["tier_counts"][tier] += 1
            accepted += 1
        if accepted:
            diagnostics["groups_with_patterns"] += 1
            diagnostics["patterns_generated"] += accepted

    diagnostics["seconds"] = time.perf_counter() - started
    diagnostics["stopped_by_deadline"] = (
        diagnostics["stopped_by_deadline"]
        or time.perf_counter() >= deadline
    )
    return diagnostics


def generate_special_dual_pricing_patterns(
    new_seats_data: List[dict], old_seats_data: List[dict],
    groups_data: List[dict], context: AssignmentContext, scorer: Any,
    weights: Dict[str, float], config: Dict[str, Any],
    elite_pattern_store: Mapping[int, Mapping[Any, Mapping[str, Any]]],
    deadline: float, pattern_recorder: Callable[..., None], enabled: bool,
) -> Dict[str, Any]:
    """Price a small repair-priority subset of unfixed special groups."""
    started = time.perf_counter()
    diagnostics = {
        "enabled": enabled,
        "lp_status": "disabled", "lp_columns": 0,
        "groups_attempted": 0, "negative_patterns": 0,
        "dfs_nodes": 0, "accepted_patterns": [], "seconds": 0.0,
    }
    if not diagnostics["enabled"] or started >= deadline:
        return diagnostics
    try:
        import highspy
        import numpy as np
    except (ImportError, ModuleNotFoundError):
        diagnostics["lp_status"] = "dependency_unavailable"
        return diagnostics
    try:
        from . import allocation_evaluator as evaluator_module
        from . import exact_column_generation as exact
    except ImportError:
        import allocation_evaluator as evaluator_module
        import exact_column_generation as exact

    special_groups = [
        group for group in groups_data
        if not any(p.get("newSeat") for p in group.get("psrs", []))
        and any(
            bool(p.get("ssr")) or p.get("needCared", "N") == "Y"
            or (p.get("mandatoryRule", {}) or {}).get(
                "needSingleSideEmpty", "N"
            ) == "Y"
            or (p.get("mandatoryRule", {}) or {}).get(
                "needBothSideEmpty", "N"
            ) == "Y"
            for p in group.get("psrs", [])
        )
    ]
    if not special_groups:
        diagnostics["lp_status"] = "no_special_groups"
        return diagnostics

    group_ids = sorted(int(group["groupId"]) for group in groups_data)
    seat_ids = sorted(str(seat["seatId"]) for seat in new_seats_data)
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.setOptionValue("threads", 1)
    solver.setOptionValue("time_limit", max(0.01, deadline - started))
    empty_i = np.empty(0, dtype=np.int32)
    empty_v = np.empty(0, dtype=np.float64)
    group_rows: Dict[int, int] = {}
    seat_rows: Dict[str, int] = {}
    for group_id in group_ids:
        solver.addRow(1.0, 1.0, 0, empty_i, empty_v)
        group_rows[group_id] = solver.getNumRow() - 1
    for seat_id in seat_ids:
        solver.addRow(-highspy.kHighsInf, 1.0, 0, empty_i, empty_v)
        seat_rows[seat_id] = solver.getNumRow() - 1

    def add_column(group_id: int, score: float, resources: Iterable[str]) -> None:
        rows = np.asarray(
            [group_rows[group_id], *(seat_rows[seat] for seat in resources)],
            dtype=np.int32,
        )
        solver.addCol(
            -float(score), 0.0, highspy.kHighsInf, len(rows), rows,
            np.ones(len(rows), dtype=np.float64),
        )
        diagnostics["lp_columns"] += 1

    for group in groups_data:
        group_id = int(group["groupId"])
        current_resources = {
            context.assigned_seats[(group_id, int(p["hostnum"]))]
            for p in group.get("psrs", [])
        }
        for passenger in group.get("psrs", []):
            current_resources.update(context.assigned_blocked.get(
                (group_id, int(passenger["hostnum"])), set()
            ))
        add_column(
            group_id,
            scorer.components(context.assigned_seats, {group_id})[
                "total_soft_score"
            ],
            sorted(current_resources),
        )
        for pattern in elite_pattern_store.get(group_id, {}).values():
            add_column(
                group_id, float(pattern["local_score"]),
                pattern.get("seat_resources", ()),
            )
    run_status = solver.run()
    model_status = solver.getModelStatus()
    diagnostics["lp_status"] = solver.modelStatusToString(model_status)
    if (
        run_status not in (highspy.HighsStatus.kOk, highspy.HighsStatus.kWarning)
        or model_status != highspy.HighsModelStatus.kOptimal
    ):
        diagnostics["seconds"] = time.perf_counter() - started
        return diagnostics
    row_dual = solver.getSolution().row_dual
    duals = exact.MasterDuals(
        group={gid: float(row_dual[row]) for gid, row in group_rows.items()},
        seat={seat: float(row_dual[row]) for seat, row in seat_rows.items()},
    )

    pricing_config = copy.deepcopy(config)
    pricing = pricing_config.setdefault("column_generation", {})
    pricing["quick_pricing_columns_per_group"] = 4
    pricing["dfs_discovery_time_limit"] = 0.05
    pricing_config["_column_generation_active_ssr_types"] = sorted({
        str(p.get("ssr")) for group in groups_data
        for p in group.get("psrs", []) if p.get("ssr")
    })
    new_topology = evaluator_module.SeatTopology(new_seats_data, pricing_config)
    old_topology = evaluator_module.SeatTopology(old_seats_data, pricing_config)
    fixed_context = exact._preprocess_fixed_seats(
        groups_data, new_topology, pricing_config
    )
    baby_cost = exact._baby_pairs(
        new_topology, groups_data, weights, pricing_config
    )
    repair_metrics = _group_repair_metrics(
        groups_data, context.assigned_seats, new_topology,
        weights, config, old_topology,
    )
    special_groups.sort(key=lambda group: _repair_priority_key(
        repair_metrics[int(group["groupId"])]
    ))
    for group in special_groups[:13]:
        if time.perf_counter() >= deadline:
            break
        group_id = int(group["groupId"])
        cache = exact._build_group_pricing_cache(
            group, new_topology, old_topology, weights,
            pricing_config, fixed_context,
        )
        priced = exact._price_group_exact_dfs(
            group, new_topology, weights, pricing_config, duals,
            baby_cost, set(), set(),
            min(deadline, time.perf_counter() + 0.05), cache,
            exact=False, phase_one=False, stop_on_negative=False,
        )
        diagnostics["groups_attempted"] += 1
        diagnostics["dfs_nodes"] += priced.dfs_nodes
        for pattern in priced.patterns:
            reduced_cost = exact._master_column_reduced_cost(
                pattern, duals, baby_cost
            )
            if reduced_cost >= -1.0e-7:
                continue
            proposal = {
                key: seat for key, seat in context.assigned_seats.items()
                if key[0] != group_id
            }
            proposal.update(dict(pattern.assignments))
            blocked_by_host: Dict[int, List[str]] = defaultdict(list)
            for seat_id, passenger_key in pattern.blocked_by:
                blocked_by_host[int(passenger_key[1])].append(seat_id)
            pattern_recorder(
                group_id, tuple(pattern.assignments),
                scorer.components(proposal, {group_id})["total_soft_score"],
                "special_dual_pricing", blocked_by_host, True,
            )
            diagnostics["negative_patterns"] += 1
            diagnostics["accepted_patterns"].append({
                "group_id": group_id,
                "reduced_cost": float(reduced_cost),
                "assignments": [list(item) for item in pattern.assignments],
                "blocked_by": [
                    [seat_id, list(passenger_key)]
                    for seat_id, passenger_key in pattern.blocked_by
                ],
            })
    diagnostics["seconds"] = time.perf_counter() - started
    return diagnostics


def _seat_demand_for_budgeting(groups_data: Sequence[dict]) -> int:
    """Return travelers plus the seats reserved by empty-seat rules."""
    demand = 0
    for group in groups_data:
        for passenger in group.get("psrs", []):
            mandatory = passenger.get("mandatoryRule", {})
            demand += 1
            if mandatory.get("needBothSideEmpty", "N") == "Y":
                demand += 2
            elif mandatory.get("needSingleSideEmpty", "N") == "Y":
                demand += 1
    return demand


def _is_ordinary_value_block_group(group: Mapping[str, Any]) -> bool:
    """Return whether a group can use deterministic value-compatible blocks."""
    return all(
        not passenger.get("ssr")
        and passenger.get("needCared", "N") != "Y"
        and not passenger.get("newSeat")
        and (passenger.get("mandatoryRule", {}) or {}).get(
            "needSingleSideEmpty", "N"
        ) != "Y"
        and (passenger.get("mandatoryRule", {}) or {}).get(
            "needBothSideEmpty", "N"
        ) != "Y"
        for passenger in group.get("psrs", [])
    )


def _run_allocation_single_cabin(new_seats_data: list, old_seats_data: list,
                                 groups_data: list, weights: dict,
                                 config: dict) -> Tuple[dict, float]:
    """
    执行整个座位分配流程：
    1. 初始化座位、乘客、组对象
    2. 分三阶段分配：预留座位 -> 配对SSR -> 剩余乘客
    返回分配结果字典和使用统一评分公式计算的综合分数。
    """
    allocation_start = time.perf_counter()
    algorithm_config = config.get("algorithm", {})
    business_time_limit = max(
        0.1, float(algorithm_config.get("business_time_limit_seconds", 5.0))
    )
    scoring_reserve = min(
        business_time_limit * 0.2,
        max(0.02, float(algorithm_config.get("scoring_time_reserve", 0.1))),
    )
    search_deadline = (
        allocation_start + business_time_limit - scoring_reserve
    )
    usable_time = max(0.0, search_deadline - allocation_start)
    stage_budgets = {
        "construction": float(
            algorithm_config.get(
                "construction_time_budget",
                usable_time * 0.20,
            )
        ),
        "repair": float(
            algorithm_config.get(
                "repair_time_budget",
                usable_time * 0.10,
            )
        ),
        "vnd": float(
            algorithm_config.get(
                "vnd_time_budget",
                usable_time * 0.20,
            )
        ),
        "pattern_generation": float(
            algorithm_config.get(
                "structured_pattern_time_budget", 1.6
            )
        ),
        "lns": float(
            algorithm_config.get(
                "lns_time_budget",
                usable_time * 0.50,
            )
        ),
        "restricted_mip": float(
            algorithm_config.get(
                "restricted_pattern_mip_time_budget",
                0.0,
            )
        ),
        "protected_multigroup_mip": float(
            algorithm_config.get(
                "protected_multigroup_mip_time_budget", 0.0
            )
        ),
    }
    traveler_total = sum(
        len(group.get("psrs", [])) for group in groups_data
    )
    # 在线求解的负载由实际消耗的座位资源决定。单侧留空额外占用
    # 1 个座位，双侧留空额外占用 2 个座位，因此不能只按真实旅客数
    # 分配阶段预算，否则保护空座较多的 stress/edge 场景会被低估。
    seat_demand_total = _seat_demand_for_budgeting(groups_data)
    post_protected_tail_reserve_active = (
        business_time_limit >= 10.0
        and len(new_seats_data) < len(old_seats_data)
        and seat_demand_total == len(new_seats_data)
        and seat_demand_total > traveler_total
        and traveler_total / max(1, len(new_seats_data)) >= 0.93
    )
    post_protected_special_pricing_active = (
        post_protected_tail_reserve_active
        and bool(algorithm_config.get(
            "enable_post_protected_special_pricing", True
        ))
    )
    if bool(algorithm_config.get("adaptive_stage_budgets", True)):
        low = float(
            algorithm_config.get("adaptive_budget_low_seat_demand", 100)
        )
        high = max(
            low + 1.0,
            float(
                algorithm_config.get(
                    "adaptive_budget_high_seat_demand", 130
                )
            ),
        )
        load = min(
            1.0,
            max(0.0, (seat_demand_total - low) / (high - low)),
        )
        # 高负载时先保证第一份完整可行解，同时为2～4组联合交换保留
        # 硬预算；低负载时把更多时间留给确定性整组模式生成。
        stage_budgets["construction"] = (
            1.3 + 1.7 * load
        )
        stage_budgets["repair"] = 0.25
        stage_budgets["vnd"] = 0.5 + 0.2 * load
        stage_budgets["pattern_generation"] = 2.3 - 1.9 * load
        # The legacy stages keep their validated budgets. Multi-group LNS uses
        # only the real tail left by the restricted pattern MIP (below).
        stage_budgets["lns"] = 0.0
        stage_budgets["restricted_mip"] = 0.4
        priority_mip_active = (
            bool(
                algorithm_config.get(
                    "enable_priority_multigroup_pattern_mip", False
                )
            )
            and business_time_limit
            >= float(
                algorithm_config.get(
                    "priority_multigroup_min_business_time_seconds", 10.0
                )
            )
        )
        if (
            bool(
                algorithm_config.get(
                    "enable_protected_multigroup_pattern_mip", False
                )
            )
            or priority_mip_active
        ):
            stage_budgets["protected_multigroup_mip"] = float(
                algorithm_config.get(
                    "protected_multigroup_mip_time_budget", 8.0
                )
            )
        else:
            stage_budgets["protected_multigroup_mip"] = 0.0
    budget_total = sum(max(0.0, value) for value in stage_budgets.values())
    if budget_total > usable_time and budget_total > 0.0:
        scale = usable_time / budget_total
        stage_budgets = {
            name: max(0.0, value) * scale
            for name, value in stage_budgets.items()
        }
    construction_deadline = min(
        search_deadline,
        allocation_start + stage_budgets["construction"],
    )
    stage_statistics: Dict[str, Dict[str, Any]] = {}
    # 根据真实新旧座位表分别建立拓扑，避免用目标机型同名座位冒充旧座位。
    global _SEAT_NEIGHBORS, _SEAT_ROW_NEIGHBORS, _SEAT_SUBROW, _SEAT_X
    global _SEAT_ROW_INDEX, _GROUP_COMPACT_CACHE
    global _OLD_SEATS, _OLD_SEAT_X, _OLD_SEAT_OWNER_REGRET
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config
    (_SEAT_NEIGHBORS, _SEAT_ROW_NEIGHBORS, _SEAT_SUBROW,
     _SEAT_X, _SEAT_ROW_INDEX) = build_seat_topology(new_seats_data, config)
    _GROUP_COMPACT_CACHE = {}
    _, _, _, _OLD_SEAT_X, _ = build_seat_topology(old_seats_data, config)

    # 构建座位映射和组对象
    seats = {s['seatId']: Seat(s) for s in new_seats_data}
    _OLD_SEATS = {s['seatId']: Seat(s) for s in old_seats_data}
    groups = [Group(g) for g in groups_data]

    context = AssignmentContext(seats)
    seat_values = {s.seat_id: calc_seat_value(s, config) for s in seats.values()}

    # 记录每个旧座位的原始主人，用于防止抢占
    old_seat_owners = {}
    for g in groups:
        for p in g.passengers:
            if p.old_seat_num and p.old_seat_num in _OLD_SEATS:
                old_seat_owners[p.old_seat_num] = (g.group_id, p.hostnum)

    # 阶段1：固定座位是不可变的硬约束。任何预检错误都表示输入
    # 不可行，不能将相关旅客静默跳过后继续运行。
    reserved_diagnostics, invalid_reserved_keys = precheck_reserved_seats(
        groups, seats, config)
    if reserved_diagnostics['has_errors']:
        raise FixedSeatPrecheckError(reserved_diagnostics)
    assign_reserved_seats(groups, context, invalid_reserved_keys)
    _OLD_SEAT_OWNER_REGRET = compute_old_seat_owner_regret(
        groups,
        seats,
        seat_values,
        weights,
        config,
        context,
    )

    # 固定座位及其留空资源已全局占用后，再为其余旅客排序候选座位，
    # 使同行固定旅客成为后续分配的几何和资源锚点。
    passenger_sorted_seats = build_passenger_sorted_seats(
        groups, seats, seat_values, weights, config, context, old_seat_owners)

    # 固定座位是同行组的几何锚点。先补齐这些组中不需要陪护的成员，避免后续
    # 特殊旅客或普通组占掉锚点周围最有价值的座位。
    anchored_groups = [
        group
        for group in groups
        if any(
            passenger.new_seat_num
            and (group.group_id, passenger.hostnum)
            in context.assigned_seats
            for passenger in group.passengers
        )
    ]
    construction_search_stats = {
        "groups_considered": 0,
        "dfs_attempted": 0,
        "dfs_succeeded": 0,
        "dfs_nodes": 0,
        "dfs_seconds": 0.0,
        "beam_groups": 0,
        "transaction_failures": 0,
    }

    def merge_construction_search_stats(
        update: Dict[str, Any],
    ) -> None:
        for name in construction_search_stats:
            construction_search_stats[name] += update.get(name, 0)

    if anchored_groups:
        merge_construction_search_stats(assign_remaining_passengers(
            anchored_groups,
            context,
            passenger_sorted_seats,
            weights,
            config,
            seat_values,
            old_seat_owners,
            construction_deadline,
        ))

    # 阶段2：有新增分配时继续配对；无进展或构造预算耗尽时立即停止。
    paired_ssr_passes = 0
    while (
        paired_ssr_passes < 4
        and time.perf_counter() < construction_deadline
    ):
        added = assign_paired_ssrs(
            groups,
            context,
            passenger_sorted_seats,
            seat_values,
            weights,
            config,
            old_seat_owners,
            construction_deadline,
        )
        paired_ssr_passes += 1
        if added <= 0:
            break

    # 阶段2b：对仍失败的配对SSR做组内联合回溯，而不是直接排除。
    paired_rescue_diagnostics = rescue_failed_paired_ssrs(
        groups, context, passenger_sorted_seats, seat_values,
        weights, config, old_seat_owners, construction_deadline)

    # 阶段3：分配剩余乘客
    merge_construction_search_stats(assign_remaining_passengers(
        groups,
        context,
        passenger_sorted_seats,
        weights,
        config,
        seat_values,
        old_seat_owners,
        construction_deadline,
    ))

    try:
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import allocation_evaluator as evaluator_module

    passenger_total = sum(len(group.passengers) for group in groups)
    stage_scorer = evaluator_module.IncrementalSoftScorer(
        new_seats_data,
        old_seats_data,
        groups_data,
        weights,
        config,
    )
    elite_pattern_limit = max(
        2,
        int(
            algorithm_config.get(
                "elite_patterns_per_group", 12
            )
        ),
    )
    elite_pattern_store: Dict[
        int,
        Dict[
            Any,
            Dict[str, Any],
        ],
    ] = {}
    construction_repair_metrics = _group_repair_metrics(
        groups_data,
        context.assigned_seats,
        stage_scorer.new_topology,
        weights,
        config,
        stage_scorer.old_topology,
    )
    protected_empty_demand = max(0, seat_demand_total - traveler_total)
    free_after_demand = max(0, len(new_seats_data) - seat_demand_total)
    diversity_resource_threshold = max(2, elite_pattern_limit - 2)
    structured_min_group_size = max(
        1,
        int(
            algorithm_config.get(
                "structured_pattern_min_group_size", 5
            )
        ),
    )
    conflict_diversity_active = (
        business_time_limit >= float(
            algorithm_config.get(
                "three_tier_min_business_time_seconds", 10.0
            )
        )
        and any(
            len(group.get("psrs", [])) >= structured_min_group_size
            and _is_ordinary_value_block_group(group)
            and float(
                construction_repair_metrics[int(group["groupId"])].get(
                    "value_mismatch_score", 0.0
                )
            ) < 0.0
            for group in groups_data
        )
        and protected_empty_demand >= diversity_resource_threshold
        and free_after_demand >= elite_pattern_limit
        and len(new_seats_data) < len(old_seats_data)
    )

    def record_elite_pattern(
        group_id: int,
        assignments: Tuple[
            Tuple[Tuple[int, int], str], ...
        ],
        local_score: float,
        source: str,
        blocked_by_host: Optional[
            Mapping[int, Iterable[str]]
        ] = None,
        pinned: bool = False,
    ) -> None:
        signature = tuple(
            sorted(
                (key[1], seat_id)
                for key, seat_id in assignments
            )
        )
        patterns = elite_pattern_store.setdefault(group_id, {})
        blocked_signature = tuple(sorted(
            (
                int(hostnum),
                tuple(sorted(str(seat_id) for seat_id in seat_ids)),
            )
            for hostnum, seat_ids in (blocked_by_host or {}).items()
            if seat_ids
        ))
        blocked_seats = tuple(sorted({
            seat_id
            for _, seat_ids in blocked_signature
            for seat_id in seat_ids
        }))
        occupied_seats = tuple(sorted(
            seat_id for _, seat_id in signature
        ))
        pattern_key = (signature, blocked_signature)
        existing = patterns.get(pattern_key)
        owner_by_resource: Dict[str, int] = {
            seat_id: key[0]
            for key, seat_id in context.assigned_seats.items()
        }
        for key, seat_ids in context.assigned_blocked.items():
            for seat_id in seat_ids:
                owner_by_resource[seat_id] = key[0]
        conflict_groups = tuple(sorted({
            owner_by_resource[seat_id]
            for seat_id in set(occupied_seats).union(blocked_seats)
            if conflict_diversity_active
            and seat_id in owner_by_resource
            and owner_by_resource[seat_id] != group_id
        }))
        # 同一旅客座位分配可以选择不同的单侧保护空座；两者消耗的
        # 全局资源不同，必须作为不同模式保留。
        if existing is None or local_score > existing["local_score"]:
            patterns[pattern_key] = {
                "assignments": signature,
                "occupied_seats": occupied_seats,
                "blocked_seats": blocked_seats,
                "blocked_by_host": blocked_signature,
                "seat_resources": tuple(sorted(
                    set(occupied_seats).union(blocked_seats)
                )),
                "conflict_groups": conflict_groups,
                "local_score": float(local_score),
                "source": source,
                "pinned": bool(pinned),
            }
        elif pinned:
            existing["pinned"] = True
        if len(patterns) > elite_pattern_limit:
            eviction = _elite_pattern_eviction_candidate(patterns)
            if eviction is not None:
                del patterns[eviction]

    def capture_stage_patterns(source: str) -> None:
        for group in groups:
            group_id = group.group_id
            assignments = tuple(
                (
                    (group_id, passenger.hostnum),
                    context.assigned_seats[(group_id, passenger.hostnum)],
                )
                for passenger in group.passengers
                if (group_id, passenger.hostnum)
                in context.assigned_seats
            )
            if len(assignments) != len(group.passengers):
                continue
            local_score = stage_scorer.components(
                context.assigned_seats, {group_id}
            )["total_soft_score"]
            record_elite_pattern(
                group_id,
                assignments,
                local_score,
                source,
                {
                    passenger.hostnum: context.assigned_blocked.get(
                        (group_id, passenger.hostnum), set()
                    )
                    for passenger in group.passengers
                },
                True,
            )

    def current_soft_score() -> float:
        return stage_scorer.components(
            context.assigned_seats
        )["total_soft_score"]

    def inject_diagnostic_reference_patterns() -> int:
        if not config.get("_diagnostic_inject_reference_patterns", False):
            return 0
        reference = config.get("_diagnostic_reference_assignments", {})
        if not reference:
            return 0
        blocked_by_host: Dict[Tuple[int, int], set[str]] = {}
        for seat_id, owners in config.get(
            "_diagnostic_reference_blocked_by", {}
        ).items():
            for owner in owners:
                key = tuple(owner)
                if len(key) == 2:
                    blocked_by_host.setdefault(key, set()).add(seat_id)
        injected = 0
        allowed_group_ids = config.get(
            "_diagnostic_reference_group_ids"
        )
        for group in groups:
            group_id = group.group_id
            if (
                allowed_group_ids is not None
                and group_id not in allowed_group_ids
            ):
                continue
            assignments = tuple(
                (
                    (group_id, passenger.hostnum),
                    reference[(group_id, passenger.hostnum)],
                )
                for passenger in group.passengers
                if (group_id, passenger.hostnum) in reference
            )
            if len(assignments) != len(group.passengers):
                continue
            proposal = dict(context.assigned_seats)
            proposal.update(dict(assignments))
            local_score = stage_scorer.components(
                proposal, {group_id}
            )["total_soft_score"]
            record_elite_pattern(
                group_id,
                assignments,
                local_score,
                "diagnostic_reference",
                {
                    passenger.hostnum: blocked_by_host.get(
                        (group_id, passenger.hostnum), set()
                    )
                    for passenger in group.passengers
                },
                True,
            )
            injected += 1
        return injected

    capture_stage_patterns("construction")
    construction_repair_queue = [
        construction_repair_metrics[group_id]
        for group_id in sorted(
            construction_repair_metrics,
            key=lambda gid: _repair_priority_key(
                construction_repair_metrics[gid]
            ),
        )
    ]
    construction_finished = time.perf_counter()
    construction_elapsed = construction_finished - allocation_start
    carry = max(0.0, construction_deadline - construction_finished)
    stage_statistics["construction"] = {
        "base_budget": stage_budgets["construction"],
        "effective_deadline": construction_deadline - allocation_start,
        "seconds": construction_elapsed,
        "assigned": len(context.assigned_seats),
        "unassigned": passenger_total - len(context.assigned_seats),
        "paired_ssr_passes": paired_ssr_passes,
        "paired_rescue_attempts": len(
            paired_rescue_diagnostics.get("attempted", [])
        ),
        "group_search": construction_search_stats,
        "score_after": current_soft_score(),
        "repair_queue": construction_repair_queue[:20],
        "extreme_dispersion_group_count": sum(
            bool(metric["extreme_dispersion"])
            for metric in construction_repair_queue
        ),
        "unused_budget_carried": carry,
    }

    repair_started = time.perf_counter()
    repair_effective_budget = stage_budgets["repair"] + carry
    construction_unassigned = (
        passenger_total - len(context.assigned_seats)
    )
    # Completeness dominates every soft-score stage.  When construction leaves
    # anyone unassigned, lend the entire remaining search budget to repair;
    # only a complete solution may continue to VND/LNS/MIP improvement.
    repair_deadline = (
        search_deadline
        if construction_unassigned > 0
        else min(
            search_deadline, repair_started + repair_effective_budget
        )
    )
    repair_effective_budget = max(
        0.0, repair_deadline - repair_started
    )
    repair_score_before = current_soft_score()
    final_repair_diagnostics = repair_unassigned_by_local_relocation(
        groups, context, passenger_sorted_seats, config, repair_deadline
    )
    capture_stage_patterns("repair")
    repair_finished = time.perf_counter()
    repair_score_after = current_soft_score()
    carry = max(0.0, repair_deadline - repair_finished)
    repair_calls = int(final_repair_diagnostics.get("attempted", 0))
    repair_accepts = int(final_repair_diagnostics.get("repaired", 0))
    stage_statistics["repair"] = {
        "base_budget": stage_budgets["repair"],
        "effective_budget": repair_effective_budget,
        "seconds": repair_finished - repair_started,
        "calls": repair_calls,
        "accepted": repair_accepts,
        "acceptance_rate": (
            repair_accepts / repair_calls if repair_calls else 0.0
        ),
        "score_improvement": repair_score_after - repair_score_before,
        "score_after": repair_score_after,
        "unused_budget_carried": carry,
    }

    vnd_started = time.perf_counter()
    vnd_effective_budget = stage_budgets["vnd"] + carry
    post_protected_pricing_reserve = min(
        max(0.0, vnd_effective_budget - 0.1),
        2.0 if post_protected_tail_reserve_active else 0.0,
    )
    vnd_effective_budget -= post_protected_pricing_reserve
    vnd_deadline = min(
        search_deadline, vnd_started + vnd_effective_budget
    )
    vnd_score_before = current_soft_score()
    local_search_diagnostics = improve_assignment_with_safe_neighborhoods(
        new_seats_data,
        old_seats_data,
        groups_data,
        groups,
        context,
        passenger_sorted_seats,
        weights,
        config,
        vnd_deadline,
    )
    capture_stage_patterns("vnd")
    vnd_finished = time.perf_counter()
    vnd_score_after = current_soft_score()
    carry = max(0.0, vnd_deadline - vnd_finished)
    vnd_calls = int(local_search_diagnostics.get("evaluated_moves", 0))
    vnd_accepts = int(local_search_diagnostics.get("accepted_moves", 0))
    stage_statistics["vnd"] = {
        "base_budget": stage_budgets["vnd"],
        "effective_budget": vnd_effective_budget,
        "seconds": vnd_finished - vnd_started,
        "calls": vnd_calls,
        "accepted": vnd_accepts,
        "acceptance_rate": (
            vnd_accepts / vnd_calls if vnd_calls else 0.0
        ),
        "score_improvement": vnd_score_after - vnd_score_before,
        "score_after": vnd_score_after,
        "unused_budget_carried": carry,
        "reserved_for_post_protected_pricing": (
            post_protected_pricing_reserve
        ),
    }

    pattern_started = time.perf_counter()
    pattern_effective_budget = (
        stage_budgets["pattern_generation"] + carry
    )
    pattern_deadline = min(
        search_deadline,
        pattern_started + pattern_effective_budget,
    )
    structured_pattern_diagnostics = generate_structured_group_patterns(
        new_seats_data,
        old_seats_data,
        groups_data,
        context,
        stage_scorer,
        weights,
        config,
        pattern_deadline,
        record_elite_pattern,
    )
    pattern_finished = time.perf_counter()
    carry = max(0.0, pattern_deadline - pattern_finished)
    stage_statistics["pattern_generation"] = {
        "base_budget": stage_budgets["pattern_generation"],
        "effective_budget": pattern_effective_budget,
        "seconds": pattern_finished - pattern_started,
        "calls": int(
            structured_pattern_diagnostics.get("groups_attempted", 0)
        ),
        "accepted": int(
            structured_pattern_diagnostics.get("patterns_generated", 0)
        ),
        "score_improvement": 0.0,
        "score_after": current_soft_score(),
        "unused_budget_carried": carry,
    }

    protected_mip_started = time.perf_counter()
    protected_mip_effective_budget = (
        stage_budgets["protected_multigroup_mip"]
        + carry
    )
    protected_mip_deadline = min(
        search_deadline,
        protected_mip_started + protected_mip_effective_budget,
    )
    protected_mip_score_before = current_soft_score()
    protected_max_passes = max(1, int(algorithm_config.get(
        "protected_multigroup_max_passes", 3
    )))
    protected_min_pass_gain = max(0.0, float(algorithm_config.get(
        "protected_multigroup_min_pass_gain", 1.0
    )))

    protected_passes: List[Dict[str, Any]] = []
    for _ in range(protected_max_passes):
        pass_diagnostics = improve_protected_multigroup_pattern_mip(
            new_seats_data, old_seats_data, groups_data, groups,
            context, elite_pattern_store, stage_scorer, weights,
            config, protected_mip_deadline,
        )
        protected_passes.append(pass_diagnostics)
        capture_stage_patterns("protected_multigroup_mip")
        if (
            not pass_diagnostics.get("enabled", False)
            or int(pass_diagnostics.get("accepted", 0)) == 0
            or float(pass_diagnostics.get("score_improvement", 0.0))
            < protected_min_pass_gain
            or time.perf_counter() >= protected_mip_deadline
        ):
            break
    protected_multigroup_mip_diagnostics = dict(protected_passes[0])
    for pass_diagnostics in protected_passes[1:]:
        for key in (
            "roots_considered", "components_tested", "mip_columns",
            "dynamic_relocation_calls", "dynamic_relocation_patterns",
            "accepted", "score_improvement", "seconds",
        ):
            protected_multigroup_mip_diagnostics[key] = (
                protected_multigroup_mip_diagnostics.get(key, 0)
                + pass_diagnostics.get(key, 0)
            )
        protected_multigroup_mip_diagnostics["tested_components"].extend(
            pass_diagnostics.get("tested_components", [])
        )
        protected_multigroup_mip_diagnostics["accepted_components"].extend(
            pass_diagnostics.get("accepted_components", [])
        )
        protected_multigroup_mip_diagnostics["stopped_by_deadline"] = bool(
            protected_multigroup_mip_diagnostics.get(
                "stopped_by_deadline", False
            ) or pass_diagnostics.get("stopped_by_deadline", False)
        )
    protected_multigroup_mip_diagnostics["passes"] = len(protected_passes)
    protected_mip_finished = time.perf_counter()
    protected_mip_score_after = current_soft_score()
    carry = max(0.0, protected_mip_deadline - protected_mip_finished)
    stage_statistics["protected_multigroup_mip"] = {
        "base_budget": stage_budgets["protected_multigroup_mip"],
        "effective_budget": protected_mip_effective_budget,
        "seconds": protected_mip_finished - protected_mip_started,
        "calls": int(
            protected_multigroup_mip_diagnostics.get(
                "components_tested", 0
            )
        ),
        "accepted": int(
            protected_multigroup_mip_diagnostics.get("accepted", 0)
        ),
        "score_improvement": (
            protected_mip_score_after - protected_mip_score_before
        ),
        "score_after": protected_mip_score_after,
        "unused_budget_carried": carry,
    }

    special_dual_pricing_diagnostics = generate_special_dual_pricing_patterns(
        new_seats_data, old_seats_data, groups_data, context,
        stage_scorer, weights, config, elite_pattern_store,
        min(
            search_deadline,
            time.perf_counter() + post_protected_pricing_reserve,
        ),
        record_elite_pattern,
        post_protected_special_pricing_active,
    )

    lns_started = time.perf_counter()
    lns_effective_budget = stage_budgets["lns"] + carry
    lns_deadline = min(
        search_deadline, lns_started + lns_effective_budget
    )
    lns_score_before = current_soft_score()
    multigroup_lns_diagnostics = improve_with_multigroup_lns(
        new_seats_data,
        old_seats_data,
        groups_data,
        groups,
        context,
        passenger_sorted_seats,
        weights,
        config,
        lns_deadline,
        record_elite_pattern,
    )
    capture_stage_patterns("lns_final")
    lns_finished = time.perf_counter()
    lns_score_after = current_soft_score()
    lns_calls = int(multigroup_lns_diagnostics.get("search_nodes", 0))
    lns_accepts = int(
        multigroup_lns_diagnostics.get("accepted_rebuilds", 0)
    )
    stage_statistics["lns"] = {
        "base_budget": stage_budgets["lns"],
        "effective_budget": lns_effective_budget,
        "seconds": lns_finished - lns_started,
        "calls": lns_calls,
        "accepted": lns_accepts,
        "acceptance_rate": (
            lns_accepts / lns_calls if lns_calls else 0.0
        ),
        "accepted_worsening": int(
            multigroup_lns_diagnostics.get("accepted_worsening", 0)
        ),
        "score_improvement": lns_score_after - lns_score_before,
        "score_after": lns_score_after,
        "unused_budget": max(0.0, lns_deadline - lns_finished),
    }

    restricted_started = time.perf_counter()
    diagnostic_reference_patterns = inject_diagnostic_reference_patterns()
    restricted_tail_budget = max(
        0.0,
        float(
            algorithm_config.get(
                "restricted_pattern_mip_tail_budget", 0.10
            )
        ),
    )
    restricted_effective_budget = max(
        stage_budgets["restricted_mip"],
        restricted_tail_budget,
    ) + max(0.0, lns_deadline - restricted_started)
    restricted_deadline = min(
        search_deadline,
        restricted_started + restricted_effective_budget,
    )
    restricted_score_before = current_soft_score()
    restricted_mip_diagnostics = improve_with_restricted_pattern_mip(
        new_seats_data,
        groups_data,
        groups,
        context,
        elite_pattern_store,
        stage_scorer,
        config,
        restricted_deadline,
    )
    capture_stage_patterns("restricted_mip")
    restricted_finished = time.perf_counter()
    restricted_score_after = current_soft_score()
    stage_statistics["restricted_mip"] = {
        "base_budget": stage_budgets["restricted_mip"],
        "effective_budget": restricted_effective_budget,
        "seconds": restricted_finished - restricted_started,
        "calls": int(restricted_mip_diagnostics.get("attempts", 0)),
        "accepted": int(restricted_mip_diagnostics.get("accepted", 0)),
        "acceptance_rate": (
            float(restricted_mip_diagnostics.get("accepted", 0))
            / int(restricted_mip_diagnostics.get("attempts", 0))
            if restricted_mip_diagnostics.get("attempts", 0)
            else 0.0
        ),
        "score_improvement": (
            restricted_score_after - restricted_score_before
        ),
        "score_after": restricted_score_after,
        "unused_budget": max(
            0.0, restricted_deadline - restricted_finished
        ),
        "diagnostic_reference_patterns": diagnostic_reference_patterns,
    }

    # Full-load 2～4-group exchange: consume only time that the restricted MIP
    # actually left unused. It therefore cannot crowd out construction, VND or
    # deterministic group-pattern generation. The LNS itself accepts only a
    # strict soft-score improvement when multigroup_allowed_drop is zero.
    allocation_elapsed = time.perf_counter() - allocation_start
    # 使用与独立评分器完全相同的函数生成可解释分项。
    violations, unassigned_count, violation_detail = (
        evaluator_module.count_hard_constraint_violations(
            new_seats_data, groups_data, context.assigned_seats, config)
    )
    soft_score, soft_detail = evaluator_module.calculate_soft_score(
        new_seats_data, old_seats_data, groups_data,
        context.assigned_seats, weights, config)
    incremental_soft_detail = stage_scorer.components(
        context.assigned_seats
    )
    incremental_soft_score = float(
        incremental_soft_detail["total_soft_score"]
    )
    soft_score_error = abs(float(soft_score) - incremental_soft_score)
    soft_score_tolerance = max(
        1e-7,
        float(
            algorithm_config.get(
                "soft_score_validation_tolerance", 1e-7
            )
        ),
    )
    if soft_score_error > soft_score_tolerance:
        raise RuntimeError(
            "soft score validation mismatch: "
            f"independent={soft_score}, incremental={incremental_soft_score}, "
            f"error={soft_score_error}"
        )
    soft_score_validation = {
        "passed": True,
        "independent_score": float(soft_score),
        "incremental_score": incremental_soft_score,
        "absolute_error": soft_score_error,
        "tolerance": soft_score_tolerance,
    }
    if violations or unassigned_count:
        violation_detail = {
            **violation_detail,
            "final_repair_diagnostics": final_repair_diagnostics,
            "stage_statistics": stage_statistics,
        }
        raise IncompleteAllocationError(
            violations, unassigned_count, violation_detail
        )
    combined_score, time_penalty = evaluator_module.calculate_combined_score(
        soft_score, unassigned_count, violations, allocation_elapsed, config)
    score_detail = {
        **soft_detail,
        'unassigned_count': unassigned_count,
        'violations': violations,
        'violation_detail': violation_detail,
        'allocation_elapsed': allocation_elapsed,
        'time_penalty': time_penalty,
        'combined_score': combined_score,
        'soft_score_validation': soft_score_validation,
    }
    elite_source_counts: Dict[str, int] = {}
    elite_counts_by_group = {
        str(group_id): len(patterns)
        for group_id, patterns in elite_pattern_store.items()
    }
    for patterns in elite_pattern_store.values():
        for pattern in patterns.values():
            source = str(pattern["source"])
            elite_source_counts[source] = (
                elite_source_counts.get(source, 0) + 1
            )
    elite_pattern_summary = {
        "groups": len(elite_pattern_store),
        "patterns": sum(elite_counts_by_group.values()),
        "per_group_limit": elite_pattern_limit,
        "conflict_diversity_active": conflict_diversity_active,
        "protected_empty_demand": protected_empty_demand,
        "free_after_demand": free_after_demand,
        "counts_by_group": elite_counts_by_group,
        "source_counts": elite_source_counts,
    }
    reference_coverage = None
    reference_assignments = config.get(
        "_diagnostic_reference_assignments"
    )
    if reference_assignments and all(
        (group.group_id, passenger.hostnum) in reference_assignments
        for group in groups for passenger in group.passengers
    ):
        reference_coverage = _elite_reference_coverage(
            groups,
            elite_pattern_store,
            reference_assignments,
            config.get("_diagnostic_reference_blocked_by", {}),
        )

    # 构建返回结果
    result = {
        'assigned_seats': dict(context.assigned_seats),  # {(组ID, 乘客编号): 座位ID}
        'occupied': list(context.occupied),
        'blocked_by': build_blocked_by(context),  # {留空座位ID: [(组ID, 乘客编号)]}
        'diagnostics': {
            'reserved_precheck': reserved_diagnostics,
            'paired_rescue': paired_rescue_diagnostics,
            'final_repair': final_repair_diagnostics,
            'local_search': local_search_diagnostics,
            'structured_pattern_generation': (
                structured_pattern_diagnostics
            ),
            'special_dual_pricing': special_dual_pricing_diagnostics,
            'protected_multigroup_pattern_mip': (
                protected_multigroup_mip_diagnostics
            ),
            'multigroup_lns': multigroup_lns_diagnostics,
            'restricted_pattern_mip': restricted_mip_diagnostics,
            'stage_budgets': stage_budgets,
            'stage_statistics': stage_statistics,
            'elite_pattern_summary': elite_pattern_summary,
            'elite_reference_coverage': reference_coverage,
            'business_time_limit_seconds': business_time_limit,
            'traveler_count': traveler_total,
            'seat_demand_for_budgeting': seat_demand_total,
            'search_time_limit_seconds': usable_time,
            'total_elapsed_seconds': allocation_elapsed,
        },
        'score_detail': score_detail,
        'total_score': combined_score,
    }
    return result, combined_score


def _sum_numeric_diagnostics(values: Sequence[Any]) -> Any:
    """Merge legacy diagnostics without hiding the per-cabin originals."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    if all(isinstance(value, dict) for value in present):
        keys = set().union(*(value.keys() for value in present))
        return {
            key: _sum_numeric_diagnostics(
                [value.get(key) for value in present if key in value]
            )
            for key in keys
        }
    if all(isinstance(value, bool) for value in present):
        return all(present)
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in present
    ):
        return sum(present)
    return present[-1]


def run_allocation(new_seats_data: list, old_seats_data: list,
                   groups_data: list, weights: dict,
                   config: dict) -> Tuple[dict, float]:
    """按预订舱位独立限时求解，再用统一评估器合并完整方案。"""
    try:
        from . import allocation_evaluator as evaluator_module
    except ImportError:
        import allocation_evaluator as evaluator_module

    mixed = evaluator_module.mixed_cabin_group_ids(groups_data)
    if mixed:
        raise ValueError(f"同行组必须舱位同质，混舱组: {mixed}")
    groups_by_cabin: Dict[str, List[dict]] = defaultdict(list)
    for group in groups_data:
        cabin = evaluator_module.group_cabin_class(group)
        if cabin is None:
            raise ValueError(
                f"同行组缺少唯一有效舱位: groupId={group.get('groupId')}"
            )
        groups_by_cabin[cabin].append(group)
    if not groups_by_cabin:
        raise ValueError("没有可分配的同行组")

    algorithm = config.get("algorithm", {})
    if not bool(algorithm.get("cabin_decomposition_enabled", True)):
        return _run_allocation_single_cabin(
            new_seats_data, old_seats_data, groups_data, weights, config
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
        return _run_allocation_single_cabin(
            new_seats_data, old_seats_data, groups_data, weights, config
        )

    started = time.perf_counter()
    total_limit = max(
        0.1, float(algorithm.get("business_time_limit_seconds", 5.0))
    )
    deadline = started + total_limit
    minimum = min(
        total_limit / len(groups_by_cabin),
        max(0.1, float(algorithm.get("cabin_min_time_seconds", 1.0))),
    )
    cabins = sorted(
        groups_by_cabin,
        key=lambda cabin: (len(target_by_cabin[cabin]), cabin),
    )
    target_total = sum(len(target_by_cabin[cabin]) for cabin in cabins)
    merged_assignments: Dict[Tuple[int, int], str] = {}
    merged_blocked: Dict[str, Any] = {}
    cabin_runs: Dict[str, Any] = {}

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
        cabin_config.setdefault("algorithm", {})[
            "business_time_limit_seconds"
        ] = cabin_limit
        cabin_started = time.perf_counter()
        cabin_result, _ = _run_allocation_single_cabin(
            target_by_cabin[cabin],
            old_by_cabin[cabin],
            groups_by_cabin[cabin],
            weights,
            cabin_config,
        )
        cabin_elapsed = time.perf_counter() - cabin_started
        merged_assignments.update(cabin_result["assigned_seats"])
        merged_blocked.update(cabin_result.get("blocked_by", {}))
        cabin_runs[cabin] = {
            "time_budget_seconds": cabin_limit,
            "elapsed_seconds": cabin_elapsed,
            "traveler_count": sum(
                len(group.get("psrs", []))
                for group in groups_by_cabin[cabin]
            ),
            "target_seats": len(target_by_cabin[cabin]),
            "total_score": cabin_result["score_detail"][
                "total_soft_score"
            ],
            "diagnostics": cabin_result.get("diagnostics", {}),
        }

    elapsed = time.perf_counter() - started
    violations, unassigned, violation_detail = (
        evaluator_module.count_hard_constraint_violations(
            new_seats_data, groups_data, merged_assignments, config
        )
    )
    soft_score, soft_detail = evaluator_module.calculate_soft_score(
        new_seats_data,
        old_seats_data,
        groups_data,
        merged_assignments,
        weights,
        config,
    )
    if violations or unassigned:
        raise IncompleteAllocationError(
            violations, unassigned, violation_detail
        )
    combined_score, time_penalty = (
        evaluator_module.calculate_combined_score(
            soft_score, unassigned, violations, elapsed, config
        )
    )
    incremental_soft_score = sum(
        float(run["total_score"]) for run in cabin_runs.values()
    )
    validation_tolerance = float(
        algorithm.get("soft_score_validation_tolerance", 1e-7)
    )
    validation_error = abs(float(soft_score) - incremental_soft_score)
    score_detail = {
        **soft_detail,
        "unassigned_count": unassigned,
        "violations": violations,
        "violation_detail": violation_detail,
        "allocation_elapsed": elapsed,
        "time_penalty": time_penalty,
        "combined_score": combined_score,
        "soft_score_validation": {
            "passed": validation_error <= validation_tolerance,
            "independent_score": float(soft_score),
            "incremental_score": incremental_soft_score,
            "absolute_error": validation_error,
            "tolerance": validation_tolerance,
        },
    }
    stage_statistics = _sum_numeric_diagnostics([
        run.get("diagnostics", {}).get("stage_statistics", {})
        for run in cabin_runs.values()
    ])
    return {
        "assigned_seats": merged_assignments,
        "occupied": sorted(set(merged_assignments.values())),
        "blocked_by": merged_blocked,
        "diagnostics": {
            "method": "independent_cabin_decomposition",
            "cabin_decomposition": {
                "enabled": True,
                "solve_order": cabins,
                "total_time_limit_seconds": total_limit,
                "economy_receives_remaining_time": True,
                "cabins": cabin_runs,
            },
            "business_time_limit_seconds": total_limit,
            "total_elapsed_seconds": elapsed,
            # Kept for callers written before cabin decomposition. Detailed
            # unaggregated records remain under cabin_decomposition.cabins.
            "stage_statistics": stage_statistics,
        },
        "score_detail": score_detail,
        "total_score": combined_score,
    }, combined_score
# BLOCK A END
