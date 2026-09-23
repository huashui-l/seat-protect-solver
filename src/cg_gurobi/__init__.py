"""Gurobi 直接定价列生成（从零实现）。

只依赖 ``docs/任务需求、数学模型与实现说明.md`` 第 3–6 章的数学模型和
``src/allocation_evaluator.py`` 给出的业务语义（硬约束判定、软分定义、
座位拓扑）。不复用归档的 HiGHS 列生成实现中的任何算法结构。

公开入口：

- :func:`solve` —— 对一次航班任务（目标座位图、旧座位图、同行组、配置）
  按舱位独立运行列生成，返回整数方案、LP 上界及证书来源。
"""
from .solver import solve, solve_single_cabin  # noqa: F401
