"""CLI: ``python -m src.direct_gurobi``."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .solver import solve


def _clean(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Gurobi direct passenger-seat solver")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--old-seatmap", type=Path, required=True)
    parser.add_argument("--new-seatmap", type=Path, required=True)
    parser.add_argument("--time-limit", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.quiet:
        config.setdefault("direct_gurobi", {})["log"] = False
    new = json.loads(args.new_seatmap.read_text(encoding="utf-8-sig"))["seats"]
    old = json.loads(args.old_seatmap.read_text(encoding="utf-8-sig"))["seats"]
    groups = json.loads(args.groups.read_text(encoding="utf-8-sig"))["groups"]
    result = _clean(solve(new, old, groups, config.get("weights", {}), config, time_limit=args.time_limit))
    serializable = dict(result)
    serializable["assigned_seats"] = {
        f"{key[0]}:{key[1]}": value for key, value in result["assigned_seats"].items()
    }
    summary = {k: v for k, v in serializable.items()
               if k not in ("assigned_seats", "score_detail", "violation_detail")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
