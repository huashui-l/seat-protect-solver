# C++ 5秒启发式实验与复现索引

本目录发布2026-10-09至2026-10-10的合成测试实验数据。算法代码按模块维护：
启发式位于 [`native/`](../../native/README.md)，列生成位于 `src/cg_gurobi/`，
直接求解器位于 `src/direct_gurobi/`。公共 benchmark 脚本仅负责运行与比较。

## 当前采用版本

- 分支：`feat/cpp5-quality-20261009`；算法提交：`03d36a842251bd4d927b4fd20c8c37ee761f43ad`。
- 配置：[`config_native_rich_5s.json`](../../configs/config_native_rich_5s.json)。
- 主验收：归档中的 `rounds/cpp5_quality_round56_paired.json`；满载补充：`rounds/cpp5_quality_round57_screen.json`。
- Formal24 + D4共40例，40/40完整合法、零未分配、零硬违规、独立评分一致。
  39例有有效冻结CG整数参考，38例 gap ≤2%，平均0.458865%，最差2.315746%。
- 当前版本共62次验收调用（40主测试、18固定重复、4满载补充），全部合法且完整进程耗时小于5秒；
  最大4.879259秒。配对新旧版本合计124次，不能将124次全部称为当前版本调用。
- `d4:wb343_to_wb333:d100` 仍未稳定达到2%目标。`forward:full_edge` 的CG在150秒和600秒均未产生完整合法解，
  不参与参考gap统计。不完整输出保留失败证据，不能当作有效方案。

交互式比较见[结果汇总 HTML](../benchmark-2026-10-10/结果汇总.html)，可下载规范化逐例JSON，
查看完整分数、分项、重复测试、预算、失效状态和证据哈希。CG与直接求解结果来自既有实验，
生成报告时未重跑。预算为启发式5秒、求解器150秒及有条件600秒补充；报告同时保留实际进程时间，
求解器自身限时不等于完整进程严格限时。

## 最优性证据的解释

整数参考gap不是理论最优性gap。直接求解600秒的 `forward:50_normal` 记录为 `optimal`，
完整合法，目标值约−221.115，与模型全局界一致，MIPGap为0。

CG在150秒记录12例 `proven_optimal`，600秒新增5例，共17个不同实例。
其中12例的已认证根LP界与合法整数解在1e−6内闭合，可由归档字段直接复核。
另外5例的根LP尚未闭合：`forward:100_normal`、`reverse:100_normal`、`reverse:50_stress`、
`d4:rj22_to_nb23:d33`、`d4:nb23_to_rj22:d33`。CG实现也可以通过 branch-and-price 树关闭给出最优性，
因此不能仅凭根LP未闭合否定标记；但当前benchmark记录没有保留完整最终树界及日志，
这5例需要补充该证据才能独立复核。

C++结果在以下6例达到已闭合的CG界，具有外部最优性证据：`forward:50_edge`、`forward:50_normal`、
`reverse:50_normal`、`d4:rj22_to_nb23:d00`、`d4:nb23_to_rj22:d00`、`d4:nb23_to_rj22:d66`。
WB `d100` 尚无最优性证明。HTML中的“记录为已证最优”统计是求解记录的标记数量，不能扩展为全体实例均已独立审计。

## 数据与版本边界

- [`experiment-data.zip`](experiment-data.zip)：516个原始文件，96,962,687字节未压缩数据，压缩后7,546,317字节。
- [`manifest.json`](manifest.json)：压缩包及每个成员的字节数、SHA-256、原始位置；各轮接受标记、提交；40个实验/备份分支与提交。
- `rounds/`：59份轮次记录，包括主测试、筛选、诊断和消融；不是59轮全部通过正式验收。
- `capture/`：版本目录下的原始JSON、历史测试脚本、LP和SOL文件。诊断运行可能有额外采集开销，不能替代正式5秒验收。
- `solver-records/`：5份原始求解记录。历史C++记录用于追溯，不代表当前采用版本；当前结果以round56/57为准。

代码版本由Git分支保存，不在压缩包中重复打包源码。`experiment/cpp5-*` 保存被比较过的变体，
`archive/pre-cpp5-*` 保存变更前检查点。未接受或被后续替换的变体不会作为正式实现入口。
清单中的 `accepted` 为各轮原始结论；缺失值表示原记录未提供，不能推断为通过。
历史路径与历史文件名按原样保留用于证据追踪，不是新的模块组织。
本次不包含可执行文件、DLL、第三方SDK、许可证或模型权重。

解压后可按清单检查原始文件；压缩包SHA-256为：

```text
a900aa85437d2c83494abd88da50351c3608e0378681292d2b54f41ef926df5e
```

## 复现入口

依赖和许可证配置见 [`docs/DEPENDENCIES.md`](../../docs/DEPENDENCIES.md)。Windows Release构建：

```powershell
./native/build_native_solver_pipeline.ps1 -HighsRoot <Highs目录> -GurobiRoot <Gurobi目录>
```

C++单例运行方法见 [`native/README.md`](../../native/README.md)。比较三种方法：

```powershell
python scripts/benchmark_methods.py --corpora formal24,d4 `
  --native-executable build/native/seat_protect_cpp.exe `
  --native-config configs/config_native_rich_5s.json `
  --json-output outputs/benchmark.json
```

该命令会重新进行耗时实验；默认5/150/条件600秒日程。仅重建已发布HTML无需运行算法：

```powershell
python scripts/build_benchmark_report.py `
  --cpp-evidence reports/benchmark-2026-10-10/evidence/cpp-accepted.json `
  --solver-evidence reports/benchmark-2026-10-10/evidence/gurobi-comparison.json `
  --supplement reports/benchmark-2026-10-10/evidence/cpp-full-load-supplement.json `
  --output reports/benchmark-2026-10-10/结果汇总.html
```

压缩包内历史配对测试脚本包含当时的本地路径和构建目录，复用前需按清单定位对应提交、配置及可执行文件，
不能直接将不同版本的二进制互换。验收时应重新记录源码、输入、配置和可执行文件哈希，并计入完整进程耗时。
