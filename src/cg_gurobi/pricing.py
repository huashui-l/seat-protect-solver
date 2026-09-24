"""MP2：单个同行组的直接定价（Gurobi MIP）。

每个同行组建立一次完整的旅客—座位—保护资源 MIP，之后每轮只改目标系数。
定价问题在该组的完整合法模式域 K_g 上精确求最小约化成本；Gurobi 报告
Optimal 时得到精确证书，达到时限时用 ObjBound 作为严格下界。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import gurobipy as gp
from gurobipy import GRB

from .instance import Group, Instance, Location, Passenger, PassengerKey

TOL = 1e-6


@dataclass(frozen=True)
class Column:
    """MP1 的一列：同行组的完整整数方案及全部主问题系数。"""
    gid: int
    assignment: Tuple[Tuple[PassengerKey, str], ...]      # 旅客 -> 座位
    protected: Tuple[Tuple[PassengerKey, str], ...]       # 旅客 -> 独占保护空座
    resources: Tuple[str, ...]                             # 消耗的全部物理座位
    flag_locations: Tuple[Location, ...]                   # 激活条件隔离的位置
    ssr_counts: Tuple[Tuple[Location, str, int], ...]      # (位置, 类型, 人数)
    infant_seats: Tuple[str, ...]
    occupied: Tuple[str, ...]
    local_score: float                                     # 个人项 + 紧凑度
    same_group_baby: float                                 # 组内婴儿对的 B 之和
    cost: float                                            # MP1 列成本 = -local_score + same_group_baby（B 为负分，见 master 推导）

    @property
    def signature(self) -> Tuple[Tuple[PassengerKey, str], ...]:
        return self.assignment + tuple(("__blocked__", s) for _, s in self.protected)  # type: ignore[return-value]

    def as_dict(self) -> Dict[PassengerKey, str]:
        return dict(self.assignment)


@dataclass
class Duals:
    convexity: Dict[int, float] = field(default_factory=dict)
    seat: Dict[str, float] = field(default_factory=dict)
    ssr_activation: Dict[Tuple[int, Location, str], float] = field(default_factory=dict)
    ssr_count: Dict[Tuple[Location, str], float] = field(default_factory=dict)
    baby: Dict[Tuple[str, str], float] = field(default_factory=dict)


@dataclass
class PricingOutcome:
    gid: int
    status: str                       # optimal | time_limit | infeasible | error
    lower_bound: float                # 完整模式域最小约化成本的严格下界
    columns: List[Column]             # 约化成本 < -TOL 的候选列（按约化成本升序）
    best_reduced_cost: float
    seconds: float
    nodes: float = 0.0

    @property
    def certified(self) -> bool:
        return self.status in ("optimal", "infeasible")


def reduced_cost(column: Column, duals: Duals, phase_one: bool) -> float:
    """按 MP1 全部行对偶重算一列的约化成本（与主问题系数一一对应）。"""
    value = (0.0 if phase_one else column.cost) - duals.convexity.get(column.gid, 0.0)
    for seat in column.resources:
        value -= duals.seat.get(seat, 0.0)
    for loc in column.flag_locations:
        for (g, l, tau), pi in duals.ssr_activation.items():
            if g == column.gid and l == loc:
                value -= pi
    for loc, tau, count in column.ssr_counts:
        value -= duals.ssr_count.get((loc, tau), 0.0) * count
    if duals.baby:
        occupied = set(column.occupied)
        infants = set(column.infant_seats)
        for (u, v), pi in duals.baby.items():
            coef = int(u in infants) + int(v in occupied)
            if coef:
                value -= pi * coef
    return value


class GroupPricer:
    """一个同行组的持久化定价 MIP。"""

    def __init__(self, inst: Instance, group: Group, env: gp.Env):
        self.inst = inst
        self.group = group
        self.model = gp.Model(f"pricing_g{group.gid}", env=env)
        self.model.Params.OutputFlag = 0
        self.model.Params.Threads = int(inst.config.get("cg_gurobi", {}).get("pricing_threads", 1))
        self.model.Params.MIPGap = 0.0
        self.model.Params.MIPGapAbs = 0.0
        self.model.Params.IntFeasTol = 1e-7
        self.model.Params.FeasibilityTol = 1e-8
        self.model.Params.PoolSolutions = int(inst.config.get("cg_gurobi", {}).get("pricing_pool", 8))
        self.model.Params.PoolSearchMode = 0
        self._last_start: List[Tuple[gp.Var, float]] = []
        self._build()

    # ------------------------------------------------------------------ 建模
    def _build(self) -> None:
        inst, g, m = self.inst, self.group, self.model
        n = g.size
        self.x: Dict[Tuple[PassengerKey, str], gp.Var] = {}
        for p in g.passengers:
            if not p.domain:
                self.infeasible_reason = f"旅客 {p.key} 可行座位域为空"
            for s in p.domain:
                self.x[(p.key, s)] = m.addVar(vtype=GRB.BINARY, name=f"x_{p.hostnum}_{s}")
        # 4.1 全员分配
        for p in g.passengers:
            m.addConstr(gp.quicksum(self.x[(p.key, s)] for s in p.domain) == 1, name=f"assign_{p.hostnum}")

        # 4.8 单侧保护：z_{pst}
        self.z: Dict[Tuple[PassengerKey, str, str], gp.Var] = {}
        for p in g.passengers:
            if not p.single_side:
                continue
            for s in p.domain:
                cands = inst.single_side_neighbors(s)
                zs = []
                for t in cands:
                    v = m.addVar(vtype=GRB.BINARY, name=f"z_{p.hostnum}_{s}_{t}")
                    self.z[(p.key, s, t)] = v
                    zs.append(v)
                m.addConstr(gp.quicksum(zs) == self.x[(p.key, s)], name=f"single_{p.hostnum}_{s}")

        # 物理资源使用量 R_t（真实占座 + 单侧保护 + 双侧保护）
        self.resource: Dict[str, gp.LinExpr] = {}

        def add_res(t: str, term):
            self.resource.setdefault(t, gp.LinExpr())
            self.resource[t] += term

        for (pk, s), var in self.x.items():
            add_res(s, var)
        for (pk, s, t), var in self.z.items():
            add_res(t, var)
        for p in g.passengers:
            if p.both_side:
                for s in p.domain:
                    for t in inst.both_side_neighbors(s):
                        add_res(t, self.x[(p.key, s)])
        for t, expr in self.resource.items():
            m.addConstr(expr <= 1, name=f"res_{t}")

        # 4.6 陪护：x_ps <= sum_{q in E_g, q != p} sum_{t in N_p(s)} x_qt
        caregivers = [q for q in g.caregivers]
        for p in g.passengers:
            if not p.needs_caregiver:
                continue
            for s in p.domain:
                terms = [
                    self.x[(q.key, t)]
                    for q in caregivers if q.key != p.key
                    for t in inst.caregiver_neighbors(p, s)
                    if (q.key, t) in self.x
                ]
                if terms:
                    m.addConstr(self.x[(p.key, s)] <= gp.quicksum(terms), name=f"care_{p.hostnum}_{s}")
                else:
                    m.addConstr(self.x[(p.key, s)] == 0, name=f"care_{p.hostnum}_{s}_none")

        # 5.4 紧凑度：重心最大偏差的精确线性化（w_c <= 0 时精确）
        self.dev_x = self.dev_y = None
        if n >= 2:
            self.dev_x = m.addVar(lb=0.0, name="dev_x")
            self.dev_y = m.addVar(lb=0.0, name="dev_y")
            cx = gp.quicksum(inst.x(s) * v for (pk, s), v in self.x.items()) * (1.0 / n)
            cy = gp.quicksum(inst.y(s) * v for (pk, s), v in self.x.items()) * (1.0 / n)
            for p in g.passengers:
                px = gp.quicksum(inst.x(s) * self.x[(p.key, s)] for s in p.domain)
                py = gp.quicksum(inst.y(s) * self.x[(p.key, s)] for s in p.domain)
                m.addConstr(self.dev_x >= px - cx)
                m.addConstr(self.dev_x >= cx - px)
                m.addConstr(self.dev_y >= py - cy)
                m.addConstr(self.dev_y >= cy - py)

        # 4.9 条件 SSR：激活指示 e_l 与类型人数 m_{l,tau}
        self.activation: Dict[Location, gp.Var] = {}
        for p in g.passengers:
            for s in p.domain:
                for kind, flagged in (("row", p.row_flag), ("subrow", p.subrow_flag)):
                    if not flagged:
                        continue
                    loc = inst.location_of(s, kind)
                    if loc not in self.activation:
                        self.activation[loc] = m.addVar(vtype=GRB.BINARY, name=f"act_{kind}_{loc[2]}")
                    m.addConstr(self.activation[loc] >= self.x[(p.key, s)])
        self.ssr_count_expr: Dict[Tuple[Location, str], gp.LinExpr] = {}
        for p in g.passengers:
            if not p.ssr:
                continue
            for s in p.domain:
                for kind in ("row", "subrow"):
                    loc = inst.location_of(s, kind)
                    if loc in inst.flag_locations:
                        key = (loc, p.ssr)
                        self.ssr_count_expr.setdefault(key, gp.LinExpr())
                        self.ssr_count_expr[key] += self.x[(p.key, s)]

        # 5.5 婴儿：I_u、O_v 及组内同组对的乘积 w_uv
        self.infant_expr: Dict[str, gp.LinExpr] = {}
        self.occupant_expr: Dict[str, gp.LinExpr] = {}
        for (pk, s), v in self.x.items():
            self.occupant_expr.setdefault(s, gp.LinExpr())
            self.occupant_expr[s] += v
            if inst.passengers[pk].is_infant:
                self.infant_expr.setdefault(s, gp.LinExpr())
                self.infant_expr[s] += v
        self.same_pair: Dict[Tuple[str, str], gp.Var] = {}
        for u in self.infant_expr:
            for (uu, v) in inst.baby_pairs_by_infant_seat.get(u, []) if inst.baby_pairs else []:
                if v not in self.occupant_expr:
                    continue
                w = m.addVar(lb=0.0, ub=1.0, name=f"same_{u}_{v}")
                m.addConstr(w >= self.infant_expr[u] + self.occupant_expr[v] - 1)
                m.addConstr(w <= self.infant_expr[u])
                m.addConstr(w <= self.occupant_expr[v])
                self.same_pair[(u, v)] = w

        # 列成本（不含对偶）：c = -Σ h_ps x_ps + |w_c| (βx dev_x + βy dev_y) - Σ_same c_uv w_uv
        self.cost_expr = gp.LinExpr()
        for (pk, s), v in self.x.items():
            self.cost_expr += (-inst.passengers[pk].score[s]) * v
        if self.dev_x is not None:
            self.cost_expr += (-inst.w_c) * (inst.compact_x_factor * self.dev_x + inst.compact_y_factor * self.dev_y)
        for (u, v), w in self.same_pair.items():
            # 评估器不计同组婴儿影响；MP1 的 b_uv 会把它算进去，列成本预先抵消。
            self.cost_expr += inst.baby_pairs[(u, v)] * w   # baby_pairs 为负分，故这里是负成本
        m.update()

    # ------------------------------------------------------------------ 定价
    def price_with_rules(self, duals: Duals, rules: Sequence[Tuple], *, phase_one: bool, time_limit: float) -> PricingOutcome:
        """带分支规则的定价。规则：('seat', seat, must) 组是否占用座位；('pax', hostnum, seat, must) 旅客是否坐该座位。"""
        m = self.model
        temp = []
        for rule in rules:
            if rule[0] == 'seat':
                _, seat, must = rule
                expr = self.occupant_expr.get(seat)
                if expr is None:
                    if must:
                        return PricingOutcome(self.group.gid, "infeasible", math.inf, [], math.inf, 0.0)
                    continue
                temp.append(m.addConstr(expr == (1.0 if must else 0.0)))
            else:
                _, hostnum, seat, must = rule
                var = self.x.get(((self.group.gid, hostnum), seat))
                if var is None:
                    if must:
                        return PricingOutcome(self.group.gid, "infeasible", math.inf, [], math.inf, 0.0)
                    continue
                temp.append(m.addConstr(var == (1.0 if must else 0.0)))
        try:
            return self.price(duals, phase_one=phase_one, time_limit=time_limit)
        finally:
            for c in temp:
                m.remove(c)
            m.update()

    def _remember_start(self) -> None:
        self._last_start = [(v, v.X) for v in self.model.getVars()]

    def price(self, duals: Duals, *, phase_one: bool, time_limit: float, use_cutoff: bool = True,
              early_stop: Optional[float] = None) -> PricingOutcome:
        """``early_stop``：找到约化成本 <= early_stop 的列即停（Gurobi BestObjStop）。
        早停返回的下界仍取 ObjBound，不冒充证书。"""
        inst, g, m = self.inst, self.group, self.model
        started = time.perf_counter()
        obj = gp.LinExpr()
        if not phase_one:
            obj += self.cost_expr
        obj -= duals.convexity.get(g.gid, 0.0)
        for t, expr in self.resource.items():
            pi = duals.seat.get(t, 0.0)
            if pi:
                obj -= pi * expr
        for loc, var in self.activation.items():
            pi = sum(v for (gg, l, tau), v in duals.ssr_activation.items() if gg == g.gid and l == loc)
            if pi:
                obj -= pi * var
        for (loc, tau), expr in self.ssr_count_expr.items():
            pi = duals.ssr_count.get((loc, tau), 0.0)
            if pi:
                obj -= pi * expr
        for (u, v), pi in duals.baby.items():
            if not pi:
                continue
            if u in self.infant_expr:
                obj -= pi * self.infant_expr[u]
            if v in self.occupant_expr:
                obj -= pi * self.occupant_expr[v]
        m.setObjective(obj, GRB.MINIMIZE)
        m.Params.TimeLimit = max(0.01, float(time_limit))
        # 只需要约化成本 < -TOL 的列：以 -TOL 为 Cutoff，Gurobi 若报 CUTOFF
        # 即证明该组不存在负列（下界 -TOL），无需求出真实最小值。
        m.Params.Cutoff = -TOL if use_cutoff else GRB.INFINITY
        m.Params.BestObjStop = float(early_stop) if early_stop is not None else -GRB.INFINITY
        if self._last_start:
            for var, value in self._last_start:
                var.Start = value
        m.optimize()
        status = m.Status
        seconds = time.perf_counter() - started
        if status == GRB.INFEASIBLE:
            return PricingOutcome(g.gid, "infeasible", math.inf, [], math.inf, seconds)
        if status == GRB.CUTOFF:
            return PricingOutcome(g.gid, "optimal", -TOL, [], math.inf, seconds, m.NodeCount)
        early_stopped = status == GRB.USER_OBJ_LIMIT
        if m.SolCount == 0:
            lb = float(m.ObjBound) if status == GRB.TIME_LIMIT and math.isfinite(m.ObjBound) else -math.inf
            return PricingOutcome(g.gid, "time_limit" if status == GRB.TIME_LIMIT else "error", lb, [], math.inf, seconds, m.NodeCount)
        self._remember_start()
        columns: List[Column] = []
        seen = set()
        for k in range(m.SolCount):
            m.Params.SolutionNumber = k
            col = self._extract_column(use_pool=k > 0)
            if col.signature in seen:
                continue
            seen.add(col.signature)
            rc = reduced_cost(col, duals, phase_one)
            if rc < -TOL:
                columns.append(col)
        columns.sort(key=lambda c: reduced_cost(c, duals, phase_one))
        best_rc = float(m.ObjVal)
        if early_stopped:
            lb = float(m.ObjBound) if math.isfinite(m.ObjBound) else -math.inf
            return PricingOutcome(g.gid, "early_stop", lb, columns, best_rc, seconds, m.NodeCount)
        if status == GRB.OPTIMAL:
            # 自审计：MIP 目标必须等于按主问题系数重算的约化成本
            best = self._extract_column(use_pool=False) if m.SolCount else None
            if best is not None:
                audit = reduced_cost(best, duals, phase_one)
                if abs(audit - best_rc) > 1e-5 * max(1.0, abs(best_rc)):
                    raise RuntimeError(
                        f"定价目标与主问题系数不一致: group={g.gid}, mip={best_rc:.6f}, audit={audit:.6f}"
                    )
            return PricingOutcome(g.gid, "optimal", best_rc, columns, best_rc, seconds, m.NodeCount)
        lb = float(m.ObjBound) if math.isfinite(m.ObjBound) else -math.inf
        return PricingOutcome(g.gid, "time_limit", lb, columns, best_rc, seconds, m.NodeCount)

    def greedy_column(self, blocked: Set[str], time_limit: float) -> Optional[Column]:
        """零对偶下、避开已占座位 ``blocked`` 的该组最优方案；用于构造初始列。"""
        m = self.model
        temp = [m.addConstr(expr <= 0.0) for t, expr in self.resource.items() if t in blocked]
        m.setObjective(self.cost_expr, GRB.MINIMIZE)
        m.Params.TimeLimit = max(0.01, float(time_limit))
        m.Params.Cutoff = GRB.INFINITY
        m.optimize()
        col = self._extract_column(use_pool=False) if m.SolCount else None
        if col is not None:
            self._remember_start()
        for c in temp:
            m.remove(c)
        m.update()
        return col

    def column_from_assignment(self, assignment: Mapping[PassengerKey, str],
                               protected: Optional[Mapping[PassengerKey, str]] = None) -> Optional[Column]:
        """把外部给定的整数放置（如启发式方案）固定进 MIP 并生成列。

        ``protected`` 给出单侧保护旅客已协调好的保护空座（跨组不重复）；未给出时由 MIP 自选。"""
        m = self.model
        temp = []
        for (pk, s), var in self.x.items():
            if assignment.get(pk) != s:
                temp.append(m.addConstr(var == 0.0))
        for (pk, s, t), var in self.z.items():
            if protected and pk in protected:
                temp.append(m.addConstr(var == (1.0 if (assignment.get(pk) == s and protected[pk] == t) else 0.0)))
        m.setObjective(self.cost_expr, GRB.MINIMIZE)
        m.Params.TimeLimit = 5.0
        m.Params.Cutoff = GRB.INFINITY
        m.optimize()
        col = self._extract_column(use_pool=False) if m.SolCount else None
        for c in temp:
            m.remove(c)
        m.update()
        return col

    # -------------------------------------------------------------- 列抽取
    def _extract_column(self, *, use_pool: bool) -> Column:
        inst, g = self.inst, self.group
        val = (lambda v: v.Xn) if use_pool else (lambda v: v.X)
        assignment: Dict[PassengerKey, str] = {}
        for (pk, s), v in self.x.items():
            if val(v) > 0.5:
                assignment[pk] = s
        protected: List[Tuple[PassengerKey, str]] = []
        for (pk, s, t), v in self.z.items():
            if val(v) > 0.5:
                protected.append((pk, t))
        for p in g.passengers:
            if p.both_side:
                for t in inst.both_side_neighbors(assignment[p.key]):
                    protected.append((p.key, t))
        return self.make_column(assignment, protected)

    def make_column(self, assignment: Mapping[PassengerKey, str], protected: Sequence[Tuple[PassengerKey, str]]) -> Column:
        """由整数放置生成一列，并用评估器函数独立计算成本（不用 MIP 目标值）。"""
        inst, g = self.inst, self.group
        occupied = tuple(sorted(assignment.values()))
        resources = tuple(sorted(set(occupied) | {t for _, t in protected}))
        if len(resources) != len(occupied) + len(protected):
            raise RuntimeError(f"组 {g.gid} 方案内部资源冲突: {assignment} / {protected}")
        flags: Set[Location] = set()
        counts: Dict[Tuple[Location, str], int] = {}
        for p in g.passengers:
            s = assignment[p.key]
            if p.row_flag:
                flags.add(inst.row_location(s))
            if p.subrow_flag:
                flags.add(inst.subrow_location(s))
            if p.ssr:
                for kind in ("row", "subrow"):
                    loc = inst.location_of(s, kind)
                    if loc in inst.flag_locations:
                        counts[(loc, p.ssr)] = counts.get((loc, p.ssr), 0) + 1
        infants = tuple(sorted(assignment[p.key] for p in g.passengers if p.is_infant))
        same_baby = 0.0
        for u in infants:
            for v in occupied:
                if v != u and (u, v) in inst.baby_pairs:
                    same_baby += inst.baby_pairs[(u, v)]
        local = inst.column_score(g, assignment)
        return Column(
            gid=g.gid,
            assignment=tuple(sorted(assignment.items())),
            protected=tuple(sorted(protected)),
            resources=resources,
            flag_locations=tuple(sorted(flags, key=str)),
            ssr_counts=tuple(sorted(((loc, tau, c) for (loc, tau), c in counts.items()), key=str)),
            infant_seats=infants,
            occupied=occupied,
            local_score=local,
            same_group_baby=same_baby,
            cost=-local + same_baby,
        )
