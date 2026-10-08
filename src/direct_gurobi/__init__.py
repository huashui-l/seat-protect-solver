"""Full passenger-seat Gurobi model.

The direct route shares the parsed instance and score semantics with
``src.cg_gurobi`` but does not use pattern columns or a restricted master.
"""

from .solver import solve

__all__ = ["solve"]
