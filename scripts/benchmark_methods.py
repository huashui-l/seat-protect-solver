"""Benchmark the heuristic, Gurobi column generation, and direct Gurobi routes.

The default schedule is the project contract: 5 seconds for the heuristic,
150 seconds for each exact route, and a 600 second supplement only when the
150 second run did not certify an optimum (or certified infeasibility).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import allocation_evaluator as evaluator  # noqa: E402


METHODS = ("cpp_heuristic", "cg_gurobi", "direct_gurobi")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _resolve_repo_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _case(
    case_id: str,
    corpus: str,
    direction: str,
    groups_path: Path,
    old_seatmap_path: Path,
    new_seatmap_path: Path,
) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "corpus": corpus,
        "direction": direction,
        "groups_path": groups_path,
        "old_seatmap_path": old_seatmap_path,
        "new_seatmap_path": new_seatmap_path,
    }


def load_d3_cases() -> List[Dict[str, Any]]:
    corpus = ROOT / "data" / "generator_v3_2" / "d3_official_24cases_2026-09-07"
    cases: List[Dict[str, Any]] = []
    for direction in ("forward", "reverse"):
        if direction == "forward":
            old_name, new_name = "3-4-3seatmap.json", "3-3seatmap.json"
        else:
            old_name, new_name = "3-3seatmap.json", "3-4-3seatmap.json"
        for groups_path in sorted((corpus / direction).glob("*_groups.json")):
            payload = _read_json(groups_path)
            cases.append(_case(
                str(payload.get("caseId") or groups_path.stem),
                "formal24",
                direction,
                groups_path,
                ROOT / "data" / old_name,
                ROOT / "data" / new_name,
            ))
    if len(cases) != 24:
        raise RuntimeError(f"expected 24 Formal24 cases, found {len(cases)}")
    return cases


def load_d4_cases() -> List[Dict[str, Any]]:
    corpus = ROOT / "data" / "generator_v3_2" / "d4_smooth_multi_aircraft_2026-09-25"
    manifest = _read_json(corpus / "manifest.json")
    cases: List[Dict[str, Any]] = []
    for item in manifest.get("cases", []):
        groups_path = _resolve_repo_path(str(item["relative_path"]))
        old_map = corpus / "seatmaps" / f"{item['old_layout']}seatmap.json"
        new_map = corpus / "seatmaps" / f"{item['new_layout']}seatmap.json"
        cases.append(_case(
            str(item["case_id"]),
            "d4",
            str(item.get("direction", "")),
            groups_path,
            old_map,
            new_map,
        ))
    if len(cases) != 16:
        raise RuntimeError(f"expected 16 D4 cases, found {len(cases)}")
    return cases


def load_cases(corpora: Sequence[str]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for corpus in corpora:
        if corpus == "formal24":
            result.extend(load_d3_cases())
        elif corpus == "d4":
            result.extend(load_d4_cases())
        else:
            raise ValueError(f"unknown corpus: {corpus}")
    return result


def _load_case(case: Mapping[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    new = _read_json(case["new_seatmap_path"])["seats"]
    old = _read_json(case["old_seatmap_path"])["seats"]
    groups = _read_json(case["groups_path"])["groups"]
    return new, old, groups


def _gurobi_config(config: Mapping[str, Any], method: str, seconds: float) -> dict:
    result = copy.deepcopy(dict(config))
    if method == "cg_gurobi":
        result["cg_gurobi"] = {
            "total_time_limit": float(seconds),
            "pricing_time_limit": min(60.0, max(1.0, seconds / 3.0)),
            "integer_time_limit": min(60.0, max(1.0, seconds / 5.0)),
            "pricing_workers": 1,
            "master_threads": 1,
            "pricing_threads": 1,
            "warm_start": "greedy",
            "log": False,
        }
    elif method == "direct_gurobi":
        result["direct_gurobi"] = {
            "time_limit": float(seconds),
            "threads": 1,
            "seed": 0,
            "log": False,
        }
    else:
        raise ValueError(method)
    return result


def _metrics(
    new: list[dict],
    old: list[dict],
    groups: list[dict],
    assigned: Mapping[Any, str],
    weights: Mapping[str, float],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    violations, unassigned, detail = evaluator.count_hard_constraint_violations(
        new, groups, dict(assigned), dict(config)
    )
    score, score_detail = evaluator.calculate_soft_score(
        new, old, groups, dict(assigned), dict(weights), dict(config)
    )
    return {
        "complete": violations == 0 and unassigned == 0,
        "violations": violations,
        "unassigned": unassigned,
        "score": score if assigned else None,
        "score_detail": score_detail,
        "hard_detail": {k: v for k, v in detail.items() if isinstance(v, int) and v},
    }


def _run_method(
    case: Mapping[str, Any],
    method: str,
    seconds: float,
    base_config: Mapping[str, Any],
    native_config_path: Path | None = None,
    native_executable: Path | None = None,
) -> Dict[str, Any]:
    new, old, groups = _load_case(case)
    config = _gurobi_config(base_config, method, seconds) if method != "cpp_heuristic" else dict(base_config)
    started = time.perf_counter()
    raw: Dict[str, Any] = {}
    error = None
    try:
        if method == "cpp_heuristic":
            if native_config_path is None or native_executable is None:
                raise ValueError("cpp_heuristic requires --native-executable and --native-config")
            native_config = _read_json(native_config_path)
            native_config.setdefault("algorithm", {})["business_time_limit_seconds"] = float(seconds)
            native_config.setdefault("input_contract", {}).setdefault("seatmaps_by_direction", {})[
                case["direction"]
            ] = {"old": str(Path(case["old_seatmap_path"]).resolve()),
                 "new": str(Path(case["new_seatmap_path"]).resolve())}
            with tempfile.TemporaryDirectory(prefix="seat-protect-bench-") as temp_dir:
                temp = Path(temp_dir)
                case_config = temp / "native_config.json"
                case_input = temp / "case.json"
                output = temp / "result.json"
                case_config.write_text(json.dumps(native_config, indent=2), encoding="utf-8")
                native_case = _read_json(case["groups_path"])
                native_case.setdefault("direction", case["direction"])
                case_input.write_text(json.dumps(native_case, indent=2), encoding="utf-8")
                command = [
                    str(native_executable),
                    "--input", str(case_input),
                    "--config", str(case_config),
                    "--output", str(output),
                    "--construction-objective", "rich-fast",
                    "--time-limit", str(float(seconds)),
                    "--seed", "0",
                ]
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=max(10.0, float(seconds) + 5.0),
                    check=False,
                )
                if not output.exists():
                    detail = (completed.stderr or completed.stdout or "native solver produced no output").strip()
                    raise RuntimeError(f"native exit={completed.returncode}: {detail[-500:]}")
                raw = _read_json(output)
                assignments = {
                    (int(item["groupId"]), int(item["hostnum"])): item["seatId"]
                    for item in raw.get("assignments", [])
                }
                raw["assigned_seats"] = assignments
                raw["solver_status"] = raw.get("status")
        elif method == "cg_gurobi":
            from src.cg_gurobi import solve

            raw = solve(new, old, groups, config.get("weights", {}), config)
        elif method == "direct_gurobi":
            from src.direct_gurobi import solve

            raw = solve(new, old, groups, config.get("weights", {}), config)
        else:
            raise ValueError(f"unknown method: {method}")
    except Exception as exc:  # benchmark rows must preserve failures
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    assigned = raw.get("assigned_seats", {}) if isinstance(raw, dict) else {}
    result: Dict[str, Any] = {
        "method": method,
        "budget_seconds": float(seconds),
        "elapsed_seconds": elapsed,
        "error": error,
    }
    if error is None:
        result.update(_metrics(new, old, groups, assigned, config.get("weights", {}), config))
        result.update({
            "status": raw.get("status"),
            "solver_status": raw.get(
                "solver_status", raw.get("integer_status", raw.get("status"))
            ),
            "proven_optimal": bool(raw.get("proven_optimal", False)),
            "bound": raw.get("lp_bound", raw.get("model_bound")),
            "bound_certified": bool(raw.get("lp_bound_certified", False)),
            "objective_error": raw.get("model_objective_error"),
            "mip_gap": raw.get("mip_gap"),
            "variables": raw.get("variables"),
            "constraints": raw.get("constraints"),
            "raw_seconds": raw.get("seconds"),
        })
        if method == "cpp_heuristic":
            result["proven_optimal"] = False
            result["status"] = "complete" if result["complete"] else "incomplete"
            result["native_score"] = raw.get("native_score")
            result["native_score_error"] = (
                abs(raw["native_score"] - result["score"])
                if raw.get("native_score") is not None and result.get("score") is not None else None
            )
            result["solve_wall_seconds"] = raw.get("solve_wall_seconds")
            result["preparation_seconds"] = raw.get("preparation_seconds")
    return result


def _certified(result: Mapping[str, Any]) -> bool:
    if result.get("error"):
        return False
    if result.get("proven_optimal"):
        return True
    return str(result.get("status")) == "infeasible" or str(result.get("solver_status")) == "infeasible"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _provenance(
    cases: Sequence[Mapping[str, Any]],
    config_path: Path,
    native_config_path: Path | None,
    native_executable: Path | None,
) -> Dict[str, Any]:
    gurobi_version = None
    try:
        import gurobipy

        gurobi_version = ".".join(str(x) for x in gurobipy.gurobi.version())
    except ImportError:
        pass
    files = {"config": _sha256(config_path)}
    if native_config_path is not None:
        files["native_config"] = _sha256(native_config_path)
    if native_executable is not None and native_executable.exists():
        files["native_executable"] = _sha256(native_executable)
    for case in cases:
        for key in ("groups_path", "old_seatmap_path", "new_seatmap_path"):
            path = case[key]
            files[str(path)] = _sha256(path)
    return {
        "commit": _git_commit(),
        "python": sys.version,
        "platform": platform.platform(),
        "gurobi": gurobi_version,
        "files_sha256": files,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Three-route benchmark",
        "",
        f"Commit: `{report['provenance']['commit']}`",
        f"Cases: {len(report['cases'])}; heuristic budget {report['schedule']['heuristic_seconds']}s; "
        f"exact-route budgets {report['schedule']['initial_seconds']}s + conditional {report['schedule']['supplement_seconds']}s.",
        "",
        "| case | method | budget | complete | score | violations | unassigned | proven | bound | elapsed | error |",
        "|---|---|---:|:---:|---:|---:|---:|:---:|---:|---:|---|",
    ]
    for row in report["cases"]:
        for run in row["runs"]:
            score = "" if run.get("score") is None else f"{run['score']:.6f}"
            bound = "" if run.get("bound") is None else f"{run['bound']:.6f}"
            error = (run.get("error") or "").replace("|", "/")
            lines.append(
                f"| {row['case_id']} | {run['method']} | {run['budget_seconds']:.0f} | "
                f"{run.get('complete', False)} | {score} | {run.get('violations', '')} | "
                f"{run.get('unassigned', '')} | {run.get('proven_optimal', False)} | {bound} | "
                f"{run['elapsed_seconds']:.3f} | {error} |"
            )
    return "\n".join(lines) + "\n"


def run_benchmark(
    cases: Sequence[Mapping[str, Any]],
    config_path: Path,
    methods: Sequence[str],
    heuristic_seconds: float,
    initial_seconds: float,
    supplement_seconds: float,
    skip_supplement: bool = False,
    native_config_path: Path | None = None,
    native_executable: Path | None = None,
) -> Dict[str, Any]:
    base_config = _read_json(config_path)
    report_cases: List[Dict[str, Any]] = []
    for case in cases:
        item = {
            "case_id": case["case_id"],
            "corpus": case["corpus"],
            "direction": case["direction"],
            "runs": [],
        }
        for method in methods:
            budget = heuristic_seconds if method == "cpp_heuristic" else initial_seconds
            first = _run_method(
                case, method, budget, base_config,
                native_config_path, native_executable,
            )
            item["runs"].append(first)
            if (
                method != "cpp_heuristic"
                and not skip_supplement
                and not _certified(first)
                and not first.get("error")
            ):
                item["runs"].append(
                    _run_method(
                        case, method, supplement_seconds, base_config,
                        native_config_path, native_executable,
                    )
                )
        report_cases.append(item)
        print(f"[{item['case_id']}] completed", flush=True)
    report = {
        "schedule": {
            "heuristic_seconds": heuristic_seconds,
            "initial_seconds": initial_seconds,
            "supplement_seconds": supplement_seconds,
            "supplement_rule": "only exact runs without a certificate",
        },
        "methods": list(methods),
        "cases": report_cases,
        "provenance": _provenance(
            cases, config_path, native_config_path, native_executable
        ),
    }
    return _json_safe(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpora", default="formal24,d4", help="comma-separated: formal24,d4")
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument(
        "--native-config", type=Path,
        default=ROOT / "configs" / "config_native_rich_5s.json",
    )
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--heuristic-seconds", type=float, default=5.0)
    parser.add_argument("--initial-seconds", type=float, default=150.0)
    parser.add_argument("--supplement-seconds", type=float, default=600.0)
    parser.add_argument("--skip-supplement", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated case IDs for a smoke run")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    methods = ["cpp_heuristic" if item == "heuristic" else item for item in methods]
    unknown = set(methods) - set(METHODS)
    if unknown:
        parser.error(f"unknown methods: {sorted(unknown)}")
    if "cpp_heuristic" in methods and args.native_executable is None:
        parser.error("--native-executable is required for cpp_heuristic")
    cases = load_cases([item.strip() for item in args.corpora.split(",") if item.strip()])
    only = {item for item in args.only.split(",") if item}
    if only:
        cases = [case for case in cases if case["case_id"] in only]
        missing = only - {case["case_id"] for case in cases}
        if missing:
            parser.error(f"unknown case IDs: {sorted(missing)}")
    report = run_benchmark(
        cases,
        args.config.resolve(),
        methods,
        args.heuristic_seconds,
        args.initial_seconds,
        args.supplement_seconds,
        args.skip_supplement,
        args.native_config.resolve(),
        args.native_executable.resolve() if args.native_executable else None,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(_markdown(report), encoding="utf-8")
    print(f"wrote {args.json_output}")


if __name__ == "__main__":
    main()
