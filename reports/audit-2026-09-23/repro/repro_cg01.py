import itertools, json, math, sys, copy
sys.path.insert(0, ".")
from src import allocation_evaluator as evaluator
from src import aircraft_seatmap_generator as gen
from src import exact_column_generation as ecg

cfg = json.loads(open("config.json", encoding="utf-8-sig").read())
weights = dict(cfg["weights"])
weights["w_c"] = 0.0     # 关闭紧凑度，保留距离项
tol = ecg._limits(cfg)["tolerance"]

cabin = gen.CabinConfig.from_dict({
    "name": "Economy", "startRow": 1, "endRow": 2,
    "seatLabels": ["A", "B", "C"], "aisleAfter": ["C"],
    "seatClass": "Economy", "bassinetRows": [], "exitRows": [],
    "extraLegroomRows": [], "toiletRows": [],
})
seatmap = gen.generate_aircraft("REPRO-2x3", [cabin], [])
seat_ids = [s["seatId"] for s in seatmap["seats"]]
print("seats:", seat_ids)

def build_topo(sm):
    try:
        return evaluator.SeatTopology(sm["seats"])
    except TypeError:
        return evaluator.SeatTopology(seat_entries=sm["seats"])

topo = build_topo(seatmap)

passengers = []
for i, old in enumerate(["1A", "1B", "1C"], start=1):
    passengers.append({
        "hostnum": i, "cabin": "Economy", "ssr": "", "needCared": "N",
        "oldSeat": {"seatNum": old, "seatValue": ""},
        "mandatoryRule": {"needBothSideEmpty": "N", "needSingleSideEmpty": "N",
                          "sameSubRowNoOtherSSR": "N", "sameRowNoOtherSSR": "N"},
        "optionRule": [],
    })
group = {"groupId": 1, "groupType": "communityType", "PNR": "REPRO-1", "psrs": passengers}

cache = ecg._build_group_pricing_cache(group, topo, topo, weights, cfg)
print("universe:", len(cache.universe), "per-passenger options:", [len(o) for o in cache.all_options])
opts_by_pax = [[p.signature for p in opts] for opts in cache.all_options]
for i, sigs in enumerate(opts_by_pax):
    print(f"  pax{i}:", [(s[1], s[2]) for s in sigs])

# 历史候选 = 第 2 排（1A/1B/1C -> 2A/2B/2C）
hist = tuple((i, sid, ()) for i, sid in enumerate(["2A", "2B", "2C"]))
if not all(sig in opts_by_pax[sig[0]] for sig in hist):
    print("历史候选不在域内，改用域内第 2 排等价列")
    hist = tuple(sorted(
        (i, [s for s in sigs if s[1].endswith(("A", "B", "C")) and s[1].startswith("2")] or sigs)
        for i, sigs in enumerate(opts_by_pax)
    ))
cache.historical_mip_start = tuple(hist)
cache.dfs_workspace = None
print("historical_mip_start:", hist)

baby_cost = {}
def brute_force(duals):
    best = math.inf
    for combo in itertools.product(*cache.all_options):
        if len({r for p in combo for r in p.resources}) != sum(len(p.resources) for p in combo):
            continue
        if not ecg._caregiver_ok(group, combo, topo, cfg):
            continue
        pat = ecg._pattern_from_placements(group, combo, topo, weights, cfg, baby_cost)
        rc = ecg._master_column_reduced_cost(pat, duals, baby_cost)
        best = min(best, rc)
    return best

print(f"\n{'pi_g':>7} {'true_min':>10} | {'DFS_term':>14} {'complete':>8} {'proven':>7} {'best_rc':>9} {'lb':>10} | {'lb>true?':>9}")
bad = []
for pi_g in [0.0, -0.3, -0.6, -1.0, 0.5, 1.0, 1.5, 2.0, -2.0]:
    duals = ecg.MasterDuals(group={1: pi_g})
    true_min = brute_force(duals)
    r = ecg._price_group_exact_dfs(
        group, topo, weights, cfg, duals, baby_cost,
        None, None, None, cache, exact=True, phase_one=False,
        stop_on_negative=True,
    )
    lb = r.reduced_cost_lower_bound
    flag = (r.proven_optimal and lb > true_min + 1e-9)
    if flag:
        bad.append((pi_g, true_min, lb))
    print(f"{pi_g:7.2f} {true_min:10.4f} | {r.termination:>14} {str(r.complete if hasattr(r,'complete') else '-'):>8} "
          f"{str(r.proven_optimal):>7} {r.reduced_cost:9.4f} {lb:10.4f} | {'<<< 误报' if flag else 'ok':>9}")

print("\nstop_on_negative=False 对照:")
for pi_g in [0.0, -0.6, 1.0]:
    duals = ecg.MasterDuals(group={1: pi_g})
    true_min = brute_force(duals)
    r = ecg._price_group_exact_dfs(
        group, topo, weights, cfg, duals, baby_cost,
        None, None, None, cache, exact=True, phase_one=False,
        stop_on_negative=False,
    )
    print(f"  pi={pi_g:5.2f} true={true_min:8.4f} proven={r.proven_optimal} lb={r.reduced_cost_lower_bound:9.4f} term={r.termination}")

if bad:
    print("\n=== 搬运污染验证 ===")
    pi_old, true_old, lb_old = bad[0]
    old_duals = ecg.MasterDuals(group={1: pi_old})
    for shift in (0.3, 0.6, 1.0):
        new_duals = ecg.MasterDuals(group={1: pi_old + shift})
        transported = ecg._transport_pricing_lower_bound(group, cache, old_duals, new_duals, lb_old)
        true_new = brute_force(new_duals)
        print(f"  shift={shift:4.2f} 旧lb={lb_old:8.4f} -> 搬运后={transported:8.4f} "
              f"(>= -tol? {transported >= -tol})  新对偶真实最小值={true_new:8.4f} "
              f"{'>>> 存在负列却被跳过' if transported >= -tol and true_new < -tol else ''}")
else:
    print("\n未复现到下界误报（可能被历史候选剪枝路径掩盖）")
