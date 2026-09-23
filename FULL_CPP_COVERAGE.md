# Full C++ Coverage Gate

> The Formal24 evidence paths cited here are provenance references to the
> internal research archive. The data, binaries, and result artifacts are not
> distributed in this repository.

Current status: **NATIVE Rich pipeline implemented; FULL-CPP-CORRECTNESS remains in progress**.

This file is a gate ledger, not a declaration of completion.

| Production stage | Native now | Evidence / remaining work |
| --- | --- | --- |
| native CLI over solver-ready `HEADER_V2` | yes | `native_solver_pipeline.cpp` supports `--input`, `--output`, `--time-limit`, `--master-time-limit`, `--schedule`, and deterministic `--seed 0` |
| raw official JSON/config input | yes | `seat_protect_cpp.exe --input raw_case.json --config ...` executes the raw-native path; `.native_v2` is not read or generated |
| topology and immutable indices | yes | native constructs row/subrow indices, coordinates, cross-aisle boundaries, seat attributes, and both adjacency relations directly from the seatmap |
| fixed/reserved preprocessing | yes | native validates fixed-seat identity/uniqueness, SSR seat rules, deterministic two-sided blocks, single-side availability, caregiver prerequisites, and conditional SSR resources |
| mutable assignment state | yes | indexed native state operations, snapshots, rollback, resource ownership, and Rich stages |
| complete per-passenger legal domain | yes for feasibility | native domains include cabin, fixed seat, SSR eligibility, exit/bassinet/aisle, and protection topology; caregiver and conditional SSR interactions are native joint constraints |
| complete feasible construction without supplied incumbent | yes | native Q0 plus Rich construction/repair and enabled cabin decomposition |
| RR/Beam pattern generation | yes | native and active |
| restricted master | yes | native and active |
| optional production memory | no | current reconstruction callback is Python |
| native score from raw assignment | yes | individual distance/value/preferences, group compactness, and BSCT contribution match the external evaluator; Formal24 max error `3.183231456205249e-12` |
| native final raw-semantic validation | feasibility version | checks completeness, uniqueness, static eligibility, fixed seats, protection-resource matching, caregiver adjacency, and conditional SSR isolation; Python evaluator remains the external audit |
| serialization | yes | native JSON |

For the raw feasibility CLI, Python on the production hot path is zero. Full Rich active-stage coverage is not yet 100%, so this is not `FULL-CPP-CORRECTNESS PASS` and is not eligible for formal profiling or 5-second compression.

## Native feasible-construction Gate

The frozen raw-native Formal24 result is in `outputs/research/native_feasibility_formal24/summary.json`: `24/24` complete, zero unassigned, zero native/external hard violations, `24/24` evaluator consistent, and maximum native/external score error `3.183231456205249e-12`. The benchmark records input/config/binary SHA-256, commit, compiler, build mode, seed, and time limit. Quality is deliberately not a Gate condition at this stage.

## Raw-core differential evidence

`tests/test_full_cpp_core_parity.py` runs the native raw-core probe over all 24 official cases. It compares every indexed passenger and group field plus every seat's immutable attributes, row/subrow position, coordinates, and exact neighbor lists against the Python reference. It also compares Python/C++ rejection on five invalid fixed-seat cases and exercises native state assign/remove/save/restore on every valid case. Current result: `2/2` tests passed, `24/24` valid cases matched with state round trips, and `5/5` invalid cases were rejected by both implementations.

This closes the standalone raw problem/topology/fixed-preprocessing semantics and establishes the mutable state foundation, not the end-to-end gate: complete legality/domain generation, scoring, construction/repair, Rich improvement stages, internal validation, and raw-result serialization remain to be migrated and integrated.

## Preprocessed-input regression (not the full-C++ Gate)

The named CLI was replayed on all 24 frozen generator-v3.2 `.native_v2` inputs with a zero search budget, forcing the audited incumbent/fallback path. Results were `24/24` complete, `24/24` zero hard violations, and `24/24` external-score consistent; maximum absolute score error was `6.821210263296962e-13`.

This verifies the file-based native entry point and preserves the supplied incumbent. It does **not** close raw preprocessing, construction, repair, internal validation, or Python-hot-path coverage, so it is deliberately not reported as `FULL-CPP-CORRECTNESS = PASS`.
