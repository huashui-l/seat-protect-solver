"""Audit native rich-fast on a manifest corpus without requiring reference answers."""
import argparse
import csv
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native.run_native_formal24 import assignment_map, read_json, sha256
from src import allocation_evaluator as evaluator


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    manifest = read_json(args.manifest)
    config = read_json(args.config)
    seatmaps = {item["layout"]: ROOT / item["relative_path"] for item in manifest["seatmaps"]}
    for item in manifest["seatmaps"] + manifest["cases"]:
        if sha256(ROOT / item["relative_path"]) != item["sha256"]:
            raise ValueError(f"Manifest hash mismatch: {item['relative_path']}")
    config["input_contract"]["seatmaps_by_direction"] = {
        case["direction"]: {"old": str(seatmaps[case["old_layout"]]),
                            "new": str(seatmaps[case["new_layout"]])}
        for case in manifest["cases"]
    }
    config_path = args.output / "config.json"
    write_json(config_path, config)
    rows = []
    for case in manifest["cases"]:
        raw = read_json(ROOT / case["relative_path"])
        # D4 omits the native routing field; add metadata only, preserving groups.
        raw["direction"] = case["direction"]
        name = case["case_id"].replace(":", "_")
        input_path = args.output / "inputs" / (name + ".json")
        output_path = args.output / "allocations" / (name + ".json")
        write_json(input_path, raw)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(args.executable.resolve()), "--input", str(input_path),
               "--config", str(config_path), "--output", str(output_path),
               "--time-limit", "5", "--seed", "0", "--construction-objective", "rich-fast"]
        started = time.perf_counter()
        row = dict(case_id=case["case_id"], input_sha256=case["sha256"],
                   runtime_input_sha256=sha256(input_path), reference_type="unavailable",
                   reference_value=None, gap_percent=None, complete=False)
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=5)
            row.update(process_wall_seconds=time.perf_counter()-started, returncode=proc.returncode)
            row["time_protocol_pass"] = row["process_wall_seconds"] < 5
            if proc.returncode != 0:
                row["error"] = proc.stderr
            else:
                result = read_json(output_path)
                assignments = assignment_map(result)
                old = read_json(seatmaps[case["old_layout"]])["seats"]
                new = read_json(seatmaps[case["new_layout"]])["seats"]
                hard, missing, violations = evaluator.count_hard_constraint_violations(
                    new, raw["groups"], assignments, config)
                score, _ = evaluator.calculate_soft_score(new, old, raw["groups"], assignments,
                                                         config["weights"], config)
                row.update(complete=result["complete"], status=result["status"], unassigned=missing,
                           native_hard_violations=result["native_hard_violations"],
                           external_hard_violations=hard, external_score=score,
                           native_score=result["native_score"],
                           score_error=abs(score-result["native_score"]),
                           native_wall_seconds=result["wall_seconds"], violation_detail=violations)
                row["evaluator_consistent"] = row["score_error"] <= 1e-7 and hard == result["native_hard_violations"]
        except subprocess.TimeoutExpired:
            row.update(process_wall_seconds=time.perf_counter()-started, time_protocol_pass=False,
                       error="External 5-second timeout; process killed and reaped")
        rows.append(row)
        write_json(args.output / "progress.json", rows)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    summary = {
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "binary_sha256": sha256(args.executable), "executable": str(args.executable.resolve()),
        "source_config_sha256": sha256(args.config), "runtime_config_sha256": sha256(config_path),
        "manifest_sha256": sha256(args.manifest), "platform": platform.platform(),
        "cpu": platform.processor(), "seed": 0, "threads": 1, "time_limit_seconds": 5,
        "construction_objective": "rich-fast", "production_python_callback_count": 0,
        "reference_status": "unavailable; no reference scores used during native solve",
        "case_count": len(rows), "complete_count": sum(r["complete"] for r in rows),
        "valid_complete_count": sum(r["complete"] and r.get("unassigned") == 0 and
                                    r.get("external_hard_violations") == 0 and
                                    r.get("native_hard_violations") == 0 for r in rows),
        "evaluator_consistent_count": sum(r.get("evaluator_consistent", False) for r in rows),
        "time_protocol_pass_count": sum(r["time_protocol_pass"] for r in rows),
        "mean_process_wall_seconds": statistics.mean(r["process_wall_seconds"] for r in rows),
        "max_process_wall_seconds": max(r["process_wall_seconds"] for r in rows), "cases": rows,
    }
    write_json(args.output / "summary.json", summary)
    fields = [key for key in rows[0] if key != "violation_detail"]
    with (args.output / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
