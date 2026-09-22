"""Replay one group-pricing problem on a saved, identical RMP dual vector."""

from __future__ import annotations

import argparse
import copy
import json
import math
import time
from pathlib import Path

import allocation_evaluator as evaluator
from exact_column_generation import (
    _baby_pairs,
    _build_group_pricing_cache,
    _deserialize_master_duals,
    _master_column_reduced_cost,
    _pattern_from_placements,
    _price_group_complete_domain_mip,
    _price_group_exact_dfs,
    _preprocess_fixed_seats,
    _skeleton_assignment_lower_bound,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="在完全相同的RMP对偶下对单组定价策略做可复现A/B"
    )
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--group-id", type=int, required=True)
    parser.add_argument(
        "--groups", type=Path,
        default=PROJECT_ROOT / "data/150_stress_groups.json",
    )
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.json")
    parser.add_argument(
        "--old-seatmap", type=Path,
        default=PROJECT_ROOT / "data/3-4-3seatmap.json",
    )
    parser.add_argument(
        "--new-seatmap", type=Path,
        default=PROJECT_ROOT / "data/3-3seatmap.json",
    )
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--node-limit", type=int, default=5_000_000)
    parser.add_argument("--bound-only", action="store_true")
    parser.add_argument(
        "--top-k-windows", type=int, default=0,
        help="对大型骨架空间精确细化下界最小的前K个行列窗口",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    detail = _load(args.detail.resolve())
    snapshot = detail.get("diagnostics", {}).get("final_root_pricing_snapshot")
    if not snapshot:
        raise SystemExit(
            "结果文件没有final_root_pricing_snapshot；"
            "请用当前代码先运行一次算例"
        )
    duals = _deserialize_master_duals(snapshot["duals"])
    config = _load(args.config.resolve())
    groups = _load(args.groups.resolve())["groups"]
    group = next(
        item for item in groups if int(item["groupId"]) == args.group_id
    )
    new_seats = _load(args.new_seatmap.resolve())["seats"]
    old_seats = _load(args.old_seatmap.resolve())["seats"]
    topology = evaluator.SeatTopology(new_seats, config)
    old_topology = evaluator.SeatTopology(old_seats, config)
    fixed_context = _preprocess_fixed_seats(groups, topology, config)
    baby_cost = _baby_pairs(
        topology, groups, config.get("weights", {}), config
    )
    cache = _build_group_pricing_cache(
        group,
        topology,
        old_topology,
        config.get("weights", {}),
        config,
        fixed_context,
    )

    results = []
    variants = () if args.bound_only else (
        ("dfs", "dfs"),
        ("highs", "mip"),
    )
    for name, method in variants:
        variant_config = copy.deepcopy(config)
        pricing = variant_config.setdefault("column_generation", {})
        pricing["mp2_time_limit"] = float(args.seconds)
        pricing["dfs_exact_time_limit"] = float(args.seconds)
        pricing["dfs_large_group_exact_time_limit"] = float(args.seconds)
        pricing["dfs_node_limit"] = int(args.node_limit)
        pricing["dfs_large_group_node_limit"] = int(args.node_limit)
        started = time.perf_counter()
        if method == "dfs":
            result = _price_group_exact_dfs(
                group,
                topology,
                variant_config.get("weights", {}),
                variant_config,
                duals,
                baby_cost,
                None,
                None,
                None,
                cache,
                exact=True,
                phase_one=False,
                stop_on_negative=False,
            )
        else:
            result = _price_group_complete_domain_mip(
                group,
                topology,
                old_topology,
                variant_config.get("weights", {}),
                variant_config,
                duals,
                baby_cost,
                pricing_cache=cache,
                exact=True,
                phase_one=False,
                stop_on_negative=False,
            )
        results.append({
            "variant": name,
            "seconds": time.perf_counter() - started,
            "termination": result.termination,
            "proven_optimal": result.proven_optimal,
            "reduced_cost": (
                result.reduced_cost
                if math.isfinite(result.reduced_cost) else None
            ),
            "strict_lower_bound": (
                result.reduced_cost_lower_bound
                if math.isfinite(result.reduced_cost_lower_bound) else None
            ),
            "nodes": result.nodes,
        })

    matching_stats = _skeleton_assignment_lower_bound(
        group, cache, config.get("weights", {}), config, duals, baby_cost,
        top_k_window_skeletons=max(0, args.top_k_windows),
    )
    if matching_stats is not None:
        lower_bound = matching_stats["lower_bound"]
        candidate_placements = matching_stats.get("candidate_placements")
        candidate_reduced_cost = None
        if candidate_placements:
            candidate_pattern = _pattern_from_placements(
                group, candidate_placements, topology,
                config.get("weights", {}), config, baby_cost,
            )
            candidate_reduced_cost = _master_column_reduced_cost(
                candidate_pattern, duals, baby_cost, phase_one=False
            )
        results.append({
            "variant": "skeleton_assignment_bound",
            "termination": "relaxation_complete",
            "proven_optimal": False,
            "reduced_cost": None,
            "strict_lower_bound": lower_bound,
            "nodes": matching_stats.get("combinations", 0),
            "candidate_available": bool(candidate_placements),
            "candidate_reduced_cost": candidate_reduced_cost,
            **{
                key: value for key, value in matching_stats.items()
                if key not in {"lower_bound", "candidate_placements"}
            },
        })

    payload = {
        "detail": str(args.detail.resolve()),
        "group_id": args.group_id,
        "restricted_master_cost": snapshot.get("restricted_master_cost"),
        "time_limit": args.seconds,
        "node_limit": args.node_limit,
        "results": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
