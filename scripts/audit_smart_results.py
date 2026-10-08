#!/usr/bin/env python3
"""Re-evaluate saved assignments and independently diagnose infant capacity."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_smart import cases, load
from src import allocation_evaluator as ev


def finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def infant_capacity_certificates(groups, seats, config):
    """A relaxation: distinct infant groups need disjoint infant/adult edges.

    Ignore all nonfixed other passengers and all SSR isolation/protection rules.
    One infant per group is enough for the bound; groups with a fixed caregiver
    are excluded because their support need not consume another free seat.
    """
    topology = ev.SeatTopology(seats, config)
    fixed = {(p.get("newSeat") or {}).get("seatNum") for g in groups for p in g["psrs"]}
    fixed.discard(None)
    selected = defaultdict(list)
    for group in groups:
        adults = [p for p in group["psrs"] if ev._is_adult_caregiver(p)]
        if any((p.get("newSeat") or {}).get("seatNum") for p in adults):
            continue
        babies = [
            p
            for p in group["psrs"]
            if p.get("ssr") == "BSCT" and not (p.get("newSeat") or {}).get("seatNum")
        ]
        if babies:
            selected[ev.group_cabin_class(group)].append(
                (group["groupId"], babies[0]["hostnum"])
            )
    rule = ev._ssr_rule("BSCT", config)
    if not rule.get("requires_caregiver", False):
        return []
    certificates = []
    for cabin, passengers in selected.items():
        edges = set()
        for s, seat in topology.seat_map.items():
            if s in fixed or seat.get("seatClass") != cabin:
                continue
            if rule.get("requires_bassinet") and not seat.get("hasBassinet"):
                continue
            if rule.get("requires_aisle") and not seat.get("isAisle"):
                continue
            if seat.get("isExitRow") and not rule.get("allow_exit_row"):
                continue
            for t in topology.row_neighbors(
                s,
                allow_cross_aisle=bool(rule.get("caregiver_allow_cross_aisle", False)),
            ):
                if t not in fixed and topology.seat_map[t].get("seatClass") == cabin:
                    edges.add(tuple(sorted((s, t))))
        adjacency = defaultdict(set)
        for a, b in edges:
            adjacency[a].add(b)
            adjacency[b].add(a)
        matching, unseen = [], set(adjacency)
        while unseen:
            component, frontier = set(), [min(unseen)]
            while frontier:
                vertex = frontier.pop()
                if vertex not in component:
                    component.add(vertex)
                    frontier.extend(adjacency[vertex] - component)
            unseen -= component

            @lru_cache(None)
            def maximum(vertices, adjacency=adjacency):
                if not vertices:
                    return ()
                a = vertices[0]
                best = maximum(vertices[1:])
                for b in sorted(adjacency[a] & set(vertices[1:])):
                    option = ((a, b),) + maximum(
                        tuple(v for v in vertices[1:] if v != b)
                    )
                    if len(option) > len(best):
                        best = option
                return best

            matching.extend(maximum(tuple(sorted(component))))
        if len(passengers) > len(matching):
            certificates.append(
                {
                    "cabin": cabin,
                    "required_distinct_groups": len(passengers),
                    "maximum_disjoint_pairs": len(matching),
                    "infants": passengers,
                    "available_edges": sorted(edges),
                    "maximum_matching": matching,
                    "reason": "Even after relaxing other passengers and SSR/protection constraints, fixed seats leave too few disjoint infant/caregiver pairs.",
                }
            )
    return certificates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        cid: (ip, op, np, groups)
        for cid, ip, op, np, groups in cases(["formal24", "d4", "v2", "public"])
    }
    errors, summaries, certificates, fingerprints = [], {}, {}, {}
    for directory in args.runs:
        summary = load(directory / "summary.json")
        cfg = summary["config"]
        counts = defaultdict(int)
        records = {}
        for row in summary["rows"]:
            cid = row["case_id"]
            if cid in records:
                errors.append([str(directory), cid, "duplicate case"])
            ip, op, np, groups = inputs[cid]
            ns, os = load(np)["seats"], load(op)["seats"]
            result_path = directory / (cid.replace(":", "_") + ".json")
            records[cid] = hashlib.sha256(result_path.read_bytes()).hexdigest()
            result = load(result_path)
            assignment = {
                tuple(map(int, k.split(","))): v
                for k, v in result.get("assigned_seats", {}).items()
            }
            for key, path in [
                ("input_sha256", ip),
                ("old_seatmap_sha256", op),
                ("new_seatmap_sha256", np),
            ]:
                if row.get(key) != hashlib.sha256(path.read_bytes()).hexdigest():
                    errors.append([str(directory), cid, "input hash mismatch", key])
            violations, unassigned, _ = ev.count_hard_constraint_violations(
                ns, groups, assignment, cfg
            )
            score, parts = ev.calculate_soft_score(
                ns, os, groups, assignment, cfg["weights"], cfg
            )
            valid = violations == unassigned == 0
            if (
                not finite_number(row.get("seconds"))
                or row["seconds"] < 0
                or not finite_number(row.get("budget"))
                or row["budget"] <= 0
            ):
                errors.append([str(directory), cid, "invalid timing record"])
            if result.get("status") != row.get("status"):
                errors.append([str(directory), cid, "status mismatch"])
            if (row.get("status") == "complete") != valid:
                errors.append([str(directory), cid, "completion status mismatch"])
            for key in ("upper_bound", "proven_optimal"):
                if result.get(key) != row.get(key):
                    # Legacy does not expose an optimality flag; both mean false.
                    if key == "proven_optimal" and not (
                        result.get(key) or row.get(key)
                    ):
                        continue
                    errors.append([str(directory), cid, "summary mismatch", key])
            if row.get("status") != "error" and (
                row.get("violations") != violations
                or row.get("unassigned") != unassigned
            ):
                errors.append([str(directory), cid, "constraint count mismatch"])
            if valid and not math.isfinite(score):
                errors.append([str(directory), cid, "nonfinite feasible score"])
            if valid != row["valid"]:
                errors.append([str(directory), cid, "feasibility mismatch"])
            if valid and (
                not finite_number(row["score"])
                or not finite_number(result["total_score"])
                or abs(score - row["score"]) > 1e-6
                or abs(score - result["total_score"]) > 1e-6
            ):
                errors.append([str(directory), cid, "score mismatch"])
            if valid and result.get("score_detail"):
                for key, value in parts.items():
                    recorded = result["score_detail"].get(key)
                    if not finite_number(recorded) or abs(recorded - value) > 1e-6:
                        errors.append(
                            [str(directory), cid, "score component mismatch", key]
                        )
            bound = result.get("upper_bound")
            if bound is not None and not finite_number(bound):
                errors.append([str(directory), cid, "nonfinite global bound"])
            if valid and bound is not None and bound < score - 1e-6:
                errors.append([str(directory), cid, "bound below feasible score"])
            if result.get("proven_optimal") and (
                not valid or bound is None or bound - score > 1e-6
            ):
                errors.append([str(directory), cid, "invalid optimality certificate"])
            history = result.get("history", [])
            if any(
                not finite_number(point.get("score"))
                or not finite_number(point.get("seconds"))
                or not 0 <= point["seconds"] <= row["seconds"] + 1e-6
                for point in history
            ):
                errors.append([str(directory), cid, "invalid incumbent history point"])
            if valid and history and abs(history[-1]["score"] - score) > 1e-6:
                errors.append([str(directory), cid, "history final score mismatch"])
            if any(b["score"] < a["score"] - 1e-7 for a, b in pairwise(history)):
                errors.append([str(directory), cid, "nonmonotone incumbent history"])
            if any(b["seconds"] < a["seconds"] for a, b in pairwise(history)):
                errors.append([str(directory), cid, "nonmonotone incumbent timestamps"])
            counts[
                "complete_legal"
                if valid
                else result.get("status", row.get("status", "error"))
            ] += 1
            counts["over_budget_50ms"] += row["seconds"] > row["budget"] + 0.05
            if cid not in certificates:
                proof = infant_capacity_certificates(groups, ns, cfg)
                if proof:
                    certificates[cid] = {
                        "input_sha256": hashlib.sha256(ip.read_bytes()).hexdigest(),
                        "proofs": proof,
                    }
            if valid and cid in certificates:
                errors.append(
                    [
                        str(directory),
                        cid,
                        "capacity certificate contradicts valid assignment",
                    ]
                )
            if row.get("status") == "infeasible" and cid not in certificates:
                errors.append(
                    [
                        str(directory),
                        cid,
                        "infeasible result lacks independent certificate",
                    ]
                )
        summaries[str(directory)] = dict(counts)
        fingerprints[str(directory)] = {
            "row_count": len(summary["rows"]),
            "summary_sha256": hashlib.sha256(
                (directory / "summary.json").read_bytes()
            ).hexdigest(),
            "result_sha256": records,
        }
        # Historical native runs adapt only the input-routing configuration.
        for name, expected in summary.get("effective_configs_sha256", {}).items():
            path = directory / name
            if (
                not path.exists()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                errors.append([str(directory), name, "native routing config mismatch"])
        if summary.get("effective_configs_sha256"):
            fingerprints[str(directory)]["auxiliary_sha256"] = summary[
                "effective_configs_sha256"
            ]
        for relative, expected in summary["source_sha256"].items():
            snapshot = directory / "source_snapshot" / relative
            if (
                not snapshot.exists()
                or hashlib.sha256(snapshot.read_bytes()).hexdigest() != expected
            ):
                errors.append([str(directory), relative, "source snapshot mismatch"])
    report = {
        "runs": summaries,
        "fingerprints": fingerprints,
        "errors": errors,
        "independent_infeasibility_certificates": certificates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
