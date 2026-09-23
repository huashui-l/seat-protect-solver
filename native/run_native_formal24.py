#!/usr/bin/env python3
"""Run one externally audited native construction-objective Formal24 benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import allocation_evaluator as evaluator


COMPONENTS = ("score_s", "score_v", "score_p", "score_c", "score_b")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assignment_map(result: dict) -> dict[tuple[int, int], str]:
    if "assigned_seats" in result:
        return {
            tuple(map(int, key.split(","))): seat
            for key, seat in result["assigned_seats"].items()
        }
    return {
        (int(item["groupId"]), int(item["hostnum"])): item["seatId"]
        for item in result["assignments"]
    }


def mean_components(details: list[dict]) -> dict[str, float]:
    return {
        key: statistics.mean(item[key] for item in details)
        for key in (*COMPONENTS, "total_soft_score")
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--reference", type=Path, required=True,
        help="frozen artifact root containing rich_python_formal24.csv and outputs/research references",
    )
    parser.add_argument(
        "--baseline", type=Path,
        help="baseline benchmark output directory; defaults to frozen feasibility",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--construction-objective",
        choices=("feasibility", "individual-soft", "group-soft", "group-first"),
        required=True,
    )
    args = parser.parse_args()

    reference_csv = args.reference / "rich_python_formal24.csv"
    rich_raw_path = (
        args.reference / "outputs/research/full_cpp_rich_reference/rich_python_raw.json"
    )
    parser_baseline = getattr(args, "baseline", None)
    baseline_summary_path = (
        parser_baseline / "summary.json" if parser_baseline else
        args.reference / "outputs/research/native_feasibility_formal24/summary.json"
    )
    baseline_allocation_dir = baseline_summary_path.parent / "allocations"
    for path in (
        args.corpus, reference_csv, rich_raw_path, baseline_summary_path,
        args.config, args.executable,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    args.output.mkdir(parents=True, exist_ok=True)
    allocation_dir = args.output / "allocations"
    allocation_dir.mkdir(exist_ok=True)
    config = read_json(args.config)
    config_root = args.config.resolve().parent
    references = {
        row["case_id"]: float(row["current_reference_F_I"])
        for row in csv.DictReader(reference_csv.open(encoding="utf-8-sig"))
    }
    rich_cases = {
        f"{row['direction']}:{row['case']}": row
        for row in read_json(rich_raw_path)["cases"]
    }
    baseline_summary = read_json(baseline_summary_path)
    baseline_cases = {
        row["case_id"]: row for row in baseline_summary["cases"]
    }

    rows = []
    candidate_details = []
    baseline_details = []
    rich_details = []
    started_at = datetime.now(timezone.utc).isoformat()
    case_paths = sorted(args.corpus.glob("*/*_groups.json"))
    for case_path in case_paths:
        raw = read_json(case_path)
        case_id = raw["caseId"]
        direction = raw["direction"]
        output_path = allocation_dir / f"{case_id.replace(':', '_')}.json"
        started = time.perf_counter()
        completed = subprocess.run([
            str(args.executable), "--input", str(case_path),
            "--config", str(args.config), "--output", str(output_path),
            "--time-limit", str(args.time_limit), "--seed", str(args.seed),
            "--construction-objective", args.construction_objective,
        ], cwd=ROOT, capture_output=True, text=True)
        process_wall = time.perf_counter() - started
        if not output_path.exists():
            raise RuntimeError(f"{case_id}: native process failed: {completed.stderr}")
        result = read_json(output_path)
        mapping = config["input_contract"]["seatmaps_by_direction"][direction]
        new_seats = read_json(config_root / mapping["new"])["seats"]
        old_seats = read_json(config_root / mapping["old"])["seats"]
        assignments = assignment_map(result)
        violations, unassigned, violation_detail = (
            evaluator.count_hard_constraint_violations(
                new_seats, raw["groups"], assignments, config
            )
        )
        external_score, detail = evaluator.calculate_soft_score(
            new_seats, old_seats, raw["groups"], assignments,
            config["weights"], config,
        )
        baseline = baseline_cases[case_id]
        baseline_result = read_json(
            baseline_allocation_dir / f"{case_id.replace(':', '_')}.json"
        )
        _, baseline_detail = evaluator.calculate_soft_score(
            new_seats, old_seats, raw["groups"], assignment_map(baseline_result),
            config["weights"], config,
        )
        rich_detail = rich_cases[case_id]["soft_score_detail"]
        candidate_details.append(detail)
        baseline_details.append(baseline_detail)
        rich_details.append(rich_detail)
        reference = references[case_id]
        row = {
            "case_id": case_id,
            "returncode": completed.returncode,
            "status": result["status"],
            "complete": bool(result["complete"]),
            "unassigned": unassigned,
            "native_hard_violations": int(result["native_hard_violations"]),
            "external_hard_violations": violations,
            "external_score": external_score,
            "native_score": float(result["native_score"]),
            "score_error": abs(float(result["native_score"]) - external_score),
            "individual_score": float(result["individual_score"]),
            "individual_score_error": abs(
                float(result["individual_score"])
                - sum(detail[key] for key in ("score_s", "score_v", "score_p"))
            ),
            "gap_I": (reference - external_score) / max(1.0, abs(reference)),
            "baseline_score": float(baseline["external_score"]),
            "delta_vs_baseline": external_score - float(baseline["external_score"]),
            "selected_incumbent": result.get("selected_incumbent", ""),
            "fallback_reason": result.get("fallback_reason", ""),
            "dfs_nodes": int(result.get("dfs_nodes", 0)),
            "beam_nodes": int(result.get("beam_nodes", 0)),
            "dfs_groups": int(result.get("dfs_groups", 0)),
            "beam_groups": int(result.get("beam_groups", 0)),
            "groups_improved": int(result.get("groups_improved", 0)),
            "q1_selected_incumbent": result.get("q1_selected_incumbent", ""),
            "q1_score": float(result.get("q1_score", 0.0)),
            "from_scratch_score": float(result.get("from_scratch_score", 0.0)),
            "from_scratch_complete": bool(result.get("from_scratch_complete", False)),
            "from_scratch_dfs_nodes": int(result.get("from_scratch_dfs_nodes", 0)),
            "from_scratch_beam_nodes": int(result.get("from_scratch_beam_nodes", 0)),
            "from_scratch_dfs_groups": int(result.get("from_scratch_dfs_groups", 0)),
            "from_scratch_beam_groups": int(result.get("from_scratch_beam_groups", 0)),
            "recovery_attempts": int(result.get("recovery_attempts", 0)),
            "recovery_succeeded": int(result.get("recovery_succeeded", 0)),
            "score_detail": {key: detail[key] for key in (*COMPONENTS, "total_soft_score")},
            "native_wall_seconds": float(result["wall_seconds"]),
            "process_wall_seconds": process_wall,
            "input_sha256": sha256(case_path),
            "violation_detail": violation_detail,
        }
        rows.append(row)
        print(
            f"{case_id} status={row['status']} hard={violations} "
            f"delta={row['delta_vs_baseline']:.6f} wall={process_wall:.3f}s",
            flush=True,
        )

    valid = [
        row for row in rows
        if row["complete"] and row["unassigned"] == 0
        and row["native_hard_violations"] == 0
        and row["external_hard_violations"] == 0
    ]
    tolerance = 1e-8
    candidate_means = mean_components(candidate_details)
    baseline_means = mean_components(baseline_details)
    rich_means = mean_components(rich_details)
    deltas = [row["delta_vs_baseline"] for row in rows]
    summary = {
        "schema": "native_construction_formal24_v2",
        "mode": "RAW_NATIVE",
        "construction_objective": args.construction_objective,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "corpus": str(args.corpus.resolve()),
            "reference": str(args.reference.resolve()),
            "config": str(args.config.resolve()),
            "config_sha256": sha256(args.config),
            "binary": str(args.executable.resolve()),
            "binary_sha256": sha256(args.executable),
            "git_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "seed": args.seed,
            "time_limit_seconds": args.time_limit,
            "production_python_callback_count": 0,
        },
        "case_count": len(rows),
        "valid_complete_count": len(valid),
        "unassigned_total": sum(row["unassigned"] for row in rows),
        "native_hard_violations_total": sum(row["native_hard_violations"] for row in rows),
        "external_hard_violations_total": sum(row["external_hard_violations"] for row in rows),
        "evaluator_consistent_count": sum(
            row["score_error"] <= tolerance and row["individual_score_error"] <= tolerance
            for row in rows
        ),
        "max_score_error": max(row["score_error"] for row in rows),
        "max_individual_score_error": max(row["individual_score_error"] for row in rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "selected_incumbent_counts": dict(Counter(
            row["selected_incumbent"] for row in rows
        )),
        "construction_statistics": {
            "dfs_nodes": sum(row["dfs_nodes"] for row in rows),
            "beam_nodes": sum(row["beam_nodes"] for row in rows),
            "dfs_groups": sum(row["dfs_groups"] for row in rows),
            "beam_groups": sum(row["beam_groups"] for row in rows),
            "groups_improved": sum(row["groups_improved"] for row in rows),
            "from_scratch_complete": sum(row["from_scratch_complete"] for row in rows),
            "from_scratch_dfs_nodes": sum(row["from_scratch_dfs_nodes"] for row in rows),
            "from_scratch_beam_nodes": sum(row["from_scratch_beam_nodes"] for row in rows),
            "from_scratch_dfs_groups": sum(row["from_scratch_dfs_groups"] for row in rows),
            "from_scratch_beam_groups": sum(row["from_scratch_beam_groups"] for row in rows),
            "recovery_attempts": sum(row["recovery_attempts"] for row in rows),
            "recovery_succeeded": sum(row["recovery_succeeded"] for row in rows),
            "fallback_reasons": dict(Counter(
                row["fallback_reason"] for row in rows if row["fallback_reason"]
            )),
        },
        "timing": {
            "mean_process_wall_seconds": statistics.mean(row["process_wall_seconds"] for row in rows),
            "median_process_wall_seconds": statistics.median(row["process_wall_seconds"] for row in rows),
            "max_process_wall_seconds": max(row["process_wall_seconds"] for row in rows),
        },
        "quality": {
            "mean_gap_I": statistics.mean(row["gap_I"] for row in rows),
            "median_gap_I": statistics.median(row["gap_I"] for row in rows),
            "paired_vs_baseline": {
                "improve": sum(delta > tolerance for delta in deltas),
                "tie": sum(abs(delta) <= tolerance for delta in deltas),
                "regress": sum(delta < -tolerance for delta in deltas),
                "mean_delta": statistics.mean(deltas),
                "median_delta": statistics.median(deltas),
            },
            "component_means": {
                key: {
                    "candidate": candidate_means[key],
                    "baseline": baseline_means[key],
                    "rich": rich_means[key],
                    "candidate_minus_baseline": candidate_means[key] - baseline_means[key],
                    "candidate_minus_rich": candidate_means[key] - rich_means[key],
                }
                for key in candidate_means
            },
        },
        "cases": rows,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}, indent=2))
    if len(valid) != len(rows) or summary["evaluator_consistent_count"] != len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
