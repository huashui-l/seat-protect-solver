#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在12组固定数据上比较限时启发式、快速整数解与根LP安全上界。"""

from __future__ import annotations

import argparse
import copy
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from . import batch_benchmark
    from . import exact_column_generation
    from . import passenger_data_generator
except ImportError:
    import batch_benchmark
    import exact_column_generation
    import passenger_data_generator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIFFICULTIES = ("normal", "stress", "edge")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve_project_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _case_sizes(capacity: int) -> List[tuple[str, int]]:
    return [("50", 50), ("100", 100), ("150", 150), ("full", capacity)]


def _load_case(path: Path) -> tuple[List[dict], Dict[str, Any]]:
    """读取既有批量数据，并兼容仅保存groups数组的旧格式。"""
    raw = _read_json(path)
    if isinstance(raw, list):
        return raw, {}
    groups = raw.get("groups")
    if not isinstance(groups, list):
        raise ValueError(f"数据文件缺少groups数组: {path}")
    return groups, raw


def _absolute_gap(upper: Any, lower: Any) -> float | None:
    if upper is None or lower is None:
        return None
    return max(0.0, float(upper) - float(lower))


def _relative_percent(gap: float | None, reference: Any) -> float | None:
    if gap is None or reference is None:
        return None
    return 100.0 * gap / max(1.0, abs(float(reference)))


def _effective_upper_bound(
    diagnostics: Dict[str, Any],
) -> tuple[float | None, str | None]:
    """取当前结果中最紧的严格有效完整列空间上界。"""
    candidates: List[tuple[float, str]] = []
    if diagnostics.get("complete_column_space_lp_bound_certified", False):
        value = diagnostics.get("complete_column_space_lp_bound")
        if value is not None:
            candidates.append((float(value), "exact_complete_column_space_lp"))
    if diagnostics.get("safe_column_space_lp_bound_certified", False):
        value = diagnostics.get("safe_column_space_lp_bound")
        if value is not None:
            candidates.append((float(value), "safe_reduced_cost_corrected_lp"))
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def _requested_cases(
    capacity: int, selected: str | None,
) -> List[tuple[str, int, str]]:
    all_cases = [
        (f"{size_label}_{difficulty}", requested, difficulty)
        for size_label, requested in _case_sizes(capacity)
        for difficulty in DIFFICULTIES
    ]
    if not selected:
        return all_cases
    wanted = {item.strip() for item in selected.split(",") if item.strip()}
    known = {item[0] for item in all_cases}
    unknown = sorted(wanted - known)
    if unknown:
        raise ValueError(f"未知算例: {', '.join(unknown)}")
    return [item for item in all_cases if item[0] in wanted]


def _update_upper_bound_counts(report: Dict[str, Any]) -> None:
    """按互斥口径统计精确U、安全修正U和无U。"""
    completed = [
        row for row in report.get("cases", [])
        if row.get("status") == "completed"
    ]
    exact = sum(bool(row.get("lp_bound_certified")) for row in completed)
    safe_only = sum(
        not bool(row.get("lp_bound_certified"))
        and bool(row.get("safe_lp_bound_certified"))
        for row in completed
    )
    report["upper_bound_counts"] = {
        "exact_complete_column_space_lp": exact,
        "safe_reduced_cost_corrected_only": safe_only,
        "no_certified_upper_bound": len(completed) - exact - safe_only,
        "completed_cases": len(completed),
    }


def _override_heuristic_time_limit(
    config: Dict[str, Any], business_time_limit: float
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "在既有50/100/150/full × normal/stress/edge数据上运行"
            "限时启发式、列池整数MIP和根节点完整列空间LP上界"
        )
    )
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config.json"
    )
    parser.add_argument(
        "--old-seatmap",
        type=Path,
        help="覆盖config.data.oldseatmap；相对路径按项目根目录解析",
    )
    parser.add_argument(
        "--new-seatmap",
        type=Path,
        help="覆盖config.data.seatmap；相对路径按项目根目录解析",
    )
    parser.add_argument(
        "--data-prefix",
        default="",
        help="输入数据文件名前缀，用于隔离不同机型方向，例如reverse_",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "outputs"
        / "lp_heuristic_12cases_bound_portfolio.json",
    )
    parser.add_argument(
        "--details-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "lp_heuristic_12cases_details",
        help="保存每个算例完整列生成结果的目录",
    )
    parser.add_argument(
        "--cases",
        help=(
            "只运行指定算例，逗号分隔，例如50_normal,100_stress；"
            "省略时运行全部12组"
        ),
    )
    parser.add_argument(
        "--regenerate-data",
        action="store_true",
        help="重新生成并覆盖data下的12组输入；默认严格复用既有数据",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="只生成/检查12组输入，不运行启发式和列生成",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="忽略已有汇总并从头运行；默认跳过汇总中status=completed的算例",
    )
    parser.add_argument(
        "--rerun-completed",
        action="store_true",
        help="重新运行--cases指定的已完成算例，并在汇总中替换对应记录",
    )
    parser.add_argument(
        "--total-time-limit",
        type=float,
        help="每个实例的列生成总时限（秒）",
    )
    parser.add_argument(
        "--mp2-time-limit",
        type=float,
        help="每次精确定价 MP2 的时限（秒）",
    )
    parser.add_argument(
        "--pricing-workers",
        type=int,
        help="并行定价组数；每个 HiGHS 实例仍为单线程",
    )
    parser.add_argument(
        "--restricted-mip-time-limit",
        type=float,
        default=30.0,
        help=(
            "根LP完成后，在已生成组模式列上改善整数可行解的时限；"
            "该步骤不进行MP1分支，也不用于最优性证明"
        ),
    )
    parser.add_argument(
        "--heuristic-business-time-limit",
        type=float,
        default=20.0,
        help="列生成warm start使用的启发式业务时限，默认20秒",
    )
    parser.add_argument(
        "--dual-stabilization-mode",
        choices=("adaptive", "fixed"),
        default="adaptive",
        help="候选定价使用自适应或固定强度的对偶中心平滑",
    )
    parser.add_argument(
        "--disable-transported-pricing-bounds",
        action="store_true",
        help="关闭跨RMP对偶的严格定价下界复用，用于相同时间A/B",
    )
    args = parser.parse_args()

    config_path = args.config.resolve()
    base_config = _read_json(config_path)
    data_config = base_config["data"]
    if not re.fullmatch(r"[A-Za-z0-9_-]*", args.data_prefix):
        raise ValueError("--data-prefix只能包含字母、数字、下划线和连字符")
    old_path = _resolve_project_path(
        args.old_seatmap or Path(data_config["oldseatmap"])
    )
    new_path = _resolve_project_path(
        args.new_seatmap or Path(data_config["seatmap"])
    )
    old_seats = _read_json(old_path)["seats"]
    new_seats = _read_json(new_path)["seats"]
    old_topology = passenger_data_generator.SeatTopology(old_seats)
    new_topology = passenger_data_generator.SeatTopology(new_seats)
    capacity = passenger_data_generator.transferable_seat_capacity(
        old_topology, new_topology
    )
    experiment = {
        "old_seatmap": _display_path(old_path),
        "new_seatmap": _display_path(new_path),
        "data_prefix": args.data_prefix,
        "dual_stabilization_mode": args.dual_stabilization_mode,
        "transported_pricing_bounds_enabled": (
            not args.disable_transported_pricing_bounds
        ),
    }

    output_path = args.output.resolve()
    details_dir = args.details_dir.resolve()
    if output_path.exists() and not args.restart:
        report = _read_json(output_path)
        report.setdefault("cases", [])
        previous_experiment = report.get("experiment")
        if previous_experiment is not None and previous_experiment != experiment:
            raise ValueError(
                "已有输出属于不同实验方向，请更换--output或使用--restart"
            )
        report["experiment"] = experiment
        report["resumed_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
    else:
        report = {
            "started_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "config": str(config_path),
            "seat_capacity": capacity,
            "experiment": experiment,
            "scope": (
                "root LP exact/safe upper bound plus restricted-column primal "
                "MIP; no MP1 integer optimality proof"
            ),
            "score_time_penalty": 0.0,
            "gap_definition": {
                "certified_heuristic_gap": "U-H",
                "integrality_gap": "U-I",
                "heuristic_to_incumbent_gap": "I-H",
            },
            "cases": [],
        }
    _write_json(output_path, report)

    cases = _requested_cases(capacity, args.cases)
    completed_names = {
        str(row.get("case"))
        for row in report["cases"]
        if str(row.get("status", "")).startswith("completed")
    }
    total_cases = len(cases)
    for case_index, (scenario, requested, difficulty) in enumerate(cases, 1):
            size_label = scenario.split("_", 1)[0]
            if scenario in completed_names and not args.rerun_completed:
                print(
                    f"[{case_index}/{total_cases}] {scenario}: already completed, skip",
                    flush=True,
                )
                continue

            data_root = PROJECT_ROOT / "data"
            if args.regenerate_data:
                data_root /= "generator_v3_2"
            data_path = data_root / f"{args.data_prefix}{scenario}_groups.json"
            if args.regenerate_data:
                print(
                    f"\n[{case_index}/{total_cases}] {scenario}: regenerating data",
                    flush=True,
                )
                groups, fixed_count, seed = batch_benchmark.generate_case(
                    requested,
                    difficulty,
                    old_topology,
                    new_topology,
                )
                traveler_count = passenger_data_generator.passenger_count(groups)
                counted_count = passenger_data_generator.target_seat_demand(groups)
                metadata = {
                    "generatorVersion": passenger_data_generator.GENERATOR_VERSION,
                    "requestedPassengerCount": requested,
                    "passengerCount": counted_count,
                    "travelerCount": traveler_count,
                    "passengerCapacity": capacity,
                    "sizeLabel": size_label,
                    "targetSeatDemand": counted_count,
                    "scenario": difficulty,
                    "seed": seed,
                    "preassignedCount": fixed_count,
                    "oldSeatmap": experiment["old_seatmap"],
                    "newSeatmap": experiment["new_seatmap"],
                    "generatorDiagnostics": (
                        passenger_data_generator.generator_diagnostics(
                            groups, old_topology,
                            passenger_data_generator.SCENARIOS[difficulty],
                        )
                    ),
                    "groups": groups,
                }
                _write_json(data_path, metadata)
            else:
                if not data_path.exists():
                    raise FileNotFoundError(
                        f"既有数据不存在: {data_path}; 如需生成请使用--regenerate-data"
                    )
                print(
                    f"\n[{case_index}/{total_cases}] {scenario}: loading existing data",
                    flush=True,
                )
                groups, metadata = _load_case(data_path)
                fixed_count = metadata.get("preassignedCount")
                seed = metadata.get("seed")
                traveler_count = passenger_data_generator.passenger_count(groups)
                counted_count = passenger_data_generator.target_seat_demand(groups)

            validation_errors = passenger_data_generator.validate_case(
                groups,
                old_topology,
                new_topology,
                passenger_data_generator.SCENARIOS[difficulty],
            )
            if validation_errors:
                preview = "; ".join(validation_errors[:10])
                raise ValueError(
                    f"{scenario} 输入校验失败（共{len(validation_errors)}项）: "
                    f"{preview}"
                )

            if args.generate_only:
                audit_row = {
                    "case": scenario,
                    "difficulty": difficulty,
                    "status": "input_validated",
                    "requested_count": requested,
                    "traveler_count": traveler_count,
                    "target_seat_demand": counted_count,
                    "groups": len(groups),
                    "seed": seed,
                    "preassigned_count": fixed_count,
                    "data_file": str(data_path.relative_to(PROJECT_ROOT)),
                    "cabin_demand": dict(
                        passenger_data_generator.cabin_seat_demand(groups)
                    ),
                    "cabin_capacity": dict(
                        passenger_data_generator.cabin_seat_capacity(
                            new_topology
                        )
                    ),
                    "validation_errors": [],
                }
                report["cases"] = [
                    old for old in report["cases"]
                    if old.get("case") != scenario
                ]
                report["cases"].append(audit_row)
                report["completed_cases"] = len(report["cases"])
                report["updated_at"] = (
                    datetime.now().astimezone().isoformat(timespec="seconds")
                )
                _write_json(output_path, report)
                print(
                    f"[{case_index}/{total_cases}] {scenario}: input valid; "
                    f"travelers={traveler_count}, demand={counted_count}, "
                    f"data={data_path}",
                    flush=True,
                )
                continue

            run_config = copy.deepcopy(base_config)
            if args.heuristic_business_time_limit <= 0:
                raise ValueError("--heuristic-business-time-limit必须为正数")
            _override_heuristic_time_limit(
                run_config, float(args.heuristic_business_time_limit)
            )
            run_config.setdefault("data", {})["oldseatmap"] = experiment[
                "old_seatmap"
            ]
            run_config["data"]["seatmap"] = experiment["new_seatmap"]
            column_cfg = run_config.setdefault("column_generation", {})
            column_cfg["dual_stabilization_mode"] = (
                args.dual_stabilization_mode
            )
            column_cfg["transported_pricing_bounds_enabled"] = (
                not args.disable_transported_pricing_bounds
            )
            # 只处理根节点：完整定价完成后保留认证 LP 上界，不进入 MP1 整数分支。
            column_cfg["mp1_node_limit"] = 1
            column_cfg["mp1_dive_max_nodes"] = 0
            column_cfg["restricted_mip_time_limit"] = max(
                0.0, args.restricted_mip_time_limit
            )
            column_cfg["show_progress"] = True
            if args.total_time_limit is not None:
                column_cfg["total_time_limit"] = args.total_time_limit
            if args.mp2_time_limit is not None:
                column_cfg["mp2_time_limit"] = args.mp2_time_limit
            if args.pricing_workers is not None:
                column_cfg["pricing_workers"] = args.pricing_workers

            print(
                f"[{case_index}/{total_cases}] {scenario}: "
                "running H + restricted-column I + exact/safe root LP U",
                flush=True,
            )
            exact_started = time.perf_counter()
            try:
                result, _ = exact_column_generation.run_column_generation(
                    new_seats,
                    old_seats,
                    groups,
                    run_config["weights"],
                    run_config,
                )
            except Exception as exc:
                exact_elapsed = time.perf_counter() - exact_started
                row = {
                    "case": scenario,
                    "difficulty": difficulty,
                    "requested_count": requested,
                    "passenger_count": counted_count,
                    "traveler_count": traveler_count,
                    "groups": len(groups),
                    "seed": seed,
                    "data_file": str(
                        data_path.relative_to(PROJECT_ROOT)
                    ),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "column_generation_seconds": exact_elapsed,
                }
                report["cases"] = [
                    old for old in report["cases"]
                    if old.get("case") != scenario
                ]
                report["cases"].append(row)
                report["completed_cases"] = len(report["cases"])
                _update_upper_bound_counts(report)
                report["updated_at"] = (
                    datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    )
                )
                _write_json(output_path, report)
                print(
                    f"[{case_index}/{total_cases}] {scenario}: failed "
                    f"after {exact_elapsed:.1f}s: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
            exact_elapsed = time.perf_counter() - exact_started
            diagnostics = result["diagnostics"]
            detail_path = details_dir / f"{scenario}.json"
            _write_json(
                detail_path,
                exact_column_generation._json_safe(result),
            )
            warm = diagnostics["heuristic_warm_start"]
            heuristic_valid = bool(warm.get("accepted", False))
            heuristic_score = (
                warm.get("score_without_time_penalty")
                if heuristic_valid
                else None
            )
            heuristic_raw_score = warm.get("score_without_time_penalty")
            heuristic_seconds = warm.get("elapsed_seconds")
            best_feasible_score = float(result["total_score"])
            lp_bound = diagnostics.get("complete_column_space_lp_bound")
            safe_lp_bound = diagnostics.get("safe_column_space_lp_bound")
            certified = bool(
                diagnostics.get(
                    "complete_column_space_lp_bound_certified", False
                )
            )
            effective_upper_bound, upper_bound_source = (
                _effective_upper_bound(diagnostics)
            )
            exact_upper_bound = lp_bound if certified else None
            certified_heuristic_gap = _absolute_gap(
                exact_upper_bound, heuristic_score
            )
            integrality_gap = _absolute_gap(
                exact_upper_bound, best_feasible_score
            )
            heuristic_to_incumbent_gap = _absolute_gap(
                best_feasible_score, heuristic_score
            )
            improved_from_warm_start = bool(
                heuristic_score is not None
                and best_feasible_score
                > float(heuristic_score) + 1e-7
            )
            row = {
                "case": scenario,
                "difficulty": difficulty,
                "requested_count": requested,
                "passenger_count": counted_count,
                "traveler_count": traveler_count,
                "groups": len(groups),
                "seed": seed,
                "preassigned_count": fixed_count,
                "data_file": str(data_path.relative_to(PROJECT_ROOT)),
                "detail_file": _display_path(detail_path),
                "status": "completed",
                "heuristic_score": heuristic_score,
                "heuristic_raw_score": heuristic_raw_score,
                "heuristic_valid": heuristic_valid,
                "heuristic_rejection_reason": warm.get("rejection_reason"),
                "heuristic_seconds": heuristic_seconds,
                "heuristic_accepted_as_warm_start": bool(
                    warm.get("accepted", False)
                ),
                "best_feasible_score": best_feasible_score,
                "best_feasible_source": (
                    "restricted_column_pool_mip"
                    if improved_from_warm_start or not heuristic_valid
                    else "heuristic_warm_start"
                ),
                "certified_lp_upper_bound": lp_bound,
                "lp_bound_certified": certified,
                "safe_lp_upper_bound": safe_lp_bound,
                "safe_lp_bound_certified": bool(
                    diagnostics.get(
                        "safe_column_space_lp_bound_certified", False
                    )
                ),
                "safe_heuristic_gap_absolute": diagnostics.get(
                    "safe_heuristic_gap_absolute"
                ),
                "safe_heuristic_gap_relative_percent": (
                    None
                    if diagnostics.get("safe_heuristic_gap_relative") is None
                    else 100.0
                    * float(diagnostics["safe_heuristic_gap_relative"])
                ),
                "safe_gap_target_reached": bool(
                    diagnostics.get("safe_gap_target_reached", False)
                ),
                "effective_lp_upper_bound": effective_upper_bound,
                "effective_lp_upper_bound_source": upper_bound_source,
                "upper_bound_quality": (
                    "exact"
                    if certified
                    else "safe_coarse"
                    if safe_lp_bound is not None
                    else "none"
                ),
                "certified_heuristic_gap_absolute": certified_heuristic_gap,
                "certified_heuristic_gap_relative_percent": _relative_percent(
                    certified_heuristic_gap, heuristic_score
                ),
                "certified_heuristic_gap_per_traveler": (
                    None
                    if certified_heuristic_gap is None or traveler_count <= 0
                    else certified_heuristic_gap / traveler_count
                ),
                "integrality_gap_absolute": integrality_gap,
                "integrality_gap_relative_percent": _relative_percent(
                    integrality_gap, best_feasible_score
                ),
                "heuristic_to_incumbent_gap_absolute": (
                    heuristic_to_incumbent_gap
                ),
                "heuristic_to_incumbent_gap_relative_percent": (
                    _relative_percent(
                        heuristic_to_incumbent_gap, heuristic_score
                    )
                ),
                "column_generation_seconds": exact_elapsed,
                "termination": diagnostics.get("termination"),
                "master_nodes": diagnostics.get("master_nodes"),
                "column_generation_iterations": diagnostics.get(
                    "column_generation_iterations"
                ),
                "pricing_calls": diagnostics.get("pricing", {}).get("calls"),
                "exact_pricing_calls": diagnostics.get("pricing", {}).get(
                    "exact_calls"
                ),
                "repeated_exact_group_calls": diagnostics.get(
                    "pricing", {}
                ).get("repeated_exact_group_calls"),
                "pricing_group_stats": diagnostics.get("pricing", {}).get(
                    "group_stats", {}
                ),
                "dual_stabilization": diagnostics.get("pricing", {}).get(
                    "adaptive_dual_stabilization", {}
                ),
                "restricted_master_integer_heuristic": diagnostics.get(
                    "master_search", {}
                ).get(
                    "restricted_master_integer_heuristic"
                ),
                "final_root_bound_sweep": diagnostics.get(
                    "final_root_bound_sweep", {}
                ),
            }
            report["cases"] = [
                old for old in report["cases"]
                if old.get("case") != scenario
            ]
            report["cases"].append(row)
            report["completed_cases"] = len(report["cases"])
            _update_upper_bound_counts(report)
            report["updated_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            _write_json(output_path, report)
            print(
                f"[{case_index}/{total_cases}] {scenario}: "
                f"H={heuristic_score}, I={best_feasible_score}, "
                f"U={effective_upper_bound} ({upper_bound_source}), "
                f"exact_U-H={certified_heuristic_gap}, "
                f"exact_U-I={integrality_gap}, "
                f"I-H={heuristic_to_incumbent_gap}, "
                f"heuristic_s={heuristic_seconds}, total_s={exact_elapsed:.1f}",
                flush=True,
            )

    report["finished_at"] = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    _update_upper_bound_counts(report)
    _write_json(output_path, report)
    print(f"\nAll requested cases finished. Results: {output_path}", flush=True)


if __name__ == "__main__":
    main()
