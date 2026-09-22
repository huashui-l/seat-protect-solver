# Active native solver sources

This directory temporarily retains the existing path of the active C++ source
closure. Moving it to a dedicated native directory is intentionally deferred to
a separate change after build and parity baselines are stable.

The raw JSON feasibility CLI consists of:

- `seat_protect_cpp.cpp`
- `full_cpp_solver_core.cpp/.hpp`
- `native_feasibility_solver.cpp/.hpp`
- `native_json.hpp`

The RR and restricted-master pipeline consists of:

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_restricted_master.cpp`
- `native_solver_pipeline.cpp`
- `native_pool_exporter.cpp`

The remaining C++ files are build-time probes or benchmark utilities. Full C++
coverage of all Rich Python active stages remains incomplete; see the root gate
documents and `ROADMAP_AND_PROGRESS.md`.

## Build

Windows x64, PowerShell, Visual Studio C++ tools, and an externally obtained
HiGHS 1.15.1 installation are required. No toolchain or prebuilt binary is
stored in this repository.

```powershell
powershell -ExecutionPolicy Bypass -File `
  research/frontier_cpp_gnn/build_native_solver_pipeline.ps1 `
  -HighsRoot $env:HIGHS_ROOT `
  -OutputDir build/native
```

The output directory is ignored by Git. Dependency version and checksum
requirements are documented in `docs/DEPENDENCIES.md`.
