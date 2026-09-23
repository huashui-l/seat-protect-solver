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

Construction and repair candidates are kept separate from the selected legal
Q0/Q1/Q2A fallback. Only a complete, legal, strictly better candidate replaces
that fallback. Construction assigned/unassigned counts remain the pre-repair
checkpoint; a legal fallback remains eligible for VND. Earlier Q2A snapshot
repair wiring established this boundary; the independent Rich remaining-passenger
constructor below now supplies the candidate state instead. Repair disable,
equal-score retention and worse-candidate rejection remain covered by public
CLI tests with independent evaluator audits.

Relocation repair now has a shared native stage function, `repair_rich_assignment`,
used by production and a dedicated replay probe. Given an assignment state and
per-passenger ordered candidates, it follows Python's frozen active-caregiver
set, dynamic minimum-feasible-domain mover selection, external-key ordering,
care-group member release/permutation, group-local caregiver validation, rollback,
node accounting, and unresolved-count rules. Cared passengers no longer fall
through to ordinary single-seat repair, and caregiver repair need not wait for
unrelated groups to be complete. Production now passes the native construction
candidate cache described below rather than individual-score-only rankings.
The probe calls the same function with identical state/rankings as actual Python
`repair_unassigned_by_local_relocation`. Tests compare exact assignments,
protection resources and all four non-timing repair counters, including a two-node
MRV completion, failed-branch rollback, frozen caregiver protection, care-group SSR
permutation with another group still missing, and public fixture partial states.

One explicit frozen-reference defect is retained as a separate reproduction,
not counted as repair parity: `caregiver_and_ssr` with everyone initially missing
and seat-input-order candidate lists leaves Python with 9 assignments but 12
occupied seats (orphan occupancy at 1B, 1E, 2B). Its static missing list revisits
adult caregivers assigned by an earlier paired repair. Native assignment state
rejects duplicate assignments and therefore diverges on this inconsistent state.
Public parity scenarios keep adults already seated when testing missing cared
passengers; this does not establish equivalence for every possible repair entry
state or prove that the reference defect is unreachable in the full pipeline.
The frozen Python source is unchanged. Production retains final legality checks
and strict incumbent fallback; exact full-pipeline parity remains unproven.
Validation: Release build passed; the native/state-replay-enabled public suite
passed 76 tests and 12,300 subtests, with 9 skips. After adding the probe's orphan
occupancy assertion, the final rebuild and all 5 repair tests / 24 subtests also
passed. The known-reference-defect test is explicitly separate from exact-state
parity comparisons. No Formal24 checkpoint was run for this change.

The native core now implements Python `compute_old_seat_owner_regret`,
`calc_seat_sort_key`, and `build_passenger_sorted_seats` as a construction
candidate cache. It includes old/new seat values and geometry, attribute and
multiple toilet preferences, old-seat reservation pressure, contextual baby
interference, and stable input-seat ordering for equal costs. Regret feasibility
is distinct from assignment admission: Python's feasibility predicate does not
require the passenger to be unassigned or enforce fixed-seat identity. The
native assignment admission checks remain in place. Production builds
this cache from the fixed initial state and shares it between construction and repair.
This replaces its individual-score-only and seat-ID-tie ranking. The remaining-passenger
construction stage below uses updated context costs while preserving these initial owner regrets.
The existing stage probe's `rank_only` replay compares native cache costs,
regrets and rankings against all three actual Python functions. All 35 scenarios
matched costs/regrets to 10 decimal places and exact candidate order, including
11 public fixtures, empty/fixed/mixed states, reservation pressure 0/1/2.5,
missing old seat with explicit old value, multiple preferences and stable ties.
Release MSVC build and the native/state-replay-enabled public suite passed:
77 tests, 12,335 subtests, 9 skips; the two existing conversion warnings remain.
This validates the cache in the exercised states and its repair integration,
not full construction ordering, runtime schedule or Formal24 quality parity.

`assign_rich_remaining` now implements the frozen Python remaining-passenger
stage in a separate native construction module. It uses stable group priority
(anchors, feasible-domain size, protection/care demand, group size), passenger
protection/domain/regret/old-position ordering, dynamic costs with frozen owner
regrets, three DFS candidate caps, exact node counting and DFS-to-Beam fallback.
Beam supports skipped passengers, compactness-based move ordering, all single-empty
choices, resource/local-SSR dominance, stable retention and transactional commits.
Static global SSR filtering and branch-local SSR checks remain separate, as in
Python; final commits recheck the combined state. Missing candidate-cap settings
now use Python's dependent defaults; explicit frozen 48/64/128 settings are retained.

Production M1 now starts its own candidate at fixed state, runs anchored groups
then all remaining non-cared passengers, and passes that candidate and the frozen
ranking cache to repair. Q0/Q1/Q2A are selection fallbacks rather than construction
input. Construction timing/counts are captured before repair, and raw CLI output
includes Rich DFS/Beam counters. A production regression forces Q2A and Rich DFS
failure but observes successful independent Rich Beam completion without repair.
Complete adaptive/carry runtime scheduling and full-pipeline quality parity are still pending;
repair is not claimed as an equivalent substitute for those missing stages.

Direct replay calls actual Python `assign_remaining_passengers` and the production
native function on identical states. Tests cover DFS and Beam on 11 public fixtures,
candidate retries, node-limit clamping, disabled DFS time, expired stage time and
omitted cap settings. Exact assignments, protection resources and all six non-time
diagnostic fields are compared. Generous per-DFS time and deterministic node limits
isolate search semantics; this does not establish default wall-clock cutoff parity.
Final verification on a complete rebuild of the frozen source passed: 81 public
tests, 12,361 subtests and 9 skips, with native and state replay both enabled.
This includes all 12,000 state operations and 26 construction replay subtests.
The two pre-existing conversion warnings remain. No Formal24 run or quality-parity
claim is attached to this remaining-construction checkpoint.

`assign_rich_paired_ssrs` now runs between anchored and remaining construction.
It matches Python group priority (cared BSCT first, care demand, group ID),
fills a caregiver next to an assigned cared passenger, retains unsatisfied fixed
cared seats, removes/repositions unfixed cared passengers when necessary, prefers
assigned caregivers, then searches joint pairs with a 500-feasible-seat cutoff.
Costs use current context and frozen owner regrets, with group compactness where
Python applies it. Stable candidate ordering and caregiver-first transactional
submission are preserved. The production loop runs at most four passes and stops
on no newly assigned passengers; its pass count is exposed by the raw CLI.

Native assignment state now accepts the explicit partner-seat exclusion needed
by these paired submissions. Feasibility excludes that partner from occupied/SSR
checks where Python does; protection blocks do not consume the partner's seat.
Single-empty placement rejects an unavailable actual block before mutating state.
The replay probe calls the same production pair function and compares actual
Python assignments, blocked resources and newly assigned count. Tests cover
11 public fixtures, assigned/unassigned care combinations, fixed unsatisfied
passengers, single/both-empty partner exclusions and the 500-feasible-seat cutoff.
The production caregiver/SSR fixture completes construction without relocation
repair. Dedicated failed-pair rescue now follows these paired passes, as described below.
Release build passed, followed by the full native/state-replay-enabled public
suite: 87 tests, 12,379 subtests, 9 skips. The existing two conversion warnings
remain. No Formal24 or default-budget quality-parity claim is made at this stage.

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
passed: 67 tests, 118 subtests, 10 skips. This initial checkpoint established budget-calculation parity; production
construction/repair/VND now consume it as described below. It does not establish
60-second quality parity.

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
Production now calls this scheduler for construction, repair and VND, as
described below. The full protected/LNS/pricing schedule still needs integration. No absent protected/LNS/pricing stage is
counted as executed, and no Formal24 quality claim follows from these clock tests.
Release MSVC build passed (the two pre-existing integer-to-double warnings
remain). Native-enabled public tests passed: 67 tests, 274 subtests, 10 skips.
The separately enabled state-replay test passed all 12,000 operation subtests.

`rescue_rich_paired_ssrs` now implements Python `rescue_failed_paired_ssrs`
and runs before remaining construction. Fixed cared passengers can obtain a
neighboring caregiver by moving an ordinary nonfixed occupant, including one
from another group. Missing cared passengers use capped options and a 1,000-node
search maximizing the paired count before cost; unresolved tasks can trigger a
bounded whole-care-group rebuild with fixed seats retained and Python's restore
ordering. Candidate costs preserve the initial owner regrets. The raw CLI emits
attempted/rescued/unresolved counts and joint rebuilds; the shared stage probe
also exposes the exact ordered external passenger-key lists.

Differential tests compare actual Python assignments, protection blocks and all
rescue diagnostics on 11 public fixtures plus fixed-neighbor displacement,
immovable fixed neighbors, bounded joint-rebuild rollback/success, and two cared
passengers sharing one adult. Release build and the complete native/state-replay
public suite passed: 92 tests, 12,392 subtests, 9 skips. Isolated rescue parity and
production integration are established for these scenarios; full runtime
scheduling and Formal24 parity remain outstanding.
No Formal24 checkpoint or quality claim accompanies this change.

The production construction sequence is now shared with the stage probe through
`construct_rich_assignment`, including the same fixed-state initializer and native
candidate cache. A new combined oracle executes the actual frozen Python allocation
prefix through anchored, paired, rescue and remaining stages, then its actual
relocation repair. For 11 public fixtures in each of three modes (normal DFS,
DFS disabled for Beam, remaining-stage time zero to exercise repair), all 33
scenarios match exact pre/post-repair assignments, protection blocks, five score
components and total, paired-pass counts, ordered rescue diagnostics, six
construction counters and four repair counters. Python orphan occupancy is
asserted absent at both checkpoints in these scenarios.

These comparisons use generous nonbinding construction/repair times and do not
establish wall-clock cutoff parity. Tail-stage runtime scheduling,
construction repair queue/elite bookkeeping, and a frozen Formal24 M1 checkpoint
are still outstanding. The earlier inconsistent all-missing repair reproduction
remains a separate reference defect; these passing trajectories do not prove
it unreachable for all inputs.
Release MSVC build and the full native/state-replay-enabled public suite passed:
93 tests, 12,425 subtests, 9 skips. This includes the 12,000-operation state replay
and the existing raw-native external legality/score audits. Only the two existing
integer-to-double conversion warnings remain; no Formal24 run was performed.

Production construction/repair/VND now use the native adaptive budget calculator
and `RichStageSchedule`. All three share the solver-entry monotonic origin;
Q0/Q1/Q2A fallback work is charged to that origin rather than resetting the Rich
construction deadline. The search deadline reserves scoring time and respects
both the configured business limit and the CLI outer cap. Incomplete construction
lends the remaining search window to repair, while the repair function retains
its own configured time/node cap. Construction and repair carry feed the next
stage, including VND's post-protected pricing reservation. A zero VND base budget
therefore does not disable VND when carry is positive.

The raw CLI reports configured/scaled budgets, search deadline, and actual
construction/repair/VND start, finish, deadline, carry and pricing reserve. It also
reports `rich_m1_selected_score`, allowing fallback acceptance to be audited before
later VND improvements. Actual production timestamps are replayed through the
frozen Python schedule expressions for adaptive/nonadaptive settings, negative
construction budget, incomplete/disabled repair and CLI clipping of a 60-second
configuration. The virtual-clock suite additionally covers an explicit outer
search-deadline cap. Unused duplicate direct stage-budget fields were removed.

This is production integration of the first three stage windows, not complete
runtime or quality parity. The Q0/Q1/Q2A prefix is an additional native fallback
cost absent from Python and can consume the construction window. Raw parsing and
topology currently occur before this solver clock. Construction repair-queue and
elite capture bookkeeping are still absent, so timing overhead is not identical.
The current RR pattern adapter remains clipped to the shared search deadline;
it is not labeled as the missing structured/protected/pricing/LNS stages, and its
restricted-master budget/carry semantics remain pending. Complete legal fallbacks
remain eligible for the existing partial VND even when the Rich candidate fails.
Validation: frozen-source Release rebuild passed; the complete native/state-replay
suite passed 94 tests, 12,482 subtests and 9 skips. The budget oracle covers 52
fixture/config scenarios with four clock traces each, including outer-cap cases;
five production scenarios replay actual construction/repair/VND timestamps.
Two existing fallback tests now assert the pre-VND M1 selection score and final
non-regression, because zero VND base budget does not suppress carried time.
No Formal24 checkpoint, tuning or profiling was performed.

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
