#!/usr/bin/env python3
"""Compare complete, paired development batches without selecting individual rows."""

from __future__ import annotations

import hashlib
import json
import statistics
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "cache60-dev": "A：几何缓存，保留完整搜索",
    "cp-only60-dev": "B：几何缓存 + 单后端 + 短重组",
    "lean60-dev": "C：进一步取消模式扩充、变形和重复重组",
}


def read(path):
    return json.loads(path.read_text())


def compare(rows, reference):
    pairs = [r for r in rows if r["valid"] and reference[r["case_id"]]["valid"]]
    deltas = [r["score"] - reference[r["case_id"]]["score"] for r in pairs]
    return {
        "comparable": len(pairs),
        "wins": sum(d > 1e-6 for d in deltas),
        "ties": sum(abs(d) <= 1e-6 for d in deltas),
        "losses": sum(d < -1e-6 for d in deltas),
        "sum_delta": sum(deltas),
        "mean_reduction": statistics.mean(
            100 * d / max(1, abs(reference[r["case_id"]]["score"]))
            for r, d in zip(pairs, deltas)
        )
        if pairs
        else None,
    }


def main():
    work = ROOT / "outputs/smart"
    dest = ROOT / "docs/provenance/smart-heuristic"
    expected = set(read(work / "focused-development-design.json")["targeted_cases"])
    base = {r["case_id"]: r for r in read(work / "guarded60/summary.json")["rows"]}
    data = {
        "budget": 60,
        "case_selection": "Six V2 development cases selected before the trials.",
        "limitations": "One seed per variant; timing and parallel search vary. B also changes recombination time allocation, so it is not a pure backend-only causal test. Development cases are not a holdout set.",
        "geometry_equivalence": read(work / "geometry-cache-equivalence.json"),
        "variants": [],
    }
    snapshots = dest / "development-source-snapshots.zip"
    with zipfile.ZipFile(snapshots, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, title in RUNS.items():
            directory = work / name
            summary = read(directory / "summary.json")
            rows = summary["rows"]
            if len(rows) != len(expected) or {r["case_id"] for r in rows} != expected:
                raise ValueError(f"Incomplete development batch: {name}")
            for row in rows:
                old = base[row["case_id"]]
                if row["budget"] != 60 or any(
                    row[key] != old[key]
                    for key in (
                        "input_sha256",
                        "old_seatmap_sha256",
                        "new_seatmap_sha256",
                    )
                ):
                    raise ValueError(f"Unpaired input or budget: {name}")
            for relative, digest in summary["source_sha256"].items():
                path = directory / "source_snapshot" / relative
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Changed source: {path}")
                archive.write(path, f"{name}/{relative}")
            data["variants"].append(
                {
                    "run": name,
                    "title": title,
                    "count": len(rows),
                    "valid": sum(r["valid"] for r in rows),
                    "over_budget": sum(r["seconds"] > r["budget"] for r in rows),
                    "max_seconds": max(r["seconds"] for r in rows),
                    "comparison": compare(rows, base),
                    "rows": rows,
                    "source_sha256": summary["source_sha256"],
                    "summary_sha256": hashlib.sha256(
                        (directory / "summary.json").read_bytes()
                    ).hexdigest(),
                }
            )
    data["source_archive"] = {
        "file": snapshots.name,
        "sha256": hashlib.sha256(snapshots.read_bytes()).hexdigest(),
    }
    output = dest / "ablation-study.json"
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    for variant in data["variants"]:
        print(variant["run"], variant["comparison"])
    print(output)


if __name__ == "__main__":
    main()
