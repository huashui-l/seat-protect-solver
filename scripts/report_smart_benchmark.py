#!/usr/bin/env python3
"""Build an auditable paired report without feeding references into the solver."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


def read_summary(directory):
    return json.loads((directory / "summary.json").read_text())


def matched(a, b):
    return all(
        a.get(k) == b.get(k) and a.get(k) is not None
        for k in ("input_sha256", "old_seatmap_sha256", "new_seatmap_sha256")
    )


def fmt(value, digits=3):
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "—"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--fast", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    new = read_summary(args.candidate)
    old = read_summary(args.baseline)
    fast = read_summary(args.fast) if args.fast else {"rows": []}
    old_rows = {r["case_id"]: r for r in old["rows"]}
    fast_rows = {r["case_id"]: r for r in fast["rows"]}
    output = []
    for r in new["rows"]:
        cid = r["case_id"]
        b = old_rows.get(cid)
        f = fast_rows.get(cid)
        comparable = bool(b and r["valid"] and b["valid"] and matched(r, b))
        delta = r["score"] - b["score"] if comparable else None
        output.append(
            {
                "case_id": cid,
                "passengers": r.get("passengers"),
                "new_budget": r["budget"],
                "new_seconds": r["seconds"],
                "new_valid": r["valid"],
                "new_score": r.get("score"),
                "old_budget": b["budget"] if b else None,
                "old_seconds": b["seconds"] if b else None,
                "old_valid": b["valid"] if b else None,
                "old_score": b.get("score") if b else None,
                "new5_valid": f["valid"] if f else None,
                "new5_score": f.get("score") if f else None,
                "same_inputs": matched(r, b) if b else False,
                "delta": delta,
                "penalty_reduction_percent": 100 * delta / max(abs(b["score"]), 1e-9)
                if comparable
                else None,
                "proven_optimal": r.get("proven_optimal", False),
                "global_upper_bound": r.get("upper_bound"),
                "input_sha256": r.get("input_sha256"),
            }
        )
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    text = [
        "# 新启发式实测报告",
        "",
        "本报告按实际输入文件、旧座位图和新座位图的 SHA-256 配对。分数越大越好；不完整或硬约束违规的结果不参与分数平均。",
        "",
        f"硬件/系统：{new['platform']}。新算法配置 {new['workers']} 个 CP-SAT 搜索线程；HiGHS 重组使用 1 线程。旧算法使用原实现默认线程配置。每个批次逐例串行运行。",
        "",
        f"依赖版本：`{json.dumps(new.get('versions', {}), ensure_ascii=False)}`。原始配置、源码哈希、逐例完整分配及独立审计位于各批次的 summary.json 和用例 JSON。",
        "",
        "| 数据集 | 新算法完整合法 | 旧算法完整合法 | 可比用例 | 胜/平/负 | 平均罚分降低 | 新算法耗时中位数/最大值 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    aggregates = {}
    for suite in ["formal24", "d4", "v2", "all"]:
        select = lambda cid, suite=suite: (
            suite == "all"
            or (
                cid.startswith(("forward:", "reverse:"))
                if suite == "formal24"
                else cid.startswith(suite + ":")
            )
        )
        nr = [r for r in new["rows"] if select(r["case_id"])]
        br = [r for r in old["rows"] if select(r["case_id"])]
        paired = [r for r in output if select(r["case_id"]) and r["delta"] is not None]
        if not nr:
            continue
        wins = sum(r["delta"] > 1e-6 for r in paired)
        ties = sum(abs(r["delta"]) <= 1e-6 for r in paired)
        losses = len(paired) - wins - ties
        mean = (
            statistics.mean(r["penalty_reduction_percent"] for r in paired)
            if paired
            else None
        )
        item = {
            "new_valid": sum(r["valid"] for r in nr),
            "new_count": len(nr),
            "old_valid": sum(r["valid"] for r in br),
            "old_count": len(br),
            "comparable": len(paired),
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "mean_penalty_reduction_percent": mean,
            "median_seconds": statistics.median(r["seconds"] for r in nr),
            "max_seconds": max(r["seconds"] for r in nr),
        }
        aggregates[suite] = item
        text.append(
            f"| {suite} | {item['new_valid']}/{len(nr)} | {item['old_valid']}/{len(br)} | {len(paired)} | {wins}/{ties}/{losses} | {fmt(mean, 2)}% | {fmt(item['median_seconds'], 2)} / {fmt(item['max_seconds'], 2)} s |"
        )
    text += [
        "",
        "平均罚分降低按每个可比用例等权计算：(新分数−旧分数)/|旧分数|。负值表示退步。不同时间预算的比较同时包含新增计算时间带来的收益。",
        "",
        "## 逐例结果",
        "",
        "| 用例 | 人数 | 新算法分数 | 新算法时间 | 旧算法分数 | 旧算法时间 | 罚分降低 | 新 5 秒分数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in output:
        text.append(
            f"| {r['case_id']} | {r['passengers']} | {fmt(r['new_score'])} | {fmt(r['new_seconds'], 2)} | {fmt(r['old_score'])} | {fmt(r['old_seconds'], 2)} | {fmt(r['penalty_reduction_percent'], 2)}% | {fmt(r['new5_score'])} |"
        )
    text += [
        "",
        "`proven_optimal=false` 的大实例均为本次找到的可行解，不能把模式池最优或历史整数参考当作全局最优。",
        "",
        "只有一个随机种子的一次墙钟预算运行不足以证明跨机器或跨运行的稳定性；多线程调度、原生求解器结束开销均会影响结果。",
    ]
    (args.output / "report.md").write_text("\n".join(text) + "\n")
    (args.output / "aggregate.json").write_text(
        json.dumps(aggregates, ensure_ascii=False, indent=2)
    )
    print(json.dumps(aggregates, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
