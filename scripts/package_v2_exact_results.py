"""Package completed V2 evidence and verify every archived member by SHA-256."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from scripts.benchmark_v2_exact import ROOT, sha
from scripts.build_v2_exact_report import comparison, load, render


def package(work, report):
    work, report = work.resolve(), report.resolve()
    benchmark = load(work / "benchmark.json")
    assert benchmark["complete"], "refusing to package an unfinished benchmark as final"
    assert sha(work / "input-audit.json") == benchmark["metadata"]["input_audit_sha256"]
    assert sha(work / "direct-incumbent-audit.json") == benchmark["metadata"]["model_audit_sha256"]
    for relative, digest in benchmark["metadata"]["sources_sha256"].items():
        assert sha(ROOT / relative) == digest, (relative, "frozen algorithm source changed")
    data = comparison(load(work / "input-audit.json"), benchmark)
    report.mkdir(parents=True, exist_ok=True)
    (report / "comparison.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (report / "index.html").write_text(render(data), encoding="utf-8")
    files = {}

    def include(path, category):
        assert path.is_file(), path
        relative = path.resolve().relative_to(ROOT).as_posix()
        files[relative] = dict(path=path, category=category)

    for name in ("benchmark.json", "input-audit.json", "direct-incumbent-audit.json", "runtime-environment.json", "process.json"):
        include(work / name, "formal_metadata_or_audit")
    for path in work.glob("*.log"):
        include(path, "audit_or_execution_log")
    for path in work.glob("report-*.json"):
        include(path, "report_or_certificate_audit")
    for path in work.glob("report-*.png"):
        include(path, "report_browser_screenshot")
    include(work / "direct-ssr-counterexample-audit.json", "model_counterexample_audit")
    for path in (work / "upstream").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            include(path, "unchanged_collaborator_snapshot")
    for record in benchmark["records"]:
        folder = (ROOT / record["raw_output"]).parent
        for name in ("raw.json", "record.json", "config.json", "solver.log"):
            path = folder / name
            if name == "raw.json" and record["raw_sha256"] is None:
                assert not path.exists()
            else:
                include(path, "formal_run")
    for relative in benchmark["metadata"]["sources_sha256"]:
        include(ROOT / relative, "frozen_algorithm_source")
    for name in ("audit_v2_exact_inputs", "audit_direct_gurobi_incumbents", "build_v2_exact_report", "package_v2_exact_results"):
        include(ROOT / "scripts" / (name + ".py"), "audit_and_delivery_source")
    for name in ("test_direct_gurobi", "test_v2_exact_benchmark", "test_v2_exact_report", "test_cg_gurobi"):
        include(ROOT / "tests" / (name + ".py"), "validation_source")
    # Failed attempts remain traceable but are excluded from comparison.json.
    for folder in ("harness-path-failure-runs", "cli-key-failure-runs", "ssr-model-failure-runs"):
        for path in (work / folder).rglob("*"):
            if path.is_file():
                include(path, "excluded_attempt_not_formal_result")
    for path in work.glob("*-failure-benchmark.json"):
        include(path, "excluded_attempt_not_formal_result")
    for path in work.glob("ssr-model-failure-*"):
        if path.is_file():
            include(path, "excluded_attempt_not_formal_result")
    manifest = dict(schema_version=1, algorithm_commit=benchmark["metadata"]["algorithm_commit"],
        upstream_commit=benchmark["metadata"]["upstream_commit"],
        case_count=72, formal_run_count=len(benchmark["records"]), complete=True,
        path_policy="Archive paths equal repository-relative paths. Windows absolute command paths in raw records describe the original environment.",
        attribution="The upstream snapshot is unchanged collaborator material at upstream_commit; its original authorship is preserved. User-owned direct CLI and benchmark/audit changes are recorded separately in Git.",
        exclusion_policy="Failed path/key attempts and the superseded SSR-model batch are retained as evidence and never counted as formal benchmark runs.",
        members={relative: dict(sha256=sha(item["path"]), bytes=item["path"].stat().st_size,
            category=item["category"]) for relative, item in sorted(files.items())})
    archive = report / "experiment-data.zip"
    temporary = archive.with_suffix(".tmp.zip")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for relative, item in sorted(files.items()):
            content = item["path"].read_bytes()
            assert hashlib.sha256(content).hexdigest() == manifest["members"][relative]["sha256"]
            bundle.writestr(relative, content)
        bundle.writestr("member-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
    with zipfile.ZipFile(temporary) as bundle:
        assert bundle.testzip() is None
        assert set(bundle.namelist()) == {*manifest["members"], "member-manifest.json"}
        assert len(bundle.namelist()) == len(manifest["members"]) + 1
        for relative, entry in manifest["members"].items():
            content = bundle.read(relative)
            assert len(content) == entry["bytes"] and hashlib.sha256(content).hexdigest() == entry["sha256"]
    temporary.replace(archive)
    artifact = dict(schema_version=1, archive=archive.name, archive_sha256=sha(archive),
        archive_bytes=archive.stat().st_size, archive_member_count=len(manifest["members"])+1,
        all_members_verified=True, algorithm_commit=manifest["algorithm_commit"],
        upstream_commit=manifest["upstream_commit"], formal_run_count=manifest["formal_run_count"],
        files={name: sha(report / name) for name in ("comparison.json", "index.html", "README.md")})
    (report / "artifact-manifest.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(artifact, ensure_ascii=False))
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    package(args.work_dir, args.report_dir)


if __name__ == "__main__":
    main()
