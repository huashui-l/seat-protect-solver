# 列生成定价实验模块

本模块通过独立进程入口复用 `src.cg_gurobi`，不修改合作者的核心文件。默认列生成入口仍是 `python -m src.cg_gurobi`。

| 文件 | 职责 |
|---|---|
| `pricing.py` | 对偶引导的合法移座／换座搜索；无结果时调用原精确定价 |
| `singleton.py` | 单旅客 PNR 的完整座位／保护空座枚举，完成遍历才提供证书 |
| `__main__.py` | 移座／换座候选及原算法的计时适配入口 |
| `fast.py` | 在候选中增加单旅客精确枚举的实验入口 |

```powershell
python -m src.cg_acceleration --config config.json --groups CASE.json --old-seatmap OLD.json --new-seatmap NEW.json --time-limit 60 --output RESULT.json --quiet
python -m src.cg_acceleration.fast --config config.json --groups CASE.json --old-seatmap OLD.json --new-seatmap NEW.json --time-limit 60 --output RESULT.json --quiet
```

`cg_acceleration.heuristic=false` 使用原始精确定价，保留同样的计时适配层作对照。`true` 启用对应入口的候选机制。输入、评分、主问题与分支规则继续由原模块定义。

启发式返回的下界为负无穷，`certified=false`；不能用“未找到新列”证明定价闭合。单旅客枚举的精确性来自完整遍历全部合法选项，并逐项使用完整主问题对偶；若超时，仍返回未认证。带分支规则的定价继续使用原始 MIP。

两种入口属于不同实验版本，不能混用结果。报告证据包中的源码快照、配置及哈希决定某次实验实际使用的版本；数学正确性测试通过不代表速度或整体分数已经改善。

当前搜索找到8个未发出的合法负约化成本列即返回，避免在输出批次已满后继续消耗搜索时间。历史完整搜索、单旅客枚举与提前返回实验分别保存源码快照，不把旧成绩归于当前代码。

该模块仍会使用 Gurobi 构建定价模型、产生初始列、精确定价、求整数主问题及分支定价。它没有实现“Gurobi 只用于主问题 LP”。

验证：`python -m unittest tests.test_cg_acceleration tests.test_cg_singleton tests.test_cg_study_resume -v`。完整规模运行需要可处理完整模型的 Gurobi 许可证；实验调度器会先求解超过受限许可证规模的小探针。

全量对照、算例修订与证书限制见[实验报告](../../reports/cg-acceleration-2026-10-14/README.md)。
