"""Structure audit and paired, resumable CG baseline/hybrid experiments."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from scripts.benchmark_methods import load_cases
from scripts.benchmark_v2_exact import verified_proof
from src import allocation_evaluator as evaluator
from src.cg_gurobi.instance import Instance

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8", newline="\n")
    temp.replace(path)


def cases_and_audit():
    audit = read(ROOT/"build/v2-exact-20261012/input-audit.json")
    cases = [dict(c, corpus="v2_72", config=audit["config"]) for c in audit["cases"]]
    cases += [dict(c, corpus="formal40", config=read(ROOT/"config.json"))
              for c in load_cases(["formal24", "d4"])]
    assert len(cases) == len({c["case_id"] for c in cases}) == 112
    rows = []
    for c in cases:
        paths = [Path(c[k]).resolve() for k in ("groups_path", "old_seatmap_path", "new_seatmap_path")]
        groups, old, new = (read(p)[k] for p, k in zip(paths, ("groups", "seats", "seats")))
        inst = Instance(new, old, groups, c["config"]["weights"], c["config"])
        sizes = [g.size for g in inst.groups]
        pax = list(inst.passengers.values())
        c["input_hashes"] = {str(p): digest(p) for p in paths}
        rows.append(dict(case_id=c["case_id"], corpus=c["corpus"], passengers=len(pax), seats=len(new),
            seat_load=len(pax)/len(new), groups=len(sizes), max_pnr=max(sizes),
            mean_pnr=sum(sizes)/len(sizes), pnr_sizes=dict(Counter(sizes)),
            domain_sum=sum(len(p.domain) for p in pax), max_group_domain_sum=max(sum(len(p.domain) for p in g.passengers) for g in inst.groups),
            fixed=sum(bool(p.fixed_seat) for p in pax), infants=sum(p.is_infant for p in pax),
            caregiver_demands=sum(p.needs_caregiver for p in pax),
            single_protection=sum(p.single_side for p in pax), both_protection=sum(p.both_side for p in pax),
            ssr_flags=sum(p.row_flag or p.subrow_flag for p in pax),
            ssr_types=dict(Counter(p.ssr for p in pax if p.ssr)),
            input_hashes=c["input_hashes"], inherited_infeasibility_certificate=c.get("inherited_infeasibility_certificate")))
    return cases, rows


def freeze_plan(output, structure, plan, sources):
    """Reject a changed resume contract before touching existing evidence."""
    documents={"structure.json":structure}
    if plan is not None:
        documents["plan.json"]=plan
    for name,value in documents.items():
        if (output/name).exists():
            assert read(output/name)==json.loads(json.dumps(value)), f"Resume {name} mismatch; evidence unchanged"
    snapshot=output/"source-snapshot.zip"
    if plan is not None and snapshot.exists():
        with zipfile.ZipFile(snapshot) as archive:
            assert archive.testzip() is None
            for p in sources:
                key=str(p.relative_to(ROOT)).replace("\\","/")
                assert hashlib.sha256(archive.read(key)).hexdigest()==plan["source_hashes"][str(p.resolve())]
    for name,value in documents.items():
        if not (output/name).exists():write(output/name,value)
    if plan is not None and not snapshot.exists():
        with zipfile.ZipFile(snapshot,"w",zipfile.ZIP_DEFLATED) as archive:
            for p in sources:archive.write(p,str(p.relative_to(ROOT)))


def run(case, variant, budget, output, source_hashes, entrypoint="src.cg_acceleration"):
    slug = case["case_id"].replace(":", "_").replace("/", "_")
    folder = output/variant/slug
    folder.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(case["config"])
    cfg.setdefault("cg_gurobi", {}).update(total_time_limit=budget, pricing_workers=1,
        master_threads=1, pricing_threads=1, log=False)
    cfg["cg_acceleration"] = dict(heuristic=variant == "hybrid")
    config_path, raw_path, record_path = folder/"config.json", folder/"raw.json", folder/"record.json"
    contract = dict(case_id=case["case_id"], variant=variant, budget=budget,
                    entrypoint=entrypoint,
                    input_hashes=case["input_hashes"], source_hashes=source_hashes,
                    config_hash=hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest())
    if record_path.exists():
        previous = read(record_path)
        assert previous["contract"] == contract, "Resume contract mismatch"
        assert previous.get("raw_hash") == (digest(raw_path) if raw_path.exists() else None)
        return previous
    assert all(digest(p) == h for p, h in {**source_hashes, **case["input_hashes"]}.items())
    write(config_path, cfg)
    command = [sys.executable, "-m", entrypoint, "--config", str(config_path.resolve()),
        "--groups", str(Path(case["groups_path"]).resolve()),
        "--old-seatmap", str(Path(case["old_seatmap_path"]).resolve()),
        "--new-seatmap", str(Path(case["new_seatmap_path"]).resolve()),
        "--time-limit", str(budget), "--output", str(raw_path.resolve()), "--quiet"]
    error, raw = None, {}
    started = time.perf_counter()
    with (folder/"solver.log").open("wb") as log:
        try:
            process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=budget+120)
            if process.returncode:
                error = f"exit {process.returncode}"
            elif not raw_path.exists():
                error = "missing raw output"
            else:
                raw = read(raw_path)
        except subprocess.TimeoutExpired:
            error = "watchdog timeout; child killed and joined"
    elapsed = time.perf_counter()-started
    assigned = {tuple(map(int, k.split(":"))): v for k,v in raw.get("assigned_seats", {}).items()}
    groups = read(case["groups_path"])["groups"]
    new, old = (read(case[k])["seats"] for k in ("new_seatmap_path", "old_seatmap_path"))
    violations, missing, details = evaluator.count_hard_constraint_violations(new, groups, assigned, cfg)
    legal = error is None and violations == missing == 0
    score, parts = evaluator.calculate_soft_score(new, old, groups, assigned, cfg["weights"], cfg)
    parity = legal and raw.get("total_score") is not None and abs(raw["total_score"]-score) <= 1e-6
    proven, bound = verified_proof("cg_gurobi", raw, legal, parity)
    record = dict(contract=contract, complete_legal=legal, valid=parity,
        score=score if parity else None, score_detail=parts if parity else None,
        violations=violations, missing=missing, violation_detail=details, error=error,
        process_seconds=elapsed, over_budget=elapsed > budget, solver_seconds=raw.get("seconds"),
        proven_optimal=proven, certified_upper_bound=bound,
        lp_bound_certified=raw.get("lp_bound_certified", False), safe_bound=raw.get("safe_bound"),
        acceleration_profile=raw.get("acceleration_profile"),
        cabins={k:{key: val for key,val in c.items() if key != "assigned_seats"}
                for k,c in raw.get("cabins",{}).items()},
        command=command, raw_hash=digest(raw_path) if raw_path.exists() else None,
        log_hash=digest(folder/"solver.log"), config_file_hash=digest(config_path))
    assert all(digest(p) == h for p,h in {**source_hashes, **case["input_hashes"]}.items())
    write(record_path, record)
    print(json.dumps(dict(case=case["case_id"], variant=variant, seconds=round(elapsed,2),
                         legal=legal, score=record["score"], proven=proven, error=error)), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--screen", type=int, default=0)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--entrypoint",choices=("src.cg_acceleration","src.cg_acceleration.fast"),default="src.cg_acceleration")
    args = parser.parse_args()
    if args.budget <= 0 or args.workers < 1 or args.screen == 1 or not 0 <= args.screen <= 112:
        parser.error("positive budget/workers required; screen must be 0 or 2..112")
    output = args.output.resolve()
    # A trivial solve does not detect Gurobi's size-limited default license.
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam("OutputFlag", 0)
        env.start()
        with gp.Model(env=env) as probe:
            probe.addVars(3001)
            probe.optimize()
            assert probe.Status == gp.GRB.OPTIMAL
    cases, audit = cases_and_audit()
    sources = list((ROOT/"src").rglob("*.py"))+list((ROOT/"archive").rglob("*.py"))+[
        ROOT/"scripts"/name for name in ("run_cg_acceleration_study.py","benchmark_methods.py","benchmark_v2_exact.py")]
    hashes = {str(p.resolve()): digest(p) for p in sources}
    structure=dict(cases=audit,source_hashes=hashes)
    if args.audit_only:
        freeze_plan(output,structure,None,sources)
        return
    if args.screen:
        # Uniform structural quantiles, chosen before observing candidate scores.
        features = {r["case_id"]:r for r in audit}
        ordered = sorted(cases, key=lambda c:(features[c["case_id"]]["max_group_domain_sum"], c["case_id"]))
        cases = [ordered[round(i*(len(ordered)-1)/(args.screen-1))] for i in range(args.screen)]
    plan=dict(case_ids=[c["case_id"] for c in cases], budget=args.budget,entrypoint=args.entrypoint,
        workers=args.workers, variants=["baseline", "hybrid"], source_hashes=hashes,
        order="paired per case; SHA256 parity alternates first variant")
    freeze_plan(output,structure,plan,sources)

    def pair(case):
        variants = ["baseline", "hybrid"]
        if int(hashlib.sha256(case["case_id"].encode()).hexdigest()[-1],16)%2:
            variants.reverse()
        return [run(case, v, args.budget, output, hashes,args.entrypoint) for v in variants]

    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(pair,c) for c in cases]
        for future in as_completed(futures):
            records.extend(future.result())
            write(output/"benchmark.json", dict(complete=False, records=records))
    assert {(r["contract"]["case_id"],r["contract"]["variant"]) for r in records} == {
        (c["case_id"],v) for c in cases for v in ("baseline","hybrid")}
    write(output/"benchmark.json", dict(complete=True, records=records))


if __name__ == "__main__":
    main()
