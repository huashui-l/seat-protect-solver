#!/usr/bin/env python3
"""Compare saved independent runs with frozen C++ results, never during search."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPP_COLUMNS = (
    "final_score",
    "repeat_score",
    "cleanup_run_external_score",
    "cleanup_repeat_external_score",
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fmt(value):
    return "—" if value is None else f"{value:.3f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True, help="name=directory")
    parser.add_argument("--cpp", type=Path, default=ROOT / "docs/provenance/cpp5-cg30")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = read(args.cpp / "manifest.json")
    provenance = read(args.cpp / "provenance.json")
    cases = {r["case_id"]: r for r in manifest["cases"]}
    maps = {r["layout"]: r["sha256"] for r in manifest["seatmaps"]}
    with (args.cpp / "comparison.csv").open() as handle:
        references = {r["case_id"]: r for r in csv.DictReader(handle)}
    rows, aggregate, metadata = [], {}, {}
    for spec in args.runs:
        name, directory = spec.split("=", 1)
        directory = Path(directory)
        summary = read(directory / "summary.json")
        metadata[name] = {k: v for k, v in summary.items() if k != "rows"}
        complete, infeasible, unknown, matched = 0, 0, 0, 0
        deltas, best_deltas, reductions, times = [], [], [], []
        for r in summary["rows"]:
            cid = r["case_id"]
            allocation = read(directory / (cid.replace(":", "_") + ".json"))
            status = allocation.get("status", "error")
            complete += r["valid"]
            infeasible += status == "infeasible"
            unknown += not r["valid"] and status != "infeasible"
            times.append(r["seconds"])
            ref, case = references.get(cid), cases.get(cid)
            same = bool(
                ref
                and case
                and r.get("input_sha256") == case["sha256"] == ref["input_sha256"]
                and r.get("old_seatmap_sha256") == maps[case["old_layout"]]
                and r.get("new_seatmap_sha256") == maps[case["new_layout"]]
            )
            if ref and not same:
                raise ValueError(f"Input/seatmap hash mismatch: {name}: {cid}")
            primary = float(ref["cleanup_run_external_score"]) if ref else None
            record = max(float(ref[k]) for k in CPP_COLUMNS) if ref else None
            delta = r["score"] - primary if same and r["valid"] else None
            best_delta = r["score"] - record if same and r["valid"] else None
            if delta is not None:
                matched += 1
                deltas.append(delta)
                best_deltas.append(best_delta)
                reductions.append(100 * delta / max(abs(primary), 1))
            rows.append(
                {
                    "run": name,
                    "case_id": cid,
                    "passengers": r.get("passengers"),
                    "budget": r["budget"],
                    "seconds": r["seconds"],
                    "status": status,
                    "valid": r["valid"],
                    "score": r.get("score"),
                    "cpp_cleanup_score": primary,
                    "cpp_best_saved_score": record,
                    "delta_vs_cpp_cleanup": delta,
                    "delta_vs_cpp_best_saved": best_delta,
                    "same_inputs": same if ref else None,
                    "proven_optimal": r.get("proven_optimal", False),
                    "input_sha256": r.get("input_sha256"),
                    "old_seatmap_sha256": r.get("old_seatmap_sha256"),
                    "new_seatmap_sha256": r.get("new_seatmap_sha256"),
                }
            )
        item = {
            "count": len(summary["rows"]),
            "complete_legal": complete,
            "infeasible": infeasible,
            "no_solution_or_error": unknown,
            "cpp_comparable": matched,
            "wins": sum(x > 1e-6 for x in deltas),
            "ties": sum(abs(x) <= 1e-6 for x in deltas),
            "losses": sum(x < -1e-6 for x in deltas),
            "wins_vs_best_saved": sum(x > 1e-6 for x in best_deltas),
            "ties_vs_best_saved": sum(abs(x) <= 1e-6 for x in best_deltas),
            "losses_vs_best_saved": sum(x < -1e-6 for x in best_deltas),
            "mean_penalty_reduction_percent": statistics.mean(reductions)
            if reductions
            else None,
            "median_seconds": statistics.median(times),
            "max_seconds": max(times),
        }
        aggregate[name] = item
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = [
        "# 新启发式与历史 C++ 的逐例比较",
        "",
        "主要基线固定为历史 cleanup_run；另列四次保存运行的逐例最好分数，作为更严格的目标。后者是多次运行的成绩包络，不是一趟 5 秒运行。新版各批次单独报告，不逐例挑选不同运行的最好成绩。",
        "",
        "历史平台为 AMD Ryzen 7 5800H / Windows，预算 5 秒、峰值 5 个搜索线程。新版平台和线程数见 metadata.json。跨机器、跨预算比较反映所得解质量，不能据此断言同硬件速度优势。",
        "",
        "输入及两份座位图按历史 manifest 的 SHA-256 逐例检查。分数越大越好；仅完整合法、输入一致的结果参与胜平负统计。历史整数参考不是全局最优。",
        "",
        "| 批次 | 完整合法/总数 | 判定不可行 | 未求出/错误 | C++ 可比数 | 对清理版 胜/平/负 | 对历史逐例最好 胜/平/负 | 平均扣分降低 | 耗时中位数/最大值 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, a in aggregate.items():
        report.append(
            f"| {name} | {a['complete_legal']}/{a['count']} | {a['infeasible']} | {a['no_solution_or_error']} | {a['cpp_comparable']} | {a['wins']}/{a['ties']}/{a['losses']} | {a['wins_vs_best_saved']}/{a['ties_vs_best_saved']}/{a['losses_vs_best_saved']} | {fmt(a['mean_penalty_reduction_percent'])}% | {fmt(a['median_seconds'])}/{fmt(a['max_seconds'])} s |"
        )
    report += [
        "",
        "平均扣分降低对可比用例等权计算：(新分数−清理版分数)/max(1, |清理版分数|)。",
        "",
        "| 批次 | 用例 | 新分数 | 时间 | C++ 清理版 | C++ 历史逐例最好 | 相对清理版差值 | 相对历史最好差值 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["cpp_cleanup_score"] is not None:
            report.append(
                f"| {r['run']} | {r['case_id']} | {fmt(r['score'])} | {fmt(r['seconds'])} | {fmt(r['cpp_cleanup_score'])} | {fmt(r['cpp_best_saved_score'])} | {fmt(r['delta_vs_cpp_cleanup'])} | {fmt(r['delta_vs_cpp_best_saved'])} |"
            )
    report += [
        "",
        "全部用例的状态、输入哈希和评分见 comparison.csv。无法分配的输入和限时未找到解的输入分别报告，不能把未找到解当作不可行证明。",
        "",
    ]
    (args.output / "report.md").write_text("\n".join(report))
    (args.output / "aggregate.json").write_text(json.dumps(aggregate, indent=2))
    (args.output / "metadata.json").write_text(
        json.dumps(
            {
                "runs": metadata,
                "cpp_provenance": provenance,
                "cpp_comparison_sha256": hashlib.sha256(
                    (args.cpp / "comparison.csv").read_bytes()
                ).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()
