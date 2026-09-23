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
runs native
Q0/Q1/Q2A fallbacks and an independent Rich construction/repair trajectory,
followed by VND, structured patterns, protected MIP, special pricing, LNS and
Rich restricted MIP/local branching. The restricted stage consumes the complete
Rich elite store; raw Rich no longer calls the separate RR adapter/master.
All these stages use the shared native deadline and carry scheduler. Raw input
and seatmaps are JSON; this path has no Python callback or `.native_v2` input.
Component integration is not the Formal24 quality-parity gate. See
`FULL_CPP_RICH_REFERENCE_STATUS.md` for differential evidence and remaining audit.

The V1R flow below is the historical preprocessed-input release path and remains
separate from the raw Rich migration.

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
