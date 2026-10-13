"""Full-domain singleton enumeration vs the unchanged Gurobi pricer."""
import random
import unittest

import gurobipy as gp

from src.cg_gurobi.instance import Instance
from src.cg_gurobi.pricing import Duals, GroupPricer, reduced_cost
from src.cg_acceleration.singleton import SingletonPricer
from tests import test_cg_gurobi as fixtures


class SingletonEnumerationTests(unittest.TestCase):
    def test_all_dual_families_and_protection_options_match_exact_mip(self):
        cfg=fixtures._config()
        sm=fixtures._seatmap(2,["A","B","C","D"],bassinet_rows=(1,))
        passengers=[fixtures._pax(1,"1A"),fixtures._pax(1,"1B",both="Y"),
                    fixtures._pax(1,"1B",single="Y"),fixtures._pax(1,"1B","UM",rowflag="Y"),
                    fixtures._pax(1,"1A","UM",subflag="Y"),fixtures._pax(1,"1A",new="1A")]
        rng=random.Random(147)
        with gp.Env(empty=True) as env:
            env.setParam("OutputFlag",0);env.start()
            for passenger in passengers:
                groups=fixtures._groups((1,[passenger]),(2,[fixtures._pax(1,"1C","BSCT"),fixtures._pax(2,"1D")]))
                inst=Instance(sm["seats"],sm["seats"],groups,cfg["weights"],cfg)
                fast=SingletonPricer(inst,inst.groups[0],env)
                exact=GroupPricer(inst,inst.groups[0],env)
                for phase in (True,False):
                    for _ in range(4):
                        duals=Duals(convexity={1:rng.uniform(-10,10)},
                            seat={s:-rng.random()*3 for s in inst.seat_ids},
                            ssr_activation={(1,loc,tau):-rng.random() for loc in inst.flag_locations for tau in inst.ssr_types},
                            ssr_count={(loc,tau):-rng.random() for loc in inst.flag_locations for tau in inst.ssr_types},
                            baby={pair:-rng.random() for pair in inst.baby_pairs})
                        with self.subTest(passenger=passenger,phase=phase):
                            f=fast.price(duals,phase_one=phase,time_limit=5,use_cutoff=False)
                            e=exact.price(duals,phase_one=phase,time_limit=5,use_cutoff=False)
                            self.assertTrue(f.certified and e.certified)
                            self.assertAlmostEqual(e.best_reduced_cost,f.best_reduced_cost,places=6)
                            minimum=min(reduced_cost(c,duals,phase) for c in fast.options())
                            self.assertAlmostEqual(minimum,f.lower_bound,places=6)
                            self.assertTrue(all(reduced_cost(c,duals,phase)<-1e-6 for c in f.columns))
                fast.model.dispose();exact.model.dispose()

    def test_timeout_never_certifies_a_partial_enumeration(self):
        cfg=fixtures._config();sm=fixtures._seatmap(2,["A","B","C"])
        groups=fixtures._groups((1,[fixtures._pax(1,"1A")]))
        inst=Instance(sm["seats"],sm["seats"],groups,cfg["weights"],cfg)
        with gp.Env(empty=True) as env:
            env.setParam("OutputFlag",0);env.start()
            fast=SingletonPricer(inst,inst.groups[0],env)
            out=fast.price(Duals(),phase_one=False,time_limit=0)
            self.assertFalse(out.certified)
            self.assertEqual(float('-inf'),out.lower_bound)
            fast.model.dispose()


if __name__ == "__main__":
    unittest.main()
