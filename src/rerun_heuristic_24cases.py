#!/usr/bin/env python3
"""Re-run a time-limited heuristic on the existing bidirectional cases."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from . import allocation_evaluator as evaluator
    from . import heuristic_seat_allocator as allocator
except ImportError:
    import allocation_evaluator as evaluator
    import heuristic_seat_allocator as allocator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIZES = ("50", "100", "150", "full")
DIFFICULTIES = ("normal", "stress", "edge")
DIRECTIONS = (
    ("forward", "", "data/3-4-3seatmap.json", "data/3-3seatmap.json"),
    (
        "reverse",
        "reverse_3-3_to_3-4-3_",
        "data/3-3seatmap.json",
        "data/3-4-3seatmap.json",
    ),
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(path: str) -> Path:
    return (PROJECT_ROOT / path).resolve()


def override_heuristic_time_limit(
    config: dict[str, Any], business_time_limit: float
) -> None:
    """Scale the configured five-second portfolio to an experiment limit."""
    algorithm = config.setdefault("algorithm", {})
    original_limit = float(
        algorithm.get("business_time_limit_seconds", 5.0)
    )
    algorithm["business_time_limit_seconds"] = business_time_limit
    config.setdefault("evaluation", {})["max_time_seconds"] = (
        business_time_limit + 1.0
    )
    if abs(business_time_limit - original_limit) <= 1e-9:
        return
    reserve = float(algorithm.get("scoring_time_reserve", 0.25))
    safety = float(algorithm.get("deadline_safety_margin_seconds", 0.0))
    scale = (
        max(0.001, business_time_limit - reserve - safety)
        / max(0.001, original_limit - reserve - safety)
    )
    for key in (
        "construction_time_budget", "repair_time_budget",
        "vnd_time_budget", "lns_time_budget",
        "structured_pattern_time_budget",
        "restricted_pattern_mip_time_budget",
        "protected_multigroup_mip_time_budget",
    ):
        if key in algorithm:
            algorithm[key] = float(algorithm[key]) * scale
    algorithm["adaptive_stage_budgets"] = False


def apply_variant(config: dict[str, Any], variant: str) -> None:
    """配置可复现的旧邻域基线或当前自适应邻域。"""
    algorithm = config.setdefault("algorithm", {})
    if variant == "baseline":
        algorithm["enable_three_tier_patterns"] = False
        algorithm["enable_pattern_local_branching"] = False
        algorithm["multigroup_max_component_size"] = 4


def method_metrics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """提取A/B需要的紧凑指标，完整诊断仍原样保留。"""
    decomposition = diagnostics.get("cabin_decomposition", {})
    cabin_runs = decomposition.get("cabins", {})
    if decomposition.get("enabled") and cabin_runs:
        per_cabin = {
            cabin: method_metrics(run.get("diagnostics", {}))
            for cabin, run in cabin_runs.items()
        }
        tiers = {"rigid": 0, "relaxed": 0, "rebuilt": 0}
        stage_seconds: dict[str, float] = {}
        radius_history: list[int] = []
        for metrics in per_cabin.values():
            for tier, count in metrics["three_tier_pattern_counts"].items():
                tiers[tier] = tiers.get(tier, 0) + int(count)
            for stage, seconds in metrics["stage_seconds"].items():
                stage_seconds[stage] = (
                    stage_seconds.get(stage, 0.0) + float(seconds)
                )
            radius_history.extend(metrics["local_branching_radius_history"])
        return {
            "three_tier_pattern_counts": tiers,
            "lns_max_actual_component_size": max(
                (
                    metrics["lns_max_actual_component_size"]
                    for metrics in per_cabin.values()
                ),
                default=0,
            ),
            "lns_components_tested": sum(
                metrics["lns_components_tested"]
                for metrics in per_cabin.values()
            ),
            "local_branching_enabled": any(
                metrics["local_branching_enabled"]
                for metrics in per_cabin.values()
            ),
            "local_branching_radius_history": radius_history,
            "stage_seconds": stage_seconds,
            "per_cabin": per_cabin,
        }
    structured = diagnostics.get("structured_pattern_generation", {})
    lns_runs = [
        diagnostics.get("multigroup_lns", {}),
        diagnostics.get("tail_multigroup_lns", {}),
    ]
    component_sizes = [
        len(component)
        for run in lns_runs
        for component in run.get("tested_group_components", [])
    ]
    restricted = diagnostics.get("restricted_pattern_mip", {})
    local_branching = restricted.get("local_branching", {})
    stage_statistics = diagnostics.get("stage_statistics", {})
    return {
        "three_tier_pattern_counts": structured.get(
            "tier_counts", {"rigid": 0, "relaxed": 0, "rebuilt": 0}
        ),
        "lns_max_actual_component_size": max(
            [
                *(int(run.get("max_tested_component_size", 0)) for run in lns_runs),
                *component_sizes,
            ],
            default=0,
        ),
        "lns_components_tested": sum(
            int(run.get("components_tested", 0)) for run in lns_runs
        ),
        "local_branching_enabled": bool(
            local_branching.get("enabled", False)
        ),
        "local_branching_radius_history": list(
            local_branching.get("radius_history", [])
        ),
        "stage_seconds": {
            name: float(values.get("seconds", 0.0))
            for name, values in stage_statistics.items()
        },
    }


def selected_case(
    selected: set[str], direction: str, case: str,
) -> bool:
    return not selected or case in selected or f"{direction}:{case}" in selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "heuristic_24cases_rerun.json",
    )
    parser.add_argument(
        "--business-time-limit",
        type=float,
        help="覆盖启发式业务时限；阶段预算会由分配器按可用时间同比缩放",
    )
    parser.add_argument(
        "--variant",
        choices=("baseline", "current"),
        default="current",
        help="baseline关闭三级模式和Local Branching并限制最多4组；current使用当前配置",
    )
    parser.add_argument(
        "--cases",
        help="逗号分隔；可写100_edge同时选双向，或forward:100_edge只选一个方向",
    )
    parser.add_argument(
        "--allocation-result-dir",
        type=Path,
        help="可选：逐算例保存可由可视化模块直接读取的完整分配结果",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help=(
            "可选：读取按forward/reverse子目录组织的冻结corpus；"
            "省略时保持历史data根目录协议"
        ),
    )
    args = parser.parse_args()

    config = read_json(args.config.resolve())
    if args.business_time_limit is not None:
        if args.business_time_limit <= 0:
            raise ValueError("--business-time-limit必须为正数")
        override_heuristic_time_limit(
            config, float(args.business_time_limit)
        )
    apply_variant(config, args.variant)
    selected = {
        item.strip() for item in (args.cases or "").split(",")
        if item.strip()
    }
    rows: list[dict[str, Any]] = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    total = sum(
        selected_case(selected, direction, f"{size}_{difficulty}")
        for direction, *_ in DIRECTIONS
        for size in SIZES
        for difficulty in DIFFICULTIES
    )
    index = 0

    for direction, prefix, old_name, new_name in DIRECTIONS:
        old_seats = read_json(resolve(old_name))["seats"]
        new_seats = read_json(resolve(new_name))["seats"]
        for size in SIZES:
            for difficulty in DIFFICULTIES:
                case = f"{size}_{difficulty}"
                if not selected_case(selected, direction, case):
                    continue
                index += 1
                if args.data_dir is not None:
                    data_path = (
                        args.data_dir.resolve() / direction
                        / f"{case}_groups.json"
                    )
                else:
                    data_path = (
                        PROJECT_ROOT / "data" / f"{prefix}{case}_groups.json"
                    )
                data = read_json(data_path)
                groups = data["groups"]
                started = time.perf_counter()
                try:
                    result, allocator_score = allocator.run_allocation(
                        new_seats,
                        old_seats,
                        groups,
                        config.get("weights", {}),
                        config,
                    )
                    elapsed = time.perf_counter() - started
                    assignments = result.get("assigned_seats", {})
                    violations, unassigned, violation_detail = (
                        evaluator.count_hard_constraint_violations(
                            new_seats, groups, assignments, config
                        )
                    )
                    soft_score, soft_detail = evaluator.calculate_soft_score(
                        new_seats,
                        old_seats,
                        groups,
                        assignments,
                        config.get("weights", {}),
                        config,
                    )
                    internal_soft = float(
                        result.get("score_detail", {}).get(
                            "total_soft_score", soft_score
                        )
                    )
                    row = {
                        "direction": direction,
                        "case": case,
                        "data_file": str(data_path.relative_to(PROJECT_ROOT)),
                        "traveler_count": int(data.get("travelerCount", len(assignments))),
                        "assigned_count": len(assignments),
                        "hard_constraint_violations": violations,
                        "unassigned_count": unassigned,
                        "heuristic_score": soft_score,
                        "allocator_combined_score": allocator_score,
                        "internal_soft_score": internal_soft,
                        "soft_score_audit_error": soft_score - internal_soft,
                        "elapsed_seconds": elapsed,
                        "valid_complete": violations == 0 and unassigned == 0,
                        "violation_detail": violation_detail,
                        "soft_score_detail": soft_detail,
                        "diagnostics": result.get("diagnostics", {}),
                        "method_metrics": method_metrics(
                            result.get("diagnostics", {})
                        ),
                        "status": "completed",
                    }
                    if args.allocation_result_dir is not None:
                        result_dir = args.allocation_result_dir.resolve()
                        result_dir.mkdir(parents=True, exist_ok=True)
                        snapshot = {
                            "schema": "seat_allocation_result_v1",
                            "direction": direction,
                            "case": case,
                            "data": {
                                "oldseatmap": old_name,
                                "seatmap": new_name,
                                "groups": str(
                                    data_path.relative_to(PROJECT_ROOT)
                                ),
                            },
                            "assigned_seats": {
                                f"{group_id},{hostnum}": seat_id
                                for (group_id, hostnum), seat_id
                                in assignments.items()
                            },
                            "blocked_by": result.get("blocked_by", {}),
                            "total_score": soft_score,
                            "score_detail": result.get(
                                "score_detail", soft_detail
                            ),
                            "diagnostics": result.get("diagnostics", {}),
                        }
                        snapshot_path = (
                            result_dir / f"{direction}_{case}.json"
                        )
                        snapshot_path.write_text(
                            json.dumps(
                                snapshot, ensure_ascii=False, indent=2
                            ),
                            encoding="utf-8",
                        )
                except Exception as exc:  # retain a complete batch audit
                    elapsed = time.perf_counter() - started
                    row = {
                        "direction": direction,
                        "case": case,
                        "data_file": str(data_path.relative_to(PROJECT_ROOT)),
                        "elapsed_seconds": elapsed,
                        "valid_complete": False,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                rows.append(row)
                print(
                    f"[{index:02d}/{total}] {direction} {case}: "
                    f"status={row['status']}, H={row.get('heuristic_score')}, "
                    f"assigned={row.get('assigned_count')}, "
                    f"unassigned={row.get('unassigned_count')}, "
                    f"violations={row.get('hard_constraint_violations')}, "
                    f"seconds={elapsed:.3f}",
                    flush=True,
                )

    report = {
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "current time-limited heuristic; existing input data; independent hard-constraint and soft-score audit",
        "variant": args.variant,
        "business_time_limit_seconds": config.get("algorithm", {}).get(
            "business_time_limit_seconds", 5.0
        ),
        "case_count": len(rows),
        "completed_count": sum(row["status"] == "completed" for row in rows),
        "valid_complete_count": sum(row["valid_complete"] for row in rows),
        "cases": rows,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Results: {output}", flush=True)


if __name__ == "__main__":
    main()
