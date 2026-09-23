import ast
import copy
import json
import math
import random
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from src import allocation_evaluator as evaluator
from src import heuristic_seat_allocator as rich
from tests import test_native_rich_repair as repair_tests
from tests.test_native_rich_pipeline import construction_prefix

ROOT = Path(__file__).resolve().parents[1]


class NativeRichLnsTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_lns_local_master_matches_frozen_function(self):
        case = self.synthetic([(30, {}), (30, {}), (10, {}), (10, {}), (20, {}), (20, {})])
        initial = [[0, "1A"], [1, "1D"], [2, "1B"], [3, "1E"], [4, "1C"], [5, "1F"]]
        tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
        lns = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "improve_with_multigroup_lns")
        master = next(n for n in lns.body if isinstance(n, ast.FunctionDef) and n.name == "solve_pattern_master")
        namespace = dict(rich.__dict__, context=SimpleNamespace(assigned_seats={p: seat for p, seat in initial}),
                         keys_by_group={g: [2*g, 2*g+1] for g in range(3)}, local_mip_time_limit=.75,
                         deadline=rich.time.perf_counter() + 120)
        exec(compile(ast.Module(body=[master], type_ignores=[]), "frozen_lns_master", "exec"), namespace)
        partner = {"1A": "1D", "1B": "1E", "1C": "1F", "2A": "2D", "2B": "2E"}
        def option(score, seat): return [score, [seat, partner[seat]], [seat, partner[seat]]]
        calls = [
            dict(component=[0, 1], root=0, options={0: [option(100, "1A"), option(-5, "2A")], 1: [option(2, "1B")]}),
            dict(component=[0, 1], root=0, options={0: [option(100, "1A")], 1: [option(2, "1B")]}),
            dict(component=[0, 1], root=0, options={0: [option(3, "2A")], 1: [option(2, "2A")]}),
            dict(component=[0, 1], root=0, options={0: [], 1: []}),
            # Same physical seats, different passenger ordering: this must remain a column.
            dict(component=[0, 1], root=0, options={0: [[-3, ["1A", "1D"], ["1D", "1A"]]], 1: [option(2, "1B")]}),
            # Conflict on the second passenger's seat only.
            dict(component=[0, 1], root=0, options={0: [option(3, "2A")], 1: [[2, ["2B", "2D"], ["2B", "2D"]]]}),
        ]
        rng = random.Random(20260923)
        for _ in range(64):
            component = rng.sample(range(3), 3)
            calls.append(dict(component=component, root=component[0], options={
                g: [option(rng.uniform(-40, 10), seat) for seat in rng.sample(["1A", "1B", "1C", "2A", "2B"], rng.randint(1, 5))]
                for g in component}))
        expected = []
        for call in calls:
            options = {g: [(score, frozenset(seats), tuple(assignment)) for score, seats, assignment in values]
                       for g, values in call["options"].items()}
            choice = namespace["solve_pattern_master"](tuple(call["component"]), options, call["root"])
            expected.append({str(g): list(seats) for g, seats in choice.items()})
        config = copy.deepcopy(self.config)
        config["input_contract"] = {"seatmaps_by_direction": {"public-test": {"old": "old.json", "new": "new.json"}}}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_context": dict(initial=initial, lns_master=calls)},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"), str(work / "replay.json")],
                                 capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(len(actual), len(expected))
        for call, a, b in zip(calls, actual, expected):
            with self.subTest(call=call): self.assertEqual(a, b)
        self.assertEqual(actual[0], {"0": ["2A", "2D"], "1": ["1B", "1E"]})
        self.assertEqual(actual[1:4], [{}, {}, {}])
        self.assertEqual(actual[4], {"0": ["1D", "1A"], "1": ["1B", "1E"]})
        self.assertEqual(actual[5], {})

    def replay_matching(self, case, initial=None, calls=None, expired=False, algorithm=None):
        config = copy.deepcopy(self.config)
        config["algorithm"].update(algorithm or {})
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        seats_data = case["newSeatmapData"]["seats"]
        topology = rich.build_seat_topology(seats_data, config)
        globals_ = dict(zip(("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW", "_SEAT_X", "_SEAT_ROW_INDEX"), topology))
        globals_["_ACTIVE_CONFIG"] = config
        with patch.multiple(rich, **globals_):
            groups = [rich.Group(g) for g in case["groupsData"]]
            passengers = [(g.group_id, p) for g in groups for p in g.passengers]
            keys = [(gid, p.hostnum) for gid, p in passengers]
            if initial is None:
                context = construction_prefix()(seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], config["weights"], config)["context"]
                initial = [[keys.index(key), seat, *sorted(context.assigned_blocked.get(key, ()))[:1]]
                           for key, seat in context.assigned_seats.items()]
            context = rich.AssignmentContext({s["seatId"]: rich.Seat(s) for s in seats_data})
            for p, seat, *block in initial:
                gid, passenger = passengers[p]
                self.assertTrue(context.assign_passenger(passenger, seat, gid, chosen_block=block[0] if block else None))
            objects = dict(zip(keys, (p for _, p in passengers)))
            raw = {(g["groupId"], p["hostnum"]): p for g in case["groupsData"] for p in g["psrs"]}
            new = evaluator.SeatTopology(seats_data, config)
            old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
            tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
            function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "improve_with_multigroup_lns")
            start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.AnnAssign)
                         and isinstance(n.target, ast.Name) and n.target.id == "keys_by_group")
            stop = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "eligible_groups") + 1
            definitions = [n for n in function.body if isinstance(n, ast.FunctionDef)
                           and n.name in ("passenger_score", "compact_score", "best_matching", "best_group_assignment")]
            duration = -1 if expired else 120
            namespace = dict(rich.__dict__, context=context, config=config, passenger_objects=objects, passenger_raw=raw,
                evaluator_module=evaluator, new_topology=new, old_topology=old, weights=config["weights"],
                deadline=rich.time.perf_counter() + duration, diagnostics={"stopped_by_deadline": False},
                individual_cache={}, baby_cache={}, compact_cache={}, w_c=config["weights"].get("w_c", -2.0),
                bsct_items=[(key[0], seat) for key, seat in context.assigned_seats.items() if objects[key].ssr == "BSCT"])
            exec(compile(ast.Module(body=function.body[start:stop] + definitions, type_ignores=[]), "frozen_lns_matching", "exec"), namespace)
            expected_keys = [[keys.index(key) for key in namespace["keys_by_group"].get(g.group_id, ())] for g in groups]
            if calls is None:
                rng = random.Random(20260923)
                seat_ids = list(context.seats)
                calls = []
                for g, group_keys in enumerate(expected_keys):
                    if not group_keys: continue
                    for variant in range(8):
                        seats = [context.assigned_seats[keys[p]] for p in group_keys] if variant == 0 else rng.sample(seat_ids, len(group_keys))
                        if variant % 2: seats.reverse()
                        released = list(context.occupied) if variant % 3 else seats
                        calls.append(dict(group_index=g, seats=seats, released=released,
                            scores=[[p, seat] for p in group_keys for seat in seats]))
            expected = []
            for call in calls:
                for p, _ in call.get("moves", []): context.remove_assignment(*keys[p])
                for p, seat in call.get("moves", []):
                    self.assertTrue(context.assign_passenger(passengers[p][1], seat, passengers[p][0]))
                gid = groups[call["group_index"]].group_id
                score, assignment = namespace["best_group_assignment"](gid, tuple(call["seats"]), frozenset(call["released"]))
                expected.append(dict(score=score if math.isfinite(score) else None, assignment=list(assignment),
                    compact=namespace["compact_score"](tuple(call["seats"])), stopped=namespace["diagnostics"]["stopped_by_deadline"],
                    scores=[namespace["passenger_score"](keys[p], seat) for p, seat in call["scores"]]))
            eligible = namespace["eligible_groups"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_context": dict(initial=initial, lns_matching=calls, deadline_seconds=duration)},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"), str(work / "replay.json")],
                                 capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(set(actual["eligible"]), eligible)
        self.assertEqual(actual["keys"], expected_keys)
        self.assertEqual(len(actual["calls"]), len(expected))
        for request, a, b in zip(calls, actual["calls"], expected):
            with self.subTest(case=case["id"], expired=expired, request=request):
                self.assertEqual(a["assignment"], b["assignment"])
                self.assertEqual(a["stopped"], b["stopped"])
                if b["score"] is None: self.assertIsNone(a["score"])
                else: self.assertAlmostEqual(a["score"], b["score"], places=8)
                self.assertAlmostEqual(a["compact"], b["compact"], places=8)
                self.assertEqual(len(a["scores"]), len(b["scores"]))
                for x, y in zip(a["scores"], b["scores"]): self.assertAlmostEqual(x, y, places=8)
        return actual

    def test_lns_matching_and_scoring_match_frozen_functions(self):
        for case in self.cases[:11]:
            self.replay_matching(case)
        self.replay_matching(self.cases[7], expired=True)

    def test_lns_eligibility_uses_seat_number_none_and_snapshot_key_order(self):
        case = self.synthetic([(90, {"newSeat": {"seatValue": 5}}), (80, {"newSeat": {"seatNum": None}}),
            (70, {"newSeat": {"seatNum": ""}}), (60, {"newSeat": {"seatNum": "2A"}}), (50, {}), (50, {}), (50, {})])
        actual = self.replay_matching(case, initial=[[6, "3C"], [5, "3B"], [4, "3A"], [3, "2A"], [2, "1C"], [1, "1B"], [0, "1A"]],
                                     calls=[], algorithm={"multigroup_pattern_group_size_limit": 0})
        self.assertEqual(set(actual["eligible"]), {90, 80})
        self.assertEqual(actual["keys"][-1], [6, 5, 4])

    def test_lns_matching_keeps_input_order_on_equal_scores(self):
        for special in (False, True):
            with self.subTest(special=special):
                case = self.synthetic([(20, {"ssr": "TEST" if special else ""}), (20, {}), (20, {})])
                config = copy.deepcopy(self.config)
                config["weights"] = {key: 0.0 for key in config["weights"]}
                saved = self.config
                self.config = config
                try:
                    actual = self.replay_matching(case, initial=[[2, "1C"], [0, "1A"], [1, "1B"]], calls=[
                        dict(group_index=0, seats=["2C", "2A", "2B"], released=["1A", "1B", "1C"], scores=[])])
                    self.assertEqual(actual["calls"][0]["assignment"], ["2C", "2A", "2B"])
                finally:
                    self.config = saved

    def test_lns_baby_cache_keeps_initial_infants_after_state_changes(self):
        case = self.synthetic([(20, {"ssr": "BSCT"}), (20, {}), (10, {"ssr": "BSCT"}), (10, {})])
        config = copy.deepcopy(self.config)
        # Public topology has bassinet seats in row one; allow this synthetic move to isolate cache lifetime.
        saved = self.config
        self.config = config
        config["ssr_rules"]["BSCT"]["requires_bassinet"] = False
        try:
            scores = [[p, seat] for p in range(4) for seat in ("1A", "1B", "2A", "3A")]
            actual = self.replay_matching(case, initial=[[2, "1D"], [3, "1E"], [0, "1A"], [1, "1B"]], calls=[
                dict(group_index=0, seats=["1A", "1B"], released=["1A", "1B"], scores=scores),
                dict(group_index=0, seats=["1A", "1B"], released=["1A", "1B", "3A", "3B"], scores=scores + [[0, "4A"], [2, "4B"]],
                     moves=[[2, "3A"], [3, "3B"]])])
            self.assertEqual(actual["calls"][0]["scores"], actual["calls"][1]["scores"][:len(scores)])
        finally:
            self.config = saved


if __name__ == "__main__":
    unittest.main()
