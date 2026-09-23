# Full C++ Migration Map

Audit baseline: clean source snapshot from branch `main`, commit `89ad65ca5499c488bec10a31a3a5264314746b0c`, imported through an explicit allowlist. Source repository history was not copied.

Status meanings are literal: `FULL_CPP` means native semantics exist; `PARTIAL_CPP` means native code still receives Python-prepared facts or required semantics remain incomplete/unverified; `PYTHON_ONLY` means the behavior remains Python; `DEAD/UNUSED` means inactive. The Full-C++ Gate is now measured against every `ACTIVE` Rich Reference row, not only V1R.

| Component | Python implementation | Existing C++ implementation | Status | Required by V1? | Required by Rich Reference? |
| --- | --- | --- | --- | --- | --- |
| input parsing | raw JSON/config readers | raw production CLI reads case/config/old+new seatmaps directly | FULL_CPP | yes | ACTIVE |
| seat topology | `SeatTopology` | native row/subrow, coordinates, attributes, and exact adjacency from raw seatmap | FULL_CPP | indirect | ACTIVE |
| passenger model | Python raw model | indexed native seat/passenger/group model with cabin, SSR, caregiver/protection, and fixed-seat fields | FULL_CPP | indirect | ACTIVE |
| AssignmentContext/state | complete mutable state | native indexed occupancy, protected-block counts, ownership/SSR maps, assign/remove, and snapshot/restore; full legality integration pending | PARTIAL_CPP | no | ACTIVE |
| hard legality | evaluator and allocator checks | native feasibility model and final validator cover cabin/fixed/SSR/caregiver/protection/resource isolation | FULL_CPP | indirect | ACTIVE |
| fixed seats | precheck and assignment | native fixed/reserved preprocessor applied to independent Rich construction; full snapshot parity remains under audit | PARTIAL_CPP | indirect | ACTIVE |
| candidate ordering/domain | native Python domain and ranking | native static domains, cached owner regret/cost ranking and remaining-group/passenger ordering with Python stage differentials | PARTIAL_CPP | indirect | ACTIVE |
| SSR/caregiver | construction, rescue, rebuild | native paired construction, fixed-neighbor rescue, bounded paired plans, care-group joint rescue and relocation repair; stateful VND caregiver rebuild integrated; full pipeline quality gate pending | PARTIAL_CPP | indirect | ACTIVE |
| initial construction | anchored, SSR-first, DFS/Beam | independent fixed-state anchored/paired/rescue/remaining construction with DFS/Beam; 33 public combined construction/repair checkpoint differentials passed; adaptive construction/repair/VND windows integrated; full timing parity pending; Q0/Q1/Q2A remain fallbacks | PARTIAL_CPP | supplied artifact | ACTIVE |
| small DFS | construction DFS | native remaining-stage DFS with retry caps, node/time bounds and fixed-care validation; fixture differential passed | PARTIAL_CPP | no | ACTIVE |
| beam search | construction Beam | native remaining-stage Beam with skips, compactness ranking, dominance and transactional commit; separate RR pattern Beam remains | PARTIAL_CPP | yes | ACTIVE |
| construction repair queue | `_group_repair_metrics`, `_repair_priority_key` | native metrics, quality threshold, exact priority ordering, full queue retention and top-20 diagnostics; direct and combined Python differentials | FULL_CPP | no | ACTIVE |
| unassigned repair | relocation-chain repair | native bounded relocation DFS with rollback, node/time limits, protection resources, and caregiver-pair rescue | PARTIAL_CPP | no | ACTIVE |
| protection exchange | protected multi-group pattern MIP | native complete protected function plus production multi-pass loop, stage captures, strict selection and budget carry; frozen function/wrapper/CLI tests passed | FULL_CPP | no | ACTIVE |
| scoring | complete independent/incremental score | native full-assignment score with Formal24 parity | FULL_CPP | indirect | ACTIVE |
| group matching | exact bitmask matching | stateful Python-ordered related-group partitions and bitmask matching integrated in production; combined stage differential passed | PARTIAL_CPP | no | ACTIVE |
| multi-group rebuild/LNS | conflict LNS and pattern MIPs | native LNS eligibility, frozen infant scoring, ordinary DP, special permutation matching and local set-partition MIP differential passed; candidate generation and search remain pending | PARTIAL_CPP | master only | ACTIVE |
| 1-opt / 2-swap | VND | stateful Python-ordered move/swap integrated in production with frozen rankings and actual repair state; combined stage differential passed | PARTIAL_CPP | no | ACTIVE |
| 3-cycle | VND explicit cycle | stateful Python-ordered cycle integrated in production; combined stage differential passed | PARTIAL_CPP | no | ACTIVE |
| caregiver rebuild | VND joint rebuild | stateful joint permutations, caregiver adjacency, global infant effects and ordered commits integrated in production; full-function and combined stage differentials passed | PARTIAL_CPP | no | ACTIVE |
| RR/search generation | separate orchestration | native task/Beam/commit/deadline | FULL_CPP | yes | Rich INACTIVE |
| simulated annealing | none active | none | DEAD/UNUSED | no | Rich INACTIVE |
| Rich complete placement domain | `_placement_options` | native full seat/protection domains, costs and SSR metadata; frozen differential and production structured consumer verified | FULL_CPP | no | ACTIVE |
| Rich pattern assembly | `_pattern_from_placements`, `_caregiver_ok` | native column assembly, SSR coefficients, infant correction and caregiver validation; used by production structured layers | FULL_CPP | no | ACTIVE |
| Structured rigid/relaxed layers | `generate_structured_group_patterns` prefix | native target backtracking, shifts/mirrors, subrow moves and stable dedup/source replacement; integrated in production | FULL_CPP | no | ACTIVE |
| Structured group/window ordering | `generate_structured_group_patterns` ordering and row windows | native primary/difficult eligibility, full-resource activation and complete/truncated window ordering; integrated in production | FULL_CPP | no | ACTIVE |
| Structured value-block layers | `generate_structured_group_patterns` value-block prefix | native ordinary/global blocks, anchors, subset matching and stable source replacement; integrated in production | FULL_CPP | no | ACTIVE |
| Rich pricing-cache geometry | `_build_group_pricing_cache` and structured window filtering | native domains, shifted coordinates, adjacency and holes; connected to production window DFS | FULL_CPP | no | ACTIVE |
| Rich DFS rectangle bounds | `_price_group_exact_dfs` geometric workspace and bound arrays | native coordinate indexes, capacity/span and coupled passenger bounds; connected to production DFS | FULL_CPP | no | ACTIVE |
| Rich DFS resource/care workspace | `_price_group_exact_dfs` static workspace and caregiver helpers | native multiword resource/SSR masks, signature lookup, care reachability and dominance state; native DFS reuse verified | FULL_CPP | no | ACTIVE |
| Rich DFS symmetry | `_price_group_exact_dfs` raw prefilter, physical classes and rank pruning | native raw passenger identity, physical-domain classes and ranks; connected to production DFS | FULL_CPP | no | ACTIVE |
| Rich pricing dynamic costs | baby pairs/relaxation, placement/column reduced costs and flag dual aggregation | native ordered baby pairs, placement/activation costs, Phase-I and column reduced costs; connected to production DFS | FULL_CPP | no | ACTIVE |
| Rich rebuilt pricing DFS | `_price_group_exact_dfs` | native traversal, constraints, pruning/dominance, history, negative pool, workspace reuse and termination diagnostics; full-function differential and production structured consumer verified | FULL_CPP | no | ACTIVE |
| Structured generation orchestration | `generate_structured_group_patterns` | native complete layer sequence, scoring and synchronous elite recording; production scheduler uses preserved Rich VND state; combined differential passed | FULL_CPP | no | ACTIVE |
| Special dual pricing | `generate_special_dual_pricing_patterns` | native continuous restricted LP, dual extraction and repair-priority DFS; synchronous pinned elite recording after protected MIP uses actual Rich state and reserved deadline; function/wrapper/CLI tests passed | FULL_CPP | no | ACTIVE |
| structured pattern handling | elite store, structured/special pricing | raw native Problem adapter, seat/protection resources, SSR location encoding, stable ID/dedup/materialization | PARTIAL_CPP | different path | ACTIVE |
| elite pattern store | `record_elite_pattern`, `_elite_pattern_eviction_candidate`, stage capture | native identity/resource/conflict handling, replacement, pinning and stable eviction with direct Python replay; construction/repair/VND capture and affected-group scores integrated; construction-frozen conflict-diversity activation and current-state candidate recording implemented; structured/special generation and protected MIP captures integrated; Rich restricted pattern consumer pending | PARTIAL_CPP | no | ACTIVE |
| restricted/protected master | Python HiGHS stages | native protected component MIP matches frozen Python; Rich restricted MIP remains pending; independent RR master is still a different production consumer | PARTIAL_CPP | yes | ACTIVE, semantics differ |
| final validation | independent evaluator | native feasibility validator plus external Python audit | PARTIAL_CPP | external | ACTIVE |
| serialization | Python benchmark output | raw-native assignment JSON and legacy solver-ready JSON | FULL_CPP | yes | ACTIVE |

## Audit conclusion

The previously timed executable is a native pattern-generator plus native restricted-master pipeline over a Python-prepared placement domain. It is not a full C++ seat-protection solver. In particular, raw input parsing, topology construction, placement-domain legality, fixed-seat preprocessing, individual score coefficients, complete-incumbent construction, repair, and optional V1R memory reconstruction are not all native.

Therefore the historically valid statement is:

> PARTIAL NATIVE VERSION MET THE 5S RELEASE BUDGET; FULL C++ COVERAGE WAS NOT TESTED.

The migration must preserve the existing `HEADER_V2` route as a differential oracle while moving the raw-input-to-option boundary and every Rich `ACTIVE` stage into C++. Current Rich active-stage coverage is not 100%; `FULL-CPP-CORRECTNESS` remains FAIL.
