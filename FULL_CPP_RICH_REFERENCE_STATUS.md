# Full C++ Rich Reference Status

Status: **NATIVE-FEASIBLE-CONSTRUCTION PASS; FULL-CPP-CORRECTNESS remains in progress.**

## Rich Python

- selected budget: 60 seconds
- active stages: raw preprocessing, fixed/reserved assignment, SSR/caregiver construction and rescue, small DFS/Beam construction, relocation repair, VND 1-opt/2-swap/3-cycle/group/caregiver rebuild, structured and special pattern generation, protected multi-group MIP, conflict-component LNS, restricted pattern MIP, native-equivalent final validation/scoring semantics
- Formal24 mean / median gap: `2.7649% / 1.7361%`
- wall mean / median / P90 / max: `49.745 / 55.150 / 58.976 / 59.794s`

## Migration

The raw-native feasibility path now includes parsing, indexed problem/topology, fixed preprocessing, native legal domains and joint hard constraints, complete-incumbent construction, native feasibility validation, scoring, and serialization. Rich construction ordering, DFS/Beam, repair, VND/LNS/pattern stages, and Rich master semantics remain partial or Python-only. The raw feasibility production path has zero Python callbacks, but Rich active-stage native coverage is not yet 100%.

The M1/M2 migration branch now also contains a native Rich construction entry,
configuration-driven DFS/Beam limits, bounded direct repair, VND 1-opt/2-swap/
3-cycle moves, and bounded two-group bitmask rebuild. These stages require a
complete legal incumbent, use the shared deadline, and accept only strict score
improvements. They are implementation progress, not a Rich quality-parity gate;
the Python relocation-chain repair, caregiver rebuild, pattern stages, LNS, and
restricted/protected master semantics remain outstanding.

## Correctness

- Rich Python Formal24 complete: `24/24`
- unassigned: `0`
- hard violations: `0`
- evaluator consistent: `24/24`
- maximum score error: `0`
- raw-native Formal24 feasibility: `24/24` complete, `0` unassigned, `0` native/external hard violations
- native raw-core parity: `24/24` valid cases matched with assignment-state round trips; `5/5` invalid fixed-seat fixtures rejected consistently
- native/external score parity: maximum absolute error `3.183231456205249e-12`

## Quality parity

Not yet eligible. The feasibility solver's mean/median gap is `188.235% / 182.872%`, recorded only to expose the expected quality deficit. Rich active stages have not yet been migrated, so this result must not be compared as Rich-quality parity.

## Timing and profile

The earlier 1.668-second native-core smoke remains a solver-ready `.native_v2` test, not a Full C++ performance result. Native profiling is intentionally deferred until raw JSON, Rich active-stage coverage, correctness, and quality parity pass.

## Required answer

Current Full C++ does **not** yet inherit all proven valuable 20s+ Rich Python search capability. The raw feasibility path now has zero runtime Python callbacks, but 100% Rich active-stage native coverage remains unmet.
