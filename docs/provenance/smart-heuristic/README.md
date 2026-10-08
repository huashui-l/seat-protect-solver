# 三档启发式实验归档

实验报告：[`../../../reports/smart-heuristic-experiments.html`](../../../reports/smart-heuristic-experiments.html)。

最终审计覆盖 563 条运行记录，错误为 0。推荐版本为 60 秒缓存版 A：V2 69 例完整合法、3 例独立证明不可行、0 例超时；相对新算法初版 60 秒为 22 胜 / 33 平 / 14 负，合计提高 322.0066 分。这里的“基础版”均指本次新算法初版，项目原有 C++ 与旧 Python 单独作为历史对照。六例开发试验与全量重跑存在分数波动，开发结果不替换最终记录。

- `results.json`：报告完整数据，包括逐例分数、目标分项、时间、改善轨迹、阶段、配置、依赖与源码 SHA-256。
- `results.csv`：逐例指标及历史基线，适合进一步统计。
- `allocations.json.gz`：各次实际运行的座位分配；包含合法结果，也如实保留限时未完成的部分分配。只应将 `valid=true` 的结果用于分数比较。
- `solver-versions.json`：5 / 20 / 60 秒各自的冻结源码清单；60 秒档单独收紧了末段重组的线程设置。
- `source-snapshots.zip`：实际测试源码，可取出对应档位复现。
- `focus-study.json`：完整基础实验后的 60 秒选档理由、三轮开发比较、选定版本和最终冻结源码哈希。
- `ablation-study.json`、`development-source-snapshots.zip`、`development-audit.json`：六个预先选定的 V2 开发案例的三个完整版本，保留各行分数、源码和独立核验，不与最终批次混用。
- `audit.json`：独立重算结果、不可行匹配证书及被审计结果文件的指纹。
- `historical_cpp_config.json`：历史 C++ 配置，源于提交 `48c81e45cc9179d590a1e6bf08174473bff70cac` 的 `configs/config_native_cpp5_gap2.json`，SHA-256 为 `d42cccfadd4d690cbcb33b07c75e0137525e35324b2f3229c6ace5ee430a28f9`。

基础新算法批次为 `bounded5`、`bounded20`、`guarded60` 和 `bounded_public5`；当前旧 Python 的对照批次为 `legacy5`，仅测试 V2 的 72 例。重点改进版为 `focused60`，只覆盖完整 V2 72 例；历史 C++ 源码本机 5 秒对照为 `cpp_v2_5`。选档和后续算法改进以 V2 为主，已完成的 Formal24 / D4 只作为补充。每档只使用这一次完整批次，不把开发期间各次运行的最好结果拼成一张成绩表。所有求解器只读取输入、座位图和数学配置，不读取历史答案或数据生成器的 witness。

V2 使用历史提交 `9e140fd66ff2789f0016153fdf167bbd9e2c6087` 的未改动 C++ 源码在本机编译复测。只通过八份按机型生成的配置路由 V2 座位图，输入与数学参数不变。新算法最多 8 线程、C++ 最多 5 线程，分别保存求解时钟和进程耗时。原 Windows 的补充历史基线取 `../cpp5-cg30/comparison.csv` 的 `cleanup_run_external_score`。其中 `baseline_score` 是早期 C++，不是 Python。四次历史 C++ 的逐例最佳包络另行显示，不能把它当作一次 5 秒运行。历史 C++ 与本次实验的硬件不同；20／60 秒对 C++ 5 秒的改善也包含额外时间收益。

代码回归运行了 59 项测试：58 项通过，1 项因仓库没有保留生成的对比图片而跳过。选中缓存版本的回归耗时 55.494 秒；跳过项不涉及模型约束或目标验证。

```bash
.venv/bin/python -m unittest \
  tests.test_smart_allocator tests.test_config_and_scoring \
  tests.test_fixed_seat_semantics tests.test_solution_comparison \
  tests.test_general_validation_smoke -v
```

依据已保存数据重建报告，无须重新求解：

```bash
.venv/bin/python scripts/build_smart_report.py \
  --from-data docs/provenance/smart-heuristic/results.json
```

当前工作区提供最新求解器。若要复现表中 5 / 20 秒的具体版本，请在独立项目副本中使用 `source-snapshots.zip` 内 `bounded5/` 或 `bounded20/` 的源码；基础 60 秒对应 `guarded60/`，改进版对应 `focused60/`。使用 `results.json` 中该批次保存的配置、依赖版本及 seed。墙钟截断和多线程仍可能造成分数波动。

完整运行方式及模型对应见 [`../../smart-heuristic.md`](../../smart-heuristic.md)。
