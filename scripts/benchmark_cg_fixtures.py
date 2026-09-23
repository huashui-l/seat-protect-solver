"""公开验证集（tests/fixtures/general_validation_suite）上的三方对比。"""
from __future__ import annotations

import argparse, json, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.general_validation_case_factory import materialize_cases  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "benchmark_cg_gurobi.py")
bench = importlib.util.module_from_spec(spec); spec.loader.exec_module(bench)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--heuristic-seconds", type=float, default=5.0)
    ap.add_argument("--methods", default="heuristic,legacy,new")
    ap.add_argument("--output", required=True)
    ap.add_argument("--json-output")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    methods = args.methods.split(",")
    rows = []
    for case in materialize_cases():
        if not case["expectedValid"]:
            continue
        new, old, groups = case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"], case["groupsData"]
        row = {"instance": case["id"], "pax": sum(len(g["psrs"]) for g in groups), "groups": len(groups),
               "max_group": max(len(g["psrs"]) for g in groups)}
        for meth in methods:
            fn = {"heuristic": bench.run_heuristic, "legacy": bench.run_legacy, "new": bench.run_new}[meth]
            secs = args.heuristic_seconds if meth == "heuristic" else args.time_limit
            try:
                row[meth] = fn(new, old, groups, cfg, secs)
            except Exception as exc:
                row[meth] = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-600:]}
            print(f"[{case['id']}] {meth}: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row[meth].items() if k not in ('trace','hard_detail')}, ensure_ascii=False)}", flush=True)
        rows.append(row)
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 公开验证集三方对比（时限 {args.time_limit:.0f}s，启发式 {args.heuristic_seconds:.0f}s）", "",
             "| 算例 | 人数/组数/最大组 | 方法 | 完整合法 | 软分 | s / v / p / c / b | U（证书） | 安全U | gap% | 耗时 s |",
             "|---|---|---|---|---:|---|---|---|---:|---:|"]
    for row in rows:
        for meth in methods:
            m = row.get(meth, {})
            if "error" in m:
                lines.append(f"| {row['instance']} | {row['pax']}/{row['groups']}/{row['max_group']} | {meth} | 出错 | {m['error'][:60]} | | | | | |"); continue
            legal = "是" if m["violations"] == 0 and m["unassigned"] == 0 else f"否({m['violations']}违规,{m['unassigned']}未分)"
            u = "—" if m["U"] is None else f"{m['U']:.4f}{'✓' if m['U_cert'] else ''}"
            su = "—" if m.get("safeU") is None else f"{m['safeU']:.4f}"
            ref = m["U"] if m["U"] is not None else m.get("safeU")
            gap = "—" if ref is None else f"{100*(ref - m['score'])/max(1,abs(m['score'])):.2f}"
            lines.append(f"| {row['instance']} | {row['pax']}/{row['groups']}/{row['max_group']} | {meth} | {legal} | {m['score']:.4f} | "
                         f"{m['s']:.2f} / {m['v']:.2f} / {m['p']:.2f} / {m['c']:.2f} / {m['b']:.3f} | {u} | {su} | {gap} | {m['time']:.1f} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
