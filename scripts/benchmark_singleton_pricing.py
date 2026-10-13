"""Same-dual microbenchmark of exact singleton MIP versus full enumeration."""
import argparse
import random
import statistics
import time

import gurobipy as gp

from scripts.run_cg_acceleration_study import write
from src.cg_gurobi.instance import Instance
from src.cg_gurobi.pricing import GroupPricer, Duals
from src.cg_acceleration.singleton import SingletonPricer
from tests import test_cg_gurobi as fixtures


def main():
    parser=argparse.ArgumentParser()
    from pathlib import Path
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--vectors",type=int,default=50)
    args=parser.parse_args()
    cfg=fixtures._config()
    rng=random.Random(147)
    results=[]
    with gp.Env(empty=True) as env:
        env.setParam("OutputFlag",0);env.start()
        for rows in (16,56):
            sm=fixtures._seatmap(rows,["A","B","C","D","E","F"],bassinet_rows=(1,))
            for kind in ("ordinary","single_protection","both_protection","ssr_flag"):
                p=fixtures._pax(1,"2B",single="Y" if kind=="single_protection" else "N",
                    both="Y" if kind=="both_protection" else "N",
                    ssr="UM" if kind=="ssr_flag" else "",rowflag="Y" if kind=="ssr_flag" else "N")
                groups=fixtures._groups((1,[p]),(2,[fixtures._pax(1,"1A","BSCT"),fixtures._pax(2,"1B")]))
                inst=Instance(sm["seats"],sm["seats"],groups,cfg["weights"],cfg)
                fast=SingletonPricer(inst,inst.groups[0],env)
                mip=GroupPricer(inst,inst.groups[0],env)
                records=[]
                for i in range(args.vectors):
                    duals=Duals(convexity={1:rng.uniform(-20,20)},
                        seat={s:-rng.random()*10 for s in inst.seat_ids},
                        ssr_activation={(1,loc,tau):-rng.random() for loc in inst.flag_locations for tau in inst.ssr_types},
                        ssr_count={(loc,tau):-rng.random() for loc in inst.flag_locations for tau in inst.ssr_types},
                        baby={pair:-rng.random() for pair in inst.baby_pairs})
                    measured={}
                    order=[("mip",mip),("enumeration",fast)]
                    if i%2:order.reverse()
                    for name,pricer in order:
                        start=time.perf_counter()
                        out=pricer.price(duals,phase_one=False,time_limit=10,use_cutoff=False)
                        measured[name]=dict(seconds=time.perf_counter()-start,rc=out.best_reduced_cost,certified=out.certified)
                    assert all(r["certified"] for r in measured.values())
                    assert abs(measured["mip"]["rc"]-measured["enumeration"]["rc"])<1e-6
                    records.append(measured)
                a=sum(r["mip"]["seconds"] for r in records)
                b=sum(r["enumeration"]["seconds"] for r in records)
                results.append(dict(seats=len(sm["seats"]),kind=kind,vectors=args.vectors,
                    mip_seconds=a,enumeration_seconds=b,total_speedup=a/b,
                    median_call_speedup=statistics.median(r["mip"]["seconds"]/r["enumeration"]["seconds"] for r in records),
                    all_exact_values_match=True,records=records))
                fast.model.dispose();mip.model.dispose()
    write(args.output,dict(results=results,
        limitation="Synthetic singleton pricing only; excludes construction and warm start. Does not establish full-CG speedup."))
    for r in results:print(r["seats"],r["kind"],round(r["total_speedup"],3))


if __name__ == "__main__":main()
