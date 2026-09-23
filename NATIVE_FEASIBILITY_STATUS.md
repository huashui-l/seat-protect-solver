# Native Feasibility Status

Status: **NATIVE-FEASIBLE-CONSTRUCTION PASS; native Rich migration ACCEPTED with user-approved quality exceptions (2026-09-24)**. The original strict quality gate remains failed.

- production path: raw case JSON + config + seatmaps -> native domains/constraints -> native complete incumbent -> native validation/score -> JSON result
- `.native_v2` production dependency: none; retained only for legacy regression
- Formal24: `24/24` complete, `0` unassigned, `0` native hard violations, `0` external hard violations
- evaluator consistency: `24/24`; maximum native/external score error `3.183231456205249e-12`
- production Python callback count: `0`; Python is used only by the benchmark harness for external audit
- provenance: input/config/binary SHA-256, commit `6252bd7e2cad551d0f6bef7ea9467ca7dd349734`, MSVC `19.40.33813`, `/O2`, seed `0`, 60-second feasibility limit
- quality record only: mean/median gap to the current best-known integer reference `188.235% / 182.872%`; this intentionally untuned feasibility solution is not a Rich-quality result

The raw Rich production path now contains native construction/repair, VND,
structured and special pricing, protected MIP, conflict LNS, restricted MIP,
native validation/serialization, and the enabled outer cabin decomposition.
The latest full 60-second Formal24 run (`c6a41f3`) is complete/legal and
evaluator-consistent on 24/24 cases: 21 improve, 0 tie, 3 regress against frozen
Python, with zero total-score error. The user accepted these small regressions
as nonblocking for migration completion; the strict benchmark verdict is unchanged.
LNS solver errors are contained and counted. Detailed results, acceptance and
known timing boundaries are in the Rich status ledger. Profiling and 5-second
compression remain outside this migration scope.
