import copy, itertools, json, math, sys
sys.path.insert(0, ".")
from src import allocation_evaluator as evaluator
from src import aircraft_seatmap_generator as gen
from src import exact_column_generation as ecg
base = json.loads(open("config.json", encoding="utf-8-sig").read())
weights = dict(base["weights"]); weights["w_c"] = 0.0
cfg = copy.deepcopy(base)
c = gen.CabinConfig.from_dict({"name":"Economy","startRow":1,"endRow":2,"seatLabels":["A","B","C"],
    "aisleAfter":["C"],"seatClass":"Economy","bassinetRows":[],"exitRows":[],"extraLegroomRows":[],"toiletRows":[]})
sm = gen.generate_aircraft("R",[c],[]); topo = evaluator.SeatTopology(sm["seats"])
def px(i, old, ssr="", row_flag="N"):
    return {"hostnum":i,"cabin":"Economy","ssr":ssr,"needCared":"N",
            "oldSeat":{"seatNum":old,"seatValue":""},
            "mandatoryRule":{"needBothSideEmpty":"N","needSingleSideEmpty":"N",
                             "sameSubRowNoOtherSSR":"N","sameRowNoOtherSSR":row_flag},
            "optionRule":[]}
g = {"groupId":7,"groupType":"communityType","PNR":"R7",
     "psrs":[px(1,"1A","CHD","Y"), px(2,"1B"), px(3,"1C")]}
cache = ecg._build_group_pricing_cache(g, topo, topo, weights, cfg)
row = cache.universe[0].ssr_flag_locations[0][1]
key4 = (7, "row", row, "CHD")
def brute(duals):
    best = math.inf
    for combo in itertools.product(*cache.all_options):
        if len({r for p in combo for r in p.resources}) != sum(len(p.resources) for p in combo): continue
        if not ecg._caregiver_ok(g, combo, topo, cfg): continue
        pat = ecg._pattern_from_placements(g, combo, topo, weights, cfg, {})
        best = min(best, ecg._master_column_reduced_cost(pat, duals, {}))
    return best
print(f"{'旧flag对偶':>10} {'新flag对偶':>10} {'真实新最小值':>12} {'旧真值(锚)':>11} {'搬运结果':>10} {'误差':>9}  判定")
for old_f, new_f in ((0.0,-4.0), (-4.0,0.0), (-4.0,-1.0), (-2.0,0.0), (-6.0,-2.0)):
    od = ecg.MasterDuals(group={7:0.0}, ssr_flag={key4: old_f})
    nd = ecg.MasterDuals(group={7:0.0}, ssr_flag={key4: new_f})
    old_true, new_true = brute(od), brute(nd)
    if not (math.isfinite(old_true) and math.isfinite(new_true)): 
        print(f"{old_f:10.1f} {new_f:10.1f}  (域空/无穷，跳过)"); continue
    t = ecg._transport_pricing_lower_bound(g, cache, od, nd, old_true)
    unsafe = t > new_true + 1e-9
    print(f"{old_f:10.1f} {new_f:10.1f} {new_true:12.4f} {old_true:11.4f} {t:10.4f} {t-new_true:9.4f}  "
          f"{'<<< 非严格下界（不安全）' if unsafe else 'ok'}")
