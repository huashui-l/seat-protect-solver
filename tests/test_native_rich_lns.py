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
from tests.test_native_rich_elite import python_capture_namespace

ROOT = Path(__file__).resolve().parents[1]


class NativeRichLnsTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def replay_search(self, case, algorithm=None, initial=None, expired=False, stage=False):
        config = copy.deepcopy(self.config)
        config["algorithm"].update(enable_conflict_component_lns=True, multigroup_mip_solve_limit=8,
            multigroup_option_limit=10, multigroup_free_seat_cap=2)
        config["algorithm"].update(algorithm or {})
        config["input_contract"] = {"seatmaps_by_direction": {"public-test": {"old": "old.json", "new": "new.json"}}}
        seats_data = case["newSeatmapData"]["seats"]
        topology = rich.build_seat_topology(seats_data, config)
        globals_ = dict(zip(("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW", "_SEAT_X", "_SEAT_ROW_INDEX"), topology))
        globals_["_ACTIVE_CONFIG"] = config
        with patch.multiple(rich, **globals_):
            groups = [rich.Group(g) for g in case["groupsData"]]
            passengers = [(g.group_id, p) for g in groups for p in g.passengers]
            keys = [(gid, p.hostnum) for gid, p in passengers]
            if initial is None:
                constructed = construction_prefix()(seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], config["weights"], config)
                context = constructed["context"]
                initial = [[keys.index(key), seat, *sorted(context.assigned_blocked.get(key, ()))[:1]]
                           for key, seat in context.assigned_seats.items()]
                rankings = [[seat.seat_id for seat in constructed["passenger_sorted_seats"][key]] for key in keys]
            else:
                rankings = [[seat["seatId"] for seat in seats_data] for _ in keys]
            context = rich.AssignmentContext({s["seatId"]: rich.Seat(s) for s in seats_data})
            for p, seat, *block in initial:
                gid, passenger = passengers[p]
                self.assertTrue(context.assign_passenger(passenger, seat, gid, chosen_block=block[0] if block else None))
            function = next(n for n in ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8")).body
                            if isinstance(n, ast.FunctionDef) and n.name == "improve_with_multigroup_lns")
            option_function = next(n for n in function.body if isinstance(n, ast.FunctionDef) and n.name == "group_options")
            loop = next(n for n in option_function.body if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "candidate_subsets")
            loop.iter = ast.Call(func=ast.Name(id="sorted", ctx=ast.Load()), args=[loop.iter], keywords=[])
            ast.fix_missing_locations(function)
            namespace = dict(rich.__dict__)
            exec(compile(ast.Module(body=[function], type_ignores=[]), "ordered_lns_search", "exec"), namespace)
            recorded = []
            scorer = evaluator.IncrementalSoftScorer(seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], config["weights"], config)
            elite = python_capture_namespace(context, groups, scorer, config["algorithm"].get("elite_patterns_per_group", 12))
            elite["capture_stage_patterns"]("lns_initial")
            initial_score = evaluator.calculate_soft_score(seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], context.assigned_seats, config["weights"], config)[0]
            duration = -1 if expired else 120
            diagnostics = namespace["improve_with_multigroup_lns"](seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], groups,
                context, {key: [context.seats[s] for s in rankings[p]] for p, key in enumerate(keys)}, config["weights"], config,
                rich.time.perf_counter() + duration,
                elite["record_elite_pattern"] if stage else
                lambda gid, assignment, score, source: recorded.append([gid, score, [[keys.index(key), s] for key, s in assignment]]))
            expected = dict(diagnostics=diagnostics, assignments=[context.assigned_seats.get(key) for key in keys],
                            order=[keys.index(key) for key in context.assigned_seats], recorded=recorded)
            if stage:
                elite["capture_stage_patterns"]("lns_final")
                score = evaluator.calculate_soft_score(seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], context.assigned_seats, config["weights"], config)[0]
                expected.update(elite={str(g): list(patterns.values()) for g, patterns in elite["elite_pattern_store"].items()}, selected_score=max(initial_score, score))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_context": dict(initial=initial, lns_search=dict(rankings=rankings, deadline_seconds=duration, stage=stage))},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"), str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        actual["diagnostics"].pop("seconds")
        self.assertEqual(actual["diagnostics"].pop("solver_errors"), 0)
        expected["diagnostics"].pop("seconds")
        expected = json.loads(json.dumps(expected))
        def compare(a, b, path):
            with self.subTest(case=case["id"], algorithm=algorithm, expired=expired, path=path):
                if isinstance(b, dict):
                    self.assertEqual(set(a), set(b))
                    for key in b: compare(a[key], b[key], path + "/" + key)
                elif isinstance(b, list):
                    self.assertEqual(len(a), len(b))
                    for i, (x, y) in enumerate(zip(a, b)): compare(x, y, path + "/" + str(i))
                elif isinstance(b, float): self.assertAlmostEqual(a, b, places=7)
                else: self.assertEqual(a, b)
        compare(actual, expected, "")
        return actual

    def test_lns_full_search_matches_order_normalized_frozen_function(self):
        totals = dict(search_nodes=0, accepted_rebuilds=0, dynamic_reorders=0, ejection_chains_generated=0)
        for case in self.cases[:11]:
            result = self.replay_search(case)
            for key in totals: totals[key] += result["diagnostics"][key]
        for key, value in totals.items(): self.assertGreater(value, 0, key)

    def test_lns_accepts_worsening_then_restores_best_context(self):
        case = self.synthetic([(30, {"oldSeat": {"seatNum": "1A"}}), (10, {"oldSeat": {"seatNum": "1B"}})])
        initial = [[1, "1B"], [0, "1A"]]
        result = self.replay_search(case, initial=initial, algorithm={
            "multigroup_mip_solve_limit": 1, "multigroup_allowed_drop": 1000,
            "multigroup_free_seat_cap": 0})
        self.assertEqual(result["diagnostics"]["accepted_worsening"], 1)
        self.assertEqual(result["diagnostics"]["score_improvement"], 0)
        self.assertEqual(result["assignments"], ["1A", "1B"])
        self.assertEqual(result["order"], [1, 0])

    def test_lns_search_disabled_expired_and_strict_acceptance(self):
        for algorithm, expired in [({"enable_conflict_component_lns": False}, False), ({}, True),
                                   ({"multigroup_allowed_drop": 0, "multigroup_mip_solve_limit": 12}, False)]:
            self.replay_search(self.cases[0], algorithm=algorithm, expired=expired)
        self.replay_search(self.synthetic([(30, {}), (30, {})]), initial=[[0, "1A"], [1, "1B"]])

    def test_lns_production_wrapper_preserves_elite_and_rich_state(self):
        for case in self.cases[:11]: self.replay_search(case, stage=True)
        self.replay_search(self.cases[0], stage=True, expired=True)
        self.replay_search(self.cases[0], stage=True, algorithm={"enable_conflict_component_lns": False})

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

    def test_lns_solver_error_discards_incomplete_choice(self):
        # Independently generated set-partition model: Random(20260923), trial 204,
        # then column deletion. HiGHS 1.15.1 presolve returns kError with an
        # incomplete primal vector; this contains no private benchmark data.
        import highspy
        case = self.synthetic([(10, {})] * 3 + [(20, {})] * 4 + [(30, {})] * 2)
        seats = ["1A", "1B", "1C", "1D", "1E", "1F", "2A", "2B", "2C"]
        columns = [[0, 8.3, [4, 5, 8]], [0, 6.2, [1, 3, 8]], [0, 5.0, [3, 5, 7]], [0, 3.4, [3, 6, 8]], [0, 9.9, [1, 3, 5]], [0, 6.4, [1, 2, 8]], [0, 0.2, [0, 1, 4]], [0, 0.1, [3, 4, 7]], [0, 10.0, [3, 7, 8]], [1, 3.9, [2, 3, 7, 8]], [1, 6.1, [1, 2, 3, 5]], [1, 7.1, [1, 5, 7, 8]], [1, 7.9, [1, 3, 4, 6]], [1, 9.4, [0, 1, 3, 7]], [1, 1.8, [1, 6, 7, 8]], [1, 0.2, [3, 4, 5, 8]], [1, 3.6, [0, 1, 2, 8]], [1, 2.8, [0, 1, 2, 3]], [2, 2.4, [2, 7]], [2, 6.6, [1, 5]], [2, 4.6, [4, 5]], [2, 2.1, [5, 7]], [2, 7.7, [4, 8]], [2, 0.2, [6, 8]], [2, 9.3, [0, 8]], [2, 10.0, [3, 6]], [2, 5.7, [3, 8]]]
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("threads", 1)
        for _ in range(3): h.addRow(1, 1, 0, [], [])
        for _ in seats: h.addRow(-highspy.kHighsInf, 1, 0, [], [])
        options = {g: [] for g in range(3)}
        for g, cost, indexes in columns:
            rows = [g] + [i + 3 for i in indexes]
            h.addCol(cost, 0, 1, len(rows), rows, [1] * len(rows))
            h.changeColIntegrality(h.getNumCol() - 1, highspy.HighsVarType.kInteger)
            chosen = [seats[i] for i in indexes]
            options[g].append([-cost, chosen, chosen])
        self.assertEqual(h.run(), highspy.HighsStatus.kError)
        self.assertLess(sum(v > .5 for v in h.getSolution().col_value), 3)
        config = copy.deepcopy(self.config)
        config["input_contract"] = {"seatmaps_by_direction": {"public-test": {"old": "old.json", "new": "new.json"}}}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"pattern_context": {"initial": [[i, seat] for i, seat in enumerate(seats)],
                    "lns_master": [{"component": [0, 1, 2], "root": 0, "options": options}]}},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"), str(work / "replay.json")],
                                 capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [{}])

    def replay_matching(self, case, initial=None, calls=None, expired=False, algorithm=None, options_mode=False):
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
            option_function = copy.deepcopy(next(n for n in function.body if isinstance(n, ast.FunctionDef) and n.name == "group_options"))
            exec(compile(ast.Module(body=[option_function], type_ignores=[]), "frozen_lns_options", "exec"), namespace)
            original_options = namespace["group_options"]
            # Normalize only the otherwise process-hash-dependent traversal.
            loop = next(n for n in option_function.body if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "candidate_subsets")
            loop.iter = ast.Call(func=ast.Name(id="sorted", ctx=ast.Load()), args=[loop.iter], keywords=[])
            index = option_function.body.index(loop)
            option_function.body[index:index] = ast.parse("observed_subsets.append(sorted(candidate_subsets))").body
            ast.fix_missing_locations(option_function)
            exec(compile(ast.Module(body=[option_function], type_ignores=[]), "ordered_lns_options", "exec"), namespace)
            recorded, observed_subsets = [], []
            namespace.update(algorithm=config["algorithm"], option_limit=max(10, int(config["algorithm"].get("multigroup_option_limit", 60))),
                incremental_scorer=evaluator.IncrementalSoftScorer(seats_data, case["oldSeatmapData"]["seats"], case["groupsData"], config["weights"], config),
                elite_pattern_recorder=lambda gid, assignment, score, source: recorded.append((gid, assignment, score, source)),
                observed_subsets=observed_subsets)
            namespace["diagnostics"]["options_generated"] = 0
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
                        if options_mode:
                            calls[-1]["option_pool"] = seat_ids if variant % 2 else list(context.occupied)
            expected = []
            generated = 0
            for call in calls:
                for p, _ in call.get("moves", []): context.remove_assignment(*keys[p])
                for p, seat in call.get("moves", []):
                    self.assertTrue(context.assign_passenger(passengers[p][1], seat, passengers[p][0]))
                gid = groups[call["group_index"]].group_id
                score, assignment = namespace["best_group_assignment"](gid, tuple(call["seats"]), frozenset(call["released"]))
                expected.append(dict(score=score if math.isfinite(score) else None, assignment=list(assignment),
                    compact=namespace["compact_score"](tuple(call["seats"])), stopped=namespace["diagnostics"]["stopped_by_deadline"],
                    scores=[namespace["passenger_score"](keys[p], seat) for p, seat in call["scores"]]))
                if "option_pool" in call:
                    original = original_options(gid, tuple(call["option_pool"]))
                    recorded.clear()
                    options = namespace["group_options"](gid, tuple(call["option_pool"]))
                    # Unmodified Python must retain the same top score multiset,
                    # even when identities at an equal-score cutoff differ.
                    self.assertEqual(len(options), len(original))
                    for a, b in zip(options, original): self.assertAlmostEqual(a[0], b[0], places=8)
                    generated += len(options)
                    expected[-1].update(options=options, subsets=observed_subsets[-1], options_generated=generated,
                        recorded=[(score, frozenset(seat for _, seat in assignment), tuple(seat for _, seat in assignment))
                                  for _, assignment, score, source in recorded])
                    self.assertTrue(all(source == "lns_generated" and group == gid for group, _, _, source in recorded))
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
                if "options" in b:
                    self.assertEqual(a["subsets"], [list(seats) for seats in b["subsets"]])
                    self.assertEqual(a["options_generated"], b["options_generated"])
                    for field in ("options", "recorded"):
                        self.assertEqual(len(a[field]), len(b[field]))
                        for x, y in zip(a[field], b[field]):
                            self.assertAlmostEqual(x[0], y[0], places=8)
                            self.assertEqual(set(x[1]), set(y[1]))
                            self.assertEqual(x[2], list(y[2]))
        return actual

    def test_lns_options_geometry_scoring_and_elite_recording(self):
        generated = 0
        for case in self.cases[:11]:
            result = self.replay_matching(case, options_mode=True, algorithm={"multigroup_option_limit": 10})
            generated += sum(len(call["options"]) for call in result["calls"])
        self.assertGreater(generated, 100)
        self.replay_matching(self.cases[7], options_mode=True, expired=True)

    def test_lns_options_config_floors_and_tied_cutoff(self):
        case = self.synthetic([(20, {}), (20, {}), (10, {}), (10, {})])
        saved = self.config
        self.config = copy.deepcopy(saved)
        self.config["weights"] = {key: 0.0 for key in saved["weights"]}
        try:
            result = self.replay_matching(case, initial=[[0, "1A"], [1, "1B"], [2, "1D"], [3, "1E"]],
                options_mode=True, algorithm={"multigroup_option_limit": 0, "multigroup_neighborhood_extra_seats": 0,
                                              "elite_patterns_from_pricing_per_call": 0})
            self.assertTrue(any(len(call["options"]) == 10 for call in result["calls"]))
            self.assertTrue(all(len(call["recorded"]) == 1 for call in result["calls"]))
        finally:
            self.config = saved

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
