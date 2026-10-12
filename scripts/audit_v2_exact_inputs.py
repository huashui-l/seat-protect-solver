"""Freeze and independently audit the collaborator's completed 60s V2 batch."""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from functools import lru_cache
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import allocation_evaluator as evaluator
from src.cg_gurobi.instance import Instance


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(upstream: Path, output: Path):
    provenance = upstream / "docs/provenance/smart-heuristic"
    results = read(provenance / "results.json")
    selected = results["focused"]
    rows = selected["rows"]
    config = selected["metadata"]["config"]
    assert sha(upstream / "src/allocation_evaluator.py") == sha(ROOT / "src/allocation_evaluator.py")
    for name in ("solver.py", "master.py", "pricing.py", "branching.py"):
        assert sha(upstream / "src/cg_gurobi" / name) == sha(ROOT / "src/cg_gurobi" / name)
    corpus = upstream / "data/generator_v3_2/benchmark_v2_2026-10-07"
    manifest = read(corpus / "manifest.json")
    allocations = json.loads(gzip.decompress((provenance / "allocations.json.gz").read_bytes()))
    allocations = [r for r in allocations if r["directory"].endswith("/focused60")]
    by_id = {r["case_id"]: r for r in rows}
    assigned_by_id = {r["case_id"]: r for r in allocations}
    ids = {c["case_id"] for c in manifest["cases"]}
    assert len(rows) == len(by_id) == len(allocations) == len(assigned_by_id) == len(ids) == 72
    assert ids == set(by_id) == set(assigned_by_id)
    assert selected["budget"] == 60
    spec = importlib.util.spec_from_file_location("_v2_upstream_seat_model", upstream / "src/seat_model.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    original_certificates = read(provenance / "audit.json")["independent_infeasibility_certificates"]
    # Recompute the upstream's documented relaxation certificate without
    # importing its benchmark runner or invoking its heuristic solver.
    certificate_source = upstream / "scripts/audit_smart_results.py"
    tree = ast.parse(certificate_source.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "infant_capacity_certificates")
    namespace = dict(ev=evaluator, defaultdict=defaultdict, lru_cache=lru_cache)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(certificate_source), "exec"), namespace)
    recompute_certificate = namespace["infant_capacity_certificates"]
    report = dict(upstream_commit="e273bd9a27ce4ec3a41912cfc5ff261ef3a51f8b",
                  heuristic_batch="focused60", heuristic_rerun=False, config=config,
                  source_sha256={p.relative_to(upstream).as_posix(): sha(p) for p in
                    [provenance / "results.json", provenance / "allocations.json.gz",
                     provenance / "audit.json", corpus / "manifest.json", upstream / "src/seat_model.py",
                     certificate_source]},
                  cases=[])
    for item in manifest["cases"]:
        cid = item["case_id"]
        row, allocation = by_id[cid], assigned_by_id[cid]
        groups_path = upstream / item["relative_path"]
        payload = read(groups_path)
        old_path = corpus / "seatmaps" / f"{payload['oldLayout']}seatmap.json"
        new_path = corpus / "seatmaps" / f"{payload['newLayout']}seatmap.json"
        for path, field in [(groups_path, "input_sha256"), (old_path, "old_seatmap_sha256"),
                            (new_path, "new_seatmap_sha256")]:
            assert sha(path) == row[field] == allocation[field], (cid, field)
        assert sha(groups_path) == item["sha256"]
        groups, old, new = payload["groups"], read(old_path)["seats"], read(new_path)["seats"]
        inst = Instance(new, old, groups, config["weights"], config)
        original = module.Instance(new, old, groups, config["weights"], config)
        assert set(inst.passengers) == set(original.passengers)
        for key, passenger in inst.passengers.items():
            # Check all normalized domains, flags and objective coefficients,
            # not just whether the two implementations accept an incumbent.
            assert vars(passenger) == vars(original.passengers[key]), (cid, key)
        assert inst.baby_pairs == original.baby_pairs
        assert (inst.w_c, inst.compact_x_factor, inst.compact_y_factor) == (
            original.w_c, original.compact_x_factor, original.compact_y_factor)
        assignment = {tuple(map(int, k.split(","))): v for k, v in allocation["assigned_seats"].items()}
        hard, missing, detail = evaluator.count_hard_constraint_violations(new, groups, assignment, config)
        score, parts = evaluator.calculate_soft_score(new, old, groups, assignment, config["weights"], config)
        if row["valid"]:
            assert hard == missing == 0, (cid, detail)
            assert abs(score - row["score"]) <= 1e-6
            assert abs(score - allocation["score"]) <= 1e-6
        else:
            assert row["status"] == "infeasible" and cid in original_certificates
            assert original_certificates[cid]["input_sha256"] == sha(groups_path)
            proofs = recompute_certificate(groups, new, config)
            assert proofs and json.loads(json.dumps(proofs)) == original_certificates[cid]["proofs"]
        report["cases"].append(dict(case_id=cid, groups_path=str(groups_path),
            old_seatmap_path=str(old_path), new_seatmap_path=str(new_path),
            groups_sha256=sha(groups_path), old_seatmap_sha256=sha(old_path),
            new_seatmap_sha256=sha(new_path), shared_instance_parameters_match=True,
            heuristic=row, independent_rescore=dict(hard_violations=hard, unassigned=missing,
                score=score if row["valid"] else None, score_detail=parts if row["valid"] else None),
            inherited_infeasibility_certificate=original_certificates.get(cid)))
    report.update(case_count=72, complete_legal_heuristic_count=sum(r["valid"] for r in rows),
        inherited_infeasible_count=sum(not r["valid"] for r in rows),
        inherited_infeasibility_certificates_revalidated=True, audit_complete=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["case_count", "complete_legal_heuristic_count",
        "inherited_infeasible_count", "audit_complete"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.upstream_root, args.output)
