# Current Active Solver Pipeline

> Repository boundary: the frozen Formal24 inputs, `.native_v2` files, memory
> artifacts, and release evidence referenced below remain in the internal
> research archive. They are not bundled with this collaboration repository.
> This document describes the audited pipeline boundary; it is not a claim
> that a fresh clone can run the frozen release workflow.

Source of truth: frozen Heuristic V1R configuration and `run_heuristic_v1r.py --final-release`.

| Stage | State | Current implementation |
| --- | --- | --- |
| Select Formal24 case, seatmaps, groups, config, frozen artifacts | ACTIVE | Python benchmark/orchestration |
| Build topology, fixed-seat context, complete placement domains and score coefficients | ACTIVE | Python exporter / previously exported `.native_v2` artifacts |
| Load a complete audited incumbent | ACTIVE | frozen Python-produced artifact encoded in `INCUMBENT` records |
| RR task scheduling and per-group pattern generation | ACTIVE | C++ `native_pattern_kernel.cpp` |
| Restricted integer master | ACTIVE | C++ `native_restricted_master.cpp` / HiGHS |
| Baseline-preserving cross-trajectory memory | OPTIONAL | Python reconstruction callback plus C++ merge/re-solve; enabled by V1R config but normally skipped unless slack is sufficient |
| External legality and objective audit | ACTIVE | Python evaluator after native solve; not part of the timed solver core |
| Reserve Beam | DEAD | disabled by frozen V1R config |
| Sequential exchange | DEAD | disabled |
| Targeted multi-pattern construction | DEAD | disabled |
| Local expansion | DEAD | disabled |
| MLP/GNN ranking | DEAD | disabled; research-only |
| Exact column generation | DEAD | offline reference/teacher/audit only |

## Raw Rich migration path

On `feat/full-cpp-rich-m1`, the raw `seat_protect_cpp.exe` path first applies
the frozen cabin decomposition (when enabled), with per-cabin topology, budgets
and independent trajectories, then merges and validates the result. Each cabin
runs a native Q0 fallback and an independent Rich construction/repair trajectory,
followed by VND, structured patterns, protected MIP, special pricing, LNS and
Rich restricted MIP/local branching. In `group-first`, independent Q1/Q2A
fallback improvements run afterward only with remaining global time and replace
the selected assignment only on a complete/legal strict improvement. The
`group-soft` entry retains its earlier Q1-before-Rich order.
The restricted stage consumes the complete
Rich elite store; raw Rich no longer calls the separate RR adapter/master.
All these stages use the shared native deadline and carry scheduler. Raw input
and seatmaps are JSON; this path has no Python callback or `.native_v2` input.
Migration acceptance was completed on 2026-09-24 after the user accepted the
three small score regressions in the frozen `c6a41f3` Formal24 run. All 24 cases
pass completeness, legality and evaluator checks; strict score nonregression
remains failed. See `FULL_CPP_RICH_REFERENCE_STATUS.md` for differential evidence,
the acceptance decision and retained timing/trajectory boundaries.

The V1R flow below is the historical preprocessed-input release path and remains
separate from the raw Rich migration.

## Recommended short-budget native path

Use `configs/config_native_rich_4p5s_search.json` with `rich-fast --time-limit 5`.
The existing native conflict LNS uses earlier stage carry plus 0.3s base budget;
restricted MIP retains 0.1s, and scoring/overhead reserve is 0.5s. Final Formal24
has 24/24 legal/complete/evaluator-consistent cases, median process wall 4.493s,
maximum 4.550s, and 20 improve / 4 tie / 0 regress versus the initial short profile.
The unchanged 60s mode remains separate. See the Rich status ledger for evidence.

## Initial short-budget native path

The `rich-fast` entry and `configs/config_native_rich_5s.json` retain the same
hard constraints, objective and native Rich implementations while changing the
schedule for 5 seconds: construction/repair -> VND -> bounded structured patterns
-> restricted MIP/local branching. Q0 is an on-demand fallback for incomplete
Rich construction/repair, using only remaining search time. Q1/Q2A, protected
MIP, special pricing and LNS are omitted in this profile. The 60s `group-first`
configuration, frozen results and archived runtime remain separately available.
The user's 5s request authorizes this component reduction and score loss; it does
not change the earlier 60s acceptance or waive legal/complete output checks.

## Historical V1R production call boundary

```text
Python selects/prepares HEADER_V2 input
  -> one C++ process call
  -> native RR/Beam + native restricted master + JSON serialization
  -> Python external evaluator
```

There is no fine-grained Python callback inside baseline native RR or the master. The optional V1R memory branch is an explicit exception: C++ emits a request and waits while Python reconstructs a memory pool. This exception must be removed or made fully native before `FULL-CPP-CORRECTNESS` can pass.

## Target boundary

```text
seat_protect_cpp.exe --input CASE --config CONFIG --output RESULT --time-limit T --seed 0
  -> native raw parsing and preprocessing
  -> native complete feasible construction
  -> native search/improvement
  -> native internal validation and serialization
```

Python remains permitted only for benchmark orchestration and the independent post-solve evaluator.
