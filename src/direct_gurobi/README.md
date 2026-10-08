# Modular direct Gurobi solver

`src.direct_gurobi` is the complete passenger-seat MIP route. It shares
instance normalization, seat domains, hard-rule semantics, and score terms
with `src.cg_gurobi.instance` and `src.allocation_evaluator`.

The implementation is split by responsibility:

- `model.py` creates assignment, protection-resource, caregiver, SSR,
  compactness, and infant-interaction variables and constraints.
- `solver.py` owns the Gurobi environment, time limit, incumbent extraction,
  evaluator recheck, and optimality fields.
- `__main__.py` provides the JSON CLI.

Run it with:

```text
python -m src.direct_gurobi --groups CASE.json --old-seatmap OLD.json \
  --new-seatmap NEW.json --config config.json --time-limit 150 \
  --output result.json
```

`complete` means the returned assignment is complete and passes the
independent evaluator. `proven_optimal` additionally requires Gurobi's
`OPTIMAL` status. A time-limited incumbent remains usable when it is complete,
but its objective is not a proof of global optimality.
