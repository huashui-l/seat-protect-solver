import copy, json, math, sys
sys.path.insert(0, ".")
from src import allocation_evaluator as evaluator
from src import aircraft_seatmap_generator as gen
from src import exact_column_generation as ecg
base = json.loads(open("config.json", encoding="utf-8-sig").read())
weights = dict(base["weights"]); weights["w_c"] = 0.0
c = gen.CabinConfig.from_dict({"name":"Economy","startRow":1,"endRow":2,"seatLabels":["A","B","C"],
    "aisleAfter":["C"],"seatClass":"Economy","bassinetRows":[],"exitRows":[],"extraLegroomRows":[],"toiletRows":[]})
sm = gen.generate_aircraft("R",[c],[]); topo = evaluator.SeatTopology(sm["seats"])
def pax(i, old):
    return {"hostnum":i,"cabin":"Economy","ssr":"","needCared":"N","oldSeat":{"seatNum":old,"seatValue":""},
            "mandatoryRule":{"needBothSideEmpty":"N","needSingleSideEmpty":"N","sameSubRowNoOtherSSR":"N","sameRowNoOtherSSR":"N"},"optionRule":[]}
g3 = {"groupId":3,"groupType":"communityType","PNR":"R3","psrs":[pax(1,"1A"),pax(2,"1B"),pax(3,"1C")]}
g1 = {"groupId":4,"groupType":"communityType","PNR":"R4","psrs":[pax(1,"1A")]}
cfg = copy.deepcopy(base)
duals3 = ecg.MasterDuals(group={3:1.0}); duals1 = ecg.MasterDuals(group={4:1.0})
def show(name, r):
    lb = r.reduced_cost_lower_bound
    lbs = '+inf' if lb==math.inf else ('-inf' if lb==-math.inf else f'{lb:.4f}')
    print(f"  {name:38s} term={r.termination:18s} proven={r.proven_optimal!s:5s} lb={lbs}")
# 情形1：强制两人同座 -> 域冲突（forced 集合合法但资源冲突）
forced_conflict = {(0,"1A",()), (1,"1A",())}
# 情形2：forbidden 覆盖某旅客全部放置 -> 域为空
forbid_all = {(0, s, ()) for s in topo.seat_map}
cache = ecg._build_group_pricing_cache(g3, topo, topo, weights, cfg)
print("### 3 人组：强制两人同坐 1A（资源冲突）")
cache.dfs_workspace=None; show("DFS", ecg._price_group_exact_dfs(g3,topo,weights,cfg,duals3,{},forced_conflict,None,None,cache,exact=True,phase_one=False,stop_on_negative=True))
show("complete_domain_mip", ecg._price_group_complete_domain_mip(g3,topo,topo,weights,cfg,duals3,{},forced_conflict,None,None,cache,exact=True,phase_one=False,stop_on_negative=True))
cache.dfs_workspace=None; show("dispatcher price_group_exact", ecg.price_group_exact(g3,topo,topo,weights,cfg,duals3,{},forced_conflict,None,None,cache,None,exact=True,phase_one=False,stop_on_negative=True))
print("### 3 人组：禁止旅客0 的全部放置（域为空）")
cache.dfs_workspace=None; show("DFS", ecg._price_group_exact_dfs(g3,topo,weights,cfg,duals3,{},None,forbid_all,None,cache,exact=True,phase_one=False,stop_on_negative=True))
show("complete_domain_mip", ecg._price_group_complete_domain_mip(g3,topo,topo,weights,cfg,duals3,{},None,forbid_all,None,cache,exact=True,phase_one=False,stop_on_negative=True))
cache.dfs_workspace=None; show("dispatcher price_group_exact", ecg.price_group_exact(g3,topo,topo,weights,cfg,duals3,{},None,forbid_all,None,cache,None,exact=True,phase_one=False,stop_on_negative=True))
print("### 1 人组：禁止全部放置（小组精确路径）")
c1 = ecg._build_group_pricing_cache(g1, topo, topo, weights, cfg)
show("small_exact", ecg._price_group_small_exact(g1,topo,weights,cfg,duals1,{},None,forbid_all,c1,phase_one=False))
show("dispatcher price_group_exact", ecg.price_group_exact(g1,topo,topo,weights,cfg,duals1,{},None,forbid_all,None,c1,None,exact=True,phase_one=False,stop_on_negative=True))
print("\n### 该 lb 进入 _safe_master_cost_lower_bound 的后果")
for name, lb in (("-inf(DFS/MIP)", -math.inf), ("+inf(small_exact)", math.inf)):
    print(f"  {name:18s} ->", ecg._safe_master_cost_lower_bound(-100.0, {3:{},5:{}}, {3: lb, 5: -1.0}))
