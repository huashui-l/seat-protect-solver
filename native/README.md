# Active native solver sources

This directory contains the active C++ source closure and its PowerShell build
entry points. Historical frontier C++/GNN research status is preserved under
[`docs/research-status/`](../docs/research-status/frontier-cpp-gnn-roadmap.md).

The raw JSON feasibility CLI consists of:

- `seat_protect_cpp.cpp`
- `full_cpp_solver_core.cpp/.hpp`
- `native_feasibility_solver.cpp/.hpp`
- `native_group_constructor.cpp/.hpp`
- `native_json.hpp`
- `native_rich_construction.cpp`
- `native_rich_pattern_adapter.cpp/.hpp`

`native_state_replay.cpp` is a build-time public-fixture differential probe for
high-volume `AssignmentState` legality and save/restore replay tests.

The CLI defaults to the frozen `feasibility` construction objective. The
experimental `--construction-objective individual-soft` mode minimizes the
exact negation of the native passenger-level `score_s + score_v + score_p`.
Group compactness (`score_c`) and BSCT interaction (`score_b`) remain final
scoring terms and are not represented as independent assignment coefficients.

The experimental `group-soft` mode preserves the complete `individual-soft`
solution as its Q0 incumbent. It then releases and jointly reconstructs one
group at a time with deterministic ordering, a 20,000-node DFS for groups of
at most four passengers, and bounded Beam search for larger groups. Placement
ordering uses the exact native `score_s + score_v + score_p + score_c`; every
complete candidate is checked with the native hard validator and full scorer,
including `score_b`, and is committed only when strictly better. Failure,
deadline, incompleteness, illegality, or a non-improving candidate returns Q0.
For `group-soft`, `q0_solver_status` reports the underlying MIP status while
the final `status` is `HeuristicComplete`; the heuristic result does not claim
global optimality for the complete group-aware objective.

The experimental `group-first` mode additionally builds an independent Q2A
candidate from fixed passengers and their required protected-seat resources.
It places whole groups in deterministic constrained-first order with the same
bounded DFS/Beam searches, and on a later placement failure makes one bounded
attempt to rebuild the immediately preceding group before retrying. The Q0
and Q1 assignments remain intact incumbents: Q2A is selected only when it is
complete, passes the native hard validator, and has a strictly better full
native score than Q1. Otherwise the Q1 result is returned unchanged.

The RR and restricted-master pipeline consists of:

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_restricted_master.cpp`
- `native_solver_pipeline.cpp`
- `native_pool_exporter.cpp`

The raw `group-first` path also runs the complete Rich stage sequence and
outer cabin decomposition. Detailed diagnostics are nested under
`cabin_decomposition.cabins.<cabin>.result` for multiple cabins; single-cabin
output keeps its existing fields. All named ACTIVE stages are native, but
Formal24 quality parity still fails (four regressions at `54f827c`). See the
root gate documents for component evidence and acceptance boundaries.

## Build

Windows x64, PowerShell, Visual Studio C++ tools, and an externally obtained
HiGHS 1.15.1 installation are required. No toolchain or prebuilt binary is
stored in this repository.

```powershell
powershell -ExecutionPolicy Bypass -File `
  native/build_native_solver_pipeline.ps1 `
  -HighsRoot $env:HIGHS_ROOT `
  -OutputDir build/native
```

The output directory is ignored by Git. Dependency version and checksum
requirements are documented in `docs/DEPENDENCIES.md`.

## Formal24 audit harness

`run_native_formal24.py` accepts all private inputs and frozen references as
explicit paths. Its `--reference` directory must contain
`rich_python_formal24.csv`, the frozen Rich raw audit, and the frozen native
feasibility summary/allocations in their recorded artifact layout. Example:

```powershell
python native/run_native_formal24.py `
  --corpus C:\private\formal24 `
  --reference C:\private\frozen-reference-root `
  --baseline C:\private\frozen-q0-output `
  --config C:\private\config.json `
  --executable build\native\seat_protect_cpp.exe `
  --output outputs\native-rich-q0 `
  --time-limit 60 --seed 0 `
  --construction-objective group-first
```

The output contains the full 24-case external legality/score audit, paired
comparison to frozen feasibility, and component means versus feasibility and
the frozen Rich run. Private inputs, benchmark outputs, and binaries remain
ignored and must not be committed.
