#!/usr/bin/env python3
"""Merge H5, H20 and bidirectional column-generation results into one audit."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def gap(upper: Any, lower: Any) -> float | None:
    if upper is None or lower is None:
        return None
    value = float(upper) - float(lower)
    return value if value >= -1e-7 else None


def feasible_improvement(better: Any, baseline: Any) -> float | None:
    """Difference between feasible scores; negative means the baseline is better."""
    if better is None or baseline is None:
        return None
    return float(better) - float(baseline)


def percent(value: float | None, reference: Any) -> float | None:
    if value is None or reference is None:
        return None
    return 100.0 * value / max(1.0, abs(float(reference)))


def fmt(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h5", type=Path, required=True)
    parser.add_argument("--h20", type=Path, required=True)
    parser.add_argument("--forward-cg", type=Path, required=True)
    parser.add_argument("--reverse-cg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()

    h5_data = read_json(args.h5.resolve())
    h20_data = read_json(args.h20.resolve())
    h5 = {(row["direction"], row["case"]): row for row in h5_data["cases"]}
    h20 = {(row["direction"], row["case"]): row for row in h20_data["cases"]}
    cg: dict[tuple[str, str], dict[str, Any]] = {}
    for direction, path in (
        ("forward", args.forward_cg),
        ("reverse", args.reverse_cg),
    ):
        for row in read_json(path.resolve()).get("cases", []):
            cg[(direction, row["case"])] = row

    rows: list[dict[str, Any]] = []
    for direction in ("forward", "reverse"):
        for size in ("50", "100", "150", "full"):
            for difficulty in ("normal", "stress", "edge"):
                case = f"{size}_{difficulty}"
                key = (direction, case)
                five = h5.get(key, {})
                twenty = h20.get(key, {})
                column = cg.get(key, {})
                h5_score = five.get("heuristic_score")
                h20_score = twenty.get("heuristic_score")
                pool_score = column.get("best_feasible_score")
                feasible_scores = [
                    float(value)
                    for value in (h5_score, h20_score, pool_score)
                    if value is not None
                ]
                best_feasible = max(feasible_scores) if feasible_scores else None
                best_source = None
                if best_feasible is not None:
                    if pool_score is not None and abs(best_feasible - float(pool_score)) <= 1e-7:
                        best_source = "column_pool_I"
                    elif h20_score is not None and abs(best_feasible - float(h20_score)) <= 1e-7:
                        best_source = "H20"
                    else:
                        best_source = "H5"
                upper = column.get("effective_lp_upper_bound")
                upper_source = column.get("effective_lp_upper_bound_source")
                exact_upper = (
                    upper
                    if upper_source == "exact_complete_column_space_lp"
                    else None
                )
                u_h5 = gap(exact_upper, h5_score)
                u_h20 = gap(exact_upper, h20_score)
                u_best = gap(exact_upper, best_feasible)
                # I is selected from H5, H20 and the restricted column-pool MIP,
                # so it is never worse than either heuristic (up to tolerance).
                # Unlike U-H, this is a feasible-score improvement I-H.
                i_h5 = feasible_improvement(best_feasible, h5_score)
                i_h20 = feasible_improvement(best_feasible, h20_score)
                rows.append({
                    "direction": direction,
                    "case": case,
                    "h5_score": h5_score,
                    "h5_seconds": five.get("elapsed_seconds"),
                    "h5_valid_complete": five.get("valid_complete", False),
                    "h20_score": h20_score,
                    "h20_seconds": twenty.get("elapsed_seconds"),
                    "h20_valid_complete": twenty.get("valid_complete", False),
                    "column_pool_score": pool_score,
                    "best_feasible_score": best_feasible,
                    "best_feasible_source": best_source,
                    "effective_lp_upper_bound": upper,
                    "upper_bound_source": upper_source,
                    "upper_bound_quality": (
                        "exact"
                        if exact_upper is not None
                        else "safe_coarse"
                        if upper is not None
                        else "none"
                    ),
                    "column_termination": column.get("termination"),
                    "column_generation_seconds": column.get("column_generation_seconds"),
                    "u_minus_h5_absolute": u_h5,
                    "u_minus_h5_percent": percent(u_h5, h5_score),
                    "u_minus_h20_absolute": u_h20,
                    "u_minus_h20_percent": percent(u_h20, h20_score),
                    "u_minus_best_feasible_absolute": u_best,
                    "u_minus_best_feasible_percent": percent(u_best, best_feasible),
                    "best_feasible_minus_h5_absolute": i_h5,
                    "best_feasible_minus_h5_percent": percent(i_h5, h5_score),
                    "best_feasible_minus_h20_absolute": i_h20,
                    "best_feasible_minus_h20_percent": percent(i_h20, h20_score),
                    "status": (
                        "complete"
                        if five.get("valid_complete") and twenty.get("valid_complete")
                        and column.get("status") == "completed"
                        else "incomplete"
                    ),
                })

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "definitions": {
            "H5": "current heuristic with a 5-second business limit",
            "H20": "current heuristic with a 20-second business limit",
            "I": "best feasible score among H5, H20 and restricted column-pool MIP",
            "U": "effective exact or safe full-column-space LP upper bound",
            "gap_rule": (
                "U-related quality gaps are reported only for exact LP bounds; "
                "safe coarse bounds are displayed as certificates only"
            ),
        },
        "case_count": len(rows),
        "complete_count": sum(row["status"] == "complete" for row in rows),
        "valid_upper_bound_count": sum(row["effective_lp_upper_bound"] is not None for row in rows),
        "upper_bound_counts": {
            "exact_complete_column_space_lp": sum(
                row["upper_bound_source"] == "exact_complete_column_space_lp"
                for row in rows
            ),
            "safe_reduced_cost_corrected_only": sum(
                row["upper_bound_source"] == "safe_reduced_cost_corrected_lp"
                for row in rows
            ),
            "no_certified_upper_bound": sum(
                row["effective_lp_upper_bound"] is None for row in rows
            ),
        },
        "cases": rows,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 双向24组：5秒、20秒启发式与列生成实验汇总",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "`I`取H5、H20和列池整数解中的最好可行分数；只有精确LP上界才计算质量gap，安全粗界只作证书展示。",
        "",
        (
            "上界覆盖：精确完整列空间U "
            f"{report['upper_bound_counts']['exact_complete_column_space_lp']} 组，"
            "安全修正U "
            f"{report['upper_bound_counts']['safe_reduced_cost_corrected_only']} 组，"
            "无U "
            f"{report['upper_bound_counts']['no_certified_upper_bound']} 组。"
        ),
        "",
        "| 方向 | 算例 | H5 | H20 | I | U | U类型 | 精确U-H5% | 精确U-H20% | 精确U-I% | I-H5% | I-H20% | CG终止 |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['direction']} | {row['case']} | {fmt(row['h5_score'])} | "
            f"{fmt(row['h20_score'])} | {fmt(row['best_feasible_score'])} | "
            f"{fmt(row['effective_lp_upper_bound'])} | "
            f"{row['upper_bound_quality']} | "
            f"{fmt(row['u_minus_h5_percent'])} | {fmt(row['u_minus_h20_percent'])} | "
            f"{fmt(row['u_minus_best_feasible_percent'])} | "
            f"{fmt(row['best_feasible_minus_h5_percent'])} | "
            f"{fmt(row['best_feasible_minus_h20_percent'])} | "
            f"{row['column_termination'] or '—'} |"
        )
    markdown = args.markdown.resolve()
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"JSON: {output}")
    print(f"Markdown: {markdown}")


if __name__ == "__main__":
    main()
