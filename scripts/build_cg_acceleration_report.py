"""Verify paired runs, explain V2 difficulty, and package reproducible evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
import math
from pathlib import Path
import statistics
import zipfile

from scripts.run_cg_acceleration_study import ROOT, read, write, digest
from scripts.benchmark_v2_exact import verified_proof
from src import allocation_evaluator as evaluator


def summary(records):
    by_case = {}
    for r in records:
        by_case.setdefault(r["contract"]["case_id"], {})[r["contract"]["variant"]] = r
    pairs = [v for v in by_case.values() if len(v) == 2]
    common = [(p["baseline"], p["hybrid"]) for p in pairs if all(r["valid"] for r in p.values())]
    out = dict(pairs=len(pairs), common_legal=len(common),
        wins=sum(y["score"]>x["score"]+1e-6 for x,y in common),
        ties=sum(abs(y["score"]-x["score"])<=1e-6 for x,y in common),
        losses=sum(y["score"]<x["score"]-1e-6 for x,y in common),
        mean_score_advantage_percent=statistics.mean((y["score"]-x["score"])/max(1,abs(x["score"]))*100 for x,y in common) if common else None)
    for variant in ("baseline", "hybrid"):
        rows = [r for r in records if r["contract"]["variant"] == variant]
        groups = [g for r in rows for g in (r["acceleration_profile"] or {}).get("groups", [])]
        masters = [m for r in rows for m in (r["acceleration_profile"] or {}).get("masters", [])]
        out[variant] = dict(attempts=len(rows), legal=sum(r["valid"] for r in rows),
            proven=sum(r["proven_optimal"] for r in rows), root_certified=sum(r["lp_bound_certified"] for r in rows),
            failures=sum(bool(r["error"]) for r in rows), over_budget=sum(r["over_budget"] for r in rows),
            process_seconds=sum(r["process_seconds"] for r in rows),
            pricer_build_seconds=sum(g["build_seconds"] for g in groups),
            warm_pricing_seconds=sum(g["warm_seconds"] for g in groups),
            exact_calls=sum(g["exact_calls"] for g in groups), exact_seconds=sum(g["exact_seconds"] for g in groups),
            heuristic_columns=sum(g["heuristic_columns"] for g in groups), heuristic_seconds=sum(g["heuristic_seconds"] for g in groups),
            enumeration_calls=sum(g.get("enumeration_calls",0) for g in groups), enumeration_seconds=sum(g.get("enumeration_seconds",0.0) for g in groups),
            lp_seconds=sum(m["lp_seconds"] for m in masters), integer_seconds=sum(m["integer_seconds"] for m in masters))
    proven_pairs = [(x,y) for x,y in common if x["proven_optimal"] and y["proven_optimal"]]
    out["both_proven_count"] = len(proven_pairs)
    out["both_proven_median_speedup"] = statistics.median(x["process_seconds"]/y["process_seconds"] for x,y in proven_pairs) if proven_pairs else None
    for field, label in (("valid", "legal"), ("proven_optimal", "proof"),
                         ("lp_bound_certified", "root_certificate")):
        out[label+"_lost_cases"] = sorted(p["baseline"]["contract"]["case_id"] for p in pairs
                                         if p["baseline"][field] and not p["hybrid"][field])
        out[label+"_gained_cases"] = sorted(p["baseline"]["contract"]["case_id"] for p in pairs
                                           if p["hybrid"][field] and not p["baseline"][field])
    return out


def baseline_repeat_variability(studies):
    repeats = {}
    names = list(studies)
    for i,name in enumerate(names):
        a = {r["contract"]["case_id"]:r for r in studies[name] if r["contract"]["variant"]=="baseline"}
        for other in names[i+1:]:
            b = {r["contract"]["case_id"]:r for r in studies[other] if r["contract"]["variant"]=="baseline"}
            assert a.keys() == b.keys()
            assert all(a[cid]["contract"][key] == b[cid]["contract"][key]
                       for cid in a for key in ("input_hashes","config_hash"))
            common = [cid for cid in a.keys() & b.keys() if a[cid]["valid"] and b[cid]["valid"]]
            changes = {cid:abs(b[cid]["score"]-a[cid]["score"])/max(1,abs(a[cid]["score"]))*100 for cid in common}
            repeats[name+" vs "+other] = dict(common_legal=len(common),
                mean_absolute_relative_score_change_percent=statistics.mean(changes.values()) if changes else None,
                changed_score_cases=sum(abs(a[cid]["score"]-b[cid]["score"])>1e-6 for cid in common),
                maximum_relative_change_case=max(changes,key=changes.get) if changes else None,
                maximum_absolute_relative_score_change_percent=max(changes.values()) if changes else None,
                legal_lost_cases=sorted(cid for cid in a if a[cid]["valid"] and not b[cid]["valid"]),
                legal_gained_cases=sorted(cid for cid in a if b[cid]["valid"] and not a[cid]["valid"]))
    return repeats


def spearman(xs,ys):
    def ranks(values):
        order=sorted(range(len(values)),key=lambda i:values[i])
        result=[0.0]*len(values)
        start=0
        while start<len(order):
            end=start+1
            while end<len(order) and values[order[end]]==values[order[start]]:end+=1
            for i in order[start:end]:result[i]=(start+end-1)/2
            start=end
        return result
    x,y=ranks(xs),ranks(ys)
    if not x:return None
    mx,my=statistics.mean(x),statistics.mean(y)
    denom=math.sqrt(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/denom if denom else None


def difficulty_gradients(records,features):
    gradients={}
    for corpus in ("formal40","v2_72"):
        selected=[r for r in records if r["contract"]["variant"]=="baseline" and
                  any(f["case_id"]==r["contract"]["case_id"] and f["corpus"]==corpus for f in features)]
        groups=[g for r in selected for g in (r["acceleration_profile"] or {}).get("groups",[]) if g["exact_calls"]]
        buckets={}
        for lo,hi in ((1,1),(2,3),(4,6),(7,10)):
            subset=[g for g in groups if lo<=g["passengers"]<=hi]
            calls=sum(g["exact_calls"] for g in subset)
            seconds=sum(g["exact_seconds"] for g in subset)
            buckets[f"{lo}-{hi}"]=dict(groups=len(subset),calls=calls,seconds=seconds,
                                       seconds_per_call=seconds/calls if calls else None)
        gradients[corpus]=dict(pnr_size_buckets=buckets,
            size_vs_mean_exact_call_spearman=spearman([g["passengers"] for g in groups],[g["exact_seconds"]/g["exact_calls"] for g in groups]),
            domain_sum_vs_mean_exact_call_spearman=spearman([g["domain_sum"] for g in groups],[g["exact_seconds"]/g["exact_calls"] for g in groups]),
            note="Descriptive association only. Time limits, dual vectors and special rules also affect pricing difficulty.")
    return gradients


def previous_difficulty():
    audit = read(ROOT/"build/v2-exact-20261012/input-audit.json")
    benchmark = read(ROOT/"build/v2-exact-20261012/benchmark.json")
    classifications = []
    for case in audit["cases"]:
        candidates = [r for r in benchmark["records"] if r["case_id"] == case["case_id"] and r["method"] == "cg_gurobi"]
        latest = max(candidates, key=lambda r:r["budget_seconds"])
        raw = read(ROOT/latest["raw_output"])
        assert digest(ROOT/latest["raw_output"]) == latest["raw_sha256"]
        command = latest["command"]
        config_path = Path(command[command.index("--config")+1])
        cfg = read(config_path)
        assert digest(config_path) == latest["config_sha256"]
        groups = read(case["groups_path"])["groups"]
        new,old = (read(case[k])["seats"] for k in ("new_seatmap_path","old_seatmap_path"))
        assignment = {tuple(map(int,k.split(":"))):s for k,s in raw["assigned_seats"].items()}
        hard,missing,_ = evaluator.count_hard_constraint_violations(new,groups,assignment,cfg)
        score,_ = evaluator.calculate_soft_score(new,old,groups,assignment,cfg["weights"],cfg)
        legal = hard == missing == 0
        parity = legal and abs(raw["total_score"]-score)<=1e-6
        assert legal == latest["complete_legal"] and parity == latest["valid_for_comparison"]
        if parity: assert abs(score-latest["score"])<=1e-6
        proven,bound = verified_proof("cg_gurobi",raw,legal,parity)
        assert proven == latest["proven_optimal"] and bound == latest["certified_upper_bound"]
        category = ("certified_infeasible" if case.get("inherited_infeasibility_certificate") else
                    "proven_optimal" if proven else
                    "integer_proof_unclosed" if raw["lp_bound_certified"] else "root_pricing_unclosed")
        classifications.append(dict(case_id=case["case_id"], category=category,
            budget=latest["budget_seconds"], root_certified=raw["lp_bound_certified"],
            independent_legal_replay=legal,config_path=str(config_path.relative_to(ROOT)),
            certified_upper_bound=latest["certified_upper_bound"], score=latest["score"],
            cabins={k:dict(termination=c["termination"],branch_and_price=c.get("branch_and_price"))
                    for k,c in raw.get("cabins",{}).items()}, raw_path=latest["raw_output"], raw_hash=digest(ROOT/latest["raw_output"])))
    return dict(counts=dict(Counter(c["category"] for c in classifications)), cases=classifications,
                known_optima={c["case_id"]:c["score"] for c in classifications if c["category"]=="proven_optimal"})


def cross_check_certificates(records):
    """A certificate must dominate every known legal incumbent of this input."""
    scores = {}
    for r in records:
        if r["valid"]:
            cid = r["contract"]["case_id"]
            scores[cid] = max(scores.get(cid, -math.inf), r["score"])
    previous = read(ROOT/"build/v2-exact-20261012/benchmark.json")["records"]
    for r in previous:
        if r["valid_for_comparison"] and r["case_id"] in scores:
            scores[r["case_id"]] = max(scores[r["case_id"]], r["score"])
    audit = read(ROOT/"build/v2-exact-20261012/input-audit.json")
    assert audit["audit_complete"]
    for case in audit["cases"]:
        h,rescore = case["heuristic"],case["independent_rescore"]
        cid = case["case_id"]
        if h["valid"] and rescore["hard_violations"] == rescore["unassigned"] == 0 and cid in scores:
            assert abs(h["score"]-rescore["score"])<=1e-6
            assert h["input_sha256"] == case["groups_sha256"]
            assert h["old_seatmap_sha256"] == case["old_seatmap_sha256"]
            assert h["new_seatmap_sha256"] == case["new_seatmap_sha256"]
            scores[cid] = max(scores[cid],rescore["score"])
    checked = safe_checked = 0
    for r in records:
        cid, bound = r["contract"]["case_id"], r["certified_upper_bound"]
        if bound is not None and cid in scores:
            assert bound >= scores[cid]-1e-5, (cid, "certificate contradicted across studies")
            checked += 1
        if r["safe_bound"] is not None and cid in scores:
            assert r["safe_bound"] >= scores[cid]-1e-5, (cid, "safe upper bound contradicted across studies")
            safe_checked += 1
    return dict(certificates_checked=checked,safe_upper_bounds_checked=safe_checked,known_legal_scores=scores,
                note="Uses original inputs only: current CG runs, prior CG/direct records and independently audited collaborator heuristic scores; never repaired cases.")


def export_revised_corpus(work, repairs):
    """Materialize a complete separate 72-case revision, with legal witnesses."""
    audit=read(ROOT/"build/v2-exact-20261012/input-audit.json")
    previous=read(ROOT/"build/v2-exact-20261012/benchmark.json")["records"]
    revised={r["case_id"]:r for r in repairs["repairs"]}
    corpus=work/"derived-v2-repairs/corpus"
    cases=[]
    write(corpus/"config.json",audit["config"])
    for case in audit["cases"]:
        repair=revised.get(case["case_id"])
        source=ROOT/repair["repaired_path"] if repair else Path(case["groups_path"])
        destination=corpus/"cases"/source.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(source.read_bytes())
        assert digest(destination)==(repair["repaired_hash"] if repair else case["groups_sha256"])
        maps={}
        for key in ("old_seatmap_path","new_seatmap_path"):
            original=Path(case[key]);target=corpus/"seatmaps"/original.name
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():assert digest(target)==digest(original)
            else:target.write_bytes(original.read_bytes())
            maps[key]=str(target.relative_to(corpus)).replace("\\","/")
        if repair:
            witness=repair["witness"]
        else:
            row=max((r for r in previous if r["case_id"]==case["case_id"] and r["method"]=="cg_gurobi" and r["valid_for_comparison"]),key=lambda r:r["budget_seconds"])
            witness=read(ROOT/row["raw_output"])["assigned_seats"]
        new=read(corpus/maps["new_seatmap_path"])["seats"]
        groups=read(destination)["groups"]
        assignment={tuple(map(int,k.split(":"))):s for k,s in witness.items()}
        hard,missing,details=evaluator.count_hard_constraint_violations(new,groups,assignment,audit["config"])
        assert hard==missing==0, (case["case_id"],details)
        witness_path=corpus/"witnesses"/source.name
        write(witness_path,dict(assigned_seats=witness,complete_legal=True,
                                soft_score_optimality_claim=False,input_hash=digest(destination)))
        cases.append(dict(case_id=case["case_id"],groups_path=str(destination.relative_to(corpus)).replace("\\","/"),
            **maps,input_sha256=digest(destination),original_sha256=case["groups_sha256"],
            old_seatmap_sha256=digest(corpus/maps["old_seatmap_path"]),
            new_seatmap_sha256=digest(corpus/maps["new_seatmap_path"]),
            changed=bool(repair),witness_path=str(witness_path.relative_to(corpus)).replace("\\","/"),
            witness_sha256=digest(witness_path)))
    assert len(cases)==72 and sum(c["changed"] for c in cases)==3
    manifest=dict(corpus_id="benchmark_v2_fixed_seat_revision_2026-10-14",status="audited_candidate",
        original_upstream_commit=audit["upstream_commit"],case_count=72,changed_cases=3,
        config_sha256=digest(corpus/"config.json"),
        original_files_unchanged=True,all_72_legal_witnesses_verified=True,cases=cases,
        result_policy="Different corpus revision: original experiment scores must not be reused for changed cases.")
    write(corpus/"manifest.json",manifest)
    return manifest


def verify_runs(work):
    plan, benchmark = read(work/"plan.json"), read(work/"benchmark.json")
    assert benchmark["complete"] and len(plan["case_ids"]) == 112
    records = benchmark["records"]
    keys = [(r["contract"]["case_id"],r["contract"]["variant"]) for r in records]
    assert len(keys) == len(set(keys)) == 224
    assert set(keys) == {(c,v) for c in plan["case_ids"] for v in ("baseline","hybrid")}
    for r in records:
        slug = r["contract"]["case_id"].replace(":","_").replace("/","_")
        folder = work/r["contract"]["variant"]/slug
        assert read(folder/"record.json") == r
        assert digest(folder/"config.json") == r["config_file_hash"]
        assert digest(folder/"solver.log") == r["log_hash"]
        assert (digest(folder/"raw.json") if (folder/"raw.json").exists() else None) == r["raw_hash"]
        assert r["contract"]["source_hashes"] == plan["source_hashes"]
        assert all(digest(p) == h for p,h in r["contract"]["input_hashes"].items())
        if not r["error"]:
            cfg,raw=read(folder/"config.json"),read(folder/"raw.json")
            command=r["command"]
            groups=read(command[command.index("--groups")+1])["groups"]
            new=read(command[command.index("--new-seatmap")+1])["seats"]
            old=read(command[command.index("--old-seatmap")+1])["seats"]
            assignment={tuple(map(int,k.split(":"))):s for k,s in raw["assigned_seats"].items()}
            hard,missing,_=evaluator.count_hard_constraint_violations(new,groups,assignment,cfg)
            assert (hard,missing)==(r["violations"],r["missing"])
            score,_=evaluator.calculate_soft_score(new,old,groups,assignment,cfg["weights"],cfg)
            if r["valid"]:
                assert hard==missing==0 and abs(score-r["score"])<=1e-6
                assert abs(raw["total_score"]-score)<=1e-6
            proven,bound=verified_proof("cg_gurobi",raw,r["complete_legal"],r["valid"])
            assert proven==r["proven_optimal"] and bound==r["certified_upper_bound"]
    for cid in plan["case_ids"]:
        slug=cid.replace(":","_").replace("/","_")
        configs=[read(work/v/slug/"config.json") for v in ("baseline","hybrid")]
        assert configs[0].pop("cg_acceleration")=={"heuristic":False}
        assert configs[1].pop("cg_acceleration")=={"heuristic":True}
        assert configs[0]==configs[1]
        pair=[r for r in records if r["contract"]["case_id"]==cid]
        scores=[r["score"] for r in pair if r["valid"]]
        for r in pair:
            if r["certified_upper_bound"] is not None and scores:
                assert r["certified_upper_bound"] >= max(scores)-1e-5, (cid,"certificate contradicted by legal solution")
    with zipfile.ZipFile(work/"source-snapshot.zip") as snapshot:
        for p,h in plan["source_hashes"].items():
            import hashlib
            assert hashlib.sha256(snapshot.read(str(Path(p).relative_to(ROOT)).replace("\\","/"))).hexdigest() == h
    return records


def render(data):
    summary_rows=[]
    for name,label in (("all","全部112例"),("formal40","原40例"),("v2_72","合作者72例")):
        s=data["summaries"][name]
        summary_rows.append(f'<tr><td>{label}</td><td>{s["common_legal"]}</td><td>{s["wins"]}/{s["ties"]}/{s["losses"]}</td><td>{s["mean_score_advantage_percent"]:+.3f}%</td><td>{s["baseline"]["legal"]}/{s["hybrid"]["legal"]}</td><td>{s["baseline"]["proven"]}/{s["hybrid"]["proven"]}</td></tr>')
    totals=data["summaries"]["all"]
    timing=[]
    for key,label in (("baseline","原算法"),("hybrid","启发式定价候选")):
        s=totals[key]
        timing.append(f'<tr><td>{label}</td><td>{s["exact_calls"]}</td><td>{s["exact_seconds"]:.2f}</td><td>{s["heuristic_seconds"]:.2f}</td><td>{s["enumeration_seconds"]:.2f}</td><td>{s["lp_seconds"]:.2f}</td><td>{s["integer_seconds"]:.2f}</td><td>{s["process_seconds"]:.2f}</td><td>{s["over_budget"]}</td></tr>')
    versions = []
    studies = [(name, scopes["all"]) for name,scopes in data.get("study_summaries",{data["study"]:data["summaries"]}).items()]
    for name,s in studies:
        label = name+'（本页主对照）' if name==data["study"] else name
        speedup = f'{s["both_proven_median_speedup"]:.3f}×' if s["both_proven_median_speedup"] is not None else '—'
        versions.append(f'<tr><td>{label}</td><td>{s["common_legal"]}</td><td>{s["wins"]}/{s["ties"]}/{s["losses"]}</td><td>{s["mean_score_advantage_percent"]:+.3f}%</td><td>{s["baseline"]["legal"]}/{s["hybrid"]["legal"]}</td><td>{s["baseline"]["proven"]}/{s["hybrid"]["proven"]}</td><td>{len(s["legal_lost_cases"])}</td><td>{s["hybrid"]["heuristic_seconds"]:.2f}</td><td>{speedup}<small>n={s["both_proven_count"]}</small></td></tr>')
    version_section = '<section><h2>版本与采用判断</h2><p>formal60：合法移座/换座启发式；formal60-fast：增加单旅客精确枚举；formal60-early：进一步找到8个新负列即返回。每版均有独立原算法对照，不把不同版本运行合并为同一实验。候选暂不替换默认算法：得分改善需同时满足合法性、证明能力和重复稳定性。最优解速比为双方均证最优案例中原算法完整进程时间除以候选时间的中位数，大于1表示候选较快；它仅代表所列n个案例。各版本共同合法及双方最优子集可能不同。</p><div class="scroll"><table><thead><tr><th>实验版本</th><th>共同合法</th><th>胜/平/负</th><th>平均变化</th><th>合法数 原/候选</th><th>最优数 原/候选</th><th>原有合法解丢失例数</th><th>启发式累计秒</th><th>最优解速比</th></tr></thead><tbody>'+''.join(versions)+'</tbody></table></div></section>'
    structures=[]
    for field,label in (("passengers","旅客数量"),("seat_load","目标机旅客占座率（不含保护）"),("max_pnr","最大PNR"),("fixed","固定旅客"),("caregiver_demands","陪护需求"),("domain_sum","旅客座位域总大小")):
        cells=[]
        for corpus in ("formal40","v2_72"):
            values=[r[field] for r in data["structure"] if r["corpus"]==corpus]
            cells.append(f'<td>{min(values):.3g} / {statistics.median(values):.3g} / {max(values):.3g}</td>')
        structures.append(f'<tr><td>{label}</td>{"".join(cells)}</tr>')
    gradients = []
    for corpus,label in (("formal40","原40例"),("v2_72","合作者72例")):
        for bucket,stats in data["difficulty_gradients"][corpus]["pnr_size_buckets"].items():
            per_call = f'{stats["seconds_per_call"]:.6f}' if stats["seconds_per_call"] is not None else '—'
            gradients.append(f'<tr><td>{label}</td><td>{bucket}</td><td>{stats["groups"]}</td><td>{stats["calls"]}</td><td>{stats["seconds"]:.2f}</td><td>{per_call}</td></tr>')
    rows = []
    infeasible = {f["case_id"] for f in data["structure"] if f.get("inherited_infeasibility_certificate")}
    for cid,pair in sorted(data["pairs"].items()):
        cells = []
        for v in ("baseline","hybrid"):
            r=pair[v]
            bound = r["certified_upper_bound"]
            bound_label = f'认证上界 {bound:.6f}' if bound is not None else '未获得认证上界'
            state = ('已证输入不可行（独立证书）' if cid in infeasible else
                     f'未取得完整合法解；缺失{r["missing"]}，硬违规{r["violations"]}')
            cells.append(f'<td>{r["score"]:.6f}<small>{r["process_seconds"]:.2f}s · {"已证最优" if r["proven_optimal"] else "未证最优"}</small><small>{bound_label}</small></td>' if r["valid"] else
                         f'<td>{state}<small>{html.escape(str(r["error"] or "详见逐例原始状态"))}</small></td>')
        x,y=pair["baseline"],pair["hybrid"]
        delta=f'{y["score"]-x["score"]:+.6f}' if x["valid"] and y["valid"] else '—'
        known = x.get("known_optimum_score")
        reference = f'{known:.6f}<small>此前独立复核的数值证书</small>' if known is not None else '未引用此前最优值'
        rows.append(f'<tr><td>{html.escape(cid)}</td>{"".join(cells)}<td>{delta}</td><td>{reference}</td></tr>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>列生成加速与算例研究 · 2026-10-14</title><style>
body{font:15px/1.7 system-ui,sans-serif;color:#183047;background:#f3f6fa;margin:0}main{max-width:1240px;margin:auto;padding:28px}h1{line-height:1.3}section{background:white;padding:22px;margin:20px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #dce5ee;text-align:left}small{display:block;color:#617285}input,select{padding:9px;margin:4px;max-width:85%}.scroll{overflow:auto}a{color:#1766b1}
</style><main><h1>列生成加速与算例研究</h1><p>112 例 · 原算法与启发式定价候选 · 同机 60 秒配置预算 · 224 次运行</p>
<p><a href="README.md">结论与限制</a> · <a href="comparison.json">完整逐例数据</a> · <a href="experiment-data.zip">复现证据</a></p>'''+version_section+'''
<section><h2>总体结果</h2><p>以下分数仅比较双方均完整合法的算例；正变化表示候选得分更高。最优性需要有效全局证书；限时、失败及不可行均保留。</p><div class="scroll"><table><thead><tr><th>范围</th><th>共同合法</th><th>候选胜/平/负</th><th>平均得分变化</th><th>合法数 原/候选</th><th>最优数 原/候选</th></tr></thead><tbody>'''+''.join(summary_rows)+'''</tbody></table></div><p>本轮每例一次，尚不能证明重复稳定性；短预算下的路径变化会影响个别结果。</p></section>
<section><h2>开销分解</h2><p>秒数是全部案例的累计值。主问题LP、整数主问题和定价分别计时；完整进程还包括构建、初始列、启动等开销。完整进程可能超过60秒配置预算。限时运行耗时更短，不自动表示更快达到相同质量。</p><div class="scroll"><table><thead><tr><th>方法</th><th>MIP定价次数</th><th>MIP定价秒</th><th>启发式秒</th><th>单旅客枚举秒</th><th>主LP秒</th><th>整数主问题秒</th><th>完整进程秒</th><th>超预算次数</th></tr></thead><tbody>'''+''.join(timing)+'''</tbody></table></div></section>
<section><h2>算例结构</h2><p>单元格为最小值 / 中位数 / 最大值。72例在总体规模和特殊资源耦合方面更重；最大PNR本身不能完整代表难度。其60/85/97%负载基于共同可转移容量，不等于目标飞机的满载率；扩大机型中的97%可能仍有较多空座。</p><p>主基准建议采用独立修订的72例，原40例保留作回归；原始72例与修订输入分开保存。</p><div class="scroll"><table><thead><tr><th>指标</th><th>原40例</th><th>合作者72例</th></tr></thead><tbody>'''+''.join(structures)+'''</tbody></table></div></section>
<section><h2>PNR规模与定价耗时</h2><p>按本轮原算法的PNR人数分档，累计MIP定价时间除以调用次数。不同档位的对偶向量、特殊规则及限时路径不同，因此这里是观察性对比，不构成规模导致变慢的因果证明。</p><div class="scroll"><table><thead><tr><th>算例集</th><th>PNR人数</th><th>有调用的组数</th><th>调用次数</th><th>累计秒</th><th>每次秒</th></tr></thead><tbody>'''+''.join(gradients)+'''</tbody></table></div></section>
<section><h2>为什么难以证明最优</h2><p>此前 72 例在各自 60/900 秒记录中：15 例已证最优，3 例已证不可行；30 例根定价尚未闭合，24 例已认证根 LP，但整数最优性仍未闭合。启发式定价只直接针对其中的定价开销。</p>
<p>3 个不可行输入均源于固定座位与跨组婴儿/陪护资源冲突。独立衍生修订每例只移动 1 个原固定座位，已证明最少修改次数并核验完整合法见证。这不是原问题软分最优证明，修订输入不能沿用原始实验成绩。</p></section>
<section><h2>逐例对照</h2><input id="q" placeholder="搜索算例"><select id="corpus"><option value="all">全部112例</option><option value="v2:">合作者72例</option><option value="formal40">原40例</option></select><p id="count"></p><div class="scroll"><table><thead><tr><th>算例</th><th>原算法</th><th>启发式定价候选</th><th>候选−原算法</th><th>此前已证最优值</th></tr></thead><tbody id="cases">'''+''.join(rows)+'''</tbody></table></div></section></main><script>
const q=document.getElementById('q'),corpus=document.getElementById('corpus'),rows=[...document.querySelectorAll('#cases tr')];function filter(){let n=0;for(const row of rows){let id=row.cells[0].textContent,c=corpus.value;let show=id.toLowerCase().includes(q.value.toLowerCase())&&(c==='all'||(c==='formal40'?!id.startsWith('v2:'):id.startsWith(c)));row.hidden=!show;if(show)n++;}document.getElementById('count').textContent=n+' 例';}q.oninput=corpus.onchange=filter;filter();</script></html>'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--work",type=Path,default=ROOT/"build/cg-acceleration-20261014")
    parser.add_argument("--output",type=Path,default=ROOT/"reports/cg-acceleration-2026-10-14")
    parser.add_argument("--study",choices=("formal60","formal60-fast","formal60-early"),default="formal60-early")
    args=parser.parse_args()
    work,output=args.work.resolve(),args.output.resolve()
    records=verify_runs(work/args.study)
    features=read(work/args.study/"structure.json")["cases"]
    for feature in features:
        row=next(r for r in records if r["contract"]["case_id"]==feature["case_id"])
        command=row["command"]
        payload=read(command[command.index("--groups")+1])
        feature["declared_difficulty"]=payload.get("difficulty")
        feature["resource_load_common_capacity"]=payload.get("resourceLoad")
        feature["resource_load_denominator"]=payload.get("passengerCapacity")
        feature["target_resource_demand"]=payload.get("targetSeatDemand")
    corpus={r["case_id"]:r["corpus"] for r in features}
    previous = previous_difficulty()
    pairs={}
    for r in records:
        compact=dict(r,contract={k:v for k,v in r["contract"].items() if k!="source_hashes"})
        bound = r["certified_upper_bound"]
        compact["reference_type"] = ("proven_optimum" if r["proven_optimal"] else
                                     "certified_upper_bound" if bound is not None else "unavailable")
        compact["gap_to_certified_upper_percent"] = (max(0,bound-r["score"])/max(1,abs(r["score"]))*100
                                                    if bound is not None and r["valid"] else None)
        known = previous["known_optima"].get(r["contract"]["case_id"])
        compact["known_optimum_score"] = known
        compact["gap_to_known_optimum_percent"] = (max(0,known-r["score"])/max(1,abs(r["score"]))*100
                                                   if known is not None and r["valid"] else None)
        pairs.setdefault(r["contract"]["case_id"],{})[r["contract"]["variant"]]=compact
    summaries={name:summary([r for r in records if name=="all" or corpus[r["contract"]["case_id"]]==name]) for name in ("all","formal40","v2_72")}
    repairs=read(work/"derived-v2-repairs/repairs.json")
    assert repairs["all_repaired_legal"]
    assert len(repairs["repairs"]) == 3
    for repair in repairs["repairs"]:
        assert repair["minimum_edits_proven"] and repair["status"] == 2
        assert abs(repair["model_bound"]-len(repair["changes"])) <= 1e-6
    revised_corpus=export_revised_corpus(work,repairs)
    data=dict(summaries=summaries,pairs=pairs,structure=features,previous_difficulty=previous,repairs=repairs,
        study=args.study,source_hashes=read(work/args.study/"plan.json")["source_hashes"],
        certificate_policy=dict(pricing_negative_column_tolerance=1e-6,
            bound_score_close_absolute=1e-5,formal_rational_proof=False,
            safe_bound_used_as_optimum=False),
        revised_corpus=revised_corpus,
        difficulty_gradients=difficulty_gradients(records,features),
        singleton_microbenchmark=read(work/"singleton-microbenchmark.json"),
        limitations=["Single 60s paired run per case; performance estimates need repeat stability checks.",
            "Time-limited algorithm trajectories can change individual scores; no per-case parameter tuning.",
            "Source collaborator CG remains unchanged; dedicated process adapter supplies pricing and profiling.",
            "Gurobi is still used for exact pricing, integer master and branch pricing; LP-only dependency is not achieved.",
            "Original corpus and repaired inputs are different versions; scores must never be mixed.",
            "72-case difficulty classification reuses prior 60/900s evidence; these are not new 900s runs.",
            "Browser surface unavailable; HTML structure, local links and filtering are checked without visual browser QA."])
    all_records=[]
    study_records={}
    data["study_summaries"]={}
    for study in ("formal60","formal60-fast","formal60-early"):
        if (work/study/"benchmark.json").exists():
            rows = records if study==args.study else verify_runs(work/study)
            study_records[study]=rows
            all_records.extend(rows)
            data["study_summaries"][study]={name:summary([r for r in rows if name=="all" or corpus[r["contract"]["case_id"]]==name]) for name in ("all","formal40","v2_72")}
    data["certificate_cross_check"]=cross_check_certificates(all_records)
    data["baseline_repeat_variability"]=baseline_repeat_variability(study_records)
    output.mkdir(parents=True,exist_ok=True)
    write(output/"comparison.json",data)
    (output/"index.html").write_text(render(data),encoding="utf-8",newline="\n")
    s=summaries["all"]
    repair_rows = '\n'.join('| '+r["case_id"]+' | '+', '.join(
        f'{c["passenger"][0]}:{c["passenger"][1]} {c["old"]} → {c["new"]}' for c in r["changes"]
        )+f' | {len(r["changes"])} |' for r in repairs["repairs"])
    early_note = ('本版找到8个未发出的合法负列即返回，避免批次已满后继续搜索。'
                  if args.study=="formal60-early" else '本版未启用找到8个新负列即返回的停止条件。')
    scope_rows = '\n'.join(f'| {label} | {q["common_legal"]} | {q["wins"]}/{q["ties"]}/{q["losses"]} | {q["mean_score_advantage_percent"]:+.3f}% | {q["baseline"]["proven"]}/{q["hybrid"]["proven"]} |'
                          for name,label in (("all","全部112例"),("formal40","原40例"),("v2_72","合作者72例")) for q in (summaries[name],))
    prose=f'''# 列生成加速与算例研究（2026-10-14）

状态：当前主对照为 `{args.study}`，112 例 × 两种定价方式 × 60 秒配置预算，共 224 次运行，覆盖核验通过。全部 {len(data['study_summaries'])} 版正式对照合计 {len(all_records)} 次运行，每版源码快照与结果分目录保存，不与主对照混用。候选保留为独立实验入口，合作者原始算法与原始算例未修改。

## 对照结果

双方共同合法 {s['common_legal']} 例；候选胜/平/负为 {s['wins']}/{s['ties']}/{s['losses']}，平均相对得分变化 {s['mean_score_advantage_percent']:+.3f}%。定义为 `(候选分数−原算法分数)/max(1,abs(原算法分数))×100%` 的算例均值，正值表示候选更好。逐套算例和耗时、证明状态见 [HTML](index.html) 与 [完整数据](comparison.json)。不能用限时运行耗时下降直接宣称更快达到同等质量；双方都证明最优的案例另列时间比。

| 范围 | 共同合法 | 候选胜/平/负 | 平均得分变化 | 已证最优 原/候选 |
|---|---:|---:|---:|---:|
{scope_rows}

精确定价调用由{s['baseline']['exact_calls']}次降至{s['hybrid']['exact_calls']}次，累计耗时由{s['baseline']['exact_seconds']:.2f}秒降至{s['hybrid']['exact_seconds']:.2f}秒；启发式自身耗时{s['hybrid']['heuristic_seconds']:.2f}秒，单旅客枚举耗时{s['hybrid']['enumeration_seconds']:.2f}秒。完整进程累计耗时为原算法{s['baseline']['process_seconds']:.2f}秒、候选{s['hybrid']['process_seconds']:.2f}秒。定价调用减少尚未转化为已证实的整体加速，启发式及后续主问题开销抵消了较多收益。

各候选版本每例只有一次配对正式运行；原算法在三版中各重跑一次，不能将不同候选版本当作同一候选的重复稳定性实验。失败、部分分配、不可行均保留并排除出分数均值；不把缺失分数记为零。完整进程耗时与超预算次数如实报告。

各轮原算法的重跑波动另见 `baseline_repeat_variability`：使用相同原始输入和配置比较其合法性与分数变化。它可提示限时路径波动，但不能替代同一候选的重复实验，也不能直接用作显著性检验。

采用判断：当前候选保留独立入口，不替换默认算法。本轮原有合法解丢失 {len(s['legal_lost_cases'])} 例，新增合法解 {len(s['legal_gained_cases'])} 例；原有最优证明丢失 {len(s['proof_lost_cases'])} 例，新增证明 {len(s['proof_gained_cases'])} 例，具体案例列表见完整数据。即使总数相同，也需检查逐例变化；当前证据不足以保证整体质量不后退。

## 定价机制与证书

候选使用主问题全部对偶系数评价合法移座/换座方案；每轮搜索的时间上限设为该调用预算的 10%，且不超过 20ms，调用检查和结果整理仍有额外开销。{early_note}启发式新列返回 `heuristic`、下界 `-inf`，不具备认证资格。搜索无果调用原精确定价，分支定价仍执行原规则与精确子问题。增加单旅客枚举的入口仅在完整遍历其全部合法选项后提供精确证书。没有按算例名调参。该方案仍需要 Gurobi 精确定价及整数主问题，并未实现“仅主问题 LP 使用 Gurobi”。全局最优标记继续要求完整合法、独立评分一致及全局界在1e-5内闭合，属于既有数值容差下的求解器证书。

方法依据：[Gurobi 列生成基础](https://cdn.gurobi.com/wp-content/uploads/Column-Generation-Basics-Webinar.pdf)。负约化成本列用于改进受限主问题；启发式未找到列不能充当完整列空间最优证书。

报告的“已证最优”沿用浮点求解器的数值证书口径：定价负列阈值为1e-6，界与独立评分的闭合检查为1e-5，并非有理数精确的形式化证明。`safe_bound` 单独保留，不作为理论最优值；只有认证LP/分支界才进入所列gap，未闭合的认证上界也不等于整数最优值。

完整数据将 `gap_to_certified_upper_percent` 与 `gap_to_known_optimum_percent` 分开。前者使用本轮认证上界；后者仅在此前72例的独立复核证书已给出最优值时提供，共15例。无相应参考时均记为null，不能将上界gap解释为真实最优差距。

## 72 例为什么只有十几例证明最优

复核此前完整 60/900 秒记录：15 例已证明全局最优，3 例已证明不可行；30 例根定价尚未闭合，另 24 例根 LP 已认证，但整数搜索仍未闭合。因而不能把全部困难归结为算例错误或子问题慢，也不能把认证 LP 上界当成整数理论最优值。

上述阶段分类针对列生成记录；此前启发式、直接MIP与列生成的完整72例对照见[既有报告](../benchmark-v2-exact-2026-10-12/README.md)，本次未重跑该900秒批次。

此前72例60秒批次累计求解器耗时3616.815秒，其中根节点定价2582.524秒（约71.4%），建模71.612秒（约2.0%）；900秒补跑也有大量定价耗时。这支持优先研究定价加速，但不单独构成PNR结构复杂度的因果证据。

## 两套算例与统一基准建议

原40例含Formal24与D4，旅客数量中位数102、最大PNR中位数8.5；72例分别为166、7。72例固定旅客中位数16.5、陪护需求中位数20.5；40例分别为9、9。因此72例通常具有更大总体规模及更多特殊资源耦合，不能仅按最大PNR判断难度。

建议后续以修订后的V2 72例作为主基准：机型转换、资源负载与PNR构成维度更系统。原40例保留为回归集。72例的60/85/97%负载使用共同可转移容量作为分母，并非目标机型满载率；扩大机型时97%也可能有明显空余，跨机型难度比较应同时看目标机实际资源占用。

72例当前只有一个开发重复，不能称为多种子正式统计基准；后续如需论文级结论，应按原规范增加冻结重复，避免依据算法成绩筛选种子。不要把两套配置下的绝对分数直接平均比较难度，也不要为提高已证最优比例而删掉合法的困难算例。PNR分档定价耗时及秩相关描述见完整数据的`difficulty_gradients`，它们是相关性而非因果证明。

## 不可行输入与独立修订

原生成流程在产生特殊资源可行安排之后另加固定座位；后续校验只检查每个陪护组独立可行，未保证各组共享资源同时可行。3例固定座位阻断婴儿/陪护相邻资源，即便放松其他限制仍匹配不足。

独立修订仅移动每例1名原固定旅客的座位，保留固定旅客身份与数量、PNR、SSR、旧座位、负载等。修订的最少修改次数均获MIP认证，并取得完整合法见证；这不表示原软目标已证明最优。修订文件与原数据分开，原实验分数不能用于修订版本。未来生成器应在固定座位之后保留或重建联合可行见证；仅逐组通过检查不够。

证据包中的 `build/cg-acceleration-20261014/derived-v2-repairs/corpus/` 是独立、完整的72例修订候选，含配置、座位图、清单及72份合法见证；其中69例输入字节不变，3例明确记录变更。

| 原始案例 | 固定旅客及座位改动 | 已证最少修改数 |
|---|---|---|
{repair_rows}

## 单旅客定价原型

另有独立的 `src.cg_acceleration.fast` 入口，对单旅客PNR遍历完整的座位和保护选项，保持精确认证。96与336座规模、4类规则、共400个固定对偶向量下，枚举最优约化成本与原MIP全部一致，定价调用累计加速约5.2至10.0倍。该微基准关闭Cutoff以核对真实最小约化成本，排除构建和初始列，不能推断完整列生成具有相同加速比；此原型与第一版候选结果分开记录。

## 复现与材料

运行前配置可处理完整模型的Gurobi许可证和gurobipy。受限默认许可证的首批筛选已排除，保留原始失败日志。正式实验跨案例并行4任务，每个Gurobi模型1线程、定价worker为1；每例两种方式按预定SHA256奇偶交替运行顺序。

实际运行环境和归档初始解依赖的版本见证据包中的 `dependency-source-manifest.json`。既有Python初始解启发式包含HiGHS辅助依赖，本次保留原样；列生成的主问题、精确定价和分支定价仍使用Gurobi。

在另一台Windows机器复跑时，先检出本功能分支，将证据包按其相对路径解压到仓库根目录，并使用新的输出目录。历史记录中的绝对路径用于原环境溯源，不能直接拿来断点续跑；源代码快照用于核对实际实验版本。下面第一条命令使用新的目录，避免覆盖已发布记录。macOS/Linux批量复跑前需要在复现副本中转换输入审计清单的Windows路径分隔符；独立修订语料的manifest已使用斜杠相对路径。重新生成原报告的脚本目前按原环境记录路径进行核验，历史HTML与JSON可直接阅读。

```powershell
python -m scripts.run_cg_acceleration_study --output build/cg-acceleration-reproduction/{args.study} --budget 60 --workers 4 --entrypoint {read(work/args.study/'plan.json')['entrypoint']}
python -m scripts.repair_v2_fixed_seats --output build/cg-acceleration-20261014/derived-v2-repairs --budget 300
python -m scripts.build_cg_acceleration_report --study {args.study}
```

[实验证据包](experiment-data.zip)保留冻结源代码、配置、原始结果、日志、结构审计、修订及其见证；[文件清单](artifact-manifest.json)提供校验。恢复运行会核对源代码、输入和配置契约，禁止混用不同版本。

交付核验包含完整覆盖、独立硬约束与评分复算、证书与合法解交叉检查、源码和输入哈希、压缩包校验及HTML结构/链接/筛选逻辑检查。当前环境未提供可用浏览器，未完成浏览器视觉与视口核验。
'''
    (output/"README.md").write_text(prose,encoding="utf-8",newline="\n")
    (output/".gitattributes").write_text('.gitattributes text eol=lf\nindex.html text eol=lf\n',encoding="utf-8",newline="\n")
    files=set(p for p in work.rglob("*") if p.is_file() and p.suffix not in (".tmp",))
    for r in records:files.update(Path(p) for p in r["contract"]["input_hashes"])
    files.update(ROOT/c[key] for c in data["previous_difficulty"]["cases"] for key in ("raw_path","config_path"))
    files.update([ROOT/"build/v2-exact-20261012/input-audit.json",ROOT/"build/v2-exact-20261012/benchmark.json",
                  Path(__file__).resolve(),ROOT/"tests/test_cg_acceleration.py",ROOT/"tests/test_cg_singleton.py",
                  ROOT/"tests/test_cg_study_resume.py",
                  ROOT/"tests/test_cg_gurobi.py",ROOT/"config.json",
                  ROOT/"requirements.txt",ROOT/"requirements-column-generation.txt",
                  ROOT/"data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/manifest.json",
                  ROOT/"scripts/benchmark_singleton_pricing.py",ROOT/"scripts/repair_v2_fixed_seats.py"])
    files.update(p for p in (ROOT/"src/cg_acceleration").iterdir() if p.is_file())
    manifest={str(p.relative_to(ROOT)).replace("\\","/"):digest(p) for p in sorted(files)}
    with zipfile.ZipFile(output/"experiment-data.zip","w",zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(files):archive.write(p,str(p.relative_to(ROOT)))
        archive.writestr("member-manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
    write(output/"artifact-manifest.json",{p.name:dict(sha256=digest(p),bytes=p.stat().st_size) for p in output.iterdir() if p.name != "artifact-manifest.json"})
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
