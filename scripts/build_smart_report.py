#!/usr/bin/env python3
"""Build a self-contained Chinese experiment report from independently audited runs."""

from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import hashlib
import json
import math
import statistics
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_KEYS = ("input_sha256", "old_seatmap_sha256", "new_seatmap_sha256")
CPP_KEYS = (
    "final_score",
    "repeat_score",
    "cleanup_run_external_score",
    "cleanup_repeat_external_score",
)
MODEL_KEYS = ("weights", "mandatory_rules", "ssr_rules", "seat_geometry", "seat_value")
OBJECTIVE_KEYS = (
    "prioritize_front",
    "front_penalty_reduction",
    "back_penalty_factor",
    "group_centroid_x_factor",
    "group_centroid_y_factor",
    "baby_front_back_factor",
)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def model_signature(config):
    return {
        **{k: config.get(k) for k in MODEL_KEYS},
        "objective_settings": {
            k: config.get("algorithm", {}).get(k) for k in OBJECTIVE_KEYS
        },
    }


def paired(a, b):
    return all(a.get(k) is not None and a.get(k) == b.get(k) for k in HASH_KEYS)


def suite(cid):
    return "formal24" if cid.startswith(("forward:", "reverse:")) else cid.split(":")[0]


def finite_json(value):
    """A missing finite restricted-pool bound is represented as null in JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_json(item) for item in value]
    return value


def render_report(data, output):
    template = (ROOT / "reports/templates/smart_heuristic.html").read_text()
    encoded = json.dumps(
        finite_json(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).replace("</", "<\\/")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__EXPERIMENT_DATA__", encoded))


def verify_audit(directory, summary, audit):
    """Bind the final audit to these exact files, including the legacy run."""
    fingerprint = audit.get("fingerprints", {}).get(str(directory), {})
    if fingerprint.get("row_count") != len(summary["rows"]):
        raise ValueError(f"Audit row coverage mismatch: {directory}")
    if (
        fingerprint.get("summary_sha256")
        != hashlib.sha256((directory / "summary.json").read_bytes()).hexdigest()
    ):
        raise ValueError(f"Summary changed after audit: {directory}")
    for name, expected in fingerprint.get("auxiliary_sha256", {}).items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Auxiliary input changed after audit: {directory}/{name}")
    for row in summary["rows"]:
        cid = row["case_id"]
        path = directory / (cid.replace(":", "_") + ".json")
        if (
            fingerprint.get("result_sha256", {}).get(cid)
            != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise ValueError(f"Result changed after audit: {directory}/{cid}")


def aggregate(rows, reference):
    comparable = [
        r for r in rows if r["valid"] and reference.get(r["case_id"]) is not None
    ]
    deltas = [r["score"] - reference[r["case_id"]] for r in comparable]
    reductions = [
        100 * delta / max(1, abs(reference[r["case_id"]]))
        for r, delta in zip(comparable, deltas)
    ]
    return {
        "count": len(rows),
        "valid": sum(r["valid"] for r in rows),
        "proven": sum(r.get("proven_optimal", False) for r in rows),
        "infeasible": sum(r["status"] == "infeasible" for r in rows),
        "unknown": sum(not r["valid"] and r["status"] != "infeasible" for r in rows),
        "comparable": len(comparable),
        "wins": sum(d > 1e-6 for d in deltas),
        "ties": sum(abs(d) <= 1e-6 for d in deltas),
        "losses": sum(d < -1e-6 for d in deltas),
        "mean_reduction": statistics.mean(reductions) if reductions else None,
        "sum_delta": sum(deltas),
        "weighted_reduction": 100
        * sum(deltas)
        / sum(max(1, abs(reference[r["case_id"]])) for r in comparable)
        if comparable
        else None,
        "median_seconds": statistics.median(r["seconds"] for r in rows)
        if rows
        else None,
        "max_seconds": max((r["seconds"] for r in rows), default=None),
        "over_budget": sum(r["seconds"] > r["budget"] for r in rows),
        "over_budget_50ms": sum(r["seconds"] > r["budget"] + 0.05 for r in rows),
    }


def compare_budgets(rows, earlier, later):
    source = {
        r["case_id"]: r
        for r in rows
        if r["budget"] == earlier and r["suite"] != "public"
    }
    target = [r for r in rows if r["budget"] == later and r["suite"] != "public"]
    result = aggregate(
        target, {cid: r["score"] for cid, r in source.items() if r["valid"]}
    )
    result.update(
        {
            "earlier": earlier,
            "later": later,
            "newly_legal": sum(
                r["valid"] and not source.get(r["case_id"], {}).get("valid", False)
                for r in target
            ),
            "lost_legal": sum(
                not r["valid"] and source.get(r["case_id"], {}).get("valid", False)
                for r in target
            ),
        }
    )
    return result


def convergence_summary(rows):
    """Describe accepted-incumbent times, not optimizer-internal events."""
    valid = [r for r in rows if r["valid"] and r["history"]]
    answer = {"count": len(valid)}
    for field in ("first_feasible", "last_improvement", "within_1pct_final"):
        values = sorted(r[field] for r in valid)
        answer[field] = {
            "median": statistics.median(values) if values else None,
            "p90": values[math.ceil(len(values) * 0.9) - 1] if values else None,
        }
    return answer


def budget_tradeoff(rows):
    """Compare each tier with the best observed score, never a claimed optimum."""
    rows = [r for r in rows if r["suite"] == "v2"]
    best_observed = {}
    for row in rows:
        if row["valid"]:
            cid = row["case_id"]
            best_observed[cid] = max(row["score"], best_observed.get(cid, -math.inf))
    comparisons = {}
    for budget in (5, 20, 60):
        selected = [r for r in rows if r["budget"] == budget]
        result = aggregate(selected, best_observed)
        result["observed_feasible_cases"] = len(best_observed)
        gaps = [
            100
            * (best_observed[r["case_id"]] - r["score"])
            / max(1, abs(best_observed[r["case_id"]]))
            for r in selected
            if r["valid"]
        ]
        result["within_1pct_cases"] = sum(gap <= 1 + 1e-9 for gap in gaps)
        result["mean_gap_pct"] = statistics.mean(gaps) if gaps else None
        result["worst_gap_pct"] = max(gaps, default=None)
        comparisons[str(budget)] = result
    return comparisons


def observed_quality(rows, focused, baseline):
    """Post-hoc V2 reference from final batches only; not an optimality bound."""
    groups = [
        (
            f"新算法初版 {budget} 秒",
            [r for r in rows if r["suite"] == "v2" and r["budget"] == budget],
        )
        for budget in (5, 20, 60)
    ]
    if focused:
        groups.append((f"新算法缓存版 {focused['budget']:g} 秒", focused["rows"]))
    reference = {}
    for _, selected in groups:
        for row in selected:
            if not row["valid"]:
                continue
            cid = row["case_id"]
            values = [row["score"], reference.get(cid, -math.inf)]
            values.extend(
                value
                for key in ("legacy", "cpp_v2")
                if (value := baseline.get(cid, {}).get(key)) is not None
            )
            reference[cid] = max(values)
    summaries = []
    for name, selected in groups:
        gaps = [
            100
            * (reference[r["case_id"]] - r["score"])
            / max(1, abs(reference[r["case_id"]]))
            for r in selected
            if r["valid"]
        ]
        summaries.append(
            {
                "name": name,
                "tested": len(selected),
                "valid": len(gaps),
                "reference_cases": len(reference),
                "within_1pct": sum(g <= 1 + 1e-9 for g in gaps),
                "mean_gap_pct": statistics.mean(gaps) if gaps else None,
                "worst_gap_pct": max(gaps, default=None),
            }
        )
    return {"reference_scores": reference, "summaries": summaries}


def result_item(row, raw, directory):
    item = {
        **row,
        "suite": suite(row["case_id"]),
        "history": raw.get("history", []),
        "parts": raw.get("score_detail", {}),
        "stages": raw.get("stages", []),
        "operator_patterns": dict(raw.get("operator_patterns", {})),
        "status": raw.get("status", "error"),
        "directory": str(directory),
        "version": "foundation",
    }
    for cabin in raw.get("cabin_runs", {}).values():
        for operator, count in cabin.get("operator_patterns", {}).items():
            item["operator_patterns"][operator] = (
                item["operator_patterns"].get(operator, 0) + count
            )
    history = item["history"]
    item["first_feasible"] = history[0]["seconds"] if history else None
    item["last_improvement"] = history[-1]["seconds"] if history else None
    item["within_1pct_final"] = (
        next(
            (
                point["seconds"]
                for point in history
                if row["score"] - point["score"]
                <= 0.01 * max(1, abs(row["score"])) + 1e-9
            ),
            None,
        )
        if row["valid"]
        else None
    )
    return item


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--runs",
        nargs="+",
        default=[
            "outputs/smart/bounded5",
            "outputs/smart/bounded20",
            "outputs/smart/guarded60",
            "outputs/smart/bounded_public5",
        ],
    )
    ap.add_argument("--legacy", type=Path, default=Path("outputs/smart/legacy5"))
    ap.add_argument(
        "--focused",
        type=Path,
        help="One complete improved V2 batch; foundation rows remain unchanged",
    )
    ap.add_argument(
        "--study", type=Path, help="Selection rationale and iteration record JSON"
    )
    ap.add_argument(
        "--cpp-v2",
        type=Path,
        help="Optional same-source historical C++ Mac V2 rebenchmark",
    )
    ap.add_argument(
        "--audit", type=Path, default=Path("outputs/smart/final-audit.json")
    )
    ap.add_argument(
        "--output", type=Path, default=Path("reports/smart-heuristic-experiments.html")
    )
    ap.add_argument(
        "--provenance", type=Path, default=Path("docs/provenance/smart-heuristic")
    )
    ap.add_argument(
        "--historical-config",
        type=Path,
        default=ROOT / "docs/provenance/smart-heuristic/historical_cpp_config.json",
    )
    ap.add_argument(
        "--version-manifest",
        type=Path,
        default=ROOT / "docs/provenance/smart-heuristic/solver-versions.json",
        help="Expected per-tier source hashes; permits explicitly recorded tier variants",
    )
    ap.add_argument(
        "--from-data",
        type=Path,
        help="Re-render a saved results.json without rerunning solvers or audits",
    )
    ap.add_argument("--allow-partial", action="store_true")
    args = ap.parse_args()
    if args.from_data:
        render_report(read(args.from_data), args.output)
        print(args.output.resolve())
        return
    cpp_root = ROOT / "docs/provenance/cpp5-cg30"
    with (cpp_root / "comparison.csv").open() as file:
        cpp = {r["case_id"]: r for r in csv.DictReader(file)}
    manifest = read(cpp_root / "manifest.json")
    case_hashes = {r["case_id"]: r for r in manifest["cases"]}
    maps = {r["layout"]: r["sha256"] for r in manifest["seatmaps"]}
    original = read(args.historical_config)
    original_hash = hashlib.sha256(args.historical_config.read_bytes()).hexdigest()
    cpp_provenance = read(cpp_root / "provenance.json")
    if original_hash != cpp_provenance["config_sha256"]:
        raise ValueError("Historical C++ config hash mismatch")
    baseline = {
        cid: {
            "cpp": float(r["cleanup_run_external_score"]),
            "envelope": max(float(r[k]) for k in CPP_KEYS),
            "early": float(r["baseline_score"]),
            "cg": float(r["reference_value"]),
            "cpp_seconds": float(r["cleanup_run_solve_wall_seconds"]),
            "cg_seconds": float(r["reference_budget_seconds"]),
        }
        for cid, r in cpp.items()
    }
    rows, runs, seen, source_signatures = [], {}, set(), set()
    solutions = []
    audited_summaries = {}
    for directory in map(Path, args.runs):
        if not (directory / "summary.json").exists() and args.allow_partial:
            continue
        summary = read(directory / "summary.json")
        audited_summaries[directory] = summary
        if model_signature(summary["config"]) != model_signature(original):
            raise ValueError(
                f"Mathematical objective/constraints differ from C++: {directory}"
            )
        source_signatures.add(json.dumps(summary["source_sha256"], sort_keys=True))
        runs[str(directory)] = {k: v for k, v in summary.items() if k != "rows"}
        for r in summary["rows"]:
            cid = r["case_id"]
            key = (cid, r["budget"])
            if key in seen:
                raise ValueError(f"Duplicate run, cannot cherry-pick: {key}")
            seen.add(key)
            raw = read(directory / (cid.replace(":", "_") + ".json"))
            solutions.append(
                {
                    "directory": str(directory),
                    **r,
                    "assigned_seats": raw.get("assigned_seats", {}),
                }
            )
            if cid in cpp:
                case = case_hashes[cid]
                if (
                    r["input_sha256"] != case["sha256"]
                    or r["input_sha256"] != cpp[cid]["input_sha256"]
                    or r["old_seatmap_sha256"] != maps[case["old_layout"]]
                    or r["new_seatmap_sha256"] != maps[case["new_layout"]]
                ):
                    raise ValueError(f"Historical input hash mismatch: {cid}")
            rows.append(result_item(r, raw, directory))
    versions = read(args.version_manifest) if args.version_manifest.exists() else {}
    if not args.allow_partial:
        if len(source_signatures) > 1 and not versions:
            raise ValueError("Different tier sources require a version manifest")
        if versions:
            for directory, summary in audited_summaries.items():
                for row in summary["rows"]:
                    expected = versions.get(str(int(row["budget"])), {})
                    if expected.get("source_sha256") != summary["source_sha256"]:
                        raise ValueError(
                            f"Tier source differs from manifest: {directory}"
                        )
    legacy = None
    if (args.legacy / "summary.json").exists():
        legacy = read(args.legacy / "summary.json")
        audited_summaries[args.legacy] = legacy
        if model_signature(legacy["config"]) != model_signature(original):
            raise ValueError("Legacy Python mathematical settings mismatch")
        current = {r["case_id"]: r for r in rows}
        for r in legacy["rows"]:
            if r["case_id"] not in current or not paired(r, current[r["case_id"]]):
                raise ValueError(f"Legacy input mismatch: {r['case_id']}")
            b = baseline.setdefault(r["case_id"], {})
            raw = read(args.legacy / (r["case_id"].replace(":", "_") + ".json"))
            solutions.append(
                {
                    "directory": str(args.legacy),
                    **r,
                    "assigned_seats": raw.get("assigned_seats", {}),
                }
            )
            b.update(
                {
                    "legacy": r.get("score") if r["valid"] else None,
                    "legacy_valid": r["valid"],
                    "legacy_seconds": r["seconds"],
                    "legacy_status": r.get("status", "error"),
                    "legacy_error": r.get("error"),
                    "legacy_parts": {
                        key: raw.get("score_detail", {}).get(key)
                        for key in (
                            "score_s",
                            "score_v",
                            "score_p",
                            "score_c",
                            "score_b",
                            "total_soft_score",
                        )
                    }
                    if r["valid"]
                    else {},
                }
            )
    extended = {}
    for kind, directory in (("focused", args.focused), ("cpp_v2", args.cpp_v2)):
        if directory is None:
            continue
        summary = read(directory / "summary.json")
        if model_signature(summary["config"]) != model_signature(original):
            raise ValueError(f"Mathematical settings mismatch: {directory}")
        audited_summaries[directory] = summary
        selected, unique = [], set()
        foundation = {r["case_id"]: r for r in rows if r["suite"] == "v2"}
        for row in summary["rows"]:
            cid = row["case_id"]
            if (
                cid in unique
                or cid not in foundation
                or not paired(row, foundation[cid])
            ):
                raise ValueError(f"Duplicate or unmatched V2 case: {directory}/{cid}")
            unique.add(cid)
            raw = read(directory / (cid.replace(":", "_") + ".json"))
            item = result_item(row, raw, directory)
            item["version"] = kind
            selected.append(item)
            solutions.append(
                {
                    "directory": str(directory),
                    **row,
                    "assigned_seats": raw.get("assigned_seats", {}),
                }
            )
            if kind == "cpp_v2":
                baseline.setdefault(cid, {}).update(
                    {
                        "cpp_v2": row["score"] if row["valid"] else None,
                        "cpp_v2_valid": row["valid"],
                        "cpp_v2_seconds": row["seconds"],
                        "cpp_v2_process_seconds": row.get("process_seconds"),
                        "cpp_v2_status": row["status"],
                        "cpp_v2_parts": item["parts"] if row["valid"] else {},
                    }
                )
        if not args.allow_partial and len(selected) != 72:
            raise ValueError(f"Expected a complete 72-case V2 batch: {directory}")
        extended[kind] = {
            "rows": selected,
            "metadata": {k: v for k, v in summary.items() if k != "rows"},
        }
    study = read(args.study) if args.study else None
    if "focused" in extended:
        focused = extended["focused"]
        budgets = {r["budget"] for r in focused["rows"]}
        if len(budgets) != 1 or next(iter(budgets)) not in (5, 20, 60):
            raise ValueError(
                "Focused version must use exactly one original budget tier"
            )
        focus_budget = next(iter(budgets))
        if not study or study.get("selected_budget") != focus_budget:
            raise ValueError(
                "Focused batch needs a matching documented budget selection"
            )
        if study.get("final_source_sha256") != focused["metadata"]["source_sha256"]:
            raise ValueError("Focused source differs from frozen study record")
        before = {
            r["case_id"]: r
            for r in rows
            if r["suite"] == "v2" and r["budget"] == focus_budget
        }
        focused["budget"] = focus_budget
        focused["comparison"] = aggregate(
            focused["rows"],
            {cid: r["score"] for cid, r in before.items() if r["valid"]},
        )
        focused["comparison"]["newly_legal"] = sum(
            r["valid"] and not before[r["case_id"]]["valid"] for r in focused["rows"]
        )
        focused["comparison"]["lost_legal"] = sum(
            not r["valid"] and before[r["case_id"]]["valid"] for r in focused["rows"]
        )
        focused["convergence"] = convergence_summary(focused["rows"])
        changes = [
            {
                "case_id": r["case_id"],
                "before": before[r["case_id"]]["score"],
                "after": r["score"],
                "delta": r["score"] - before[r["case_id"]]["score"],
            }
            for r in focused["rows"]
            if r["valid"] and before[r["case_id"]]["valid"]
        ]
        focused["extreme_changes"] = (
            sorted(
                [c for c in changes if c["delta"] > 1e-6],
                key=lambda c: -c["delta"],
            )[:3]
            + sorted(
                [c for c in changes if c["delta"] < -1e-6],
                key=lambda c: c["delta"],
            )[:3]
        )
        focused["by_load"] = {
            load: aggregate(
                [r for r in focused["rows"] if r["case_id"].split(":")[2] == load],
                {cid: r["score"] for cid, r in before.items() if r["valid"]},
            )
            for load in ("load60", "load85", "load97")
        }
        focused["component_deltas"] = {
            part: sum(
                r["parts"][part] - before[r["case_id"]]["parts"][part]
                for r in focused["rows"]
                if r["valid"] and before[r["case_id"]]["valid"]
            )
            for part in ("score_s", "score_v", "score_p", "score_c", "score_b")
        }
        focused["baselines"] = {
            key: aggregate(
                focused["rows"], {cid: b.get(key) for cid, b in baseline.items()}
            )
            for key in ("legacy", "cpp_v2")
        }
        for cid, row in before.items():
            baseline.setdefault(cid, {})["foundation"] = (
                row["score"] if row["valid"] else None
            )
    audit = read(args.audit) if args.audit.exists() else {}
    if not args.allow_partial and (
        legacy is None
        or len([r for r in legacy["rows"] if r["case_id"].startswith("v2:")]) != 72
    ):
        raise ValueError("The complete 72-case V2 legacy Python run is missing")
    if audit.get("errors"):
        raise ValueError(f"Independent audit failed: {audit['errors']}")
    for budget in (5, 20, 60):
        selected = [r for r in rows if r["budget"] == budget and r["suite"] == "v2"]
        if len(selected) != 72 and not args.allow_partial:
            raise ValueError(
                f"Expected 72 V2 cases at {budget}s, found {len(selected)}"
            )
    if not args.allow_partial and (
        len([r for r in rows if r["suite"] == "public"]) != 11 or not audit
    ):
        raise ValueError("Public validation or final audit missing")
    expected_audits = {str(directory) for directory in audited_summaries}
    if not args.allow_partial and not expected_audits <= set(audit["runs"]):
        raise ValueError("Final audit does not cover every run")
    if not args.allow_partial:
        for directory, summary in audited_summaries.items():
            verify_audit(directory, summary, audit)
    public_cases = read(ROOT / "tests/fixtures/general_validation_suite/cases.json")[
        "cases"
    ]
    for r in public_cases:
        if r["expectedValid"]:
            baseline.setdefault("public:" + r["id"], {}).update(
                {
                    "public_reference": r.get("referenceScore"),
                    "public_reference_status": r.get("referenceStatus"),
                }
            )
    stats, historical_comparison = {}, {}
    for budget in (5, 20, 60):
        selected = [r for r in rows if r["budget"] == budget and r["suite"] == "v2"]
        stats[str(budget)] = {
            k: aggregate(selected, {cid: b.get(k) for cid, b in baseline.items()})
            for k in ("cpp", "envelope", "early", "legacy", "cg", "cpp_v2")
        }
        historical_comparison[str(budget)] = {
            k: aggregate(
                [r for r in rows if r["budget"] == budget and r["case_id"] in cpp],
                {cid: b.get(k) for cid, b in baseline.items()},
            )
            for k in ("cpp", "envelope", "early", "cg")
        }
    allocation_bytes = gzip.compress(
        json.dumps(
            finite_json(solutions),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode(),
        mtime=0,
    )
    data = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "draft": args.allow_partial,
        "branch": "codex/model-driven-anytime-heuristic",
        "primary_suite": "v2",
        "primary_case_count": 72,
        "hardware": {
            "cpu": "Apple M5",
            "cores": 10,
            "memory_gib": 16,
            "cp_sat_workers": 8,
            "highs_threads": 1,
            "long_budget_recombination_workers": 1,
        },
        "rows": rows,
        "focused": extended.get("focused"),
        "cpp_v2": extended.get("cpp_v2"),
        "study": study,
        "baselines": baseline,
        "aggregates": stats,
        "historical_cpp_comparison": historical_comparison,
        "budget_tradeoff": budget_tradeoff(rows),
        "observed_quality": observed_quality(rows, extended.get("focused"), baseline),
        "budget_comparisons": [
            compare_budgets([r for r in rows if r["suite"] == "v2"], a, b)
            for a, b in [(5, 20), (20, 60), (5, 60)]
        ],
        "convergence": {
            str(budget): convergence_summary(
                [r for r in rows if r["budget"] == budget and r["suite"] == "v2"]
            )
            for budget in (5, 20, 60)
        },
        "audit": audit,
        "metadata": {
            "runs": runs,
            "tier_versions": versions,
            "cpp": cpp_provenance,
            "historical_config_sha256": original_hash,
            "mathematical_settings_match": True,
            "allocation_archive": {
                "file": "allocations.json.gz",
                "sha256": hashlib.sha256(allocation_bytes).hexdigest(),
                "records": len(solutions),
            },
            "legacy": {k: v for k, v in (legacy or {}).items() if k != "rows"},
        },
    }
    data = finite_json(data)
    render_report(data, args.output)
    args.provenance.mkdir(parents=True, exist_ok=True)
    (args.provenance / "allocations.json.gz").write_bytes(allocation_bytes)
    with zipfile.ZipFile(
        args.provenance / "source-snapshots.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for directory, summary in audited_summaries.items():
            for relative in summary["source_sha256"]:
                archive.write(
                    directory / "source_snapshot" / relative,
                    f"{directory.name}/{relative}",
                )
            for name in summary.get("effective_configs_sha256", {}):
                archive.write(directory / name, f"{directory.name}/{name}")
    (args.provenance / "results.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    )
    compact = [
        {
            k: v
            for k, v in r.items()
            if k not in ("history", "stages", "operator_patterns", "parts")
        }
        | r["parts"]
        | {
            k: v
            for k, v in baseline.get(r["case_id"], {}).items()
            if k not in ("legacy_parts", "cpp_v2_parts")
        }
        | {
            "legacy_" + k: v
            for k, v in baseline.get(r["case_id"], {}).get("legacy_parts", {}).items()
        }
        | {
            "cpp_v2_" + k: v
            for k, v in baseline.get(r["case_id"], {}).get("cpp_v2_parts", {}).items()
        }
        for r in rows + extended.get("focused", {}).get("rows", [])
    ]
    fields = list(dict.fromkeys(k for r in compact for k in r))
    with (args.provenance / "results.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(compact)
    (args.provenance / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2)
    )
    (args.provenance / "historical_cpp_config.json").write_bytes(
        args.historical_config.read_bytes()
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
