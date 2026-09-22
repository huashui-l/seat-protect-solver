import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.allocation_evaluator import SeatTopology
from src import exact_column_generation as exact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS = PROJECT_ROOT / "data/generator_v3_2/d3_official_24cases_2026-09-07"
CONFIG_PATH = PROJECT_ROOT / "rich_python_reference_config.json"
PROBE = PROJECT_ROOT / "outputs/research/native_full_core_probe.exe"


class FullCppCoreParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PROBE.exists():
            raise unittest.SkipTest("native_full_core_probe.exe has not been built")
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_formal24_raw_problem_and_topology_match_python(self):
        case_paths = sorted(CORPUS.glob("*/*_groups.json"))
        self.assertEqual(24, len(case_paths))
        for case_path in case_paths:
            with self.subTest(case_path=case_path.relative_to(CORPUS)):
                raw_case = json.loads(case_path.read_text(encoding="utf-8"))
                direction = raw_case["direction"]
                seatmap_path = PROJECT_ROOT / self.config["input_contract"][
                    "seatmaps_by_direction"
                ][direction]["new"]
                seatmap = json.loads(seatmap_path.read_text(encoding="utf-8"))
                topology = SeatTopology(seatmap["seats"], self.config)

                completed = subprocess.run(
                    [
                        str(PROBE),
                        "--input", str(case_path),
                        "--config", str(CONFIG_PATH),
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                native = json.loads(completed.stdout)
                passengers = [
                    passenger
                    for group in raw_case["groups"]
                    for passenger in group["psrs"]
                ]

                expected_counts = {
                    "seat_count": len(seatmap["seats"]),
                    "group_count": len(raw_case["groups"]),
                    "passenger_count": len(passengers),
                    "fixed_count": sum(bool(p.get("newSeat", {}).get("seatNum")) for p in passengers),
                    "ssr_count": sum(bool(p.get("ssr")) for p in passengers),
                    "need_cared_count": sum(p.get("needCared") == "Y" for p in passengers),
                    "single_protection_count": sum(
                        p.get("mandatoryRule", {}).get("needSingleSideEmpty") == "Y"
                        for p in passengers
                    ),
                    "double_protection_count": sum(
                        p.get("mandatoryRule", {}).get("needBothSideEmpty") == "Y"
                        for p in passengers
                    ),
                }
                deterministic_fixed_blocked = set()
                for passenger in passengers:
                    fixed_seat = passenger.get("newSeat", {}).get("seatNum")
                    if (
                        fixed_seat
                        and passenger.get("mandatoryRule", {}).get("needBothSideEmpty") == "Y"
                    ):
                        deterministic_fixed_blocked.update(
                            topology.row_neighbors(
                                fixed_seat,
                                allow_cross_aisle=self.config["mandatory_rules"][
                                    "both_side_empty_allow_cross_aisle"
                                ],
                            )
                        )
                expected_counts["deterministic_fixed_blocked_count"] = len(
                    deterministic_fixed_blocked
                )
                expected_counts["initially_assigned_count"] = expected_counts["fixed_count"]
                expected_counts["initially_blocked_count"] = len(deterministic_fixed_blocked)
                self.assertEqual(raw_case["caseId"], native["case_id"])
                self.assertEqual(direction, native["direction"])
                self.assertTrue(native["state_self_test"])
                for field, expected in expected_counts.items():
                    self.assertEqual(expected, native[field], field)

                expected_passengers = []
                expected_groups = []
                passenger_index = 0
                for group_index, group in enumerate(raw_case["groups"]):
                    group_passengers = []
                    for passenger in group["psrs"]:
                        mandatory = passenger.get("mandatoryRule", {})
                        expected_passengers.append({
                            "group_index": group_index,
                            "group_id": group["groupId"],
                            "hostnum": passenger["hostnum"],
                            "cabin": {
                                "F": "First", "A": "First", "C": "Business", "J": "Business",
                                "W": "PremiumEconomy", "P": "PremiumEconomy", "Y": "Economy", "E": "Economy",
                            }.get(
                                passenger["cabin"], passenger["cabin"]
                            ),
                            "ssr": passenger.get("ssr", ""),
                            "old_seat": passenger.get("oldSeat", {}).get("seatNum", ""),
                            "fixed_seat": passenger.get("newSeat", {}).get("seatNum", ""),
                            "need_cared": passenger.get("needCared") == "Y",
                            "need_both_empty": mandatory.get("needBothSideEmpty") == "Y",
                            "need_single_empty": mandatory.get("needSingleSideEmpty") == "Y",
                            "same_subrow_no_other_ssr": mandatory.get("sameSubRowNoOtherSSR") == "Y",
                            "same_row_no_other_ssr": mandatory.get("sameRowNoOtherSSR") == "Y",
                        })
                        group_passengers.append(passenger_index)
                        passenger_index += 1
                    expected_groups.append({"id": group["groupId"], "passengers": group_passengers})
                self.assertEqual(expected_passengers, native["passengers"])
                self.assertEqual(expected_groups, native["groups"])

                native_seats = {seat["id"]: seat for seat in native["seats"]}
                self.assertEqual(set(topology.seat_map), set(native_seats))
                for seat_id, raw_seat in topology.seat_map.items():
                    actual = native_seats[seat_id]
                    self.assertEqual(int(raw_seat["row"]), actual["row"])
                    self.assertEqual(raw_seat["col"], actual["column"])
                    self.assertEqual(topology.subrow[seat_id][1], actual["subrow"])
                    self.assertEqual(topology.index_in_row[seat_id], actual["index_in_row"])
                    self.assertAlmostEqual(topology.x[seat_id], actual["x"], places=12)
                    self.assertAlmostEqual(topology.y[seat_id], actual["y"], places=12)
                    self.assertEqual(raw_seat.get("seatClass", ""), actual["cabin"])
                    self.assertEqual(bool(raw_seat.get("isWindow", False)), actual["window"])
                    self.assertEqual(bool(raw_seat.get("isAisle", False)), actual["aisle"])
                    self.assertEqual(bool(raw_seat.get("isExitRow", False)), actual["exit_row"])
                    self.assertEqual(bool(raw_seat.get("hasBassinet", False)), actual["bassinet"])
                    self.assertEqual(bool(raw_seat.get("extraLegroom", False)), actual["extra_legroom"])
                    self.assertEqual(
                        bool(raw_seat.get("nearToilet", raw_seat.get("isNearToilet", False))),
                        actual["near_toilet"],
                    )
                    self.assertEqual(topology.subrow_neighbors(seat_id), actual["same_block_neighbors"])
                    self.assertEqual(topology.row_neighbors(seat_id), actual["row_neighbors"])

    def test_fixed_preprocessing_rejects_same_invalid_cases_as_python(self):
        base_path = CORPUS / "forward/50_normal_groups.json"
        base = json.loads(base_path.read_text(encoding="utf-8"))
        seatmap_path = PROJECT_ROOT / self.config["input_contract"][
            "seatmaps_by_direction"
        ]["forward"]["new"]
        seatmap = json.loads(seatmap_path.read_text(encoding="utf-8"))
        topology = SeatTopology(seatmap["seats"], self.config)

        def clean_case():
            case = copy.deepcopy(base)
            for group in case["groups"]:
                for passenger in group["psrs"]:
                    passenger.pop("newSeat", None)
                    passenger["ssr"] = ""
                    passenger["needCared"] = "N"
                    passenger["mandatoryRule"] = {
                        "needBothSideEmpty": "N",
                        "needSingleSideEmpty": "N",
                        "sameSubRowNoOtherSSR": "N",
                        "sameRowNoOtherSSR": "N",
                    }
            return case

        def first_passengers(case, count):
            return [
                passenger for group in case["groups"] for passenger in group["psrs"]
            ][:count]

        cases = {}
        case = clean_case()
        first_passengers(case, 1)[0]["newSeat"] = {"seatNum": "999Z"}
        cases["unknown_fixed_seat"] = case

        case = clean_case()
        for passenger in first_passengers(case, 2):
            passenger["newSeat"] = {"seatNum": "5A"}
        cases["duplicate_fixed_seat"] = case

        case = clean_case()
        passenger = first_passengers(case, 1)[0]
        passenger["newSeat"] = {"seatNum": "1A"}
        passenger["mandatoryRule"]["needBothSideEmpty"] = "Y"
        cases["two_sided_protection_at_edge"] = case

        case = clean_case()
        passenger = first_passengers(case, 1)[0]
        passenger["newSeat"] = {"seatNum": "5A"}
        passenger["ssr"] = "WCHR"
        cases["wheelchair_not_at_aisle"] = case

        case = clean_case()
        left, right = first_passengers(case, 2)
        left["newSeat"] = {"seatNum": "5A"}
        right["newSeat"] = {"seatNum": "5F"}
        left["ssr"] = right["ssr"] = "UM"
        left["mandatoryRule"]["sameRowNoOtherSSR"] = "Y"
        cases["conditional_ssr_row_conflict"] = case

        for name, raw_case in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                python_accepted = True
                try:
                    exact._preprocess_fixed_seats(raw_case["groups"], topology, self.config)
                except ValueError:
                    python_accepted = False
                case_path = Path(directory) / "case.json"
                case_path.write_text(json.dumps(raw_case), encoding="utf-8")
                native = subprocess.run(
                    [str(PROBE), "--input", str(case_path), "--config", str(CONFIG_PATH)],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                native_accepted = native.returncode == 0
                self.assertFalse(python_accepted)
                self.assertEqual(python_accepted, native_accepted, native.stderr)


if __name__ == "__main__":
    unittest.main()
