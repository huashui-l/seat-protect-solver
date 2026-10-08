import itertools, json, math, sys
sys.path.insert(0, ".")
from src import allocation_evaluator as evaluator
from src import aircraft_seatmap_generator as gen
from src import exact_column_generation as ecg
cfg = json.loads(open("config.json", encoding="utf-8-sig").read())
weights = dict(cfg["weights"]); weights["w_c"] = 0.0
tol = ecg._limits(cfg)["tolerance"]
c = gen.CabinConfig.from_dict({"name":"Economy","startRow":1,"endRow":2,"seatLabels":["A","B","C"],
    "aisleAfter":["C"],"seatClass":"Economy","bassinetRows":[],"exitRows":[],"extraLegroomRows":[],"toiletRows":[]})
sm = gen.generate_aircraft("R",[c],[]); topo = evaluator.SeatTopology(sm["seats"])
def pax(i, old):
    return {"hostnum":i,"cabin":"Economy","ssr":"","needCared":"N","oldSeat":{"seatNum":old,"seatValue":""},
            "mandatoryRule":{"needBothSideEmpty":"N","needSingleSideEmpty":"N","sameSubRowNoOtherSSR":"N","sameRowNoOtherSSR":"N"},"optionRule":[]}
g = {"groupId":1,"groupType":"communityType","PNR":"R1","psrs":[pax(1,"1A"),pax(2,"1B"),pax(3,"1C")]}
baby = {}
duals = ecg.MasterDuals(group={1:10.0})
def in_domain(sig, forced, forbidden):
    s = set(sig)
    return not (s & forbidden) and forced <= s
for label, forced, forbidden in (
    ("禁止 旅客0→1A", set(), {(0,"1A",())}),
    ("强制 旅客0→2A", {(0,"2A",())}, set()),
):
    cache = ecg._build_group_pricing_cache(g, topo, topo, weights, cfg)
    cache.historical_mip_start = ((0,"1A",()),(1,"1B",()),(2,"1C",()))
    # 域内穷举
    best, best_sig = math.inf, None
    for combo in itertools.product(*cache.all_options):
        if len({r for p in combo for r in p.resources}) != sum(len(p.resources) for p in combo): continue
        sig = tuple(p.signature for p in combo)
        if not in_domain(sig, forced, forbidden): continue
        pat = ecg._pattern_from_placements(g, combo, topo, weights, cfg, baby)
        rc = ecg._master_column_reduced_cost(pat, duals, baby)
        if rc < best: best, best_sig = rc, sig
    print(f"\n[{label}] 域内穷举最小={best:.4f} 达到者={[(s[1]) for s in best_sig]}")
    for stop in (True, False):
        cache.dfs_workspace = None
        r = ecg._price_group_exact_dfs(g, topo, weights, cfg, duals, baby, forced or None, forbidden or None,
                                       None, cache, exact=True, phase_one=False, stop_on_negative=stop)
        rows = []
        for p in r.patterns:
            rc = ecg._master_column_reduced_cost(p, duals, baby)
            rows.append(f"{[s[1] for s in p.signature]} rc={rc:.3f} {'域内' if in_domain(p.signature, forced, forbidden) else '<<域外>>'}")
        print(f"  stop_on_negative={stop}: 报告rc={r.reduced_cost:.4f} lb={r.reduced_cost_lower_bound:.4f} proven={r.proven_optimal} seeded={r.dfs_incumbent_seeded} term={r.termination}")
        for row in rows: print("     ", row)
        has_domain_opt = any(in_domain(p.signature, forced, forbidden) and abs(ecg._master_column_reduced_cost(p,duals,baby)-best)<1e-9 for p in r.patterns)
        print(f"      域内最优列是否被返回: {has_domain_opt}")
    # 调度器入口
    cache.dfs_workspace = None
    r = ecg.price_group_exact(g, topo, topo, weights, cfg, duals, baby, forced or None, forbidden or None, None, cache, None,
                              exact=True, phase_one=False, stop_on_negative=True)
    outs = [ [s[1] for s in p.signature] for p in r.patterns if not in_domain(p.signature, forced, forbidden)]
    print(f"  调度器 price_group_exact: 报告rc={r.reduced_cost:.4f} proven={r.proven_optimal} 返回{len(r.patterns)}列, 域外列={outs}")
