"""命令行入口：python -m src.cg_gurobi --groups case.json --old-seatmap old.json --new-seatmap new.json"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .solver import solve


def main() -> None:
    parser = argparse.ArgumentParser(description="Gurobi 直接定价列生成")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--old-seatmap", type=Path, required=True)
    parser.add_argument("--new-seatmap", type=Path, required=True)
    parser.add_argument("--time-limit", type=float)
    parser.add_argument("--pricing-time-limit", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    cg = config.setdefault("cg_gurobi", {})
    if args.time_limit is not None:
        cg["total_time_limit"] = args.time_limit
    if args.pricing_time_limit is not None:
        cg["pricing_time_limit"] = args.pricing_time_limit
    if args.quiet:
        cg["log"] = False
    new = json.loads(args.new_seatmap.read_text(encoding="utf-8-sig"))["seats"]
    old = json.loads(args.old_seatmap.read_text(encoding="utf-8-sig"))["seats"]
    groups = json.loads(args.groups.read_text(encoding="utf-8-sig"))["groups"]
    result = solve(new, old, groups, config.get("weights", {}), config)
    summary = {k: v for k, v in result.items() if k not in ("assigned_seats", "score_detail", "violation_detail")}
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        serializable = dict(result)
        serializable["assigned_seats"] = {f"{k[0]}:{k[1]}": v for k, v in result["assigned_seats"].items()}
        args.output.write_text(json.dumps(serializable, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
