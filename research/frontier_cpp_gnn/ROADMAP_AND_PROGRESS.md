# GNN-Guided 5s Seat Protection Solver：路线与进度

> 本文件保留内部研究路线和证据索引。其中的`outputs/`、`archive/`、GNN权重、
> Formal24和其他实验产物未进入本协作仓库；相关路径不是克隆后的默认运行依赖。

最后更新：2026-09-11

本文件是 `frontier_cpp_gnn` 研究路线的进度入口。每次实验或实现完成后，
应同步更新状态、结果、问题、解决方法和证据路径。

## 状态说明

- `DONE`：实现和对应验证均已完成。
- `EXPERIMENTAL`：已有原型或结果，但未达到晋级条件，不能接入在线求解器。
- `BLOCKED`：存在已确认的阻碍，需要先解决记录的问题。
- `TODO`：尚未开始。
- `RETIRED`：实验路线已证明收益不足，不再作为主线。
- `PAUSED`：研究结论保留，但因基础设施或数据语义变更暂不继续实验。

## 正式目标与边界

目标不是让 GNN 端到端分配座位，而是让它在有限时间内优先搜索高价值的
**合法候选座位、完整同行组模式和修复邻域**。

正式验收条件：

- [ ] 24/24 官方实例完整分配。
- [ ] 24/24 官方实例硬约束违规为 0。
- [ ] 24/24 官方实例 `solver_wall_time` 不超过 5 秒。
- [ ] 24/24 官方实例与当前列池整数参考解 `I` 的差距不超过 2%。
- [ ] 独立 evaluator 复算的 soft score 与求解器报告一致。

时间口径锁定为：

```text
solver_wall_time = 输入解析 + 预处理 + 求解 + 内部必要合法性检查 + 结果序列化
```

外部独立 evaluator 是验收工具，不计入正式 5 秒，但必须同时报告
`audited_end_to_end_time`，防止隐藏审计和数据转换成本。C++ 无 GNN 核心版本的
工程目标是 worst `solver_wall_time <= 4.5s`，为模型、系统抖动和 fallback
保留余量；GNN 在线预算目标为 50～100ms。

当前分数 `F` 通常为负数且越大越好，统一使用：

```text
gap_I = (F_I - F_candidate) / max(abs(F_I), epsilon)
```

`I` 是当前列池上的 best-known integer solution，不称为全局最优解。

工程边界：

- 当前 Python H5 不作为新路线的修改基础。
- 在线主线是把 H20 思路重写为独立 C++ 求解器，再压缩到 5 秒。
- GNN 只排序已经通过硬约束生成器的候选。
- incumbent、固定座位模式、唯一可行模式不能被 GNN 剪掉。
- GNN 即使不直接生成非法模式，错误排序仍可能通过限量列池和截止时间间接导致
  无完整解或低质量解，因此必须使用 handcrafted/GNN/diversity Top-K 并集和
  硬保留规则。
- 最终方案必须经过独立 evaluator。

## 三个可证伪的研究假设

- `H1 Pattern Value`：有限候选预算没有优先保留具有高全局价值的模式；由
  P5 Pattern Regret 和 P6 Pattern Ranker V2 验证。
- `H2 Exchange Structure`：即使单组已有好模式，整数改善仍依赖多组交换关系；
  由 P6.5 Exchange Component Discovery 验证。
- `H3 Runtime`：C++ 重写能够释放足够预算，在 5 秒内执行高级模式搜索、联合
  repair 和 restricted master；由 P8 验证。

最终结果取决于三项共同成立：

```text
C++ speedup + better pattern selection + component-guided joint repair
```

## 总体进度

| 阶段 | 状态 | 当前结论 | 下一晋级条件 |
|---|---|---|---|
| P0：5 秒基线与 `I` 差距 | `DONE` | 已建立 24 例合法基线 | 保持为所有实验的固定对照 |
| P1：训练语料分层与保护 | `DONE` | Gold/Silver 管线可用，困难整例 CG 默认拒绝 | 增加局部精确标签 |
| P2：Group 中心预测 GNN | `RETIRED` | 有一定行区域预测能力，但不能闭合全局交换链 | 不再作为主要标签和指标 |
| P3：C++ 合法模式生成 | `EXPERIMENTAL` | 生成快、独立审计合法、候选多样性有效 | 与排序器及 C++ master 形成 5 秒闭环 |
| P4：Pattern Ranking V1 | `EXPERIMENTAL` | Top-5 有增益，Top-1 尚未超过手工排序 | 使用 regret 标签训练 V2 |
| P5：Pattern Regret 数据 | `EXPERIMENTAL` | 12 实例 Gate A 通过；覆盖仍偏向优先组 | 扩展 Gold/Hard/OOD 和每组候选覆盖 |
| P6：Pattern Ranker V2 | `TODO` | 尚未实现 | 困难留出 Top-K 和 restricted master 均改善 |
| P6.5a：Heuristic Exchange Graph | `EXPERIMENTAL / STOPPED` | v3.2 Gate=`GENERATION LIMITED / component construction limited`；97.6%可观察但frozen candidate-set上限接近C1 | `P6.5a-1 Targeted Component Expansion Diagnostic`（`NOT STARTED`） |
| P6.5b：Learned Component Predictor | `TODO` | 有条件启动 | 启发式组件有效后，学习选择更高价值组件 |
| P7：异构图/多任务 Head | `TODO` | 暂不优先 | Pattern V2 证明最终求解收益后再实施 |
| P8：C++ H20→5s 求解器 | `EXPERIMENTAL` | RR 已晋级；post-Pass-0 sampled task regret 61/64 为0且无可解释在线特征 | 保持 RR baseline；停止 scheduler 深化，转回 Pattern Value / Exchange Structure |
| P9：GNN 在线集成 | `TODO` | 尚未实现 | 相同候选预算下稳定优于无 GNN 版本 |
| P10：最终 24 例验收 | `TODO` | 尚未达到 2% 目标 | 24/24 gap 到 `I` 不超过 2% |

## 已完成内容

### P0：基线与瓶颈分析 — `DONE`

当前 Python H5 官方 24 例基线：

- 24/24 完整合法；
- 平均 gap 到 `I` 约 6.98%；
- 中位 gap 约 4.83%；
- 仅 6/24 在 2% 内；
- 最大运行时间约 4.78 秒。

主要瓶颈不是 restricted master 本身，而是候选池缺少能够闭合 24～32 组
长交换链的兼容模式，尤其涉及大组、SSR 和保护空座资源时更明显。

证据：

- `outputs/benchmarks/frontier_5s_training_baseline_2026-08-27.json`
- `outputs/benchmarks/frontier_5s_training_allocations_2026-08-27/`

### P1：训练语料与标签保护 — `DONE`

已实现：

- 隔离式数据生成器；
- `--max-group-size` 课程上限；
- H5、H20、column-pool 三类标签；
- 标签质量和训练权重；
- 默认拒绝大于 100 座位需求或大于 7 人组的整例 column-pool 标注；
- H5/H20 不完整解记录为 `failed_incomplete` 并排除。

当前权重约定：

```text
proved column-pool integer optimum = 1.00
unproved column-pool feasible       = 0.60
H5/H20 anytime pseudo-label        = 0.25
```

已确认问题：`edge/stress + 150/full + 9～10 人组` 的整例列生成可能耗时极长。

解决方法：

- 小规模、组规模不超过 6/7 的实例用于 Gold 精确标签；
- 困难整例仅生成低权重 Silver 轨迹；
- 后续对困难实例提取小冲突邻域，做局部精确 oracle，而不是整例 CG。

相关文件：

- `generate_training_corpus.py`
- `label_training_corpus.py`
- `build_group_graph_dataset.py`

### P2：Group 中心预测 GNN — `RETIRED`

最好 mixed-pilot 困难留出结果：

```text
center within ±2 rows         = 65.27%
special group within ±2 rows = 70.54%
inference                    = 5.60 ms
```

增加 23 对困难 H5/H20 标签后结果不稳定：

```text
overall holdout = 64.07%～67.07%
special holdout = 68.75%～70.54%
```

问题：

- 中心行标签不能表达完整资源组合；
- H5/H20 在时间截止边界存在轨迹非确定性；
- 更多同分布伪标签没有稳定提升特殊组表现。

结论：中心预测可作为 pattern generator 的一个提示，但不再作为主模型或主要
验收指标。

证据：

- `outputs/research/group_gnn_mixed_pilot.metrics.json`
- `outputs/research/group_gnn_hard_v1.metrics.json`

### P3：C++ Native Pattern Kernel — `EXPERIMENTAL`

已实现：

- C++17 bitset 资源冲突检查；
- caregiver、SSR flag/location 和保护座位资源处理；
- Beam 搜索、多个行提示和确定性排序扰动；
- true score 与 dual-guided rank score 分离；
- Python 独立合法性和软分审计；
- restricted exact master 组合测试。

24 例 pattern corpus：

```text
legal patterns = 15,126
invalid patterns = 0
C++/Python score mismatches = 0
```

已确认问题：在 `forward/full_stress` 上，更多模式改善了 restricted LP，
但没有改善 restricted integer incumbent。这说明候选多样性有所增加，但仍未闭合
关键整数交换链。

解决方向：

- 不继续盲目扩大 beam/variant；
- 使用 Pattern Ranker 和 regret 识别全局价值模式；
- 针对大组/特殊组构造局部冲突邻域 oracle。

相关文件：

- `native_pattern_kernel.cpp`
- `export_native_pattern_input.py`
- `generate_native_pattern_corpus.py`
- `evaluate_native_patterns.py`
- `solve_native_pattern_master.py`

### P4：Pattern Ranking V1 — `EXPERIMENTAL`

数据：24 个官方实例、15,126 个完整合法模式；五个困难实例按完整 instance
留出：

```text
forward:100_edge
forward:150_edge
forward:full_stress
reverse:100_edge
reverse:full_edge
```

残差排序器困难留出结果：

```text
model Top-1 quality             = 0.4088
local-score Top-1 quality       = 0.4181
current-similarity Top-1        = 0.4056

model Top-5 oracle recall       = 70.19%
local-score Top-5 oracle recall = 67.70%

model exact-I Top-5 recall      = 82.43%
local-score exact-I Top-5       = 78.38%
```

判断：模型有 Top-K 补充价值，但 Top-1 尚未超过手工 local score，不能独立替换
手工排序，也不能接入在线求解器。

已确认问题：

- 当前 quality 只是 `I` 旅客分配一致度和座位集合重合度的组合，不是真正全局价值；
- 纯学习排序器训练集过拟合；
- Python 批量推理尚未代表最终 C++ 在线延迟；
- `I` 中未选择的近等价模式会被当前标签低估。

临时解决方法：

```text
handcrafted Top-K
UNION GNN Top-K
UNION diversity Top-K
→ restricted master
```

在 Pattern Regret V2 完成前，不允许 GNN 单独删除候选。

证据：

- `outputs/research/pattern_ranking_dataset.json`
- `outputs/research/pattern_ranker_residual.metrics.json`
- `build_pattern_ranking_dataset.py`
- `train_pattern_ranker.py`

## 下一步待办

### P5：Pattern Regret 数据 — `EXPERIMENTAL`（最高优先级）

- [x] 定义统一 label schema：instance、state、group、pattern、teacher quality。
- [x] 单实例汇总 H5、native kernel 和 `I` 的合法公共模式列池。
- [x] 单实例求解基准 restricted integer master，记录 `I_pool`。
- [x] 对试验组中 `I_pool` 选中模式执行 delete-one regret。
- [x] 对试验组中采样的未选模式执行 force-one regret。
- [x] 实现 restricted-pool 必要条件预筛，预筛通过不误报为可行。
- [x] 实现公共池冻结前的有界 companion-pattern ejection-chain 扩展。
- [ ] 每组只标注 8～20 个候选，禁止对全部模式逐一重解。
- [ ] 优先采样大组、特殊组、低 reduced-cost、模型分歧和资源多样模式。
- [x] 记录 MIP 状态、time limit、是否证明最优、regret 下界/实际值和置信权重。
- [x] 单实例独立审计全部 teacher solution。
- [x] 基准过期时自动提高求解强度、重求 `I_pool` 并重算当前实例全部标签。
- [x] 增加完整 `pattern_catalog`，保留 assignments、保护资源、SSR 系数和
  master cost，确保 regret 能连接到 Ranker 特征。
- [x] 增加冻结公共池 fingerprint 和 generation config，禁止把不同公共池当作
  同一 teacher state 的重复样本。
- [x] 生成第一份可连接的五实例 pilot ranking dataset。
- [ ] 扩展完整 Gold/Hard/OOD 实例并正式判断 Gate A（当前 12 例通过，另有
  `forward:full_stress` 因可比较候选不足未通过）。

Regret 必须保留两个不同语义，禁止直接混成一个标量：

```text
delete_regret = F(I_pool) - F(I_pool without selected k)
                数值越大，已选模式越关键

force_regret  = F(I_pool) - F(I_pool with candidate k forced)
                数值越小，未选模式越有竞争力
```

统一排序 utility 可对未选模式使用 `-force_regret`；`delete_regret` 用作当前
incumbent 模式的重要性目标或样本权重。强制后不可行必须记录独立状态，不能用
无穷大污染回归标签；MIP 超时必须记录界、gap、状态和低置信权重。

P5 数据管线硬规则：

```text
统一公共 column pool
→ 求解并验证 I_pool
→ 生成 delete/force regret
→ 任一 force-one 得到 F(new) > F(I_pool)
→ 基准过期，更新 I_pool
→ 当前实例已有 regret 标签全部失效并重算
```

负 `force_regret` 不作为有趣标签保留，它表示 teacher 基准过期。

建议标签：

```text
delete_regret
force_regret
pairwise_preference
reduced_cost
seat_dual_targets
teacher_confidence
```

### P6：Pattern Ranker V2 — `TODO`

- [ ] 主目标改为 pairwise/listwise regret ranking。
- [ ] Reduced cost 和 seat dual 只作为辅助损失。
- [ ] 对 hard case、大组和特殊组提高采样权重。
- [ ] 使用完整实例级 train/validation/test 划分。
- [ ] 增加 IID、Hard、OOD 三类测试集。
- [ ] 与 local score、H5 similarity、线性融合和 V1 残差模型对比。
- [ ] 评估 Top-1、Recall@5/16/32、NDCG 和 exact-I recall。
- [ ] 用 Top-K 候选实际运行 restricted master，报告最终分数，不只报告网络指标。

晋级条件：

- 困难留出集 Top-K 指标稳定超过所有手工基线；
- restricted master 在相同列数和时间预算下稳定改善；
- 任何实例均不降低候选池可行性；
- 多 seed 训练结论一致。

### P6.5：Exchange Component Discovery — `TODO`

#### P6.5a：Heuristic Exchange Graph

- [ ] 构造组间依赖边：pattern seat overlap、release-demand dependency、
  protected-seat interaction、SSR 稀缺资源竞争、H5↔I co-change、历史 LNS
  联合改善和高 dual 共享资源。
- [ ] 从潜在长链中截取重叠的 4～10 组组件。
- [ ] 对组件生成高价值 pattern 并运行 small restricted MIP。
- [ ] 改善后接受方案、重新构图，再选择下一个重叠组件。
- [ ] 报告 `ImprovementRate`、`delta_F/ms` 和 `ComponentMIPSuccessRate`。

禁止第一版直接求一个 24～32 组的大 MIP。长链应通过多个重叠小邻域逐步传播：

```text
A0 → component C1 → A1 → rebuild graph → component C2 → A2 → ...
```

#### P6.5b：Learned Component Predictor

只有 P6.5a 在相同时间预算下稳定改善整数解后才启动。模型预测 jointly rebuilt
概率或 destroy score；如果启发式组件本身无收益，不训练该模型。

### Go/No-Go Gates

#### Gate A：Regret teacher 是否可信

- 同一 `(instance, group)` 重复求解的 pairwise preference agreement ≥90%；
- Top-5 overlap ≥80%；
- `abs(R_i - R_j) < epsilon_regret` 的候选标记为 tie，不计排序错误；
- teacher solution 合法率 100%；
- 超时、不可行、未证明和基准更新均有独立状态；
- regret 与实际列池贡献有稳定相关性。

Gate A 未通过：先修复列池、MIP 稳定性和标签协议，不进入复杂模型训练。

#### Gate B：标签是否值得使用复杂模型

固定数据 split，依次比较：

```text
handcrafted → linear/logistic → GBDT/LightGBM → MLP → Group-GNN → Heterogeneous GNN
```

最终选择依据是 `integer solution gain / added online time`，而不是必须让 GNN
获胜。GNN 是候选技术，不是项目成功条件。

当前 12 实例 leave-one-instance-out 仍是诊断试验：MLP 的 Top-1 excess regret
最低；local master cost 的 Top-3 oracle recall 和 pairwise accuracy 最高。
学习信号存在，但尚不支持模型单独替换规则。Gate B 只允许继续验证
`local-cost UNION MLP/GNN Top-K`；在 restricted-master 等预算 A/B 前不晋级。

#### Gate C：Pattern 模型唯一正式晋级门槛

固定相同时间、总列数、每组列数、master 时间、incumbent 和随机种子，对比：

```text
handcrafted
handcrafted + diversity
handcrafted + model
handcrafted + model + diversity
```

正式判断只看完整实例最终整数解。困难五例目标为至少 `4 improve / 1 tie /
0 regress`，且多训练 seed 结论一致。Recall、NDCG 和 exact-I Top-K 仅作诊断。

#### Gate D：何时停止升级 Pattern 模型

如果 ranking 指标明显提高但相同预算下 restricted integer 基本不动，停止堆叠
更深 GNN、attention 或 heterogeneous transformer，转向 P6.5 交换组件发现。

### P7：异构图与多 Head — `TODO`

- [ ] Passenger、Seat、Group 三类节点。
- [ ] `belongs_to`、`feasible`、`physical_neighbor`、`same_subrow`、
  `same_row`、`front_back` 边类型。
- [ ] Seat topology 静态 embedding 预计算。
- [ ] Pattern Head 先复用静态 embedding。
- [ ] Pattern V2 有最终求解收益后，再增加 Candidate、Seat Dual 和 LNS Head。
- [ ] 不在每个 DFS 节点完整执行 GNN forward。

### P8：独立 C++ H20→5s 求解器 — `EXPERIMENTAL`

- [x] 与 P5/P6 交错推进，不等待完整模型训练结束。
- [x] 只读梳理 H20 五困难例各阶段时间和边际收益。
- [x] 定义逐阶段计时和诊断返回字段草案。
- [x] 固化 C++ pattern/master 数据协议。
- [x] 实现 C++ `PatternRecord` v1，并通过 Python `ExactPattern` round-trip 差分。
- [x] 实现固定列池 C++ restricted master，并与 Python master 做三实例差分。
- [x] 把 C++ 合法 pattern 生成和 restricted master 合并为单一在线进程。
- [ ] 静态座位拓扑和可行域缓存。
- [x] 保留 incumbent，并通过 24 例 incumbent-only 合法性回归。
- [x] 完成 24 例 no-GNN 5/10/20s 与五困难例 60s anytime benchmark。
- [ ] 无 GNN 条件下先实现 24/24 合法、worst `solver_wall_time <=4.5s`。
- [ ] 单独报告 `audited_end_to_end_time`。
- [ ] 再接入 Pattern Ranker，进行完全相同预算的 A/B 测试。

双工作流汇合点：

```text
Offline: P5 regret → simple baselines → P6 Pattern Ranker V2 ─┐
                                                              ├→ Master A/B → P9
Online:  H20 profiling → C++ protocol → no-GNN C++ <=4.5s ────┘
```

### P9：在线 GNN 集成 — `TODO`

- [ ] 导出轻量模型或将 MLP/GNN 权重直接加载到 C++。
- [ ] 一次批量评分，禁止在每个 DFS 节点重复全图 forward。
- [ ] 合并 handcrafted/GNN/diversity Top-K。
- [ ] 记录 GNN encoding、scoring、pattern generation、MIP 和 audit 时间。
- [ ] 设置自动回退：模型异常或超时则使用纯 C++ 手工排序。

### P10：最终验收 — `TODO`

- [ ] 固定构建模式、CPU/GPU、线程数和随机种子。
- [ ] 先跑五个困难实例，再跑完整 24 例。
- [ ] 每个实例独立验证完整性、硬约束和软分。
- [ ] 报告 median、mean、P90、worst gap，但正式门槛仍为 24/24 ≤2%。
- [ ] 至少重复 3 次，报告 wall time 和 gap 的波动。

## 当前问题台账

| ID | 状态 | 问题 | 原因判断 | 当前处理/解决方向 |
|---|---|---|---|---|
| R-001 | 已规避 | hard 150/full 整例 CG 标签过慢 | 9～10 人组和稀缺资源使完整定价困难 | Gold 小规模精确标签 + hard 局部 oracle |
| R-002 | 未解决 | 5 秒 H5/H20 轨迹非确定 | 截止时间、搜索顺序和求解器时序影响 incumbent | 只作低权重 Silver；固定线程/seed；改用 regret |
| R-003 | 未解决 | 更多 native patterns 只改善 LP、不改善整数解 | 关键长交换链未闭合 | regret 排序 + 局部冲突组件标签 |
| R-004 | 已降级 | Group 中心标签收益平台 | 标签不能表达保护/SSR 资源组合 | 仅作为 row hint，不再作为主任务 |
| R-005 | 未解决 | Pattern V1 Top-1 低于 local score | 相似度标签不等于全局机会成本，且数据少 | Pattern Regret V2，保守 Top-K union |
| R-006 | 待验证 | Python 模型延迟不能代表 C++ 在线延迟 | 尚未进行 C++ 推理实现 | 静态 embedding + 批量轻量 Head |
| R-007 | 未解决 | 24 例基线平均 gap 仍约 6.98% | 候选池和交换链质量不足 | P5→P9 主线 |
| R-008 | 部分解决 | Regret 已有 12 实例，但尚无固定 IID/Hard/OOD 三分测试集 | 当前数据只覆盖优先组，且含 2 个正式困难留出实例 | 继续生成独立 split；最终训练禁止按 pattern 随机拆分 |
| R-009 | 待验证 | 单组好模式不能转化为整数改善 | 缺少联合交换组件发现 | P6.5 重叠小组件 repair |
| R-010 | 已定口径 | 是否把外部 evaluator 计入 5 秒存在歧义 | solver 与验收工具边界未锁定 | solver≤5s；外部 audit 单报 end-to-end |
| R-011 | 部分解决 | full/stress 多数 force pattern 在有限池中无兼容整数解 | 直接 companion 支持已补齐，剩余是多组组合不闭合 | companion 多样性 + P6.5 交换组件 |
| R-012 | 部分解决 | HiGHS 极短 `time_limit` 不能充当严格 wall deadline | 模型传入、presolve 和返回阶段可能超过 1ms solver limit | 剩余时间不超过50ms时已绕过HiGHS并返回经master系数审计的incumbent；benchmark驱动有外部超时，进程内仍不能中断已开始的HiGHS调用 |

## 不应重复的实验

- 不再通过单纯增加 H5/H20 hard 整例伪标签数量期待稳定提升。
- 不再把中心行 ±2 命中率当作最终模型成功指标。
- 不再盲目扩大 native beam/variant，而不检查整数交换链是否闭合。
- 不让 GNN 直接输出最终旅客座位。
- 不按 pattern 随机拆分 train/test。
- 不把未证明的 `I` 描述为全局最优。
- 不在当前 Python H5 上继续堆实验性修改。

## 每次更新模板

完成一次实验后，在文件末尾追加：

```text
### YYYY-MM-DD：实验名称

状态：DONE / EXPERIMENTAL / BLOCKED / RETIRED

假设：
改动范围：
数据与 split：
对照组：
成功标准：
结果：
合法性审计：
运行时间：
发现的问题：
解决方法或下一步：
证据文件：
是否晋级：是 / 否
```

## 更新日志

### 2026-08-27：建立路线与进度文件

状态：`DONE`

- 汇总基线、语料分层、Group GNN、C++ native pattern 和 Pattern Ranker V1。
- 明确下一主线为 Pattern Regret → Pattern Ranker V2 → 独立 C++ H20→5s。
- 当前所有 GNN 和 native pattern 代码均为研究原型，尚未接入生产求解器。

### 2026-08-27：收紧 Regret、Exchange 与 Runtime 计划

状态：`DONE`

- 明确 delete regret 与 force regret 的相反语义和基准失效重算 invariant。
- 增加 Gate A～D，模型离线指标不再作为继续增加复杂度的充分理由。
- 增加 P6.5a 启发式交换图和 P6.5b 学习型组件预测。
- 正式采用重叠 4～10 组小组件逐步传播长交换链。
- P5/P6 与 P8 改为双工作流，并在相同预算 Master A/B 汇合。
- 锁定 5 秒时间口径；无 GNN C++ 核心目标收紧为 worst 4.5 秒。

### 2026-08-27：Restricted-pool 预筛与 Companion 扩展

状态：`EXPERIMENTAL`

- force-one 前增加安全必要条件预筛：组兼容支持、条件 SSR 和满载座位覆盖。
- 预筛失败时不启动 MIP，也不把该 pattern 标成低质量；预筛通过只返回
  `undetermined`。
- 在统一公共池冻结前运行有界 companion ejection chain；硬禁止 forced
  seat/SSR resources，并奖励释放和缺少覆盖的座位。
- `forward/full_stress` 保持原 2 组×4 force×2 repeats 范围，公共池从 1,053
  增至 1,092；所有 8 个 force 候选均从预筛确定不可行修复为 `undetermined`。
- 可行扰动从 6/20 增至 8/20，restricted infeasible 从 14 降至 12；全部返回解
  合法，0 unresolved，基准分保持 `-853.3722891534109`。
- 剩余 12 次由 MIP 证明当前公共池组合不可行，说明下一瓶颈是多组 companion
  多样性和交换组件闭合，而不是单组零支持。
- 修复 Gate A 小样本误判：至少需要 3 个非 tie pairwise comparisons；本次只有
  1 个有效比较，因此正确保持未通过。
- 证据：`outputs/research/pattern_regret_forward_full_stress_companions.json`。

### 2026-08-27：P5 Regret 单实例闭环

状态：`EXPERIMENTAL`

- `forward:50_normal` 公共池包含 300 个去重合法模式。
- restricted baseline 精确复现列池参考分 `-222.21`，两次均为 Optimal、gap 0。
- 对 3 个优先组生成 42 条 delete/force 扰动记录，返回解全部合法且 Optimal。
- force regret 范围约 0～20.65，delete regret 范围约 0～3.10。
- 两次重复的 pairwise preference agreement 和 Top-5 overlap 均为 100%。
- 单实例 Gate A 小试验通过；尚未扩展到 Gold/Hard 数据，P5 不晋级 DONE。
- 基准过期会废弃本轮记录、倍增求解时间并全量重算；超过重试上限仍不可训练。
- 证据：`outputs/research/pattern_regret_forward_50_normal.json`、
  `PATTERN_REGRET_SCHEMA.md`、`tests/test_pattern_regret_labels.py`。

### 2026-08-27：P8 H20 五困难例只读贡献剖析

状态：`EXPERIMENTAL`

- 五例 H5/H20 均完整合法，H20 相对 H5 平均提高约 44.00 分。
- VND：8.72 秒、直接改善约 108.01，五例均改善，优先 C++ 化。
- restricted MIP：3.24 秒、直接改善约 99.00，4/5 改善，最高收益/秒。
- protected multigroup MIP：33.19 秒、改善约 51.20，五例均有收益但需提速。
- pattern generation：13.62 秒、直接改善为 0，但属于下游 MIP 的使能阶段，
  需要相同预算消融后才能判断贡献。
- 当前 LNS：23.54 秒、仅 1/5 改善、累计约 3.28，不原样移植；转向 P6.5
  重叠交换组件 repair。
- 证据：`H20_CPP_PORT_PROFILE.md`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/h20_hard5_contribution_profile.json`。

### 2026-08-27：P5 跨方向和困难实例小规模扩展

状态：`EXPERIMENTAL`

- `reverse:50_normal`：197 个公共池模式，42/42 扰动得到合法 Optimal 解。
- `forward:100_edge`：465 个公共池模式；38 次合法 Optimal，4 次 force-one
  在当前 restricted pool 中不可行，0 次 unresolved/invalid。
- 两例 baseline 均精确复现各自 `I`，无基准过期。
- 两例重复 pairwise agreement 和 Top-5 overlap 均为 100%。
- 困难例 restricted-pool-infeasible force pattern 作为独立类别保存，数值 regret
  保持空值；它表示缺少兼容 companion patterns，不表示 pattern 自身非法或低质。
- 当前仅验证 3 个实例，尚不足以宣布完整 Gate A 通过。
- 证据：`outputs/research/pattern_regret_reverse_50_normal.json`、
  `outputs/research/pattern_regret_forward_100_edge.json`。

### 2026-08-27：P5 `forward:full_stress` 极端检查

状态：`BLOCKED`

- 公共池包含 1,053 个模式；restricted baseline 两次均为 Optimal、gap 0、合法。
- 公共池分数 `-853.3723`，比现有参考 `I=-855.3223` 高约 1.95，说明 native
  与 `I` 模式联合后发现了新的研究 best-known pool solution；正式参考更新前仍需
  标准化复核。
- 20 次扰动中仅 6 次有合法最优解，14 次在当前 restricted pool 中不可行。
- 当前两个优先组缺少足够可比较的 force candidates，pairwise comparisons 为 0，
  因此 Gate A 明确不通过，不能用 100% Top-5 overlap 掩盖样本不足。
- restricted-pool-infeasible 不代表 pattern 自身非法或低质，而是缺少兼容
  companion patterns；该问题转入可行性预筛、companion generation 和 P6.5。
- 证据：`outputs/research/pattern_regret_forward_full_stress.json`。

### 2026-08-28：P5 数据契约与五实例 Gate A 扩展

状态：`EXPERIMENTAL`

- 新增 `pattern_catalog`，regret record 可通过稳定 ID 连接回完整 pattern
  assignments、保护座位、SSR 资源、master cost 和来源。
- 新增公共池 SHA-256 fingerprint 与 generation config；不同 fingerprint 不再
  被视为同一冻结 teacher state 的重复求解。
- `forward:100_stress`：560 个模式，42/42 合法 Optimal，34 个非 tie 比较，
  pairwise agreement 和 Top-5 overlap 均为 100%。
- `reverse:100_edge`：427 个模式，26 个合法 Optimal、6 个 restricted-pool
  infeasible、0 unresolved/invalid；16 个非 tie 比较，重复排序一致率 100%。
- 刷新并汇总 5 个 Gate A 通过实例，得到 14 个可比较组、79 个 force 候选；
  incumbent delete-regret 与 candidate force-regret 已拆分，避免模型通过选择硬保留
  incumbent 获得虚假高指标。
- 8 个 Pattern Regret 单元测试通过。
- 证据：`build_pattern_regret_labels.py`、`build_regret_ranking_dataset.py`、
  `PATTERN_REGRET_SCHEMA.md`、`outputs/research/regret_ranking_dataset_pilot.json`。

### 2026-08-28：Gate B 简单模型五实例小试验

状态：`EXPERIMENTAL`

- 使用 leave-one-instance-out，5 个实例、14 个组、79 个 force 候选。
- GBDT Top-1 excess regret 为 0.50，当前 pilot 最低；local master cost 为
  2.40。
- local master cost pairwise accuracy 为 83.8%，高于 GBDT 的 53.2%。
- Ridge Top-3 oracle recall 为 92.9%，高于其他三个基线的 85.7%。
- 指标赢家不一致且样本过少，Gate B 不通过也不失败；结论是继续扩大实例级数据，
  暂不训练复杂 GNN。
- 证据：`evaluate_regret_baselines.py`、
  `outputs/research/regret_baseline_pilot_metrics.json`。

### 2026-08-28：P5 十二实例 Gate A 汇总

状态：`EXPERIMENTAL`

- 12/12 个已标注实例通过当前 Gate A；共 702 次 teacher 扰动，660 次返回合法
  可行解，42 次明确为 restricted-pool infeasible，0 unresolved/invalid。
- restricted-pool infeasible 比例约 5.98%；这些记录继续保留为可行池诊断，不作为
  数值 regret 回归样本。
- 排序数据包含 41 个可比较组、286 个 force candidate 和 43 个 pinned incumbent
  pattern；incumbent 与 candidate 任务保持分离。
- 所有重复求解的 pairwise agreement 与 Top-5 overlap 为 100%，所有返回解的合法率
  为 100%。这说明当前 teacher 在已覆盖范围内稳定，但不代表 Gold/Hard/OOD 或
  `full_stress` 已经解决。
- `reverse:150_stress` 的冻结公共池发现分数 `-458.2641`，比旧 `I=-458.4966`
  高约 0.2325；先记录为研究 best-known，正式 `I` 暂不修改。
- 证据：`outputs/research/regret_ranking_dataset_v1_12cases.json`、
  `outputs/research/pattern_regret_*.json`。

### 2026-08-28：Gate B 十二实例 MLP 诊断

状态：`EXPERIMENTAL`

- 使用 leave-one-instance-out，12 个实例、41 个组、286 个 force candidates、
  763 个非 tie pairwise comparisons。
- MLP 的 Top-1 excess regret 为 1.565，优于 local master cost 的 1.923、
  current similarity 的 2.092、GBDT 的 2.427 和 Ridge 的 2.594。
- local master cost 的 Top-3 oracle recall 为 90.24%，高于 MLP 的 82.93%；其
  pairwise accuracy 为 85.71%，也高于 MLP 的 75.62%。
- 五实例 pilot 中 GBDT 的偶然优势没有在 12 实例上保持；说明小样本模型结论会
  反转。MLP 显示了非线性学习价值，但尚不能单独替换手工排序。
- Gate B 决定为“有条件继续”：下一步只测
  `local-cost UNION MLP/GNN Top-K`，且必须进入相同列数和时间预算的
  restricted-master A/B；在此之前不启动更复杂异构 GNN。
- 证据：`evaluate_regret_baselines.py`、
  `outputs/research/regret_baseline_v1_12cases_metrics.json`。

### 2026-08-28：P8 C++ Pattern/Master v1 协议冻结

状态：`DONE`

- 冻结稳定实例索引、完整 `PatternRecord`、主问题系数、硬保留规则、绝对截止时间、
  fallback 和输出诊断字段。
- 修正 v0 边界：最终 C++ pattern generator 必须直接输出保护资源、SSR、baby 和
  master cost，不再让在线 Python 根据 option 下标重建。
- incumbent、固定座位方案和唯一可行方案先入池且不可被模型删除；任何超时、异常
  或新增池不可行均回退到已审计 incumbent。
- 固定实现验收顺序为 pattern round-trip → Python/C++ master differential →
  24 例 incumbent-only → no-GNN C++ 4.5s → model Top-K。
- 证据：`CPP_PATTERN_MASTER_PROTOCOL.md`。

### 2026-08-28：P8 C++ PatternRecord v1 Round-trip

状态：`DONE`

- 扩展 native 输入为 typed SSR resource，并把 SSR 类型纳入冲突状态；不再把同一
  row/subrow 上的不同 SSR 类型无条件合并。
- C++ 输出完整 `pattern_id`、`master_cost`、保护座位、`ssr_all`、
  `ssr_flagged`、婴儿座位和占用座位；`master_cost` 包含同组 baby pair 抵消项。
- `forward:50_normal` 真实实例生成 90 个 pattern，90/90 与 Python
  `ExactPattern` 逐字段一致，mismatch 为 0。
- 非空字段覆盖：17 个保护 pattern、29 个 SSR pattern、4 个 SSR flag pattern、
  9 个婴儿及同组 baby 修正 pattern，避免只验证空系数。
- 独立 evaluator 审计 90/90 合法，C++/Python soft score mismatch 为 0。
- v1 输出通过兼容 `parsed_choices` 进入现有 Python restricted master：加入 20 个
  incumbent 和 71 个去重新 native pattern 后得到合法 Optimal 解 `-222.31`，比
  H5 snapshot 提高 1.10；这只验证旧 master 兼容，不等同于 C++ master 完成。
- 修复 Windows UTF-8 BOM 导致第一条 `PATTERN` 被跳过的问题；旧
  `parsed_choices` 读取入口保持兼容。
- 9 个定向单元测试通过。该结果只完成协议第一门槛，不代表 C++ restricted
  master 或 5 秒求解器已经完成。
- 证据：`native_pattern_kernel.cpp`、`export_native_pattern_input.py`、
  `validate_native_pattern_roundtrip.py`、`tests/test_native_pattern_protocol.py`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/forward_50_normal.patterns_v1.txt`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/native_pattern_roundtrip_forward_50_normal.json`。

### 2026-08-28：P8 原生 HiGHS Restricted Master

状态：`DONE`（固定列池 master）；整体 C++ 求解器仍为 `EXPERIMENTAL`

- 使用用户授权下载的官方 HiGHS 1.15.1 Windows x64 MIT 包，与本机 highspy
  版本对齐；官方 SHA-256 为
  `26302d9024f307e09128a45a58898917287351dcf754c55aebc07742237f78bf`。
- C++ 通过 HiGHS C API 一次性传入稀疏 MIP，固定 `threads=1`、`random_seed=0`、
  `mip_rel_gap=0`，并注入完整 incumbent warm start。
- 主问题已覆盖 group partition、seat packing/full-load partition、typed SSR flag/all
  big-M 和 baby 三族行；返回结果在 C++ 内再次审计系数与目标值。
- 三个冻结列池 Python/C++/独立 evaluator 差分全部通过：
  - `forward:50_normal`：score `-222.31`，三方差异 0，C++ 约 18.82ms；
  - `forward:full_normal`：score `-862.266932`，最大差异约 `1.14e-13`，
    C++ 约 47.05ms；
  - `reverse:50_normal`：score `-161.085553`，最大差异约 `2.84e-14`，
    C++ 约 115.63ms。
- 24/24 官方实例 incumbent-only 回归全部 Optimal、完整、硬约束 0，分数与 H5
  incumbent 和独立 evaluator 一致；最慢 C++ master 建模+求解约 37.15ms，最慢
  独立进程 wall time 约 68.64ms，外部 audit 最慢约 6.64ms。
- 1ms solver limit 测试仍返回完整 warm-start incumbent，但进程内 master 阶段约
  49.7ms，证明 HiGHS `time_limit` 不是严格 wall deadline；已登记 R-012。
- 新增可复现 MSVC 构建脚本；第三方包只放在 `outputs/research/toolchains/`。
- 本阶段没有证明带大候选池的 24 例性能，也没有达到最终 5 秒/2% 目标。
- 证据：`native_restricted_master.cpp`、`export_restricted_master_input.py`、
  `validate_restricted_master_result.py`、`build_native_restricted_master.ps1`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/restricted_master_differential_*.json`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/incumbent_cpp_master_24cases.json`。

### 2026-08-28：原生 HiGHS Master 源码重建复核

状态：`DONE`

- 重新使用官方 HiGHS 1.15.1 C API 和 MSVC 构建脚本生成
  `native_restricted_master.exe`；没有沿用临时自研 branch-and-bound。
- `forward:50_normal` 的 91 列池与 `reverse:50_normal` 的 63 列池重新差分，
  C++、Python HiGHS 和独立 evaluator 的 objective/soft score 均一致，0 违规、
  0 未分配。
- 重新运行 24 例 incumbent-only：24/24 Optimal、完整合法；最大 objective/score
  数值误差保持在浮点容差内。本次最慢 master 建模+求解约 57.52ms，最慢进程
  wall time 约 122.96ms，最慢外部 audit 约 15.38ms。
- 本次运行时间高于上一轮记录，进一步说明 incumbent-only 数据只验证协议与
  fallback，不可用作最终 4.5 秒候选扩池性能结论。
- 已删除重复的 B&B 回归脚本和临时输出，保留正式 HiGHS 路线作为唯一 C++ master。
- 证据：`outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/restricted_master_diff_forward_50_normal.json`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/restricted_master_diff_reverse_50_normal.json`、
  `outputs/archive/frontier_cpp_gnn_native_prototypes_through_2026-08-31/incumbent_cpp_master_24cases.json`。

## 上一轮实施入口与完成情况

下一轮不直接训练新 GNN，先完成两个最小任务包：

### Task A：P5 Regret 单实例闭环

- [x] 定义 `pattern_regret_label_v1` schema 和状态枚举。
- [x] 选择 `forward:50_normal`，构造统一公共列池并求 `I_pool`。
- [x] 对 3 个试验组的已选模式计算 delete regret。
- [x] 每个试验组采样未选模式计算 force regret。
- [x] 检测基准过期并自动使整批标签不可训练。
- [x] 基准过期后自动倍增时间、更新 `I_pool` 并重算全部标签（重试次数有上限）。
- [x] 重复求解，计算 pairwise agreement、Top-5 overlap 和 tie 比例。
- [x] 独立审计全部 teacher solution，单实例 Gate A 小试验通过。

### Task B：P8 H20 贡献剖析规范

- [x] 固定 H20 阶段名称、输出诊断和计时边界草案。
- [x] 从已有报告提取五困难例各阶段 `time / delta_F / feasibility fields`。
- [x] 明确当前数据是观察性贡献，不能替代相同预算阶段消融。
- [x] 产出 C++ 重写优先级和 stage protocol 草案。
- [ ] 通过相同 seed/budget 消融区分 pattern generation 的下游使能收益。
- [x] 固化可直接实现的 C++ pattern/master 协议。

Task A 先形成可靠标签闭环；Task B 同期只做只读剖析和架构规范。两者通过后，
再分别进入批量 P5 数据生产和 P8 C++ 实现。

## 2026-08-28 当前实施入口

1. [x] 将 Gate A 数据扩展到至少 12 个完整实例，并保持按实例划分。
2. [ ] 每组候选提高到 8～12 个，优先覆盖大组、特殊组和资源分歧模式；当前只完成
   每实例 3～4 个优先组，不能误记为全组覆盖。
3. [x] 对不同 fingerprint 的同实例只保留一个冻结版本，另一个仅用于稳定性诊断。
4. [x] 重跑 Ridge/GBDT/MLP 简单基线；结论为 local 与 MLP 联合候选，不让模型
   单独剪枝。
5. [x] 完成 P8 C++ pattern/master v1 协议。

下一实现任务按风险从低到高排列：

1. [x] 实现 C++ `PatternRecord` 与 Python `ExactPattern` round-trip 差分测试。
2. [x] 实现只接收冻结 pattern pool 的 C++ restricted master，并在小实例上与 Python
   目标值和可行状态逐项对齐。
3. [x] 跑 24 例 incumbent-only 回归，先证明 C++ 管线不会丢失可行性。
4. [ ] 在相同候选预算下实现 handcrafted 与 handcrafted+MLP union 的 master A/B；
   若最终整数解基本不动，停止加深 GNN，转向 P6.5 交换组件。

下一轮 P8 入口：

1. [x] 已消除 pattern→master 的 Python/file/文本中转；同一 C++ 进程直接传递共享
   `MasterProblem/PatternRecord` 对象。
2. [ ] 已为 pattern 批次与 beam 扩展传递单一 `steady_clock` 截止时间并保留 incumbent；
   尚未完成“master 剩余时间不足时完全绕过 HiGHS”和外部进程硬截止监管。
3. 使用实际 handcrafted/diversity 候选池跑 24 例 no-GNN C++，分别报告生成、
   master、内部 audit、序列化和进程 wall time。
4. 只有 no-GNN 闭环稳定后，再做 local-cost 与 MLP/GNN union 的等预算 Gate C。

### 2026-08-28：P8 单进程 native bridge 与 incumbent 输入闭环

状态：`EXPERIMENTAL`

- 新增 `HEADER_V2` / `INCUMBENT` 输入：每组明确携带 passenger option index，并用
  当前保护空座 owner 集合消歧；旧 `HEADER_V1` 的读取和 PatternRecord v1 输出不变。
- C++ kernel 从 incumbent choices 自行重建并硬保留完整 pattern；不再需要在线 Python
  为 master 重建 incumbent 系数。
- 将 kernel 和唯一的官方 HiGHS master 改为可调用 stream 接口，新增
  `native_solver_pipeline.exe`，在同一进程内完成生成与 restricted master。
- `forward:50_normal`：338 patterns，generation `4464.36ms`，master bridge
  `88.82ms`，total `4553.18ms`，soft score `-222.31`。
- `reverse:50_normal`：243 patterns，generation `6147.27ms`，master bridge
  `83.34ms`，total `6230.61ms`，soft score `-161.0855533234514`。
- 两例均为 Optimal；与 Python frozen-pool master 最大差异 `2.84e-14`，独立 evaluator
  均为 0 违规、0 未分配。stream 化后 24/24 incumbent-only 回归仍全部通过；本轮最慢
  master `58.40ms`、最慢进程 wall `115.21ms`。
- 结论：HiGHS 和进程中转不是当前 5s 瓶颈；reverse 的 pattern generation 单阶段已
  超预算。当前实现没有绝对截止时间，也仍有 107866/239007 bytes 的内存文本 bridge，
  不能标记为最终单进程对象协议或 5s 达标。
- V2 产生的 hard-keep 与 Python incumbent-only master 逐字段规范化复核：forward
  `20/20`、reverse `18/18` 均无语义差异；成本最大打印误差分别为 `1.42e-14` 和
  `7.11e-15`。
- 下一步只做两项：先把 master 接口改为直接接收共享 `PatternRecord` 容器；随后加入
  单一 `steady_clock` deadline 和按组/批次预算，在 master 保留时间不足时返回 incumbent。
- 证据：`native_solver_pipeline.cpp`、`build_native_solver_pipeline.ps1`、
  `outputs/research/native_pipeline/*pipeline_result.json`、
  `outputs/research/native_pipeline/*pipeline_diff.json`。

### 2026-08-28：P8 PatternRecord 对象直传与 5s 生成截止

状态：`EXPERIMENTAL`

- 新增 `native_master_types.hpp`，冻结 C++ 内存中的 `BabyPair`、`PatternRecord` 和
  `MasterProblem`；generator 和 HiGHS master 不再分别维护同义结构。
- master 的对象入口与 `MASTER_V1` 文本适配入口共用唯一建模/求解函数；standalone
  文本差分与在线对象 pipeline 均通过，避免两套约束实现漂移。
- 单进程 `pipeline_bridge_bytes` 从 forward/reverse 的 `107866/239007` 降为 `0`。
- 无 deadline 的对象直传串行测量：forward `3599.24ms`，reverse `5212.53ms`；两例
  仍分别得到 `-222.31` 和 `-161.0855533234514`。
- 加入统一 5s 预算：给 200ms master 再留 50ms 安全余量，在 hint/variant、passenger
  和 beam expansion 内检查同一 `steady_clock` 截止点。
- 5s 串行结果：forward 未触发 cutoff，338 patterns，total `3726.68ms`；reverse 触发
  cutoff，243 降为 196 patterns，total `4845.52ms`。两例仍为 Optimal，与当前
  frozen-pool 参考最大差异 `2.84e-14`，独立审计均为 0 违规、0 未分配。
- reverse 重复 3 次均触发 cutoff，总耗时 `4823.51～4882.06ms`，patterns `196～211`，
  三次 soft score 均为 `-161.0855533234514`，未发生 fallback 或质量波动。
- 200ms 极端预算验证：forward/reverse 分别在 `98.89/108.97ms` 返回完整 H5 incumbent，
  0 违规、0 未分配；因为不等于 frozen-pool 最优，质量差分脚本按设计返回不通过，
  但 C++ 目标与独立 evaluator 的差异不超过 `5.68e-14`。
- 24/24 incumbent-only standalone master 回归继续通过；本轮最慢 master `38.04ms`、
  最慢进程 wall `91.29ms`。9 项定向单元测试通过。
- 当前边界：只验证了两个 50-normal 实例，尚未运行实际候选池 24 例；进程内无法
  强制中断单次 HiGHS 调用，因此 R-012 只标记为部分解决。
- 下一步：实现剩余时间不足时不进入 HiGHS 的纯 incumbent 输出，然后导出并运行
  24 例 V2 no-GNN pipeline，报告合法率、gap、cutoff 和 wall-time 分布。
- 证据：`native_master_types.hpp`、`native_pattern_kernel.cpp`、
  `native_restricted_master.cpp`、`native_solver_pipeline.cpp`、
  `outputs/research/native_pipeline/*direct*`、`*deadline*`。

### 2026-08-29：P8 deadline fallback 与 no-GNN anytime baseline

状态：`EXPERIMENTAL`（研究假设验证完成；未达到最终 5 秒验收）

#### Deadline correctness

- pipeline 不再把不足的 master 剩余时间强制夹成 1ms 后调用 HiGHS。
- master 安全余量固定为 50ms；剩余时间不超过该余量时跳过 HiGHS，返回每组
  `hard_keep` incumbent，并输出 `master_skipped_due_to_deadline=true`。
- fallback 在返回前按 restricted-master 完整行系数审计 incumbent；定向 0s 预算
  测试确认状态为 `DeadlineFallback`、0 未分配、0 硬违规，C++ soft score 与独立
  evaluator 一致。
- 已开始的单次 HiGHS 调用仍不能由进程内 deadline 强制中断，因此 R-012 保持
  `部分解决`；benchmark 驱动额外设置外部进程超时。

#### 固定实验协议

- 只使用 incumbent 中心与旧座位中心两个 handcrafted row hints；不加载 MLP/GNN
  推理，不加入 exchange component，不改变 beam、variant、权重或搜索顺序。
- 24 个官方实例各跑 5/10/20s；预定义五困难例额外跑 60s，共 77 次。
- `I` 使用 `cabin_decomposed_2026-08-23/details/` 中当前正式列池整数参考分数。
- 每次记录进程 wall、pipeline generation/master、唯一 pattern 数、cutoff、fallback、
  soft score、`gap_I`、未分配与违规，并由 Python evaluator 独立复算。

#### 正确性与质量—时间趋势

- 77/77 运行独立审计通过；全部 0 未分配、0 硬违规、分数一致。
- 正常 benchmark 中 fallback 与 master skip 均为 0；这两个字段由独立定向测试覆盖。
- 5s：平均 gap `6.722%`，中位 `3.390%`，19/24 generation cutoff。
- 10s：平均 gap `5.710%`，中位 `3.383%`，11/24 cutoff。
- 20s：平均 gap `5.563%`，中位 `3.315%`，2/24 cutoff。
- 5s→10s 为 10 改善、12 持平、2 回退；5s→20s 为 14 改善、8 持平、2 回退。
- 五困难例 20s→60s 全部整数持平；60s 均未 cutoff，但固定搜索在约
  `7.7～21.0s` 已耗尽，继续等待不产生新工作。

两个非单调实例必须保留为后续 Gate C 风险：

- `forward:150_stress`：5s `-761.314167`，10/20s `-763.764167`；
- `forward:full_normal`：5s `-849.541932`，10/20s `-862.266932`。

这不是合法性错误，而是列池非严格嵌套：更长搜索产生的新模式在每组固定容量
截断时可能替换早期但具有更高全局整数价值的模式。

#### A/B/C 分类

预先固定标准：相对 5s，最大预算使 `gap_I` 至少下降 0.1 个百分点为 A；不属于 A，
但唯一 pattern 增加或 cutoff 解除为 B；两者均无为 C。

- A，14 例：时间增加后整数明显改善。说明 5～20s 范围 runtime/候选吞吐仍重要。
- B，5 例：`forward:150_stress`、`forward:full_normal`、
  `forward:full_stress`、`reverse:150_edge`、`reverse:50_edge`。候选池扩大但整数
  不改善，其中前两例回退。
- C，5 例：`forward:100_normal`、`forward:50_edge`、`forward:50_normal`、
  `forward:50_stress`、`reverse:50_normal`。固定搜索在 5s 内已耗尽且质量不动。

#### 研究含义与停止点

- H3 得到部分支持：5s 有 19/24 cutoff，增加时间使 14/24 整数解明显改善，runtime
  优化可把一部分 10～20s 收益搬回 5s。
- H1 仍值得继续：更多 pattern 并不保证更好整数解，且出现两例非单调回退；后续
  Pattern Ranking/保留协议首先要确保 pinned/早期高价值 pattern 不被新增候选挤掉。
- H2 对剩余困难平台更有针对性：五困难例 20→60s 全部持平；尤其
  `reverse:full_edge` 增加 57 个 pattern 并解除 cutoff 后整数仍不动，说明仅延长
  单组 pattern 搜索不足以闭合交换链。
- 本轮到此停止；没有训练新模型、修改 exchange-component 或针对单例继续调参。

证据：

- `run_no_gnn_anytime_benchmark.py`
- `tests/test_native_deadline_fallback.py`
- `outputs/research/no_gnn_anytime_2026-08-29/summary.json`
- `outputs/research/no_gnn_anytime_2026-08-29/gap_I.csv`
- `outputs/research/no_gnn_anytime_2026-08-29/analysis.md`

### 2026-08-29：候选池单调保留与 pattern discovery-time 诊断

状态：`EXPERIMENTAL`（研究假设验证完成；未进入 GNN 或 Exchange Component）

#### 固定协议与实现范围

- 未改变 handcrafted pattern generation 的 beam、variant、hint、Top-K、权重或搜索顺序；未训练
  GNN/MLP，未修改 exchange-component。
- 新增稳定 pattern ID 的诊断元数据：group、首次完成时间、source、hint、variant、group size、
  SSR/protection 与 truncation disposition。
- monotonic retention 原型使用 `union_all_shorter_formal_pools`：hard keep、历史/当前 master
  incumbent 和所有较短预算正式 pool 均不会被普通 Top-K 覆盖；本轮允许 restricted-master pool
  增长，不做固定容量参数优化。
- master 对历史已审计 incumbent 做 warm start；若限时解比该 incumbent 更差，则保留 incumbent。

#### 两个旧非单调实例的根因检查

- 受控 5/10/20s 重跑中，`forward:150_stress` 的 46/46 个 5s 选中 pattern、
  `forward:full_normal` 的 78/78 个 5s 选中 pattern 在 10s 和 20s raw pool 中均仍存在；
  没有一个在 per-hint 或 per-group Top-K 被删除。
- 两例 raw pool 分别确实有 11 和 2 个较短预算 pattern 在长预算生成中消失，原型将它们带入
  cumulative pool，但这些 pattern 均不属于受控 5s master 解。因此“已选关键 pattern 被普通
  截断挤出”没有解释本轮观测。
- 原 2026-08-29 baseline 没有保存 selected pattern IDs，无法逐 ID 重建历史那一次回退；该限制必须
  保留。独立复现还观察到 `forward:full_normal` 的 5s pool/score 随 deadline 吞吐和 HiGHS
  限时路径变化，说明旧回退更可能来自 deadline 附近的候选吞吐和 cold master incumbent/search
  path，而不是合法性或已选 pattern 丢失。

#### 24 例 monotonic benchmark

- 72/72 个 5/10/20s 结果通过独立 evaluator：0 未分配、0 hard violation、C++/Python score
  一致；24/24 实例的 integer score 随预算不退化。
- 5s：mean/median gap_I `6.824%/3.839%`，19/24 cutoff。
- 10s：mean/median gap_I `5.675%/3.383%`，11 improve、13 tie、0 regress，10/24 cutoff。
- 20s：mean/median gap_I `5.528%/3.315%`，15 improve、9 tie、0 regress，0 cutoff。
- 平均 raw/master pool 从 5s 的 `341.1/341.1` 增至 10s 的 `572.6/573.8`、20s 的
  `651.7/653.3`；10s/20s 共带入 28/39 个较短预算 pattern。该容量增长是原型的明确代价，
  本轮没有调分槽或 Top-K 参数。

#### Discovery-time 结论

- 对原 A 类 14 例与五个困难实例的并集（15 例），记录了 615 个 20s 最终选中 pattern。
  其中 545 个在不晚于 5s 的主运行或固定 replay 中发现，60 个在 `(5,10]s`，10 个在
  `(10,20]s`。
- 对 14 个改善 A 类实例，20s 解相对 5s 解更换 166 个 pattern：63 个已在正式 5s pool 但
  当时未选中，34 个在主 5s 截止前未进入正式 pool、但固定 5s replay 可发现，69 个在 5s 后
  才首次发现；`5s 已发现但被 Top-K 裁掉` 为 0。
- 12/14 个改善 A 类实例的最终解至少使用一个 5s 后首次发现的 pattern。69 个真正 late pattern
  中 59 个在 `(5,10]s`、10 个在 `(10,20]s`；64/69 来自 hint index 0，69/69 来自 variant 0，
  44/69 属于 group size 1--2，21/69 带 SSR，12/69 带 protection。
- late pattern 主要不是深 hint/variant 搜索结果，而是串行遍历较晚才到达的 group 的首个搜索路径；
  因此当前瓶颈更偏向 generation scheduling/throughput 与 master anytime incumbent，而不是普通
  pool retention 或盲目扩大 beam/variant。

#### 下一阶段建议与停止点

- 下一阶段优先 `Pattern Ranking/runtime acceleration` 分支，但先做 runtime/group scheduling 和
  master anytime/reproducibility；learned ranking 仅在相同容量、相同预算下验证，不应先扩大模型。
- Exchange Component 对 20--60s 平台型困难实例仍有价值，但本轮证据显示 5--20s 改善主要是
  发现速度问题，故不先进入 Exchange Component。
- 本轮到此停止；未启动新 GNN、MLP、Exchange Component 或单实例参数调优。

证据：

- `native_monotonic_pipeline.cpp`
- `run_monotonic_retention_benchmark.py`
- `outputs/research/monotonic_retention_2026-08-29/summary.json`
- `outputs/research/monotonic_retention_2026-08-29/gap_I.csv`
- `outputs/research/monotonic_retention_2026-08-29/discovery_patterns.csv`
- `outputs/research/monotonic_retention_2026-08-29/changed_improvement_patterns.csv`
- `outputs/research/monotonic_retention_2026-08-29/analysis.md`

### 2026-08-31：Group-major 与 breadth-first round-robin 调度 A/B

状态：`EXPERIMENTAL`（generation scheduling 假设得到机制性支持；原型因 20s 集成回退不晋级）

#### 只读确认与固定实现边界

- 原 native generator 的任务顺序确认是 `group → hint → variant → search_hint`；前一 group
  完成全部 hint/variant 后才处理下一 group。
- round-robin 只改变相同任务的执行顺序：先让所有 group 执行 `hint0/variant0`，再按 hint
  顺序完成 variant0，之后按 variant、hint、原 group 输入顺序确定性轮转剩余任务。
- 所有任务继续调用同一个 `search_hint`；未修改 beam width、pattern limit、candidate ranking、
  hint/variant 内容、group 输入顺序、deadline 或 master reserve。
- 所有 hard-keep incumbent 在搜索任务开始前加入；两臂使用相同 monotonic-retention 和历史
  incumbent 协议。未加入 case priority、I-based scheduling、ML/GNN 或 Exchange Component。
- 每个 group 记录 `hint0/variant0` 开始与完成 steady-clock 时间；完整逐组记录在
  `group_coverage.csv`。
- 搜索耗尽烟测中，两种调度均得到相同 314 个 pattern 和相同整数分，确认任务内容没有改变。

#### 24 例 5/10/20s 等预算 A/B

- 两臂共 144/144 个预算结果通过独立 evaluator：全部 0 未分配、0 hard violation，C++/Python
  soft score 一致。
- 5s：round-robin 相对 group-major 为 9 improve、13 tie、2 regress；其中原 A 类 8 例改善。
  mean gap_I 从 `6.733%` 降至 `6.400%`，平均 master pool 从 `375.0` 增至 `440.3`；两臂均
  19/24 cutoff。
- 5s 改善实例：`forward:100_edge`、`forward:100_stress`、`forward:150_edge`、
  `forward:150_stress`、`reverse:100_normal`、`reverse:100_stress`、
  `reverse:150_normal`、`reverse:50_stress`、`reverse:full_normal`。
- 5s 回退实例：`forward:150_normal`、`forward:full_normal`。变化的 11 例中 9 例非劣且真实改善，
  但 breadth-first 会牺牲部分前部 group 的 depth。
- 10s：5 improve、18 tie、1 regress；mean gap_I `5.594% → 5.554%`。
- 20s：0 improve、23 tie、1 regress；mean gap_I `5.463% → 5.528%`。两臂均无 cutoff。

#### Hint0/variant0 coverage 与 late valuable pattern

- 24 例共 888 个 group。5s 正式运行中，hint0/variant0 完成覆盖从 group-major 的
  `497/888` 增至 round-robin 的 `690/888`；完成样本的 median/P90 从
  `2474.9/4450.3ms` 降至 `2023.1/3940.2ms`。5s max 受 deadline censoring，约 4.72s，
  不适合单独判断覆盖尾部。
- 20s 两臂均完成 888/888，因此可比较未截尾分布：median 从 `3339.3ms` 降至
  `1901.1ms`，P90 从 `8555.3ms` 降至 `4592.1ms`，max 从 `17484.5ms` 降至
  `9643.1ms`。
- 上轮 69 个真正 late valuable pattern 在两臂 20s 均匹配；66/69 被 round-robin 提前。
  相对本轮并发 group-major，发现时间 median/P90 提前 `3069.3/4980.1ms`；相对上轮记录时间
  median/P90 提前 `4290.4/5940.6ms`。
- 20s 最终选中的 888 个 pattern 中，正式 5s pool 已包含的数量从 `784` 增至 `827`；固定
  5s discovery replay 可发现的数量从 `822` 增至 `868`。

#### 20s 回退与研究判断

- 唯一 20s 回退是 `forward:full_normal`，round-robin 相对 group-major 损失 `12.725` 分。
- 两臂 20s 都已搜索耗尽，最终 raw pool 均为 1448 个 pattern，且 pattern ID 集合完全相同；
  group-major master 为 `Optimal`，round-robin 为 `TimeLimit`。因此该回退不是 breadth 搜索
  遗漏，而是相同列集合因列顺序/历史 incumbent 顺序不同触发了不同的限时 HiGHS 路径。
- generation scheduling 假设得到支持：首轮覆盖显著提前，旧 late valuable pattern 大量前移，
  并在 8 个原 A 类实例产生 5s integer improvement。
- 当前 round-robin 原型不晋级：它没有满足“不以明显 20s 质量损失为代价”的完整 success signal。
  按 stop rule 只记录 breadth-depth 与 master-order 集成风险，不调度参数、不针对该实例修复 master。
- 本轮到此停止；未训练模型、修改 Exchange Component、增加 variant 或展开新优化方向。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_monotonic_pipeline.cpp`
- `run_scheduling_ab_benchmark.py`
- `outputs/research/scheduling_round_robin_2026-08-31/summary.json`
- `outputs/research/scheduling_round_robin_2026-08-31/runs.csv`
- `outputs/research/scheduling_round_robin_2026-08-31/group_coverage.csv`
- `outputs/research/scheduling_round_robin_2026-08-31/late_pattern_advancement.csv`
- `outputs/research/scheduling_round_robin_2026-08-31/analysis.md`

### 2026-08-31：Frozen-pool canonical master protocol（Gate M）

状态：`DONE`（研究 A/B master 协议冻结；不改变 production trajectory）

#### Canonical ordering 与定向验证

- restricted master 默认按 `(group, hard_keep descending, stable pattern_id)` 排列 pattern 列；相同
  pattern set 不依赖上游 insertion order。
- 新增显式 `MasterSolveOptions`，将 canonicalization 与 warm-start pattern IDs 从生成轨迹中解耦；
  monotonic pipeline 继续显式传入真实历史 incumbent，因此 production trajectory 未被抹除。
- 模型 fingerprint 覆盖最终列 ID、目标/边界/integrality、行边界和全部稀疏约束系数。
- 定向测试对 original、reversed 和 fixed-seed random permutation 验证唯一列序、唯一模型 hash、
  唯一 warm start；deadline fallback 定向测试同时保持通过。

#### Frozen-pool sensitivity

- 代表集覆盖 small/normal、stress、edge、large/full 和问题发现点 `forward:full_normal`，pattern 数为
  314、496、789、1448、759。
- 每个池在 200ms common-incumbent 下运行 original、reversed、random seed 1/7/42/2026；raw 与
  canonical 合计 60 次 sensitivity run。
- raw insertion order 在 4 个 Optimal 池中返回不同的同分最优 selected pattern set，但 5 个池均未
  出现实质 objective 波动。canonical 后每池 6/6 run 的列序、完整模型 hash、selected IDs、objective
  和 status 均一致。
- `forward:full_normal` canonical 6 次均为 `TimeLimit -862.266932`；gap 仅在
  `0.01491298..0.01509893` 间轻微抖动，returned incumbent 不变。
- 全部 sensitivity、warm isolation 和 anytime 共 100/100 次结果通过 evaluator：0 unassigned、
  0 hard violation，C++/Python soft score 一致。

#### Warm-start isolation 与原回退根因

- canonical 200ms 下，5 个池的 common、group-major 10s trajectory、round-robin 10s trajectory
  使用相同 frozen pool 和 HiGHS 配置。
- 前四个非问题池的三种协议最终 objective 一致；`forward:full_normal` 的 common 与 round-robin
  trajectory 都为 `TimeLimit -862.266932`，group-major trajectory 为
  `Optimal -849.541932`。
- 因此上一轮相同 1448 pattern IDs 的 `12.725` 分差异，根因是 group-major 已携带更好的历史
  incumbent，而非 candidate-pool quality；列序是必须消除的潜在混杂项，但不是该次分差的主因。

#### Master anytime 与 Gate M

- common-incumbent/canonical 下 50/100/200/500/1000ms 的 Optimal 数为 `1/3/4/5/5`；500ms
  首次让代表集 5/5 Optimal，1s 不再改变质量或证明状态。
- 后续 Pool Potential Test 固定 canonical ordering 和 common audited incumbent；研究默认使用
  500ms，并要求 `Optimal` 才把 objective 差异解释为 pool potential。若任一 arm 未 Optimal，统一
  升级到预先声明的 1s confirmatory budget，或标记为 censored。
- production end-to-end 继续使用各自真实 trajectory，衡量真实 anytime 表现；同时并列 common-
  incumbent/canonical 结果，明确区分 pool quality 与 warm-start trajectory effect。
- 本轮未修改 generator、round-robin、beam/hint/variant、ML/GNN 或 Exchange Component，也未针对
  `forward:full_normal` 调整任何 HiGHS 参数。Gate M 完成后停止，不启动下一研究阶段。

证据：

- `native_master_types.hpp`
- `native_restricted_master.cpp`
- `native_master_benchmark.cpp`
- `run_master_protocol_benchmark.py`
- `tests/test_native_master_canonicalization.py`
- `outputs/research/master_protocol_2026-08-31/summary.json`
- `outputs/research/master_protocol_2026-08-31/details/`
- `outputs/research/master_protocol_2026-08-31/frozen_pools/`
- `outputs/research/master_protocol_2026-08-31/analysis.md`

### 2026-08-31：GM vs RR canonical Pool Potential A/B

状态：`DONE`（Round-robin 晋级为新的 no-GNN generation baseline）

#### 固定协议与 correctness

- 完整 24 例分别执行 GM/RR 的完整 5/10/20s generation；master 独立于 generation budget。
- 两臂使用相同 `union_all_shorter_formal_pools`，不把各自历史 selected incumbent 写入 frozen pool。
- 每个 instance/budget 的 master 均使用 canonical order、相同 audited common incumbent、固定
  HiGHS 配置和 500ms limit；任一 arm 非 Optimal 才统一确认到 1s。
- 72/72 pair 均在 500ms 得到 `Optimal/Optimal`，无 confirmation、无 censored pair。
- 144/144 个最终 master 均为 0 unassigned、0 hard violation，evaluator soft score 一致。

#### Pool Potential

- 5s：RR 相对 GM 为 **10 improve、14 tie、0 regress**；mean delta_pool `+1.553958`，mean gap_I
  从 `6.897%` 降至 `6.545%`。原 A 类为 `8/6/0`。
- 10s：`5/19/0`；mean delta_pool `+0.743438`，mean gap_I `5.690% → 5.510%`。
- 20s：`0/24/0`；两臂 mean gap_I 均为 `5.499%`。
- 五困难例在 5s/10s 各有 1 个改善、无回退；20s 全 tie，但 mean gap 仍为 `15.355%`。

#### Pool overlap 与价值机制

- mean GM/RR cumulative pool 数在 5s 为 `324.5/419.8`、10s 为 `562.6/610.8`、20s 为
  `653.6/652.3`；median Jaccard 为 `0.598/0.984/1.000`。
- 5s/10s/20s 的完全相同 pool 分别为 5/10/16 例；20s integer objective 为 24/24 tie，说明
  RR 主要提前发现价值，没有观察到永久损失重要搜索空间。
- 上轮 66 个 RR-advanced old-late patterns 中，16 个进入本轮 RR 5s pool，9 个进入 Optimal master，
  7 个已发现但未使用，50 个仍未跨入 5s formal pool。
- RR 5s 相对 GM 多选 121 个 pattern；116 来自 hint0、117 来自 variant0、117 来自 hint_beam。
  其中 115 个属于改善实例，说明 first-pass coverage 的提前确实转化成 pool value。
- selected-extra 主要来自 1--2 人组（81/121），并非主要由大 group 驱动；29/121 带 SSR 或
  protection。RR-only pool 中 SSR/protection pattern 比例高于 GM-only，但最终收益不能单独归因于
  protection-heavy groups。
- 多个实例 pattern count 增加至少 10% 但 integer objective 不动；反过来，少量 selected-extra
  pattern 即可带来明显改善，例如 `forward:100_edge` 3 个对应 `+8.150`。

#### Production 与决策

- 上轮真实 trajectory + deadline 的 production 辅助结果仍为 5s `9/13/2`、10s `5/18/1`、
  20s `0/23/1`；不得与本轮 Pool Potential 指标混合解释。
- **GO：Round-robin 晋级为新的 no-GNN generation baseline。** 下一阶段优先通用 heuristic
  budget-aware scheduler / Pattern Value budget allocation；本轮不启动该研究。
- 20s 平台后完整集仍有 `5.499%` mean gap、困难例 `15.355%` mean gap，记录为后续 Exchange
  Structure 候选；本轮不启动 P6.5。
- 本轮未修改 generator 搜索、RR 调度、beam/hint/variant、HiGHS heuristic/presolve、ML/GNN 或
  Exchange Component，也未针对单例调参。

证据：

- `native_pool_exporter.cpp`
- `run_pool_potential_ab_benchmark.py`
- `outputs/research/pool_potential_round_robin_2026-08-31/summary.json`
- `outputs/research/pool_potential_round_robin_2026-08-31/pool_comparison.csv`
- `outputs/research/pool_potential_round_robin_2026-08-31/master_runs.csv`
- `outputs/research/pool_potential_round_robin_2026-08-31/rr_selected_extra_patterns.csv`
- `outputs/research/pool_potential_round_robin_2026-08-31/old_late_valuable_usage.csv`
- `outputs/research/pool_potential_round_robin_2026-08-31/pool_flag_diagnostics.csv`
- `outputs/research/pool_potential_round_robin_2026-08-31/production_end_to_end_historical.csv`
- `outputs/research/pool_potential_round_robin_2026-08-31/analysis.md`

### 2026-08-31：Post-Pass-0 Task Value / Regret（Gate S0）

状态：`DONE`（Gate S0 `NO-GO`；保留 RR baseline，停止 budget-aware scheduler 深化）

#### Baseline 与 task diagnostics

- 固定已通过 Pool Potential Gate 的 Round-robin；Pass 0 仍是所有 group 执行 `hint0/variant0`，
  未修改 task 顺序、search_hint、beam、hint、variant、pattern limit 或 deadline。
- diagnostics 为显式 opt-in；默认 production/pipeline 不收集。搜索耗尽 A/A 中 diagnostics off/on
  生成字节级相同的314-pattern pool。
- 每个 task 记录 group/flags/hint/variant、start/end/runtime、现有 beam expansion counter、
  produced/new unique/duplicate/final retained counts，以及 produced 与 first-discovered pattern IDs。
- Pass 0 后记录每组 pattern count、best/incumbent local score、gap 和 resource-signature diversity。

#### Profile scope 与运行差异

- 9个固定代表实例覆盖 ordinary、A、hard、A+hard；20s 搜索均耗尽。
- 共记录287个 Pass-0 group 状态和201个 post-Pass-0 task，其中186个在5s后开始。
- task runtime min/median/P90/max 为 `0.011/48.789/653.829/1130.539ms`；beam expansions median/P90
  为 `27,350/482,044`；new unique pattern median/max 为 `2/16`。
- 当前全部888个 group 都是 `ranking_variants=1`；625个有2个 hints、263个仅1个。因此全部
  post-Pass-0 task 都是 `hint1/variant0`，hint/variant value 差异在当前 baseline 中不可识别。

#### Sampled task regret

- 按5s后启动、full master 使用、高 runtime、高 unique、大/小组、SSR/protection 等预定义分层，
  每例最多12个，最终采样64/201个 task；覆盖全部3个 exclusive-selected task。
- 12个 task 无 exclusive retained pattern，直接记零；52个执行 removal master。
- 9个 full master + 52个 removal master 共61/61在500ms内 Optimal；无1s confirmation、无 censored，
  全部0 unassigned、0 violation，最大 evaluator 分差 `2.27e-13`。
- **61/64 task regret=0**；仅3个为正，分布于2个实例。两个大 regret `40.654981` 都来自
  `forward:100_edge`，另一个 `reverse:150_normal` 仅 `0.4`。
- 三个正例的 regret/ms 为 `0.00830/0.06416/3.19889`，但在线特征不一致：size2/size7、
  SSR/protection 有/无、Pass-0 gap `0/0.2/64.1` 均出现。

#### 统计结论与 Gate S0

- regret 与 runtime、new unique、group size、Pass-0 gap/diversity 的 Spearman 分别约为
  `-0.085/+0.205/-0.089/+0.004/-0.089`，没有可解释的强在线关系。
- 唯一接近完美的相关项是 exclusive pattern 是否被 full master 选中，但这是 master 后离线信息，
  不能用于在线调度。
- **Gate S0 NO-GO**：绝大多数 sampled task regret 为零；大值集中在单个实例；没有跨实例稳定、
  在线可观察的高价值特征。保持 RR 为 no-GNN baseline，不继续设置 priority/权重或 grid search。
- 下一研究方向转回 Pattern Value 或 Exchange Structure；本轮不自动启动任何后续阶段。
- 本轮未修改 Round-robin/Pass 0、group priority、beam/hint/variant、HiGHS 参数、ML/GNN 或
  Exchange Component，也未针对单例设计规则。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `run_task_value_profile.py`
- `tests/test_native_pool_exporter.py`
- `outputs/research/task_value_profile_2026-08-31/summary.json`
- `outputs/research/task_value_profile_2026-08-31/all_post_pass0_task_diagnostics.csv`
- `outputs/research/task_value_profile_2026-08-31/pass0_group_diagnostics.csv`
- `outputs/research/task_value_profile_2026-08-31/sampled_task_regret.csv`
- `outputs/research/task_value_profile_2026-08-31/full_master_runs.csv`
- `outputs/research/task_value_profile_2026-08-31/profiles/`
- `outputs/research/task_value_profile_2026-08-31/master_details/`
- `outputs/research/task_value_profile_2026-08-31/analysis.md`

### 2026-08-31：P6.5-pre Exchange Structure Oracle Diagnostic（Gate E0）

状态：`DONE`（Gate E0 `GO`，但 P6.5a 必须采用 dynamic rebuild / overlapping propagation）

#### 冻结协议与 correctness

- 实验前固定 9 例：五困难例、`forward:150_stress`、`reverse:100_stress`、
  `reverse:full_stress` 和低 gap control `forward:50_normal`；未按结果增删。
- A0 固定为当前 RR20 cumulative pool 的 canonical/common-incumbent Optimal solution；9/9 的 20s
  generation 均 `cutoff=false`，9/9 A0 均在 500ms 内 Optimal，无 reference-stale-at-A0。
- I allocation 仅作为 `oracle-only` delta 与 component 内 support；没有写回 native pool、generator 或
  production pipeline。component 外固定当前 selected，component 内只开放 RR20 pool + I support。
- 最大 component size 使用嵌套的固定档位邻域：K=6 包含 K=4 候选，K=8 包含 K=4/6，K=10
  包含 K=4/6/8；不为单例改变图、邻域或 size。
- 共执行 5,195 个 component master，全部在 canonical 500ms 内 Optimal，无 1s confirmation、无
  censored；全部 0 unassigned、0 hard violation，C++/evaluator score 一致。

#### RR20↔I oracle delta structure

- 9 例共有 198 个 changed groups、42 个 connected components；component size median/P90/max 为
  `1.5/22/32`。
- 35 个 1--3 组 component 覆盖 53 个 changed groups；仅 2 个自然 4--10 组 component 覆盖 9 组；
  5 个 >10 组大块覆盖 136 组（68.7%）。真实结构呈“小独立变化 + 少数大连通块”的双峰，而非
  天然分割成多个独立 4--10 组块。
- release-demand、I-demand/current-seat、protected-seat、SSR-resource 边分别为
  `219/202/52/17`。33/42 component 为 SSR/protection-heavy，覆盖 137/198 changed groups。
- 困难例 component size P50/P90/max 为 `2/26/32`，非困难例为 `1/4/29`；困难例更明显形成大块。

#### Oracle component repair

- K=4/6/8/10 sequential 的 ImprovementRate 均为 `9/9`；mean recoverable fraction 依次为
  `0.603/0.677/0.764/0.856`，median 为 `0.651/1.000/1.000/1.000`。
- 对应 mean absolute delta_F 为 `25.745/30.195/35.145/43.758`；accepted repair rounds 合计
  `37/41/41/41`，groups touched 合计 `95/119/136/155`。
- 五困难例 K=10 mean/median recoverable 为 `0.741/1.000`，5/5 改善、3/5 完整闭合到 I；
  `forward:150_edge` 恢复 66.9%，`forward:full_stress` 仅 3.7%，未为后者扩大 size 或特殊拆分。
- `forward:150_stress` oracle endpoint 比旧 I 高 0.1，raw recoverable fraction 为 1.0018；只记录为
  oracle reference surpassed，不替换正式 I。
- K=4/6/8/10 one-shot mean delta_F 为 `10.839/17.899/17.992/20.637`，sequential direct mean 为
  `25.745/30.195/35.145/43.724`。K=10 sequential 约为 one-shot 的 2.12 倍，且多个大块实例只有
  重叠 sequential repair 才恢复大部分 gap。
- direct greedy trajectory 可因动态重建路径分叉而非单调；最大-size potential 另报所有 `<=K`
  已完成 trajectory 的 best envelope，不把该现象误判为候选可行域收缩，也未继续调 selector。

#### Gate E0 与下一步

- **Gate E0 GO**：即使 online discovery 尚未知，4--10 组 oracle sequential component 已在多个平台和
  全部困难例产生稳定 integer improvement，并恢复有意义比例的 RR20→I gap。
- 同时接受 MODIFY 结论：自然图经常是 22--32 组大块，one-shot 明显弱于 sequential；P6.5a 必须把
  dynamic rebuild、overlap 和 propagation 作为核心，而不是一次静态 component selector。
- 下一阶段可启动 `P6.5a Heuristic Exchange Graph`，优先验证不知道 I 时的通用在线依赖边与动态传播；
  不先做 learned predictor。本轮按 stop rule 停止，未实现在线 Exchange、未修改 RR/generator、
  未优化 runtime、未训练 Pattern Ranker/MLP/GNN。

证据：

- `run_exchange_structure_oracle.py`
- `tests/test_exchange_structure_oracle.py`
- `outputs/research/exchange_structure_oracle_2026-08-31/protocol.json`
- `outputs/research/exchange_structure_oracle_2026-08-31/summary.json`
- `outputs/research/exchange_structure_oracle_2026-08-31/changed_groups.csv`
- `outputs/research/exchange_structure_oracle_2026-08-31/exchange_graph_edges.csv`
- `outputs/research/exchange_structure_oracle_2026-08-31/oracle_components.csv`
- `outputs/research/exchange_structure_oracle_2026-08-31/repair_summary.csv`
- `outputs/research/exchange_structure_oracle_2026-08-31/cases/`
- `outputs/research/exchange_structure_oracle_2026-08-31/analysis.md`

### 2026-08-31：P6.5a-0 Online-only Component Discovery（Gate E1）

状态：`DONE`（Gate E1 `MODIFY`；先改善 online closure，不进入 targeted pattern expansion）

#### 严格信息隔离

- 正式 Gate E1 使用 strict no-influence：固定9例、K=`4/6/8/10`、H0/H1、最多10轮与 random seeds
  `1/7/42/2026`。全部 heuristic/random component sequence 在 discovery-only 阶段先落盘，随后才
  加载 I 做 `oracle-supported evaluation`。
- `online_shadow_state` 始终只含 A0、RR pool 与 attempted seed/component/frontier history；evaluation
  接受的 I support 只进入 `oracle_eval_state`，不影响后续 selector、轮数或停止条件。
- provenance-blind dynamic 另标记为 `oracle-supported dynamic propagation upper bound`，只作传播诊断，
  不进入 Gate E1；其 greedy 数值不保证支配 strict。

#### Online graph 与 H0/H1

- H0 使用局部更优 RR alternative 的 demand-blocker 边及 blocker 最低 RR-pool release cost，固定 score
  为 `blocked_gain - release_cost`。H1 只增加固定归一化 resource-contention signal，无调参。
- 每例平均约247.2个 blocked opportunities、77.2条聚合边。冻结 selected dependency 共1,090条：
  occupied/protected/typed-SSR 分别出现 `985/359/28` 次；1,045条有 RR release support、45条没有。
- `forward:50_normal` 无符合条件的 blocked opportunity，strict H0/H1 输出空序列，未用 I/random 补齐。
- H1 K=10 仅在 `forward:150_stress` 比 H0 低0.1，其余8例 objective相同；scarce signal 没有晋级依据。

#### Strict、one-shot 与 random

- H0 K=10 one-shot 为4/9改善、mean ΔF `7.274`；strict sequential 为8/9改善、mean ΔF `16.417`，
  sequential 相对 one-shot `8 improve / 1 tie / 0 regress`。
- H0 K=10 strict mean/median normalized discovery recovery 为 `0.408/0.122`；mean groups touched
  为17.33。
- 四 seed size/round-matched random 共30/36改善，但 mean ΔF仅`4.202`、mean normalized recovery
  `0.114`；H0分别为其3.91倍和3.58倍。按实例比较 heuristic 与 random mean 为`5/2/2`。
- 五困难例 strict均改善，但 normalized recovery 为`0.982/0.122/0.100/1.000/0.020`；大块实例仍不稳。
- 全部 H0/H1 strict、random 和 dynamic 共3,658个 component master，3,658/3,658在500ms内Optimal，
  无 confirmation/censored；全部0 unassigned、0 hard violation，C++/evaluator一致。

#### Closure 与 Gate E1

- post-selection oracle closure 对照显示：高 recovery 实例通常只缺0--1组；`forward:150_edge`、
  `reverse:full_edge`、`reverse:full_stress` 和 `forward:full_stress` 的关键 oracle component 常缺2--7组。
- H0 K=4/6/8/10 dynamic propagation diagnostic mean ΔF为`14.113/15.823/16.501/16.639`，strict为
  `11.055/11.122/11.327/16.417`；小K有传播空间，K=10额外收益很小，且不用于正式判定。
- **Gate E1 MODIFY**：online graph 明显优于 random、sequential 明显优于 one-shot，因此不是NO-GO；
  但median normalized recovery仅12.2%，完美I support仍被不完整component限制，尚不能进入
  `P6.5a-1 Targeted Pattern Expansion`。
- 下一步应先研究通用 component expansion/closure（多个 release-support 分支、显式依赖闭包），不训练
  ML/GNN、不实现 targeted generator。本轮按 stop rule 停止，未修改 RR/beam/hint/variant/HiGHS。

证据：

- `online_exchange_selector.py`
- `run_online_component_discovery.py`
- `tests/test_online_exchange_selector.py`
- `outputs/research/online_component_discovery_2026-08-31/protocol.json`
- `outputs/research/online_component_discovery_2026-08-31/discovery/`
- `outputs/research/online_component_discovery_2026-08-31/evaluations/`
- `outputs/research/online_component_discovery_2026-08-31/selected_dependency_edges.csv`
- `outputs/research/online_component_discovery_2026-08-31/closure_diagnostics.csv`
- `outputs/research/online_component_discovery_2026-08-31/summary.json`
- `outputs/research/online_component_discovery_2026-08-31/summary.csv`
- `outputs/research/online_component_discovery_2026-08-31/analysis.md`

### 2026-09-01：P6.5a-0.5 Online Dependency Closure（Gate E1.5）

状态：`DONE`（Gate E1.5 `NO-GO / redesign`；停止继续调 pairwise blocker/release closure）

#### 冻结协议与实现

- H0 demand-blocker/release graph、seed 排序和 edge 权重完全冻结；H1 不再继续。本轮只比较
  `seed -> component expansion`：C0 为旧 H0 greedy，C1 在 K 内原子加入一个 opportunity 的全部
  direct blockers，C2 使用预先固定的 width-4 small beam，未做 beam/权重 grid search。
- 固定上一轮9例 portfolio、K=10、最多10轮和 random seeds `1/7/42/2026`。全部 heuristic/random
  sequence 与 online closure diagnostics 均在 discovery-only 阶段先落盘，随后才加载 I。
- strict no-influence 保持：oracle support 只进入 evaluation state，不改变 online shadow、selector sequence、
  轮数或停止条件。真实9例 C0 的 component 与 seed pattern ID 全部逐项等于上一轮 H0 K=10 sequence。
- 新增 online-only diagnostics：direct external blockers、second-hop blockers、external release dependencies、
  unresolved count/weight、closed/external dependency count；oracle-useful 缺失分类仅在 component 冻结后执行，
  不反馈 selector。

#### C0/C1/C2 strict sequential 与 random

- C0 复现上一轮：8/9改善，mean/median ΔF `16.417/7.800`，mean/median normalized recovery
  `40.8%/12.2%`。
- C1 为8/9改善，mean/median ΔF `18.866/13.843`，mean/median normalized recovery
  `44.6%/40.7%`；五困难例 mean/median normalized recovery 为`51.3%/46.3%`。
- C1 相对 size/round-matched random 的 mean ΔF 为`18.866 vs 3.939`（4.79倍），mean normalized
  recovery 为`44.6% vs 8.9%`（5.01倍）；按实例为`7 improve / 1 tie / 1 regress`。strict sequential
  相对 one-shot 为`8 improve / 1 tie / 0 regress`。
- 但 C1 对 C0 的提升高度集中：7例tie；`forward:150_edge` 从`7.8`升至`29.6425`，贡献总增量
  22.0425中的21.8425；`reverse:full_stress`仅增加0.2。其余低恢复困难例
  `forward:full_stress`与`reverse:full_edge`分别仍为10.0%与2.05%。
- C2 strict 为6/9改善，mean/median normalized recovery `27.7%/12.2%`；相对 C0 为
  `0 improve / 2 tie / 7 regress`。固定 small-beam 结果为负，不继续调 beam 或评分。

#### Closure 诊断、correctness 与 Gate E1.5

- C1 component 平均8.11组，mean/median unresolved count 为`0.273/0`；C0为`0.105/0`，说明
  graph-local closure count 的变化不能直接解释 integer recovery。
- C1 事后共有64个缺失 oracle-useful group occurrence：direct blocker 0、second-hop 3（4.7%）、
  alternative release branch 8（12.5%）、matched selected-seed closure 中无 online dependency path
  53（82.8%）。剩余缺失并非主要来自 second-hop，因此不启动预先限定的 depth-2 版本。
- 共1,100/1,100个 component master 在500ms内 Optimal，无1s confirmation、无 censored；reference audit
  全部0 unassigned、0 hard violation且 evaluator/master一致。control `forward:50_normal` 始终为空 sequence。
- **Gate E1.5 NO-GO / redesign**：C1 证明 direct atomic closure 在个别实例可有价值，但收益由一个困难例
  主导，未稳定改善多个低恢复困难例；C2明显退化，且82.8%的剩余缺失不在当前 selected pairwise closure
  路径中。GO 的跨实例稳定性条件不成立，MODIFY 的 second-hop 主导条件也不成立。
- 停止继续调 pairwise blocker/release heuristic，不进入 targeted pattern expansion。下一研究设计应先考虑
  dependency hypergraph / explicit pattern-combination representation；本轮不启动该方向，也未修改 RR、
  beam/hint/variant、generator、HiGHS、Pattern Ranker、MLP/GNN 或 Exchange Component。

证据：

- `online_dependency_closure.py`
- `run_online_dependency_closure.py`
- `tests/test_online_dependency_closure.py`
- `outputs/research/online_dependency_closure_2026-09-01/protocol.json`
- `outputs/research/online_dependency_closure_2026-09-01/discovery/`
- `outputs/research/online_dependency_closure_2026-09-01/evaluations/`
- `outputs/research/online_dependency_closure_2026-09-01/closure_diagnostics.csv`
- `outputs/research/online_dependency_closure_2026-09-01/oracle_post_selection_missing_groups.csv`
- `outputs/research/online_dependency_closure_2026-09-01/case_results.csv`
- `outputs/research/online_dependency_closure_2026-09-01/summary.json`
- `outputs/research/online_dependency_closure_2026-09-01/analysis.md`

### 2026-09-01：P6.5a-0.75 Exchange Observability / Pattern-Combination Diagnostic（Gate E2-pre）

状态：`DONE`（Gate E2-pre `REDESIGN`；observability 提升，但有限 validation 未转化为 integer gain）

#### 冻结输入与 pattern/resource representation

- C0/H0、C1、上一轮全部 K=10 sequence 和64个 missing-group diagnosis 均作为冻结输入；未修改原
  pairwise selector。继续使用同一9例与完全相同的 C1/H0 seed group/pattern ID。
- discovery-only 阶段只读取 RR20 A0、RR20 legal pool、当前资源状态和上一轮 no-I discovery 文件；
  pattern/resource reachability 与有限 validation sample 全部先冻结，之后才读取 oracle-derived missing rows。
- 最小 representation 保留 group、candidate pattern 和 resource requirement；对每个 pattern 记录
  occupied/protected seat demand/release、typed SSR delta、cost delta、当前 blocker，以及能够释放需求的全部
  RR candidate pattern。9例共5,519个 transition、7,201个 requirement arc、84,623个 release branch。
- RR export 没有独立 local cost 字段，因此 `local_cost_delta` 明确复用 `master_cost` delta 并记录 provenance，
  未虚构第二套分数。

#### Online reachability 与 oracle-only observability

- 66个冻结 seed 的 old pairwise vs pattern/resource mean reachable groups 为`18.55 -> 23.09`，median
  为`25 -> 30`；新表示 mean/max path depth 为`5.44/9`，mean/max release branching factor
  为`10.22/32`。
- 64个 prior missing oracle-useful occurrences 中，old unrestricted pairwise reachable rate 为62.5%，新表示
  为78.1%；五困难例中的 missing-group observable rate 为`13/22 = 59.1%`。
- 重新分类为：`pairwise_representation_failure=13`（20.3%）、`reachable_but_not_selected=37`
  （57.8%）、`current_pool_unobservable=14`（21.9%）、ambiguous=0。
- 原53个 `no online dependency path` 中39个现可观察，no-path count减少73.6%，剩14个 pool-unobservable；
  其细分为 representation failure 9、reachable-but-not-selected 30、pool-unobservable 14。旧 full-graph
  reachability 与上轮标签不矛盾：上轮检查 matched bounded selected-seed closure，本轮另算无界 transitive closure。
- 主要分层：size-1 observable rate `50.0% -> 83.3%`、size-4 `40.0% -> 80.0%`；SSR
  `65.5% -> 72.4%`、non-SSR `60.0% -> 82.9%`；protection `41.7% -> 58.3%`、
  non-protection `67.3% -> 82.7%`。50个 observable witness 的 path length 2--7 计数为
  `11/12/11/6/4/6`；occupied/protected/typed-SSR interaction 分别出现50/26/1次（可重叠）。

#### 有限 oracle-supported validation 与 Gate E2-pre

- 预先固定5例：`forward:150_edge`、`forward:full_stress`、`reverse:full_edge`、
  `reverse:full_stress`和高恢复control `forward:100_edge`。每例用不读 I 的固定 shortest/
  lowest-positive-cost novel-path rule 选2个 component，共10对、20次 master；没有按 oracle gain 重采样。
- 20/20在500ms内 Optimal，无 confirmation/censored、unassigned、hard violation 或 score mismatch。
  combination component 相对同 seed 的冻结 C1 baseline 为`0 better / 8 tie / 2 worse`，mean/median delta
  为`-0.587/0.000`。
- 10个 online-selected target 均未碰巧成为 prior oracle-useful missing group。该限制说明结果否定的是当前
  无 value score 的 shortest-path 转换规则，不是新 representation 所有 component 的 oracle upper bound；
  禁止据此用 I 重采样。
- **Gate E2-pre REDESIGN**：pattern identity/all release branches 显著改善 observability，且多困难例的 useful
  group 已在 RR20 pool 可观察，因此 candidate universe 缺失不是总体主因，不支持直接进入 targeted generation；
  但高 branching、37/64 reachable-but-not-selected 与0/10 validation gain 表明 reachability 尚不能转化为
  integer value，满足 REDESIGN 条件，不直接晋级 Combination-aware Online Component Selector。
- 停止扩大 representation。下一研究问题应先定义 component objective / pattern-combination scoring，区分
  jointly compatible valuable path 与仅合法可达 branch；本轮不启动该工作，也未修改 RR、HiGHS、C0/C1、
  depth-2/beam/edge weight，未启动 targeted generator、Pattern Ranker、MLP/GNN 或 learned predictor。

证据：

- `pattern_resource_observability.py`
- `run_exchange_observability_diagnostic.py`
- `tests/test_pattern_resource_observability.py`
- `outputs/research/exchange_observability_2026-09-01/protocol.json`
- `outputs/research/exchange_observability_2026-09-01/discovery/`
- `outputs/research/exchange_observability_2026-09-01/representations/`
- `outputs/research/exchange_observability_2026-09-01/seed_reachability.csv`
- `outputs/research/exchange_observability_2026-09-01/missing_group_observability.csv`
- `outputs/research/exchange_observability_2026-09-01/case_observability.csv`
- `outputs/research/exchange_observability_2026-09-01/observability_diagnostics.csv`
- `outputs/research/exchange_observability_2026-09-01/validation_results.csv`
- `outputs/research/exchange_observability_2026-09-01/summary.json`
- `outputs/research/exchange_observability_2026-09-01/analysis.md`

### 2026-09-06：generator-v3 旧座位生成语义修复

状态：`DONE`（算法研究 `PAUSED`；未重跑P6.5）

#### 已确认问题与正式口径

- generator-v2先构造紧凑`oldSeat`，再追加SSR、caregiver与保护规则；因此后生成规则可能与已冻结
  source state不一致。旧validator对单/双侧保护只检查物理邻座存在，没有验证邻座在旧状态真实为空，
  也没有完整检查保护空座跨需求共享。
- `50/100/150/full`口径保持不变：`travelerCount + protectedSeatCount = requested resource demand`；
  `full`仍表示达到新旧机型共同分舱容量，而非真实旅客数。
- 现有24例与其他未带版本字段的历史实验输入冻结标记为`generator-v2`，不原地覆盖；v3 CLI当时写入
  `data/generator_v3/`并在顶层写入`generatorVersion=generator-v3`，现已归档到
  `data/archive/generator_contract_history_2026-09-06/generator_v3/`。

#### generator-v3 实现

- 正式生成顺序改为：resource target → group sizes → group cabin → passenger records → SSR → caregiver
  → protection → constructive old assignment → source validation → newSeat preassignment → final validation。
- 旧座位sampler显式维护`occupied_old / protected_empty_old / free_old`；按受限程度放置组，并以固定
  120次组内尝试、20次全局restart为界。双侧保护原子占用“空+客+空”，单侧保护原子占用旅客与一个
  同子排邻座，需照顾SSR与预留的同组普通caregiver联合相邻放置；未引入MIP、列生成、LNS或完整
  seat-allocation heuristic。
- normal/stress/edge使用显式但非业务统计的`synthetic old-assignment profile`，覆盖`compact / same_row /
  nearby / scattered`。这些比例只制造可复现的scenario差异，不宣称代表真实航空分布，也不优化soft score。
- 独立`validate_source_state`检查oldSeat存在/唯一/舱位/SSR属性、caregiver真实邻接、单/双侧保护真实
  空置、保护资源不可共享，以及conditional SSR row/subrow语义。
- `generator_diagnostics`仅供数据审计，报告真实旅客、保护座位、resource demand、旧状态三类座位数、
  分舱load factor、placement mode计数、contiguous/same-row比例和row-span median/P90；不作为solver feature。

#### 验证与关键重验证计划

- 全量回归：`179 passed, 1 skipped, 70 subtests passed`。另对正向3个scenario×4个规模及反向
  3个scenario×3个seed做21组smoke generation，全部source validator为0错误且resource demand精确
  命中目标。多seed检查未退化为100% compact；同seed序列化结果完全一致。
- 原先把“新随机样本必须被当前5秒heuristic解完”混入generator容量测试的断言已移除。v3承诺
  hard-feasible、合理分布与可复现，不承诺对当前soft/search trajectory友好；solver性能必须在独立
  benchmark中衡量。
- 后续只规划、不在本轮执行：先生成独立v3双向24例并做source/target/evaluator schema审计；随后重建
  v3 H5/RR20 no-GNN基线与best-known I；最后才评估哪些P6.5结论需在v3复验。v2与v3的objective/gap
  不直接混合比较，不在本轮启动targeted generation、Pattern Ranker、ML/GNN或Exchange实验。

证据：

- `src/passenger_data_generator.py`
- `src/batch_benchmark.py`
- `src/run_lp_heuristic_benchmark.py`
- `tests/test_passenger_generator_v3.py`
- `tests/test_aircraft_expansion.py`
- `tests/test_general_validation_cases.py`
- `tests/fixtures/general_validation_suite/cases.json`
- `data/README.md`

### 2026-09-06：D3 Generator-v3 Minimal Algorithm Revalidation preflight

状态：`BLOCKED`（P6.5继续`PAUSED`；尚未进入算法revalidation）

#### 正式24例语料生成

- 使用既定双向、4规模、3 scenario与seed协议，在独立目录
  `data/archive/generator_contract_history_2026-09-06/generator_v3/d3_official_24cases/`保留当时生成的语料；未覆盖generator-v2。
- 23/24例生成成功，23例均通过source-state与final validator，且resource demand精确命中：
  50/100/150分别保持原口径，full按两机型共同可转移容量184计数。
- 23例共2,483名真实旅客、237个保护空座、2,720个resource demand；synthetic placement mode合计
  compact/same-row/nearby/scattered=`424/142/89/204`，逐例contiguous ratio均值`0.462`、范围
  `0.171--0.705`，没有退化为全compact。

#### Correctness blocker

- 唯一缺失例为`reverse:full_edge`。正式连续seed窗口260--289的30个seed全部失败；每个seed均耗尽
  traveler-count回退，最终在真实旅客数低于edge要求的固定10人大组时抛出
  `required group size exceeds decremented traveler count`。
- reverse source容量为Business 16 + Economy 168 = 184，因此该失败不是full口径误设为200；失败发生在
  generator构造阶段，尚未进入source validator或solver。
- 本轮stop rule禁止修改generator-v3，也禁止偏离正式seed协议，故没有用窗口外seed、单例规则或参数变化
  绕过。D3不能形成合法24例冻结输入，H5/H20、GM/RR Gate M、RR20 plateau与Exchange Oracle均未启动，
  避免把23例结果误报为完整revalidation。
- 下一步需要先单独授权修复generator-v3对`reverse/full/edge`的通用满载构造稳定性，再从Task 1重启D3；
  当前没有任何依据确认或否定RR/Exchange路线。

证据：

- `run_generator_v3_d3_corpus.py`
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_d3_revalidation_2026-09-06/corpus/partial_summary.json`
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_d3_revalidation_2026-09-06/corpus/corpus_diagnostics_partial.csv`
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_d3_revalidation_2026-09-06/corpus/analysis.md`

### 2026-09-06：Generator-v3.1 Group-size Coverage Contract Fix

状态：`BLOCKED`（group-size contract修复完成；D3 Task 1仍未形成24/24，算法实验继续`PAUSED`）

#### 已完成的contract修复

- normal/stress/edge正式`SCENARIOS`均删除`required_group_size`；`_generate_groups_exact_count`只使用
  原有`group_size_weights`、真实旅客数和分舱容量生成组规模，没有人数阈值替代逻辑，也没有
  reverse/full/edge/seed特判。三个scenario的权重、cohesion profile、SSR/protection比例均保持不变。
- 数据契约升为`generator-v3.1`，当时输出隔离到`data/generator_v3_1/`；该目录现归档为
  `data/archive/generator_contract_history_2026-09-06/generator_v3_1/`，旧generator-v2、generator-v3
  corpus和审计产物均未覆盖。
- 10人普通组由deterministic test-only构造覆盖，验证source-state合法且与共存3人组的旧座位资源唯一；
  另一定向测试强制正式生成路径只产生最大4人组，验证`max group size < 10`不构成失败。
- generator-v3专项测试`14/14`通过；未修改old-assignment状态/原子放置、synthetic cohesion profile、
  resource-demand口径、solver或任何算法研究代码。

#### D3 Task 1重新生成结果与新blocker

- 已从零写入现归档于`data/archive/generator_contract_history_2026-09-06/generator_v3_1/d3_official_24cases/`的独立目录，没有复用原23例。前23例均完成
  source/final validation、resource-demand校验和同seed二次生成逐字节等价检查；full仍为184。
- 23例group-size分布1--10依次为`277/257/139/88/60/37/24/6/15/6`；共27个`>=8`组、6个10人组，
  6例自然出现10人组、17例没有10人组。后17例均正常通过，直接验证absence不再是case legality条件。
- `reverse:full_edge`在删除强制10人组后不再触发`required_group_size`错误，但正式seed 260--289仍全部
  耗尽并报“目标机型没有足够容量生成任何旅客”。独立诊断确认max group size为8或9的seed也失败，
  因而剩余blocker不是per-case size-10 contract。
- 根因进入本轮禁止修改的联合special-resource/old-assignment范围：元数据阶段只验证单个SSR位置存在，
  未验证多组SSR/caregiver pattern的共同可放置性；例如source Economy仅有两套互不重叠的
  BSCT+caregiver物理资源，而候选可生成更多相互竞争的组合。当前constructive sampler随后无法完成
  全舱精确占用。尝试增加restart至2,000仍失败，说明不是扩大seed/restart窗口可合理修复的问题。
- 按stop rule，没有把SSR/protection分布、old-assignment逻辑或cohesion改成单例补丁，也没有启动
  H5/H20、GM/RR、plateau、Exchange Oracle、P6.5或ML/GNN。下一步需单独授权一个通用的
  pre-assignment joint special-resource feasibility contract，再重新执行D3 Task 1。

证据：

- `src/passenger_data_generator.py`
- `tests/test_passenger_generator_v3.py`
- `run_generator_v3_d3_corpus.py`
- `data/archive/generator_contract_history_2026-09-06/generator_v3_1/d3_official_24cases/`（23例partial，不是正式完成corpus）
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_1_d3_task1_2026-09-06/corpus/partial_summary.json`
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_1_d3_task1_2026-09-06/corpus/analysis.md`

### 2026-09-06：Generator-v3.2 Joint Special-Resource Feasibility（用户暂停点）

状态：`PAUSED`（correctness blocker已通过固定窗口与robustness验证；正式D3 Task 1生成在19/24时按用户要求中止）

#### 已完成实现与定向验证

- 在任何`oldSeat`生成前，仅使用source/target拓扑与synthetic角色元数据，为SSR、caregiver、单/双侧保护、
  conditional row/subrow isolation group构造有限完整special patterns；pattern原子记录硬约束成员、caregiver、
  protected-empty、全部物理资源及SSR隔离资源。
- 使用固定上限的deterministic、most-constrained-first DFS寻找每个special group各一个互相兼容pattern；
  `no_*_joint_special_witness`与`*_witness_search_budget_exhausted`分开记录，不调用solver、HiGHS、LNS或列生成。
- source witness写入显式`occupied_old / protected_empty_old / free_old`后，普通成员/组仍由原v3.1 synthetic
  cohesion sampler完成；target witness仅作隐藏可行性证明，不写`newSeat`或solver输入。
- special role最多固定20次重采样；group sizes、resource demand、SSR/protection数量语义和cohesion比例不变。
  protection身份分配使用source/target逐舱共同容量，避免把保护资源放入仅target有余量而source已满的舱。
- generator专项测试为`23 passed, 28 subtests passed`，覆盖联合物理冲突、替代pattern、保护独占、caregiver、
  conditional SSR隔离、target witness不泄漏、固定seed复现、full需求不降低及DFS预算状态区分。

#### Blocker与robustness结果

- `reverse:full_edge`正式seed 260--289：`30/30`生成成功；全部source/final validator通过、resourceDemand=184、
  同seed完整replay一致。special-role attempts median/P90/max=`9/18/20`；最终source witness nodes
  `62/79/180`，target nodes `41/46/49`；无failure reason。
- 独立固定robustness窗口300--329：`30/30`成功，保持相同合法性、184与复现要求。attempts
  median/P90/max=`7.5/17/20`；source nodes=`66/82/104`，target nodes=`41/49/51`；无failure reason。

#### 明确暂停位置

- 全新、现归档于`data/archive/generator_contract_history_2026-09-06/generator_v3_2_partial_19cases/`的目录从零生成，未复用或覆盖v2/v3/v3.1；在用户要求停止时已写入
  `19/24`：forward 12例全部完成，reverse的50三例、100三例、150_normal完成。
- 尚缺`reverse:150_stress`、`reverse:150_edge`及reverse full三例；因此当前目录是partial，不得视为正式D3
  corpus。corpus级`summary.json/corpus_diagnostics.csv`尚未生成。
- 恢复时应从头重新运行完整24例以满足本轮原合同，不拼接partial；之后才更新最终corpus审计与ROADMAP。
- P6.5、H5/H20、GM/RR、plateau、Exchange Oracle及ML/GNN仍保持暂停，未启动。

证据：

- `src/passenger_data_generator.py`
- `tests/test_passenger_generator_v3.py`
- `run_generator_v3_d3_corpus.py`
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_2_d3_task1_2026-09-06/blocker_regression_260_289.json`
- `outputs/archive/generator_contract_history_2026-09-06/generator_v3_2_d3_task1_2026-09-06/robustness_smoke_300_329.json`
- `data/archive/generator_contract_history_2026-09-06/generator_v3_2_partial_19cases/`（19例partial，不是正式完成corpus）

### 2026-09-07：D3 Generator-v3.2 Minimal Algorithm Revalidation

状态：`DONE`；D3 Decision=`ROUTE CONFIRMED`。P6.5路线恢复为下一阶段候选，但本轮未启动任何旧E1/E2、
targeted generation、Component Value、Pattern Ranker或ML/GNN实验。

#### 正式corpus与H baseline

- 从空的新目录`data/generator_v3_2/d3_official_24cases_2026-09-07/`重新生成全部24例，未复用此前
  19-case partial。generation/source validation/final validation/exact resource demand/same-seed replay均为
  `24/24`；6个full case均为184；generator version统一为`generator-v3.2`。
- group size 1--10合计为`289/268/141/96/68/44/30/4/16/8`；6例自然含10人组、18例不含且同样合法。
  synthetic placement mode compact/same_row/nearby/scattered=`499/148/98/219`，mean contiguous ratio=`0.4764`。
- H5/H20均为`24/24`合法且evaluator完全一致。H5→H20为`21 improve / 2 tie / 1 regress`，mean/median
  delta F=`+24.812/+13.285`；唯一regression为`reverse:full_stress`的`-6.946`，未据此调参。
- H5/H20 mean wall time=`4.547s/17.508s`。按structured-pattern-generation deadline字段，H5为
  11 cutoff / 13 exhausted，H20为0 cutoff / 24 exhausted。v3.2没有正式全局I，因此未报告gap_I。

#### GM vs RR Pool Potential与plateau

- 使用Gate M canonical order、相同audited common incumbent、500ms master及统一1s confirmation；未使用
  trajectory incumbent或generator-v2 I。5s为`11 improve / 10 tie / 3 regress`，mean/median delta_pool=
  `+0.992/0`；10s为`5/17/2`、`+2.206/0`；20s的23个eligible pair为`1/22/0`、`+0.043/0`。
- 唯一censored pair为`reverse:150_edge@20s`：GM在1s仍TimeLimit、RR Optimal，故不判pool胜负；其余
  71 pair均可按Optimal解释。5s/10s aggregate为正，20s无可判定regression且mean Jaccard=`0.987`，
  因而RR继续作为no-GNN baseline，但不声称短预算逐例支配GM。
- RR 5→10为`13 improve / 11 tie / 0 regress`，mean delta=`+5.453`；10→20为`7/17/0`，mean
  delta=`+0.740`。RR20为23/24 exhausted，仅`reverse:full_edge` cutoff；24个RR20 master均Optimal合法。
- 对固定五困难例做60s confirmation，五例`F60-F20=0`。其中四例20s已exhausted；
  `reverse:full_edge`由672增至823个pool pattern并在60s exhausted，integer F仍不变。RR20整数平台成立。

#### generator-v3.2 Exchange Oracle

- v3.2尚无正式全局I，因此reference冻结为逐例H5/H20中较优的已审计合法解，并显式标记
  `not_global_best_known_I=true`；21例来自H20、3例来自H5。Oracle只使用固定9例的这些v3.2 patterns，
  没有混入任何v2 reference。
- 9/9 A0 Optimal；3个RR20已优于当前reference的实例标为stale、不计算recoverable fraction。其余
  6个eligible实例在K=4/6/8/10均取得sequential整数改善；全部3,446个component master Optimal、
  0 censored、审计合法。
- K=4 sequential mean/median delta F=`14.710/7.211`，mean/median recovery=`61.3%/31.3%`；
  K=6/8/10均为`17.051/14.236`和`78.6%/63.7%`。K=10 one-shot mean/median delta F=
  `14.082/10.425`，6个eligible中5个sequential严格优于one-shot。
- 五个hard eligible case全部改善，K=10 mean/median recovery=`59.0%/27.5%`。超过100%的个例仅表示
  oracle endpoint超过这套当前H5/H20 reference，不表示超过未知全局I。
- delta graph共161个changed groups、34个component，size median/P90/max=`1.5/22/27`；28个size 1--3、
  1个size 4--10、5个>10。大块结构和sequential收益继续支持dynamic rebuild + overlapping propagation。

#### D3 Decision与停止点

- **ROUTE CONFIRMED**：RR短预算aggregate仍不劣于GM；RR20/60显示明显单组搜索整数平台；v3.2-only
  Exchange Oracle仍能恢复有意义比例的当前reference gap。因此generator-v3修复没有推翻RR/Exchange主路线。
- 下一轮应先进行精简的v3 Exchange re-baseline；generator-v2的E1/E1.5/E2 empirical数值不得直接迁移。
  本轮按stop rule停止，未修改generator-v3.2、RR/GM、beam/hint/variant、HiGHS或solver数学模型，
  未运行P6.5 online selector、targeted generation、Component Value、Pattern Ranker或ML/GNN。

证据：

- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/analysis.md`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/corpus/summary.json`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/heuristic_5s.json`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/heuristic_20s.json`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/pool_potential/summary.json`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/rr60_confirmation/summary.json`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/references_best_h5_h20/manifest.json`
- `outputs/research/generator_v3_2_d3_revalidation_2026-09-07/exchange_oracle/summary.json`

### 2026-09-07：R0-v3 Reference Stabilization / P6.5-v3 Exchange Re-baseline

状态：`DONE / STOPPED`；Gate P6.5-v3=`GENERATION LIMITED / component construction limited`。
旧generator-v2 empirical P6.5结论已由本节的generator-v3.2 re-baseline替代；未恢复旧Component Support，
未启动targeted expansion。

#### R0-v3 union-pool reference

- 新reference固定命名为`generator-v3.2 current best-known union-pool integer reference`，明确不是global optimum。
  每例只合并v3.2 H5/H20 selected patterns、GM/RR 5/10/20 online pools及固定五困难例既有RR60 pool；
  无v2 pattern、oracle I support或Exchange人工support column。
- stable ID去重并校验结构一致，canonical order、共同audited H5 incumbent、500ms/必要时1s Gate M。
  24/24 master Optimal、0 censored；24/24 reference均能还原完整protection pattern并通过evaluator审计。
- union pool size min/median/mean/max=`205/782/752.9/1508`。相对D3 best-H5/H20 reference，19/24提高，
  mean/max improvement=`+12.098/+48.583`，因此旧D3 reference与eligible set不能继续沿用。
- 最终correctness closure未重跑任何master：24/24 selected stable IDs均存在于对应canonical union pool，
  assignment、protection/`blocked_by`、physical resources与SSR row/subrow metadata可唯一重构；objective与独立
  evaluator一致，合计0 unassigned、0 hard violation。
- 从全部允许online源重新构造union并逐ID核对；同ID异内容继续作为error，未发现generator-v2、oracle-only
  support、人工I support或label-driven pattern污染。H0/C1、observability与candidate-set的27个冻结online
  artifacts也全部通过RR20路径、hash和pattern-ID归属审计。

#### K=10 Oracle refresh

- 只重跑必要的K=10。新eligible set为7例；`reverse:full_stress`与`forward:50_normal`为零gap，
  无stale case。7/7 eligible均改善，mean/median delta F=`20.036/18.821`，mean/median recovery=
  `69.1%/100%`；one-shot mean/median delta F=`14.105/9.425`，sequential相对one-shot为`5 improve/2 tie`。
- 896/896 component master Optimal且审计合法。delta graph为123 changed groups、14 components，
  size median/P90/max=`3/25/27`，size 1--3/4--10/>10数量=`8/2/4`。

#### H0/C1 strict no-influence re-baseline

- 仅运行H0/C0与C1，固定K=10、10轮、random seeds`1/7/42/2026`；H1/C2/depth-2未运行。
  所有online sequence先冻结，随后才加载union reference；evaluation不反馈shadow state。
- H0 improved 4/9，one-shot mean=`3.775`，strict mean/median delta F=`6.181/0`，mean/median normalized
  recovery=`43.0%/10.8%`；相对matched-random按实例为`3 improve/5 tie/1 regress`。
- C1 improved 3/9，strict mean/median=`5.808/0`，normalized=`41.9%/0`，相对random为`2/4/3`；
  direct atomic closure没有提高H0。random mean/median delta F=`3.351/0`。780/780 component master Optimal。

#### Observability与candidate-set oracle

- 冻结既有pattern/resource edge定义后，42个C1 missing oracle-useful groups分类为
  `reachable_but_not_selected=39`、`pairwise_representation_failure=2`、`current_pool_unobservable=1`、
  `ambiguous=0`。new observable rate=`97.6%`，hard=`96.4%`；原16个no-path中15个变为可达。
- 在加载reference前冻结249个online-only candidate components；labels=`69 positive/180 zero`。
  249个one-shot与201个additional sequential master全部Optimal、0 censored。
- C1与candidate-set oracle mean delta F分别为`5.808/6.101`，后者仅在2/9实例进一步改善；
  candidate set aggregate增量仅`+0.293`，而full K10 Oracle在7个eligible实例mean delta F=`20.036`。
  严重candidate短缺集中于`forward:100_edge`、`forward:150_edge`、`forward:150_stress`；
  `reverse:full_edge`虽仍有小的remaining oracle gap，但candidate/C1已恢复full Oracle的93.3%。

#### Gate P6.5-v3与停止点

- **GENERATION LIMITED / component construction limited**。三层证据必须分开解释：
  1. pattern/resource observability高：missing oracle-useful groups observable=`97.6%`，hard=`96.4%`；
  2. current selector弱：H0/C1只恢复部分Oracle潜力且median为0，C1没有提高H0；
  3. frozen candidate construction上限也弱：candidate-set oracle mean delta F=`6.101`，仅比C1的`5.808`
     高`0.293`、只改善2/9，远低于full K10 Oracle的`20.036`（7 eligible）。
- 因此瓶颈不是“ranker无法从已经足够好的frozen components中选择”，也不能简化为“RR20 pattern universe
  总体缺失”；准确结论是当前online constructor没有把大体可观察的pattern/resource dependencies组成足够
  高价值的joint repair components。
- 下一候选阶段为`P6.5a-1 Targeted Component Expansion Diagnostic`，状态明确为`NOT STARTED`。先分解
  Oracle优势来自既有RR20 patterns的更好component组合/扩张，还是确需RR20 pool外的新pattern support；
  在此之前不得直接称为或启动targeted pattern generation。
- 本轮按stop rule停止：未修改generator-v3.2、RR/GM、beam/hint/variant、scheduler或HiGHS，未运行新
  component master、targeted expansion/generation、Component Support、Pattern Ranker、MLP/GNN或learned
  component predictor。

证据：

- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/analysis.md`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/r0_union_reference/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/oracle_k10/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/pairwise_h0_c1/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/observability/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/candidate_set_oracle/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/correctness_closure.json`

### 2026-09-07：P6.5a-1A.1 Nested Support Envelope Closure

状态：`DONE / STOPPED`；正式Gate=`ONLINE-POOL-REUSE SIGNAL`。下一候选为
`Pattern Reuse / Pool Augmentation Diagnostic`，状态`NOT STARTED`。

- 完全复用17个frozen positive steps及其E0/E1；未重新选component、重跑K10 search或写回arm state。
  新增统一E2N=`R0 online union UNION original K10 reference support`。
- 17/17 E2N master均在500ms内Optimal，0 confirmation/censored/unassigned/hard violation，evaluator一致。
  17/17 support-set审计满足`E0 subset E1 subset E2N`，gain审计满足`E0 <= E1 <= E2N`。
- step mean gain E0/E1/E2N=`0.433/8.294/8.294`，case mean=`1.051/20.143/20.143`；weighted
  `R_existing/R_union=5.2%/100%`。类型为`13 U / 4 C / 0 P / 0 mixed`；9个hard steps全部为U。
- E2N selected provenance P0/P1/P2/P3=`25/47/24/0`，没有online-union之外的实际关键support证据。
- **old E2 was not a support superset of E1 and therefore was not a valid upper envelope.** 旧结果已按
  `original_non_nested_E2`保留为protocol history，不作为正式denominator；E2N未按单case结果选择协议，
  17个step全部使用同一定义，数值未clip或截断。
- 本轮到正式Gate即停止，未启动pool augmentation、component-level observability、targeted expansion/
  generation、representation修改或ML/GNN。

证据：

- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/steps.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/cases.csv`

### 2026-09-07：P6.5a-1B Pattern Reuse / Pool Augmentation Diagnostic

状态：`DONE / STOPPED`；正式Gate=`CROSS-TRAJECTORY POOL GO`。下一阶段候选为
`Cross-Trajectory Pool Augmentation`，状态`NOT STARTED`。

- 仅追踪E2N实际selected的24个P2 patterns；24/24来源均且仅为`H20_selected`，首次可观察预算为20s，
  T1/T2/T3/T4/T5=`0/0/24/0/0`。H20现有证据只提供case-level完成时刻18.310--19.782s，不能声称精确的
  pattern-level discovery time。
- 24个pattern均未出现在RR5/10/20 generated-pattern diagnostics；结合已验证的
  `union_all_shorter_formal_pools` retention协议，分类为`24 discovery failure / 0 retention failure`。
- 13/17 steps需要P2，每step注入0--3个（mean/median=`1.41/1`）。SSR=21、protection=8（均与SSR重叠）、
  ordinary=3；group size 2/3/4/5/6/7/9分布=`1/4/6/5/2/4/2`。
- EA仅在RR20上加入各step实际selected P2。17/17满足`E0 subset EA subset E1`及`E0<=EA<=E1`；
  total gain E0/EA/E1=`7.360/141.001/141.001`，weighted `R_minimal_reuse=100%`。
- 24/24 leave-one-pattern均有正损失，mean/median/max=`8.402/2.257/39.325`；删除13个step的selected H20
  source class后只保留4.53%的E1 gain。价值集中在每step少量关键H20-derived patterns，而非diffuse pool。
- 17 EA、24 leave-one-pattern及13 leave-one-source-class共54个新增master全部500ms内Optimal，
  0 confirmation/censored/unassigned/hard violation，evaluator一致。
- 本轮结果是oracle-informed static upper bound，不是production方法；未修改generator、RR/GM、scheduler、
  component construction、representation或ML/GNN，也未启动下一阶段。

证据：

- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/pattern_reuse/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/pattern_reuse/patterns.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/pattern_reuse/steps.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/pattern_reuse/leave_one_pattern.csv`

### 2026-09-09：P6.5a-1C H20-to-RR Pattern Mechanism Diagnostic

状态：`DONE / STOPPED`；正式Gate=`MIXED`。未启动RR hinting、special-resource operator或state-aware
pattern expansion。

- 严格复用24个P2 IDs；24/24 H20 assignments均能由正式exact/native schema逐字段重建，并已在audited
  Optimal E2N master中接受。R0/R1=`24/0`，没有representation gap。
- 固定无权重nearest tuple与strict very-close定义后，RR20 min assignment distance mean/median/range=
  `2.792/2.5/1--8`，min resource distance=`5.333/5/0--16`；very-close仅4/24。
- 只有8个H20 final patterns能与artifact中的明确accepted-pattern lineage精确匹配；M0/M1/M2/M3/M4/M5=
  `0/6/0/2/0/16`。未以SSR标签替代历史证据，其余16个保持unknown。
- 静态target-region capability probe不运行RR、不增加production hint：G0/G1/G2/G3=`23/1/0/0`。
  唯一G1为`forward:full_stress` group 2的一个target option超出单一row-radius窗口。20个pattern虽与pre-state
  其他group有资源冲突，但native generator不使用外部占用过滤，因此不归G3。
- SSR-only 13个：R0=13，assignment/resource median=`2/4`，very-close=3，G0=13；SSR+protection 8个：
  R0=8，median=`3/6`，very-close=1，G0/G1=`7/1`；ordinary 3个：R0=3，median=`3/6`，very-close=0，G0=3。
- representability与generic construction能力支持bounded search miss，但“多数patterns已有very-close RR20
  邻居”不成立，且2/3的H20 lineage不可恢复，故不满足RR-SEARCH-MISS完整Gate，也没有operator/state/
  representation gap的主导证据。下一候选记录为`stratified follow-up; no single route selected`，未启动。

证据：

- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/h20_rr_mechanism/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/h20_rr_mechanism/patterns.csv`

### 2026-09-09：P6.5a-1D RR Target-Reachability Trace Diagnostic

状态：`DONE / STOPPED`；正式Gate=`SEARCH-RANKING/CAP GO`。下一阶段候选
`Targeted Special-Pattern Search Prioritization`保持`NOT STARTED`。

- 严格冻结1C的24个stable IDs/assignments；23个G0主分析，唯一G1单列。只增加读取candidate rank/cap、
  prefix beam rank/depth和retention的trace，未修改RR搜索或任何预算。
- 7/7相关case的普通/trace RR20 replay在generated IDs、最终pool bytes/hash、master score、selected IDs和
  termination reason上相同；`reverse:full_edge`的20秒墙钟边界需第2对同预算replay才取得逐字节一致。
- 当前重编译普通pool仅1/7与旧冻结RR20 IDs相同；该artifact/binary provenance漂移已显式保留，不把当前pool
  回写为历史正式pool，也不参与instrumentation A/A判定。
- G0 S0--S7=`0/19/0/0/0/2/2/0`：19个beam survival miss，2个完整生成后per-hint Top-K移除，
  2个正式hint row-window缺transition。elimination rank median/range=`10007/2114--39398`，beam width=2000；
  first/max option rank median=`9.5/21`，depth fraction median=`0.75`。
- SSR-only=`11 S1 + 2 S5`；SSR+protection G0=`5 S1 + 2 S6`；ordinary=`3 S1`。主导miss为跨分层的S1，
  并非node/time budget；两个S6仅出现在protection层。
- 唯一G1为`forward:full_stress` group 2：passenger index 6的option 24在row 8，距target hint 11.4286，
  超出正式radius=4达7.4286，在该窗口内绝对无法进入candidate domain。未扩大radius或调参。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `run_rr_target_reachability_trace.py`
- `tests/test_rr_target_reachability_trace.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/rr_target_reachability/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/rr_target_reachability/patterns.csv`

### 2026-09-09：P6.5a-1E Beam Elimination Score Decomposition

状态：`DONE / STOPPED`；正式Gate=`PREFIX-HEURISTIC GO`。下一阶段候选
`Partial-State Lookahead Diagnostic`保持`NOT STARTED`。

- 只分析1D冻结的19个S1及其首次淘汰点；S5/S6/G1未进入Gate。7/7 current-version ordinary/trace A/A
  在generated IDs、pool bytes/hash、selected IDs、master score和termination上等价，19/19冻结淘汰点复现。
- 历史provenance限制继续成立：current replay仅1/7匹配旧RR20 IDs；本轮rank/score只标记为
  current-replay diagnostic evidence，未尝试修改search以匹配旧trajectory。
- beam key仅为累计rank score；19/19 resource-dual target/cutoff gap为0，实际gap全部由累计individual score
  解释。不存在partial compactness/penalty/lower-bound/depth/tie-break项。
- score gap to cutoff median/range=`-0.8/-7.7--0`；elimination rank=`10007/2114--39398`；depth fraction
  median=`0.75`。完整target相对历史/current group pool的local rank median均为1、percentile median均为0。
- B0/B1/B2/B3=`5/13/0/1`。SSR-only=`2/8/0/1`，SSR+protection=`1/4/0/0`，ordinary=`2/1/0/0`。
  special-resource中12/16为B1，ordinary为1/3；当前证据倾向prefix低估，而非final-local objective失配。
- cutoff带虽有row结构重复，但仅1/19 score-close，且其rank未达到1.25倍beam；B2联合条件0/19。
  leave-one loss与local percentile Spearman=`+0.126`，不足以证明Exchange价值与差local score系统相关。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `run_beam_elimination_score_decomposition.py`
- `tests/test_beam_elimination_score_decomposition.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/beam_score_decomposition/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/beam_score_decomposition/patterns.csv`

### 2026-09-09：P6.5a-1F Partial-State Lookahead Diagnostic

状态：`DONE / STOPPED`；正式Gate=`LOOKAHEAD NO-GO`。下一阶段候选
`Passenger Ordering / Prefix Cost Normalization`保持`NOT STARTED`。

- 主分析冻结1E的13个B1；5个B0与1个B3仅作controls。未重跑altered-ranking search；L0/L1/L2只对冻结淘汰层状态集离线重排。
- L1为剩余乘客独立最佳individual-cost relaxation；L2再加入唯一座位assignment，放松未来乘客间的protection、SSR、caregiver adjacency和compactness约束。两者均为state-specific且无target leakage。
- 7/7 ordinary/diagnostic正式RR20在input/config、generated IDs、pool bytes/hash、selected IDs、master score和termination上等价；snapshot由隔离shadow replay采集。仅1/7 current pool匹配旧RR20 IDs，结论只标记为current-replay diagnostic evidence。
- B1 rescue L1/L2=`0/13`，controls=`0/6`。B1 rank improvement median/mean为L1 `13/-190.31`、L2 `13/-166.54`；没有target回到top-2000。
- B1 cost-underestimate error median/mean为L1 `1.0/1.427`、L2 `0.9/1.4`，方向一致性均为13/13。SSR-only、SSR+protection、ordinary的L1/L2救回均为0。
- 全19层L0/L1和L0/L2 Spearman median均为`0.999978`，top-2000 overlap median均为`2000`；简单lookahead整体扰动小且未解决B1淘汰。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `run_partial_state_lookahead_diagnostic.py`
- `tests/test_partial_state_lookahead_diagnostic.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/partial_state_lookahead/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/partial_state_lookahead/patterns.csv`

### 2026-09-09：P6.5a-1G Passenger-Order Causality Diagnostic

状态：`DONE / STOPPED`；正式Gate=`ORDER NO-GO`。Passenger Ordering路线停止；下一阶段候选
`Prefix Ranking Signal Diagnostic`保持`NOT STARTED`。

- 冻结1F的13个B1，5个B0和1个B3仅作controls；未修改production passenger order、candidate、beam key、beam width或budget。
- 19/19层内所有state均处理相同的passenger indices `0..k-1`，无skip/deferred passenger，same-depth same-passenger-set rate=`100%`。任何层内公共baseline subtraction都不能改变rank。
- baseline固定为每名passenger正式RR candidates的最低individual cost；regret固定为second-best减best，单一option记0。B1 current max/final residual median=`3.4`，elimination residual median=`2.4`，elimination/final ratio median=`0.862`。
- B1 residual rank、raw accumulated-individual rank与L0 rank为13/13完全一致；controls residual/raw rank为6/6一致，实际冻结状态集验证了公共平移结论。
- O1 regret降序、O2 candidate-count升序均未降低max residual：B1 positive relief=`0/13`。O1淘汰等价depth仅3改善、3变差，O2无改善；SSR-only、SSR+protection、ordinary的positive relief均为0。
- Task 8 Gate未打开，按协议没有运行shadow-order modified-search experiment。

证据：

- `run_passenger_order_causality_diagnostic.py`
- `tests/test_passenger_order_causality_diagnostic.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/passenger_order_causality/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/passenger_order_causality/patterns.csv`

### 2026-09-09：P6.5a-1H Prefix Ranking Signal Diagnostic

状态：`DONE / STOPPED`；正式Gate=`INCONCLUSIVE`。没有选定下一阶段，状态保持`NOT STARTED`。

- 冻结13个B1、5个B0与1个B3首次淘汰层；每层固定sample 48个state，seed=`20260909`，未按completion结果追加。
  completion统一固定prefix并使用正式candidate universe、resource/SSR/caregiver约束及完整local objective；无target、
  H20、Exchange或reference leakage，未修改production search。
- 912个state中735个Optimal、177个精确infeasible、0 timeout/error，formal coverage=`19/19`。全部Optimal通过
  prefix、hard constraints、native schema及objective复核；最大objective误差=`1.000000154e-6 < 1e-5`。
- 全层prefix/completion Spearman median/mean=`0.530/0.457`，top-10% overlap rate median=`0`，beam FNR
  median/mean=`0.8/0.663`。B1对应Spearman=`0.500/0.401`，FNR=`1.0/0.738`。
- 13个B1 target当前均在beam外；sample内completion reversal median/mean=`20/16.385`，8/13进入固定sample的
  top-2000-equivalent。B0为Spearman/FNR/reversal median=`0.678/0.4/19`、3/5进入；B3为
  `0.546/0.2/19`、0/1进入，故mismatch并非target-only。
- SSR-only、SSR+protection、ordinary B1的Spearman median=`0.556/0.494/0.341`，FNR median=
  `1.0/0.7/1.0`，reversal median=`24/15/20`；没有special-resource专属Gate证据。
- overall median Spearman既未达到weak `<=0.30`也未达到strong `>=0.60`，故尽管FNR和target reversal明显，
  仍不能按预声明规则选择ranking design、cross-group value或special-resource路线。本轮未设计新ranking formula。

证据：

- `run_prefix_ranking_signal_diagnostic.py`
- `tests/test_prefix_ranking_signal_diagnostic.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/prefix_ranking_signal/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/existing_vs_extra_support/prefix_ranking_signal/states.csv`

### 2026-09-11：H-Prod-1A RR Beam Robustness Prototype

状态：`DONE / STOPPED`；正式Gate=`BEAM-PROTOTYPE NO-GO`。5s Fair Test未启动；下一阶段候选
`H-Prod-1B Sequential Exchange Prototype`保持`NOT STARTED`。

- 在原2000总cap内实现默认关闭的Main/Reserve双通道：B1固定`100/1`，B2固定`200/2`；bucket只含online
  partial row footprint、protection resource count与SSR/flag count。candidate、primary score、operator、
  legality、master和evaluator均未修改，H20 target不参与搜索决策。
- B0代表性穷尽例与旧冻结pool逐字节一致，B1 replay逐字节一致；144/144 master Optimal且evaluator合法，
  0 unassigned、0 hard violation，最大score difference=`5.684e-13`。
- 19个历史S1在B0/B1/B2、10/20s均`0/19`完整生成；B1/B2虽最多有9/16个target曾经由Reserve保留，
  但无完整rescue且23个G0进入正式pool均为0。
- 10s B1 improve/tie/regress=`0/16/8`、mean Δ=`-3.057`；B2=`3/15/6`、`-2.617`。
  20s B1=`2/18/4`、`-0.180`；B2=`7/14/3`、`+0.262`。五个hard cases均无提升。
- mean gap to current best-known reference：10s B0/B1/B2=`3.055%/3.535%/3.480%`；20s=
  `2.940%/2.977%/2.905%`。这里只是当前best-known reference gap，不是global optimality gap。
- mean total solver wall：10s B0/B1/B2=`7.876/8.507/8.574s`；20s=`8.902/11.927/11.822s`。
  Reserve增加开销且替换掉较多baseline patterns，证据不支持继续调参。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `native_solver_pipeline.cpp`
- `run_beam_robustness_prototype.py`
- `tests/test_beam_reserve_prototype.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/beam_robustness_prototype/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/beam_robustness_prototype/cases.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/beam_robustness_prototype/target_rescue.csv`

### 2026-09-12：H-Prod-1B Sequential Exchange Prototype

状态：`DONE / STOPPED`；正式Gate=`SEQUENTIAL-EXCHANGE GO`。下一阶段候选
`H-Prod-1C 5s Exchange Compression`具备资格，但保持`NOT STARTED`，本轮未做5s tuning。

- 实现默认关闭的fully-online sequential Exchange：RR incumbent后由冻结H0+C1选择4--10组component，调用现有
  native generator作local expansion，组件外固定；Optimal且严格改善才commit，并从更新后的incumbent继续，最多3轮。
- 固定local预算为每组1个现有search task、64 patterns/group、512 new patterns/component，5s/group只作必须不触发的
  safety deadline；component MIP为500ms canonical + 非Optimal时1s confirmation。110次solve均首轮Optimal。
- 48/48 baseline合法且evaluator一致；0 regress、组件外状态不变、strict commit成立。敏感
  `forward:150_stress@10s`重放的component、expanded stable IDs、决策和最终解一致；reference/H20 IDs只作后验。
- 10s improve/tie/regress=`7/17/0`，mean/median ΔF=`+2.1066/0`；mean gap从`3.038%`降至`2.750%`。
  20s=`6/18/0`，mean/median ΔF=`+1.9087/0`；mean gap从`2.940%`降至`2.677%`。
- 16次accepted repairs中14次依赖expanded patterns；共9036条case-budget-round expanded pattern记录（每轮stable-ID
  去重），最终选择发生23次。hard case
  `reverse:full_edge@10s`改善`+0.05`，其余hard case-budget tie。
- 平均EX总wall为10s `11.594s`、20s `12.289s`，相对各自baseline的mean per-case ratio为`1.479x/1.400x`；
  local expansion为`3.762s/3.302s`，component MIP为`0.284s/0.263s`。

证据：

- `sequential_exchange.py`
- `run_sequential_exchange_prototype.py`
- `tests/test_sequential_exchange.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/sequential_exchange_prototype/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/sequential_exchange_prototype/cases.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/sequential_exchange_prototype/accepted_repairs.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/sequential_exchange_prototype/expanded_patterns.csv`

### 2026-09-12：H-Prod-1C 5s Exchange Compression

状态：`DONE / STOPPED`；正式Gate=`5S-COMPRESSION NO-GO`。下一阶段候选为
`when to invoke Exchange / RR-Exchange scheduling`，状态`NOT STARTED`。

- 冻结三臂：F0为当前RR、Reserve/Exchange OFF；F1=`RR 3.8s + 1 round + 24/192 caps`；F2=
  `RR 3.4s + 2 rounds + 32/256 caps`。共享5秒absolute deadline、200ms final safety；component MIP固定75ms，
  非Optimal不commit。未改变H0/C1、native operators、Beam、ranking或evaluator。
- F0/F1/F2均24/24 complete、0 hard violation、evaluator一致，max wall分别为`4.875/4.785/4.814s`，
  三臂均0次>5秒。mean wall=`4.370/4.314/3.982s`。
- F1 improve/tie/regress=`0/16/8`、mean ΔF=`-1.0897`；F2=`1/14/9`、`-0.8466`。mean gap=
  `4.051%/4.283%/4.276%`，gap≤2%=`8/7/7`，五个hard cases均无收益。
- F1为20 rounds、0 accepted、261 expanded/0 selected；F2为21 rounds、1 accepted、567/0 selected，唯一
  accepted只复用existing RR。expansion cutoff为F1 `17/20`、F2 `18/21`。
- F1/F2 expansion总wall=`9.575/10.640s`，component MIP=`1.575/1.649s`；retained gain fraction=
  `-51.73%/-40.19%`。敏感例replay中F0/F1一致、F2不一致。
- 因RR让时导致系统性回退且expanded pattern无实际选择贡献，停止当前压缩方式；不增加第三配置、不放宽5秒。

证据：

- `five_second_exchange.py`
- `run_5s_exchange_compression.py`
- `tests/test_five_second_exchange.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/five_second_exchange_compression/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/five_second_exchange_compression/cases.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/five_second_exchange_compression/exchange_activity.csv`

### 2026-09-14：H-Prod-1D Opportunistic Exchange Scheduling

状态：`DONE / STOPPED`；正式Gate=`SCHEDULING NO-GO`。Slack Gate未打开，因此S1未实现。下一阶段候选
`Native Local Expansion Acceleration`保持`NOT STARTED`。

- S0严格复用1C F0：RR max=4.55s、4.8s compute cutoff、5s global deadline、200ms safety；预先固定
  minimum usable slack=250ms。reference与1B labels只用于后验报告。
- 24/24 complete、0 hard violation、evaluator一致、0次>5s，且24/24 score与冻结1C F0一致。solver wall
  mean/median/P90/max=`4.399/4.692/4.768/4.905s`。
- slack mean/median/P90/max=`405.7/107.6/765.8/3360.3ms`；≥100/250/500/750/1000ms=
  `15/5/5/3/2`。仅5个自然穷尽容易例达到250ms。
- 1B的16个盈利repair中`0/16`能按已记录成本塞入对应case slack；expanded类也是0。五个hard cases slack=
  `82.3/120.2/122.3/105.7/150.6ms`，均不够启动且均未自然exhaust。
- `forward:full_normal`边界replay在score、1476 patterns、status与exhaustion状态上一致，wall=
  `4.905/4.902s`。
- 自然slack只落在少数容易例，无法承载已知盈利repair；不实现S1、不调threshold或增加S2。

证据：

- `run_opportunistic_exchange_scheduling.py`
- `tests/test_opportunistic_exchange_scheduling.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/opportunistic_exchange_scheduling/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/opportunistic_exchange_scheduling/cases.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/opportunistic_exchange_scheduling/scheduling_activity.csv`

### 2026-09-14：H-Prod-1E Native Local Expansion Acceleration

状态：`DONE / STOPPED`；正式 Gate=`NATIVE-ACCEL NO-GO`。batch 工程优化保留；下一阶段候选
`Local Expansion Search-Efficiency Prototype` 保持 `NOT STARTED`，未启动 1F。

- 冻结 7 个盈利 expanded-dependent components 与 3 个 controls，每个运行 3 次 A0/A1。A1 仅把原来的 per-group exporter invocation 合并为现有 multi-group scope 的单 component invocation，并使用 `--master-only`；每 group 搜索 task、cap、domain、Beam、排序、ID 与 master 均未改变。
- 10/10 A/A 在 stable IDs/order、所有 pattern fields/score、无 cutoff termination 及 selected repair 上通过；batch repeated replay deterministic，无跨 group mutable state 泄漏。
- A0/A1 workload mean-wall 总和=`31.316/22.634s`，overall=`1.384x`，median component=`1.381x`，范围=`1.224x--1.966x`；profitable/control 合计 speedup=`1.410x/1.296x`。
- top hot spots：search=`69.31%`、完整 input parse=`18.71%`、process/orchestration residual=`4.83%`。A0 确实重复 topology/input/group setup 和完整 pool I/O；但 inner Beam 的 `State` vector deep copies 与 candidate rebuild/sort 留在占主导的 formal search 本体内，本轮未改搜索表示或顺序。
- 完整 24-case 10s 1B replay 24/24 逐 case 质量、逐 round IDs/order 与合法性一致；仍为 `7/17/0`、10 accepted repairs、mean ΔF=`+2.106608`。accelerated expansion mean/median=`2702.9/1978.2ms`，mean total solver wall=`11593.5 -> 10539.5ms`。
- 16 个历史盈利 repair 的 expansion mean/median/P90 从 `2813.9/2721.6/4563.6ms` 降到 `2446.3/2473.8/3508.3ms`，S0 slack fit 仍为 `0/16`；盈利 hard repair `reverse:full_edge@10s` 估计总 cost=`4820.7ms` 对 slack=`150.6ms`。
- 因 speedup 未达到 1.5x、slack feasibility 无变化，按预声明 Gate 停止 pure-engineering micro-optimization。后续若授权，应进入 formal domain 不变的 search-efficiency prototype，而非 5s integration。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `run_sequential_exchange_prototype.py`
- `run_native_local_expansion_acceleration.py`
- `tests/test_sequential_exchange.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/native_local_expansion_acceleration/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/native_local_expansion_acceleration/profiling.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/native_local_expansion_acceleration/aa_equivalence.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/native_local_expansion_acceleration/replay_cases.csv`

### 2026-09-14：Local Expansion Search-Efficiency Prototype

状态：`DONE / STOPPED`；正式 Gate=`SEARCH-EFFICIENCY NO-GO`。下一阶段候选
`Targeted Multi-Pattern Construction` 保持 `NOT STARTED`；未启动 1F 或修改正式 5s solver。

- 冻结 16 个 1B positive steps（14 expanded-dependent、2 existing-only）与10个 controls。P0为现有local Beam；P1固定使用其他component groups的incumbent占用与RR依赖率构造resource pressure，以`P0 rank - pressure`排序，并只在complete-pattern retention做resource-signature diversity。formal top-48 domain、hard constraints、score、stable ID和component MIP不变。
- Full labels仅在全部search冻结后连接；Full P0与1B正式expanded stable IDs/order逐step一致。3659个P1 unique patterns全部通过stable ID、schema、hard legality与master-cost重建（max error=0）；MIP/evaluator一致且outside state固定。
- nominal 25/50/100/200/500ms的P0/P1 raw positive counts=`2/2,2/2,2/4,2/2,4/4`；扣除2个existing-only后为`0/0,0/0,0/2,0/0,2/2`。所有短预算median/P25 recovery=0；P1 100ms expanded-dependent mean recovery仅0.00290。
- selected-pattern recall P0/P1在100/200/500ms分别为`0.0952/0.0476, 0.0952/0.0476, 0.1548/0.1071`。P1的两个100ms新增正gain仅恢复约1.7%/2.3%，50%和75% recovery仍全部要到Full（除existing-only）。
- actual mean wall P0/P1：25ms=`130.6/144.7ms`、50ms=`135.9/149.1ms`、100ms=`168.8/186.7ms`、200ms=`291.2/308.2ms`、500ms=`589.6/608.0ms`；25ms被parse/startup耗尽，0 expansions。
- P1在100/200/500ms将mean resource signatures从`18.9/28.6/39.1`提高到`25.0/34.2/43.2`，但未转化为repair value；100ms candidate expansions不降，selected-useful/ms下降。
- SSR+protection/SSR-only/ordinary样本=`2/7/7`。100ms P1 raw gain count有SSR-only与ordinary各+1，200ms均消失；不存在稳定special-resource-only信号。
- controls在25--200ms无gain；P1 500ms发现1个合法真实repair，Full发现5个，说明长期Beam路径不同但时间尺度不适合production。
- S0 slack fit=`0/16`；hard `reverse:full_edge@10s`在100/200ms recovery=0，P1需约4950.5ms才有gain，对应slack仅150.6ms。
- 因100/200/500ms median recovery=0、500ms不优于P0且无hard/slack可行性，停止简单component-aware ranking。后续若授权，转向直接构造Exchange-support patterns。

证据：

- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_pool_exporter.cpp`
- `run_local_expansion_search_efficiency.py`
- `run_local_expansion_search_efficiency_support.py`
- `tests/test_local_expansion_search_efficiency.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/local_expansion_search_efficiency/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/local_expansion_search_efficiency/budget_curve.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/local_expansion_search_efficiency/repairs.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/local_expansion_search_efficiency/search_diagnostics.csv`

### 2026-09-15：Targeted Multi-Pattern Construction

状态：`DONE / STOPPED`；正式 Gate=`TARGETED-CONSTRUCTION NO-GO`。下一阶段候选
`Cross-Trajectory Pattern Memory / Reuse` 保持 `NOT STARTED`；未进入 1F，也未修改正式 5s solver。

- 冻结 16 个 1B positive steps（14 expanded-dependent、2 existing-only）和原 10 个 controls。T1 避开一个
  高 pressure incumbent resource，T2 仅排序偏好一个低 pressure row，T3 使用一个 avoid+prefer；每类每组
  最多 2 target、每组总计最多 6、K=2、固定 1024-node direct-DFS cap，无 Full/H20/label guidance。
- 14 个 expansion-dependent repairs 的 58 个 changed groups 通常是多资源联动：passenger changes 中位数 2，
  resource symmetric difference 中位数 4，release/acquire 中位数均为 2；protection、SSR 与 caregiver changes
  的中位数均为 0。
- 579 个 targeted unique patterns 全部通过 formal top-48 membership、hard/SSR/protection/caregiver/baby、
  stable ID 与 score 重建审计（max error=0）；component MIP 均 Optimal/evaluator 一致，outside state 固定。
- 25/50/100/150/200ms 下，expanded-dependent gain-positive count P0/TMC 均为 `0/0`，mean/median/P25
  recovery 均为 0，达到 25%/50%/75%/100% 的数量均为 0。TMC mean wall=`27.3/52.6/103.0/152.0/202.8ms`，
  P0=`127.5/130.5/170.9/232.1/288.7ms`。
- TMC 五档分别生成 `72/87/109/115/115` 个 patterns，P0 为 `0/132/393/487/485`；两者被 component MIP
  选中的新 patterns 均为 0，selected/generated 与 gain/ms 都没有改善。
- Full Targeted 仅 2/14 positive steps 获得 gain，mean/median recovery=`0.1034/0`，生成 473、选中 3，
  mean wall=4596.2ms。T1/T2/T3 仅在 Full 出现 1/2/2 次 accepted-repair attribution，短预算均为 0。
- 10 controls 所有 TMC 档位均为 0 gain；hard `reverse:full_edge@10s` 在 50/100/150/200ms 均无 gain；
  S0 slack fit=`0/16`。因此停止 generic formal-candidate 在线搜索/构造优化，不自动启动 pattern memory。

证据：
- `targeted_multi_pattern_construction.py`
- `run_targeted_multi_pattern_construction.py`
- `tests/test_targeted_multi_pattern_construction.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/targeted_multi_pattern_construction/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/targeted_multi_pattern_construction/budget_curve.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/targeted_multi_pattern_construction/intent_stats.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/targeted_multi_pattern_construction/repairs.csv`

### 2026-09-15：H-Prod-2A Cross-Trajectory Pattern Memory / Reuse V1

状态：`DONE / STOPPED`；正式 Gate=`PATTERN-MEMORY GO`。下一阶段候选为 `Heuristic V1 Freeze and Final Benchmark`，保持 `NOT STARTED`。

- 仅使用 H5/H20 与 GM/RR 5/10/20 秒的合法 selected patterns；严格 whole-case leave-one-case-out，无 R0/reference/oracle/人工 support 泄漏。
- 模板为 exact GroupSignature + canonical formal-option rank vector，不存绝对 seat ID；7,712/7,712 source patterns 精确自重建，去重后 1,093 templates / 202 signatures。
- production cap 为每组 4 个最高排序模板。LOO coverage=`721/964=74.793%`，legal reconstruction=`2,127/3,097=68.679%`；special sparse signature 未过半，coverage/data Gate 通过。
- M1 保持 RR 4.55 秒、nominal master 0.2 秒且 Exchange OFF。24/24 complete、0 hard violation、24/24 evaluator 一致、0 次超过 5 秒；wall mean/max=`4.413/4.940s`，memory overhead mean/max=`38.51/81.31ms`。
- 相对冻结 M0，improve/tie/regress=`9/15/0`，mean delta F=`+0.67156`；mean gap `4.0513% -> 3.9576%`，median、worst 与 gap<=2% case 数不变。
- 8 个 case 共选择 14 个 memory patterns，selected/legal=`0.6582%`；历史 H20 targets 后验命中 `0/24`，不参与方法或 Gate。
- ML/GNN 正式状态改为 `DEFERRED — NOT REJECTED`：早期实验的数据规模、调参与 learning target 不足以支持方法级 NO-GO。本阶段未运行 ML/GNN。

证据：
- `pattern_memory.py`
- `run_cross_trajectory_pattern_memory.py`
- `tests/test_pattern_memory.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/cross_trajectory_pattern_memory/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/cross_trajectory_pattern_memory/memory_templates.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/cross_trajectory_pattern_memory/coverage.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/cross_trajectory_pattern_memory/cases.csv`

### 2026-09-15：Heuristic V1 Freeze and Final Benchmark

状态：`DONE / STOPPED`；正式 Gate=`HEURISTIC-V1 HOLD`，质量状态=`QUALITY TARGET NOT YET MET`。本轮未进入 V2。

- V1 冻结为 exact Pattern Memory + RR 4.55s + nominal 0.2s restricted master；global deadline=5.0s、final safety=0.2s、template cap=4。Reserve Beam、Sequential Exchange、local expansion、TMC、ML/GNN 均关闭。
- 唯一正式 24-case attempt 中，`reverse:full_normal` 触发硬 deadline 且未重试；其余 23/24 complete、0 unassigned、0 hard violation、23/23 evaluator consistent。completed wall mean/median/P90/max=`4.561/4.795/4.878/4.971s`。
- 因 1 例无 objective，不提供 24-case aggregate quality。23 completed cases 的 mean/median/P90/worst gap=`3.936%/3.098%/8.644%/14.665%`；gap<=1/2/5%=`7/7/16`，Green/Yellow/Red=`7/9/7`，另有 1 deadline-unclassified。
- 对冻结 B0 的 completed-case improve/tie/regress=`7/13/3`、mean delta F=`+0.41271`；三个 regress 为 `forward:50_edge`、`forward:100_stress`、`reverse:100_normal`，不满足 release 的无系统性回退条件。
- Memory 在 7 个 case 选中 15 个 patterns；selected/legal=`15/1902=0.789%`，全部 ordinary。五个 replay 的 memory IDs、selected IDs、score、legality 全一致；RR pool hash `3/5` 一致，边界 pool 仍有时间敏感性。
- 冻结 artifact SHA-256=`bd9012bd6ed152dfe7d76a99b479db84c8470f08bd79d6ac47f805f2c64c2826`。ML/GNN 保持 `DEFERRED — NOT REJECTED`。
- V2 backlog：Red/worst-gap closure、Pattern Memory Corpus Expansion、special-resource coverage、ML/GNN fair benchmark、possible learned template/intent ranking；仅记录，全部 `NOT STARTED`。

证据：
- `research/frontier_cpp_gnn/heuristic_v1_config.json`
- `research/frontier_cpp_gnn/run_heuristic_v1_final.py`
- `reports/HEURISTIC_V1_STATUS.md`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1_final/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1_final/cases.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1_final/memory_contribution.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1_final/heuristic_v1_memory_v1.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1_final/heuristic_v1_memory_v1.manifest.json`

### 2026-09-16: Heuristic V1 Baseline-Preserving Memory Integration

状态：`DONE / STOPPED`；Gate=`HEURISTIC-V1R HOLD`。未进入正式 24-case benchmark，未进入 V2。

- 已实现 B0 RR -> B0 master -> 永久保存 baseline incumbent -> slack gate -> 可选 memory reconstruction/augmented master；augmented master 显式使用 baseline selected IDs，且只允许严格改善时 commit，不重跑 RR。
- `minimum_memory_start_slack=650ms`，仅由既有最坏 memory path 约 411ms、200ms final safety 和 39ms 抖动余量推导；没有按 case、gap、历史收益或 reference 调度。
- 修复 benchmark harness 的管道排空问题并改用紧凑 RR hash 后，冻结等价性 Gate 中 `forward:full_normal` 的 RR hash、selected IDs、objective、assignment、evaluator 全部一致。
- `reverse:full_normal` 的 objective/evaluator 一致，但 RR hash、selected IDs、assignment 不一致；Memory 尚未开始，故根因边界定位为 B0 wall-clock trajectory 未冻结，而非 Memory regression。
- 按 stop rule，3 个历史 regress safety cases 与新 24-case one-shot benchmark 均未运行；没有重试失败 Gate、没有观察结果后改参。
- 正式结论：`V1 release requires runtime safety margin adjustment`。下一阶段只允许 `Baseline Deadline Reliability Closure`；ML/GNN 保持 `DEFERRED - NOT REJECTED`。

证据：
- `research/frontier_cpp_gnn/native_solver_pipeline.cpp`
- `research/frontier_cpp_gnn/run_heuristic_v1r.py`
- `research/frontier_cpp_gnn/heuristic_v1r_config.json`
- `reports/HEURISTIC_V1_STATUS.md`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1r/summary.json`

### 2026-09-16: Baseline Deadline Reliability Closure

状态：`DONE / STOPPED`；Gate=`BASELINE-RELIABILITY PARTIAL`。`Heuristic V1R Final Release Benchmark` 保持 `NOT STARTED`。

- 固定诊断集为 `reverse:full_normal`、`forward:full_normal`、`reverse:150_normal`，每例 3 次 diagnostic replay；没有扩大案例或调参。
- 根因是 deadline-interrupted 末尾 RR task 会提交 timing-dependent partial patterns，且边界上最后 task 的 admission 也会变化。修复前 interrupted task 的新增 commit 在 0 与 16 之间变化。
- 完整 task duration median/P90/max=`0.965/219.174/805.272ms`；同 task jitter median/P90/max=`0.128/10.725/125.306ms`。一次性固定 global admission guard=`250ms`，未按 case/quality/reference 调度。
- Atomic Task Commit 规定只有完整 task 可提交；deadline/interruption/admission rejection 全部 commit 0。RR soft stop 与 5.0s global hard deadline 分离。
- 3x3 reliability replay：9/9 complete、0 hard violation、9/9 evaluator consistent、0 deadline failure；wall mean/median/max=`4667.246/4646.844/4876.728ms`，minimum headroom=`123.272ms`。
- 三例 objective 均 3/3 稳定，且相对 frozen B0 objective/gap delta 均为 0。两个 full case selected IDs/assignment 3/3 稳定；`reverse:150_normal` 为同 objective 的合法多解，未增加未被业务要求的 canonical tie-break。
- 三例 RR pool hash 均仍有 timing sensitivity，记录为 internal pool nondeterminism；Memory 全程 OFF，未运行 24-case benchmark。

证据：
- `native_master_types.hpp`
- `native_pattern_kernel.cpp`
- `native_solver_pipeline.cpp`
- `run_baseline_deadline_reliability.py`
- `tests/test_baseline_deadline_reliability.py`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/baseline_deadline_reliability/summary.json`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/baseline_deadline_reliability/deadline_trace.csv`
- `outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/baseline_deadline_reliability/replay_results.csv`

### 2026-09-16: Heuristic V1R Final Release Benchmark

状态：`RELEASED / STOPPED`；Gate=`HEURISTIC-V1 RELEASE`；质量状态=`QUALITY TARGET NOT YET MET`。

- 固定24-case one-shot完成：24/24 complete、0 unassigned、0 hard violations、24/24 evaluator一致、0 deadline failures、0 same-run regress；23 Optimal / 1 TimeLimit，不重试。
- wall mean/median/P90/max=`4471.007/4676.360/4771.081/4840.792ms`；minimum headroom=`159.208ms`；audited mean/max=`4478.261/4847.139ms`。
- mean/median/P90/worst gap=`4.255%/3.449%/8.444%/14.665%`；gap≤1/2/5%=`6/7/15`；Green/Yellow/Red=`7/8/9`。24/24 gap≤2%尚未完成，不与工程Release混淆。
- Memory started/augmented master=`2/2`，127 legal patterns、0 accepted、0 selected、ΔF=0；22例按固定slack跳过。冻结config/artifact/provenance通过。
- Heuristic V1 已满足第一版可用算法的正确性与5秒运行要求，但最终质量目标仍开放；全部V2 backlog为`NOT STARTED`，ML/GNN=`DEFERRED — NOT REJECTED`。
- 正式结果：`outputs/research/generator_v3_2_p65_rebaseline_2026-09-07/heuristic_v1r_final_release/`；报告：`reports/HEURISTIC_V1_STATUS.md`。历史HOLD/reliability结果保留。
