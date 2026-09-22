# Dependencies

## Python

Python 3.11 is the supported baseline for this initial import. Core source code
uses NumPy, SciPy, Matplotlib, and the Python HiGHS bindings. Install the common
dependencies from `requirements.txt` and column-generation support from
`requirements-column-generation.txt`.

The initial import does not include GNN training code, so PyTorch and
scikit-learn are intentionally not core dependencies. Versions are not pinned
without a verified compatibility run.

## Native C++

Native builds currently require Windows x64, PowerShell, Visual Studio C++
tools, and HiGHS 1.15.1. Pass the extracted HiGHS directory explicitly through
`-HighsRoot`; the dependency is not stored in this repository. The previously
audited Windows archive had SHA-256
`26302d9024f307e09128a45a58898917287351dcf754c55aebc07742237f78bf`.

Python `highspy` and the native HiGHS library are separate installations. The
native ABI target is fixed at 1.15.1; exact Python/native version equivalence
has not yet been established as a repository-wide requirement.

