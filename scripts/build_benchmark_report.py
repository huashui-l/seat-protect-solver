"""Build a self-contained comparison of accepted C++ and recorded Gurobi runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid(run):
    return (run.get("complete") is True and run.get("violations") == 0
            and run.get("unassigned") == 0 and not run.get("error")
            and isinstance(run.get("score"), (int, float)) and math.isfinite(run["score"]))


def normalized_files(provenance):
    result = {}
    for key, value in provenance["files_sha256"].items():
        key = key.replace("\\", "/")
        while "//" in key:
            key = key.replace("//", "/")
        if "/data/" in key:
            result[key[key.index("/data/") + 1:]] = value
        elif key == "config":
            result[key] = value
    return result


def assemble(cpp, solvers, supplement):
    assert cpp["accepted"] and cpp["benchmark_complete"] and cpp["source_hashes_verified"]
    assert supplement["benchmark_complete"] and supplement["source_hashes_verified"]
    assert cpp["candidate_commit"] == supplement["candidate_commit"]
    assert cpp["executables"]["candidate"]["sha256"] == supplement["executables"]["candidate"]["sha256"]
    contract = normalized_files(cpp["provenance"])
    for key in ("source_2", "source_3", "source_4", "source_5"):
        other = normalized_files(solvers["provenance"][key]["run_provenance"])
        assert other and all(contract.get(name) == value for name, value in other.items()), key
    historical = {case["case_id"]: case for case in solvers["cases"]}
    assert len(historical) == len(cpp["cases"]) == 40
    cases = []
    for case in cpp["cases"]:
        cid = case["case_id"]
        old = historical[cid]
        runs = {"cpp": dict(case["runs"]["candidate"])}
        assert valid(runs["cpp"]) and runs["cpp"]["elapsed_seconds"] <= 5
        for method, prefix in (("cg_gurobi", "cg"), ("direct_gurobi", "direct")):
            for seconds in (150, 600):
                found = [r for r in old["runs"] if r["method"] == method and r["budget_seconds"] == seconds]
                assert len(found) <= 1
                if found:
                    runs[f"{prefix}{seconds}"] = dict(found[0])
                else:
                    first = runs[f"{prefix}150"]
                    assert method == "cg_gurobi" and valid(first) and first["proven_optimal"]
                    runs[f"{prefix}600"] = {**first, "carried_certified_150": True}
        reference = {key: value for key, value in case["reference"].items() if key.startswith("reference_")}
        for run in runs.values():
            run["valid"] = valid(run)
            run["gap_reference_percent"] = (max(0, reference["reference_score"] - run["score"])
                / max(1, abs(reference["reference_score"])) * 100
                if run["valid"] and reference["reference_score"] is not None else None)
            # Invalid or incomplete incumbents cannot enter score comparisons.
            if not run["valid"]:
                run["recorded_score"] = run.get("score")
                run["score"] = None
        cases.append({"case_id": cid, "corpus": case["corpus"], "direction": old["direction"],
                      "reference": reference, "runs": runs})
    current_runs = [c["runs"]["candidate"] for c in cpp["cases"]]
    current_runs += [r["runs"]["candidate"] for r in cpp["repeat_validation"]["runs"]]
    current_runs += [r["runs"]["candidate"] for r in supplement["cases"] + supplement["repeat_validation"]["runs"]]
    assert len(current_runs) == 62 and all(valid(r) and r["elapsed_seconds"] <= 5 for r in current_runs)
    repeats = []
    for item in cpp["repeat_validation"]["runs"]:
        repeats.append({"case_id": item["case_id"], "repeat": item["repeat"], "run": item["runs"]["candidate"]})
    supplemental_runs = [{"case_id": r["case_id"], "run": r["runs"]["candidate"]}
                         for r in supplement["cases"] + supplement["repeat_validation"]["runs"]]
    summaries = {}
    for method in ("cpp", "cg150", "direct150", "cg600", "direct600"):
        runs = [c["runs"][method] for c in cases]
        good = [r for r in runs if r["valid"]]
        gaps = [r["gap_reference_percent"] for r in good if r["gap_reference_percent"] is not None]
        times = [r["elapsed_seconds"] for r in runs if r.get("elapsed_seconds") is not None]
        summaries[method] = {"cases": len(runs), "valid": len(good), "reference_cases": len(gaps),
            "within_2": sum(g <= 2 + 1e-9 for g in gaps), "mean_gap": statistics.mean(gaps),
            "worst_gap": max(gaps), "median_seconds": statistics.median(times), "mean_seconds": statistics.mean(times),
            "max_seconds": max(times), "certified_optimal": sum(bool(r.get("proven_optimal")) for r in good),
            "over_nominal": sum(r["elapsed_seconds"] > r["budget_seconds"] for r in runs),
            "carried": sum(bool(r.get("carried_certified_150")) for r in runs)}
    return {"schema": "seatprotect_benchmark_report_v1", "report_date": "2026-10-10",
        "cpp_commit": cpp["candidate_commit"], "production_commit": cpp["promotion"]["documentation_commit"],
        "solver_commit": solvers["provenance"]["source_2"]["run_provenance"]["commit"],
        "cpp_executable": cpp["executables"]["candidate"], "build": cpp["build"],
        "contract_hashes_verified": True, "contract_files_sha256": contract,
        "current_cpp_validation": {"invocations": len(current_runs), "paired_invocations": 124,
            "max_seconds": max(r["elapsed_seconds"] for r in current_runs),
            "max_native_score_error": max(r["native_score_error"] for r in current_runs)},
        "cases": cases, "summaries": summaries, "repeats": repeats, "supplemental_runs": supplemental_runs,
        "solver_provenance": {k: v for k, v in solvers["provenance"].items() if k != "source_1"}, "timing_contract": cpp["timing_contract"]}


PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Seatprotect · 三种求解方法结果汇总</title>
<style>
:root{--ink:#17252d;--muted:#587079;--paper:#f2f5f4;--line:#dce5e2;--cpp:#087f75;--cg:#416bb0;--direct:#b86a37;--bad:#b4443b}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}main{max-width:1420px;margin:auto;padding:42px 34px 60px}h1{font-size:36px;line-height:1.25;margin:12px 0}h2{font-size:22px;margin:0 0 15px}h3{font-size:17px;margin:0 0 10px}p{margin:10px 0}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--cpp);font-weight:700}.lead{max-width:1000px;color:var(--muted);font-size:16px}.chip{display:inline-block;background:#e2eeea;border:1px solid #c9ddd5;border-radius:20px;padding:3px 11px;font-size:12px;margin:0 5px 6px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin:25px 0}.tile,.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:23px}.tile strong{display:block;font-size:30px;font-weight:650;line-height:1.5}.tile small,.muted{color:var(--muted)}.tile .label{font-size:13px}.accent{color:var(--cpp)}.warning{background:#fff4e5;border-left:4px solid #c98a35;padding:14px 18px;border-radius:6px;margin:20px 0;color:#66491f}.panel{margin:20px 0}.method-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.method{border-top:4px solid var(--cpp);padding:15px;background:#f7faf9;border-radius:5px}.method.cg{border-color:var(--cg)}.method.direct{border-color:var(--direct)}.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}label{font-size:13px}select,input,button{font:inherit;border:1px solid #c9d6d0;border-radius:6px;background:white;color:var(--ink);padding:8px 10px}button{cursor:pointer}button:hover{background:#eaf3ee}input{width:260px}.scroller{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:right;padding:11px 12px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:top}th{background:#edf3f0;font-size:12px;color:#42615c}th:first-child,td:first-child{text-align:left}tbody tr:hover{background:#f3f8f5}.bad{color:var(--bad)}.good{color:var(--cpp)}.best{font-weight:700;background:#e9f5ef}.sub{font-size:11px;color:var(--muted)}.note{font-size:13px;color:var(--muted)}.charts{display:grid;grid-template-columns:1.15fr 1fr;gap:20px}.charts .panel{min-width:0}.barrow{display:grid;grid-template-columns:190px 1fr 58px;gap:9px;align-items:center;font-size:12px;margin:9px 0}.bartrack{background:#e9efeb;height:12px;position:relative;border-radius:3px}.barfill{background:var(--cpp);height:100%;border-radius:3px;min-width:1px}.target{position:absolute;top:-3px;bottom:-3px;width:1px;background:#c58530}.legend{display:flex;gap:15px;flex-wrap:wrap;font-size:12px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}svg{display:block;width:100%;height:auto}.pair-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0}.pair{padding:16px;border:1px solid var(--line);border-radius:8px}.pair b{display:block;font-size:20px}.detail{white-space:pre-wrap;word-break:break-word;background:#f3f6f4;padding:14px;border-radius:7px;font:12px/1.6 Consolas,monospace}details{margin:12px 0}summary{cursor:pointer;color:var(--cpp)}a{color:#16685d}code{overflow-wrap:anywhere;font-size:12px}.footer{font-size:12px;color:var(--muted);margin-top:30px}.no-print{display:flex;gap:10px}.empty{text-align:center;padding:30px;color:var(--muted)}
@media(max-width:900px){main{padding:25px 17px}.grid{grid-template-columns:repeat(2,1fr)}.charts{grid-template-columns:1fr}.method-grid{grid-template-columns:1fr}h1{font-size:29px}.panel{padding:18px}.barrow{grid-template-columns:170px 1fr 54px}}
@media(max-width:520px){.grid{gap:9px}.tile{padding:14px}.tile strong{font-size:25px}.pair-grid{grid-template-columns:1fr}.barrow{grid-template-columns:145px 1fr 48px;font-size:11px}input{width:100%}.controls label{max-width:100%}h1{font-size:25px}.no-print{flex-wrap:wrap}}
@media print{body{background:white}main{padding:0}.no-print,.controls{display:none}.panel,.tile{break-inside:avoid}.scroller{overflow:visible}table{font-size:9px}th,td{padding:5px}.charts{grid-template-columns:1fr}.grid{grid-template-columns:repeat(4,1fr)}}
</style></head><body><main>
<header><div class="eyebrow">SEATPROTECT / COMPUTATIONAL RESULTS / 2026.10.10</div>
<h1>三种求解方法，当前结果如何？</h1>
<p class="lead">C++ 启发式已将多数案例压到参考差距 2% 以内。当前正式版本在 40 个案例上全部完整合法，39 个有列生成整数参考结果的案例中有 38 个达标；剩余难点是 WB d100 的稳定性。</p>
<span class="chip">Formal24 · 24 例</span><span class="chip">D4 · 16 例</span><span class="chip">原始软评分 · 越高越好</span><span class="chip">正式 C++ 版本 03d36a8</span>
<div class="no-print"><button id="download">下载报告数据 JSON</button><button id="print">打印 / 保存 PDF</button></div></header>
<section class="grid" id="headline"></section>
<div class="warning"><b>目标尚未全部达成。</b>WB d100 主测试 gap 为 2.316%，重复测试有波动。本文的 gap 使用冻结的列生成完整合法整数解作参考，不是已证明的全局最优性差距；优于参考的解记为 0%，并保留原始分数供比较。forward:full_edge 缺少有效列生成参考，gap 显示“无参考”。</div>
<section class="panel"><h2>比较口径与方法</h2><div class="method-grid">
<article class="method"><h3>C++ 启发式 · 5 秒</h3><p>当前验收版本：构造与修复、局部搜索、结构化/几何模式、资源占用者互补迁移、受限 Gurobi 模式主问题。</p><p class="note">5 秒约束覆盖完整进程调用，包含输入准备、求解、工作线程结束及结果返回；独立 Python 评估在计时外。本文不逐案例挑选重复运行的最好结果。</p></article>
<article class="method cg"><h3>Gurobi 列生成 · 150 / 600 秒</h3><p>使用已有同输入、同评分配置的 Gurobi 列生成记录。主档 150 秒；延长档只对尚未获证书的案例补测 600 秒。</p><p class="note">12 个未补测案例已在 150 秒记录为最优，延长视图沿用其原始结果和耗时，并明确标注。池内优化界不能直接当作原问题全局界。</p></article>
<article class="method direct"><h3>Gurobi 直接求解 · 150 / 600 秒</h3><p>使用已有直接模型记录；150 秒和 600 秒分别展示，全部案例均有记录。展示独立评估得到的合法整数解分数。</p><p class="note">名义求解预算与端到端耗时分开列示。150 秒档 40/40、600 秒档 39/40 的实际耗时超过名义预算；不可据此宣称三法满足同一硬截止时间。</p></article>
</div><p class="note">求解器记录来自提交 903b897；C++ 算法来自 03d36a8（正式分支文档提交 2505861）。构建报告时已核对各记录的共享输入文件及基础评分配置 SHA-256；这是既有实验汇总，生成报告没有重新运行 150/600 秒求解器。</p></section>
<section class="panel"><h2>总体比较</h2><div class="controls"><label>求解器档位 <select id="budget"><option value="150">主测试 · 150 秒</option><option value="600">延长档 · 600 秒 / 已证最优沿用 150 秒</option></select></label><label>案例集 <select id="corpus"><option value="all">全部 40 例</option><option value="formal24">Formal24 · 24 例</option><option value="d4">D4 · 16 例</option></select></label></div>
<div class="scroller"><table><thead><tr><th>方法 / 记录预算</th><th>完整合法</th><th>gap ≤2%</th><th>平均参考 gap</th><th>最差参考 gap</th><th>耗时中位数</th><th>最大实际耗时</th><th>实际耗时超名义预算</th><th>记录为已证最优</th></tr></thead><tbody id="summary"></tbody></table></div>
<div class="pair-grid" id="pairs"></div><p class="note">胜 / 平 / 负仅比较双方都有完整合法解的案例，按分数差 10⁻⁶ 判定；不将失败案例填成零分。不同实例的分数尺度不同，因此以逐例结果和 gap 为主，不把总分大小当作唯一质量结论。无效结果不进入 gap 和分数统计。gap = max(0, F参考 − F方法) / max(1, |F参考|) × 100%。延长档 CG 的平均 gap 为 0%，是因为该档合法解构成了冻结参考本身，不表示全部案例已证明全局最优。</p></section>
<section class="charts"><article class="panel"><h2>C++：参考差距最大的案例</h2><p class="note">显示当前案例集前 10 个有参考的案例，橙线为 2% 目标。</p><div id="gapchart"></div></article>
<article class="panel"><h2>时间与质量</h2><p class="note">横轴为实际耗时的对数刻度；纵轴为同一冻结参考的 gap。悬停查看逐例数据。</p><div class="legend"><span><i class="dot" style="background:var(--cpp)"></i>C++</span><span><i class="dot" style="background:var(--cg)"></i>列生成</span><span><i class="dot" style="background:var(--direct)"></i>直接求解</span></div><div id="scatter"></div></article></section>
<section class="panel"><h2>40 个案例逐例比较</h2><div class="controls"><label>搜索 <input id="search" placeholder="案例 ID，例如 wb343 或 full_edge"></label><label>关注范围 <select id="focus"><option value="all">所有案例</option><option value="miss">C++ gap 超过 2%</option><option value="invalid">求解器结果无效</option><option value="noref">缺少参考</option></select></label><label>排序 <select id="sort"><option value="case">案例 ID</option><option value="gap">C++ gap 从大到小</option><option value="delta">C++ 相对列生成分数差从小到大</option></select></label><span id="count" class="muted"></span></div>
<div class="scroller"><table><thead><tr><th>案例</th><th>C++ 分数<br>实际耗时 / gap</th><th>列生成分数<br>实际耗时 / gap</th><th>直接求解分数<br>实际耗时 / gap</th><th>C++ − 列生成</th><th>C++ − 直接求解</th><th>冻结参考分数</th><th>明细</th></tr></thead><tbody id="cases"></tbody></table></div><p class="note">绿色背景为该行完整合法解中的最高分，允许并列。每个方法均显示完整性、未分配数、硬违规数、求解器状态；展开明细可见各项软评分、预算、记录来源及证书字段。</p></section>
<section class="panel"><h2>重复运行与当前限制</h2><p>当前 C++ 版本验收覆盖 <b>62 次调用</b>：40 例主测试、6 个固定案例各 3 次重复，以及同一满载案例的 4 次补充验证。与旧基线配对合计 124 次；当前版本 62 次全部完整合法、原生与独立评分一致，最长耗时 <b id="validationtime"></b>。</p><div class="scroller"><table><thead><tr><th>重复案例</th><th>次数</th><th>最低 / 最高分</th><th>平均分</th><th>gap ≤2% 次数</th><th>最大耗时</th></tr></thead><tbody id="repeats"></tbody></table></div><p class="note">本表为预定的 6 个案例各 3 次重复，补充验证单列保存在证据 JSON 中；重复统计不覆盖主测试列。近期多起点搜索、反向并列排序、照护组并行搜索，以及补充非价值改善互补模式的试验，均未稳定胜过正式基线；这些未验收版本未混入上面的三法比较。</p></section>
<section class="panel"><h2>来源与可追溯性</h2><p class="note">报告不读取历史分配作为启发式输入；这里的实验文件仅用于离线汇总。参考为 39 个案例在冻结的列生成 150/600 秒合法整数记录中较高的分数。</p><div id="sources"></div><details><summary>查看版本、构建、配置与二进制哈希</summary><pre class="detail" id="provenance"></pre></details><details><summary>查看共享输入与基础配置 SHA-256</summary><pre class="detail" id="hashes"></pre></details><p class="note">HTML 内嵌标准化数据，无需网络或外部图表库；顶部可下载 JSON。旁边的 evidence 目录保留生成本报告的原始实验记录。</p></section>
<footer class="footer">Seatprotect · 2026-10-10 · 结果快照，非实时状态。构建程序：scripts/build_benchmark_report.py。</footer>
</main><script type="application/json" id="report-data">__DATA__</script><script>
'use strict';
const D=JSON.parse(document.getElementById('report-data').textContent), $=id=>document.getElementById(id);
const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,n=3)=>x==null?'—':Number(x).toLocaleString('en-US',{minimumFractionDigits:n,maximumFractionDigits:n});
const gap=r=>r.gap_reference_percent==null?'无参考':fmt(r.gap_reference_percent)+'%';
const mean=a=>a.reduce((s,v)=>s+v,0)/a.length, median=a=>{a=[...a].sort((x,y)=>x-y);return (a[Math.floor((a.length-1)/2)]+a[Math.floor(a.length/2)])/2;};
const labels={cpp:'C++ · 5s',cg150:'列生成 · 150s',cg600:'列生成 · 延长档',direct150:'直接求解 · 150s',direct600:'直接求解 · 600s'};
const colors={cpp:'#087f75',cg:'#416bb0',direct:'#b86a37'};
const keys=()=>['cpp','cg'+$('budget').value,'direct'+$('budget').value];
const subset=()=>D.cases.filter(c=>$('corpus').value==='all'||c.corpus===$('corpus').value);
function stat(cs,k){let rs=cs.map(c=>c.runs[k]),good=rs.filter(r=>r.valid),gs=good.map(r=>r.gap_reference_percent).filter(x=>x!=null),ts=rs.map(r=>r.elapsed_seconds).filter(x=>x!=null);return {legal:good.length,n:rs.length,gs,within:gs.filter(x=>x<=2+1e-9).length,mean:mean(gs),worst:Math.max(...gs),median:median(ts),max:Math.max(...ts),over:rs.filter(r=>r.elapsed_seconds>r.budget_seconds).length,cert:good.filter(r=>r.proven_optimal).length,carried:rs.filter(r=>r.carried_certified_150).length};}
function render(){let cs=subset(),ks=keys();$('summary').innerHTML=ks.map(k=>{let s=stat(cs,k);return `<tr><td><b>${labels[k]}</b>${s.carried?`<div class="sub">${s.carried} 例沿用已证最优的 150s 记录</div>`:''}</td><td>${s.legal}/${s.n}</td><td>${s.within}/${s.gs.length}</td><td>${fmt(s.mean)}%</td><td>${fmt(s.worst)}%</td><td>${fmt(s.median)}s</td><td>${fmt(s.max)}s</td><td>${s.over}/${s.n}</td><td>${s.cert}</td></tr>`;}).join('');
$('pairs').innerHTML=ks.slice(1).map(k=>{let wins=0,ties=0,losses=0,excluded=0;for(let c of cs){let a=c.runs.cpp,b=c.runs[k];if(!a.valid||!b.valid){excluded++;continue;}let d=a.score-b.score;if(d>1e-6)wins++;else if(d< -1e-6)losses++;else ties++;}return `<div class="pair"><span>C++ 对比 ${labels[k]}</span><b><span class="good">${wins} 胜</span> · ${ties} 平 · <span class="bad">${losses} 负</span></b><span class="note">有效配对 ${wins+ties+losses} 例；无效排除 ${excluded} 例。</span></div>`;}).join('');
let rows=cs.filter(c=>c.runs.cpp.gap_reference_percent!=null).sort((a,b)=>b.runs.cpp.gap_reference_percent-a.runs.cpp.gap_reference_percent).slice(0,10),max=Math.max(2.5,...rows.map(c=>c.runs.cpp.gap_reference_percent));
$('gapchart').innerHTML=rows.map(c=>`<div class="barrow"><span title="${esc(c.case_id)}">${esc(c.case_id.replace('d4:',''))}</span><div class="bartrack"><span class="target" style="left:${200/max}%"></span><div class="barfill" style="width:${c.runs.cpp.gap_reference_percent/max*100}%;background:${c.runs.cpp.gap_reference_percent>2?'#bf7548':colors.cpp}"></div></div><b>${fmt(c.runs.cpp.gap_reference_percent,2)}%</b></div>`).join('');
scatter(cs,ks);renderCases();}
function scatter(cs,ks){let pts=[];for(let c of cs)for(let k of ks){let r=c.runs[k];if(r.valid&&r.gap_reference_percent!=null)pts.push({c,k,r});}let ymax=Math.max(3,...pts.map(p=>p.r.gap_reference_percent)),xmax=Math.max(900,...pts.map(p=>p.r.elapsed_seconds)),W=520,H=275,L=48,T=16,B=230,R=500,x=v=>L+(Math.log10(Math.max(1,v))/Math.log10(xmax))*(R-L),y=v=>B-(v/ymax)*(B-T);let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="实际耗时与参考 gap 散点图">`;for(let t of [0,2,ymax]){s+=`<line x1="${L}" y1="${y(t)}" x2="${R}" y2="${y(t)}" stroke="${t===2?'#d9a961':'#e0e8e4'}" ${t===2?'stroke-dasharray="4 4"':''}/><text x="${L-7}" y="${y(t)+4}" text-anchor="end" font-size="11" fill="#587079">${fmt(t,1)}%</text>`;}for(let t of [1,5,30,150,600])s+=`<text x="${x(t)}" y="${B+22}" text-anchor="middle" font-size="11" fill="#587079">${t}s</text>`;for(let {c,k,r} of pts)s+=`<circle cx="${x(r.elapsed_seconds)}" cy="${y(r.gap_reference_percent)}" r="3.5" fill="${colors[k.replace(/\d/g,'')]}" opacity=".65"><title>${esc(c.case_id)} / ${labels[k]} / ${fmt(r.elapsed_seconds)}s / gap ${gap(r)}</title></circle>`;s+=`<text x="${(R+L)/2}" y="271" text-anchor="middle" font-size="11" fill="#587079">实际耗时（对数刻度）</text></svg>`;$('scatter').innerHTML=s;}
function renderCases(){let ks=keys(),query=$('search').value.trim().toLowerCase(),focus=$('focus').value;let cs=subset().filter(c=>c.case_id.toLowerCase().includes(query)).filter(c=>focus==='all'||focus==='miss'&&c.runs.cpp.gap_reference_percent>2||focus==='invalid'&&ks.some(k=>!c.runs[k].valid)||focus==='noref'&&c.reference.reference_score==null);let sort=$('sort').value;cs.sort((a,b)=>sort==='gap'?(b.runs.cpp.gap_reference_percent??-1)-(a.runs.cpp.gap_reference_percent??-1):sort==='delta'?delta(a,ks[1])-delta(b,ks[1]):a.case_id.localeCompare(b.case_id));$('count').textContent=`显示 ${cs.length} / ${subset().length} 例`;
$('cases').innerHTML=cs.map(c=>{let best=Math.max(...ks.map(k=>c.runs[k].valid?c.runs[k].score:-Infinity));let cells=ks.map(k=>{let r=c.runs[k];return `<td class="${r.valid&&Math.abs(r.score-best)<1e-6?'best':''}"><b>${r.valid?fmt(r.score):'<span class="bad">无有效完整解</span>'}</b><div class="sub">${fmt(r.elapsed_seconds)}s / ${r.valid?gap(r):'不计 gap'}</div><div class="sub">${r.valid?'合法':'无效'} · 未分配 ${r.unassigned??'—'} · 违规 ${r.violations??'—'}</div><div class="sub">${esc(r.solver_status||r.status||'—')}${r.carried_certified_150?' · 沿用150s':''}</div></td>`;}).join('');return `<tr><td><b>${esc(c.case_id)}</b><div class="sub">${c.corpus==='formal24'?'Formal24':'D4'}</div></td>${cells}<td>${deltaCell(c,ks[1])}</td><td>${deltaCell(c,ks[2])}</td><td>${fmt(c.reference.reference_score)}<div class="sub">${c.reference.reference_score==null?'无完整合法 CG 参考':`CG 整数参考 · ${c.reference.reference_budget}s`}</div></td><td><details><summary>展开</summary><pre class="detail">${esc(JSON.stringify({case_id:c.case_id,reference:c.reference,runs:Object.fromEntries(ks.map(k=>[k,c.runs[k]]))},null,2))}</pre></details></td></tr>`;}).join('')||'<tr><td colspan="8" class="empty">没有符合筛选条件的案例。</td></tr>';}
function delta(c,k){return c.runs[k].valid?c.runs.cpp.score-c.runs[k].score:Infinity;}
function deltaCell(c,k){let d=delta(c,k);return Number.isFinite(d)?`<span class="${d>1e-6?'good':d< -1e-6?'bad':''}">${d>0?'+':''}${fmt(d)}</span>`:'不可比较';}
const S=D.summaries.cpp;$('headline').innerHTML=[['完整合法',`${S.valid}/${S.cases}`,'主测试；零未分配、零硬违规'],['参考 gap ≤2%',`${S.within_2}/${S.reference_cases}`,'1 例缺少有效 CG 参考'],['平均 / 最差 gap',`${fmt(S.mean_gap)}%`,'最差 '+fmt(S.worst_gap)+'%'],['完整进程最大耗时',`${fmt(S.max_seconds)}s`,'C++ 主测试；硬上限 5.0s']].map(([label,value,note])=>`<article class="tile"><span class="label">${label}</span><strong class="accent">${value}</strong><small>${note}</small></article>`).join('');
$('validationtime').textContent=fmt(D.current_cpp_validation.max_seconds)+' 秒';let grouped={};for(let r of D.repeats)(grouped[r.case_id]??=[]).push(r.run);$('repeats').innerHTML=Object.entries(grouped).map(([cid,rs])=>{let ref=D.cases.find(c=>c.case_id===cid).reference.reference_score,gs=rs.map(r=>maxgap(ref,r.score));return `<tr><td>${esc(cid)}</td><td>${rs.length}</td><td>${fmt(Math.min(...rs.map(r=>r.score)))} / ${fmt(Math.max(...rs.map(r=>r.score)))}</td><td>${fmt(mean(rs.map(r=>r.score)))}</td><td>${gs.filter(g=>g<=2+1e-9).length}/${gs.length}</td><td>${fmt(Math.max(...rs.map(r=>r.elapsed_seconds)))}s</td></tr>`;}).join('');function maxgap(ref,score){return Math.max(0,ref-score)/Math.max(1,Math.abs(ref))*100;}
$('sources').innerHTML=D.source_files.map(f=>`<p><a href="${esc(f.relative_path)}">${esc(f.label)}</a><br><code>SHA-256 ${f.sha256}</code></p>`).join('');$('provenance').textContent=JSON.stringify({cpp_algorithm_commit:D.cpp_commit,production_documentation_commit:D.production_commit,solver_commit:D.solver_commit,cpp_executable:D.cpp_executable,build:D.build,validation:D.current_cpp_validation,timing_contract:D.timing_contract},null,2);$('hashes').textContent=JSON.stringify(D.contract_files_sha256,null,2);
for(let id of ['budget','corpus'])$(id).addEventListener('change',render);for(let id of ['focus','sort'])$(id).addEventListener('change',renderCases);$('search').addEventListener('input',renderCases);$('print').onclick=()=>window.print();$('download').onclick=()=>{let url=URL.createObjectURL(new Blob([JSON.stringify(D,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='seatprotect-benchmark-2026-10-10.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};render();
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpp-evidence", type=Path, required=True)
    parser.add_argument("--solver-evidence", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = assemble(read(args.cpp_evidence), read(args.solver_evidence), read(args.supplement))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = args.output.parent / "evidence"
    evidence.mkdir(exist_ok=True)
    data["source_files"] = []
    for source, filename, label in ((args.cpp_evidence, "cpp-accepted.json", "C++ 当前版本主测试与固定重复记录"),
            (args.solver_evidence, "gurobi-comparison.json", "三法历史记录（报告仅取其中 CG / 直接求解列）"),
            (args.supplement, "cpp-full-load-supplement.json", "C++ 满载补充配对验收记录")):
        target = evidence / filename
        target.write_bytes(source.read_bytes())
        data["source_files"].append({"label": label, "relative_path": "evidence/" + filename, "sha256": digest(target)})
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    args.output.write_text(PAGE.replace("__DATA__", encoded), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cases": len(data["cases"]), "summaries": data["summaries"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
