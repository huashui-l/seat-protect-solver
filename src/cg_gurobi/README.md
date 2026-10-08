# `src/cg_gurobi` —— Gurobi 直接定价列生成

从零实现的 Dantzig–Wolfe 列生成，只依赖两份"建模信息"：

- `docs/任务需求、数学模型与实现说明.md` 第 3–6 章（集合、硬约束、软目标、MP1/MP2 模型、证书口径）；
- `src/allocation_evaluator.py`（座位拓扑、单旅客软分、紧凑度、婴儿影响、硬约束判定、陪护者定义）。

归档在 `archive/legacy_column_generation/` 的旧实现只保留给启发式的结构化模式生成和历史复现，本包不复用其任何算法结构。

## 模块

| 文件 | 内容 |
|---|---|
| `instance.py` | 输入 → 集合/参数/可行域 D_p；固定座位与分舱容量校验（§4.4、§4.10）；个人软分 h_ps、婴儿系数 B(u,v) 直接调用评估器 |
| `pricing.py` | MP2：每组一个持久化 Gurobi MIP，完整旅客—座位—保护资源域；陪护、单双侧保护、条件 SSR 激活、紧凑度精确线性化、同组婴儿修正；`Cutoff=-tol` 证"无负列"；`Column` 携带全部主问题系数并用评估器函数独立计算成本 |
| `master.py` | MP1：Gurobi LP。行：组凸性 =1、座位资源 ≤1、条件 SSR 激活/人数、婴儿 b_uv ≥ I_u + O_v − 1；两阶段人工列；列池整数 MIP |
| `branching.py` | 分支定价：按"组 g 是否占用座位 s"二分；节点内完整列生成；未认证节点用安全界 |
| `solver.py` | 主循环、分舱独立求解与严格合并、初始列（项目启发式或贪心）、潜水、升级未认证组、可选对偶平滑/早停 |

## 证书口径

| 字段 | 含义 |
|---|---|
| `lp_bound` / `lp_bound_certified` | 根节点完整列空间 LP 上界。只有某轮**所有组**的定价均 Optimal/Cutoff 且无负列时才置真 |
| `safe_bound` | L_RMP + Σ min(0, lb_g) 取负；lb_g 来自 Gurobi ObjBound，任一组无有限下界则为空 |
| `bp_bound` / `proven_optimal` | 分支定价后的全局界；所有节点认证且前沿为空才 `proven_optimal` |
| `total_score` | 整数解经独立评估器复核：硬违规不为 0 的候选不会被采用（退回启发式方案并在 `integer_status` 标记 fallback）；软分与列成本不一致时抛错 |

## 运行

```bash
.venv/bin/python -m src.cg_gurobi --groups case.json --old-seatmap old.json --new-seatmap new.json \
    --time-limit 180 --pricing-time-limit 60 --output result.json
.venv/bin/python -m unittest tests.test_cg_gurobi -v
.venv/bin/python scripts/benchmark_cg_gurobi.py --instances DIR --old-seatmap old.json --new-seatmap new.json --time-limit 150 --output report.md
```

The active column-generation route requires `gurobipy>=13.0` and a Gurobi
license that can hold the generated master and pricing models. The archived
HiGHS column-generation implementation is not an active dependency.

配置在 `config.json` 的 `cg_gurobi` 节（缺省见 `solver._cfg`）：`total_time_limit`、`pricing_time_limit`、`integer_time_limit`、`pricing_workers`、`warm_start`（heuristic/greedy/none）、`dive`、`branch_and_price`、`bp_node_limit`、`dual_smoothing_alpha`、`pricing_early_stop`。

## 验证

- `tests/test_cg_gurobi.py`：6 个语义场景穷举对照（U ≥ 全局最优、I = 全局最优）；11 个公开算例完整合法、成本与评估器一致、旧实现已证 LP 值逐位复现、两个满载算例证明最优；无效输入被拒绝。
- `reports/audit-2026-09-23/benchmark/`：启发式 / 旧列生成 / 新列生成三方对比。
