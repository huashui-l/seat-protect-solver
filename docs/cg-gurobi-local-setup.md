# `cg-gurobi-direct` 本地运行环境

本文记录 `feat/cg-gurobi-direct-pricing`（本地工作分支名 `cg-gurobi-direct`）的可复现运行方式。Gurobi 的许可证是本机私有配置，不写入仓库。

## 1. 创建虚拟环境

项目建议使用 Python 3.11 或更高版本。进入仓库根目录后执行：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-column-generation.txt
.venv/bin/python -m pip install gurobipy
```

`requirements-column-generation.txt` 只声明开源的 HiGHS 依赖；`gurobipy` 单独安装，是为了避免没有 Gurobi 许可证的环境在安装普通列生成依赖时被强制要求使用商业求解器。

本项目使用的 Gurobi 代码位于 `src/cg_gurobi/`，因此不能只安装 `requirements.txt` 和 `requirements-column-generation.txt`。

## 2. 检查许可证

Gurobi 会按其默认规则查找本机许可证，也可以通过 `GRB_LICENSE_FILE` 指定许可证路径。不要把许可证文件、用户名、密码或 WLS 凭据提交到仓库。

需要区分两种情况：从 PyPI 安装的 `gurobipy` wheel 可能自带一个 `Restricted license - for non-production use only`，它可以让小规模 smoke test 启动，但它不是用户的教育版许可证，也不代表教育版许可证已经激活。使用教育版许可证时，应让 Gurobi 日志明确显示 `Academic license`，并按教育版许可证的限制运行。

使用下面的最小模型检查 `gurobipy`、求解器运行时和许可证是否都可用：

```bash
.venv/bin/python - <<'PY'
import gurobipy as gp

with gp.Env(empty=True) as env:
    env.setParam("OutputFlag", 0)
    env.start()
    with gp.Model(env=env) as model:
        x = model.addVar(lb=0, ub=1)
        model.setObjective(x, gp.GRB.MAXIMIZE)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert abs(model.ObjVal - 1.0) < 1e-9
print("Gurobi license smoke test: OK")
PY
```

如果这里报 `No Gurobi license found`，需要先在本机激活许可证，再重复检查。若日志显示的是 `Restricted license`，说明当前使用的是 wheel 自带的受限许可证；若要使用教育版许可证，应先激活它或设置 `GRB_LICENSE_FILE`，直到日志显示 `Academic license`。许可证激活属于本机账户操作，不应通过提交配置文件解决。

## 3. 运行 Gurobi 回归测试

目标分支自带的直接定价列生成回归测试为：

```bash
.venv/bin/python -m unittest tests.test_cg_gurobi -v
```

测试覆盖小规模穷举对照、公开验证案例、固定座位和保护空座、陪护与 SSR、无效输入拒绝，以及分支定价回归。

## 4. 直接运行一个案例

下面的命令运行仓库内 D4 的一个案例，并把结果写到临时目录：

```bash
.venv/bin/python -m src.cg_gurobi \
  --groups data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/cases/rj22_to_nb23_d00_groups.json \
  --old-seatmap data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/seatmaps/2-2seatmap.json \
  --new-seatmap data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25/seatmaps/2-3seatmap.json \
  --time-limit 180 \
  --pricing-time-limit 60 \
  --output /tmp/cg-gurobi-direct-result.json
```

输出中的关键字段包括：

- `status`：是否得到完整结果；
- `hard_violations`：硬约束违规数，应为 0；
- `unassigned`：未分配旅客数，应为 0；
- `total_score`：独立评估器复核后的软分；
- `lp_bound`、`lp_bound_certified`：根节点列空间 LP 上界及认证状态；
- `proven_optimal`：分支定价是否完成全局证明。

## 5. 基准运行

对一组目录中的案例运行批处理：

```bash
.venv/bin/python scripts/benchmark_cg_gurobi.py \
  --instances DIR \
  --old-seatmap OLD_SEATMAP.json \
  --new-seatmap NEW_SEATMAP.json \
  --time-limit 150 \
  --output report.md
```

正式 Formal24/CG30 的输入和参考工件以 manifest、哈希和 provenance 文件为准；不要通过手工修改输入 JSON 来改变冻结 benchmark。

## 6. 当前环境验证记录

本地验证过以下项目：

- Python 3.12.14 虚拟环境 `.venv`；
- `numpy`、`scipy`、`matplotlib`、`highspy`、`gurobipy` 已安装；
- Gurobi 有界最小模型求解成功；当前日志显示的是 wheel 自带的 restricted non-production license，不是教育版许可证激活证明；
- `tests.test_cg_gurobi`：8 项测试全部通过；
- D4 `rj22_to_nb23:d00` 案例实际运行返回完整、无硬违规、无未分配。

`.venv/` 被 Git 忽略，只保留本机环境；换机器时按本文重新创建即可。
