# 飞机座位保护与重分配

> **私有协作仓库边界。** 本仓库是从内部研究快照通过显式允许清单建立的核心代码候选仓库，
> 不包含 Formal24、正式业务数据、完整 benchmark 输出、模型权重、预编译二进制或第三方工具链。
> 文档中的历史结果用于说明研究状态，并不使缺失 artifact 成为默认运行依赖。
>
> Python 基线可用于核心算法和独立 evaluator 测试。raw native feasibility 与 RR/master
> C++ 源码已迁入，但 Full C++ 对 Rich Python active stages 的覆盖仍为
> **FAIL / IN PROGRESS**，不能表述为 production-ready。所有整数参考均称为
> `current best-known integer reference`，不是 global optimum；代理实验也不是生产收益。
>
> 当前可直接运行的范围、测试分层和 native 依赖分别见
> [tests/README.md](tests/README.md) 与 [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md)。
> 会生成结果的脚本应写入被 Git 忽略的本地目录；需要外部数据的命令在取得经授权的
> 数据后才能运行。

本项目研究机型变更后的旅客座位重分配：读取旧机型座位、目标机型座位和同行旅客组，在满足固定座位、特殊旅客服务（SSR）、陪护关系及保护空座等硬约束的前提下，保证每名真实旅客都有座位，严禁改变旅客预订舱位，并尽量保持座位属性、个人偏好和同行组紧凑度。

当前代码包含两条互补路径：

- 限时启发式：用于在线快速产生完整可行方案，当前生产基准为5秒；
- 列生成：用于离线产生列池整数参考解，并在定价条件满足时给出完整列空间根LP上界。

任务需求、符号定义、硬约束、软目标、同行组列生成模型，以及每项需求在代码中的处理位置，详见[任务需求、数学模型与实现说明](docs/任务需求、数学模型与实现说明.md)。README只保留项目入口和运行方法，该文档作为当前需求与模型的正式技术规格。

## 1. 当前状态

当前正式研究输入为generator-v3.2双向24例；24/24 source/final validation、资源需求和同seed复现均通过。
H5/H20均为24/24完整合法，Round-robin继续作为no-GNN generation baseline。当前列池参考固定命名为
`generator-v3.2 current best-known union-pool integer reference`，它只是现有online/search pattern union上的
整数参考，不是global optimum。

P6.5-v3当前Gate为`GENERATION LIMITED / component construction limited`：pattern/resource observability很高，
但现有online component constructor无法稳定组成接近K10 Oracle价值的联合修复组件。研究已停在
`P6.5a-1 Targeted Component Expansion Diagnostic`（`NOT STARTED`），不会直接跳到targeted pattern generation。

最新状态和证据路径见[frontier_cpp_gnn 路线与进度](research/frontier_cpp_gnn/ROADMAP_AND_PROGRESS.md)。2026-08及更早的
阶段报告与原始输出已归档到`reports/archive/`和`outputs/archive/`，只用于历史追溯；历史方法台账见
[算法方法尝试效果台账](reports/archive/2026-08_current-at-the-time/2026-08-17_算法方法尝试效果台账.md)。

## 2. 任务需求

### 2.1 输入

一次求解需要三类输入：

1. 旧机型座位图：旅客原座位及原仓位的依据；
2. 新机型座位图：可分配座位、排/列、过道、出口排、摇篮位和仓位信息；
3. 同行组数据：每名旅客的旧座位、固定新座位、SSR、陪护需求、座位偏好及单侧/双侧保护空座需求。

### 2.2 硬约束

- 每名真实旅客最终恰有一个目标座位；
- 同一座位不能被多名旅客占用；
- 每名旅客只能分配到与其`cabin`一致的目标舱位，任何跨舱分配均计为硬约束违规；
- 同一同行组内所有旅客必须属于同一舱位，混舱组直接拒绝；
- 固定新座位必须保持，输入冲突时明确报错；
- SSR的出口排、过道、摇篮位及排/小排互斥规则必须满足；
- 需要陪护的旅客必须与同组成年陪护者满足允许的相邻关系；
- 单侧或双侧保护空座是不可共享的独占资源；
- 保护空座不得被其他旅客占用。

### 2.3 求解优先级

项目采用字典序目标，不用一个加权和近似硬约束：

1. 硬约束违规数最少，最终必须为0；
2. 未分配人数最少，最终必须为0；
3. 只在完整可行方案之间最大化软分。

### 2.4 软分

软分采用扣分形式，数值越接近0越好，包含：

- `score_s`：旧新座位距离；
- `score_v`：座位价值变化；
- `score_p`：旅客偏好；
- `score_c`：同行组座位相对重心的最大横向、纵向偏差；
- `score_b`：婴儿对非同行旅客的影响；
- 舱位是硬约束，不设置舱位软分或`w_class`；非法跨舱只通过`cabin_mismatch_violation`计入硬约束违规。

启发式内部增量评分与`allocation_evaluator.py`的完整评分必须一致；最终方案若未分配、违规或评分不一致，将拒绝正常输出。

## 3. 需求分析与问题分解

该问题同时包含旅客—座位匹配、同行组几何、保护资源和跨组容量冲突。直接建立全体旅客的完整整数模型会产生很大的组合空间，因此采用两种不同目标的算法：

- 在线需求关注“尽快得到完整可行解”，适合限时构造和局部搜索；
- 离线验证关注“解距离理论最好值还有多远”，适合按同行组分解的列生成。

两种算法共用同一套硬约束语义和独立评分器，避免线上算法和离线模型评价不同的问题。

在当前业务定义下，同行组、陪护、保护空座、SSR资源和婴儿影响都不跨舱，目标座位资源也按舱位互斥，因此完整问题可严格分解为各舱位子问题。当前通常依次执行Business和Economy两次内核；商务舱规模小，先快速处理，经济舱获得总时限内的主要剩余预算。完整方案的软分、列池整数解和有效LP上界均可按舱位相加，但只有每个舱位都取得相应上界证书时，才能报告全局上界。

## 4. 算法一：限时启发式

入口实现为`src/heuristic_seat_allocator.py`，主要流程如下：

1. 建立新旧座位拓扑，按实际座位需求分配各搜索阶段的时间预算；
2. 预检查并锁定固定新座位及其确定性保护资源；
3. 只在旅客预订舱位内生成候选座位并排序；
4. 先补齐固定座位锚定组，再处理需要陪护的SSR；
5. 对失败的陪护关系执行组内联合回溯；
6. 使用小组DFS、束搜索和候选截断安排其余同行组；
7. 对未分配旅客执行局部搬移和整组重建救援；
8. 按紧凑度损失和跨排程度建立最差组修复队列；
9. 执行单人移动、两人交换和三人循环的VND；
10. 为问题组枚举“刚性平移—小幅变形—完全重建”三级完整座位模式；
11. 按坏组、资源冲突和阻塞关系自适应扩大联合组件，不再固定限制为2至4组；
12. 在动态冲突组件上执行LNS和带扩张半径Local Branching的受限模式MIP；
13. 对20秒满载缩容且存在保护空座压力的舱段，补充全舱排窗紧凑块模式并执行保护组联合模式MIP；
14. 对旅客占座率至少93%的满载缩容舱段，从VND后段转移2秒，在保护组MIP完成后用当前模式池LP对偶为修复队列前13个无固定座位特殊组各补至多1个负检验数模式；
15. 将当前完整分配作为全局限制模式MIP的HiGHS incumbent，联合组合普通组与新增特殊组模式；
16. 独立复算硬约束和软分，只返回完整有效结果。

启发式的主要局限不是单纯运行速度，而是顺序构造、候选截断和邻域规模。若一个改进需要同时搬动5组以上，或候选模式没有覆盖关键连续块，延长时间也可能仍停留在同类局部解。

## 5. 算法二：列生成与LP上界

入口实现为`src/exact_column_generation.py`。一列表示一个同行组的完整整数放置，包括组内全部旅客座位、保护空座、SSR资源和软成本。

### 5.1 MP1限制主问题

MP1从每个同行组当前已有列中选择一列，并处理：

- 每组恰选一个完整模式；
- 真实座位及保护空座资源容量；
- 跨组条件SSR资源；
- 婴儿对其他同行组旅客的影响。

### 5.2 MP2同行组定价

MP2固定一个同行组，在该组各旅客所属舱位的完整座位—保护资源域中寻找负约化成本列。跨舱座位不进入定价变量域。当前策略为：

- 使用资源位图表示占座与保护空座冲突；
- 使用历史最佳模式和当前RMP最好列作为incumbent；
- 使用旅客域最小成本后缀界和行列窗口界剪枝；
- 对完全同质旅客消除排列对称性；
- DFS未自然关闭时，只对该未认证组回退完整域HiGHS MIP；
- 多个同行组并行定价，每个HiGHS实例保持单线程。

对9人及以上、物理域与逐座成本完全相同且无耦合约束的同行组，优先使用按排位图、同质旅客计数的精确标签DP；不满足适用条件时自动回退到通用DFS/HiGHS。DFS超时时返回覆盖完整域的根松弛下界，HiGHS有有效best bound时再取较强下界。

### 5.3 根LP流程

1. 用启发式完整方案初始化同行组列和整数incumbent；
2. 求解当前MP1并取得对偶价格；
3. 快速定价有希望的组，发现负列后加入MP1并重新求解；
4. 快速阶段无新列时，对所有未认证组使用原始对偶精确定价；
5. 任一组找到新负列则继续迭代；
6. 所有组证明无负列时，输出精确完整列空间LP上界；
7. 若所有组都有严格定价下界，可输出安全修正上界；
8. 任一组既未闭合又没有安全下界时，上界必须留空。

候选列发现使用自适应对偶稳定化：根据RMP改善和是否产生新列动态调整平滑强度；最终认证始终使用原始对偶。若全部完整列的座位资源总数恰好等于目标座位数，第二阶段会把座位容量不等式合法强化为集合划分等式。

### 5.4 三种结果不能混用

- `H`：限时启发式完整可行解；
- `I`：当前列池上的整数可行解，不证明全局整数最优，也不是上界；
- `U`：经过完整定价或安全约化成本修正得到的LP上界。

`I-H`表示已经实际找到的可实现改善；`U-H`是相对上界的差距，安全U可能较宽。达到时间或节点限制时，当前受限MP1值不能冒充U。

## 6. 数据生成与实验设计

`src/passenger_data_generator.py`的generator-v3.2先固定资源需求、组规模和舱位，再生成旅客、SSR、陪护与保护规则；随后分别在source/target拓扑上用有限special-pattern集合与有界DFS验证联合可放置性。source witness作为constructive old assignment的硬资源起点，target witness仅作隐藏可行性证明且不会写入`newSeat`。正式场景的组规模只按`group_size_weights`采样；10人组是测试体系覆盖项，不是每个正式算例的生成不变量。旧座位采用明确标记为synthetic的`compact / same_row / nearby / scattered`场景配置，不代表真实航空分布，也不以soft objective最优为目标。容量检查仍按舱位分别统计“真实旅客座位 + 不可共享保护空座”；混舱同行组或任一舱位超载的数据都会被拒绝。

当前24组实验由以下组合组成：

- 方向：3-4-3→3-3、3-3→3-4-3；
- 负载：50、100、150、full；
- 难度：normal、stress、edge。

`full`表示真实旅客座位与保护空座合计达到两机型共同容量184，不一定有184名真实旅客。正式case允许不出现10人组；10人大组由独立测试覆盖。生成数据覆盖CHD、WCHR、BLND、BSCT、UM、EXST和CBBG七类SSR。正式数据未进入本协作仓库，本段只保留输入契约说明。

## 7. 项目结构

```text
seat-protect/
├─ config.json                         算法配置；其中数据路径是外部数据契约
├─ configs/                            研发配置说明与30秒研究配置
├─ docs/                               任务规格、依赖与来源清单
├─ src/                                Python算法、评估器和辅助脚本
├─ research/frontier_cpp_gnn/          active native源码及历史路线说明
├─ tests/                              核心、native和手动测试
└─ .github/                            PR模板；CI将在依赖闭环稳定后加入
```

模型规格见[任务需求、数学模型与实现说明](docs/任务需求、数学模型与实现说明.md)，依赖见[DEPENDENCIES](docs/DEPENDENCIES.md)，测试边界见[tests/README.md](tests/README.md)。

## 8. 安装

建议使用Python 3.11。

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-column-generation.txt
```

`requirements.txt`包含核心数值与可视化依赖；列生成和完整求解路径额外依赖`highspy`。本阶段未迁移GNN训练代码，因此核心依赖不含PyTorch或scikit-learn。

## 9. 可运行范围

### 9.1 生成一组数据

```powershell
python src/passenger_data_generator.py --passengers 150 --scenario stress --seed 200
```

省略`--output`时脚本会写入被Git忽略的本地`data/`目录。生成数据不应直接提交，必须经过合成性和分发审查。

### 9.2 运行5秒启发式并生成对比图

```powershell
python src/allocation_visualizer.py --config config.json --output-dir outputs/manual_demo --no-show
```

该命令需要未随仓库分发的座位图和同行组数据。`config.json`保留相对数据路径作为输入契约，不能把它理解为克隆后即有正式数据。

### 9.3 运行单组列生成

```powershell
python src/exact_column_generation.py --config config.json --output outputs/manual_column_result.json
```

该命令同样需要外部数据和`highspy`，不属于普通PR验证。

### 9.4 运行双向24组统一实验

```powershell
powershell -ExecutionPolicy Bypass -File .\run_24case_h5_h20_cg.ps1
```

Formal24未进入仓库，因此该脚本仅保留协议和历史复现入口，本地克隆默认不能运行。

可选参数：

```powershell
.\run_24case_h5_h20_cg.ps1 `
  -ColumnTotalSeconds 3600 `
  -Mp2Seconds 120 `
  -PricingWorkers 4 `
  -RestrictedMipSeconds 30 `
  -RunName cabin_decomposed_2026-08-23
```

该脚本只保留JSON和最终Markdown，不再生成`.log`文件。重复运行时，列生成批处理会复用或跳过已完成算例。

### 9.5 运行核心测试

```powershell
python -m unittest tests.test_config_and_scoring `
  tests.test_fixed_seat_semantics `
  tests.test_solution_comparison -v
```

缺失正式数据的测试会以明确原因跳过。native smoke只接受从当前源码构建的EXE，具体见[tests/README.md](tests/README.md)。不要在普通PR中运行Formal24或长列生成。

## 10. 当前研究问题

### 10.1 大组LP认证

受控实验中，5至7人组在约1秒内完成，8人组进入临界区，9至10人组在120秒MP2时限内不能稳定闭合。真实数据中，带SSR、陪护或保护空座的7至8人组也可能成为瓶颈。下一步需要改进MP2状态表示、下界和跨轮复用，而不是只增加总时限。

### 10.2 启发式软分

舱位现已从软偏好改为不可跨越的硬可行域，最终24组H20全部完整合法。结构门控模式池显著改善正向`100_edge`和`150_edge`。针对正向`full_stress`新增的后置特殊组定价在两次20秒重复中均由-936.072提高到-907.939，使I-H20%由约8.63%降到5.80%；该值尚未进入完整24组正式汇总。方法在`full_edge`上由旅客占座率门控关闭并保持-819.805。安全粗U不应用于判断启发式质量。

## 11. 维护约定

- 冻结benchmark与历史结果保留在未导入的内部研究档案中；协作仓库不以其作为默认运行依赖；
- 临时调试日志不进入项目，`.gitignore`已忽略所有`.log`；
- 历史实验、报告和一次性原型不进入核心协作仓库；确有需要时通过独立PR和明确允许清单导入；
- 任何LP上界必须保留认证来源；无证书时字段置空，不能用0或受限主问题值替代。
