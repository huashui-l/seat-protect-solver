"""Solve the complete passenger-seat model with Gurobi."""
from __future__ import annotations

import time
from typing import Any, Dict, Mapping, Sequence, Tuple

import gurobipy as gp
from gurobipy import GRB

from src import allocation_evaluator as evaluator
from src.cg_gurobi.instance import Instance, PassengerKey

from .model import DirectModel


def _config(config: Mapping[str, Any], time_limit: float | None) -> Dict[str, Any]:
    result = dict(config)
    direct = dict(result.get("direct_gurobi", {}))
    direct.setdefault("time_limit", 150.0)
    direct.setdefault("threads", 1)
    direct.setdefault("log", False)
    if time_limit is not None:
        direct["time_limit"] = float(time_limit)
    result["direct_gurobi"] = direct
    return result


def solve(
    new_seats: Sequence[dict],
    old_seats: Sequence[dict],
    groups_data: Sequence[dict],
    weights: Mapping[str, float],
    config: Mapping[str, Any],
    *,
    time_limit: float | None = None,
) -> Dict[str, Any]:
    cfg = _config(config, time_limit)
    started = time.perf_counter()
    instance = Instance(new_seats, old_seats, groups_data, weights, cfg)
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", int(cfg["direct_gurobi"].get("log", False)))
    env.start()
    try:
        direct = DirectModel.build(instance, env)
        direct.model.Params.TimeLimit = max(0.01, float(cfg["direct_gurobi"]["time_limit"]))
        direct.model.Params.Seed = int(cfg["direct_gurobi"].get("seed", 0))
        direct.model.Params.MIPGap = 0.0
        direct.model.Params.MIPGapAbs = 0.0
        direct.model.optimize()

        assigned: Dict[PassengerKey, str] = {}
        if direct.model.SolCount:
            for p in instance.passengers.values():
                selected = [
                    sid for sid in p.domain
                    if direct.assignment[(p.key, sid)].X > 0.5
                ]
                if len(selected) == 1:
                    assigned[p.key] = selected[0]

        hard, unassigned, detail = evaluator.count_hard_constraint_violations(
            list(new_seats), list(groups_data), assigned, dict(cfg)
        )
        score, score_detail = evaluator.calculate_soft_score(
            list(new_seats), list(old_seats), list(groups_data), assigned,
            dict(weights), dict(cfg),
        )
        status = {
            GRB.OPTIMAL: "optimal",
            GRB.TIME_LIMIT: "time_limit",
            GRB.INFEASIBLE: "infeasible",
            GRB.INTERRUPTED: "interrupted",
        }.get(direct.model.Status, f"status_{direct.model.Status}")
        complete = bool(assigned) and hard == 0 and unassigned == 0
        proven = status == "optimal" and complete
        objective = float(direct.model.ObjVal) if direct.model.SolCount else None
        bound = float(direct.model.ObjBound) if direct.model.SolCount or status != "infeasible" else None
        objective_error = None if objective is None else abs(objective - score)
        return {
            "status": "complete" if complete else status,
            "solver_status": status,
            "complete": complete,
            "proven_optimal": proven,
            "assigned_seats": assigned,
            "total_score": score if assigned else None,
            "score_detail": score_detail,
            "hard_violations": hard,
            "unassigned": unassigned,
            "violation_detail": detail,
            "model_objective": objective,
            "model_bound": bound,
            "model_objective_error": objective_error,
            "mip_gap": float(direct.model.MIPGap) if direct.model.SolCount else None,
            "variables": direct.model.NumVars,
            "constraints": direct.model.NumConstrs,
            "seconds": time.perf_counter() - started,
        }
    finally:
        env.dispose()

