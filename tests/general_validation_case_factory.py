from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from src import aircraft_seatmap_generator as seatmap_generator


SUITE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "general_validation_suite"
    / "cases.json"
)


def load_suite() -> dict[str, Any]:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def build_seatmaps(suite: dict[str, Any]) -> dict[str, dict]:
    result = {}
    for name, spec in suite["seatmaps"].items():
        cabins = [
            seatmap_generator.CabinConfig.from_dict(raw)
            for raw in spec["cabins"]
        ]
        result[name] = seatmap_generator.generate_aircraft(
            spec["aircraftModel"], cabins, []
        )
    return result


def _merge_passenger_override(passenger: dict, override: dict) -> None:
    for key, value in override.items():
        if key == "mandatoryRule":
            passenger["mandatoryRule"].update(value)
        elif key == "newSeat":
            passenger["newSeat"] = {"seatNum": str(value)}
        else:
            passenger[key] = copy.deepcopy(value)


def build_groups(case: dict[str, Any]) -> list[dict]:
    groups = []
    for group_spec in case["groups"]:
        overrides = group_spec.get("passengerOverrides", {})
        passengers = []
        for hostnum, old_seat in enumerate(group_spec["oldSeats"], start=1):
            passenger = {
                "hostnum": hostnum,
                "cabin": group_spec["cabin"],
                "ssr": "",
                "needCared": "N",
                "oldSeat": {"seatNum": old_seat, "seatValue": ""},
                "mandatoryRule": {
                    "needBothSideEmpty": "N",
                    "needSingleSideEmpty": "N",
                    "sameSubRowNoOtherSSR": "N",
                    "sameRowNoOtherSSR": "N"
                },
                "optionRule": [],
            }
            _merge_passenger_override(
                passenger, overrides.get(str(hostnum), {})
            )
            passengers.append(passenger)
        groups.append({
            "groupId": int(group_spec["groupId"]),
            "groupType": "communityType",
            "PNR": f"VALIDATION-{case['id']}-{group_spec['groupId']}",
            "psrs": passengers,
        })
    return groups


def materialize_cases() -> list[dict[str, Any]]:
    suite = load_suite()
    seatmaps = build_seatmaps(suite)
    materialized = []
    for case in suite["cases"]:
        reference_assignments = {
            (int(group["groupId"]), hostnum): seat_id
            for group in case["groups"]
            for hostnum, seat_id in enumerate(
                group.get("referenceSeats", []), start=1
            )
        }
        materialized.append({
            **case,
            "oldSeatmapData": seatmaps[case["oldSeatmap"]],
            "newSeatmapData": seatmaps[case["newSeatmap"]],
            "groupsData": build_groups(case),
            "referenceAssignments": reference_assignments,
        })
    return materialized
