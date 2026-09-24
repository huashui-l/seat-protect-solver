"""汇总所有基准 JSON，生成三方（含 Rich 60s 参考）对比表与统计摘要（Markdown）。"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "reports/audit-2026-09-23/benchmark"
METHODS = ["heuristic", "legacy", "new"]
LABEL = {"heuristic": "启发式 5s", "legacy": "旧列生成 150s", "new": "新列生成 150s"}


def load(name):
    p = B / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def rich_reference():
    p = ROOT / "rich_python_formal24.csv"
    if not p.exists():
        return {}
    ref = {}
    with p.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ref[row["case_id"]] = {"rich60": float(row["objective"]), "best_I": float(row["current_reference_F_I"]),
                                   "rich_wall": float(row["wall_seconds"])}
    return ref


def merge_new(rows, new_rows):
    """用最终版新方法结果覆盖三方表中的 new 列。"""
    if not new_rows:
        return rows
    by = {r["instance"]: r.get("new") for r in new_rows}
    for r in rows:
        if r["instance"] in by and by[r["instance"]] and "error" not in by[r["instance"]]:
            r["new"] = by[r["instance"]]
    return rows


def fmt(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def bound_of(m):
    if m.get("bpU") is not None and m.get("proven"):
        return m["bpU"], "证明最优"
    if m.get("U") is not None:
        return m["U"], "精确U"
    if m.get("safeU") is not None:
        return m["safeU"], "安全U"
    return None, "无"


def table(rows, ref=None, prefix=""):
    lines = ["| 算例 | 人/组/最大组 | 方法 | 合法 | 软分 | s / v / p / c / b | 界 | 界类型 | gap% | 秒 |", "|---|---|---|---|---:|---|---:|---|---:|---:|"]
    for r in rows:
        name = r["instance"].replace("_groups", "")
        for meth in METHODS:
            m = r.get(meth)
            if not m or "error" in (m or {}):
                lines.append(f"| {name} | {r['pax']}/{r['groups']}/{r['max_group']} | {LABEL[meth]} | 出错 | | | | | | |"); continue
            legal = "是" if m["violations"] == 0 and m["unassigned"] == 0 else f"否({m['violations']}/{m['unassigned']})"
            bnd, kind = bound_of(m)
            gap = "—" if bnd is None else f"{100*(bnd-m['score'])/max(1,abs(m['score'])):.2f}"
            lines.append(f"| {name} | {r['pax']}/{r['groups']}/{r['max_group']} | {LABEL[meth]} | {legal} | {m['score']:.3f} | "
                         f"{m['s']:.1f}/{m['v']:.1f}/{m['p']:.1f}/{m['c']:.1f}/{m['b']:.2f} | {fmt(bnd)} | {kind} | {gap} | {m['time']:.0f} |")
        if ref is not None:
            key = f"{prefix}{name}"
            if key in ref:
                lines.append(f"| {name} | | Rich 60s 参考 | 是 | {ref[key]['rich60']:.3f} | | | | | {ref[key]['rich_wall']:.0f} |")
                lines.append(f"| {name} | | 历史最好整数 I | 是 | {ref[key]['best_I']:.3f} | | | | | |")
    return "\n".join(lines)


def summary(rows, ref=None, prefix=""):
    n = 0; wins = {m: 0 for m in METHODS}; ties = 0; cert = {m: 0 for m in METHODS}; proven = {m: 0 for m in METHODS}
    legal = {m: 0 for m in METHODS}; gaps = {m: [] for m in METHODS}; times = {m: [] for m in METHODS}
    new_vs_legacy = []; new_vs_rich = []; new_vs_best = []; new_bound_vs_best_violations = []
    for r in rows:
        ms = {m: r.get(m) for m in METHODS}
        if any(not ms[m] or "error" in ms[m] for m in METHODS):
            continue
        n += 1
        for m in METHODS:
            if ms[m]["violations"] == 0 and ms[m]["unassigned"] == 0: legal[m] += 1
            if ms[m].get("U") is not None: cert[m] += 1
            if ms[m].get("proven"): proven[m] += 1
            times[m].append(ms[m]["time"])
            bnd, _ = bound_of(ms[m])
            if bnd is not None: gaps[m].append(100*(bnd-ms[m]["score"])/max(1, abs(ms[m]["score"])))
        best = max(ms[m]["score"] for m in METHODS)
        top = [m for m in METHODS if abs(ms[m]["score"]-best) < 1e-6]
        if len(top) > 1: ties += 1
        for m in top: wins[m] += 1
        new_vs_legacy.append(ms["new"]["score"] - ms["legacy"]["score"])
        name = r["instance"].replace("_groups", ""); key = f"{prefix}{name}"
        if ref and key in ref:
            new_vs_rich.append(ms["new"]["score"] - ref[key]["rich60"]); new_vs_best.append(ms["new"]["score"] - ref[key]["best_I"])
            if ms["new"].get("U") is not None and ms["new"]["U"] < ref[key]["best_I"] - 1e-6:
                new_bound_vs_best_violations.append(name)
    out = [f"- 有效算例：{n}", f"- 完整合法：" + "、".join(f"{LABEL[m]} {legal[m]}/{n}" for m in METHODS),
           f"- 最好软分（含并列）：" + "、".join(f"{LABEL[m]} {wins[m]}" for m in METHODS) + f"（并列 {ties}）",
           f"- 有精确 LP 上界：" + "、".join(f"{LABEL[m]} {cert[m]}" for m in METHODS),
           f"- 证明整数最优：" + "、".join(f"{LABEL[m]} {proven[m]}" for m in METHODS),
           f"- 相对自身界的 gap% 均值/中位：" + "、".join(f"{LABEL[m]} {statistics.mean(gaps[m]):.2f}/{statistics.median(gaps[m]):.2f}" if gaps[m] else f"{LABEL[m]} —" for m in METHODS),
           f"- 耗时均值 s：" + "、".join(f"{LABEL[m]} {statistics.mean(times[m]):.0f}" for m in METHODS)]
    if new_vs_legacy:
        out.append(f"- 新 − 旧 软分差：均值 {statistics.mean(new_vs_legacy):+.2f}，中位 {statistics.median(new_vs_legacy):+.2f}，新更好 {sum(1 for d in new_vs_legacy if d > 1e-6)} 例，旧更好 {sum(1 for d in new_vs_legacy if d < -1e-6)} 例")
    if new_vs_rich:
        out.append(f"- 新 − Rich60s 软分差：均值 {statistics.mean(new_vs_rich):+.2f}，新更好 {sum(1 for d in new_vs_rich if d > 1e-6)}/{len(new_vs_rich)}")
        out.append(f"- 新 − 历史最好 I 软分差：均值 {statistics.mean(new_vs_best):+.2f}，新更好 {sum(1 for d in new_vs_best if d > 1e-6)}/{len(new_vs_best)}")
        out.append(f"- 新 U 低于历史最好 I（应为 0，否则界有误）：{len(new_bound_vs_best_violations)} {new_bound_vs_best_violations}")
    return "\n".join(out)


def main():
    ref = rich_reference()
    sections = []
    sets = [
        ("公开验证集（小规模，6–23 人，60 s）", "fixtures.json", "fixtures_new_final.json", None, ""),
        ("Formal24 正向（3-4-3→3-3，150 s）", "formal24_forward.json", None, ref, "forward:"),
        ("Formal24 反向（3-3→3-4-3，150 s）", "formal24_reverse.json", None, ref, "reverse:"),
        ("自生成正向 12 例（种子 40/50/60，150 s）", "forward12.json", "forward12_new_final.json", None, ""),
        ("自生成正向 12 例（种子 41/51/61，150 s）", "forward12_seed2.json", None, None, ""),
        ("自生成反向 12 例（150 s）", "reverse12.json", None, None, ""),
    ]
    for title, main_json, new_json, r, prefix in sets:
        rows = load(main_json)
        if not rows:
            sections.append(f"## {title}\n\n（尚未完成）\n"); continue
        rows = merge_new(rows, load(new_json) if new_json else None)
        sections.append(f"## {title}\n\n{summary(rows, r, prefix)}\n\n{table(rows, r, prefix)}\n")
    out = B / "汇总对比.md"
    out.write_text("# 三方对比汇总\n\n" + "\n".join(sections), encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
