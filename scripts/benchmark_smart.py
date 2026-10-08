#!/usr/bin/env python3
"""Reproducible serial benchmark. Solvers never see generator witnesses/references."""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import allocation_evaluator as ev
from src.smart_seat_allocator import solve


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def cases(suites):
    roots = {
        "formal24": "d3_official_24cases_2026-09-07",
        "d4": "d4_smooth_multi_aircraft_2026-09-25",
        "v2": "benchmark_v2_2026-10-07",
    }
    for suite in suites:
        if suite == "public":
            from tests.general_validation_case_factory import materialize_cases

            dest = ROOT / "outputs/smart_public_inputs"
            dest.mkdir(parents=True, exist_ok=True)
            for case in materialize_cases():
                if not case["expectedValid"]:
                    continue
                stem = dest / case["id"]
                ip, op, np = [
                    Path(str(stem) + suffix)
                    for suffix in ("_groups.json", "_old.json", "_new.json")
                ]
                for path, value in [
                    (ip, {"groups": case["groupsData"]}),
                    (op, case["oldSeatmapData"]),
                    (np, case["newSeatmapData"]),
                ]:
                    path.write_text(
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                    )
                yield "public:" + case["id"], ip, op, np, case["groupsData"]
            continue
        root = ROOT / "data/generator_v3_2" / roots[suite]
        for entry in load(root / "manifest.json")["cases"]:
            path = ROOT / entry["relative_path"]
            data = load(path)
            if suite == "formal24":
                old, new = (
                    ("3-4-3", "3-3")
                    if entry["direction"] == "forward"
                    else ("3-3", "3-4-3")
                )
                maps = ROOT / "data"
            else:
                old = data.get("oldLayout", entry.get("old_layout"))
                new = data.get("newLayout", entry.get("new_layout"))
                maps = root / "seatmaps"
            yield (
                entry["case_id"],
                path,
                maps / f"{old}seatmap.json",
                maps / f"{new}seatmap.json",
                data["groups"],
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--suites",
        nargs="+",
        default=["v2"],
        help="Default: V2 (72 cases). Use formal24 d4 v2 for all 112 cases.",
    )
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--filter", default="")
    ap.add_argument("--case-ids", nargs="+", help="Optional exact case IDs to run")
    ap.add_argument("--solver", choices=["smart", "legacy"], default="smart")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        ap.error("--output must be empty; preserve each benchmark as a separate batch")
    args.output.mkdir(parents=True, exist_ok=True)
    cfg = load(ROOT / "config.json")
    source_files = [
        *sorted((ROOT / "src/smart_seat_allocator").glob("*.py")),
        ROOT / "src/seat_model.py",
        ROOT / "src/allocation_evaluator.py",
        ROOT / "src/heuristic_seat_allocator.py",
    ]
    if args.solver == "legacy":
        # The legacy search imports this archived pricing implementation.
        source_files.extend(
            sorted((ROOT / "archive/legacy_column_generation").glob("*.py"))
        )
    source_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_files
    }
    snapshot = args.output / "source_snapshot"
    for relative in source_hashes:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    versions = {}
    for package in ("numpy", "scipy", "ortools", "highspy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    if args.solver == "legacy":
        from src.heuristic_seat_allocator import run_allocation

    rows = []
    for cid, ip, op, np, groups in cases(args.suites):
        if args.filter and args.filter not in cid:
            continue
        if args.case_ids and cid not in args.case_ids:
            continue
        ns, os = load(np)["seats"], load(op)["seats"]
        input_hashes = {
            "input_sha256": hashlib.sha256(ip.read_bytes()).hexdigest(),
            "old_seatmap_sha256": hashlib.sha256(op.read_bytes()).hexdigest(),
            "new_seatmap_sha256": hashlib.sha256(np.read_bytes()).hexdigest(),
        }
        start = time.perf_counter()
        try:
            if args.solver == "smart":
                r = solve(
                    ns,
                    os,
                    groups,
                    cfg["weights"],
                    cfg,
                    time_limit=args.seconds,
                    workers=args.workers,
                    seed=args.seed,
                )
            else:
                cfg["algorithm"]["business_time_limit_seconds"] = args.seconds
                r, _ = run_allocation(ns, os, groups, cfg["weights"], cfg)
            elapsed = time.perf_counter() - start
            a = r.get("assigned_seats", {})
            v, u, _details = ev.count_hard_constraint_violations(ns, groups, a, cfg)
            score, _parts = ev.calculate_soft_score(
                ns, os, groups, a, cfg["weights"], cfg
            )
            row = {
                "case_id": cid,
                "solver": args.solver,
                "budget": args.seconds,
                "seed": args.seed if args.solver == "smart" else None,
                "seconds": elapsed,
                "passengers": sum(len(g["psrs"]) for g in groups),
                "valid": v == u == 0,
                "status": r.get("status", "complete" if v == u == 0 else "no_solution"),
                "violations": v,
                "unassigned": u,
                "score": score if v == u == 0 else None,
                "upper_bound": r.get("upper_bound"),
                "proven_optimal": r.get("proven_optimal", False),
                "input_sha256": hashlib.sha256(ip.read_bytes()).hexdigest(),
                "old_seatmap_sha256": hashlib.sha256(op.read_bytes()).hexdigest(),
                "new_seatmap_sha256": hashlib.sha256(np.read_bytes()).hexdigest(),
            }
            serial = {
                **r,
                "status": row["status"],
                "assigned_seats": {f"{g},{p}": s for (g, p), s in a.items()},
                "external_audit": row,
            }
        except Exception as exc:  # noqa: BLE001 -- Record failures and continue the benchmark.
            row = {
                "case_id": cid,
                "solver": args.solver,
                "budget": args.seconds,
                "seconds": time.perf_counter() - start,
                "valid": False,
                "error": f"{type(exc).__name__}: {exc}",
                "passengers": sum(len(g["psrs"]) for g in groups),
                "status": "error",
                "score": None,
                **input_hashes,
            }
            serial = row
        (args.output / f"{cid.replace(':', '_')}.json").write_text(
            json.dumps(serial, ensure_ascii=False, indent=2, default=str)
        )
        rows.append(row)
        (args.output / "summary.json").write_text(
            json.dumps(
                {
                    "platform": platform.platform(),
                    "started_at": started_at,
                    "python": sys.version,
                    "config": cfg,
                    "workers": args.workers
                    if args.solver == "smart"
                    else "legacy defaults",
                    "source_sha256": source_hashes,
                    "versions": versions,
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
    for relative, expected in source_hashes.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Source changed during benchmark: {relative}")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with (args.output / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
