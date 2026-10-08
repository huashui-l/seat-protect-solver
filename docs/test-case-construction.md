# 测试用例构建与维护说明

本文是本项目测试数据的统一入口。它说明测试用例分哪几层、每层如何构建、哪些字段代表什么、哪些文件是事实来源，以及修改生成器或基准集后必须同步更新什么。

现有标尺的覆盖缺陷、保留/新增建议与新基准设计见 [2026-10-07 测试体系评估](test-case-assessment-2026-10-07.md)，实际输入统计见 [覆盖审计数据](provenance/test-case-coverage.json)。本文描述现状，不表示当前数据已经充分覆盖真实航班。

Benchmark v2 的 72 例组合、实际规模、PNR/SSR 设置、生成方法和已知缺口，见 [Benchmark v2 专门说明](benchmark-v2.md)。

## 1. 先看结论

本项目的测试数据不是由一套单一规则生成的，而是由以下几类数据组成：

| 层次 | 数量/范围 | 主要目的 | 当前事实来源 |
|---|---:|---|---|
| 核心单元与合成小例 | 按测试文件定义 | 检查评分、固定座位、局部约束和求解器状态 | `tests/` 下各测试文件 |
| 通用验证套件 | 13 个：11 合法、2 非法 | 用少量可读案例覆盖关键业务约束和非法输入 | [`tests/fixtures/general_validation_suite/README.md`](../tests/fixtures/general_validation_suite/README.md)、`cases.json`、`tests/general_validation_case_factory.py` |
| 单 PNR 规模诊断 | 可生成 5–10 人的 6 例 | 控制单组规模，观察定价/搜索开销 | `src/run_group_size_scaling_benchmark.py` |
| Formal24 | 24 个 | 固定方向、负载和难度组合，作为正式合成实验集 | [`data/generator_v3_2/d3_official_24cases_2026-09-07/manifest.json`](../data/generator_v3_2/d3_official_24cases_2026-09-07/manifest.json) |
| D4 smooth multi-aircraft | 16 个 | 覆盖更多机型，并把 normal 到 edge 平滑插值 | [`data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/manifest.json`](../data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/manifest.json)、[`scripts/generate_smooth_multi_aircraft_corpus.py`](../scripts/generate_smooth_multi_aircraft_corpus.py) |
| Benchmark v2 开发集 | 72 个 | 将转换、资源压力和 PNR 构成拆成独立因素；正式计划为 72 规格×5种子 | [`benchmark-v2-spec-2026-10-07.json`](provenance/benchmark-v2-spec-2026-10-07.json)、[`data/generator_v3_2/benchmark_v2_2026-10-07/manifest.json`](../data/generator_v3_2/benchmark_v2_2026-10-07/manifest.json)、[`scripts/generate_benchmark_v2.py`](../scripts/generate_benchmark_v2.py) |
| CG30 | 30 个 | 固定 C++/CG 对比基准：Formal24 加 6 个 D4 | [`docs/provenance/cpp5-cg30/manifest.json`](provenance/cpp5-cg30/manifest.json) |

其中，**请求规模和场景标签可以分别选择**。例如 `150_edge` 表示请求规模为 150、场景为 edge；不能把 edge 简化成“人数更多”。场景会同时改变 SSR 比例、组大小、保护空座、固定座位比例、条件隔离规则和旧座位分散程度，不保证实际求解难度单调增加。

## 2. 文件职责和事实来源

维护时应按下面的优先级理解数据：

1. **生成规则的事实来源**：`src/passenger_data_generator.py` 和 `src/aircraft_seatmap_generator.py`。
2. **D4 的组合方式和种子规则**：`scripts/generate_smooth_multi_aircraft_corpus.py`。
3. **冻结案例的身份、路径和哈希**：各 corpus 的 `manifest.json`。
4. **CG30 的参考分数、运行结果和耗时**：`references.json`、`comparison.csv` 和 `provenance.json`。
5. **给人阅读的案例目录**：[`docs/provenance/cpp5-cg30/case_catalog.md`](provenance/cpp5-cg30/case_catalog.md)。

`README.md` 和本文负责解释规则，但不替代 manifest、输入 JSON 或生成器代码。案例总表只描述已经冻结的案例，不应被当作新的生成规则。

Formal24 的 manifest 保留案例路径、输入哈希、配置哈希、生成器版本和来源提交；本次核对的 main 及当前分支包含全部 24 例 JSON。旧 manifest 的输入哈希对应 CRLF 字节，当前文件及 CG30 manifest 对应 LF 字节；语义一致，但原始字节哈希不同，核验时应明确区分。

分支边界：当前 `cg-gurobi-direct` 已暂存 main 的 D4 数据和部分 native 测试，但 D4 生成脚本及完整 native 实现尚未迁入。下文 D4 重生成命令应在包含对应脚本的 main 版本执行；不能仅凭测试文件存在认定当前分支已具备运行条件。

## 3. 一个案例包含什么

一次座位保护求解至少有三类输入：

### 3.1 旧机型座位图

旧座位图用于表达旅客原座位和原始座位属性。每个座位包含：

- `seatId`、`row`、`col`；
- `isWindow`、`isAisle`、`isExitRow`；
- `hasBassinet`、`extraLegroom`、`nearToilet`；
- `seatClass`。

### 3.2 目标机型座位图

目标座位图是实际分配和保护空座使用的资源集合。真实旅客座位和不可共享的保护空座都占用同一套目标座位容量。

### 3.3 同行组和旅客

输入按 PNR/同行组组织：

```text
groups[]
  ├─ groupId
  └─ psrs[]
       ├─ hostnum
       ├─ cabin
       ├─ oldSeat.seatNum
       ├─ newSeat.seatNum       # 可选，表示固定目标座位
       ├─ ssr
       ├─ needCared
       ├─ mandatoryRule
       └─ optionRule
```

一个同行组必须属于一个舱位。混舱同行组是输入错误，应该由校验拒绝，而不是由求解器通过换舱修复。

当前舱位代码映射为：`F/A → First`、`C/J → Business`、`W/P → PremiumEconomy`、`Y/E → Economy`。

## 4. 标准生成流程

`generate_groups()` 和 `generate_preassignments()` 实际执行的流程如下：

1. **读取旧、目标座位图**，建立排、列、过道、舱位、出口排、摇篮位和厕所邻近关系。
2. **选择场景配置**：`normal`、`stress` 或 `edge`。
3. **决定目标座位需求**。生成器同时考虑真实旅客座位和保护空座，确保需求不超过可转移的目标容量。
4. **抽取 PNR 组大小**，并把完整同行组分配到单一舱位。
5. **先生成 SSR、陪护和保护空座规则**，再构造座位。这样不会先生成一个普通座位分配、再附加互相冲突的特殊规则。
6. **构造旧机型座位状态**。旧座位配置按 `compact`、`same_row`、`nearby`、`scattered` 的合成分布生成，仅用于实验，不代表真实航空公司的历史分配分布。
7. **生成固定目标座位**。固定座位从目标机型中选择，并在写入前验证；需要陪护或保护空座的旅客不会被随意固定成无解状态。
8. **在旧、目标拓扑上检查特殊资源联合可行性**。特殊资源使用有限 pattern 集合和有界 DFS 检查；旧机型 witness 用于构造旧状态，目标机型 witness 见证所选特殊组的联合放置，不会自动写成 `newSeat`。这不能替代加入所有固定座位后的最终完整可行解验收。
9. **执行容量和语义校验**。失败时生成器直接报错，不把非法输入交给求解器。
10. **写入生成器诊断、随机种子、版本和案例元数据**，使案例可以复现和审计。

单个案例可以用以下命令生成：

```bash
python3 src/passenger_data_generator.py \
  --passengers 150 \
  --scenario stress \
  --seed 200 \
  --old-seatmap data/3-4-3seatmap.json \
  --new-seatmap data/3-3seatmap.json
```

省略 `--output` 时，生成器会写入本地 `data/generator_v3_2/`；生成数据不应直接作为未审查的新 benchmark 提交。

## 5. 机型和座位图

座位图由 [`src/aircraft_seatmap_generator.py`](../src/aircraft_seatmap_generator.py) 的预设生成。D4 manifest 当前记录六种布局：

| 布局 | 机型名称 | 总座位数 | 备注 |
|---|---|---:|---|
| `2-2` | `Generic-RJ-26-2-2` | 98 | 支线机，前舱 1-1、经济舱 2-2 |
| `2-3` | `Generic-NB-28-2-3` | 137 | 窄体机，前舱 2-2、经济舱 2-3 |
| `3-3` | `Generic-NB-32-3-3` | 184 | Formal24 的窄体目标/旧机型 |
| `3-4-3` | `Generic-WB-36-3-4-3` | 336 | Formal24 的宽体旧机型 |
| `2-4-2` | `Generic-WB-37-2-4-2` | 284 | D4 扩展布局 |
| `3-3-3` | `Generic-WB-40-3-3-3` | 339 | D4 扩展布局 |

预设还包含 Business/Economy 分舱、出口排、加长腿部空间、摇篮位和厕所邻近字段。布局名称描述座位排布，不等于实际旅客数。

当前 CG30 实际使用：

- Formal24：`3-4-3 → 3-3` 和反向 `3-3 → 3-4-3`；
- D4：`2-2 → 2-3` 和反向 `2-3 → 2-2` 的六个冻结案例。

D4 原始 corpus 还生成 `3-3 → 2-4-2` 和 `3-4-3 → 3-3-3`，但它们不自动等于当前 CG30 的案例。

Benchmark v2 当前已生成 1 个开发种子，覆盖 8 个双向机型转换、60%/85%/97% 共同可转移资源负载和 small/family/large 三种 PNR 构成，共 72 个案例。正式评测计划使用 5 个生成种子，共 360 个案例；`none`、`sparse` SSR 版本属于诊断变体，使用 `--special-profiles` 单独生成，不和正式 mixed 版本混在主分母中。

Benchmark v2 的特殊需求配置由规格文件中的 `special_profiles` 定义：

| 配置 | `required_ssrs` | 用途 |
|---|---|---|
| `none` | 空集合 | 检查没有 SSR 时的基础座位保护难度 |
| `sparse` | `WCHR` | 检查稀疏特殊需求，至少覆盖过道位和照护需求 |
| `mixed` | `CHD`、`WCHR`、`BSCT`、`EXST` | 主集覆盖照护、过道位、摇篮位和保护空座；其他类型按生成过程出现 |

`required_ssrs` 表示生成结果中必须至少出现的 SSR 类型；它不是要求每类固定出现一个，也不直接规定最终 SSR 比例。`mixed` 还会按配置中的 `ssr_ratio` 生成额外特殊旅客，生成器会在目标机型资源不足时重采样或关闭随机附加隔离规则，并把最终统计写入案例的 `ssrCounts` 和 `generatorDiagnostics`。因此审查案例时应以输入 JSON 和 manifest 统计为准。

## 6. 规模的定义

案例中有四个容易混淆的数量：

| 字段/概念 | 含义 |
|---|---|
| `requestedPassengerCount` | 生成器收到的请求规模，例如 50、100、150 或 65 |
| `travelerCount` | `groups[].psrs[]` 中真实旅客人数，不包含保护空座 |
| `protected_seat_count` | SSR 规则额外要求独占的保护空座数量 |
| `passengerCount` / `targetSeatDemand` | 真实旅客座位加保护空座，是目标资源需求 |

生成器的 `sizeLabel` 使用目标需求和可转移容量判断：

- 未达到容量时，标签通常是实际目标需求，例如 `50`、`100`、`150`；
- 请求规模达到或超过可用容量时，标签是 `full`；
- `full` 不表示一定有同样数量的真实旅客，因为其中包含保护空座。

Formal24 的组合为：

```text
方向：3-4-3 → 3-3、3-3 → 3-4-3
规模：50、100、150、full
难度：normal、stress、edge
总数：2 × 4 × 3 = 24
```

Formal24 的 `full` 使用两种机型共同可用容量 184 作为资源口径；因此 `travelerCount` 可以小于 184。

D4 的规模由每个机型转换在脚本中的 `TRANSITIONS` 定义。当前脚本设定为：

| 转换 | 请求规模 |
|---|---:|
| `2-2 → 2-3` | 65 |
| `2-3 → 2-2` | 65 |
| `3-3 → 2-4-2` | 120 |
| `3-4-3 → 3-3-3` | 180 |

## 7. 难度的定义

### 7.1 Formal24 的三档场景

`normal`、`stress` 和 `edge` 是独立于规模的场景配置。核心参数如下：

| 参数 | normal | stress | edge | 作用 |
|---|---:|---:|---:|---|
| `ssr_ratio` | 0.07 | 0.15 | 0.22 | 特殊服务旅客比例 |
| `preassign_ratio` | 0.20 | 0.10 | 0.05 | 固定目标座位比例 |
| `option_rule_ratio` | 0.25 | 0.40 | 0.50 | 软偏好规则比例 |
| `same_subrow_rule_ratio` | 0.10 | 0.25 | 0.35 | 同子排隔离比例 |
| `same_row_rule_ratio` | 0.05 | 0.15 | 0.25 | 同排隔离比例 |
| `both_side_empty_ratio` | 0.01 | 0.02 | 0.03 | 双侧保护空座比例 |
| `minimum_both_side_empty` | 1 | 2 | 3 | 最低双侧保护空座覆盖数 |

旧座位分布也随难度变化：

| 旧座位模式 | normal | stress | edge |
|---|---:|---:|---:|
| `compact` | 0.35 | 0.25 | 0.15 |
| `same_row` | 0.25 | 0.25 | 0.20 |
| `nearby` | 0.25 | 0.25 | 0.25 |
| `scattered` | 0.15 | 0.25 | 0.40 |

PNR 组大小权重也会变化：

| 组大小 | normal | stress | edge |
|---:|---:|---:|---:|
| 1 | 0.4000 | 0.230 | 0.130 |
| 2 | 0.3400 | 0.250 | 0.180 |
| 3 | 0.1300 | 0.170 | 0.160 |
| 4 | 0.0800 | 0.130 | 0.140 |
| 5 | 0.0300 | 0.080 | 0.110 |
| 6 | 0.0150 | 0.050 | 0.090 |
| 7 | 0.0030 | 0.035 | 0.065 |
| 8 | 0.0010 | 0.025 | 0.055 |
| 9 | 0.0005 | 0.020 | 0.040 |
| 10 | 0.0005 | 0.010 | 0.030 |

因此，场景标签不能单纯由旅客数量解释，也不是已经测量过的难度等级。规模影响资源压力，场景参数共同改变约束密度和组合搜索；比较某个因素的影响需要另外构造受控案例。

表中是请求参数，并非保证实现的比例。例如生成器强制七类 SSR 均出现，50_normal 的实际 46 名旅客中有 7 名 SSR，占 15.2%。满共同可转移容量时还会关闭两类随机 SSR 隔离规则。评测应使用实际统计值。

### 7.2 D4 的平滑难度

D4 不直接重复三档名称，而是定义四个插值点：

| 标签 | 插值参数 | 含义 |
|---|---:|---|
| `d00` | 0 | 完全使用 `normal` |
| `d33` | 1/3 | `normal` 到 `edge` 的三分之一 |
| `d66` | 2/3 | `normal` 到 `edge` 的三分之二 |
| `d100` | 1 | 完全使用 `edge` |

插值由 `interpolate()` 完成；整数参数会四舍五入，其他数值按线性插值，非数值字段在中点前后选择对应场景值。D4 使用固定 `SEED_BASE = 20260925`，实际种子为：

```text
seed = 20260925 + transition_index * 100 + level_index
```

## 8. SSR、陪护和保护空座

当前生成器定义七类 SSR：

| SSR | 主要约束 |
|---|---|
| `CHD` | 不允许出口排，需要同组照护者 |
| `WCHR` | 不允许出口排，需要过道位，需要同组照护者 |
| `BLND` | 不允许出口排，需要同组照护者 |
| `BSCT` | 不允许出口排，需要摇篮位，需要同组照护者 |
| `UM` | 不允许出口排，当前配置不要求成人陪护 |
| `EXST` | 允许出口排，需要单侧保护空座 |
| `CBBG` | 允许出口排，需要单侧保护空座 |

实现还会根据机型和资源情况处理：

- `needCared`：是否需要同组代理照护者；
- `needSingleSideEmpty`：独占一个同子排保护空座；
- `needBothSideEmpty`：独占两侧保护空座；
- `sameRowNoOtherSSR`：同排不能出现其他 SSR；
- `sameSubRowNoOtherSSR`：同子排不能出现其他 SSR；
- `mandatoryRule`：硬约束；
- `optionRule`：只影响软分的偏好。

随机规则不会无条件叠加。生成器会关闭或重采样与当前机型资源冲突的附加隔离规则，并对特殊角色重新采样，避免“案例本身没有可行解”被误当成算法失败。

## 9. PNR/同行组的定义

一组 PNR 对应一个 `group`，组内旅客数量是 `len(group["psrs"])`。本项目关心的“最大 PNR 人数”就是所有组中这个长度的最大值。

组生成规则包括：

- 一个组只能属于一个舱位；
- 组大小按场景权重采样，范围为 1–10 人；
- 需要照护的旅客会生成同组代理照护者，并安排可验证的邻接关系；
- 同一个 PNR 可以包含普通旅客和 SSR 旅客；
- 10 人组是测试覆盖项，不是每个正式案例必须出现的生成不变量；
- 随机种子、组大小权重和完整输入 JSON 一起决定实际 PNR 数量和最大 PNR 人数。

因此，`requestedPassengerCount` 不能直接推出 PNR 组数或最大 PNR 人数。查看冻结案例时，应使用 [`case_catalog.md`](provenance/cpp5-cg30/case_catalog.md) 中的“组数”和“最大 PNR”列，或直接统计案例 JSON。

## 10. 固定目标座位和旧分配

固定目标座位写在旅客的 `newSeat.seatNum` 中。生成器默认按场景比例生成：normal 约 20%、stress 约 10%、edge 约 5%。比例降低同时改变搜索自由度和几何阻塞程度，不能单凭这个设置判断整体更难或更简单。

固定座位生成遵循以下限制：

- 固定座位必须来自目标机型；
- 同一目标座位不能被重复固定；
- 需要照护的旅客只有在照护关系仍可行时才固定；
- 需要单侧或双侧保护空座的旅客默认不直接固定；
- 固定后重新执行案例校验。

旧座位是实验输入的一部分，但不是目标答案。旧座位模式是合成的 `compact`、`same_row`、`nearby`、`scattered`，用于改变软分和重排压力，不宣称代表真实航班历史分配。

## 11. 可行性和输入验收

生成器在写出案例前至少检查以下内容：

1. 旅客字段、组结构和舱位映射完整；
2. 组内没有混舱；
3. 旧座位和目标座位满足基本舱位与 SSR 属性；
4. 照护关系存在同组、合格、可邻接的代理者；
5. 保护空座资源不会超过对应舱位容量；
6. 真实旅客座位加保护空座不超过目标机型容量；
7. 固定目标座位不冲突且仍保留整组可行放置；
8. 源/目标座位拓扑上的特殊资源可以由有限 pattern 和有界 DFS 见证；
9. 满载案例中不会强行加入目标机型无法容纳的附加随机隔离规则。

通用验证套件另外保留非法混舱组和目标容量超限反例。这两个案例预期由输入校验拒绝，不应被修改成“求解器必须修复”的案例。

## 12. Formal24、D4 和 CG30 的关系

### 12.1 Formal24

Formal24 是 `generator-v3.2` 的 24 个固定合成输入：

```text
2 个方向 × 4 个规模 × 3 个场景 = 24 个案例
```

案例 ID 形如：

```text
forward:150_edge
reverse:full_normal
```

manifest 记录案例路径、输入哈希、配置哈希、生成器版本和来源提交。改变任一输入 JSON、生成配置或生成器代码，都不能继续使用旧哈希作为同一冻结案例。

### 12.2 D4

D4 由四个机型转换、四个难度插值点构成，共 16 个案例：

```text
4 个转换 × {d00, d33, d66, d100} = 16 个案例
```

脚本同时生成六种座位图和每个案例的 manifest 元数据。D4 案例 ID 形如：

```text
d4:rj22_to_nb23:d33
```

### 12.3 CG30

CG30 是一个冻结的对比集合，不是“重新按规则取 30 个案例”的动态切片：

- Formal24 的 24 个案例全部纳入；
- 另外纳入 manifest 中列出的 6 个 D4 案例；
- 参考分数在 `references.json` 中冻结；
- 运行合法性和性能在 `comparison.csv` 中记录；
- 来源和审计信息在 `provenance.json` 中记录；
- 人类可读的规模、旅客、PNR、最大 PNR、固定座位和耗时见 `case_catalog.md`。

如果要改变 CG30 的成员、参考分数或运行配置，必须把它视为新的基准版本，并更新 manifest、references、comparison、provenance 和案例总表。

## 13. 测试结果应记录什么

每次新算法或新生成器版本的比较，至少记录：

- `case_id`；
- 输入 JSON 哈希、座位图哈希和生成器版本；
- 运行配置和时间预算；
- 是否完整分配；
- 未分配人数；
- 硬约束违规数及分项；
- 软分和独立审计误差；
- `solver-wall` 和 `process-wall`；
- 是否触发修复、回退或超时；
- 若是随机/限时算法，至少报告重复运行的最好值、中位数和最差值。

耗时口径：

- `solver-wall`：构造、修复、搜索、native 校验和结果序列化的求解器时钟；
- `process-wall`：从进程启动到退出的外部墙钟时间，包含启动和 I/O；
- 两者不应混为一个“算法耗时”。

## 14. 维护流程

### 14.1 只新增一个普通验证案例

1. 在 `tests/fixtures/general_validation_suite/cases.json` 增加紧凑规格；
2. 在 `tests/general_validation_case_factory.py` 确认规格能扩展为正式输入；
3. 写清案例意图、合法/非法预期和显式参考解；
4. 更新 `tests/fixtures/general_validation_suite/README.md` 的覆盖列表；
5. 运行通用验证测试，并记录未分配、硬违规、软分和耗时。

### 14.2 修改生成器规则

1. 修改 `src/passenger_data_generator.py` 或座位图生成器；
2. 更新 `GENERATOR_VERSION`，不要静默复用旧版本号；
3. 使用明确的 seed 生成新版本 corpus，保留旧版本输入；
4. 重新运行 `validate_case` 和相关测试；
5. 更新 manifest 中的路径、哈希、配置哈希和来源提交；
6. 若影响 CG30，重新生成 `case_catalog.md`、`comparison.csv` 或参考工件，并说明基准版本变化；
7. 在 PR 或提交说明中写明旧案例是否仍兼容。

### 14.3 重新生成 D4

```bash
python3 scripts/generate_smooth_multi_aircraft_corpus.py
```

生成后检查：

- manifest 是否仍为 16 个案例和 6 个座位图；
- 四个转换和四个难度等级是否完整；
- seed 是否可复现；
- 每个案例的 `passengerCount`、`travelerCount`、`fixedSeatCount` 是否与输入一致；
- `validate_case` 是否全部通过。

### 14.4 生成 Benchmark v2

默认命令生成 1 个开发种子、72 个 mixed SSR 案例：

```bash
python3 scripts/generate_benchmark_v2.py
```

正式候选集使用规格文件声明的 5 个种子：

```bash
python3 scripts/generate_benchmark_v2.py --formal \
  --output data/generator_v3_2/benchmark_v2_2026-10-07-formal
```

`none` 和 `sparse` 只用于诊断对照。可以在单独输出目录生成一个或多个变体，避免覆盖 mixed 主集：

```bash
python3 scripts/generate_benchmark_v2.py \
  --special-profiles none,sparse \
  --output data/generator_v3_2/benchmark_v2_2026-10-07-diagnostic
```

生成后至少检查 `manifest.json` 的 `case_count`、`generation_failures`、每个案例的 `sha256`，以及输入中的 `travelerCount`、`targetSeatDemand`、`protectedSeatCount`、`fixedSeatCount`、`pnrSizeHistogram` 和 `ssrCounts`。诊断变体不能与 mixed 主集混合汇总，否则会把 SSR 维度重复计入样本数。

### 14.5 修改 CG30

CG30 成员不是由 `case_catalog.md` 手工决定。应先修改并审查 [`docs/provenance/cpp5-cg30/manifest.json`](provenance/cpp5-cg30/manifest.json)，然后同步：

1. `references.json`；
2. `comparison.csv`；
3. `provenance.json`；
4. `case_catalog.md`；
5. 相关 README 或实验说明。

所有冻结输入和结果都应保留哈希。只修改总表文字而不更新机器可读清单，会造成文档与 benchmark 不一致。

## 15. 常用核对命令

查看生成器版本和场景配置：

```bash
rg -n "GENERATOR_VERSION|SCENARIOS|SSR_PROFILES|group_size_weights" \
  src/passenger_data_generator.py
```

查看 D4 组合和种子：

```bash
rg -n "TRANSITIONS|LEVELS|SEED_BASE|interpolate" \
  scripts/generate_smooth_multi_aircraft_corpus.py
```

查看 CG30 数量和案例 ID：

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path("docs/provenance/cpp5-cg30/manifest.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("case_count =", d["case_count"])
for item in d["cases"]:
    print(item["case_id"])
PY
```

运行核心测试：

```bash
python3 -m unittest \
  tests.test_config_and_scoring \
  tests.test_fixed_seat_semantics \
  tests.test_solution_comparison \
  tests.test_general_validation_smoke \
  tests.test_general_validation_cases
```

Formal24、长列生成和研究 benchmark 不属于普通 pull request 的默认测试；需要外部数据或 native 构建时，应按 [`tests/README.md`](../tests/README.md) 的测试边界执行。

## 16. 维护原则

- 生成器代码定义规则，manifest 定义冻结案例身份，案例总表负责展示。
- 不手工编辑已经冻结的输入 JSON 来“修正”结果；应修改生成规则、重新生成并更新哈希。
- 随机案例必须记录 seed；不能只记录最终 JSON 而丢失生成条件。
- 规模字段、真实旅客数、目标座位需求和保护空座数量必须分开记录。
- 难度标签必须说明具体约束来源，不能只用旅客数量替代难度。
- 非法输入案例要验证“入口拒绝”，不能把拒绝改写成算法缺陷。
- 新增 benchmark 必须同时增加机器可读元数据和人类可读说明。
- 任何改变输入、生成器、求解配置或参考工件的提交，都要在变更说明中指出是否破坏旧结果的可比性。

本文维护目标是让后续开发者只需从这里出发，就能定位生成规则、冻结案例、验证入口和结果口径；具体参数变化仍以链接的代码和 manifest 为准。
