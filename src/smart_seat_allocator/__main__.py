"""Run the independent heuristic on one input instance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .solver import solve


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description="Model-driven anytime seat allocation")
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--old-seatmap", type=Path, required=True)
    parser.add_argument("--new-seatmap", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "config.json",
    )
    parser.add_argument("--time-limit", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    cfg = read(args.config)
    raw = read(args.groups)
    groups = raw["groups"] if isinstance(raw, dict) else raw
    old, new = read(args.old_seatmap), read(args.new_seatmap)
    old = old["seats"] if isinstance(old, dict) else old
    new = new["seats"] if isinstance(new, dict) else new

    def progress(point):
        print(
            f"{point['seconds']:.3f}s  score={point['score']:.6f}  {point['phase']}",
            file=sys.stderr,
            flush=True,
        )

    result = solve(
        new,
        old,
        groups,
        cfg["weights"],
        cfg,
        time_limit=args.time_limit,
        workers=args.workers,
        seed=args.seed,
        on_improvement=progress if args.progress else None,
    )
    serial = {
        **result,
        "assigned_seats": {
            f"{g},{p}": s for (g, p), s in result["assigned_seats"].items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(serial, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in serial.items()
                if k
                in (
                    "status",
                    "total_score",
                    "hard_violations",
                    "unassigned",
                    "wall_seconds",
                    "upper_bound",
                    "proven_optimal",
                    "absolute_gap",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
