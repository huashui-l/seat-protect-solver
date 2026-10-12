"""Run the audited V2 exact methods, preserving separate 60s and 900s records."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import allocation_evaluator as evaluator


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def verified_proof(method, raw, legal, parity):
    bounds = []
    if method == "direct_gurobi":
        bound = raw.get("model_bound")
        if isinstance(bound, (int, float)) and math.isfinite(bound):
            bounds.append(bound)
    else:
        if raw.get("lp_bound_certified") and raw.get("lp_bound") is not None:
            bounds.append(raw["lp_bound"])
        cabins = raw.get("cabins", {})
        if cabins and all(c.get("bp_bound_certified") for c in cabins.values()) and raw.get("bp_bound") is not None:
            bounds.append(raw["bp_bound"])
    bound = min(bounds) if bounds else None
    score = raw.get("total_score")
    proven = bool(raw.get("proven_optimal") and legal and parity and bound is not None
                  and score is not None and abs(bound - score) <= 1e-5)
    return proven, bound


def run_case(case, method, budget, config, folder):
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    inputs = [(Path(case["groups_path"]), case["groups_sha256"]),
              (Path(case["old_seatmap_path"]), case["old_seatmap_sha256"]),
              (Path(case["new_seatmap_path"]), case["new_seatmap_sha256"])]
    assert all(sha(path) == digest for path, digest in inputs)
    cfg = copy.deepcopy(config)
    # Resource limits are fixed for every case; search settings stay at the
    # upstream defaults. Parallelism is across cases rather than within pricing.
    if method == "direct_gurobi":
        cfg["direct_gurobi"] = dict(time_limit=budget, threads=1, seed=0, log=False)
    else:
        cg = cfg.setdefault("cg_gurobi", {})
        cg.update(total_time_limit=budget, pricing_workers=1, master_threads=1, pricing_threads=1, log=False)
    config_path, raw_path = folder / "config.json", folder / "raw.json"
    write(config_path, cfg)
    command = [sys.executable, "-m", "src." + method, "--config", str(config_path.resolve()),
        "--groups", str(Path(case["groups_path"]).resolve()),
        "--old-seatmap", str(Path(case["old_seatmap_path"]).resolve()),
        "--new-seatmap", str(Path(case["new_seatmap_path"]).resolve()),
        "--time-limit", str(budget), "--output", str(raw_path.resolve()), "--quiet"]
    started = time.perf_counter()
    error, raw, exit_code = None, {}, None
    with (folder / "solver.log").open("wb") as log:
        try:
            process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=budget + 120)
            exit_code = process.returncode
            if exit_code:
                error = f"solver process exited with code {exit_code}"
            elif not raw_path.exists():
                error = "solver returned without raw output"
            else:
                raw = read(raw_path)
        except subprocess.TimeoutExpired:
            error = "watchdog exceeded configured budget plus 120s; child killed and joined"
    process_seconds = time.perf_counter() - started
    assigned = {tuple(map(int, key.split(":"))): value for key, value in raw.get("assigned_seats", {}).items()}
    new = read(Path(case["new_seatmap_path"]))["seats"]
    old = read(Path(case["old_seatmap_path"]))["seats"]
    groups = read(Path(case["groups_path"]))["groups"]
    hard, missing, detail = evaluator.count_hard_constraint_violations(new, groups, assigned, cfg)
    score, parts = evaluator.calculate_soft_score(new, old, groups, assigned, cfg["weights"], cfg)
    legal = not error and hard == missing == 0
    raw_score = raw.get("total_score")
    parity = raw_score is not None and abs(raw_score - score) <= 1e-6
    if method == "direct_gurobi" and raw.get("model_objective_error") is not None:
        parity = parity and raw["model_objective_error"] <= 1e-6
    proven, bound = verified_proof(method, raw, legal, parity)
    assert all(sha(path) == digest for path, digest in inputs)
    infeasible = bool(raw.get("proven_infeasible")) if method == "direct_gurobi" else False
    record = dict(case_id=case["case_id"], method=method, budget_seconds=budget,
        complete_legal=legal, valid_for_comparison=legal and parity,
        score=score if legal and parity else None, independently_evaluated_score=score if legal else None,
        score_detail=parts if legal else None,
        hard_violations=hard, unassigned=missing, violation_detail=detail,
        objective_parity=parity if assigned else None, solver_status=raw.get("solver_status", raw.get("status")),
        reported_proven_optimal=bool(raw.get("proven_optimal")), proven_optimal=proven,
        proven_infeasible_by_method=infeasible,
        external_infeasibility_certificate=case.get("inherited_infeasibility_certificate"),
        certified_upper_bound=bound, safe_bound=raw.get("safe_bound"),
        gap_to_certified_upper_percent=(max(0, bound-score)/max(1, abs(score))*100)
            if bound is not None and legal else None,
        process_seconds=process_seconds, solver_seconds=raw.get("seconds"),
        process_over_budget=process_seconds > budget,
        error=error, exit_code=exit_code, command=command, config_sha256=sha(config_path),
        raw_output=str(raw_path.relative_to(ROOT)), raw_sha256=sha(raw_path) if raw_path.exists() else None,
        log_sha256=sha(folder / "solver.log"), groups_sha256=case["groups_sha256"],
        old_seatmap_sha256=case["old_seatmap_sha256"], new_seatmap_sha256=case["new_seatmap_sha256"])
    write(folder / "record.json", record)
    return record


def needs_followup(record):
    return not (record["proven_optimal"] or record["proven_infeasible_by_method"]
                or record["external_infeasibility_certificate"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-audit", type=Path, required=True)
    parser.add_argument("--model-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    args.output = args.output.resolve()
    audit, model_audit = read(args.input_audit), read(args.model_audit)
    assert audit["audit_complete"] and audit["case_count"] == 72
    assert audit["inherited_infeasibility_certificates_revalidated"]
    assert model_audit["audit_complete"] and len(model_audit["cases"]) == 69
    assert model_audit["input_audit_sha256"] == sha(args.input_audit)
    sources = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(
        [*(ROOT / "src").rglob("*.py"), *(ROOT / "archive").rglob("*.py"),
         Path(__file__).resolve()]))}
    assert all(sources[p] == h for p, h in model_audit["source_sha256"].items())
    folder = args.output.parent
    folder.mkdir(parents=True, exist_ok=True)
    metadata = dict(algorithm_commit=subprocess.check_output(["git", "rev-parse", "HEAD"],cwd=ROOT,text=True).strip(),
        sources_sha256=sources, upstream_commit=audit["upstream_commit"], input_audit_sha256=sha(args.input_audit),
        model_audit_sha256=sha(args.model_audit), workers=args.workers,
        schedule="72 cases per method at60s, then900s for each unresolved record; proven infeasible cases need no further optimization.",
        timing="Solver budget includes model setup as supported by each existing route; process and solver elapsed reported separately, all overruns retained. External audit excluded.",
        heuristic_batch="focused60, inherited 72 results, no rerun",
        resource_settings="One Gurobi thread per model; one pricing worker; fixed across cases. Other CG search settings remain upstream defaults.")
    report = dict(metadata=metadata, records=[], complete=False)
    if args.output.exists():
        report = read(args.output)
        assert report["metadata"] == metadata, "refusing to mix versions or audit inputs"
    existing = {(r["case_id"],r["method"],r["budget_seconds"]): r for r in report["records"]}
    for record in existing.values():
        if record["raw_sha256"]:
            assert sha(ROOT / record["raw_output"]) == record["raw_sha256"]
    for budget in (60, 900):
        jobs = []
        for case in audit["cases"]:
            for method in ("direct_gurobi", "cg_gurobi"):
                key = (case["case_id"],method,budget)
                if key in existing:
                    continue
                if budget == 900 and not needs_followup(existing[(case["case_id"],method,60)]):
                    continue
                jobs.append((case, method))
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run_case, case, method, budget, audit["config"],
                folder / "runs" / method / str(budget) / case["case_id"].replace(":", "_"))
                for case, method in jobs]
            for future in as_completed(futures):
                record = future.result()
                key = (record["case_id"],record["method"],budget)
                existing[key] = record
                report["records"].append(record)
                write(args.output, report)
                print(len(existing), record["case_id"], record["method"], budget,
                      record["score"], record["proven_optimal"], record["error"], flush=True)
    assert all(sha(ROOT / p) == h for p,h in sources.items())
    report["complete"] = True
    write(args.output, report)


if __name__ == "__main__":
    main()
