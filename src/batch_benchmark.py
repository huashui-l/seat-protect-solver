#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量生成座位保护测试数据，并且每个场景只输出一张对比图。"""

from __future__ import annotations

import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from . import passenger_data_generator as datagenerate
    from . import allocation_evaluator as evaluator
    from . import heuristic_seat_allocator as final
    from . import allocation_visualizer as visualize
except ImportError:  # 兼容直接运行当前脚本
    import passenger_data_generator as datagenerate
    import allocation_evaluator as evaluator
    import heuristic_seat_allocator as final
    import allocation_visualizer as visualize


PROJECT_ROOT = Path(__file__).resolve().parent.parent
POPULATIONS = (50, 100, 150, 200)
DIFFICULTIES = ("normal", "stress", "edge")
SEED_OFFSETS = {"normal": 40, "stress": 50, "edge": 60}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def generate_case(
    passengers: int,
    difficulty: str,
    old_topology: datagenerate.SeatTopology,
    new_topology: datagenerate.SeatTopology,
) -> Tuple[List[dict], int, int]:
    """生成并校验一个场景；若随机布局不可行，则确定性地尝试后续种子。"""
    initial_seed = passengers + SEED_OFFSETS[difficulty]
    cfg = datagenerate.SCENARIOS[difficulty]
    last_error: Exception | None = None

    for seed in range(initial_seed, initial_seed + 30):
        try:
            rng = random.Random(seed)
            groups = datagenerate.generate_groups(
                old_topology, passengers, rng, cfg, target_topology=new_topology
            )
            fixed_count = datagenerate.generate_preassignments(
                groups,
                old_topology,
                new_topology,
                rng,
                cfg["preassign_ratio"],
            )
            errors = datagenerate.validate_case(
                groups, old_topology, new_topology, cfg
            )
            if errors:
                raise RuntimeError("; ".join(errors[:5]))
            return groups, fixed_count, seed
        except RuntimeError as exc:
            last_error = exc

    raise RuntimeError(
        f"{passengers}_{difficulty} 在 30 个种子内均无法生成: {last_error}"
    )


def group_row_metrics(
    assignments: Dict[Tuple[int, int], str],
    new_seat_map: Dict[str, dict],
) -> Tuple[int, float, int]:
    rows_by_group: Dict[int, List[int]] = defaultdict(list)
    for (gid, _), seat_id in assignments.items():
        seat = new_seat_map.get(seat_id)
        if seat:
            rows_by_group[gid].append(int(seat["row"]))
    spans = [max(rows) - min(rows) for rows in rows_by_group.values() if rows]
    return (
        sum(span == 0 for span in spans),
        sum(spans) / len(spans) if spans else 0.0,
        max(spans, default=0),
    )


def run_case(
    traveler_count: int,
    counted_passengers: int,
    size_label: str,
    difficulty: str,
    groups: List[dict],
    fixed_count: int,
    seed: int,
    old_seats: List[dict],
    new_seats: List[dict],
    config: dict,
    output_path: Path,
) -> Dict[str, Any]:
    start = time.perf_counter()
    result, module_score = final.run_allocation(
        new_seats,
        old_seats,
        groups,
        config.get("weights", {}),
        config,
    )
    elapsed = time.perf_counter() - start
    assignments = visualize.normalize_assignment(result.get("assigned_seats", {}))
    blocked_by = visualize.normalize_blocked_by(result.get("blocked_by", {}))
    old_assignments = visualize.build_old_assignments(groups)
    movement = visualize.movement_statistics(
        groups,
        old_assignments,
        assignments,
        old_seats,
        new_seats,
    )
    movement["module_score"] = module_score

    visualize.save_comparison(
        output_path,
        old_seats,
        new_seats,
        groups,
        old_assignments,
        assignments,
        movement,
        show=False,
        case_label=(
            f"{size_label} {difficulty} "
            f"(travelers={traveler_count}, counted={counted_passengers})"
        ),
        new_blocked_by=blocked_by,
    )

    violations, unassigned, _ = evaluator.count_hard_constraint_violations(
        new_seats,
        groups,
        assignments,
        config,
    )
    soft_score, _ = evaluator.calculate_soft_score(
        new_seats,
        old_seats,
        groups,
        assignments,
        config.get("weights", {}),
        config,
    )
    combined_score, _ = evaluator.calculate_combined_score(
        soft_score,
        unassigned,
        violations,
        elapsed,
        config,
    )

    new_map = {seat["seatId"]: seat for seat in new_seats}
    old_map = {seat["seatId"]: seat for seat in old_seats}
    same_row_groups, average_row_span, max_row_span = group_row_metrics(
        assignments, new_map)
    cabin_mismatches = sum(
        old_map[passenger["oldSeat"]["seatNum"]]["seatClass"]
        != new_map[assignments[(group["groupId"], passenger["hostnum"])]]["seatClass"]
        for group in groups
        for passenger in group["psrs"]
        if (group["groupId"], passenger["hostnum"]) in assignments
    )
    ssr_counts = Counter(
        passenger.get("ssr")
        for group in groups
        for passenger in group["psrs"]
        if passenger.get("ssr")
    )

    return {
        "case": f"{size_label}_{difficulty}",
        "traveler_count": traveler_count,
        "passenger_count": counted_passengers,
        "seed": seed,
        "groups": len(groups),
        "max_group_size": max(len(group["psrs"]) for group in groups),
        "ssr_counts": dict(sorted(ssr_counts.items())),
        "fixed_seats": fixed_count,
        "assigned": len(assignments),
        "unassigned": unassigned,
        "violations": violations,
        "cabin_mismatches": cabin_mismatches,
        "same_row_groups": same_row_groups,
        "average_row_span": round(average_row_span, 3),
        "max_row_span": max_row_span,
        "combined_score": round(combined_score, 3),
        "elapsed": round(elapsed, 3),
    }


def main() -> None:
    config = load_json(PROJECT_ROOT / "config.json")
    old_path = PROJECT_ROOT / config["data"]["oldseatmap"]
    new_path = PROJECT_ROOT / config["data"]["seatmap"]
    old_data = load_json(old_path)
    new_data = load_json(new_path)
    old_seats = old_data["seats"]
    new_seats = new_data["seats"]
    old_topology = datagenerate.SeatTopology(old_seats)
    new_topology = datagenerate.SeatTopology(new_seats)

    data_dir = PROJECT_ROOT / "data" / "generator_v3_2"
    output_dir = (
        PROJECT_ROOT / "outputs" / "benchmarks" / "batch_visualizations"
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    visualize.configure_fonts()

    reports: List[Dict[str, Any]] = []
    for passengers in POPULATIONS:
        for difficulty in DIFFICULTIES:
            groups, fixed_count, seed = generate_case(
                passengers,
                difficulty,
                old_topology,
                new_topology,
            )
            traveler_count = datagenerate.passenger_count(groups)
            counted_passengers = datagenerate.target_seat_demand(groups)
            passenger_capacity = datagenerate.transferable_seat_capacity(
                old_topology, new_topology
            )
            size_label = datagenerate.test_case_size_label(
                passengers,
                counted_passengers,
                passenger_capacity,
            )
            stem = f"{size_label}_{difficulty}"
            data_path = data_dir / f"{stem}_groups.json"
            data_path.write_text(
                json.dumps(
                    {
                        "generatorVersion": datagenerate.GENERATOR_VERSION,
                        "requestedPassengerCount": passengers,
                        "passengerCount": counted_passengers,
                        "travelerCount": traveler_count,
                        "passengerCapacity": passenger_capacity,
                        "sizeLabel": size_label,
                        "targetSeatDemand": counted_passengers,
                        "generatorDiagnostics": (
                            datagenerate.generator_diagnostics(
                                groups, old_topology,
                                datagenerate.SCENARIOS[difficulty],
                            )
                        ),
                        "groups": groups,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if counted_passengers != passengers:
                print(
                    f"容量调整：请求 {passengers} 人，实际计入 "
                    f"{counted_passengers} 人，真实旅客 "
                    f"{traveler_count} 人"
                )
            print(f"已生成：{data_path}")

            report = run_case(
                traveler_count,
                counted_passengers,
                size_label,
                difficulty,
                groups,
                fixed_count,
                seed,
                old_seats,
                new_seats,
                config,
                output_dir / f"{stem}_result.png",
            )
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False))

    print("\n批量测试完成：")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
