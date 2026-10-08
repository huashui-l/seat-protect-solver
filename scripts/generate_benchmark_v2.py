#!/usr/bin/env python3
"""Generate the controlled benchmark described by benchmark-v2-spec.

The default invocation creates one development replicate.  Use ``--formal``
to create the five frozen replicates.  Existing Formal24/D4/CG30 files are
never modified.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import aircraft_seatmap_generator as aircraft
from src import passenger_data_generator as generator

SPEC_PATH = ROOT / "docs/provenance/benchmark-v2-spec-2026-10-07.json"
DEFAULT_OUT = ROOT / "data/generator_v3_2/benchmark_v2_2026-10-07"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def case_config(spec: dict[str, Any], profile: dict[str, Any], special: dict[str, Any]) -> dict:
    config = copy.deepcopy(generator.SCENARIOS["stress"])
    config["group_size_weights"] = {
        int(size): float(weight)
        for size, weight in profile["group_size_weights"].items()
    }
    for key, value in special.items():
        config[key] = copy.deepcopy(value)
    # The profile is a controlled benchmark dimension.  Keep old-seat
    # geometry fixed at the stress distribution so PNR composition and demand
    # are not silently confounded with a second geometry profile.
    config["synthetic_old_assignment_profile"] = copy.deepcopy(
        generator.SCENARIOS["stress"]["synthetic_old_assignment_profile"]
    )
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--formal", action="store_true", help="generate all five frozen replicates")
    parser.add_argument("--seeds", type=int, help="number of replicates; defaults to 5 with --formal, otherwise 1")
    parser.add_argument("--limit", type=int, help="development smoke limit on the number of specifications")
    parser.add_argument(
        "--special-profiles", default="mixed",
        help="comma-separated special profiles; default is the formal mixed profile",
    )
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    seed_count = args.seeds if args.seeds is not None else (5 if args.formal else 1)
    if seed_count <= 0:
        raise SystemExit("--seeds must be positive")
    rows = spec["specs"][:args.limit] if args.limit else spec["specs"]
    profiles = spec["pnr_profiles"]
    specials = spec["special_profiles"]
    selected_specials = [name.strip() for name in args.special_profiles.split(",") if name.strip()]
    unknown_specials = sorted(set(selected_specials) - set(specials))
    if unknown_specials:
        raise SystemExit(f"unknown special profiles: {unknown_specials}")

    out = args.output.resolve()
    seatmap_dir = out / "seatmaps"
    seatmaps: dict[str, Path] = {}
    for layout, (filename, factory) in aircraft.PRESETS.items():
        path = seatmap_dir / filename
        write_json(path, factory())
        seatmaps[layout] = path

    cases: list[dict] = []
    failures: list[dict] = []
    for row_index, row in enumerate(rows):
        old_data = json.loads(seatmaps[row["old_layout"]].read_text(encoding="utf-8"))
        new_data = json.loads(seatmaps[row["new_layout"]].read_text(encoding="utf-8"))
        old_topology = generator.SeatTopology(old_data["seats"])
        new_topology = generator.SeatTopology(new_data["seats"])
        profile = profiles[row["pnr_profile"]]
        for replicate in range(seed_count):
            seed = int(spec["seed_policy"]["generation_seed_base"]) + row_index * 100 + replicate
            # Use a separate algorithm seed in the metadata.  Solvers must not
            # accidentally reuse the generator stream when comparing methods.
            algorithm_seed = int(spec["seed_policy"]["algorithm_seed_base"]) + row_index * 100 + replicate
            for special_index, special_id in enumerate(selected_specials):
                special = specials[special_id]
                # Keep the generation stream reproducible across special
                # profiles while holding the case seed and specification fixed.
                rng = random.Random(seed + special_index * 1_000_000)
                config = case_config(spec, profile, special)
                diagnostics: dict = {}
                case_id = f"{row['spec_id']}:{special_id}:r{replicate}"
                try:
                    groups = generator.generate_groups(
                        old_topology,
                        int(row["requested_resource_demand"]),
                        rng,
                        config,
                        target_topology=new_topology,
                        diagnostics_out=diagnostics,
                    )
                    fixed_count = 0
                    preassignment_errors: list[str] = []
                    # A fixed target seat is part of the input contract.  At
                    # 97% demand a random fixed seat can block an otherwise
                    # feasible caregiver or protection pattern, so retry the
                    # fixed-seat sample while keeping the generated groups
                    # and all other dimensions unchanged.
                    for preassign_attempt in range(20):
                        candidate_groups = copy.deepcopy(groups)
                        preassign_rng = random.Random(
                            seed + special_index * 1_000_000 + preassign_attempt * 10_000
                        )
                        candidate_fixed_count = generator.generate_preassignments(
                            candidate_groups, old_topology, new_topology,
                            preassign_rng, float(special["preassign_ratio"]),
                        )
                        errors = generator.validate_case(
                            candidate_groups, old_topology, new_topology, config
                        )
                        if not errors:
                            groups = candidate_groups
                            fixed_count = candidate_fixed_count
                            diagnostics["preassignment_attempts"] = preassign_attempt + 1
                            break
                        preassignment_errors = errors
                    else:
                        raise RuntimeError("; ".join(preassignment_errors[:5]))
                except Exception as exc:  # recorded as a generation outcome
                    failures.append({"case_id": case_id, "error": str(exc), "seed": seed})
                    continue

                demand = generator.target_seat_demand(groups)
                travelers = generator.passenger_count(groups)
                path = out / "cases" / f"{case_id.replace(':', '_')}_groups.json"
                payload = {
                    "generatorVersion": generator.GENERATOR_VERSION,
                    "corpus": "benchmark_v2_2026-10-07",
                    "caseId": case_id,
                    "specId": row["spec_id"],
                    "specialProfile": special_id,
                    "replicate": replicate,
                    "difficulty": {"resourceLoadTarget": row["resource_load_target"],
                                   "pnrProfile": row["pnr_profile"], "specialProfile": special_id},
                    "seed": seed,
                    "algorithmSeed": algorithm_seed,
                    "requestedResourceDemand": row["requested_resource_demand"],
                    "targetSeatDemand": demand,
                    "travelerCount": travelers,
                    "protectedSeatCount": generator.protected_seat_count(groups),
                    "passengerCapacity": row["common_transferable_capacity"],
                    "resourceLoad": demand / row["common_transferable_capacity"],
                    "fixedSeatCount": fixed_count,
                    "pnrSizeHistogram": dict(sorted(
                        {str(size): sum(len(g["psrs"]) == size for g in groups)
                         for size in range(1, 11)}.items(),
                        key=lambda item: int(item[0]),
                    )),
                    "ssrCounts": dict(sorted(
                        {
                            ssr: sum(
                                p.get("ssr") == ssr
                                for g in groups for p in g["psrs"]
                            )
                            for ssr in generator.SSR_PROFILES
                        }.items()
                    )),
                    "oldLayout": row["old_layout"],
                    "newLayout": row["new_layout"],
                    "generatorDiagnostics": generator.generator_diagnostics(groups, old_topology, config),
                    "jointSpecialResourceDiagnostics": diagnostics,
                    "groups": groups,
                }
                write_json(path, payload)
                relative_path = (
                    str(path.relative_to(ROOT)).replace("\\", "/")
                    if path.is_relative_to(ROOT)
                    else str(path)
                )
                cases.append({
                    "case_id": case_id, "spec_id": row["spec_id"], "special_profile": special_id,
                    "replicate": replicate, "seed": seed, "algorithm_seed": algorithm_seed,
                    "relative_path": relative_path,
                    "sha256": sha256(path), "traveler_count": travelers, "target_seat_demand": demand,
                    "protected_seat_count": generator.protected_seat_count(groups),
                    "fixed_seats": fixed_count, "pnr_profile": row["pnr_profile"],
                    "resource_load_target": row["resource_load_target"],
                })

    source_spec = (
        str(args.spec.resolve().relative_to(ROOT)).replace("\\", "/")
        if args.spec.resolve().is_relative_to(ROOT)
        else str(args.spec.resolve())
    )
    manifest = {
        "schema_version": 1,
        "corpus": "generator-v3.2 benchmark v2",
        "corpus_id": "benchmark_v2_2026-10-07",
        "status": "generated_development" if seed_count == 1 else "generated_formal_candidate",
        "source_spec": source_spec,
        "seed_count": seed_count, "spec_count": len(rows), "case_count": len(cases),
        "generation_failures": failures, "cases": cases,
    }
    write_json(out / "manifest.json", manifest)
    print(json.dumps({"output": str(out), "spec_count": len(rows), "case_count": len(cases),
                      "failure_count": len(failures)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
