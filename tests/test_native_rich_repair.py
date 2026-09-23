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


class NativeRichRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = os.environ.get("SEAT_PROTECT_NATIVE_EXE")
        if not executable:
            raise unittest.SkipTest("set SEAT_PROTECT_NATIVE_EXE to the current build")
        cls.probe = Path(executable).resolve().with_name("native_rich_repair_probe.exe")
        cls.config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cls.cases = materialize_cases()

    def replay(self, case, initial, rankings, node_limit=20000, expected_orphan_count=0, construct_algorithm=None,
               paired_algorithm=None, rescue_algorithm=None):
        config = copy.deepcopy(self.config)
        config["algorithm"].update(final_repair_node_limit=node_limit, final_repair_time_limit=10.0)
        if construct_algorithm is not None:
            config["algorithm"].update(construct_algorithm)
        if paired_algorithm is not None:
            config["algorithm"].update(paired_algorithm)
        if rescue_algorithm is not None:
            config["algorithm"].update(rescue_algorithm)
        config["input_contract"] = {"seatmaps_by_direction": {
            "public-test": {"old": "old.json", "new": "new.json"}}}
        seats_data = case["newSeatmapData"]["seats"]
        topology = rich.build_seat_topology(seats_data, config)
        globals_ = dict(zip(("_SEAT_NEIGHBORS", "_SEAT_ROW_NEIGHBORS", "_SEAT_SUBROW",
                             "_SEAT_X", "_SEAT_ROW_INDEX"), topology))
        globals_["_ACTIVE_CONFIG"] = config
        globals_.update(_OLD_SEATS={s["seatId"]: rich.Seat(s) for s in case["oldSeatmapData"]["seats"]},
                        _OLD_SEAT_X=rich.build_seat_topology(case["oldSeatmapData"]["seats"], config)[3],
                        _OLD_SEAT_OWNER_REGRET={}, _GROUP_COMPACT_CACHE={})
        with patch.multiple(rich, **globals_):
            seats = {s["seatId"]: rich.Seat(s) for s in seats_data}
            groups = [rich.Group(g) for g in case["groupsData"]]
            passengers = [(g.group_id, p) for g in groups for p in g.passengers]
            context = rich.AssignmentContext(seats)
            for index, seat, *block in initial:
                group, passenger = passengers[index]
                self.assertTrue(context.assign_passenger(passenger, seat, group,
                                                         chosen_block=block[0] if block else None))
            ordered = {(group, p.hostnum): [seats[s] for s in row]
                       for (group, p), row in zip(passengers, rankings)}
            if construct_algorithm is not None or paired_algorithm is not None or rescue_algorithm is not None:
                values = {sid: rich.calc_seat_value(seat, config) for sid, seat in seats.items()}
                owners = {p.old_seat_num: (g, p.hostnum) for g, p in passengers if p.old_seat_num in rich._OLD_SEATS}
                rich._OLD_SEAT_OWNER_REGRET = rich.compute_old_seat_owner_regret(
                    groups, seats, values, config["weights"], config, context)
                if rescue_algorithm is not None:
                    expected = rich.rescue_failed_paired_ssrs(
                        groups, context, ordered, values, config["weights"], config, owners)
                elif paired_algorithm is not None:
                    expected = {"paired_added": rich.assign_paired_ssrs(
                        groups, context, ordered, values, config["weights"], config, owners)}
                else:
                    expected = rich.assign_remaining_passengers(groups, context, ordered, config["weights"], config, values, owners)
            else:
                expected = rich.repair_unassigned_by_local_relocation(groups, context, ordered, config)
            expected_assignments = [context.assigned_seats.get((g, p.hostnum)) for g, p in passengers]
            expected_blocked = [sorted(context.assigned_blocked.get((g, p.hostnum), ())) for g, p in passengers]
            orphan_occupied = context.occupied - set(context.assigned_seats.values())
            self.assertEqual(len(orphan_occupied), expected_orphan_count)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, data in {
                "case.json": {"caseId": case["id"], "direction": "public-test", "groups": case["groupsData"]},
                "config.json": config, "old.json": case["oldSeatmapData"], "new.json": case["newSeatmapData"],
                "replay.json": {"assignments": initial, "rankings": rankings,
                                "construct_remaining": construct_algorithm is not None,
                                "paired_ssrs": paired_algorithm is not None,
                                "paired_rescue": rescue_algorithm is not None},
            }.items():
                (work / name).write_text(json.dumps(data), encoding="utf-8")
            run = subprocess.run([str(self.probe), str(work / "case.json"), str(work / "config.json"),
                                  str(work / "replay.json")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        if expected_orphan_count:
            # Explicit reference defect reproduction, not a passing parity case.
            self.assertNotEqual(actual["assignments"], expected_assignments)
        else:
            self.assertEqual(actual["assignments"], expected_assignments)
            self.assertEqual([sorted(row) for row in actual["blocked"]], expected_blocked)
            fields = (("groups_considered", "dfs_attempted", "dfs_succeeded", "dfs_nodes", "beam_groups", "transaction_failures")
                      if construct_algorithm is not None else ("attempted", "repaired", "unresolved", "search_nodes"))
            if paired_algorithm is not None:
                fields = ("paired_added",)
            if rescue_algorithm is not None:
                fields = ()
                for name in ("attempted", "rescued", "unresolved"):
                    self.assertEqual(actual["rescue"][name], [list(key) for key in expected[name]], name)
                    self.assertEqual(len(actual["rescue"][name]), expected[name + "_count"])
                self.assertEqual(actual["rescue"]["joint_rebuilds"], expected.get("joint_rebuilds", 0))
            for name in fields:
                self.assertEqual(actual[name], expected[name], name)
        return actual

    def synthetic(self, passengers):
        case = copy.deepcopy(self.cases[0])
        prototype = case["groupsData"][0]["psrs"][0]
        groups = {}
        for group_id, attributes in passengers:
            group = groups.setdefault(group_id, {"groupId": group_id, "psrs": []})
            passenger = copy.deepcopy(prototype)
            passenger["hostnum"] = len(group["psrs"]) + 1
            passenger.update(attributes)
            group["psrs"].append(passenger)
        case["groupsData"] = list(groups.values())
        return case

    def test_dynamic_mrv_relocation_and_node_limit_rollback(self):
        case = self.synthetic([(301, {}), (102, {}), (203, {"mandatoryRule": {"needBothSideEmpty": "Y"}})])
        for limit in (0, 1, 2, 20000):
            with self.subTest(node_limit=limit):
                actual = self.replay(case, [[0, "1A"], [1, "1C"]],
                                     [["2A", "2B"], ["2A"], ["1B"]], limit)
                self.assertEqual(actual["assignments"], ["2B", "2A", "1B"] if limit >= 2 else ["1A", "1C", None])

    def test_care_group_permutation_while_other_group_remains_missing(self):
        case = self.synthetic([(101, {"ssr": "BLND"}), (101, {}),
                               (101, {"ssr": "BSCT"}), (902, {})])
        actual = self.replay(case, [[0, "1A"], [1, "1B"]],
                             [["1C"], ["1B"], ["1A"], ["1D"]])
        self.assertEqual(actual["assignments"], ["1C", "1B", "1A", "1D"])
        self.assertEqual(actual["repaired"], 2)

    def test_existing_caregiver_cannot_be_displaced(self):
        case = self.synthetic([(101, {"ssr": "BSCT"}), (101, {}), (902, {})])
        actual = self.replay(case, [[0, "1A"], [1, "1B"]],
                             [["1A"], ["2A"], ["1B"]])
        self.assertEqual(actual["assignments"], ["1A", "1B", None])
        self.assertEqual(actual["search_nodes"], 0)

    def test_public_partial_states_match_python_repair(self):
        for case in self.cases[:10]:
            passengers = [(g["groupId"], p) for g in case["groupsData"] for p in g["psrs"]]
            seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
            care_groups = {g["groupId"] for g in case["groupsData"] if any(
                rich.passenger_requires_caregiver(rich.Passenger(p), self.config) for p in g["psrs"])}
            rankings = [[p["newSeat"]["seatNum"]] if p.get("newSeat") else seats
                        for _, p in passengers]
            for partial in (False, True):
                with self.subTest(case=case["id"], partial=partial):
                    initial = []
                    for index, (group, passenger) in enumerate(passengers):
                        fixed = passenger.get("newSeat", {}).get("seatNum")
                        reference = case["referenceAssignments"].get((group, passenger["hostnum"]))
                        seat = fixed or (reference if partial and index % 3 != 0 else None)
                        # Python's ordinary construction fills caregivers before
                        # final repair. Preserve that stage-entry condition here.
                        if not seat and group in care_groups and rich.is_adult_caregiver(rich.Passenger(passenger)):
                            old = passenger.get("oldSeat", {}).get("seatNum")
                            if old in seats:
                                seat = old
                        if seat:
                            initial.append([index, seat])
                    self.replay(case, initial, rankings)

    def test_frozen_python_all_missing_caregiver_state_has_known_orphan_occupancy(self):
        case = next(c for c in self.cases if c["id"] == "caregiver_and_ssr")
        seats = [s["seatId"] for s in case["newSeatmapData"]["seats"]]
        count = sum(len(g["psrs"]) for g in case["groupsData"])
        self.replay(case, [], [seats] * count, expected_orphan_count=3)
