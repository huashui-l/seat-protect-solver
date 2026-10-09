"""Merge separate 5/150/600-second runs and verify the benchmark schedule."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_three_routes import (  # noqa: E402
    METHODS, _certified, _read_json, _sha256, load_cases,
)


def merge_reports(
    sources: Sequence[tuple[Path, Mapping[str, Any]]],
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require complete route coverage and exactly the requested supplements."""
    expected = {case["case_id"]: case for case in cases}
    rows: dict[str, dict[tuple[str, float], dict[str, Any]]] = {
        case_id: {} for case_id in expected
    }
    provenance = {}
    config_hash = None
    for index, (path, report) in enumerate(sources):
        source_id = f"source_{index + 1}"
        source = report["provenance"]
        files = source["files_sha256"]
        if config_hash is None:
            config_hash = files["config"]
        if files["config"] != config_hash:
            raise ValueError(f"configuration hash mismatch: {path}")
        provenance[source_id] = {
            "path": str(path), "sha256": _sha256(path),
            "run_provenance": copy.deepcopy(source),
        }
        seen = set()
        for item in report["cases"]:
            case_id = item["case_id"]
            if case_id not in expected or case_id in seen:
                raise ValueError(f"unknown or duplicate case: {case_id} in {path}")
            seen.add(case_id)
            case = expected[case_id]
            for key in ("corpus", "direction"):
                if item[key] != case[key]:
                    raise ValueError(f"case metadata mismatch: {case_id} {key}")
            for key in ("groups_path", "old_seatmap_path", "new_seatmap_path"):
                input_path = case[key]
                if files.get(str(input_path)) != _sha256(input_path):
                    raise ValueError(f"input hash mismatch: {case_id} {input_path}")
            for run in item["runs"]:
                method = run["method"]
                budget = float(run["budget_seconds"])
                allowed = (5.0,) if method == "cpp_heuristic" else (150.0, 600.0)
                if method not in METHODS or budget not in allowed:
                    raise ValueError(f"invalid method or budget: {case_id} {method} {budget}")
                key = (method, budget)
                if key in rows[case_id]:
                    raise ValueError(f"duplicate run: {case_id} {method} {budget}")
                if run.get("error"):
                    raise ValueError(f"failed run: {case_id} {method}: {run['error']}")
                if run.get("proven_optimal") and not run.get("complete"):
                    raise ValueError(f"optimality without a legal incumbent: {case_id} {method}")
                if run.get("complete") and (
                    run.get("violations") != 0 or run.get("unassigned") != 0
                    or run.get("score") is None
                ):
                    raise ValueError(f"invalid complete result: {case_id} {method}")
                rows[case_id][key] = copy.deepcopy(run) | {"source": source_id}

    report_cases = []
    for case_id, case in expected.items():
        runs = []
        for method in METHODS:
            budget = 5.0 if method == "cpp_heuristic" else 150.0
            first = rows[case_id].get((method, budget))
            if first is None:
                raise ValueError(f"missing initial run: {case_id} {method}")
            runs.append(first)
            if method == "cpp_heuristic":
                continue
            supplement = rows[case_id].get((method, 600.0))
            needs_supplement = not _certified(first)
            if needs_supplement != (supplement is not None):
                raise ValueError(f"supplement coverage mismatch: {case_id} {method}")
            if supplement is not None:
                runs.append(supplement)
        report_cases.append({
            "case_id": case_id, "corpus": case["corpus"],
            "direction": case["direction"], "runs": runs,
        })
    return {
        "schedule": {
            "heuristic_seconds": 5, "initial_seconds": 150, "supplement_seconds": 600,
            "supplement_rule": "exact runs without an optimality or infeasibility certificate",
        },
        "methods": list(METHODS), "cases": report_cases, "provenance": provenance,
    }


def markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Three-route benchmark", "",
        "Schedule verified: heuristic 5s; Gurobi routes 150s, followed by 600s only without a certificate.",
        "Elapsed measures each route call, including model construction and extraction; the subsequent independent evaluator audit is excluded.",
        "The solver time limit and this wall time are separate measurements.",
        "", "| method | cases | 600s supplements | legal at 150s/5s | legal after supplement | proven |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        runs_by_case = [[r for r in item["runs"] if r["method"] == method] for item in report["cases"]]
        initial_complete = sum(bool(runs[0].get("complete")) for runs in runs_by_case)
        complete = sum(any(r.get("complete") for r in runs) for runs in runs_by_case)
        proven = sum(any(r.get("proven_optimal") for r in runs) for runs in runs_by_case)
        supplements = sum(len(runs) - 1 for runs in runs_by_case)
        lines.append(f"| {method} | {len(runs_by_case)} | {supplements} | {initial_complete} | {complete} | {proven} |")
    lines += ["", "## Sources", "", "| source | path | commit | SHA-256 |", "|---|---|---|---|"]
    for source_id, source in report["provenance"].items():
        lines.append(f"| {source_id} | `{source['path']}` | `{source['run_provenance']['commit']}` | `{source['sha256']}` |")
    lines += [
        "", "## Runs", "",
        "| case | method | budget | complete | proven | score | bound | bound certified | elapsed | source |",
        "|---|---|---:|:---:|:---:|---:|---:|:---:|---:|---|",
    ]
    for item in report["cases"]:
        for run in item["runs"]:
            score = "" if run.get("score") is None else f"{run['score']:.6f}"
            bound = "" if run.get("bound") is None else f"{run['bound']:.6f}"
            lines.append(
                f"| {item['case_id']} | {run['method']} | {run['budget_seconds']:.0f} | "
                f"{run.get('complete', False)} | {run.get('proven_optimal', False)} | "
                f"{score} | {bound} | {run.get('bound_certified', False)} | "
                f"{run['elapsed_seconds']:.3f} | {run['source']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    sources = [(path.resolve(), _read_json(path)) for path in args.sources]
    report = merge_reports(sources, load_cases(("formal24", "d4")))
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(f"verified and merged {len(report['cases'])} cases into {args.json_output}")


if __name__ == "__main__":
    main()
