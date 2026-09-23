import ast
import copy
import json
import math
import random
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from src import heuristic_seat_allocator as rich
from src import allocation_evaluator as evaluator
from src import exact_column_generation as exact
from tests import test_native_rich_repair as repair_tests
from tests.test_native_rich_pipeline import construction_prefix

ROOT = Path(__file__).resolve().parents[1]


def python_rigid_relaxed(case, config, assignments, value_blocks=False, expired=False):
    tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "generate_structured_group_patterns")
    loop = next(n for n in function.body if isinstance(n, ast.For)
                and isinstance(n.target, ast.Name) and n.target.id == "group")
    start = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "seat_at")
    stop = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "group_deadline")
    code = compile(ast.Module(body=loop.body[start:stop], type_ignores=[]), "frozen_rigid_relaxed", "exec")
    block_stop = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.AnnAssign)
                      and isinstance(n.target, ast.Name) and n.target.id == "pattern_by_signature")
    block_code = compile(ast.Module(body=loop.body[stop + 1:block_stop], type_ignores=[]), "frozen_value_blocks", "exec")
    new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
    old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
    fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
    baby = exact._baby_pairs(new, case["groupsData"], config["weights"], config)
    scorer = evaluator.IncrementalSoftScorer(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                           case["groupsData"], config["weights"], config)
    metrics = rich._group_repair_metrics(case["groupsData"], assignments, new, config["weights"], config, old)
    activation = next(n for n in function.body if isinstance(n, ast.Assign)
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "full_resource_global_blocks")
    result = []
    for group in case["groupsData"]:
        namespace = dict(rich.__dict__, group=group, passengers=group["psrs"], group_id=group["groupId"],
                         new_topology=new, pricing_config=config, algorithm=config["algorithm"],
                         weights=config["weights"], exact=exact, baby_cost=baby,
                         context=types.SimpleNamespace(assigned_seats=assignments), diagnostics={"three_tier_active": False},
                         cache=exact._build_group_pricing_cache(group, new, old, config["weights"], config, fixed))
        exec(code, namespace)
        if value_blocks:
            namespace.update(new_seats_data=case["newSeatmapData"]["seats"], old_seats_data=case["oldSeatmapData"]["seats"],
                             resource_demand=rich._seat_demand_for_budgeting(case["groupsData"]),
                             traveler_demand=sum(len(g["psrs"]) for g in case["groupsData"]),
                             current_metric=metrics[group["groupId"]], target_span=max(0, metrics[group["groupId"]]["row_span"] - 2),
                             old_topology=old, config=config, evaluator_module=evaluator, scorer=scorer,
                             group_deadline=rich.time.perf_counter() + (-1.0 if expired else 60.0))
            exec(compile(ast.Module(body=[activation], type_ignores=[]), "frozen_global_activation", "exec"), namespace)
            exec(block_code, namespace)
        for pattern, source in namespace["tiered_patterns"].values():
            result.append(dict(group_id=pattern.group_id, signature=pattern.signature, source=source,
                               assignments=pattern.assignments, blocked_by=pattern.blocked_by,
                               seat_resources=sorted(pattern.seat_resources), infant_seats=sorted(pattern.infant_seats),
                               occupied_seats=sorted(pattern.occupied_seats), ssr_all=pattern.ssr_all,
                               ssr_flagged=pattern.ssr_flagged, master_cost=pattern.master_cost,
                               caregiver_ok=exact._caregiver_ok(group, pattern.placements, new, config)))
    return json.loads(json.dumps(result))


class NativeRichStructuredTests(unittest.TestCase):
    setUpClass = classmethod(repair_tests.NativeRichRepairTests.setUpClass.__func__)
    synthetic = repair_tests.NativeRichRepairTests.synthetic

    def test_pricing_resource_flag_and_caregiver_workspace_match_python(self):
        tree = ast.parse((ROOT / "src/exact_column_generation.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_price_group_exact_dfs")
        def index(name):
            return next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
        code = compile(ast.Module(body=function.body[index("workspace_started"):index("dynamic_refresh_started")],
                                  type_ignores=[]), "frozen_dfs_workspace", "exec")
        care_code = compile(ast.Module(body=[n for n in function.body if isinstance(n, ast.FunctionDef)
                                            and n.name in ("caregiver_still_possible", "caregiver_dominance_state")],
                                       type_ignores=[]), "frozen_dfs_care", "exec")
        rng = random.Random(831)
        large = self.synthetic([(101, {"ssr": "BLND", "mandatoryRule": {"needSingleSideEmpty": "Y",
                                 "sameRowNoOtherSSR": "Y", "sameSubRowNoOtherSSR": "Y"}}),
                                (101, {}), (101, {"needCared": "Y"}),
                                (101, {"mandatoryRule": {"needBothSideEmpty": "Y"}})])
        large["id"] = "workspace_multiple_words"
        prototypes = [s for s in large["newSeatmapData"]["seats"] if s["row"] == 1]
        large["newSeatmapData"]["seats"] = [{**s, "seatId": f"{row}{s['col']}", "row": row}
                                            for row in range(1, 37) for s in prototypes]
        coverage = set()
        for original in [*self.cases[:11], large]:
            for reverse in (False, True):
                with self.subTest(case=original["id"], reverse=reverse):
                    case = copy.deepcopy(original)
                    if reverse: case["newSeatmapData"]["seats"].reverse()
                    config = copy.deepcopy(self.config)
                    config["input_contract"] = {"seatmaps_by_direction": {
                        "public-test": {"old": "old.json", "new": "new.json"}}}
                    new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
                    old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
                    fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
                    requests, expected = [], []
                    for g, group in enumerate(case["groupsData"]):
                        base = exact._build_group_pricing_cache(group, new, old, config["weights"], config, fixed)
                        for window in (None, [max(new.row_seats)], []):
                            cache = copy.copy(base)
                            if window is not None:
                                cache.all_options = [[o for o in options if new.seat_map[o.seat_id]["row"] in window]
                                                     for options in base.all_options]
                                cache.universe = [o for options in cache.all_options for o in options]
                                cache.seat_ids = tuple(sorted({o.seat_id for o in cache.universe}))
                                cache.row_coordinate = {s: base.row_coordinate[s] for s in cache.seat_ids}
                                cache.x_coordinate = {s: base.x_coordinate[s] for s in cache.seat_ids}
                            namespace = dict(exact.__dict__, cache=cache, passengers=group["psrs"],
                                             new_topology=new, config=config, pricing_cfg={})
                            exec(code, namespace)
                            workspace = namespace["workspace"]
                            seat_words = (len(new.seat_map) + 63) // 64
                            flag_words = (len(workspace["flag_locations"]) + 63) // 64
                            def words(mask, count):
                                return [str((mask >> (64 * i)) & ((1 << 64) - 1)) for i in range(count)]
                            resources = [[words(workspace["resource_mask"][o.signature], seat_words) for o in options]
                                         for options in cache.all_options]
                            flags = [[words(workspace["option_flag_mask"][o.signature], flag_words) for o in options]
                                     for options in cache.all_options]
                            indexes = [[list(bit.bit_length() - 1 for bit in workspace["option_flag_bits"][o.signature])
                                        for o in options] for options in cache.all_options]
                            lookup = {o.signature: [p, i] for p, options in enumerate(cache.all_options) for i, o in enumerate(options)}
                            expected.append(dict(flag_locations=workspace["flag_locations"], resource_masks=resources,
                                                 flag_masks=flags, option_flag_indexes=indexes,
                                                 caregiver_specs=workspace["caregiver_specs"],
                                                 option_lookup=[lookup[workspace["option_by_signature"][o.signature].signature]
                                                                for o in cache.universe]))
                            requests.append(dict(group_index=g, workspace=True))
                            if window is not None: requests[-1]["window"] = window
                            selections = [([-1] * len(cache.all_options), [])]
                            for _ in range(8):
                                selections.append(([rng.randrange(-1, len(options)) for options in cache.all_options],
                                                   rng.sample(list(new.seat_map), min(4, len(new.seat_map)))))
                            for cared, cross, adults in workspace["caregiver_specs"]:
                                if not cache.all_options[cared]: continue
                                chosen = [-1] * len(cache.all_options)
                                chosen[cared] = 0
                                selections.extend([(chosen[:], []), (chosen[:], list(new.seat_map))])
                                neighbors = set(new.row_neighbors(cache.all_options[cared][0].seat_id, allow_cross_aisle=cross))
                                for adult in adults:
                                    adjacent = next((i for i, o in enumerate(cache.all_options[adult]) if o.seat_id in neighbors), None)
                                    if adjacent is not None:
                                        chosen[adult] = adjacent
                                        selections.append((chosen[:], []))
                                        break
                            namespace.update(caregiver_specs=workspace["caregiver_specs"],
                                             domains=dict(enumerate(cache.all_options)))
                            exec(care_code, namespace)
                            requests[-1]["care_queries"], expected[-1]["care_queries"] = [], []
                            for selected, extra in selections:
                                namespace["selected"] = [options[i] if i >= 0 else None for options, i in zip(cache.all_options, selected)]
                                used_seats = set(extra)
                                for o in namespace["selected"]:
                                    if o is not None: used_seats.update(o.resources)
                                used_mask = sum(workspace["seat_bit"][s] for s in used_seats)
                                possible = namespace["caregiver_still_possible"](used_mask)
                                states = namespace["caregiver_dominance_state"]()
                                requests[-1]["care_queries"].append(dict(selected=selected, used_seats=sorted(used_seats)))
                                expected[-1]["care_queries"].append(dict(possible=possible,
                                    state=[None if mask == -1 else words(mask, seat_words) for mask in states]))
                                if not possible: coverage.add("impossible_care")
                                if any(mask == 0 for mask in states): coverage.add("satisfied_care")
                                if any(mask > 0 for mask in states): coverage.add("pending_care")
                                if any(mask >> 64 > 0 for mask in states): coverage.add("care_words")
                            if any(int(w) for row in resources for mask in row for w in mask[1:]): coverage.add("seat_words")
                            if any(int(w) for row in flags for mask in row for w in mask[1:]): coverage.add("flag_words")
                            for _, cross, adults in workspace["caregiver_specs"]:
                                if adults: coverage.add("cross" if cross else "same_side")
                    with tempfile.TemporaryDirectory() as directory:
                        work = Path(directory)
                        for name, value in {
                            "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                            "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                            "replay.json": {"pricing_cache": requests},
                        }.items():
                            (work / name).write_text(json.dumps(value), encoding="utf-8")
                        run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                              str(work / "replay.json")], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    actual = json.loads(run.stdout)
                    expected = json.loads(json.dumps(expected))
                    self.assertEqual(len(actual), len(expected))
                    for request, native, python in zip(requests, actual, expected):
                        with self.subTest(request=request): self.assertEqual(native, python)
        self.assertEqual(coverage, {"seat_words", "flag_words", "cross", "same_side",
                                   "impossible_care", "satisfied_care", "pending_care", "care_words"})

    def test_pricing_rectangle_bounds_match_frozen_dfs(self):
        tree = ast.parse((ROOT / "src/exact_column_generation.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_price_group_exact_dfs")
        def index(name):
            return next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
        workspace_code = compile(ast.Module(body=function.body[index("workspace_started"):index("dynamic_refresh_started")],
                                           type_ignores=[]), "frozen_dfs_workspace", "exec")
        bounds_code = compile(ast.Module(body=function.body[index("compactness"):index("baby_relaxation")],
                                        type_ignores=[]), "frozen_dfs_bounds", "exec")
        def values(array):
            return [float(v) if math.isfinite(v) else None for v in array.ravel()]
        reserved = self.synthetic([(101, {}), (101, {}), (202, {"newSeat": {"seatNum": "1B"}})])
        reserved["id"] = "bounds_reserved_middle"
        crowded = self.synthetic([(101, {})] * 7)
        crowded["id"] = "bounds_insufficient_capacity"
        crowded["newSeatmapData"]["seats"] = [s for s in crowded["newSeatmapData"]["seats"] if s["row"] == 1]
        for case in [*self.cases[:11], reserved, crowded]:
            with self.subTest(case=case["id"]):
                config = copy.deepcopy(self.config)
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}}}
                new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
                old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
                fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
                requests, expected = [], []
                for g, group in enumerate(case["groupsData"]):
                    base_cache = exact._build_group_pricing_cache(group, new, old, config["weights"], config, fixed)
                    for variant in range(4):
                        cache = base_cache
                        window = [max(new.row_seats)]
                        if variant == 3:
                            cache = copy.copy(base_cache)
                            cache.all_options = [[o for o in options if new.seat_map[o.seat_id]["row"] in window]
                                                 for options in base_cache.all_options]
                            if any(not options for options in cache.all_options): continue
                            cache.universe = [o for options in cache.all_options for o in options]
                            cache.seat_ids = tuple(sorted({o.seat_id for o in cache.universe}))
                            cache.row_coordinate = {s: base_cache.row_coordinate[s] for s in cache.seat_ids}
                            cache.x_coordinate = {s: base_cache.x_coordinate[s] for s in cache.seat_ids}
                            cache.dfs_workspace = None
                        # Nonzero dual-like offsets exercise negative costs independently of geometric costs.
                        costs = [[o.individual_cost + ((i % 5) - 2) * 3.125 if variant == 2 else o.individual_cost
                                  for i, o in enumerate(options)] for options in cache.all_options]
                        order = list(range(len(group["psrs"])))
                        if variant == 2: order.reverse()
                        namespace = dict(exact.__dict__, cache=cache, passengers=group["psrs"], new_topology=new,
                                         config=config, pricing_cfg={}, weights=config["weights"], phase_one=variant == 1,
                                         gid=group["groupId"], duals=exact.MasterDuals(), order=order,
                                         domains=dict(enumerate(cache.all_options)),
                                         base_cost={o.signature: cost for options, row in zip(cache.all_options, costs)
                                                    for o, cost in zip(options, row)})
                        exec(workspace_code, namespace)
                        exec(bounds_code, namespace)
                        workspace = namespace["workspace"]
                        compactness = namespace["compactness"]
                        requests.append(dict(group_index=g, bounds=True, order=order, base_cost=costs, variant=variant,
                                             row_span_cost=compactness.row_span, column_span_cost=compactness.column_span))
                        if variant == 3: requests[-1]["window"] = window
                        root = namespace["root_rectangle_bound"]
                        expected.append(dict(row_values=workspace["row_values"], x_values=workspace["x_values"],
                                             seat_prefix=values(workspace["seat_prefix"]),
                                             span=values(namespace["rectangle_span_bound"]),
                                             suffix=[values(a) for a in namespace["suffix_rectangle_bound"]],
                                             root_span=root if math.isfinite(root) else None))
                with tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    for name, value in {
                        "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                        "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                        "replay.json": {"pricing_cache": requests},
                    }.items():
                        (work / name).write_text(json.dumps(value), encoding="utf-8")
                    run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                          str(work / "replay.json")], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)
                self.assertEqual(len(actual), len(expected))
                for i, (native, python) in enumerate(zip(actual, expected)):
                    with self.subTest(group=requests[i]["group_index"], variant=requests[i]["variant"]):
                        self.assertEqual(native, python)

    def test_pricing_cache_and_window_geometry_match_python(self):
        tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_structured_group_patterns")
        group_loop = next(n for n in function.body if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "group")
        window_loop = next(n for n in group_loop.body if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "window")
        start = next(i for i, n in enumerate(window_loop.body) if isinstance(n, ast.Assign)
                     and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "window_cache")
        stop = next(i for i, n in enumerate(window_loop.body) if isinstance(n, ast.AugAssign))
        filtering = compile(ast.Module(body=window_loop.body[start:stop], type_ignores=[]), "frozen_window_cache", "exec")
        reserved = self.synthetic([(101, {}), (202, {"newSeat": {"seatNum": "1B"}})])
        reserved["id"] = "pricing_reserved_middle"
        coverage = set()
        for original in [*self.cases[:11], reserved]:
            for reverse in (False, True):
                with self.subTest(case=original["id"], reverse=reverse):
                    case = copy.deepcopy(original)
                    if reverse: case["newSeatmapData"]["seats"].reverse()
                    config = copy.deepcopy(self.config)
                    config["input_contract"] = {"seatmaps_by_direction": {
                        "public-test": {"old": "old.json", "new": "new.json"}}}
                    new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
                    old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
                    fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
                    requests, expected = [], []
                    for g, group in enumerate(case["groupsData"]):
                        cache = exact._build_group_pricing_cache(group, new, old, config["weights"], config, fixed)
                        reachable = set(cache.seat_ids)
                        if any(a not in reachable or b not in reachable for a, b in cache.adjacency_edges):
                            coverage.add("unreachable_neighbor")
                        if any(m not in reachable for m, _, _ in cache.hole_specs):
                            coverage.add("unreachable_middle")
                        rows = sorted(new.row_seats)
                        for window in (None, rows[:1], rows[-1:], rows[::2], []):
                            request = dict(group_index=g)
                            selected = cache
                            if window is not None:
                                request["window"] = window
                                filtered = [[o for o in options if int(new.seat_map[o.seat_id]["row"]) in window]
                                            for options in cache.all_options]
                                namespace = dict(rich.__dict__, cache=cache, filtered_options=filtered)
                                exec(filtering, namespace)
                                selected = namespace["window_cache"]
                                if not any(selected.all_options): coverage.add("empty_window")
                                if selected.row_coordinate and min(selected.row_coordinate.values()) > 0:
                                    coverage.add("retained_origin")
                            requests.append(request)
                            expected.append({**{k: getattr(selected, k) for k in ("seat_ids", "row_coordinate", "x_coordinate",
                                               "row_big_m", "x_big_m", "adjacency_edges", "hole_specs")},
                                             "all_options": [[o.signature for o in options] for options in selected.all_options]})
                    expected = json.loads(json.dumps(expected))
                    with tempfile.TemporaryDirectory() as directory:
                        work = Path(directory)
                        for name, value in {
                            "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                            "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                            "replay.json": {"pricing_cache": requests},
                        }.items():
                            (work / name).write_text(json.dumps(value), encoding="utf-8")
                        run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                              str(work / "replay.json")], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    actual = json.loads(run.stdout)
                    self.assertEqual(len(actual), len(expected))
                    for index, (native, python) in enumerate(zip(actual, expected)):
                        with self.subTest(request=requests[index]):
                            self.assertEqual(native, python)
        self.assertEqual(coverage, {"unreachable_neighbor", "unreachable_middle", "empty_window", "retained_origin"})

    def replay_windows(self, case, config):
        config = copy.deepcopy(config)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        tree = ast.parse((ROOT / "src/heuristic_seat_allocator.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_structured_group_patterns")
        loop = next(n for n in function.body if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "group")
        def index(body, name):
            return next(i for i, n in enumerate(body) if
                        (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
                        or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name))
        order_code = compile(ast.Module(body=function.body[index(function.body, "base_min_group_size"):function.body.index(loop)], type_ignores=[]), "frozen_structured_order", "exec")
        window_code = compile(ast.Module(body=loop.body[index(loop.body, "seats_by_row"):index(loop.body, "ordinary_group")], type_ignores=[]), "frozen_windows", "exec")
        new = evaluator.SeatTopology(case["newSeatmapData"]["seats"], config)
        old = evaluator.SeatTopology(case["oldSeatmapData"]["seats"], config)
        fixed = exact._preprocess_fixed_seats(case["groupsData"], new, config)
        # Metrics accept partial checkpoint assignments; window domains use only fixed reservations.
        assignments, initial = {}, []
        for i, (group, passenger) in enumerate((g, p) for g in case["groupsData"] for p in g["psrs"]):
            seat = (passenger.get("newSeat") or {}).get("seatNum") or (passenger.get("oldSeat") or {}).get("seatNum")
            if seat in new.seat_map:
                assignments[group["groupId"], passenger["hostnum"]] = seat
                initial.append([i, seat])
        namespace = dict(rich.__dict__, algorithm=config["algorithm"], config=config,
                         weights=config["weights"], groups_data=case["groupsData"],
                         new_seats_data=case["newSeatmapData"]["seats"], old_seats_data=case["oldSeatmapData"]["seats"],
                         new_topology=new, old_topology=old, evaluator_module=evaluator,
                         context=types.SimpleNamespace(assigned_seats=assignments), diagnostics={})
        exec(order_code, namespace)
        expected = {"min_group_size": namespace["min_group_size"],
                    "full_resource_global_blocks": namespace["full_resource_global_blocks"],
                    "ordered_groups": [g["groupId"] for g in namespace["ordered_groups"]],
                    "repair_queue": namespace["diagnostics"]["repair_queue"], "difficult_groups": [], "windows": {}}
        difficult_statement = loop.body[index(loop.body, "difficult")]
        for group in namespace["ordered_groups"]:
            metric = namespace["repair_metrics"][group["groupId"]]
            namespace.update(passengers=group["psrs"], group=group, current_metric=metric,
                             target_span=max(0, metric["row_span"] - 2),
                             cache=exact._build_group_pricing_cache(group, new, old, config["weights"], config, fixed))
            exec(compile(ast.Module(body=[difficult_statement], type_ignores=[]), "frozen_difficult", "exec"), namespace)
            if namespace["difficult"]: expected["difficult_groups"].append(group["groupId"])
            exec(window_code, namespace)
            expected["windows"][str(group["groupId"])] = {k: namespace[k] for k in ("minimum_width", "old_center", "all_row_windows", "row_windows")}
        expected = json.loads(json.dumps(expected))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"structured_windows": True, "assignments": initial},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(len(actual["repair_queue"]), len(expected["repair_queue"]))
        for native, python in zip(actual.pop("repair_queue"), expected.pop("repair_queue")):
            self.assertEqual(native.keys(), python.keys())
            for key in python: self.assertAlmostEqual(native[key], python[key], places=8)
        self.assertEqual(actual, expected)
        return actual

    def test_order_and_windows_match_frozen_python(self):
        full_resource_seen = False
        for case in self.cases[:11]:
            for variant in ({"business_time_limit_seconds": 5.0}, {"business_time_limit_seconds": 60.0},
                            {"business_time_limit_seconds": 60.0, "structured_pattern_extra_rows": -1,
                             "structured_pattern_window_limit": 0, "enable_global_full_resource_blocks": False}):
                with self.subTest(case=case["id"], variant=variant):
                    config = copy.deepcopy(self.config)
                    config["algorithm"].update(variant)
                    actual = self.replay_windows(case, config)
                    full_resource_seen |= actual["full_resource_global_blocks"]
        self.assertTrue(full_resource_seen)

    def test_window_value_order_explicit_values_and_unknown_old_seats(self):
        for no_old in (False, True):
            with self.subTest(no_old=no_old):
                case = copy.deepcopy(self.cases[1])
                case["oldSeatmapData"] = copy.deepcopy(case["oldSeatmapData"])
                for group in case["groupsData"]:
                    for passenger in group["psrs"]:
                        passenger["oldSeat"]["seatValue"] = 75.0
                        if no_old: passenger["oldSeat"]["seatNum"] = "missing"
                for seat in case["newSeatmapData"]["seats"]: seat["seatValue"] = 75.0 if seat["row"] == 2 else 0.0
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=60.0, structured_pattern_window_limit=1,
                                           structured_research_min_group_size=0)
                self.replay_windows(case, config)

    def replay(self, case, assignments, config=None, value_blocks=False, expired=False):
        config = copy.deepcopy(config or self.config)
        active = sorted({p["ssr"] for g in case["groupsData"] for p in g["psrs"] if p.get("ssr")})
        config["_column_generation_active_ssr_types"] = active
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        expected = python_rigid_relaxed(case, config, assignments, value_blocks, expired)
        requests = [dict(group_index=i, current_targets=[assignments.get((g["groupId"], p["hostnum"])) for p in g["psrs"]])
                    for i, g in enumerate(case["groupsData"])]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, value in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"], "config.json": config,
                "replay.json": {"rigid_relaxed": requests, "active_ssr_types": active,
                                "value_blocks": value_blocks, "expired": expired},
            }.items():
                (work / name).write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        self.assertEqual(len(actual), len(expected))
        for native, python in zip(actual, expected):
            self.assertAlmostEqual(native.pop("master_cost"), python.pop("master_cost"), places=8)
            for name in ("ssr_all", "ssr_flagged"):
                native[name] = {json.dumps(k): v for k, v in native[name]}
                python[name] = {json.dumps(k): v for k, v in python[name]}
            self.assertEqual(native, python)
        return actual

    def test_public_construction_states_match_frozen_rigid_relaxed(self):
        prefix = construction_prefix()
        sources = set()
        for case in self.cases[:11]:
            config = copy.deepcopy(self.config)
            config["algorithm"].update(business_time_limit_seconds=120.0, adaptive_stage_budgets=False,
                                       construction_time_budget=60.0, small_group_dfs_time_limit=20.0)
            context = prefix(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                             case["groupsData"], config["weights"], config)["context"]
            with self.subTest(case=case["id"]):
                actual = self.replay(case, context.assigned_seats, config)
                sources.update(p["source"] for p in actual)
        self.assertEqual(sources, {"rigid", "relaxed"})

    def test_activation_shift_caps_incomplete_and_duplicate_source_overwrite(self):
        case = self.synthetic([(101, {}), (101, {})])
        assignments = {(101, 1): "2A", (101, 2): "2B"}
        for variant in ({}, {"enable_three_tier_patterns": False}, {"business_time_limit_seconds": 9.99},
                        {"structured_rigid_shift_rows": 0}, {"structured_rigid_shift_rows": -3},
                        {"three_tier_min_business_time_seconds": None}):
            with self.subTest(variant=variant):
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=10.0, three_tier_min_business_time_seconds=10.0)
                config["algorithm"].update(variant)
                if variant.get("three_tier_min_business_time_seconds", 1) is None:
                    config["algorithm"].pop("three_tier_min_business_time_seconds")
                actual = self.replay(case, assignments, config)
                if variant.get("enable_three_tier_patterns") is False or variant.get("business_time_limit_seconds") == 9.99:
                    self.assertEqual(actual, [])
                else:
                    moved = next(p for p in actual if p["assignments"] == [[[101, 1], "1A"], [[101, 2], "1B"]])
                    self.assertEqual(moved["source"], "relaxed")
        config["algorithm"]["enable_three_tier_patterns"] = True
        self.assertEqual(self.replay(case, {(101, 1): "2A"}, config), [])

    def test_protection_backtracking_and_caregiver_mirroring(self):
        case = self.synthetic([(101, {"mandatoryRule": {"needSingleSideEmpty": "Y"}}), (101, {}),
                               (202, {"ssr": "BLND"}), (202, {})])
        config = copy.deepcopy(self.config)
        config["algorithm"]["business_time_limit_seconds"] = 60.0
        actual = self.replay(case, {(101, 1): "2B", (101, 2): "2A", (202, 1): "1C", (202, 2): "1D"}, config)
        original = next(p for p in actual if p["assignments"] == [[[101, 1], "2B"], [[101, 2], "2A"]])
        self.assertEqual(original["blocked_by"], [["2C", [101, 1]]])
        self.assertTrue(all(p["caregiver_ok"] for p in actual))

    def test_unequal_row_widths_and_string_ordered_subrows(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                case = self.synthetic([(101, {}), (101, {}), (101, {})])
                prototypes = {s["col"]: s for s in case["newSeatmapData"]["seats"] if s["row"] == 1}
                seats = [{**prototypes[col], "seatId": f"{row}{col}", "row": row}
                         for row in (1, 2, 3, 9, 10, 11)
                         for col in ("ABCF" if row == 9 else "ABCDEF")]
                case["oldSeatmapData"] = {"seats": copy.deepcopy(seats)}
                case["newSeatmapData"] = {"seats": seats[::-1] if reverse else seats}
                config = copy.deepcopy(self.config)
                config["algorithm"].update(business_time_limit_seconds=60.0, structured_rigid_shift_rows=1)
                actual = self.replay(case, {(101, 1): "10F", (101, 2): "2A", (101, 3): "10D"}, config)
                self.assertTrue(actual)

    def test_value_blocks_match_frozen_prefix_and_overwrite_sources(self):
        prefix = construction_prefix()
        sources = set()
        for case in self.cases[:11]:
            config = copy.deepcopy(self.config)
            config["algorithm"].update(business_time_limit_seconds=120.0, adaptive_stage_budgets=False,
                                       construction_time_budget=60.0, small_group_dfs_time_limit=20.0)
            assignments = prefix(case["newSeatmapData"]["seats"], case["oldSeatmapData"]["seats"],
                                 case["groupsData"], config["weights"], config)["context"].assigned_seats
            with self.subTest(case=case["id"]):
                actual = self.replay(case, assignments, config, value_blocks=True)
                sources.update(p["source"] for p in actual)
        self.assertIn("global_value_block", sources)
        case = self.synthetic([(101, {}), (101, {})])
        case["oldSeatmapData"] = copy.deepcopy(case["oldSeatmapData"])
        config = copy.deepcopy(self.config)
        config["algorithm"]["business_time_limit_seconds"] = 60.0
        assignments = {(101, 1): "2A", (101, 2): "2B"}
        actual = self.replay(case, assignments, config, value_blocks=True)
        self.assertIn("value_block", {p["source"] for p in actual})
        expired = self.replay(case, assignments, config, value_blocks=True, expired=True)
        self.assertFalse(any(p["source"].endswith("value_block") for p in expired))
