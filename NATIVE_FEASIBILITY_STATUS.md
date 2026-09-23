# Native Feasibility Status

Status: **NATIVE-FEASIBLE-CONSTRUCTION PASS**. This is not yet `FULL-CPP-CORRECTNESS PASS`.

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
The latest full 60-second Formal24 run (`54f827c`) is complete/legal and
evaluator-consistent on 24/24 cases, but quality still fails: 20 improve,
0 tie, 4 regress against frozen Python. LNS solver errors are contained and
counted. Detailed results and the timed DFS boundary are in the Rich status ledger.
No profiling or 5-second compression is authorized yet.
