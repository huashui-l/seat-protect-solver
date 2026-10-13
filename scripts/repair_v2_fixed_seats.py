"""Create a separate V2 corpus revision with audited fixed-seat repairs.

Only previously certified-infeasible inputs are repaired. Minimize the number
of fixed passengers whose seat changes, retain every fixed-passenger identity,
and verify a complete legal assignment. Original files are never overwritten.
"""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import gurobipy as gp
from gurobipy import GRB

from scripts.run_cg_acceleration_study import read, write, digest, ROOT
from src.cg_gurobi.instance import Instance
from src.direct_gurobi.model import DirectModel
from src import allocation_evaluator as evaluator


def repair(case, config, output, budget):
    original = read(case["groups_path"])
    relaxed = copy.deepcopy(original)
    fixed = {}
    for g in relaxed["groups"]:
        for p in g["psrs"]:
            if p.get("newSeat", {}).get("seatNum"):
                fixed[(g["groupId"], p["hostnum"])] = p["newSeat"]["seatNum"]
                del p["newSeat"]
    new, old = (read(case[k])["seats"] for k in ("new_seatmap_path", "old_seatmap_path"))
    cfg = copy.deepcopy(config)
    cfg["direct_gurobi"] = dict(threads=1, log=False)
    started = time.perf_counter()
    inst = Instance(new, old, relaxed["groups"], cfg["weights"], cfg)
    # Baby interaction is an objective term only. This diagnostic minimizes
    # fixed-seat edits rather than soft-score cost, so no baby auxiliaries are needed.
    inst.baby_pairs = {}
    with gp.Env(empty=True) as env:
        env.setParam("OutputFlag", 0)
        env.start()
        direct = DirectModel.build(inst, env)
        m = direct.model
        edits = gp.quicksum(1-direct.assignment[(pk,s)] if (pk,s) in direct.assignment else 1
                           for pk,s in fixed.items())
        m.setObjective(edits, GRB.MINIMIZE)
        m.Params.TimeLimit = max(0, budget-(time.perf_counter()-started))
        m.Params.MIPFocus = 1
        m.optimize()
        result = dict(case_id=case["case_id"], original_hash=digest(case["groups_path"]),
                      status=m.Status, seconds=time.perf_counter()-started,
                      model_bound=float(m.ObjBound), solution_count=m.SolCount)
        if not m.SolCount:
            return result
        assignment = {pk:s for (pk,s),v in direct.assignment.items() if v.X > 0.5}
        repaired = copy.deepcopy(original)
        changes = []
        for g in repaired["groups"]:
            for p in g["psrs"]:
                pk = g["groupId"], p["hostnum"]
                if pk in fixed:
                    p["newSeat"]["seatNum"] = assignment[pk]
                    if fixed[pk] != assignment[pk]:
                        changes.append(dict(passenger=list(pk), old=fixed[pk], new=assignment[pk]))
        hard, missing, details = evaluator.count_hard_constraint_violations(new, repaired["groups"], assignment, cfg)
        assert hard == missing == 0, details
        assert len(changes) == round(m.ObjVal)
        # No passenger/group/SSR/old-seat changes; only existing newSeat values.
        stripped_original, stripped_repaired = copy.deepcopy(original), copy.deepcopy(repaired)
        for payload in (stripped_original, stripped_repaired):
            for g in payload["groups"]:
                for p in g["psrs"]:
                    p.pop("newSeat", None)
        assert stripped_original == stripped_repaired
        repaired["revision"] = dict(kind="fixed-seat-feasibility-repair-v1",
            original_sha256=result["original_hash"], changes=changes,
            minimum_edits_proven=m.Status == GRB.OPTIMAL,
            notice="Derived input; original benchmark results do not apply to this revision.")
        path = output/"cases"/Path(case["groups_path"]).name
        write(path, repaired)
        result.update(repaired_path=str(path.relative_to(ROOT)), repaired_hash=digest(path),
            changes=changes, minimum_edits_proven=m.Status == GRB.OPTIMAL,
            complete_legal=True, violations=hard, unassigned=missing,
            witness={f"{pk[0]}:{pk[1]}":s for pk,s in assignment.items()},
            soft_score_optimality_claim=False)
        print(json.dumps({k:v for k,v in result.items() if k != "witness"},ensure_ascii=False),flush=True)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=300)
    args = parser.parse_args()
    output = args.output.resolve()
    audit = read(ROOT/"build/v2-exact-20261012/input-audit.json")
    cases = [c for c in audit["cases"] if c.get("inherited_infeasibility_certificate")]
    assert len(cases) == 3
    if (output/"repairs.json").exists():
        existing=read(output/"repairs.json")
        assert existing["all_repaired_legal"]
        assert {r["case_id"] for r in existing["repairs"]}=={c["case_id"] for c in cases}
        for record in existing["repairs"]:
            case=next(c for c in cases if c["case_id"]==record["case_id"])
            assert digest(case["groups_path"])==record["original_hash"]
            assert digest(ROOT/record["repaired_path"])==record["repaired_hash"]
        print("Existing audited repair revision retained; no files overwritten.")
        return
    with ThreadPoolExecutor(max_workers=3) as executor:
        records = list(executor.map(lambda c:repair(c,audit["config"],output,args.budget), cases))
    write(output/"repairs.json", dict(original_corpus="benchmark_v2_2026-10-07", repairs=records,
        policy="Minimize changed fixed seats; retain fixed passengers, all other passenger fields and difficulty dimensions.",
        all_repaired_legal=all(r.get("complete_legal") for r in records)))


if __name__ == "__main__":
    main()
