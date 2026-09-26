#!/usr/bin/env python3
"""Generate the small, reproducible multi-aircraft smooth-difficulty corpus."""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import aircraft_seatmap_generator as aircraft
from src import passenger_data_generator as generator


OUT = ROOT / "data" / "generator_v3_2" / "d4_smooth_multi_aircraft_2026-09-25"
SEATMAP_DIR = OUT / "seatmaps"

# These directions cover all six built-in layouts and include both capacity
# increases and reductions. Requested demand is deliberately below each target
# capacity so the corpus exercises assignment quality rather than infeasibility.
TRANSITIONS = (
    ("rj22_to_nb23", "2-2", "2-3", 65),
    ("nb23_to_rj22", "2-3", "2-2", 65),
    ("nb33_to_wb242", "3-3", "2-4-2", 120),
    ("wb343_to_wb333", "3-4-3", "3-3-3", 180),
)
LEVELS = (("d00", 0.00), ("d33", 1 / 3), ("d66", 2 / 3), ("d100", 1.00))
SEED_BASE = 20260925


def interpolate(a: Any, b: Any, t: float) -> Any:
    if isinstance(a, dict):
        return {key: interpolate(a[key], b[key], t) for key in a}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        value = a + (b - a) * t
        return int(round(value)) if isinstance(a, int) and isinstance(b, int) else value
    return copy.deepcopy(a if t < 0.5 else b)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    SEATMAP_DIR.mkdir(parents=True, exist_ok=True)

    seatmaps: dict[str, Path] = {}
    for layout, (filename, factory) in aircraft.PRESETS.items():
        path = SEATMAP_DIR / filename
        write_json(path, factory())
        seatmaps[layout] = path

    cases: list[dict] = []
    for transition_index, (direction, old_layout, new_layout, passengers) in enumerate(TRANSITIONS):
        old_data = json.loads(seatmaps[old_layout].read_text(encoding="utf-8"))
        new_data = json.loads(seatmaps[new_layout].read_text(encoding="utf-8"))
        old_topology = generator.SeatTopology(old_data["seats"])
        new_topology = generator.SeatTopology(new_data["seats"])
        for level_index, (level, t) in enumerate(LEVELS):
            cfg = interpolate(generator.SCENARIOS["normal"], generator.SCENARIOS["edge"], t)
            seed = SEED_BASE + transition_index * 100 + level_index
            rng = random.Random(seed)
            diagnostics: dict = {}
            groups = generator.generate_groups(
                old_topology,
                passengers,
                rng,
                cfg,
                target_topology=new_topology,
                diagnostics_out=diagnostics,
            )
            fixed_count = generator.generate_preassignments(
                groups, old_topology, new_topology, rng, cfg["preassign_ratio"]
            )
            errors = generator.validate_case(groups, old_topology, new_topology, cfg)
            if errors:
                raise RuntimeError(f"{direction}/{level}: {errors[:5]}")

            counted = generator.target_seat_demand(groups)
            travelers = generator.passenger_count(groups)
            stem = f"{direction}_{level}"
            case_path = OUT / "cases" / f"{stem}_groups.json"
            write_json(
                case_path,
                {
                    "generatorVersion": generator.GENERATOR_VERSION,
                    "corpus": "d4_smooth_multi_aircraft_2026-09-25",
                    "caseId": f"d4:{direction}:{level}",
                    "difficultyLevel": level,
                    "difficultyInterpolation": t,
                    "requestedPassengerCount": passengers,
                    "passengerCount": counted,
                    "travelerCount": travelers,
                    "passengerCapacity": generator.transferable_seat_capacity(old_topology, new_topology),
                    "sizeLabel": generator.test_case_size_label(
                        passengers, counted, generator.transferable_seat_capacity(old_topology, new_topology)
                    ),
                    "targetSeatDemand": counted,
                    "seed": seed,
                    "fixedSeatCount": fixed_count,
                    "oldLayout": old_layout,
                    "newLayout": new_layout,
                    "generatorDiagnostics": generator.generator_diagnostics(groups, old_topology, cfg),
                    "jointSpecialResourceDiagnostics": diagnostics,
                    "groups": groups,
                },
            )
            cases.append(
                {
                    "case_id": f"d4:{direction}:{level}",
                    "direction": direction,
                    "difficulty_level": level,
                    "difficulty_interpolation": t,
                    "seed": seed,
                    "old_layout": old_layout,
                    "new_layout": new_layout,
                    "requested_passengers": passengers,
                    "passenger_count": counted,
                    "traveler_count": travelers,
                    "fixed_seats": fixed_count,
                    "relative_path": str(case_path.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": sha256(case_path),
                }
            )

    manifest = {
        "schema_version": 1,
        "corpus": "generator-v3.2 d4 smooth multi-aircraft",
        "corpus_id": "d4_smooth_multi_aircraft_2026-09-25",
        "generator_version": generator.GENERATOR_VERSION,
        "description": "Four aircraft transitions, six built-in layouts, four evenly interpolated normal-to-edge difficulty levels.",
        "seed_base": SEED_BASE,
        "difficulty_definition": {
            "source_low": "normal",
            "source_high": "edge",
            "levels": [{"name": name, "interpolation": t} for name, t in LEVELS],
        },
        "seatmaps": [
            {
                "layout": layout,
                "relative_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "aircraft_model": json.loads(path.read_text(encoding="utf-8"))["aircraftModel"],
                "total_seats": json.loads(path.read_text(encoding="utf-8"))["totalSeats"],
                "sha256": sha256(path),
            }
            for layout, path in sorted(seatmaps.items())
        ],
        "cases": cases,
    }
    write_json(OUT / "manifest.json", manifest)
    print(f"generated {len(cases)} cases in {OUT}")


if __name__ == "__main__":
    main()
