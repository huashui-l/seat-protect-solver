# Test tiers

- **Core runnable:** `test_config_and_scoring.py`,
  `test_fixed_seat_semantics.py`, `test_solution_comparison.py`,
  `test_general_validation_smoke.py`, and the
  case-contract test in `test_general_validation_cases.py`. Tests explicitly
  marked as requiring excluded benchmark data are skipped with that reason.
- **Gurobi required:** `test_cg_gurobi.py` (新列生成的穷举对照、公开算例与证明最优复现；无 `gurobipy` 时整体跳过).
- **Native build required:** `test_native_smoke.py`. Set
  `SEAT_PROTECT_NATIVE_EXE` to the executable built from this checkout.
- **Formal24/manual:** `test_full_cpp_core_parity.py` and the frozen gate in
  `test_native_feasibility_solver.py`.
- **External artifact required:** the source-repository
  `test_native_deadline_fallback.py` was reviewed but not imported. Every test
  in it depends on legacy `.native_v2` or Formal24 artifacts, and the module
  imports a research audit helper outside this initial allowlist. No
  artifact-independent deadline-fallback test was available to migrate.

The shared 24-case Formal24 plus 16-case D4 benchmark is driven by
`scripts/benchmark_three_routes.py`. Its default schedule is the C++ native
heuristic at 5 seconds, each Gurobi route at 150 seconds, and a 600-second
supplement only for exact runs without an optimality or infeasibility
certificate.

For runs collected in separate batches, verify and merge the source reports
without discarding their individual provenance:

```powershell
python scripts/merge_three_route_benchmarks.py `
  --sources CPP_5S.json CG_150S.json CG_600S.json DIRECT_150S.json DIRECT_600S.json `
  --json-output outputs/three_routes.json `
  --markdown-output outputs/three_routes.md
```

The merger checks input/configuration hashes, all 40 cases, route budgets,
duplicate or failed runs, and the conditional 600-second coverage.

Formal24, training, long column generation, and research benchmarks are not
ordinary pull-request tests.

