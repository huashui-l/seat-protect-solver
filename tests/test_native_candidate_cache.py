import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import heuristic_seat_allocator as rich
from tests.general_validation_case_factory import materialize_cases

ROOT = Path(__file__).resolve().parents[1]


class NativeCandidateCacheTests(unittest.TestCase):
    def test_costs_regrets_and_stable_rankings_match_python(self):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            self.skipTest("set SEAT_PROTECT_NATIVE_EXE to the current build")
        probe = Path(executable).resolve().with_name("native_rich_repair_probe.exe")
        base = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cases = materialize_cases()
        missing_old = copy.deepcopy(cases[0])
        missing_old["id"] = "missing_old_and_multiple_preferences"
        passenger = missing_old["groupsData"][0]["psrs"][0]
        passenger["oldSeat"] = {"seatNum": "missing", "seatValue": "11.5"}
        passenger["optionRule"] = [{"nearToilet": "Y", "weight": 2}, {"nearToilet": "N", "weight": 3}]
        tied = copy.deepcopy(cases[0])
        tied["id"] = "duplicate_old_owner_and_tied_cost"
        for group in tied["groupsData"]:
            for passenger in group["psrs"]:
                passenger["oldSeat"] = {"seatNum": "1A"}
        scenarios = [(case, mode, pressure) for case in cases[:11]
                     for mode, pressure in (("empty", 0.0), ("fixed", 1.0), ("mixed", 2.5))]
        scenarios += [(missing_old, "mixed", 1.0), (tied, "empty", 0.0)]
        for case, mode, pressure in scenarios:
            with self.subTest(case=case["id"], mode=mode, pressure=pressure):
                config = copy.deepcopy(base)
                config["algorithm"]["old_seat_reservation_pressure"] = pressure
                if case is tied:
                    config["weights"] = {key: 0.0 for key in config["weights"]}
                config["input_contract"] = {"seatmaps_by_direction": {
                    "public-test": {"old": "old.json", "new": "new.json"}}}
                topology = rich.build_seat_topology(case["newSeatmapData"]["seats"], config)
                globals_ = dict(zip(("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW",
                                     "_SEAT_X", "_SEAT_ROW_INDEX"), topology))
                globals_.update(_ACTIVE_CONFIG=config, _OLD_SEATS={s["seatId"]: rich.Seat(s)
                                for s in case["oldSeatmapData"]["seats"]},
                                _OLD_SEAT_X=rich.build_seat_topology(case["oldSeatmapData"]["seats"], config)[3],
                                _OLD_SEAT_OWNER_REGRET={})
                with patch.multiple(rich, **globals_):
                    seats = {s["seatId"]: rich.Seat(s) for s in case["newSeatmapData"]["seats"]}
                    groups = [rich.Group(g) for g in case["groupsData"]]
                    passengers = [(g.group_id, p) for g in groups for p in g.passengers]
                    context = rich.AssignmentContext(seats)
                    initial = []
                    if mode != "empty":
                        for index, (group, passenger) in enumerate(passengers):
                            if passenger.new_seat_num:
                                self.assertTrue(context.assign_passenger(passenger, passenger.new_seat_num, group))
                                initial.append([index, passenger.new_seat_num])
                    if mode == "mixed":
                        for index, (group, passenger) in enumerate(passengers):
                            if passenger.new_seat_num or (index % 3 and passenger.ssr != "BSCT"):
                                continue
                            for seat in seats:
                                if context.assign_passenger(passenger, seat, group):
                                    initial.append([index, seat])
                                    break
                    values = {s: rich.calc_seat_value(seat, config) for s, seat in seats.items()}
                    owners = {p.old_seat_num: (g, p.hostnum) for g, p in passengers
                              if p.old_seat_num in rich._OLD_SEATS}
                    regrets = rich.compute_old_seat_owner_regret(groups, seats, values, config["weights"], config, context)
                    rich._OLD_SEAT_OWNER_REGRET = regrets
                    rankings = rich.build_passenger_sorted_seats(groups, seats, values, config["weights"],
                                                                  config, context, owners)
                    keys = [[rich.calc_seat_sort_key(p, seat, values, config["weights"], config, context, g, owners)
                             for seat in seats.values()] for g, p in passengers]
                    expected_order = [[list(seats).index(s.seat_id) for s in rankings[g, p.hostnum]] for g, p in passengers]
                    expected_regret = [regrets[g, p.hostnum] for g, p in passengers]
                with tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    for name, data in {
                        "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                        "config.json": config, "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"],
                        "replay.json": {"assignments": initial, "rank_only": True},
                    }.items():
                        (work / name).write_text(json.dumps(data), encoding="utf-8")
                    run = subprocess.run([str(probe), str(work / "case.json"), str(work / "config.json"),
                                          str(work / "replay.json")], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                actual = json.loads(run.stdout)["passengers"]
                self.assertEqual(len(actual), len(passengers))
                for index, row in enumerate(actual):
                    self.assertAlmostEqual(row["regret"], expected_regret[index], places=10)
                    for cost, expected in zip(row["costs"], keys[index]):
                        self.assertAlmostEqual(cost, expected, places=10)
                    self.assertEqual(row["order"], expected_order[index], f"passenger {index}")
