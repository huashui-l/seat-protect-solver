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
output keeps its existing fields. All named ACTIVE stages are native. The latest
frozen Formal24 (`c6a41f3`) passes 24/24 completeness/legality/scoring checks,
with 21 improve / 0 tie / 3 regress. On 2026-09-24 the user accepted these small
regressions for migration completion; strict score nonregression remains failed.
See the root gate documents for evidence and known trajectory/timing boundaries.

## Independent 5-second Rich profile

Use `--construction-objective rich-fast --time-limit 5` with
`configs/config_native_rich_5s.json`. The `input_contract` seatmap paths are
relative to that config (`../data/...`); supply those external seatmaps or use
a local copy with absolute paths. Private Formal24 inputs are not bundled.

```powershell
./build/native/seat_protect_cpp.exe --input CASE.json `
  --config configs/config_native_rich_5s.json --output RESULT.json `
  --construction-objective rich-fast --time-limit 5 --seed 0
```

The short profile begins directly with fixed/SSR/caregiver Rich construction and
relocation repair, followed by VND, a bounded structured pattern pool and up to
two restricted MIP/local-branching attempts. Protected MIP, special pricing and
conflict LNS are disabled. Q0 individual-soft MIP runs only if construction/repair
is incomplete and time remains; Q1/Q2A do not run. A complete legal candidate is
accepted even when its score is negative and there is no previous incumbent.
All subsequent replacements still require a strict score improvement.

Nominal single-cabin stage budgets are construction 2.4s, repair 0.3s, VND 0.7s,
patterns 0.8s, restricted MIP 0.4s, with 0.4s scoring/overhead reserve. Existing
carry and incomplete-repair borrowing apply; cabin budgets scale these windows.
Per-group pattern DFS is capped at 0.02s, with four windows and six patterns per
group. The configuration targets a 5s invocation; measured process time must
still be checked because native setup and solver termination have overhead.

For single-cabin diagnostics, `q0_solver_status=NotRun` means the Q0 score and
Q0-relative delta fields have no baseline interpretation (their numeric defaults
are not an objective reference). `q1_selected_incumbent=NotRun` marks omitted
Q1/Q2A. For multiple cabins, inspect each nested result. Infeasible/incomplete
outputs retain nonzero exit status and cannot pass the Formal24 legality gate.

The accepted 60s route remains `group-first`. `configs/config_native_rich_60s.json`
preserves the external frozen `rich_python_reference_config.json` settings,
changing only relative seatmap paths for its location. The external original
remains untouched. Its original results remain in
`outputs/research/full_cpp_rich_60s_c6a41f3`; the exact old executable, HiGHS DLL
and config snapshot are also preserved in that directory's `runtime/` (ignored
local artifacts). The original config's relative seatmap paths still resolve
from the reference directory; the copied snapshot records provenance.
Use separate output directories for every benchmark. Frozen `d4b3379` Formal24
passes 24/24 complete/legal/evaluator-consistent checks with process mean/median/
max 1.247/1.052/2.784s; every case is below 5s. Mean score loss versus the saved
C++ 60s run is 24.154 points (mean normalized per-case loss 4.34%). All 24 scores
are lower; this profile is a speed/quality tradeoff. See the existing Rich status
ledger for per-case results, provenance and retained acceptance boundaries.

## Short profile with 4.5 seconds of search

`configs/config_native_rich_4p5s_search.json` uses the same `rich-fast --time-limit 5`
entry. Relative to the first 5s profile it enables native conflict-component LNS
and reserves 0.5s for scoring/overhead. LNS receives a 0.3s base budget plus
unused construction/repair/VND/pattern carry before restricted MIP, which keeps
0.1s instead of the earlier 0.4s base budget. Existing solve-count and stagnation limits remain,
so exhausted neighborhoods may still finish early. No artificial wait is added.
The preserved 60s `group-first` profile and archived results are unchanged.

This is the recommended short profile. Frozen `17727e4` Formal24 passes all
24 completeness/legality/evaluator checks; process mean/median/max is
4.292/4.493/4.550s, all below 5s. Against the first short profile: 20 improve,
4 tie, 0 regress, mean score gain 7.969. Average normalized score loss versus
C++ 60s is 2.87%. Exact results and the intermediate timing diagnosis are in
`FULL_CPP_RICH_REFERENCE_STATUS.md`.

```powershell
./build/native/seat_protect_cpp.exe --input CASE.json `
  --config configs/config_native_rich_4p5s_search.json --output RESULT.json `
  --construction-objective rich-fast --time-limit 5 --seed 0
```

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
