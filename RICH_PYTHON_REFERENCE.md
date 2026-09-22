# Rich Python Algorithm Reference

> Formal24 inputs and raw result artifacts referenced below are retained in the
> internal research archive and are not distributed in this repository.

## Frozen references

- **Reference A — Heuristic V1R:** unchanged 5-second production engineering baseline. It remains the final same-budget comparison and is not the migration prototype.
- **Reference B — RICH_PYTHON_REFERENCE:** current generator-v3.2 Python allocator, `current` variant, frozen by `rich_python_reference_config.json` at 60 seconds with deterministic solver seed 0.

The 60-second choice is evidence-based. Historical H20 improved H5 on 21/24 cases with mean/median delta F `+24.812/+13.285`. In the current checkout the only 20-second structured-generation cutoff case, `forward:full_stress`, scored `-962.104`, `-953.454`, and `-938.379` in isolated 20/30/60-second saturation probes. The active-stage set is identical across those budgets; the additional time changes completion/repetition, not the algorithm inventory. The frozen Formal24 result is one separate 60-second batch run, never best-of-runs.

## Active-stage audit

| Search component | 5s V1 | Historical richer version | Quality evidence | Rich reference |
| --- | --- | --- | --- | --- |
| initial construction | supplied/offline incumbent in V1R | Python fixed-seat, anchored-group, SSR-first construction | required for raw-input completeness | ACTIVE |
| small DFS | absent from online V1R | groups up to 4, 20,000-node / 0.02s cap | part of complete construction semantics | ACTIVE |
| Beam | native RR Beam only | Python construction Beam for larger groups | part of complete construction semantics | ACTIVE |
| candidate expansion | Python-precomputed native-v2 domain | native raw candidate caps 48/64/128 and adaptive regions | required by construction and repair | ACTIVE |
| relocation repair | incumbent makes it unnecessary | `repair_unassigned_by_local_relocation` | repaired 2 demands in frozen 60s run; completeness stage | ACTIVE |
| protection exchange | disabled in V1R | protected multi-group pattern MIP and dynamic relocation | +287.022 aggregate F, positive in 20/24 cases | ACTIVE |
| Hungarian/group matching | absent | exact small bitmask group matching inside VND; no Hungarian library path | useful as part of group rebuild, not a separate stage | ACTIVE |
| multi-group rebuild | restricted native pattern master only | conflict-component LNS and protected multi-group rebuild | +141.887 aggregate F, positive in 13/24 cases | ACTIVE |
| 1-opt | absent | first-improvement empty-seat relocation | included in VND evidence | ACTIVE |
| 2-swap | absent | occupied-seat pair swap | included in VND evidence | ACTIVE |
| 3-cycle | absent | explicit three-passenger cyclic exchange | included in VND evidence | ACTIVE |
| caregiver rebuild | absent | caregiver-group plus ordinary-group exact rebuild | part of VND hard semantics | ACTIVE |
| RR | native V1R search | historical RR5/10/20 pools | useful for V1R pattern search, but not called by the raw-input Python allocator | Rich INACTIVE |
| deeper completion | admission-guard/time reduced | longer structured generation, LNS, local branching, restricted MIP | 60s vs frozen 20s: 15/3/6, mean delta F `+8.813` | ACTIVE |
| simulated annealing | absent | no current production implementation | no current evidence | DEAD |
| pattern generation | RR pattern generation | structured rigid/relaxed/rebuilt patterns and special dual pricing | 27,503 retained/generated contributions in frozen run | ACTIVE |
| master | native restricted master over RR pool | protected and restricted pattern MIPs using HiGHS | restricted MIP +249.189 F, positive in 23/24 cases | ACTIVE |

The later Sequential Exchange prototype had positive evidence on a different `.native_v2` RR baseline, but it is not silently appended to this raw-input Python reference: doing so would mix candidate universes and produce an unaudited composite. It remains a proven optional follow-up after raw-input rich parity, not an active requirement of this frozen reference.

## Frozen Formal24 result

The complete per-case result is `rich_python_formal24.csv`; raw diagnostics and allocations are under `outputs/research/full_cpp_rich_reference/`.

- complete: `24/24`
- unassigned: `0`
- hard violations: `0`
- evaluator consistent: `24/24`
- maximum score error: `0`
- mean / median gap to current best-known integer reference: `2.7649% / 1.7361%`
- P90 / worst gap: `7.4778% / 12.9950%`
- gap <=1% / <=2% / <=5%: `8 / 17 / 19`
- mean / median / P90 / max wall: `49.745 / 55.150 / 58.976 / 59.794s`

The current best-known integer reference is not a global optimum. It is the per-case maximum of the prior frozen R0 union-pool feasible reference and this frozen single-run Rich Python result; two cases were updated by the latter.
