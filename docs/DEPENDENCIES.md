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
tools, and HiGHS 1.15.1. Pass the extracted HiGHS directory explicitly through
`-HighsRoot`; the dependency is not stored in this repository. The previously
audited Windows archive had SHA-256
`26302d9024f307e09128a45a58898917287351dcf754c55aebc07742237f78bf`.

The native HiGHS SDK is an internal dependency of the C++ heuristic, separate
from the two Gurobi solver routes. Its native ABI target is fixed at 1.15.1.

