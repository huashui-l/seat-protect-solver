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
| SSR/caregiver | construction, rescue, rebuild | native paired construction, fixed-neighbor rescue, bounded paired plans, care-group joint rescue and relocation repair; VND caregiver rebuild still pending | PARTIAL_CPP | indirect | ACTIVE |
| initial construction | anchored, SSR-first, DFS/Beam | independent fixed-state anchored/paired/rescue/remaining construction with DFS/Beam; 33 public combined construction/repair checkpoint differentials passed; runtime schedule parity pending; Q0/Q1/Q2A remain fallbacks | PARTIAL_CPP | supplied artifact | ACTIVE |
| small DFS | construction DFS | native remaining-stage DFS with retry caps, node/time bounds and fixed-care validation; fixture differential passed | PARTIAL_CPP | no | ACTIVE |
| beam search | construction Beam | native remaining-stage Beam with skips, compactness ranking, dominance and transactional commit; separate RR pattern Beam remains | PARTIAL_CPP | yes | ACTIVE |
| unassigned repair | relocation-chain repair | native bounded relocation DFS with rollback, node/time limits, protection resources, and caregiver-pair rescue | PARTIAL_CPP | no | ACTIVE |
| protection exchange | protected multi-group pattern MIP | none | PYTHON_ONLY | no | ACTIVE |
| scoring | complete independent/incremental score | native full-assignment score with Formal24 parity | FULL_CPP | indirect | ACTIVE |
| group matching | exact bitmask matching | bounded two-group bitmask matching over the current occupied seat union | PARTIAL_CPP | no | ACTIVE |
| multi-group rebuild/LNS | conflict LNS and pattern MIPs | restricted RR master only | PARTIAL_CPP | master only | ACTIVE |
| 1-opt / 2-swap | VND | native strict-improvement moves with shared deadline and full validator | PARTIAL_CPP | no | ACTIVE |
| 3-cycle | VND explicit cycle | native strict-improvement cycles with shared deadline and full validator | PARTIAL_CPP | no | ACTIVE |
| caregiver rebuild | VND joint rebuild | native caregiver-pair rescue exists in repair; full VND caregiver joint rebuild remains Python-only | PARTIAL_CPP | no | ACTIVE |
| RR/search generation | separate orchestration | native task/Beam/commit/deadline | FULL_CPP | yes | Rich INACTIVE |
| simulated annealing | none active | none | DEAD/UNUSED | no | Rich INACTIVE |
| structured pattern handling | elite store, structured/special pricing | raw native Problem adapter, seat/protection resources, SSR location encoding, stable ID/dedup/materialization | PARTIAL_CPP | different path | ACTIVE |
| restricted/protected master | Python HiGHS stages | native RR restricted master; raw M3 adapter invokes it with native patterns | PARTIAL_CPP | yes | ACTIVE, semantics differ |
| final validation | independent evaluator | native feasibility validator plus external Python audit | PARTIAL_CPP | external | ACTIVE |
| serialization | Python benchmark output | raw-native assignment JSON and legacy solver-ready JSON | FULL_CPP | yes | ACTIVE |

## Audit conclusion

The previously timed executable is a native pattern-generator plus native restricted-master pipeline over a Python-prepared placement domain. It is not a full C++ seat-protection solver. In particular, raw input parsing, topology construction, placement-domain legality, fixed-seat preprocessing, individual score coefficients, complete-incumbent construction, repair, and optional V1R memory reconstruction are not all native.

Therefore the historically valid statement is:

> PARTIAL NATIVE VERSION MET THE 5S RELEASE BUDGET; FULL C++ COVERAGE WAS NOT TESTED.

The migration must preserve the existing `HEADER_V2` route as a differential oracle while moving the raw-input-to-option boundary and every Rich `ACTIVE` stage into C++. Current Rich active-stage coverage is not 100%; `FULL-CPP-CORRECTNESS` remains FAIL.
