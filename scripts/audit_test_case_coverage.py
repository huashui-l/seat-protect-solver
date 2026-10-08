#!/usr/bin/env python3
"""Inventory frozen test inputs without running or changing any solver/corpus.

Run from any directory with Python 3.10+: python scripts/audit_test_case_coverage.py
This measures input coverage, not feasibility, optimality, or runtime.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.general_validation_case_factory import materialize_cases

FORMAL = "data/generator_v3_2/d3_official_24cases_2026-09-07/manifest.json"
D4 = "data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/manifest.json"
CG30 = "docs/provenance/cpp5-cg30/manifest.json"
CABINS = {"F": "First", "A": "First", "C": "Business", "J": "Business",
          "W": "PremiumEconomy", "P": "PremiumEconomy", "Y": "Economy", "E": "Economy"}


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def protection(p):
    rules = p.get("mandatoryRule") or {}
    return 2 * (rules.get("needBothSideEmpty") == "Y") + (rules.get("needSingleSideEmpty") == "Y")


def summarize(rows):
    hist = Counter()
    for row in rows:
        hist.update(row["pnr_size_histogram"])
    fields = ["travelers", "pnrs", "max_pnr", "resource_demand", "target_resource_load",
              "ssr_ratio", "fixed_ratio"]
    return {
        "case_count": len(rows),
        "ranges": {k: {"min": min(r[k] for r in rows), "max": max(r[k] for r in rows)} for k in fields},
        "pnr_size_histogram": dict(sorted(hist.items(), key=lambda item: int(item[0]))),
        "cases_with_pnr_size_10": sum(r["pnr_size_histogram"].get("10", 0) > 0 for r in rows),
        "cases_with_all_seven_ssrs": sum(len(r["ssr_counts"]) == 7 for r in rows),
        "cases_with_fixed_protected_traveler": sum(r["fixed_protected_travelers"] > 0 for r in rows),
        "cases_with_target_load_at_least_90pct": sum(r["target_resource_load"] >= .9 for r in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/provenance/test-case-coverage.json")
    parser.add_argument("--compare-ref", default="main")
    args = parser.parse_args()
    manifests = {name: read_json(path) for name, path in [("Formal24", FORMAL), ("D4", D4), ("CG30", CG30)]}
    cgids = {c["case_id"] for c in manifests["CG30"]["cases"]}
    maps = {s["layout"]: s["relative_path"] for s in manifests["D4"]["seatmaps"]}
    maps.update({"3-3": "data/3-3seatmap.json", "3-4-3": "data/3-4-3seatmap.json"})
    seatmaps = {layout: read_json(path) for layout, path in maps.items()}
    rows = []
    compared = {}
    for suite in ("Formal24", "D4"):
        for item in manifests[suite]["cases"]:
            path = item["relative_path"]
            raw = (ROOT / path).read_bytes()
            data = json.loads(raw)
            compared[path] = json.loads(git("show", f"{args.compare_ref}:{path}")) == data
            old_layout = item.get("old_layout", "3-4-3" if item["direction"] == "forward" else "3-3")
            new_layout = item.get("new_layout", "3-3" if item["direction"] == "forward" else "3-4-3")
            passengers = [p for g in data["groups"] for p in g["psrs"]]
            sizes = Counter(str(len(g["psrs"])) for g in data["groups"])
            ssrs = Counter(p["ssr"] for p in passengers if p.get("ssr"))
            fixed = [p for p in passengers if p.get("newSeat", {}).get("seatNum")]
            cabin_n, cabin_q = Counter(), Counter()
            for p in passengers:
                cabin = CABINS.get(p["cabin"], p["cabin"])
                cabin_n[cabin] += 1
                cabin_q[cabin] += 1 + protection(p)
            old_caps = Counter(s["seatClass"] for s in seatmaps[old_layout]["seats"])
            new_caps = Counter(s["seatClass"] for s in seatmaps[new_layout]["seats"])
            n = len(passengers)
            q = n + sum(protection(p) for p in passengers)
            capacity = sum(new_caps.values())
            metadata_matches = {"travelerCount": n == data["travelerCount"],
                                "targetSeatDemand": q == data["targetSeatDemand"],
                                "fixed_count": len(fixed) == data.get("preassignedCount", data.get("fixedSeatCount"))}
            if not all(metadata_matches.values()):
                raise ValueError(f"Input metadata mismatch: {item['case_id']}: {metadata_matches}")
            rows.append({
                "case_id": item["case_id"], "suite": suite, "in_cg30": item["case_id"] in cgids,
                "path": path, "sha256": sha(raw), "old_layout": old_layout, "new_layout": new_layout,
                "label": data.get("scenario", data.get("difficultyLevel")),
                "size_label": data["sizeLabel"], "seed": data.get("seedProtocol", data.get("seed")),
                "travelers": n, "pnrs": len(data["groups"]), "max_pnr": max(map(int, sizes)),
                "pnr_size_histogram": dict(sorted(sizes.items(), key=lambda item: int(item[0]))),
                "protected_seats": q - n, "resource_demand": q,
                "source_capacity": sum(old_caps.values()), "target_capacity": capacity,
                "target_traveler_load": n / capacity, "target_resource_load": q / capacity,
                "cabin_stats": {c: {"travelers": cabin_n[c], "resource_demand": cabin_q[c],
                                    "source_capacity": old_caps[c], "target_capacity": new_caps[c],
                                    "target_resource_load": cabin_q[c] / new_caps[c] if new_caps[c] else None}
                                for c in sorted(old_caps.keys() | new_caps.keys() | cabin_q.keys())},
                "ssr_counts": dict(sorted(ssrs.items())), "ssr_ratio": sum(ssrs.values()) / n,
                "fixed_travelers": len(fixed), "fixed_ratio": len(fixed) / n,
                "fixed_protected_travelers": sum(protection(p) > 0 for p in fixed),
                "care_travelers": sum(p.get("needCared") == "Y" for p in passengers),
                "same_row_isolation": sum(p.get("mandatoryRule", {}).get("sameRowNoOtherSSR") == "Y" for p in passengers),
                "same_subrow_isolation": sum(p.get("mandatoryRule", {}).get("sameSubRowNoOtherSSR") == "Y" for p in passengers),
            })
    for layout, path in maps.items():
        compared[path] = read_json(path) == json.loads(git("show", f"{args.compare_ref}:{path}"))
    hashes = {}
    for suite, manifest in manifests.items():
        checks = []
        for item in manifest["cases"] + manifest.get("supporting_files", []) + manifest.get("seatmaps", []):
            raw = (ROOT / item["relative_path"]).read_bytes()
            lf = raw.replace(b"\r\n", b"\n")
            checks.append({"path": item["relative_path"], "raw_matches": sha(raw) == item["sha256"],
                           "lf_matches": sha(lf) == item["sha256"],
                           "crlf_matches": sha(lf.replace(b"\n", b"\r\n")) == item["sha256"]})
        hashes[suite] = checks
    transitions = defaultdict(list)
    for row in rows:
        transitions[f"{row['old_layout']} -> {row['new_layout']}"].append(row)
    micro = []
    for case in materialize_cases():
        n = sum(len(g["psrs"]) for g in case["groupsData"])
        micro.append({"case_id": case["id"], "expected_valid": case["expectedValid"], "travelers": n,
                      "pnrs": len(case["groupsData"]), "tags": case["tags"],
                      "stored_reference_assignments": len(case["referenceAssignments"]),
                      "has_complete_stored_reference": len(case["referenceAssignments"]) == n,
                      "current_heuristic_completion_required": case.get("expectedCurrentHeuristicComplete", True)})
    result = {
        "schema_version": 1, "scope": "Input coverage audit; not a solver benchmark or feasibility certificate",
        "checkout_head": git("rev-parse", "HEAD").decode().strip(),
        "comparison_ref": args.compare_ref, "comparison_commit": git("rev-parse", args.compare_ref).decode().strip(),
        "json_equal_to_comparison_ref": compared,
        "summary": {"all40": summarize(rows), "CG30": summarize([r for r in rows if r["in_cg30"]]),
                    **{name: summarize([r for r in rows if r["suite"] == name]) for name in ("Formal24", "D4")}},
        "transitions": {key: summarize(value) for key, value in transitions.items()},
        "cases": rows, "micro_cases": micro, "manifest_hash_checks": hashes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": result["summary"],
                      "micro_count": len(micro), "all_compared_json_equal": all(compared.values())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
