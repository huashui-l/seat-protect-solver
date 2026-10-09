# Dependencies

## Python

Python 3.11 is the supported baseline. Core source code uses NumPy, SciPy,
and Matplotlib. Install the common dependencies from `requirements.txt`.
The maintained column-generation and direct solver routes use
`gurobipy>=13.0`, installed from `requirements-column-generation.txt`, and
require a Gurobi license that supports the benchmark model sizes.

The initial import does not include GNN training code, so PyTorch and
scikit-learn are intentionally not core dependencies. Versions are not pinned
without a verified compatibility run.

## Native C++

Native builds currently require Windows x64, PowerShell, Visual Studio C++
tools, HiGHS 1.15.1 and Gurobi 13.0. Pass the extracted HiGHS directory explicitly through
`-HighsRoot`; the dependency is not stored in this repository. The previously
audited Windows archive had SHA-256
`26302d9024f307e09128a45a58898917287351dcf754c55aebc07742237f78bf`.

The C++ elite-pool integer master uses the Gurobi C API. Pass the installation
root through `-GurobiRoot` (the directory containing `include`, `lib`, and `bin`);
the build links `gurobi130.lib` and copies `gurobi130.dll` into the output directory.
A license supporting the model sizes is required at runtime. Set `GRB_LICENSE_FILE`
when the license is outside the standard search paths. License files are not
repository inputs and must not be committed.

The native HiGHS SDK remains an internal dependency for feasibility, protected
component/LNS subproblems, optional pricing LPs and historical tools. Its native ABI
target remains 1.15.1. Replacing the elite-pool master does not migrate those modules.

