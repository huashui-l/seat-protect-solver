#!/usr/bin/env python3
"""Serial V2 rebenchmark of unchanged historical C++ sources on this machine."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_smart import cases, load
from scripts.build_smart_report import model_signature
from src import allocation_evaluator as ev


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--filter", default="")
    parser.add_argument("--seconds", type=float, default=5)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output must be empty; preserve every complete batch")
    args.output.mkdir(parents=True, exist_ok=True)
    build = load(args.build / "build.json")
    binary = Path(build["binary"])
    if digest(binary) != build["binary_sha256"]:
        raise ValueError("C++ executable hash mismatch")
    original = ROOT / "docs/provenance/smart-heuristic/historical_cpp_config.json"
    cfg = load(original)
    selected = [c for c in cases(["v2"]) if not args.filter or args.filter in c[0]]
    # Keep all search/objective settings. Only adapt the input routing table.
    configs, config_paths, config_hashes = {}, {}, {}
    for cid, ip, op, np, _ in selected:
        direction = load(ip).get("direction", "")
        key = (direction, str(op.resolve()), str(np.resolve()))
        if key not in configs:
            derived = load(original)
            derived["input_contract"]["seatmaps_by_direction"][direction] = {
                "old": key[1],
                "new": key[2],
            }
            path = args.output / f"cpp-config-{len(configs)}.json"
            path.write_text(json.dumps(derived, ensure_ascii=False, indent=2))
            configs[key] = path
            config_hashes[path.name] = digest(path)
        config_paths[cid] = configs[key]
    if model_signature(cfg) != model_signature(load(ROOT / "config.json")):
        raise ValueError("Current and historical mathematical settings differ")
    hashes = dict(build["source_sha256"])
    for relative, expected in hashes.items():
        source = args.build / relative
        if digest(source) != expected:
            raise ValueError(f"Historical source changed: {relative}")
        target = args.output / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for relative in (
        "src/allocation_evaluator.py",
        "scripts/benchmark_historical_cpp.py",
        "scripts/build_historical_cpp.py",
    ):
        source = ROOT / relative
        hashes[relative] = digest(source)
        target = args.output / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    metadata = {
        "platform": platform.platform(),
        "python": sys.version,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "config": cfg,
        "workers": "historical native defaults, peak 5",
        "source_sha256": hashes,
        "versions": {"highs": "1.15.1"},
        "build": build,
        "original_config_sha256": digest(original),
        "effective_configs_sha256": config_hashes,
        "case_config": {cid: path.name for cid, path in config_paths.items()},
        "timing": "native solve_wall_seconds; process_seconds also recorded; initial parse excluded",
    }
    rows = []
    for cid, ip, op, np, groups in selected:
        native_path = args.output / (cid.replace(":", "_") + ".native.json")
        command = [
            str(binary),
            "--input",
            str(ip),
            "--config",
            str(config_paths[cid].resolve()),
            "--output",
            str(native_path.resolve()),
            "--time-limit",
            str(args.seconds),
            "--seed",
            "0",
            "--construction-objective",
            "rich-fast",
        ]
        ns, os = load(np)["seats"], load(op)["seats"]
        start = time.perf_counter()
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=args.seconds + 5,
                check=False,
            )
            process_seconds = time.perf_counter() - start
            if process.returncode not in (0, 4):
                raise RuntimeError(
                    f"Native exit {process.returncode}: {process.stderr[-1000:]}"
                )
            native = load(native_path)
            assignment = {
                (a["groupId"], a["hostnum"]): a["seatId"] for a in native["assignments"]
            }
            if len(assignment) != len(native["assignments"]):
                raise ValueError("Duplicate native passenger assignments")
            violations, unassigned, details = ev.count_hard_constraint_violations(
                ns, groups, assignment, cfg
            )
            score, parts = ev.calculate_soft_score(
                ns, os, groups, assignment, cfg["weights"], cfg
            )
            valid = violations == unassigned == 0
            # Accept only the external original-model evaluation as the score.
            row = {
                "case_id": cid,
                "solver": "historical_cpp_mac",
                "budget": args.seconds,
                "seed": 0,
                "seconds": native["solve_wall_seconds"],
                "process_seconds": process_seconds,
                "passengers": sum(len(g["psrs"]) for g in groups),
                "valid": valid,
                "status": "complete" if valid else "no_solution",
                "violations": violations,
                "unassigned": unassigned,
                "score": score if valid else None,
                "upper_bound": None,
                "proven_optimal": False,
                "native_score_delta": native["native_score"] - score if valid else None,
            }
            result = {
                "status": row["status"],
                "assigned_seats": {f"{g},{p}": s for (g, p), s in assignment.items()},
                "total_score": row["score"],
                "score_detail": parts,
                "history": [],
                "stages": [],
                "hard_violations": violations,
                "unassigned": unassigned,
                "violation_detail": details,
                "native_status": native["status"],
                "native_returncode": process.returncode,
            }
        except Exception as exc:  # noqa: BLE001 -- Preserve failures in the full batch.
            row = {
                "case_id": cid,
                "solver": "historical_cpp_mac",
                "budget": args.seconds,
                "seconds": time.perf_counter() - start,
                "valid": False,
                "status": "error",
                "score": None,
                "error": f"{type(exc).__name__}: {exc}",
                "passengers": sum(len(g["psrs"]) for g in groups),
            }
            result = {"status": "error", "error": row["error"]}
        row.update(
            {
                "input_sha256": digest(ip),
                "old_seatmap_sha256": digest(op),
                "new_seatmap_sha256": digest(np),
            }
        )
        result["external_audit"] = row
        (args.output / (cid.replace(":", "_") + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        rows.append(row)
        (args.output / "summary.json").write_text(
            json.dumps({**metadata, "rows": rows}, ensure_ascii=False, indent=2)
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if digest(binary) != build["binary_sha256"]:
        raise ValueError("C++ executable changed during benchmark")
    for name, expected in config_hashes.items():
        if digest(args.output / name) != expected:
            raise ValueError(f"Routing config changed during benchmark: {name}")
    for relative, expected in hashes.items():
        path = (
            args.build / relative if relative.startswith("native/") else ROOT / relative
        )
        if digest(path) != expected:
            raise ValueError(f"Source changed during benchmark: {relative}")


if __name__ == "__main__":
    main()
