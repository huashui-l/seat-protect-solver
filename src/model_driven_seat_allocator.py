"""File-loadable bridge for the existing visualizer/evaluation interface."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import allocation_evaluator as evaluator
from src.smart_seat_allocator import run_allocation as _allocate


def run_allocation(new_seats_data, old_seats_data, groups_data, weights, config):
    started = time.perf_counter()
    result, _ = _allocate(new_seats_data, old_seats_data, groups_data, weights, config)
    if result["status"] != "complete":
        raise RuntimeError(
            f"New allocator did not produce a complete solution: {result['status']}"
        )
    blocked = {}
    if result["status"] == "complete":
        topology = evaluator.SeatTopology(new_seats_data, config)
        assignment = result["assigned_seats"]
        occupied = set(assignment.values())
        demands = []
        cross = bool(
            config.get("mandatory_rules", {}).get(
                "both_side_empty_allow_cross_aisle", False
            )
        )
        for group in groups_data:
            for p in group["psrs"]:
                key = (group["groupId"], p["hostnum"])
                seat = assignment[key]
                mandatory = p.get("mandatoryRule") or {}
                if mandatory.get("needBothSideEmpty") == "Y":
                    for neighbor in topology.row_neighbors(
                        seat, allow_cross_aisle=cross
                    ):
                        blocked[neighbor] = [key]
                if mandatory.get("needSingleSideEmpty") == "Y":
                    demands.append((key, topology.subrow_neighbors(seat)))
        owner = {}

        def augment(index, seen):
            for seat in demands[index][1]:
                if seat in occupied or seat in blocked or seat in seen:
                    continue
                seen.add(seat)
                if seat not in owner or augment(owner[seat], seen):
                    owner[seat] = index
                    return True
            return False

        for i in range(len(demands)):
            if not augment(i, set()):
                raise RuntimeError(
                    "Failed to recover independently validated protection"
                )
        blocked.update({seat: [demands[i][0]] for seat, i in owner.items()})
    result["blocked_by"] = blocked
    elapsed = time.perf_counter() - started
    result["wall_seconds"] = elapsed
    return result, elapsed
