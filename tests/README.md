# Test tiers

- **Core runnable:** `test_config_and_scoring.py`,
  `test_fixed_seat_semantics.py`, `test_solution_comparison.py`,
  `test_general_validation_smoke.py`, and the
  case-contract test in `test_general_validation_cases.py`. Tests explicitly
  marked as requiring excluded benchmark data are skipped with that reason.
- **Native build required:** `test_native_smoke.py`. Set
  `SEAT_PROTECT_NATIVE_EXE` to the executable built from this checkout.
- **Formal24/manual:** `test_full_cpp_core_parity.py` and the frozen gate in
  `test_native_feasibility_solver.py`.
- **External artifact required:** the source-repository
  `test_native_deadline_fallback.py` was reviewed but not imported. Every test
  in it depends on legacy `.native_v2` or Formal24 artifacts, and the module
  imports a research audit helper outside this initial allowlist. No
  artifact-independent deadline-fallback test was available to migrate.

Formal24, training, long column generation, and research benchmarks are not
ordinary pull-request tests.

