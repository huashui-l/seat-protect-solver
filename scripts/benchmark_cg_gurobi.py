"""三方对比基准：项目启发式（5 s） vs 旧列生成（HiGHS，归档） vs 新列生成（Gurobi 直接定价）。

指标：完整性、硬违规分项、软分分项（s/v/p/c/b）、LP 上界及证书、gap、耗时。
用法：
    .venv/bin/python scripts/benchmark_cg_gurobi.py --instances /tmp/cgdata/forward \
        --old-seatmap /tmp/cgdata/3-4-3seatmap.json --new-seatmap /tmp/cgdata/3-3seatmap.json \
        --time-limit 180 --output reports/audit-2026-09-23/cg_gurobi_benchmark.md
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import allocation_evaluator as evaluator  # noqa: E402


def _metrics(new, old, groups, assigned, weights, cfg):
    v, u, det = evaluator.count_hard_constraint_violations(new, groups, assigned, cfg)
    s, sd = evaluator.calculate_soft_score(new, old, groups, assigned, weights, cfg)
    return {"score": s, "violations": v, "unassigned": u,
            "hard_detail": {k: x for k, x in det.items() if isinstance(x, int) and x},
            "s": sd["score_s"], "v": sd["score_v"], "p": sd["score_p"], "c": sd["score_c"], "b": sd["score_b"]}


def run_heuristic(new, old, groups, cfg, seconds):
    from src import heuristic_seat_allocator as heuristic
    c = copy.deepcopy(cfg); c["algorithm"]["business_time_limit_seconds"] = seconds
    t = time.perf_counter()
    r, _ = heuristic.run_allocation(new, old, groups, c["weights"], c)
    m = _metrics(new, old, groups, r["assigned_seats"], c["weights"], c)
    m.update({"U": None, "U_cert": False, "safeU": None, "time": time.perf_counter() - t}); return m


def run_legacy(new, old, groups, cfg, seconds):
    from archive.legacy_column_generation import exact_column_generation as legacy
    c = copy.deepcopy(cfg); c["column_generation"]["total_time_limit"] = seconds
    c["column_generation"]["show_progress"] = False
    t = time.perf_counter()
    r, _ = legacy.run_column_generation(new, old, groups, c["weights"], c)
    m = _metrics(new, old, groups, r["assigned_seats"], c["weights"], c)
    d = r["diagnostics"]
    m.update({"U": d.get("complete_column_space_lp_bound"), "U_cert": bool(d.get("complete_column_space_lp_bound_certified")),
              "safeU": d.get("safe_column_space_lp_bound"), "proven": d.get("proven_optimal"),
              "time": time.perf_counter() - t}); return m


CG_OPTIONS: dict = {}


def run_new(new, old, groups, cfg, seconds):
    from src.cg_gurobi import solve
    c = copy.deepcopy(cfg); c["cg_gurobi"] = {"total_time_limit": seconds, "pricing_time_limit": min(60.0, seconds / 3),
                                             "integer_time_limit": min(30.0, seconds / 5), "log": False, **CG_OPTIONS}
    t = time.perf_counter()
    r = solve(new, old, groups, c["weights"], c)
    m = _metrics(new, old, groups, r["assigned_seats"], c["weights"], c)
    m.update({"U": r["lp_bound"], "U_cert": bool(r["lp_bound_certified"]), "safeU": r["safe_bound"],
              "proven": bool(r.get("proven_optimal")), "bpU": r.get("bp_bound"),
              "time": time.perf_counter() - t}); return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", required=True, help="目录，含 *_groups 形式的 case JSON")
    ap.add_argument("--old-seatmap", required=True); ap.add_argument("--new-seatmap", required=True)
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--time-limit", type=float, default=180.0)
    ap.add_argument("--heuristic-seconds", type=float, default=5.0)
    ap.add_argument("--methods", default="heuristic,legacy,new")
    ap.add_argument("--only", default="", help="逗号分隔的实例名过滤")
    ap.add_argument("--output", required=True)
    ap.add_argument("--json-output")
    ap.add_argument("--cg-options", default="{}", help="JSON，覆盖新列生成的 cg_gurobi 配置")
    args = ap.parse_args()
    CG_OPTIONS.update(json.loads(args.cg_options))
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    new = json.load(open(args.new_seatmap))["seats"]; old = json.load(open(args.old_seatmap))["seats"]
    methods = args.methods.split(",")
    files = sorted(glob.glob(os.path.join(args.instances, "*.json")))
    only = {x for x in args.only.split(",") if x}
    rows = []
    for f in files:
        name = Path(f).stem
        if only and name not in only:
            continue
        groups = json.load(open(f))["groups"]
        n = sum(len(g["psrs"]) for g in groups)
        row = {"instance": name, "pax": n, "groups": len(groups), "max_group": max(len(g["psrs"]) for g in groups)}
        for meth in methods:
            fn = {"heuristic": run_heuristic, "legacy": run_legacy, "new": run_new}[meth]
            secs = args.heuristic_seconds if meth == "heuristic" else args.time_limit
            try:
                row[meth] = fn(new, old, groups, cfg, secs)
            except Exception as exc:
                row[meth] = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-800:]}
            print(f"[{name}] {meth}: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row[meth].items() if k not in ('trace','hard_detail')}, ensure_ascii=False)}", flush=True)
        rows.append(row)
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 三方对比基准（时限 {args.time_limit:.0f}s，启发式 {args.heuristic_seconds:.0f}s）", "",
             "| 实例 | 人数/组数/最大组 | 方法 | 完整合法 | 软分 | s / v / p / c / b | U（证书） | 安全U | gap% | 耗时 s |",
             "|---|---|---|---|---:|---|---|---|---:|---:|"]
    for row in rows:
        for meth in methods:
            m = row.get(meth, {})
            if "error" in m:
                lines.append(f"| {row['instance']} | {row['pax']}/{row['groups']}/{row['max_group']} | {meth} | 出错 | {m['error'][:60]} | | | | | |"); continue
            legal = "是" if m["violations"] == 0 and m["unassigned"] == 0 else f"否({m['violations']}违规,{m['unassigned']}未分)"
            u = "—" if m["U"] is None else f"{m['U']:.3f}{'✓' if m['U_cert'] else ''}"
            su = "—" if m.get("safeU") is None else f"{m['safeU']:.3f}"
            ref = m["U"] if m["U"] is not None else m.get("safeU")
            gap = "—" if ref is None else f"{100*(ref - m['score'])/max(1,abs(m['score'])):.2f}"
            lines.append(f"| {row['instance']} | {row['pax']}/{row['groups']}/{row['max_group']} | {meth} | {legal} | {m['score']:.3f} | "
                         f"{m['s']:.1f} / {m['v']:.1f} / {m['p']:.1f} / {m['c']:.1f} / {m['b']:.2f} | {u} | {su} | {gap} | {m['time']:.1f} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
