# Active native solver sources

This directory contains the active C++ source closure and its PowerShell build
entry points. Historical frontier C++/GNN research status is preserved under
[`docs/research-status/`](../docs/research-status/frontier-cpp-gnn-roadmap.md).

The raw JSON feasibility CLI consists of:

- `seat_protect_cpp.cpp`
- `full_cpp_solver_core.cpp/.hpp`
- `native_feasibility_solver.cpp/.hpp`
- `native_json.hpp`

The CLI defaults to the frozen `feasibility` construction objective. The
experimental `--construction-objective individual-soft` mode minimizes the
exact negation of the native passenger-level `score_s + score_v + score_p`.
Group compactness (`score_c`) and BSCT interaction (`score_b`) remain final
scoring terms and are not represented as independent assignment coefficients.

The RR and restricted-master pipeline consists of:

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_restricted_master.cpp`
- `native_solver_pipeline.cpp`
- `native_pool_exporter.cpp`

The remaining C++ files are build-time probes or benchmark utilities. Full C++
coverage of all Rich Python active stages remains incomplete; see the root gate
documents and the historical roadmap linked above.

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
  --config C:\private\config.json `
  --executable build\native\seat_protect_cpp.exe `
  --output outputs\native-rich-q0 `
  --time-limit 60 --seed 0 `
  --construction-objective individual-soft
```

The output contains the full 24-case external legality/score audit, paired
comparison to frozen feasibility, and component means versus feasibility and
the frozen Rich run. Private inputs, benchmark outputs, and binaries remain
ignored and must not be committed.
