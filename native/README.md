# Active native solver sources

This directory contains the active C++ source closure and its PowerShell build
entry points. Historical frontier C++/GNN research status is preserved under
[`docs/research-status/`](../docs/research-status/frontier-cpp-gnn-roadmap.md).

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
