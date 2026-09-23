import json
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.general_validation_case_factory import materialize_cases


ROOT = Path(__file__).resolve().parents[1]


class NativeStateDifferentialTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("SEAT_PROTECT_STATE_REPLAY"),
        "set SEAT_PROTECT_STATE_REPLAY to a binary built from this checkout",
    )
    def test_12000_deterministic_partial_state_operations(self):
        executable = Path(os.environ["SEAT_PROTECT_STATE_REPLAY"]).resolve()
        case = next(
            item for item in materialize_cases()
            if item["id"] == "identity_keep_seats"
        )
        passenger_count = sum(len(group["psrs"]) for group in case["groupsData"])
        seat_count = len(case["newSeatmapData"]["seats"])
        passenger_groups = [
            group_index
            for group_index, group in enumerate(case["groupsData"])
            for _ in group["psrs"]
        ]
        rng = random.Random(20260922)
        operations = []
        expected = []
        passenger_to_seat = [-1] * passenger_count
        seat_to_passenger = [-1] * seat_count
        owner_group_by_seat = [-1] * seat_count
        saved = None
        for _ in range(12_000):
            roll = rng.randrange(100)
            if roll < 40:
                passenger = rng.randrange(passenger_count)
                seat = rng.randrange(seat_count)
                legal = passenger_to_seat[passenger] < 0 and seat_to_passenger[seat] < 0
                operations.append({"op": "query", "passenger": passenger, "seat": seat})
                result = int(legal)
            elif roll < 65:
                passenger = rng.randrange(passenger_count)
                seat = rng.randrange(seat_count)
                legal = passenger_to_seat[passenger] < 0 and seat_to_passenger[seat] < 0
                operations.append({"op": "assign", "passenger": passenger, "seat": seat})
                result = int(legal)
                if legal:
                    passenger_to_seat[passenger] = seat
                    seat_to_passenger[seat] = passenger
                    owner_group_by_seat[seat] = passenger_groups[passenger]
            elif roll < 85:
                passenger = rng.randrange(passenger_count)
                operations.append({"op": "remove", "passenger": passenger})
                result = -1
                seat = passenger_to_seat[passenger]
                if seat >= 0:
                    passenger_to_seat[passenger] = -1
                    seat_to_passenger[seat] = -1
                    owner_group_by_seat[seat] = -1
            elif roll < 93:
                operations.append({"op": "save"})
                result = -1
                saved = (
                    passenger_to_seat.copy(), seat_to_passenger.copy(),
                    owner_group_by_seat.copy(),
                )
            else:
                operations.append({"op": "restore"})
                result = -1
                if saved is not None:
                    passenger_to_seat, seat_to_passenger = (
                        saved[0].copy(), saved[1].copy()
                    )
                    owner_group_by_seat = saved[2].copy()
            expected.append((
                result, passenger_to_seat.copy(), seat_to_passenger.copy(),
                owner_group_by_seat.copy(),
            ))

        base_config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "old.json").write_text(
                json.dumps(case["oldSeatmapData"]), encoding="utf-8"
            )
            (work / "new.json").write_text(
                json.dumps(case["newSeatmapData"]), encoding="utf-8"
            )
            case_path = work / "case.json"
            case_path.write_text(json.dumps({
                "caseId": "state-differential",
                "direction": "public-test",
                "groups": case["groupsData"],
            }), encoding="utf-8")
            config = dict(base_config)
            config["input_contract"] = {"seatmaps_by_direction": {
                "public-test": {"old": "old.json", "new": "new.json"}
            }}
            config_path = work / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            operations_path = work / "operations.json"
            operations_path.write_text(json.dumps(operations), encoding="utf-8")
            completed = subprocess.run([
                str(executable), "--input", str(case_path),
                "--config", str(config_path),
                "--operations", str(operations_path),
            ], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stderr)
        replay = json.loads(completed.stdout)
        self.assertEqual(12_000, replay["operation_count"])
        self.assertEqual(12_000, len(replay["steps"]))
        for index, (actual, wanted) in enumerate(zip(replay["steps"], expected)):
            with self.subTest(operation=index):
                self.assertEqual(wanted[0], actual["result"])
                self.assertEqual(wanted[1], actual["passenger_to_seat"])
                self.assertEqual(wanted[2], actual["seat_to_passenger"])
                self.assertEqual([0] * seat_count, actual["blocked_count"])
                self.assertEqual(wanted[3], actual["owner_group_by_seat"])
                self.assertEqual([-1] * seat_count, actual["seat_ssr_passenger"])


if __name__ == "__main__":
    unittest.main()
