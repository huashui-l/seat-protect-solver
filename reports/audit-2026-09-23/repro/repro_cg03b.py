import copy, json, math, sys
sys.path.insert(0, ".")
from src import allocation_evaluator as evaluator
from src import aircraft_seatmap_generator as gen
from src import exact_column_generation as ecg

base = json.loads(open("config.json", encoding="utf-8-sig").read())
weights = dict(base["weights"]); weights["w_c"] = 0.0

def seatmap(rows, labels):
    c = gen.CabinConfig.from_dict({"name":"Economy","startRow":1,"endRow":rows,
        "seatLabels":labels,"aisleAfter":[labels[-1]],"seatClass":"Economy",
        "bassinetRows":[],"exitRows":[],"extraLegroomRows":[],"toiletRows":[]})
    sm = gen.generate_aircraft("REPRO", [c], [])
    return sm, evaluator.SeatTopology(sm["seats"])

def pax(i, old, both="N", single="N", **kw):
    p = {"hostnum":i,"cabin":"Economy","ssr":"","needCared":"N",
         "oldSeat":{"seatNum":old,"seatValue":""},
         "mandatoryRule":{"needBothSideEmpty":both,"needSingleSideEmpty":single,
                          "sameSubRowNoOtherSSR":"N","sameRowNoOtherSSR":"N"},
         "optionRule":[]}
    p.update(kw); return p

print("########## CG-03 复核 ##########")
for require_two in (True, False):
    cfg = copy.deepcopy(base); cfg.setdefault("mandatory_rules", {})
    cfg["mandatory_rules"]["require_two_real_neighbors"] = require_two
    for label, mk in (
        ("固定 1A + 双侧保护", lambda: {"groupId":1,"groupType":"communityType","PNR":"R",
            "psrs":[pax(1,"1A",both="Y",newSeat={"seatNum":"1A"})]}),
        ("非固定 1A + 双侧保护", lambda: {"groupId":1,"groupType":"communityType","PNR":"R",
            "psrs":[pax(1,"1A",both="Y")]}),
        ("双旗(双侧+单侧同时Y)", lambda: {"groupId":1,"groupType":"communityType","PNR":"R",
            "psrs":[pax(1,"1B",both="Y",single="Y")]}),
    ):
        sm, topo = seatmap(1, ["A","B","C"])
        g = mk()
        try:
            ctx = ecg._preprocess_fixed_seats([g], topo, cfg)
        except Exception as exc:
            print(f"  req_two={require_two} [{label}] 预处理拒绝: {exc}")
            continue
        demand = ecg._fixed_seat_resource_demand([g])
        try:
            opts = ecg._placement_options(g, topo, topo, weights, cfg, ctx)
            cells = [[(x.seat_id, x.resources) for x in o] for o in opts]
        except Exception as exc:
            cells = f"放置域失败 {type(exc).__name__}: {exc}"
        print(f"  req_two={require_two} [{label}] demand={demand}/{len(topo.seat_map)} 域={cells}")
        if label.startswith("非固定"):
            for sid, res in (("1A", ("1A","1B")), ("1A", ("1A",)),
                             ("1B", ("1B","1A")), ("1B", ("1B","1C"))):
                assigns = {(1,1): sid}
                gg = copy.deepcopy(g)
                gg["psrs"][0]["_res"] = res
                v, u, det = evaluator.count_hard_constraint_violations(
                    sm["seats"], [gg], assigns, cfg)
                print(f"      评估器: 坐{sid} 独占{res} → violations={v} {det if v else ''}")

print("\n########## 分支域不可行时的下界语义 ##########")
sm3, topo3 = seatmap(2, ["A","B","C"])
cfg = copy.deepcopy(base)
g3 = {"groupId":3,"groupType":"communityType","PNR":"R3",
      "psrs":[pax(1,"1A"), pax(2,"1B"), pax(3,"1C")]}
cache = ecg._build_group_pricing_cache(g3, topo3, topo3, weights, cfg); cache.dfs_workspace = None
duals = ecg.MasterDuals(group={3: 1.0})
impossible = (0, "2A", ("2B",))
print(f"  传入分支禁止=不存在的放置 {impossible}")
for name, fn in (
    ("DFS _price_group_exact_dfs", lambda: ecg._price_group_exact_dfs(
        g3, topo3, weights, cfg, duals, {}, None, {impossible}, None, cache,
        exact=True, phase_one=False, stop_on_negative=True)),
    ("调度器 price_group_exact", lambda: ecg.price_group_exact(
        g3, topo3, topo3, weights, cfg, duals, {}, None, {impossible}, None,
        cache, None, exact=True, phase_one=False, stop_on_negative=True)),
):
    r = fn(); lb = r.reduced_cost_lower_bound
    lbs = '+inf' if lb==math.inf else ('-inf' if lb==-math.inf else f'{lb:.4f}')
    print(f"    {name}: term={r.termination} proven={r.proven_optimal} rc={r.reduced_cost} lb={lbs}")

print("\n########## 同一情况下的 MIP / DP 全路径对照（域内分支禁止） ##########")
# 让组域整体为空：强制两人同座（不可能），走 infeasible_branch
for name, fn in (
    ("_price_group_complete_domain_mip", lambda: ecg._price_group_complete_domain_mip(
        g3, topo3, weights, cfg, duals, {}, {(0,"1A",()), (1,"1A",())}, None, cache,
        exact=True, phase_one=False)),
    ("DP _price_group_homogeneous_row_label_dp", lambda: ecg._price_group_homogeneous_row_label_dp(
        g3, topo3, weights, cfg, duals, {}, {(0,"1A",()), (1,"1A",())}, None, cache,
        exact=True, phase_one=False)),
):
    try:
        r = fn(); lb = r.reduced_cost_lower_bound
        lbs = '+inf' if lb==math.inf else ('-inf' if lb==-math.inf else f'{lb:.4f}')
        print(f"    {name}: term={r.termination} proven={r.proven_optimal} lb={lbs}")
    except Exception as exc:
        print(f"    {name}: 异常 {type(exc).__name__}: {exc}")
