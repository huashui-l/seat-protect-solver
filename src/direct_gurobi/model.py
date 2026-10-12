"""Full passenger-seat assignment model built with Gurobi.

This module owns variables and constraints only.  Input normalization and
score definitions remain in ``src.cg_gurobi.instance`` and the independent
evaluator, so the direct and column-generation routes use the same semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple

import gurobipy as gp
from gurobipy import GRB

from src.cg_gurobi.instance import Instance, PassengerKey


@dataclass
class DirectModel:
    instance: Instance
    model: gp.Model
    assignment: Dict[Tuple[PassengerKey, str], gp.Var]
    single_protection: Dict[Tuple[PassengerKey, str, str], gp.Var]
    baby_interactions: Dict[Tuple[PassengerKey, str, PassengerKey, str], gp.Var]
    compact_x: Dict[int, gp.Var]
    compact_y: Dict[int, gp.Var]

    @classmethod
    def build(cls, instance: Instance, env: gp.Env | None = None) -> "DirectModel":
        model = gp.Model("seat_protect_direct", env=env)
        cfg = instance.config.get("direct_gurobi", {})
        model.Params.OutputFlag = int(cfg.get("log", False))
        model.Params.Threads = int(cfg.get("threads", 1))
        model.Params.MIPGap = 0.0
        model.Params.MIPGapAbs = 0.0
        model.Params.FeasibilityTol = 1e-8
        model.Params.IntFeasTol = 1e-7

        assignment: Dict[Tuple[PassengerKey, str], gp.Var] = {}
        for p in instance.passengers.values():
            for sid in p.domain:
                assignment[(p.key, sid)] = model.addVar(
                    vtype=GRB.BINARY, name=f"x_{p.gid}_{p.hostnum}_{sid}"
                )
            if not p.domain:
                raise ValueError(f"passenger has no legal seat domain: {p.key}")
            model.addConstr(
                gp.quicksum(assignment[(p.key, sid)] for sid in p.domain) == 1,
                name=f"assign_{p.gid}_{p.hostnum}",
            )

        resource: Dict[str, gp.LinExpr] = {
            sid: gp.LinExpr() for sid in instance.seat_ids
        }
        for (key, sid), var in assignment.items():
            resource[sid] += var

        single_protection: Dict[Tuple[PassengerKey, str, str], gp.Var] = {}
        for p in instance.passengers.values():
            for sid in p.domain:
                candidates = instance.single_side_neighbors(sid)
                if not p.single_side:
                    continue
                vars_for_assignment = []
                for empty_sid in candidates:
                    var = model.addVar(
                        vtype=GRB.BINARY,
                        name=f"single_{p.gid}_{p.hostnum}_{sid}_{empty_sid}",
                    )
                    single_protection[(p.key, sid, empty_sid)] = var
                    vars_for_assignment.append(var)
                    resource[empty_sid] += var
                model.addConstr(
                    gp.quicksum(vars_for_assignment)
                    == assignment[(p.key, sid)],
                    name=f"single_choice_{p.gid}_{p.hostnum}_{sid}",
                )

        for p in instance.passengers.values():
            if not p.both_side:
                continue
            neighbors_fn = instance.both_side_neighbors
            for sid in p.domain:
                for empty_sid in neighbors_fn(sid):
                    resource[empty_sid] += assignment[(p.key, sid)]

        for sid, expr in resource.items():
            model.addConstr(expr <= 1.0, name=f"resource_{sid}")

        cls._add_caregiver_constraints(instance, model, assignment)
        cls._add_ssr_isolation_constraints(instance, model, assignment)
        compact_x, compact_y = cls._add_compactness_constraints(instance, model, assignment)
        baby_interactions = cls._add_baby_constraints(instance, model, assignment)

        objective = gp.LinExpr()
        for p in instance.passengers.values():
            for sid in p.domain:
                objective += p.score[sid] * assignment[(p.key, sid)]
        for gid, var in compact_x.items():
            objective += instance.w_c * instance.compact_x_factor * var
        for gid, var in compact_y.items():
            objective += instance.w_c * instance.compact_y_factor * var
        for (infant_key, infant_sid, other_key, other_sid), var in baby_interactions.items():
            objective += instance.baby_pairs[(infant_sid, other_sid)] * var
        model.setObjective(objective, GRB.MAXIMIZE)

        model.ModelSense = GRB.MAXIMIZE
        model.update()
        return cls(
            instance,
            model,
            assignment,
            single_protection,
            baby_interactions,
            compact_x,
            compact_y,
        )

    @staticmethod
    def _add_caregiver_constraints(
        instance: Instance,
        model: gp.Model,
        assignment: Mapping[Tuple[PassengerKey, str], gp.Var],
    ) -> None:
        for p in instance.passengers.values():
            if not p.needs_caregiver:
                continue
            group = instance.group_by_id[p.gid]
            caregivers = [
                q for q in group.passengers
                if q.is_caregiver and q.key != p.key
            ]
            for sid in p.domain:
                terms = [
                    assignment[(q.key, neighbor)]
                    for q in caregivers
                    for neighbor in instance.caregiver_neighbors(p, sid)
                    if (q.key, neighbor) in assignment
                ]
                lhs = assignment[(p.key, sid)]
                if terms:
                    model.addConstr(
                        lhs <= gp.quicksum(terms),
                        name=f"caregiver_{p.gid}_{p.hostnum}_{sid}",
                    )
                else:
                    model.addConstr(
                        lhs == 0.0,
                        name=f"caregiver_missing_{p.gid}_{p.hostnum}_{sid}",
                    )

    @staticmethod
    def _add_ssr_isolation_constraints(
        instance: Instance,
        model: gp.Model,
        assignment: Mapping[Tuple[PassengerKey, str], gp.Var],
    ) -> None:
        # Any flagged SSR activates isolation at its location for every SSR
        # type, including types different from the triggering passenger's.
        counts = {}
        triggers = {}
        for passenger in instance.passengers.values():
            if not passenger.ssr:
                continue
            for sid in passenger.domain:
                var = assignment[(passenger.key, sid)]
                for kind, flagged in (("row", passenger.row_flag), ("subrow", passenger.subrow_flag)):
                    location = instance.location_of(sid, kind)
                    if location not in instance.flag_locations:
                        continue
                    counts.setdefault((location, passenger.ssr), []).append(var)
                    if flagged:
                        triggers.setdefault((location, passenger.key), []).append(var)
        for index, location in enumerate(sorted(instance.flag_locations, key=str)):
            types = [ssr for ssr in instance.ssr_types if instance.big_m(location, ssr) > 1]
            if not types:
                continue
            activation = model.addVar(lb=0.0, ub=1.0, name=f"ssr_activation_{index}")
            for (trigger_location, key), terms in triggers.items():
                if trigger_location == location:
                    model.addConstr(gp.quicksum(terms) <= activation,
                                    name=f"ssr_trigger_{index}_{key[0]}_{key[1]}")
            for ssr in types:
                maximum = instance.big_m(location, ssr)
                model.addConstr(gp.quicksum(counts.get((location, ssr), []))
                                + (maximum - 1) * activation <= maximum,
                                name=f"ssr_count_{index}_{ssr}")

    @staticmethod
    def _add_compactness_constraints(
        instance: Instance,
        model: gp.Model,
        assignment: Mapping[Tuple[PassengerKey, str], gp.Var],
    ) -> Tuple[Dict[int, gp.Var], Dict[int, gp.Var]]:
        if instance.w_c > 0 or instance.compact_x_factor < 0 or instance.compact_y_factor < 0:
            raise ValueError("direct model requires nonpositive compactness weight/factors")
        compact_x: Dict[int, gp.Var] = {}
        compact_y: Dict[int, gp.Var] = {}
        for group in instance.groups:
            if group.size <= 1:
                continue
            d_x = model.addVar(lb=0.0, name=f"compact_x_{group.gid}")
            d_y = model.addVar(lb=0.0, name=f"compact_y_{group.gid}")
            compact_x[group.gid] = d_x
            compact_y[group.gid] = d_y
            n = float(group.size)
            centroid_x = gp.quicksum(
                instance.x(sid) * assignment[(p.key, sid)]
                for p in group.passengers for sid in p.domain
            ) / n
            centroid_y = gp.quicksum(
                instance.y(sid) * assignment[(p.key, sid)]
                for p in group.passengers for sid in p.domain
            ) / n
            for p in group.passengers:
                px = gp.quicksum(instance.x(sid) * assignment[(p.key, sid)] for sid in p.domain)
                py = gp.quicksum(instance.y(sid) * assignment[(p.key, sid)] for sid in p.domain)
                model.addConstr(d_x >= px - centroid_x, name=f"compact_x_pos_{group.gid}_{p.hostnum}")
                model.addConstr(d_x >= centroid_x - px, name=f"compact_x_neg_{group.gid}_{p.hostnum}")
                model.addConstr(d_y >= py - centroid_y, name=f"compact_y_pos_{group.gid}_{p.hostnum}")
                model.addConstr(d_y >= centroid_y - py, name=f"compact_y_neg_{group.gid}_{p.hostnum}")
        return compact_x, compact_y

    @staticmethod
    def _add_baby_constraints(
        instance: Instance,
        model: gp.Model,
        assignment: Mapping[Tuple[PassengerKey, str], gp.Var],
    ) -> Dict[Tuple[PassengerKey, str, PassengerKey, str], gp.Var]:
        variables: Dict[Tuple[PassengerKey, str, PassengerKey, str], gp.Var] = {}
        infants = [p for p in instance.passengers.values() if p.is_infant]
        for infant in infants:
            for other in instance.passengers.values():
                if other.gid == infant.gid:
                    continue
                for infant_sid in infant.domain:
                    for other_sid in other.domain:
                        score = instance.baby_pairs.get((infant_sid, other_sid), 0.0)
                        if abs(score) <= 1e-12:
                            continue
                        key = (infant.key, infant_sid, other.key, other_sid)
                        var = model.addVar(lb=0.0, ub=1.0, name=f"baby_{infant.gid}_{infant.hostnum}_{infant_sid}_{other.gid}_{other.hostnum}_{other_sid}")
                        variables[key] = var
                        model.addConstr(var <= assignment[(infant.key, infant_sid)])
                        model.addConstr(var <= assignment[(other.key, other_sid)])
                        model.addConstr(
                            var >= assignment[(infant.key, infant_sid)] + assignment[(other.key, other_sid)] - 1.0
                        )
        return variables
