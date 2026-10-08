"""Model-driven anytime seat allocation, independent of the legacy heuristic."""

from .solver import solve

__all__ = ["run_allocation", "solve"]


def run_allocation(new_seats_data, old_seats_data, groups_data, weights, config):
    """Adapter for the project's ``(result, elapsed_seconds)`` Python interface."""
    options = config.get("smart_heuristic", {})
    result = solve(
        new_seats_data,
        old_seats_data,
        groups_data,
        weights,
        config,
        time_limit=float(options.get("time_limit", 20)),
        workers=int(options.get("workers", 8)),
        seed=int(options.get("seed", 42)),
    )
    return result, result["wall_seconds"]
