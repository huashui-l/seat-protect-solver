# Test tiers

测试数据的构建规则和维护流程见[测试用例构建与维护说明](../docs/test-case-construction.md)；本文只说明测试层级和运行边界。

业务覆盖、基准公正性和保留/新增建议见[测试体系评估](../docs/test-case-assessment-2026-10-07.md)。可用 `python scripts/audit_test_case_coverage.py` 重建输入覆盖明细；该命令不运行算法。新版 72 规格开发集由 `scripts/generate_benchmark_v2.py` 生成，清单契约由 `test_benchmark_v2_spec.py` 校验。

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

Formal24, training, long column generation, and research benchmarks are not
ordinary pull-request tests.
