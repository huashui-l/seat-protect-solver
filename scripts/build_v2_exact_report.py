"""Validate and render the V2 comparison without treating integer references as optima."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path

from scripts.benchmark_v2_exact import ROOT, needs_followup, sha


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def comparison(audit, benchmark, verify_files=True):
    cases = {c["case_id"]: c for c in audit["cases"]}
    assert len(cases) == 72 and audit["audit_complete"]
    records = {}
    for record in benchmark["records"]:
        key = (record["case_id"], record["method"], record["budget_seconds"])
        assert key not in records and key[0] in cases and key[1] in ("direct_gurobi", "cg_gurobi") and key[2] in (60, 900)
        if verify_files:
            if record["raw_sha256"]:
                assert sha(ROOT / record["raw_output"]) == record["raw_sha256"]
            folder = (ROOT / record["raw_output"]).parent
            assert sha(folder / "config.json") == record["config_sha256"]
            assert sha(folder / "solver.log") == record["log_sha256"]
            assert load(folder / "record.json") == record, "per-run checkpoint disagrees with aggregate"
        case = cases[key[0]]
        for name in ("groups", "old_seatmap", "new_seatmap"):
            assert record[name + "_sha256"] == case[name + "_sha256"]
        if record["valid_for_comparison"]:
            assert record["complete_legal"] and record["objective_parity"] and record["score"] is not None
        else:
            assert record["score"] is None
        if record["proven_optimal"]:
            assert record["valid_for_comparison"] and abs(record["certified_upper_bound"] - record["score"]) <= 1e-5
        records[key] = record
    expected = {(cid, method, 60) for cid in cases for method in ("direct_gurobi", "cg_gurobi")}
    for key in list(expected):
        if key in records and needs_followup(records[key]):
            expected.add((key[0], key[1], 900))
    assert set(records) <= expected, "unexpected 900s run or inconsistent first-tier selection"
    if benchmark["complete"]:
        assert set(records) == expected, "completed batch is missing required experiments"
    rows = []
    for cid, case in cases.items():
        inherited = case["heuristic"]
        rescore = case["independent_rescore"]
        heuristic = dict(method="collaborator_heuristic", budget_seconds=60,
            score=rescore["score"] if inherited["valid"] else None,
            valid_for_comparison=inherited["valid"], proven_optimal=False,
            solver_status=inherited["status"], solver_seconds=inherited["seconds"],
            hard_violations=rescore["hard_violations"], unassigned=rescore["unassigned"],
            inherited=True, source_batch="focused60")
        if inherited["valid"]:
            assert rescore["hard_violations"] == rescore["unassigned"] == 0
            assert abs(rescore["score"] - inherited["score"]) <= 1e-6
        exact = [r for key, r in records.items() if key[0] == cid]
        scores = [r["score"] for r in [heuristic, *exact] if r["valid_for_comparison"]]
        best = max(scores) if scores else None
        proofs = [r for r in exact if r["proven_optimal"]]
        bounds = [r["certified_upper_bound"] for r in exact if r["certified_upper_bound"] is not None]
        if best is not None:
            assert all(bound >= best - 1e-5 for bound in bounds), (cid, "global bound below legal incumbent")
            assert all(abs(r["score"] - best) <= 1e-5 for r in proofs), (cid, "claimed optimum contradicted by legal incumbent")
        infeasible = case["inherited_infeasibility_certificate"]
        assert not infeasible or best is None, (cid, "infeasibility certificate contradicted by legal incumbent")
        for budget in (60, 900):
            methods = {"collaborator_heuristic": dict(heuristic)}
            for method in ("direct_gurobi", "cg_gurobi"):
                record = records.get((cid, method, budget))
                if record is None:
                    first = records.get((cid, method, 60))
                    unnecessary = budget == 900 and first is not None and not needs_followup(first)
                    record = dict(method=method, budget_seconds=budget, score=None,
                        valid_for_comparison=False, proven_optimal=False,
                        solver_status="not_required" if unnecessary else "pending")
                    if unnecessary:
                        record["not_run_reason"] = ("首轮已证明最优" if first["proven_optimal"] else
                            "首轮方法已证明不可行" if first["proven_infeasible_by_method"] else "外部证书已证明不可行")
                methods[method] = dict(record)
            for record in methods.values():
                record["gap_to_reference_percent"] = ((best-record["score"])/max(1, abs(best))*100
                    if best is not None and record["valid_for_comparison"] else None)
            rows.append(dict(case_id=cid, tier_seconds=budget, reference_score=best,
                reference_type="certified_optimum" if proofs else "best_known_legal_integer" if best is not None else "none",
                certified_upper_bound=min(bounds) if bounds else None,
                external_infeasibility_certificate=infeasible, methods=methods))
    summaries = []
    for budget in (60, 900):
        for method in ("collaborator_heuristic", "direct_gurobi", "cg_gurobi"):
            if method == "collaborator_heuristic" and budget == 900:
                continue
            selected = [r["methods"][method] for r in rows if r["tier_seconds"] == budget]
            summaries.append(dict(method=method, tier_seconds=budget, cases=len(selected),
                valid=sum(r["valid_for_comparison"] for r in selected),
                proven_optimal=sum(r["proven_optimal"] for r in selected),
                proven_infeasible_by_method=sum(bool(r.get("proven_infeasible_by_method")) for r in selected),
                failures=sum(bool(r.get("error")) for r in selected),
                process_over_budget=sum(bool(r.get("process_over_budget")) for r in selected),
                statuses=dict(Counter(r["solver_status"] for r in selected))))
    paired = []
    for left, right in (("collaborator_heuristic", "direct_gurobi"),
                        ("collaborator_heuristic", "cg_gurobi"), ("direct_gurobi", "cg_gurobi")):
        shared = [(row["methods"][left]["score"], row["methods"][right]["score"])
            for row in rows if row["tier_seconds"] == 60
            and row["methods"][left]["valid_for_comparison"] and row["methods"][right]["valid_for_comparison"]]
        paired.append(dict(left=left, right=right, shared_legal_cases=len(shared),
            left_wins=sum(a > b + 1e-6 for a, b in shared), ties=sum(abs(a-b) <= 1e-6 for a, b in shared),
            right_wins=sum(b > a + 1e-6 for a, b in shared),
            mean_left_advantage_percent=sum((a-b)/max(1, abs(b))*100 for a, b in shared)/len(shared) if shared else None))
    return dict(complete=benchmark["complete"], metadata=benchmark["metadata"],
        reference_policy="Best independently legal integer across all recorded methods and budgets; called an optimum only when an audited global certificate closes. Safe bounds excluded.",
        summaries=summaries, paired_60s=paired, rows=rows)


LABELS = {"collaborator_heuristic": "合作者启发式 60s", "direct_gurobi": "直接 MIP", "cg_gurobi": "列生成"}


def cell(record):
    escape = html.escape
    if record["score"] is None:
        status = escape(str(record["solver_status"]))
        if record.get("not_run_reason"):
            status += '<br>' + escape(record['not_run_reason'])
        if record.get("proven_infeasible_by_method"):
            status += '<br>方法已证明不可行'
        if record.get("external_infeasibility_certificate"):
            status += '<br>外部匹配证书已确认不可行'
        if record.get("error"):
            status += '<br>运行失败：' + escape(record['error'])
        return status + '<details><summary>证据字段</summary><pre>' + escape(json.dumps(record, ensure_ascii=False, indent=2)) + '</pre></details>'
    status = "已证明最优" if record["proven_optimal"] else "合法可行，未证明最优"
    bound = record.get("certified_upper_bound")
    timing = record.get("process_seconds", record.get("solver_seconds"))
    detail = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)
    return (f'<strong>{record["score"]:.6f}</strong><br>{status}'
        f'<br>参考差距 {record["gap_to_reference_percent"]:.3f}%'
        + (f'<br>认证上界 {bound:.6f}' if bound is not None else '')
        + (f'<br>耗时 {timing:.3f}s' if timing is not None else '')
        + (' · 超预算' if record.get('process_over_budget') else '')
        + f'<details><summary>证据字段</summary><pre>{escape(detail)}</pre></details>')


def render(data):
    escape = html.escape
    stats = ''.join(f'<tr><td>{LABELS[s["method"]]}</td><td>{s["tier_seconds"]}</td>'
        f'<td>{s["valid"]}</td><td>{s["proven_optimal"]}</td><td>{s["proven_infeasible_by_method"]}</td>'
        f'<td>{s["failures"]}</td><td>{s["process_over_budget"]}</td></tr>' for s in data['summaries'])
    pairs = ''.join(f'<tr><td>{LABELS[p["left"]]} / {LABELS[p["right"]]}</td><td>{p["shared_legal_cases"]}</td>'
        f'<td>{p["left_wins"]} / {p["ties"]} / {p["right_wins"]}</td><td>'
        + (f'{p["mean_left_advantage_percent"]:.3f}%' if p['mean_left_advantage_percent'] is not None else '—')
        + '</td></tr>' for p in data['paired_60s'])
    rows = ''
    for row in data['rows']:
        reference = ('已认证最优' if row['reference_type'] == 'certified_optimum' else '最佳已知合法整数')
        if row['external_infeasibility_certificate']:
            reference = '外部匹配证书：不可行'
        rows += f'<tr data-tier="{row["tier_seconds"]}"><td>{escape(row["case_id"])}<br>{reference}'
        if row['reference_score'] is not None:
            rows += f'<br>{row["reference_score"]:.6f}'
        rows += '</td><td>' + str(row['tier_seconds']) + '</td>'
        rows += ''.join('<td>' + cell(row['methods'][m]) + '</td>' for m in LABELS) + '</tr>'
    state = '已完成' if data['complete'] else '运行中：以下是阶段快照，不能作为最终结论'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>V2 72例精确方法比较</title><style>
body{{font:15px/1.6 system-ui,sans-serif;margin:24px;color:#172536;background:#f6f8fb}}h1{{font-size:26px}}
table{{border-collapse:collapse;width:100%;min-width:800px;background:white;margin:16px 0}}th,td{{padding:10px;border:1px solid #d9e0e9;text-align:left;vertical-align:top}}th{{background:#e9eff8;position:sticky;top:0}}td:first-child{{overflow-wrap:anywhere;max-width:270px}}code{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-width:350px;font-size:11px}}.scroll{{overflow:auto}}select,input{{padding:8px;margin-right:12px}}.notice{{background:#fff3cd;padding:12px}}a{{color:#1458a5}}
</style><h1>V2 72例：启发式、直接 MIP 与列生成</h1><p class="notice">{state}</p>
<p>启发式沿用合作者 focused60，未重跑。两种精确方法先各跑72例60秒，未证明最优且无不可行证明时独立补跑900秒。下表900秒栏的启发式仍为原60秒基线；未补跑的精确方法明确标注 not_required，不用60秒记录冒充900秒记录。</p>
<p>只有完整合法且独立评分一致的解参与比较。参考取所有已记录预算的最佳合法整数，仅在认证全局界闭合时称最优。安全上界与列池最优均不构成证明。三个匹配不可行证书已独立重算；方法自身证明与外部证明分别保留。</p>
<p>合作者：Apple M5，10核，16GiB，CP-SAT最多8线程。本次：Ryzen 7 5800H，8核16线程，约16GiB，最多4例并行，每个Gurobi模型1线程。硬件不同，不能作严格同机速度比较。耗时列为本次完整求解进程时间，启发式为继承的算法报告时间；独立复核时间不计入。实际超预算如实标注。</p>
<p>受测提交 <code>{escape(data['metadata']['algorithm_commit'])}</code>。<a href="comparison.json">逐例完整字段 JSON</a> · <a href="README.md">建模审查、版本与证书说明</a></p>
<div class="scroll"><table><thead><tr><th>方法</th><th>预算秒</th><th>合法</th><th>证明最优</th><th>方法证明不可行</th><th>运行失败</th><th>进程超预算</th></tr></thead><tbody>{stats}</tbody></table></div>
<p>60秒共同合法案例比较：每对方法只比较双方均合法的同一批案例。平均优势为 (左方法得分−右方法得分)/max(1,|右方法得分|)，正值表示左方法更好；不使用900秒结果替换首轮成绩。</p>
<div class="scroll"><table><thead><tr><th>左方法 / 右方法</th><th>共同合法例数</th><th>左胜 / 平 / 右胜</th><th>左方法平均相对优势</th></tr></thead><tbody>{pairs}</tbody></table></div>
<label>预算 <select id="tier"><option value="60">60秒首轮</option><option value="900">900秒补跑</option><option value="all">全部</option></select></label><label>案例筛选 <input id="query" placeholder="方向、负载或组类型"></label>
<div class="scroll"><table id="cases"><thead><tr><th>案例与参考</th><th>精确方法预算</th><th>合作者启发式60s</th><th>直接 MIP</th><th>列生成</th></tr></thead><tbody>{rows}</tbody></table></div>
<script>const tier=document.getElementById('tier'),query=document.getElementById('query');function filter(){{for(const r of document.querySelectorAll('#cases tbody tr'))r.hidden=(tier.value!=='all'&&r.dataset.tier!==tier.value)||!r.cells[0].textContent.toLowerCase().includes(query.value.toLowerCase());}}tier.addEventListener('change',filter);query.addEventListener('input',filter);filter();</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-audit", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    benchmark = load(args.benchmark)
    assert benchmark["complete"] or args.allow_partial, "batch not complete; use --allow-partial for a labelled snapshot"
    assert sha(args.input_audit) == benchmark["metadata"]["input_audit_sha256"]
    data = comparison(load(args.input_audit), benchmark)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (args.output / "index.html").write_text(render(data), encoding="utf-8")
    print(f"Validated {len(benchmark['records'])} exact runs; complete={data['complete']}")


if __name__ == "__main__":
    main()
