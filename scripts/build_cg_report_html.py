"""生成自包含的 HTML 报告：列生成重做（Gurobi 直接定价）——算法、建模、实验对比、审计。

读取 reports/audit-2026-09-23/benchmark/*.json 与 rich_python_formal24.csv，输出
reports/audit-2026-09-23/列生成重做报告.html。
"""
from __future__ import annotations

import csv
import html
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "reports/audit-2026-09-23/benchmark"
OUT = ROOT / "reports/audit-2026-09-23/列生成重做报告.html"
METHODS = ["heuristic", "legacy", "new"]
LABEL = {"heuristic": "启发式 5 s", "legacy": "旧列生成 150 s", "new": "新列生成 150 s"}


def load(name):
    p = B / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def rich_reference():
    p = ROOT / "rich_python_formal24.csv"
    ref = {}
    if p.exists():
        with p.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                ref[row["case_id"]] = {"rich60": float(row["objective"]), "best_I": float(row["current_reference_F_I"])}
    return ref


def merge_new(rows, new_rows):
    if not new_rows:
        return rows
    by = {r["instance"]: r.get("new") for r in new_rows}
    for r in rows:
        if by.get(r["instance"]) and "error" not in by[r["instance"]]:
            r["new"] = by[r["instance"]]
    return rows


def merge_methods(rows, other, methods=("legacy", "new")):
    """用另一轮结果覆盖指定方法列（计时补测：新旧两法重跑，启发式沿用）。"""
    if not other:
        return rows
    by = {r["instance"]: r for r in other}
    for r in rows:
        o = by.get(r["instance"])
        if not o:
            continue
        for m in methods:
            if o.get(m) and "error" not in o[m]:
                r[m] = o[m]
    return rows


def bound_of(m):
    if m.get("proven"):
        return (m["bpU"] if m.get("bpU") is not None else m["score"]), "proven"
    if m.get("U") is not None:
        return m["U"], "exact"
    if m.get("safeU") is not None:
        return m["safeU"], "safe"
    return None, "none"


KIND = {"proven": "证明最优", "exact": "精确 U", "safe": "安全 U", "none": "无"}


def esc(x):
    return html.escape(str(x))


def fmt(x, nd=2):
    return "—" if x is None else f"{x:,.{nd}f}"


def summary_stats(rows, ref=None, prefix=""):
    n = 0
    legal = {m: 0 for m in METHODS}; wins = {m: 0 for m in METHODS}; cert = {m: 0 for m in METHODS}; proven = {m: 0 for m in METHODS}
    gaps = {m: [] for m in METHODS}; times = {m: [] for m in METHODS}
    d_new_legacy, d_new_rich, d_new_best, bound_viol = [], [], [], []
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
            b, _ = bound_of(ms[m])
            if b is not None: gaps[m].append(100 * (b - ms[m]["score"]) / max(1, abs(ms[m]["score"])))
        best = max(ms[m]["score"] for m in METHODS)
        for m in METHODS:
            if abs(ms[m]["score"] - best) < 1e-6: wins[m] += 1
        d_new_legacy.append(ms["new"]["score"] - ms["legacy"]["score"])
        key = prefix + r["instance"].replace("_groups", "")
        if ref and key in ref:
            d_new_rich.append(ms["new"]["score"] - ref[key]["rich60"]); d_new_best.append(ms["new"]["score"] - ref[key]["best_I"])
            if ms["new"].get("U") is not None and ms["new"]["U"] < ref[key]["best_I"] - 1e-6:
                bound_viol.append(key)
    return dict(n=n, legal=legal, wins=wins, cert=cert, proven=proven, gaps=gaps, times=times,
                d_new_legacy=d_new_legacy, d_new_rich=d_new_rich, d_new_best=d_new_best, bound_viol=bound_viol)


def stat_tiles(s):
    if not s["n"]:
        return "<p class='muted'>尚无结果。</p>"
    def cell(label, vals):
        return f"<div class='tile'><div class='tile-l'>{label}</div><div class='tile-v'>{vals}</div></div>"
    n = s["n"]
    tiles = [
        cell("有效算例", f"{n}"),
        cell("完整合法", " / ".join(f"{s['legal'][m]}" for m in METHODS) + f"<span class='sub'>启发式 / 旧 / 新，满分 {n}</span>"),
        cell("最好软分（含并列）", " / ".join(f"{s['wins'][m]}" for m in METHODS) + "<span class='sub'>启发式 / 旧 / 新</span>"),
        cell("精确 LP 上界", f"{s['cert']['legacy']} / {s['cert']['new']}<span class='sub'>旧 / 新</span>"),
        cell("证明整数最优", f"{s['proven']['legacy']} / {s['proven']['new']}<span class='sub'>旧 / 新</span>"),
    ]
    for m in ("legacy", "new"):
        g = s["gaps"][m]
        tiles.append(cell(f"{LABEL[m]} 相对自身界 gap%", f"{statistics.mean(g):.2f}<span class='sub'>均值；中位 {statistics.median(g):.2f}</span>" if g else "—"))
    d = s["d_new_legacy"]
    tiles.append(cell("新 − 旧 软分差", f"{statistics.mean(d):+.2f}<span class='sub'>均值；新优 {sum(1 for x in d if x > 1e-6)} 例，旧优 {sum(1 for x in d if x < -1e-6)} 例</span>"))
    if s["d_new_rich"]:
        tiles.append(cell("新 − Rich 60 s", f"{statistics.mean(s['d_new_rich']):+.2f}<span class='sub'>新优 {sum(1 for x in s['d_new_rich'] if x > 1e-6)}/{len(s['d_new_rich'])}</span>"))
        tiles.append(cell("新 − 历史最好 I", f"{statistics.mean(s['d_new_best']):+.2f}<span class='sub'>新优 {sum(1 for x in s['d_new_best'] if x > 1e-6)}/{len(s['d_new_best'])}</span>"))
        tiles.append(cell("新 U 低于历史最好 I 的次数", f"{len(s['bound_viol'])}<span class='sub'>应为 0（界的有效性检查）</span>"))
    return "<div class='tiles'>" + "".join(tiles) + "</div>"


def delta_chart(rows):
    """新 − 旧 软分差的水平条形图（inline SVG）。"""
    items = []
    for r in rows:
        a, b = r.get("legacy"), r.get("new")
        if not a or not b or "error" in a or "error" in b:
            continue
        d = b["score"] - a["score"]
        _, kind = bound_of(b)
        items.append((r["instance"].replace("_groups", ""), d, kind))
    if not items:
        return ""
    maxabs = max(1.0, max(abs(d) for _, d, _ in items))
    row_h, left, width = 22, 170, 720
    plot_w = width - left - 60
    h = row_h * len(items) + 30
    mid = left + plot_w / 2
    scale = (plot_w / 2 - 10) / maxabs
    parts = [f"<svg class='chart' viewBox='0 0 {width} {h}' role='img' aria-label='新减旧软分差'>"]
    parts.append(f"<line x1='{mid}' y1='6' x2='{mid}' y2='{h-22}' class='axis'/>")
    for i, (name, d, kind) in enumerate(items):
        y = 8 + i * row_h
        x0 = mid if d >= 0 else mid + d * scale
        w = abs(d) * scale
        cls = "bar-pos" if d > 1e-6 else ("bar-neg" if d < -1e-6 else "bar-zero")
        parts.append(f"<text x='{left-8}' y='{y+15}' class='lbl' text-anchor='end'>{esc(name)}</text>")
        parts.append(f"<rect x='{x0:.1f}' y='{y+3}' width='{max(w,1.5):.1f}' height='{row_h-8}' class='{cls}'/>")
        tx = mid + d * scale + (6 if d >= 0 else -6)
        anchor = "start" if d >= 0 else "end"
        parts.append(f"<text x='{tx:.1f}' y='{y+15}' class='val' text-anchor='{anchor}'>{d:+.1f}</text>")
        mark = {"proven": "●", "exact": "◐", "safe": "○", "none": "·"}[kind]
        parts.append(f"<text x='{width-40}' y='{y+15}' class='mark k-{kind}' text-anchor='middle'>{mark}</text>")
    parts.append(f"<text x='{mid}' y='{h-6}' class='lbl' text-anchor='middle'>0（右侧 = 新方法软分更高）</text>")
    parts.append(f"<text x='{width-40}' y='{h-6}' class='lbl' text-anchor='middle'>新界</text>")
    parts.append("</svg>")
    legend = "<p class='legend'>右列标记新方法的界类型：● 证明最优　◐ 精确 LP 上界　○ 安全上界　· 无</p>"
    return "".join(parts) + legend


def result_table(rows, ref=None, prefix=""):
    head = ("<div class='tablewrap'><table><thead><tr><th>算例</th><th>人/组/最大组</th><th>方法</th><th>合法</th>"
            "<th class='num'>软分</th><th class='num'>s / v / p / c / b</th><th class='num'>界</th><th>界类型</th>"
            "<th class='num'>gap%</th><th class='num'>秒</th></tr></thead><tbody>")
    body = []
    for r in rows:
        name = r["instance"].replace("_groups", "")
        first = True
        for m in METHODS:
            x = r.get(m)
            cell_name = f"<td class='name' rowspan='{3 + (2 if ref and prefix+name in ref else 0)}'>{esc(name)}<div class='sub'>{r['pax']}/{r['groups']}/{r['max_group']}</div></td>" if first else ""
            first = False
            if not x or "error" in (x or {}):
                body.append(f"<tr>{cell_name}<td></td><td>{LABEL[m]}</td><td class='bad'>出错</td><td colspan='6' class='muted'>{esc((x or {}).get('error','')[:80])}</td></tr>")
                continue
            legal = "<span class='ok'>是</span>" if x["violations"] == 0 and x["unassigned"] == 0 else f"<span class='bad'>否 {x['violations']}/{x['unassigned']}</span>"
            b, kind = bound_of(x)
            gap = "—" if b is None else f"{100*(b-x['score'])/max(1,abs(x['score'])):.2f}"
            cls = f"k-{kind}"
            body.append(f"<tr class='m-{m}'>{cell_name}<td></td><td>{LABEL[m]}</td><td>{legal}</td><td class='num strong'>{fmt(x['score'],3)}</td>"
                        f"<td class='num small'>{x['s']:.1f} / {x['v']:.1f} / {x['p']:.1f} / {x['c']:.1f} / {x['b']:.2f}</td>"
                        f"<td class='num'>{fmt(b,3)}</td><td class='{cls}'>{KIND[kind]}</td><td class='num'>{gap}</td><td class='num'>{x['time']:.0f}</td></tr>")
        key = prefix + name
        if ref and key in ref:
            body.append(f"<tr class='m-ref'><td></td><td>Rich Python 60 s（冻结参考）</td><td>是</td><td class='num'>{fmt(ref[key]['rich60'],3)}</td><td colspan='5' class='muted small'>来源 rich_python_formal24.csv</td></tr>")
            body.append(f"<tr class='m-ref'><td></td><td>历史最好整数解 I</td><td>是</td><td class='num'>{fmt(ref[key]['best_I'],3)}</td><td colspan='5' class='muted small'>R0 union 列池 / Rich 60 s 取优</td></tr>")
    return head + "".join(body) + "</tbody></table></div>"


def timing_table(rows_list):
    """精确方法的时间指标：根 LP 认证用时 / 证明最优用时 / 截停时 gap。"""
    head = ("<div class='tablewrap'><table><thead><tr><th>算例</th>"
            "<th class='num'>旧：根 LP 认证 s</th><th class='num'>新：根 LP 认证 s</th>"
            "<th class='num'>旧：证明最优 s</th><th class='num'>新：证明最优 s</th>"
            "<th class='num'>旧：截停 gap%</th><th class='num'>新：截停 gap%</th></tr></thead><tbody>")
    body = []
    agg = {"legacy": {"root": [], "proven": []}, "new": {"root": [], "proven": []}}
    both_root = []
    both_proven = []
    only = {"legacy": 0, "new": 0}
    for title, rows in rows_list:
        body.append(f"<tr><td colspan='7' class='muted small'>{esc(title)}</td></tr>")
        for r in rows:
            o, n = r.get("legacy"), r.get("new")
            if not o or not n or "error" in o or "error" in n:
                continue
            def cell_t(x, key):
                v = x.get(key)
                if key == "proven_seconds" and v is None and x.get("proven"):
                    v = x.get("time")   # 求到最优即停：墙钟即证明用时
                return "—" if v is None else f"{v:.0f}"
            def gap(x):
                if x.get("proven"): return "0（已证明）"
                b, _ = bound_of(x)
                return "—" if b is None else f"{100*(b-x['score'])/max(1,abs(x['score'])):.2f}"
            for m in ("legacy", "new"):
                x = r[m]
                if x.get("root_seconds") is not None: agg[m]["root"].append(x["root_seconds"])
                pv = x.get("proven_seconds") if x.get("proven_seconds") is not None else (x.get("time") if x.get("proven") else None)
                if pv is not None: agg[m]["proven"].append(pv)
            if o.get("root_seconds") is not None and n.get("root_seconds") is not None:
                both_root.append((o["root_seconds"], n["root_seconds"]))
            if o.get("proven") and n.get("proven"):
                both_proven.append((o["time"], n["time"]))
            elif o.get("proven") or n.get("proven"):
                only["legacy" if o.get("proven") else "new"] += 1
            name = r["instance"].replace("_groups", "")
            body.append(f"<tr><td class='name'>{esc(name)}</td><td class='num'>{cell_t(o,'root_seconds')}</td><td class='num'>{cell_t(n,'root_seconds')}</td>"
                        f"<td class='num'>{cell_t(o,'proven_seconds')}</td><td class='num'>{cell_t(n,'proven_seconds')}</td>"
                        f"<td class='num'>{gap(o)}</td><td class='num'>{gap(n)}</td></tr>")
    summ = []
    root_cert = {m: sum(1 for _, rows in rows_list for r in rows if r.get(m) and "error" not in r[m] and r[m].get("U") is not None) for m in ("legacy", "new")}
    for m, lab in (("legacy", "旧"), ("new", "新")):
        r_, p_ = agg[m]["root"], agg[m]["proven"]
        summ.append(f"{lab}：拿到精确 U {root_cert[m]} 例" + (f"（有根 LP 认证时刻记录的 {len(r_)} 例中位 {statistics.median(r_):.0f} s）" if r_ else "（旧方法原始运行未记录认证时刻，仅补跑 2 例）" if m == "legacy" else ""))
        summ.append(f"证明最优 {len(p_)} 例（中位 {statistics.median(p_):.0f} s）" if p_ else "证明最优 0 例")
    if both_root:
        faster = sum(1 for a, b in both_root if b < a - 1e-6)
        summ.append(f"两法都拿到根 LP 认证的 {len(both_root)} 例中新方法更快 {faster} 例，用时比中位 {statistics.median([b/max(a,1e-9) for a, b in both_root]):.2f}")
    if both_proven:
        faster = sum(1 for a, b in both_proven if b < a - 1e-6)
        summ.append(f"两法都证明最优的 {len(both_proven)} 例（求到最优即停）中新方法更快 {faster} 例，用时比中位 {statistics.median([b/max(a,1e-9) for a, b in both_proven]):.2f}")
    if only["legacy"] or only["new"]:
        summ.append(f"仅一方证明最优：旧 {only['legacy']} 例，新 {only['new']} 例")
    return "<p class='small'>" + "；".join(summ) + "。</p>" + head + "".join(body) + "</tbody></table></div>"


def convergence_charts(rows, picks):
    """LP 界（分数口径）随时间的收敛曲线：旧（含 incumbent）与新。"""
    out = []
    by = {r["instance"].replace("_groups", ""): r for r in rows}
    for name in picks:
        r = by.get(name)
        if not r or not r.get("legacy") or not r.get("new"):
            continue
        series = []
        for m, lab, cls in (("legacy", "旧 LP 界", "s-legacy"), ("new", "新 LP 界", "s-new")):
            traj = r[m].get("trajectory") or {}
            pts = []
            offset = 0.0
            for cab, tr in traj.items():   # 舱位顺序运行：时间累加
                cab_pts = []
                for t in tr:
                    el, bound = float(t[0]), t[1]
                    if bound is None: continue
                    cab_pts.append((offset + el, float(bound)))
                if cab_pts:
                    offset = cab_pts[-1][0]
                    pts.extend(cab_pts)
            if len(pts) >= 2:
                series.append((lab, cls, pts))
        finals = [(LABEL[m], r[m]["score"]) for m in ("legacy", "new")]
        if not series:
            continue
        xs = [x for _, _, pts in series for x, _ in pts]; ys = [y for _, _, pts in series for _, y in pts] + [v for _, v in finals]
        x0, x1 = 0.0, max(xs) * 1.05 or 1.0
        y0, y1 = min(ys), max(ys)
        pad = (y1 - y0) * 0.08 or 1.0; y0 -= pad; y1 += pad
        W, H, L, Bm, T, R = 720, 260, 70, 34, 16, 20
        def X(x): return L + (x - x0) / (x1 - x0) * (W - L - R)
        def Y(y): return T + (y1 - y) / (y1 - y0) * (H - T - Bm)
        svg = [f"<svg class='chart' viewBox='0 0 {W} {H}' role='img' aria-label='{esc(name)} 收敛曲线'>"]
        for k in range(5):
            yv = y0 + (y1 - y0) * k / 4
            svg.append(f"<line x1='{L}' y1='{Y(yv):.1f}' x2='{W-R}' y2='{Y(yv):.1f}' class='grid'/>")
            svg.append(f"<text x='{L-6}' y='{Y(yv)+4:.1f}' class='lbl' text-anchor='end'>{yv:,.0f}</text>")
        for k in range(6):
            xv = x0 + (x1 - x0) * k / 5
            svg.append(f"<text x='{X(xv):.1f}' y='{H-10}' class='lbl' text-anchor='middle'>{xv:.0f}s</text>")
        for lab, cls, pts in series:
            d = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
            svg.append(f"<polyline points='{d}' class='{cls}' fill='none'/>")
            lx, ly = pts[-1]
            svg.append(f"<circle cx='{X(lx):.1f}' cy='{Y(ly):.1f}' r='3' class='{cls}-dot'/>")
        for i, (lab, v) in enumerate(finals):
            cls = "s-legacy" if i == 0 else "s-new"
            svg.append(f"<line x1='{L}' y1='{Y(v):.1f}' x2='{W-R}' y2='{Y(v):.1f}' class='{cls} dashed'/>")
        svg.append(f"<text x='{L}' y='{T-4}' class='lbl'>{esc(name)}　实线：LP 界随时间；虚线：150 s 时的整数解</text>")
        svg.append("</svg>")
        out.append("".join(svg))
    legend = "<p class='legend'><span class='sw s-legacy'></span> 旧列生成　<span class='sw s-new'></span> 新列生成　（纵轴为分数口径，越高越好；LP 界从上方逼近，整数解在下方）</p>"
    return (legend + "".join(out)) if out else "<p class='muted'>暂无轨迹数据。</p>"


def convergence_state(m):
    """三类收敛状态：proven / root / none。"""
    if m.get("proven"):
        return "proven"
    if m.get("U") is not None:
        return "root"
    return "none"


STATE_LABEL = {"proven": "证明最优", "root": "根 LP 收敛（未证明）", "none": "时限内未收敛"}
STATE_CLASS = {"proven": "k-proven", "root": "k-exact", "none": "k-safe"}


def convergence_matrix(rows, budget):
    counts = {m: {"proven": 0, "root": 0, "none": 0} for m in ("legacy", "new")}
    per = []
    for r in rows:
        o, n = r.get("legacy"), r.get("new")
        if not o or not n or "error" in o or "error" in n:
            continue
        so, sn = convergence_state(o), convergence_state(n)
        counts["legacy"][so] += 1; counts["new"][sn] += 1
        def t(x, st):
            if st == "proven":
                v = x.get("proven_seconds") if x.get("proven_seconds") is not None else x.get("time")
                return f"{v:.0f} s 证明"
            if st == "root":
                v = x.get("root_seconds")
                return f"根 LP {v:.0f} s；截停 gap {100*(x['U']-x['score'])/max(1,abs(x['score'])):.2f}%" if v is not None else f"截停 gap {100*(x['U']-x['score'])/max(1,abs(x['score'])):.2f}%"
            b, _ = bound_of(x)
            return "无界" if b is None else f"安全界 gap {100*(b-x['score'])/max(1,abs(x['score'])):.1f}%"
        per.append((r["instance"].replace("_groups", ""), so, t(o, so), sn, t(n, sn)))
    if not per:
        return ""
    n = len(per)
    head = ("<div class='tablewrap'><table class='plain'><thead><tr><th>方法</th>"
            "<th class='num'>证明最优</th><th class='num'>根 LP 收敛（未证明）</th><th class='num'>时限内未收敛</th></tr></thead><tbody>")
    body = "".join(f"<tr><td>{LABEL[m]}</td><td class='num k-proven'>{counts[m]['proven']}</td><td class='num k-exact'>{counts[m]['root']}</td><td class='num k-safe'>{counts[m]['none']}</td></tr>" for m in ("legacy", "new"))
    matrix = head + body + "</tbody></table></div>"
    detail = ("<div class='tablewrap'><table><thead><tr><th>算例</th><th>旧列生成</th><th>说明</th><th>新列生成</th><th>说明</th></tr></thead><tbody>"
              + "".join(f"<tr><td class='name'>{esc(name)}</td><td class='{STATE_CLASS[so]}'>{STATE_LABEL[so]}</td><td class='small'>{to}</td>"
                        f"<td class='{STATE_CLASS[sn]}'>{STATE_LABEL[sn]}</td><td class='small'>{tn}</td></tr>" for name, so, to, sn, tn in per)
              + "</tbody></table></div>")
    return (f"<h4>收敛状态（预算 {budget}）</h4><p class='small muted'>证明最优 = 分支定价前沿清空并全部认证，求解提前结束；根 LP 收敛 = 所有组定价闭合、拿到精确上界，但整数 gap 未在时限内关闭；"
            f"时限内未收敛 = 只有安全界或无界，解质量看软分与截停 gap。</p>{matrix}{detail}")


def long_budget_section(f150, l600, n600, ref, prefix, title):
    """600 s 长预算：只对 150 s 未证明的运行重跑；与 150 s 结果并列。"""
    if not l600 and not n600:
        return f"<section><h3>{esc(title)}</h3><p class='muted'>600 s 长预算尚未完成。</p></section>"
    by150 = {r["instance"]: r for r in f150}
    by600 = {}
    for rows, m in ((l600 or [], "legacy"), (n600 or [], "new")):
        for r in rows:
            if r.get(m) and "error" not in r[m]:
                by600.setdefault(r["instance"], {})[m] = r[m]
    head = ("<div class='tablewrap'><table><thead><tr><th>算例</th><th>方法</th>"
            "<th class='num'>150 s 软分</th><th class='num'>600 s 软分</th><th class='num'>Δ</th>"
            "<th>150 s 状态</th><th>600 s 状态</th><th class='num'>600 s 界</th><th class='num'>600 s gap%</th><th class='num'>用时 s</th><th class='num'>Rich 60 s</th><th class='num'>历史 I</th></tr></thead><tbody>")
    body = []; stats = {"legacy": {"n": 0, "proven": 0, "root": 0, "improved": 0, "dsum": 0.0}, "new": {"n": 0, "proven": 0, "root": 0, "improved": 0, "dsum": 0.0}}
    for inst in sorted(by600):
        name = inst.replace("_groups", ""); key = prefix + name
        first = True
        for m in ("legacy", "new"):
            x6 = by600[inst].get(m)
            if not x6:
                continue
            x1 = by150.get(inst, {}).get(m, {})
            st1, st6 = convergence_state(x1) if x1 else "none", convergence_state(x6)
            b, kind = bound_of(x6)
            gap = "0" if st6 == "proven" else ("—" if b is None else f"{100*(b-x6['score'])/max(1,abs(x6['score'])):.2f}")
            d = x6["score"] - x1.get("score", x6["score"])
            st = stats[m]; st["n"] += 1; st["proven"] += st6 == "proven"; st["root"] += st6 == "root"; st["improved"] += d > 1e-6; st["dsum"] += d
            rich = ref.get(key, {}).get("rich60"); best = ref.get(key, {}).get("best_I")
            body.append(f"<tr class='m-{m}'>{('<td class=name rowspan=' + str(len(by600[inst])) + '>' + esc(name) + '</td>') if first else ''}<td>{LABEL[m].replace('150 s','')}</td>"
                        f"<td class='num'>{fmt(x1.get('score'),3) if x1 else '—'}</td><td class='num strong'>{fmt(x6['score'],3)}</td><td class='num'>{d:+.2f}</td>"
                        f"<td class='{STATE_CLASS[st1]}'>{STATE_LABEL[st1]}</td><td class='{STATE_CLASS[st6]}'>{STATE_LABEL[st6]}</td>"
                        f"<td class='num'>{fmt(b,3)}</td><td class='num'>{gap}</td><td class='num'>{x6['time']:.0f}</td><td class='num'>{fmt(rich,2)}</td><td class='num'>{fmt(best,2)}</td></tr>")
            first = False
    summ = "；".join(f"{'旧' if m=='legacy' else '新'}：重跑 {st['n']} 例，600 s 内证明最优 {st['proven']}、根 LP 收敛 {st['root']}、仍未收敛 {st['n']-st['proven']-st['root']}；软分改善 {st['improved']} 例，均值 {st['dsum']/max(1,st['n']):+.2f}" for m, st in stats.items() if st["n"])
    return f"<section><h3>{esc(title)}</h3><p class='small'>只对 150 s 内未证明最优的运行重跑，预算 600 s，其余设置不变；旧方法同样重跑。{summ}。</p>{head}{''.join(body)}</tbody></table></div></section>"


def instance_profile_table(f24f, f24r, n600f, n600r):
    """24 例的同行组规模分布，并标出新方法 150 s / 600 s 的收敛状态。"""
    import collections, glob
    D = ROOT / "data/generator_v3_2/d3_official_24cases_2026-09-07"
    six = {}
    for d, rows in (("forward", n600f or []), ("reverse", n600r or [])):
        for r in rows:
            six[(d, r["instance"])] = r["new"]
    base = {}
    for d, rows in (("forward", f24f), ("reverse", f24r)):
        for r in rows:
            base[(d, r["instance"])] = r["new"]
    head = ("<div class='tablewrap'><table><thead><tr><th>算例</th><th class='num'>人数</th><th class='num'>组数</th><th class='num'>最大组</th>"
            "<th class='num'>≥7 人组</th><th class='num'>≥9 人组</th><th>组规模分布（人数:组数）</th><th>新 150 s</th><th>新 600 s</th></tr></thead><tbody>")
    body = []
    for d in ("forward", "reverse"):
        body.append(f"<tr><td colspan='9' class='muted small'>{'正向 3-4-3 → 3-3' if d == 'forward' else '反向 3-3 → 3-4-3'}</td></tr>")
        for f in sorted(glob.glob(str(D / d / "*_groups.json"))):
            inst = Path(f).name.replace(".json", "")
            g = json.loads(Path(f).read_text(encoding="utf-8-sig"))["groups"]
            sizes = collections.Counter(len(x["psrs"]) for x in g)
            dist = " ".join(f"{k}:{v}" for k, v in sorted(sizes.items()))
            b = base.get((d, inst)); x6 = six.get((d, inst))
            s1 = convergence_state(b) if b else "none"
            s6 = convergence_state(x6) if x6 else (s1 if s1 == "proven" else "none")
            note6 = "" if x6 else ("<span class='muted small'>（150 s 已证明，未重跑）</span>" if s1 == "proven" else "")
            body.append(f"<tr><td class='name'>{esc(inst.replace('_groups',''))}</td><td class='num'>{sum(len(x['psrs']) for x in g)}</td><td class='num'>{len(g)}</td>"
                        f"<td class='num strong'>{max(sizes)}</td><td class='num'>{sum(v for k, v in sizes.items() if k >= 7)}</td><td class='num'>{sum(v for k, v in sizes.items() if k >= 9)}</td>"
                        f"<td class='small'>{dist}</td><td class='{STATE_CLASS[s1]}'>{STATE_LABEL[s1]}</td><td class='{STATE_CLASS[s6]}'>{STATE_LABEL[s6]}{note6}</td></tr>")
    return head + "".join(body) + "</tbody></table></div>"


def section(title, rows, ref=None, prefix="", note="", budget="150 s"):
    global LABEL
    saved = dict(LABEL)
    LABEL = {"heuristic": "启发式 5 s", "legacy": f"旧列生成 {budget}", "new": f"新列生成 {budget}"}
    try:
        return _section(title, rows, ref, prefix, note, budget)
    finally:
        LABEL = saved


def _section(title, rows, ref=None, prefix="", note="", budget="150 s"):
    if not rows:
        return f"<section><h3>{esc(title)}</h3><p class='muted'>尚未完成。</p></section>"
    s = summary_stats(rows, ref, prefix)
    return (f"<section><h3>{esc(title)}</h3>{('<p>'+note+'</p>') if note else ''}{stat_tiles(s)}"
            f"{convergence_matrix(rows, budget)}"
            f"<h4>逐算例：新 − 旧 软分差</h4>{delta_chart(rows)}<h4>逐算例明细</h4>{result_table(rows, ref, prefix)}</section>")


def _full_edge_diag_text():
    p = B / "full_edge_official_diag.json"
    if not p.exists():
        return "（诊断结果缺失。）"
    d = json.loads(p.read_text(encoding="utf-8"))
    res, eco = d["result"], d["economy"]
    bound = res["lp_bound"] if res["lp_bound"] is not None else res["safe_bound"]
    gap = 100 * (bound - res["total_score"]) / abs(res["total_score"])
    return (f"经济舱 {eco['iterations']} 轮、{eco['columns']} 列后根 LP 仍未闭合（部分 8–10 人组的定价在 120 s 内不能证明无负列），"
            f"安全上界 {bound:.1f}，整数解 {res['total_score']:.1f}，整数 gap 约 {gap:.1f}%；受限主问题成本从第 22 轮的 966 降到第 88 轮的 839，"
            f"后 500 s 仅下降约 3%。与 normal 系列 &lt;1% 的 gap 相比明显偏大。")


def _conclusion_scale(f24f, f24r, ref):
    parts = []
    for title, rows, prefix in (("正向", f24f, "forward:"), ("反向", f24r, "reverse:")):
        if not rows:
            continue
        s = summary_stats(rows, ref, prefix)
        parts.append(f"Formal24 {title}：新方法最好或并列 {s['wins']['new']}/{s['n']}（旧 {s['wins']['legacy']}/{s['n']}），精确 U 新 {s['cert']['new']} / 旧 {s['cert']['legacy']}，"
                     f"证明最优 新 {s['proven']['new']} / 旧 {s['proven']['legacy']}，优于 Rich 60 s {sum(1 for d in s['d_new_rich'] if d > 1e-6)}/{len(s['d_new_rich'])}，"
                     f"优于历史最好 I {sum(1 for d in s['d_new_best'] if d > 1e-6)}/{len(s['d_new_best'])}")
    return "；".join(parts) + ("。反向 12 例新方法全部不劣于旧实现（8 例严格更好）；正向 12 例中 9 例不劣于旧实现，3 例略劣（100_edge −1.0、full_normal −3.2、full_edge −39.4），旧方法在正向 100_stress 48 s 证明最优而新方法未认证。"
                              "正向 full_edge 是唯一低于历史最好 I 的算例；保护空座密集的满载 edge 例两边均未认证，根因是 LP 松弛弱而非实现缺陷。") if parts else "（待 Formal24 结果）"


def main():
    ref = rich_reference()
    fx = merge_new(load("fixtures.json") or [], load("fixtures_new_final.json"))
    f24f = merge_methods(load("formal24_forward.json") or [], load("formal24_forward_timing.json"), ("new",))
    f24f = merge_methods(f24f, load("formal24_forward_legacy_timing.json"), ("legacy",))
    f24r = merge_methods(load("formal24_reverse.json") or [], load("formal24_reverse_timing.json"), ("new",))
    f24r = merge_methods(f24r, load("formal24_reverse_legacy_timing.json"), ("legacy",))
    timing_available = bool(load("formal24_forward_timing.json"))
    l600f, n600f = load("formal24_forward_600s_legacy.json"), load("formal24_forward_600s_new.json")
    l600r, n600r = load("formal24_reverse_600s_legacy.json"), load("formal24_reverse_600s_new.json")

    # 总览数字（Formal24 优先）
    all_rows = [("Formal24 正向", f24f, "forward:"), ("Formal24 反向", f24r, "reverse:")]
    overview = []
    for title, rows, prefix in all_rows:
        if rows:
            s = summary_stats(rows, ref, prefix)
            overview.append((title, s))

    css = """
:root{--paper:#F5F6F4;--ink:#1B2430;--ink-2:#4A5563;--muted:#6B7480;--line:#D9DEE3;--panel:#FFFFFF;--accent:#0B5FA5;--accent-soft:#E3EEF8;--amber:#B8600F;--amber-soft:#FBEEDD;--green:#2E7D4F;--green-soft:#E1F1E7;--red:#B23A3A;--mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;--sans:'IBM Plex Sans','Noto Sans SC','PingFang SC','Microsoft YaHei',system-ui,sans-serif;--serif:'IBM Plex Serif','Noto Serif SC','Songti SC',Georgia,serif;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--paper:#12171E;--ink:#E6EAEE;--ink-2:#C2CAD3;--muted:#98A2AD;--line:#2A333D;--panel:#1A2129;--accent:#6FB1E8;--accent-soft:#1C2E40;--amber:#E39A4E;--amber-soft:#34281A;--green:#5FBF87;--green-soft:#1B3226;--red:#E07070;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#12171E;--ink:#E6EAEE;--ink-2:#C2CAD3;--muted:#98A2AD;--line:#2A333D;--panel:#1A2129;--accent:#6FB1E8;--accent-soft:#1C2E40;--amber:#E39A4E;--amber-soft:#34281A;--green:#5FBF87;--green-soft:#1B3226;--red:#E07070;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:15.5px;line-height:1.65;padding-inline:16px;padding-block:0 48px}
.wrap{max-width:1180px;margin:0 auto;display:grid;grid-template-columns:220px minmax(0,1fr);gap:40px}
@media (max-width:900px){.wrap{grid-template-columns:minmax(0,1fr)}nav.toc{position:static;display:none}}
header.top{grid-column:1/-1;padding-block:40px 8px;border-bottom:1px solid var(--line)}
header .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
header h1{font-family:var(--serif);font-weight:500;font-size:clamp(28px,3.4vw,40px);line-height:1.15;margin:8px 0 10px;text-wrap:balance}
header .lede{max-width:72ch;color:var(--ink-2);margin:0}
header .meta{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:14px;font-family:var(--mono);font-size:12.5px;color:var(--muted)}
nav.toc{position:sticky;top:env(safe-area-inset-top,0px);align-self:start;padding-top:28px;font-size:13.5px}
nav.toc a{display:block;color:var(--ink-2);text-decoration:none;padding:4px 0 4px 10px;border-left:2px solid var(--line)}
nav.toc a:hover{color:var(--accent);border-left-color:var(--accent)}
main{min-width:0;padding-top:20px}
main>section{padding-block:22px 10px;border-bottom:1px solid var(--line)}
h2{font-family:var(--serif);font-weight:500;font-size:26px;margin:0 0 12px;text-wrap:balance}
h3{font-family:var(--serif);font-weight:500;font-size:20px;margin:26px 0 8px;text-wrap:balance}
h4{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:22px 0 8px;font-weight:600}
p,li{max-width:72ch}
code,pre{font-family:var(--mono);font-size:13px}
pre{background:var(--panel);border:1px solid var(--line);padding:14px 16px;overflow-x:auto;line-height:1.5;max-width:100%}
.muted{color:var(--muted)}.small{font-size:12.5px}.strong{font-weight:600}
.ok{color:var(--green);font-weight:600}.bad{color:var(--red);font-weight:600}
.k-proven{color:var(--green);font-weight:600}.k-exact{color:var(--accent);font-weight:600}.k-safe{color:var(--amber)}.k-none{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--panel);border:1px solid var(--line);padding:12px 14px}
.tile-l{font-size:12px;color:var(--muted);letter-spacing:.02em}
.tile-v{font-family:var(--mono);font-size:22px;font-variant-numeric:tabular-nums;margin-top:4px;line-height:1.2}
.tile .sub,.name .sub{display:block;font-family:var(--sans);font-size:12px;color:var(--muted);margin-top:4px;font-weight:400}
.tablewrap{overflow-x:auto;border:1px solid var(--line);background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}
th{font-size:12px;letter-spacing:.04em;color:var(--muted);font-weight:600;background:var(--paper);position:sticky;top:0}
td.num,th.num{text-align:right;font-family:var(--mono)}
td.name{font-weight:600;white-space:normal;min-width:150px}
tr.m-new td{background:color-mix(in srgb,var(--accent-soft) 45%,transparent)}
tr.m-ref td{color:var(--muted);font-style:italic}
table.plain td,table.plain th{white-space:normal}
svg.chart{width:100%;height:auto;max-width:100%;display:block;margin:6px 0}
svg .axis{stroke:var(--line);stroke-width:1}
svg .lbl{font-family:var(--mono);font-size:11px;fill:var(--muted)}
svg .val{font-family:var(--mono);font-size:11px;fill:var(--ink-2)}
svg .mark{font-size:13px}svg .k-proven{fill:var(--green)}svg .k-exact{fill:var(--accent)}svg .k-safe{fill:var(--amber)}svg .k-none{fill:var(--muted)}
svg .grid{stroke:var(--line);stroke-width:1}
svg .s-legacy{stroke:var(--amber);stroke-width:1.8}svg .s-new{stroke:var(--accent);stroke-width:1.8}
svg .s-legacy-dot{fill:var(--amber)}svg .s-new-dot{fill:var(--accent)}svg .dashed{stroke-dasharray:4 4;stroke-width:1.2}
.sw{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:4px}.sw.s-legacy{background:var(--amber)}.sw.s-new{background:var(--accent)}
svg .bar-pos{fill:var(--accent)}svg .bar-neg{fill:var(--amber)}svg .bar-zero{fill:var(--line)}
.legend{font-size:12.5px;color:var(--muted);margin:0 0 8px}
.callout{border-left:3px solid var(--accent);background:var(--accent-soft);padding:10px 14px;margin:14px 0;max-width:80ch}
.callout.warn{border-left-color:var(--amber);background:var(--amber-soft)}
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:12px 0;counter-reset:step}
.flow div{background:var(--panel);border:1px solid var(--line);padding:10px 12px;font-size:13.5px;position:relative;counter-increment:step}
.flow div::before{content:counter(step);font-family:var(--mono);color:var(--accent);font-size:12px;display:block;margin-bottom:4px}
.flow b{display:block;margin-bottom:2px}
.badge{display:inline-block;font-family:var(--mono);font-size:11.5px;padding:1px 7px;border:1px solid var(--line);border-radius:3px;color:var(--ink-2);margin-right:4px}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
"""

    def ov_line(title, s):
        d = s["d_new_legacy"]
        return (f"<tr><td>{title}</td><td class='num'>{s['n']}</td><td class='num'>{s['legal']['new']}/{s['n']}</td>"
                f"<td class='num'>{s['wins']['legacy']} / {s['wins']['new']}</td><td class='num'>{s['cert']['legacy']} / {s['cert']['new']}</td>"
                f"<td class='num'>{s['proven']['legacy']} / {s['proven']['new']}</td>"
                f"<td class='num'>{statistics.mean(d):+.2f}</td>"
                f"<td class='num'>{(statistics.mean(s['d_new_rich']) if s['d_new_rich'] else float('nan')):+.2f}</td>"
                f"<td class='num'>{len(s['bound_viol'])}</td></tr>")

    overview_html = ""
    if overview:
        overview_html = ("<div class='tablewrap'><table class='plain'><thead><tr><th>数据集</th><th class='num'>算例</th><th class='num'>新方法完整合法</th>"
                         "<th class='num'>最好软分 旧/新</th><th class='num'>精确 U 旧/新</th><th class='num'>证明最优 旧/新</th>"
                         "<th class='num'>新−旧 均值</th><th class='num'>新−Rich60s 均值</th><th class='num'>新 U 低于历史 I</th></tr></thead><tbody>"
                         + "".join(ov_line(t, s) for t, s in overview) + "</tbody></table></div>")
    else:
        overview_html = "<p class='muted'>Formal24 结果尚未完成。</p>"

    page = f"""<title>座位保护列生成重做报告</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;600&family=Noto+Serif+SC:wght@500&display=swap">
<style>{css}</style>
<div class="wrap">
<header class="top">
  <div class="eyebrow">seat-protect-solver · feat/cg-gurobi-direct-pricing</div>
  <h1>座位保护列生成重做：Python + Gurobi 直接定价</h1>
  <p class="lede">旧 HiGHS 列生成的证书层经审计确认不可信后归档；本报告说明从零重写的 Dantzig–Wolfe 直接定价列生成的建模、算法流程、正确性验证，以及与项目启发式、旧实现、Rich 60 s 参考在小/中/大规模上的逐算例对比。</p>
  <div class="meta"><span>日期 2026-09-24</span><span>机器 macOS · 8 核</span><span>Gurobi 12.0.3 · Python 3.12</span><span>时限 新/旧 150 s · 启发式 5 s</span><span>所有分数由独立评估器重算</span></div>
</header>
<nav class="toc">
  <a href="#s0">总览</a><a href="#s1">为什么重做</a><a href="#s2">建模</a><a href="#s3">算法流程</a><a href="#s4">正确性验证</a><a href="#s5">实验设计</a>
  <a href="#r1">Formal24 正向</a><a href="#r2">Formal24 反向</a><a href="#rt">求解时间</a><a href="#r600">600 s 长预算</a><a href="#s6">审计与修复</a><a href="#r3">附录：公开验证集</a><a href="#s7">结论与后续</a>
</nav>
<main>
<section id="s0"><h2>总览</h2>{overview_html}
<p class="small muted">"最好软分"含并列；"精确 U"= 根 LP 全组定价闭合；"证明最优"= 分支定价前沿清空且全部节点认证。"新 U 低于历史 I"是界有效性的一致性检查，任何非零都意味着界有错。</p></section>

<section id="s1"><h2>1 · 为什么重做</h2>
<p>对旧列生成（8100 行、四种定价器调度、HiGHS）的审计用穷举复现了三处高严重度证书缺陷：DFS 在"只找负列"模式下把历史候选的检验数当作域最小值上报（CG-01）；历史候选不受分支域约束（CG-02）；跨对偶搬运界里条件 SSR 标志对偶的键少一维、增量恒为 0（H2）。三者叠加可在零次精确定价下输出 <code>proven_optimal=True</code>。探针还显示调度层在真实数据上退化：标签 DP 从未启用，≥7 人组 DFS 从不闭合却每次先跑满 3–15 s，快速轮与精确轮在同一对偶上哈希级重复。结论：旧实现的 U 与 proven 标记不可信，遂归档旧代码，仅沿用其建模信息，以 Gurobi 从零重写直接定价列生成。</p>
<h3>1.1 旧列生成的历史记录（2026-08-13 实验说明）</h3>
<p>当时的正式协议是列生成 3600 s、单次 MP2 120 s、4 组并行。在那一版 24 例语料上：17/24 得到 U，其中仅 3 例为精确 LP 上界、14 例为逐组安全下界修正的安全 U，7 例在 3600 s 内无界；无界的 7 例中最耗时组均为 7–10 人，其中 6 例叠加陪护/SSR/保护空座，单组累计定价时间约 995–3237 s。受控实验显示单组从 8 人到 9 人时 DFS 节点从 6 万跃升到 100 万以上，120 s MP2 时限内不再闭合。启发式 H5 相对列池整数解 I 平均差 14.3%（最大 46.5%），H20 平均差 8.5%。</p>
<div class="callout warn">这份记录<b>不能</b>与本报告的 Formal24 数字直接对比：其语料是 generator v3.2 之前的版本（每例强制含 10 人组，同名算例的分值量级与现在不同，正向 I 值比现在高 0.9–1.8 倍），舱位当时仍是软惩罚项而非硬约束；其 14 例安全 U 来自审计前代码，正是 CG-01 假下界会污染的路径。本报告把它作为"旧路线在长预算下也难以给出可信证书"的背景证据，原文存档于 <code>reports/audit-2026-09-23/参考/</code>。</div></section>

<section id="s2"><h2>2 · 建模</h2>
<p>全部取自《任务需求、数学模型与实现说明》第 3–6 章，以 <code>allocation_evaluator.py</code> 为业务语义的唯一裁判：距离/价值/属性分、紧凑度、婴儿影响系数都直接调用评估器函数计算，列成本与独立评分逐位一致。</p>
<h3>2.1 可行域与输入校验（instance.py）</h3>
<ul><li>D<sub>p</sub>：舱位一致；<code>newSeat</code> 固定座位只保留该座；出口排按 SSR 的 <code>allow_exit_row</code>；<code>requires_bassinet / requires_aisle</code>；双侧保护旅客在 <code>require_two_real_neighbors</code> 下要求恰有两个真实邻座（邻座是否跨过道由配置决定）。</li>
<li>输入层拒绝：混舱组、重复 groupId 或 (groupId, hostnum)、非整数 id、固定座位缺失/冲突/落在另一固定双侧旅客的保护空座上、分舱容量必要条件（§4.10）。</li>
<li>个人分 h<sub>ps</sub>、紧凑度 F<sub>c</sub> = w<sub>c</sub>(β<sub>x</sub>·max|X−X̄| + β<sub>y</sub>·max|Y−Ȳ|)、婴儿系数 B(u,v)（同子排或前后排、同舱、有向）。</li></ul>
<h3>2.2 定价子问题 MP2（pricing.py，每组一个持久化 Gurobi MIP）</h3>
<pre>变量  x_ps ∈{{0,1}} (s∈D_p)   z_pst ∈{{0,1}} 单侧保护空座选择 (t∈N⁰(s))   dev_x,dev_y ≥0   e_ℓ ∈{{0,1}} 条件SSR激活   w_uv∈[0,1] 同组婴儿对
约束  Σ_s x_ps = 1                                  全员分配
      Σ_t z_pst = x_ps                              单侧保护恰选一个邻座
      R_t = Σ_p x_pt + Σ z_{{p s t}} + Σ_{{双侧}} Σ_{{s: t∈N(s)}} x_ps ≤ 1   物理资源互斥（与主问题座位行同口径）
      x_ps ≤ Σ_{{q∈E_g,q≠p}} Σ_{{t∈N_p(s)}} x_qt          陪护：同组合格陪护者相邻
      dev_x ≥ ±(Σ_s X_s x_ps − (1/n)Σ_p Σ_s X_s x_ps)     紧凑度重心最大偏差（w_c ≤ 0 时精确）
      e_ℓ ≥ x_ps (带标志旅客, s∈ℓ)；m_ℓτ = Σ_{{ssr=τ}} Σ_{{s∈ℓ}} x_ps   条件 SSR 激活与类型人数
      w_uv ≥ I_u + O_v − 1,  w_uv ≤ I_u,  w_uv ≤ O_v        同组婴儿对乘积
目标  min  c_k − π_g − Σ_t λ_t R_t − Σ μ^e e_ℓ − Σ μ^m m_ℓτ − Σ ν_uv (I_u + O_v)
      c_k = −Σ h_ps x_ps + |w_c|(β_x dev_x + β_y dev_y) + Σ_{{同组对}} B(u,v) w_uv</pre>
<p>列生成后用评估器函数独立重算成本，与 MIP 目标差超过 1e-5 即抛错。证书：<code>Optimal</code> → 精确最小约化成本；<code>Cutoff = −tol</code> 报 <code>CUTOFF</code> → 证明不存在负列；<code>TimeLimit</code> / 早停 → 下界取 <code>ObjBound</code>，不冒充证书。</p>
<h3>2.3 受限主问题 MP1（master.py，Gurobi LP）</h3>
<pre>Σ_k y_gk + art_g = 1                       组凸性（Phase I 人工列）
Σ_{{g,k}} a_gks y_gk ≤ 1                     座位资源（真实占座 + 保护空座）
Σ_k e_kℓ y_gk − a_ℓτ ≤ 0   每个带标志组       条件 SSR 激活
Σ_{{g,k}} m_kℓτ y_gk + M_ℓτ a_ℓτ ≤ M_ℓτ + 1   激活后该位置每类型至多 1 人
Σ_k (i_ku + o_kv) y_gk − b_uv ≤ 1            婴儿影响 b_uv ≥ I_u + O_v − 1，成本 −B(u,v) ≥ 0
列成本预先加上同组对的 B(u,v)，整数解中同组影响恰好抵消，只留跨组项。</pre></section>

<section id="s3"><h2>3 · 算法流程</h2>
<div class="flow">
<div><b>分舱</b>按预订舱位拆成独立子问题；座位少的舱先算，剩余时间留给最大舱。</div>
<div><b>初始列</b>项目 5 s 启发式的完整合法方案 → 用评估器同款增广路径匹配全局协调单侧保护空座 → 拆成各组列；缺组用零对偶贪心补，贪心完整方案经评估器验证后可作兜底。</div>
<div><b>列生成</b>解 LP → 原始对偶 → 各组并行定价（每组独占 Gurobi Env，6 线程；早停：找到 ≤0.3×上轮最小 rc 的列即停）→ 加入 rc &lt; −tol 的列。</div>
<div><b>认证</b>无新列且全组 Optimal/Cutoff ⇒ 根 LP 认证（精确 U）；有组超时 ⇒ 加倍时限重试；每轮都记录安全界 L<sub>RMP</sub> + Σ min(0, lb<sub>g</sub>)。</div>
<div><b>潜水</b>固定 LP 值最大的分数列并继续定价，只扩列池，不改根界。</div>
<div><b>整数解</b>列池整数 MIP（启发式列作 MIP start；失败退回启发式方案）；结果必须过独立评估器。</div>
<div><b>分支定价</b>先按 (组,座位) 用量、再按 (组,旅客,座位) 用量取最接近 0.5 者二分；节点内完整列生成；受限主问题不可行时进入节点级 Phase I，人工列仍为正且全组闭合才认证不可行；LP 整数点与节点 MIP 结果均须通过评估器才成为 incumbent。</div>
<div><b>合并与证书</b>各舱整数解合并后整体重评估；U 只在全部舱位有证书时求和；<code>proven_optimal</code> 仅当每舱前沿清空且全部节点认证。</div>
</div>
<div class="callout">三级证书：<b>精确 U</b>（根 LP 全组闭合）→ <b>安全 U</b>（L<sub>RMP</sub> + Σ min(0, lb<sub>g</sub>)，lb<sub>g</sub> 取 ObjBound，任一组无有限下界则为空）→ <b>无 U</b>。任何时候都不用受限主问题值或列池整数值冒充上界。</div></section>

<section id="s4"><h2>4 · 正确性验证</h2>
<ul>
<li><b>穷举对照</b>（tests/test_cg_gurobi.py）：6 个语义场景（普通组、单双侧保护、陪护+出口排、条件 SSR 隔离、跨组婴儿、固定座位+保护）旅客—座位全排列求全局最优，断言 U ≥ 最优且 I = 最优。</li>
<li><b>公开验证集 11 例</b>：完整合法、软分与评估器逐位一致、旧实现证明过的 LP 值逐位复现；11 例全部证明最优，两个满载算例（-116.79、-122.025）与旧实现的分支定价结果一致。</li>
<li><b>无效输入</b>：混舱、超容量、非整数 id、固定座位冲突在入口被拒绝；结构不可行（如 CHD 无同组陪护者）由 Phase I 认证并贯通到顶层。</li>
<li><b>自审计</b>：每次定价 MIP 目标 vs 按主问题系数重算的约化成本；每个整数解列成本之和 vs 评估器软分；不一致即抛错，全程未触发。</li>
<li><b>对抗式审计</b>：五维度独立审计 + 每条发现两名核查者驳斥 + 完整性评审，见第 6 节。</li>
</ul></section>

<section id="s5"><h2>5 · 实验设计</h2>
<ul><li><b>数据</b>：Formal24 官方 24 例（正向 3-4-3→3-3、反向 3-3→3-4-3；50/100/150/full × normal/stress/edge；附冻结的 Rich Python 60 s 参考分与历史最好整数解 I）；公开验证集 11 例（6–23 人）仅作正确性回归放在附录。两者都是旧实现正式协议使用过的数据，不另加自生成算例。</li>
<li><b>方法</b>：项目启发式 5 s（V1 生产口径）；旧列生成 150 s（归档版，含其分支定价，4 线程默认）；新列生成 150 s（默认配置，6 定价线程）。新方法 24 例全部以审计修复后代码重跑；旧方法沿用首轮结果，仅正向 150_normal、full_normal 为补记根 LP 认证时刻重跑一次，其中 full_normal 重跑未再拿到精确 U（首轮 −970.49，重跑仅安全界 −962.72），说明该例旧方法的收敛处于 150 s 边界，报告按重跑值记录。</li>
<li><b>指标</b>：完整合法（硬违规/未分配）、软分及 s/v/p/c/b 分项、界与证书类型、相对自身界的 gap、耗时。全部由独立评估器重算；串行运行避免计时干扰。</li></ul>
<h3>5.1 算例画像：同行组规模与收敛状态</h3>
<p>列生成的定价子问题按同行组分解，定价难度随组内人数指数增长（旧记录的受控实验：单组 8 人 6 万节点、9 人 100 万节点）。下表列出 24 例的组规模分布，并标出新方法在 150 s / 600 s 的收敛状态。正反向使用相同的旅客生成规则，组规模分布逐例相同。</p>
{instance_profile_table(f24f, f24r, n600f, n600r)}
<div class="callout">规律：<b>能否收敛几乎只取决于最大组规模</b>。最大组 ≤7 人的 8 例（全部 normal 档与 50_stress）在 150 s 内全部证明最优或根 LP 收敛；最大组 8 人的 4 例（100_stress、full_stress）600 s 内两例证明/收敛、两例仍差 3–4%；最大组 9–10 人的 12 例（全部 edge 档与 150_stress）除 100_edge 外 600 s 仍不能闭合，gap 5–33%。edge 档每例含 3–6 个 ≥7 人组，其中 2–4 个 9–10 人组，这些组的定价 MIP 在 120 s 内不能证明无负列，是收敛的瓶颈；full_edge 还叠加了保护空座密集导致的松弛弱。</div>
<div class="callout warn">口径提醒：新旧列生成都是 150 s，Rich 参考是 60 s 单次运行，启发式是 5 s；跨方法比较看软分与界，不比耗时。相对自身界的 gap 依赖界的紧度，旧实现的安全界在困难例上极松（曾出现 +912 这样的无意义值），其 gap 均值不能当作质量差距。</div></section>

<section id="r1"><h2>6 · 结果</h2>{section("Formal24 正向（3-4-3 → 3-3，150 s）", f24f, ref, "forward:", "官方语料；附冻结 Rich 60 s 参考与历史最好整数解 I。")}</section>
<section id="r2">{section("Formal24 反向（3-3 → 3-4-3，150 s）", f24r, ref, "reverse:")}</section>
<section id="rt"><h3>求解时间（精确方法）</h3>
<p>对精确算法，时间要看"多久拿到证书"而不是墙钟：<b>根 LP 认证用时</b>是所有组定价闭合、拿到精确 U 的时刻，反映定价效率；<b>证明最优用时</b>是分支定价前沿清空的时刻；150 s 内两者都没到的算例给出截停时相对自身界的 gap。{"" if timing_available else "<span class='muted'>（计时补测尚未完成，下表为空或不完整。）</span>"}</p>
{timing_table([("Formal24 正向", f24f), ("Formal24 反向", f24r)])}
<h4>收敛曲线</h4>
{convergence_charts(f24f, ["100_normal", "150_normal", "150_edge", "full_stress"])}
{convergence_charts(f24r, ["100_normal", "150_edge", "full_stress"])}
</section>

<section id="r600"><h2>6.5 · 600 s 长预算：150 s 未收敛的运行能否收敛</h2>
{long_budget_section(f24f, l600f, n600f, ref, "forward:", "Formal24 正向")}
{long_budget_section(f24r, l600r, n600r, ref, "reverse:", "Formal24 反向")}</section>

<section id="s6"><h2>7 · 对新实现的对抗式审计与修复</h2>
<p>5 个维度（硬约束保真、目标一致性、证书有效性、鲁棒性、测试覆盖）各由一名独立审计者阅读代码并构造反例，25 条原始发现逐条交给 2 名持怀疑立场的核查者尝试驳倒（允许 5 s 内的小实例复现），最后由完整性评审列未覆盖项。结果 22 条成立（均被复现）、3 条被驳回；按根因归并修复如下，并新增 5 个回归测试。</p>
<div class="tablewrap"><table class="plain"><thead><tr><th>严重度</th><th>缺陷</th><th>修复</th></tr></thead><tbody>
<tr><td class="bad">高</td><td>分支定价把"(组,座位) 用量已整数但 y 仍分数"的节点当作整数解，用 LP 值冒充成本，可产出评估器判非法的方案并标 proven（6 座 3 人条件 SSR 反例复现）</td><td>新增旅客级分支；只有所有 y∈{{0,1}} 才视为整数点；LP 整数点与节点 MIP 结果必须通过独立评估器才成为 incumbent；无可分支用量而 y 仍分数 → 节点标未认证</td></tr>
<tr><td class="bad">高</td><td>受限主问题 LP 不可行被当作节点不可行并关闭（无 Farkas 定价）</td><td>节点级 Phase I：放开人工列、用 Phase I 对偶定价，人工列仍为正且全组闭合才认证不可行</td></tr>
<tr><td class="bad">高</td><td>多个定价器共享同一 Gurobi Env 而线程池任意调度，违反线程安全契约</td><td>每个定价器独占一个 Env</td></tr>
<tr><td class="bad">高</td><td>整数阶段预留 2 s 硬底，小预算下列生成窗口为负、完整贪心初始列被丢弃</td><td>预留 = min(配置, 15% 预算)；每轮单组时限不超过剩余时间</td></tr>
<tr><td>中</td><td>分舱只传该舱旧座位图，原座位在其他舱的旅客个人分被算成 0</td><td>旧座位图整张传入（仅用于评分）</td></tr>
<tr><td>中</td><td>顶层把某舱已认证的不可行擦成 incomplete / 0.0；不可行返回缺字段</td><td>不可行贯通到顶层 status=infeasible，字段齐全</td></tr>
<tr><td>中</td><td>require_two_real_neighbors=False 时容量条件把双侧保护按 +2 计，拒绝可行输入</td><td>该配置下双侧只按本人 1 座计</td></tr>
<tr><td>中</td><td>启发式失败时没有完整整数兜底（评审补充）</td><td>资源互斥的完整贪心方案经评估器验证后作为 MIP start 与兜底</td></tr>
<tr><td>低</td><td>w_b 符号未校验；oldSeat:null 抛 AttributeError；字符串 id 与评估器键不匹配；重复 groupId；固定座位落在他人保护空座；result.dive 恒 None；CLI JSON 含 Infinity；README 措辞过强</td><td>入口校验 + 输出清洗 + 文档措辞</td></tr>
</tbody></table></div>
<p>被驳回的 3 条：异常路径下 Env 未释放（实际有释放）；启发式时限未按舱位截止裁剪（影响可忽略）；字符串 id 由评估器容忍（实际会键不匹配，已改为入口拒绝）。审计确认的问题都在分支定价与鲁棒性层，根 LP 界与列成本一致性未发现问题；修复前跑出的新方法结果全部以修复后代码重跑，本报告数字均来自修复后版本。</p>
<h3>7.1 LP 松弛强度的观察</h3>
<p>在官方 forward:full_edge（162 人、17 名保护旅客、36 名 SSR）上做 900 s 单线程诊断（不开分支定价，只看根 LP 与列池整数解）：{{FULL_EDGE_DIAG}}原因是保护空座在 LP 松弛里可被两列各以 0.5 权重"共享"（一列把座位 t 当保护空座、另一列让旅客坐 t，资源行之和仍为 1）。这是模型松弛本身的弱点；分支定价能逐步修正但 150 s 内到不了。加强方向：对"座位 + 邻座保护"的 clique 割，或把单侧保护选择提升为主问题变量。</p></section>

<section id="s7"><h2>8 · 结论与后续</h2>
<ul>
<li>新实现的正确性有三重保障：穷举对照、评估器逐位复核、旧实现证明值复现；审计确认的缺陷全部修复并有回归测试。</li>
<li>{{CONCLUSION_SCALE}}</li>
<li>后续：加强松弛（clique 割 / 单侧保护主问题化）；把 Rich 60 s 的搜索能力作为初始列注入的对照；将本报告的基准脚本纳入 CI 的"手动/长跑"层。</li>
</ul></section>

<section id="r3"><h2>附录 · 公开验证集（正确性回归）</h2>
<p class="small muted">仓库自带的 11 个小规模算例（6–23 人），只用于正确性回归：小到可以穷举验证，且旧实现在其上有已证明的最优值可供逐位复现。不参与主结论，也不再重复运行。</p>
{section("公开验证集（60 s）", fx, budget="60 s")}</section>
</main></div>
"""
    diag = _full_edge_diag_text()
    concl = _conclusion_scale(f24f, f24r, ref)
    page = page.replace("{FULL_EDGE_DIAG}", diag).replace("{CONCLUSION_SCALE}", concl)
    OUT.write_text(page, encoding="utf-8")
    print(f"written {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
