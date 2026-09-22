#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受控测试单个同行组人数（5至10人）与求解时间的关系。"""

from __future__ import annotations

import argparse
import copy
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from . import exact_column_generation
except ImportError:
    import exact_column_generation


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GROUP_SIZES = tuple(range(5, 11))
OLD_SEATS = (
    "14A", "14B", "14C", "14D", "14E",
    "14F", "15A", "15B", "15C", "15D",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _old_seat_value(seat: Dict[str, Any]) -> str:
    if seat.get("isWindow"):
        return "W"
    if seat.get("isAisle"):
        return "A"
    return ""


def build_case(size: int, old_seats_data: List[dict]) -> Dict[str, Any]:
    """构造嵌套、无特殊规则的单同行组算例。"""
    if size not in GROUP_SIZES:
        raise ValueError(f"group size must be one of {GROUP_SIZES}: {size}")
    seat_by_id = {str(seat["seatId"]): seat for seat in old_seats_data}
    missing = [seat_id for seat_id in OLD_SEATS[:size] if seat_id not in seat_by_id]
    if missing:
        raise ValueError(f"old seatmap is missing controlled seats: {missing}")
    passengers = []
    for hostnum, seat_id in enumerate(OLD_SEATS[:size], start=1):
        passengers.append({
            "hostnum": hostnum,
            "cabin": "Y",
            "oldSeat": {
                "seatNum": seat_id,
                "seatValue": _old_seat_value(seat_by_id[seat_id]),
            },
            "needCared": "N",
            "ssr": "",
            "mandatoryRule": {
                "needBothSideEmpty": "N",
                "needSingleSideEmpty": "N",
                "sameSubRowNoOtherSSR": "N",
                "sameRowNoOtherSSR": "N",
            },
            "optionRule": [],
        })
    return {
        "experiment": "controlled_single_group_size_scaling",
        "groupSize": size,
        "travelerCount": size,
        "targetSeatDemand": size,
        "controlledVariables": {
            "groups": 1,
            "ssrPassengers": 0,
            "fixedPassengers": 0,
            "protectedEmptySeats": 0,
            "nestedSample": True,
        },
        "groups": [{
            "groupId": 1,
            "groupType": "communityType",
            "PNR": f"SIZE{size:02d}",
            "psrs": passengers,
        }],
    }


def generate_cases(data_dir: Path, old_seats_data: List[dict]) -> List[Path]:
    paths = []
    for size in GROUP_SIZES:
        path = data_dir / f"group_size_scaling_{size}.json"
        _write_json(path, build_case(size, old_seats_data))
        paths.append(path)
    return paths


def _selected_sizes(value: str | None) -> Iterable[int]:
    if not value:
        return GROUP_SIZES
    sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    unknown = sorted(set(sizes) - set(GROUP_SIZES))
    if unknown:
        raise ValueError(f"unsupported group sizes: {unknown}")
    return sizes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--old-seatmap", type=Path, default=Path("data/3-3seatmap.json"))
    parser.add_argument("--new-seatmap", type=Path, default=Path("data/3-4-3seatmap.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data/group_size_scaling"))
    parser.add_argument("--sizes", help="逗号分隔，例如5,6,7；默认5至10")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--total-time-limit", type=float, default=3600.0)
    parser.add_argument("--mp2-time-limit", type=float, default=120.0)
    parser.add_argument("--pricing-workers", type=int, default=1)
    parser.add_argument("--restricted-mip-time-limit", type=float, default=30.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/benchmarks/group_size_scaling_5_to_10.json"),
    )
    parser.add_argument(
        "--details-dir",
        type=Path,
        default=Path("outputs/benchmarks/group_size_scaling_details"),
    )
    args = parser.parse_args()

    config_path = _resolve(args.config)
    old_path = _resolve(args.old_seatmap)
    new_path = _resolve(args.new_seatmap)
    data_dir = _resolve(args.data_dir)
    output_path = _resolve(args.output)
    details_dir = _resolve(args.details_dir)
    config = _read_json(config_path)
    old_seats = _read_json(old_path)["seats"]
    new_seats = _read_json(new_path)["seats"]
    paths = generate_cases(data_dir, old_seats)
    for path in paths:
        print(f"data ready: {path}", flush=True)
    if args.generate_only:
        return

    experiment = {
        "oldSeatmap": str(old_path.relative_to(PROJECT_ROOT)),
        "newSeatmap": str(new_path.relative_to(PROJECT_ROOT)),
        "design": "one ordinary group; nested passengers; no SSR/fixed/protection",
        "pricingWorkers": args.pricing_workers,
    }
    if output_path.exists() and not args.restart:
        report = _read_json(output_path)
        if report.get("experiment") != experiment:
            raise ValueError("existing output belongs to a different experiment")
    else:
        report = {
            "startedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "experiment": experiment,
            "cases": [],
        }
    completed = {
        int(row["groupSize"])
        for row in report["cases"]
        if row.get("status") == "completed"
    }
    selected = tuple(_selected_sizes(args.sizes))
    for index, size in enumerate(selected, start=1):
        if size in completed:
            print(f"[{index}/{len(selected)}] size={size}: completed, skip", flush=True)
            continue
        payload = _read_json(data_dir / f"group_size_scaling_{size}.json")
        run_config = copy.deepcopy(config)
        column = run_config.setdefault("column_generation", {})
        column.update({
            "mp1_node_limit": 1,
            "mp1_dive_max_nodes": 0,
            "total_time_limit": args.total_time_limit,
            "mp2_time_limit": args.mp2_time_limit,
            "pricing_workers": args.pricing_workers,
            "restricted_mip_time_limit": args.restricted_mip_time_limit,
            "show_progress": True,
        })
        print(f"[{index}/{len(selected)}] size={size}: solving", flush=True)
        started = time.perf_counter()
        try:
            result, _ = exact_column_generation.run_column_generation(
                new_seats, old_seats, payload["groups"], run_config["weights"], run_config
            )
            elapsed = time.perf_counter() - started
            diagnostics = result["diagnostics"]
            pricing = diagnostics.get("pricing", {})
            warm = diagnostics.get("heuristic_warm_start", {})
            detail_path = details_dir / f"group_size_{size}.json"
            _write_json(detail_path, exact_column_generation._json_safe(result))
            row = {
                "groupSize": size,
                "status": "completed",
                "heuristicSeconds": warm.get("elapsed_seconds"),
                "heuristicScore": warm.get("score_without_time_penalty") if warm.get("accepted") else None,
                "totalSeconds": elapsed,
                "termination": diagnostics.get("termination"),
                "exactLpCertified": bool(diagnostics.get("complete_column_space_lp_bound_certified")),
                "safeLpCertified": bool(diagnostics.get("safe_column_space_lp_bound_certified")),
                "lpUpperBound": diagnostics.get("complete_column_space_lp_bound"),
                "pricingCalls": pricing.get("calls"),
                "exactPricingCalls": pricing.get("exact_calls"),
                "pricingNodes": pricing.get("nodes"),
                "dfsNodes": pricing.get("dfs_nodes"),
                "patternsAdded": pricing.get("patterns_added"),
                "mp2Timeouts": (pricing.get("termination_counts") or {}).get("mp2_time_limit", 0),
                "detailFile": _display_path(detail_path),
            }
        except Exception as exc:
            row = {
                "groupSize": size,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "totalSeconds": time.perf_counter() - started,
            }
        report["cases"] = [row0 for row0 in report["cases"] if int(row0["groupSize"]) != size]
        report["cases"].append(row)
        report["cases"].sort(key=lambda item: int(item["groupSize"]))
        report["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _write_json(output_path, report)
        print(
            f"[{index}/{len(selected)}] size={size}: status={row['status']}, "
            f"seconds={row['totalSeconds']:.3f}, termination={row.get('termination')}",
            flush=True,
        )
    report["finishedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _write_json(output_path, report)
    print(f"results: {output_path}", flush=True)


if __name__ == "__main__":
    main()
