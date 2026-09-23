# Full C++ Rich Reference Status

Status: **NATIVE-FEASIBLE-CONSTRUCTION PASS; FULL-CPP-CORRECTNESS remains in progress.**

## Rich Python

- selected budget: 60 seconds
- active stages: raw preprocessing, fixed/reserved assignment, SSR/caregiver construction and rescue, small DFS/Beam construction, relocation repair, VND 1-opt/2-swap/3-cycle/group/caregiver rebuild, structured and special pattern generation, protected multi-group MIP, conflict-component LNS, restricted pattern MIP, native-equivalent final validation/scoring semantics
- Formal24 mean / median gap: `2.7649% / 1.7361%`
- wall mean / median / P90 / max: `49.745 / 55.150 / 58.976 / 59.794s`

## Migration

The raw-native feasibility path now includes parsing, indexed problem/topology, fixed preprocessing, native legal domains and joint hard constraints, complete-incumbent construction, native feasibility validation, scoring, and serialization. Rich construction ordering, DFS/Beam, repair, VND/LNS/pattern stages, and Rich master semantics remain partial or Python-only. The raw feasibility production path has zero Python callbacks, but Rich active-stage native coverage is not yet 100%.

The M1/M2 migration branch now also contains a native Rich construction entry,
configuration-driven DFS/Beam limits, bounded direct repair, VND 1-opt/2-swap/
3-cycle moves, and bounded two-group bitmask rebuild. These stages require a
complete legal incumbent, use the shared deadline, and accept only strict score
improvements. They are implementation progress, not a Rich quality-parity gate;
the full Python caregiver joint rebuild, pattern stages, LNS, and
restricted/protected master semantics remain outstanding. Native repair now
uses rollback snapshots, bounded relocation DFS, protected-seat resources, and
caregiver-pair rescue; its diagnostics are emitted by the raw CLI.

The M3 checkpoint now also builds a raw `Problem` into the native pattern-kernel
protocol, materializes native patterns, and runs the restricted master. The
adapter encodes seat/protection resources, SSR row/subrow locations, caregiver
flags, incumbent choices, and a shared deadline. The exercised smoke cases
produced complete evaluator-valid assignments; this remains a stage checkpoint,
not Formal24 quality parity. Structured/special pattern semantics and the
protected multi-group/LNS stages are still pending.

M3 adapter validation (2026-09-23): MSVC build passed; the public suite with
`SEAT_PROTECT_NATIVE_EXE` set passed 56 tests and 39 subtests, with 10 skips.
External evaluator audits at a 20-second budget passed for
`forward:50_normal`, `forward:full_edge`, and `reverse:full_edge`: all complete,
zero hard violations, zero unassigned, and zero score error. These are three
smoke cases, not the 24-case 60-second parity gate. The current adapter still
uses the existing RR beam pattern semantics; exact ACTIVE stage orchestration
and adaptive/carry budget semantics remain outstanding. Equal-score master results do
not replace or relabel the incoming incumbent.

The raw adapter now includes baby-pair objective terms and the kernel's same-group
cancellation, matching Python `_baby_pairs` / `_pattern_from_placements`.
The public `caregiver_and_ssr` differential exercises 288 pairs with nonzero
external baby score (-0.6985902255639098); reconstructed master score
(-8.198590225563912) matches native and external assignment score
(-8.19859022556391). The native-enabled public suite remains 56 passed,
10 skipped, and 39 subtests passed. This verifies objective encoding on the
fixture, not structured generation or full ACTIVE stage equivalence.

Restricted-master configuration is now read from the raw algorithm config:
`enable_restricted_pattern_mip`, `restricted_pattern_mip_time_budget`, and
`restricted_pattern_mip_tail_budget` (Python defaults true, 0.0, 0.10).
The provisional 0.5-second cap is removed. The current single master call uses
max(base, nonnegative tail), clipped to remaining solver time. Disabled or zero
budget calls preserve the VND incumbent while retaining generated pool diagnostics.
Adaptive scaling and LNS unused-budget carry are not yet implemented.
Native-enabled full-suite verification passed 58 tests
and 44 subtests (10 skips); an additional remaining-time clipping regression and
the affected group-first suite then passed (11 tests, 14 subtests).

Restricted-master local branching and repeated solves now run natively on the
same HiGHS model. The center remains the initial incumbent; configured radii
grow per attempt up to the group-count/configured cap. Each solved assignment
is excluded before the next attempt, and the best earlier assignment survives
later infeasibility. The raw adapter reads the attempts/enabled/initial/growth/
maximum settings. Existing master callers default to one unrestricted solve.
Multi-attempt results suppress the last model's gap and dual bound because
exclusion rows prevent interpreting them as bounds for the original pool.
Constructed exchange tests cover radii 0/1/2, explicit centers, reordered columns,
radius caps, exhaustion after exclusions, and disabled-path coefficient hashes.
MSVC build and the native-enabled public suite passed: 65 tests, 53 subtests,
10 skips. This does not prove the full Python restricted stage equivalent:
its elite store, structured-global-value-block bypass, candidate context rebuild,
and dynamic conflict rejection still need migration/semantic differential tests.

The raw pattern adapter now uses a native static placement domain matching
Python `_placement_options` fixed/reserved-seat filtering. Movable passengers
cannot occupy fixed or deterministically blocked seats; protection choices
cannot block an occupied fixed seat. Both-empty precedence is preserved when
both protection flags are present. The core probe's `--placement-domains`
mode exposes occupied/block choices and individual scores for differential
tests. All 13 public fixtures (175 passengers) match the Python candidate sets,
blocked-seat sets, and individual coefficients (10 decimal places). MSVC and
the native-enabled suite passed: 66 tests, 66 subtests, 10 skips. This is static
domain parity on those fixtures; dynamic state semantics and structured search
ordering/retention are not established by this test.

Initial Rich stage-budget calculation is now available as a native core
function and exposed by the core probe. It matches the frozen Python allocation
prefix for scoring reserve, protection-aware seat demand, adaptive interpolation,
priority/protected stage activation, proportional scaling, and post-protected
tail/special-pricing activation. The oracle executes the actual Python prefix
without running search. All 52 fixture/config scenarios matched to 10 decimal
places, including absent settings, negative budgets, threshold boundaries,
shrink/expand layouts, and protected demand. MSVC and the native-enabled suite
passed: 67 tests, 118 subtests, 10 skips. Production stage orchestration does not
yet consume this calculation; replacing its existing limits and implementing
unused-budget carry remain required. This checkpoint is budget-calculation
parity, not runtime scheduling or 60-second quality parity.

The native core now also supplies `RichStageSchedule`, with caller-supplied
monotonic timestamps and a `--schedule-replay` core-probe mode. It preserves
construction's allocation-start deadline, repair's completeness-first borrowing,
VND's post-protected pricing reserve, normal unused-budget carry, special pricing's
separate deadline without overwriting protected carry, and restricted MIP's
LNS carry measured at restricted entry (including intervening bookkeeping).
Negative configured budgets and expired global deadlines retain Python behavior.
The differential oracle executes scheduling expressions extracted from the actual
Python allocation function, including its VND augmented assignment. Across 52
fixture/config scenarios, 156 virtual-clock traces matched to 10 decimal places;
these include early finishes, incomplete construction, overruns, negative tail
budgets, nonzero clock origins, and active/inactive pricing reserve.
Production does not yet call this scheduler: its Q0/Q1/Q2A fallback orchestration
and partial Rich construction/repair boundaries still need separation before
wiring in the frozen stage schedule. No absent protected/LNS/pricing stage is
counted as executed, and no Formal24 quality claim follows from these clock tests.
Release MSVC build passed (the two pre-existing integer-to-double warnings
remain). Native-enabled public tests passed: 67 tests, 274 subtests, 10 skips.
The separately enabled state-replay test passed all 12,000 operation subtests.

## Correctness

- Rich Python Formal24 complete: `24/24`
- unassigned: `0`
- hard violations: `0`
- evaluator consistent: `24/24`
- maximum score error: `0`
- raw-native Formal24 feasibility: `24/24` complete, `0` unassigned, `0` native/external hard violations
- native raw-core parity: `24/24` valid cases matched with assignment-state round trips; `5/5` invalid fixed-seat fixtures rejected consistently
- native/external score parity: maximum absolute error `3.183231456205249e-12`

## Quality parity

Not yet eligible. The feasibility solver's mean/median gap is `188.235% / 182.872%`, recorded only to expose the expected quality deficit. Rich active stages have not yet been migrated, so this result must not be compared as Rich-quality parity.

## Timing and profile

The earlier 1.668-second native-core smoke remains a solver-ready `.native_v2` test, not a Full C++ performance result. Native profiling is intentionally deferred until raw JSON, Rich active-stage coverage, correctness, and quality parity pass.

## Required answer

Current Full C++ does **not** yet inherit all proven valuable 20s+ Rich Python search capability. The raw feasibility path now has zero runtime Python callbacks, but 100% Rich active-stage native coverage remains unmet.
