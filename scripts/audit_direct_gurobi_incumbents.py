"""Check every saved legal V2 incumbent against the full direct MIP model."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gurobipy as gp
from src.cg_gurobi.instance import Instance
from src.direct_gurobi.model import DirectModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-audit", type=Path, required=True)
    parser.add_argument("--allocations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.input_audit.read_text(encoding="utf-8"))
    assert audit["audit_complete"] and audit["inherited_infeasibility_certificates_revalidated"]
    allocations = {r["case_id"]: r for r in json.loads(gzip.decompress(args.allocations.read_bytes()))
                   if r["directory"].endswith("/focused60")}
    cfg = audit["config"]
    cfg["direct_gurobi"] = dict(threads=1, log=False)
    paths = [ROOT / p for p in ("src/direct_gurobi/model.py", "src/cg_gurobi/instance.py",
                              "src/allocation_evaluator.py")]
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report = dict(purpose="Full-domain direct model, every x variable fixed to a saved independently legal incumbent; checks feasibility and all objective terms. This is model validation, not an optimization benchmark or global optimality proof.",
                  input_audit_sha256=hashlib.sha256(args.input_audit.read_bytes()).hexdigest(),
                  source_sha256=hashes, cases=[], audit_complete=False)
    cases = sorted((c for c in audit["cases"] if c["heuristic"]["valid"]),
                   key=lambda c: (-c["heuristic"]["passengers"], c["case_id"]))
    for case in cases:
        started = time.perf_counter()
        new = json.loads(Path(case["new_seatmap_path"]).read_text(encoding="utf-8"))["seats"]
        old = json.loads(Path(case["old_seatmap_path"]).read_text(encoding="utf-8"))["seats"]
        groups = json.loads(Path(case["groups_path"]).read_text(encoding="utf-8"))["groups"]
        assignment = {tuple(map(int, k.split(","))): v for k, v in allocations[case["case_id"]]["assigned_seats"].items()}
        inst = Instance(new, old, groups, cfg["weights"], cfg)
        with gp.Env(params={"OutputFlag": 0}) as env:
            direct = DirectModel.build(inst, env)
            assert all((key, sid) in direct.assignment for key, sid in assignment.items())
            for (key, sid), var in direct.assignment.items():
                var.LB = var.UB = float(assignment[key] == sid)
            direct.model.Params.TimeLimit = 30
            direct.model.optimize()
            assert direct.model.Status == gp.GRB.OPTIMAL, (case["case_id"], direct.model.Status)
            error = abs(direct.model.ObjVal - case["independent_rescore"]["score"])
            assert error <= 1e-6, (case["case_id"], error)
            report["cases"].append(dict(case_id=case["case_id"], model_status="optimal_for_fixed_assignment",
                variables=direct.model.NumVars, constraints=direct.model.NumConstrs,
                objective=direct.model.ObjVal, expected_score=case["independent_rescore"]["score"],
                objective_error=error, elapsed_seconds=time.perf_counter()-started))
            direct.model.dispose()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(case["case_id"], len(report["cases"]), error, flush=True)
    assert len(report["cases"]) == 69
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    report["audit_complete"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
