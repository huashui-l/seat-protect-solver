"""MP1：受限主问题（Gurobi LP / 列池整数 MIP）。

行结构严格对应文档第 6.2 节：组凸性等式、座位资源容量、条件 SSR 的
激活与人数约束、婴儿影响的线性化。Phase I 用人工列求可行性，人工列
之和为 0 后切换到真实成本。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import gurobipy as gp
from gurobipy import GRB

from .instance import Instance, Location
from .pricing import Column, Duals


class Master:
    def __init__(self, inst: Instance, env: gp.Env):
        self.inst = inst
        self.model = gp.Model("master", env=env)
        self.model.Params.OutputFlag = 0
        self.model.Params.Method = 1          # 对偶单纯形，便于增列热启动
        self.model.Params.Threads = int(inst.config.get("cg_gurobi", {}).get("master_threads", 1))
        self.phase_one = True
        self.columns: List[Column] = []
        self.column_vars: List[gp.Var] = []
        self.signatures: Set[Any] = set()
        self._build_rows()

    # ------------------------------------------------------------------ 行
    def _build_rows(self) -> None:
        inst, m = self.inst, self.model
        # 组凸性：Σ_k y_gk + art_g = 1
        self.convexity: Dict[int, gp.Constr] = {}
        self.artificial: Dict[int, gp.Var] = {}
        for g in inst.groups:
            art = m.addVar(lb=0.0, ub=1.0, obj=1.0, name=f"art_{g.gid}")
            self.artificial[g.gid] = art
            self.convexity[g.gid] = m.addConstr(art == 1.0, name=f"conv_{g.gid}")
        # 座位资源：Σ a_gks y_gk <= 1
        self.seat_rows: Dict[str, gp.Constr] = {
            s: m.addConstr(gp.LinExpr() <= 1.0, name=f"seat_{s}") for s in inst.seat_ids
        }
        # 条件 SSR：a_{l,tau} in [0,1]；每个带标志组的激活行；类型人数行
        self.ssr_var: Dict[Tuple[Location, str], gp.Var] = {}
        self.ssr_activation_rows: Dict[Tuple[int, Location, str], gp.Constr] = {}
        self.ssr_count_rows: Dict[Tuple[Location, str], gp.Constr] = {}
        for loc in sorted(inst.flag_locations, key=str):
            for tau in inst.ssr_types:
                M = inst.big_m(loc, tau)
                if M <= 0:
                    continue
                a = m.addVar(lb=0.0, ub=1.0, obj=0.0, name=f"act_{loc[0]}_{loc[2]}_{tau}")
                self.ssr_var[(loc, tau)] = a
                for gid in sorted(inst.flag_groups):
                    self.ssr_activation_rows[(gid, loc, tau)] = m.addConstr(
                        -a <= 0.0, name=f"ssrflag_{gid}_{loc[0]}_{loc[2]}_{tau}"
                    )
                self.ssr_count_rows[(loc, tau)] = m.addConstr(
                    M * a <= float(M + 1), name=f"ssrcnt_{loc[0]}_{loc[2]}_{tau}"
                )
        # 婴儿：Σ (i_gku + o_gkv) y_gk - b_uv <= 1，b 成本 c_uv = -B(u,v) >= 0（Phase II）
        self.baby_var: Dict[Tuple[str, str], gp.Var] = {}
        self.baby_rows: Dict[Tuple[str, str], gp.Constr] = {}
        for (u, v), score in inst.baby_pairs.items():
            b = m.addVar(lb=0.0, ub=1.0, obj=0.0, name=f"baby_{u}_{v}")
            self.baby_var[(u, v)] = b
            self.baby_rows[(u, v)] = m.addConstr(-b <= 1.0, name=f"babyrow_{u}_{v}")
        m.ModelSense = GRB.MINIMIZE
        m.update()

    # ------------------------------------------------------------------ 列
    def add_columns(self, columns: Sequence[Column]) -> int:
        added = 0
        for col in columns:
            if col.signature in self.signatures:
                continue
            constrs: List[gp.Constr] = [self.convexity[col.gid]]
            coefs: List[float] = [1.0]
            for s in col.resources:
                constrs.append(self.seat_rows[s]); coefs.append(1.0)
            for loc in col.flag_locations:
                for tau in self.inst.ssr_types:
                    row = self.ssr_activation_rows.get((col.gid, loc, tau))
                    if row is not None:
                        constrs.append(row); coefs.append(1.0)
            for loc, tau, count in col.ssr_counts:
                row = self.ssr_count_rows.get((loc, tau))
                if row is not None:
                    constrs.append(row); coefs.append(float(count))
            if self.baby_rows:
                infants, occupied = set(col.infant_seats), set(col.occupied)
                touched: Dict[Tuple[str, str], int] = {}
                for u in infants:
                    for pair in self.inst.baby_pairs_by_infant_seat.get(u, []):
                        touched[pair] = touched.get(pair, 0) + 1
                for v in occupied:
                    for pair in self.inst.baby_pairs_by_other_seat.get(v, []):
                        touched[pair] = touched.get(pair, 0) + 1
                for pair, coef in touched.items():
                    constrs.append(self.baby_rows[pair]); coefs.append(float(coef))
            var = self.model.addVar(
                lb=0.0, ub=GRB.INFINITY, obj=0.0 if self.phase_one else col.cost,
                column=gp.Column(coefs, constrs), name=f"y_{col.gid}_{len(self.columns)}",
            )
            self.columns.append(col)
            self.column_vars.append(var)
            self.signatures.add(col.signature)
            added += 1
        if added:
            self.model.update()
        return added

    # ------------------------------------------------------------ 阶段切换
    def start_phase_two(self) -> None:
        if not self.phase_one:
            return
        for art in self.artificial.values():
            art.UB = 0.0
            art.Obj = 0.0
        for col, var in zip(self.columns, self.column_vars):
            var.Obj = col.cost
        for pair, b in self.baby_var.items():
            b.Obj = -self.inst.baby_pairs[pair]   # 成本 = -B(u,v) >= 0
        self.phase_one = False
        self.model.update()

    # ------------------------------------------------------------------ 求解
    def solve_lp(self) -> Tuple[str, float, float]:
        """返回 (状态, 目标成本, 人工列之和)。"""
        m = self.model
        for var in self.column_vars:
            var.VType = GRB.CONTINUOUS
        m.Params.TimeLimit = GRB.INFINITY
        m.optimize()
        if m.Status != GRB.OPTIMAL:
            return ("infeasible" if m.Status == GRB.INFEASIBLE else f"status_{m.Status}", math.inf, math.inf)
        art = sum(v.X for v in self.artificial.values())
        return ("optimal", float(m.ObjVal), float(art))

    def duals(self) -> Duals:
        d = Duals()
        d.convexity = {gid: c.Pi for gid, c in self.convexity.items()}
        d.seat = {s: c.Pi for s, c in self.seat_rows.items() if abs(c.Pi) > 1e-12}
        d.ssr_activation = {k: c.Pi for k, c in self.ssr_activation_rows.items() if abs(c.Pi) > 1e-12}
        d.ssr_count = {k: c.Pi for k, c in self.ssr_count_rows.items() if abs(c.Pi) > 1e-12}
        d.baby = {k: c.Pi for k, c in self.baby_rows.items() if abs(c.Pi) > 1e-12}
        return d

    def fix_column(self, index: int) -> None:
        self.column_vars[index].LB = 1.0
        self.model.update()

    def unfix_all(self) -> None:
        for var in self.column_vars:
            var.LB = 0.0
        self.model.update()

    def fixed_groups(self) -> Set[int]:
        return {col.gid for col, var in zip(self.columns, self.column_vars) if var.LB > 0.5}

    def lp_values(self) -> Dict[int, float]:
        return {i: var.X for i, var in enumerate(self.column_vars) if var.X > 1e-9}

    def solve_integer(self, time_limit: float, start: Optional[Sequence[Column]] = None) -> Tuple[str, Optional[List[Column]], Optional[float]]:
        """列池整数主问题（primal heuristic）。返回 (状态, 选中列, 目标成本)。

        ``start``：已知的完整合法列组合（如启发式方案），作为 MIP start，保证有解。"""
        if self.phase_one:
            return ("phase_one", None, None)
        m = self.model
        start_sigs = {c.signature for c in (start or [])}
        for col, var in zip(self.columns, self.column_vars):
            var.VType = GRB.BINARY
            if start_sigs:
                var.Start = 1.0 if col.signature in start_sigs else 0.0
        m.Params.TimeLimit = max(0.01, float(time_limit))
        m.Params.MIPGap = 0.0
        m.optimize()
        status = m.Status
        if m.SolCount == 0:
            for var in self.column_vars:
                var.VType = GRB.CONTINUOUS
            return ("no_solution", None, None)
        selected = [col for col, var in zip(self.columns, self.column_vars) if var.X > 0.5]
        obj = float(m.ObjVal)
        for var in self.column_vars:
            var.VType = GRB.CONTINUOUS
        gids = [c.gid for c in selected]
        if sorted(gids) != sorted(self.convexity):
            return ("incomplete", None, None)
        return ("optimal" if status == GRB.OPTIMAL else "time_limit", selected, obj)
