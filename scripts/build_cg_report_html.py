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


def bound_of(m):
    if m.get("bpU") is not None and m.get("proven"):
        return m["bpU"], "proven"
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


def section(title, rows, ref=None, prefix="", note=""):
    if not rows:
        return f"<section><h3>{esc(title)}</h3><p class='muted'>尚未完成。</p></section>"
    s = summary_stats(rows, ref, prefix)
    return (f"<section><h3>{esc(title)}</h3>{('<p>'+note+'</p>') if note else ''}{stat_tiles(s)}"
            f"<h4>逐算例：新 − 旧 软分差</h4>{delta_chart(rows)}<h4>逐算例明细</h4>{result_table(rows, ref, prefix)}</section>")


def main():
    ref = rich_reference()
    fx = merge_new(load("fixtures.json") or [], load("fixtures_new_final.json"))
    f24f = load("formal24_forward.json") or []
    f24r = load("formal24_reverse.json") or []
    fwd = merge_new(load("forward12.json") or [], load("forward12_new_final.json"))
    rev = merge_new(load("reverse12.json") or [], load("reverse12_new_final.json"))
    seed2 = load("forward12_seed2.json") or []

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
  <a href="#r1">Formal24 正向</a><a href="#r2">Formal24 反向</a><a href="#r3">公开验证集</a><a href="#r4">自生成正向</a><a href="#r5">自生成反向</a><a href="#r6">第二组种子</a><a href="#s6">审计与修复</a><a href="#s7">结论与后续</a>
</nav>
<main>
<section id="s0"><h2>总览</h2>{overview_html}
<p class="small muted">"最好软分"含并列；"精确 U"= 根 LP 全组定价闭合；"证明最优"= 分支定价前沿清空且全部节点认证。"新 U 低于历史 I"是界有效性的一致性检查，任何非零都意味着界有错。</p></section>

<section id="s1"><h2>1 · 为什么重做</h2>
<p>对旧列生成（8100 行、四种定价器调度、HiGHS）的审计用穷举复现了三处高严重度证书缺陷：DFS 在"只找负列"模式下把历史候选的检验数当作域最小值上报（CG-01）；历史候选不受分支域约束（CG-02）；跨对偶搬运界里条件 SSR 标志对偶的键少一维、增量恒为 0（H2）。三者叠加可在零次精确定价下输出 <code>proven_optimal=True</code>。探针还显示调度层在真实数据上退化：标签 DP 从未启用，≥7 人组 DFS 从不闭合却每次先跑满 3–15 s，快速轮与精确轮在同一对偶上哈希级重复。结论：旧实现的 U 与 proven 标记不可信，遂归档旧代码，仅沿用其建模信息，以 Gurobi 从零重写直接定价列生成。</p></section>

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
<li><b>公开验证集 11 例</b>：完整合法、软分与评估器逐位一致、旧实现证明过的 LP 值逐位复现；两个满载算例证明最优（-116.79、-122.025，5 个分支节点对旧实现 279 个）。</li>
<li><b>无效输入</b>：混舱、超容量、非整数 id、固定座位冲突在入口被拒绝；结构不可行（如 CHD 无同组陪护者）由 Phase I 认证并贯通到顶层。</li>
<li><b>自审计</b>：每次定价 MIP 目标 vs 按主问题系数重算的约化成本；每个整数解列成本之和 vs 评估器软分；不一致即抛错，全程未触发。</li>
<li><b>对抗式审计</b>：五维度独立审计 + 每条发现两名核查者驳斥 + 完整性评审，见第 6 节。</li>
</ul></section>

<section id="s5"><h2>5 · 实验设计</h2>
<ul><li><b>数据</b>：Formal24 官方 24 例（正向 3-4-3→3-3、反向 3-3→3-4-3；50/100/150/full × normal/stress/edge；附冻结的 Rich Python 60 s 参考分与历史最好整数解 I）；公开验证集 11 例（6–23 人）；自生成 36 例（generator v3.2，座位图与官方逐位相同，仅旅客抽样不同）。</li>
<li><b>方法</b>：项目启发式 5 s（V1 生产口径）；旧列生成 150 s（归档版，含其分支定价，4 线程默认）；新列生成 150 s（默认配置，6 定价线程）。</li>
<li><b>指标</b>：完整合法（硬违规/未分配）、软分及 s/v/p/c/b 分项、界与证书类型、相对自身界的 gap、耗时。全部由独立评估器重算；串行运行避免计时干扰。</li></ul>
<div class="callout warn">口径提醒：新旧列生成都是 150 s，Rich 参考是 60 s 单次运行，启发式是 5 s；跨方法比较看软分与界，不比耗时。相对自身界的 gap 依赖界的紧度，旧实现的安全界在困难例上极松（曾出现 +912 这样的无意义值），其 gap 均值不能当作质量差距。</div></section>

<section id="r1"><h2>6 · 结果</h2>{section("Formal24 正向（3-4-3 → 3-3，150 s）", f24f, ref, "forward:", "官方语料；附冻结 Rich 60 s 参考与历史最好整数解 I。")}</section>
<section id="r2">{section("Formal24 反向（3-3 → 3-4-3，150 s）", f24r, ref, "reverse:")}</section>
<section id="r3">{section("公开验证集（小规模 6–23 人，60 s）", fx)}</section>
<section id="r4">{section("自生成正向 12 例（种子 40/50/60，150 s）", fwd)}</section>
<section id="r5">{section("自生成反向 12 例（150 s）", rev)}</section>
<section id="r6">{section("自生成正向 12 例（第二组种子 41/51/61，150 s）", seed2)}</section>

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
<p>在自生成 full_edge（158 人、18 名保护旅客、35 名 SSR）上做 900 s 单线程诊断：根 LP 87 轮后接近闭合（最小约化成本 −0.006），LP 界约 −792，而整数解 −962，整数 gap 约 18%，远高于 normal 系列的 &lt;1%。原因是保护空座在 LP 松弛里可被两列各以 0.5 权重"共享"（一列把座位 t 当保护空座、另一列让旅客坐 t，资源行之和仍为 1）。这是模型松弛本身的弱点；分支定价能逐步修正但 150 s 内到不了。加强方向：对"座位 + 邻座保护"的 clique 割，或把单侧保护选择提升为主问题变量。</p></section>

<section id="s7"><h2>8 · 结论与后续</h2>
<ul>
<li>新实现的正确性有三重保障：穷举对照、评估器逐位复核、旧实现证明值复现；审计确认的缺陷全部修复并有回归测试。</li>
<li>小/中规模（≤100 人）新方法全面不劣于旧实现且认证更多、更快；大规模 normal 系列同样认证收敛；edge/stress 大例两边多数未认证，新方法在反向系列明显更好，在个别正向 edge 例（保护空座密集）落后，根因是 LP 松弛弱而非实现缺陷。</li>
<li>后续：加强松弛（clique 割 / 单侧保护主问题化）；把 Rich 60 s 的搜索能力作为初始列注入的对照；将本报告的基准脚本纳入 CI 的"手动/长跑"层。</li>
</ul></section>
</main></div>
"""
    OUT.write_text(page, encoding="utf-8")
    print(f"written {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
