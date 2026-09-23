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
3-cycle moves, bounded two-group bitmask rebuild and caregiver joint rebuild. These stages require a
complete legal incumbent, use the shared deadline, and accept only strict score
improvements. They are implementation progress, not a Rich quality-parity gate;
pattern stages, LNS, and restricted/protected master semantics remain outstanding. Native repair now
uses rollback snapshots, bounded relocation DFS, protected-seat resources, and
caregiver-pair rescue; its diagnostics are emitted by the raw CLI.

Construction and repair candidates are kept separate from the selected legal
Q0/Q1/Q2A fallback. Only a complete, legal, strictly better candidate replaces
that fallback. Construction assigned/unassigned counts remain the pre-repair
checkpoint. VND continues the actual complete/legal Rich repair state, including
its insertion order, protection blocks and frozen candidate rankings, even when
repair loses to the fallback; incomplete Rich candidates skip VND. Earlier Q2A snapshot
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
later-stage elite bookkeeping and a frozen Formal24 M1 checkpoint
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
topology currently occur before this solver clock. Construction repair-queue bookkeeping is now native as described below;
construction/repair elite capture is now native as described below. Later-stage
bookkeeping remains incomplete, so timing overhead is not identical.
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

The construction repair queue now follows Python `_group_repair_metrics` and
`_repair_priority_key`. Each group retains size/assigned counts, row span/count,
centroid compactness penalty/score, value and preference losses, priority loss and
extreme-dispersion flag. Quality losses activate at the configured business-time
threshold; preference loss sums all toilet rules. Sorting uses descending priority
loss, row span and compactness penalty, then ascending external group ID. The full
queue is retained natively for later stage consumers; raw CLI diagnostics truncate
to the first 20 groups and count extreme dispersion over every group, matching
Python. Queue computation occurs before construction finish/carry accounting.

Actual Python metrics/order match in 45 direct fixture/state/config scenarios,
including the activation threshold, partial assignments, zero compactness weight
and multiple toilet rules. All 33 combined construction/repair scenarios now also
compare their construction repair queues. Production tests verify count consistency,
sorting and extreme-dispersion diagnostics. The queue is not yet consumed by the
unmigrated structured/protected/LNS algorithms; this change does not claim those
stages implemented or Full C++ quality parity.
Release MSVC build passed; the full native/state-replay-enabled public suite
passed 95 tests, 12,527 subtests, 9 skips. Existing two conversion warnings remain.
No Formal24 checkpoint or performance experiment was run for this change.

The native core now supplies `RichEliteStore`, matching the frozen nested
`record_elite_pattern` and `_elite_pattern_eviction_candidate` semantics. Identity
contains host/seat assignments and per-host protected blocks; occupied, blocked
and union resources are canonicalized separately. Conflict groups derive from
current resource owners only when conflict diversity is active. Better-score
replacement retains insertion order but takes the new pinned/source values;
equal/worse pinned records only promote pinning. Eviction first prefers removable
patterns in duplicated conflict classes, then the lowest local score with stable
ties. Pinned entries are never evicted, so the per-group limit may be exceeded.

The stage probe replays records through this core store. A differential oracle
executes the actual nested Python function and compares every field and pattern
order after 636 records across three limits (639 subtests including the limits).
Coverage includes alternative protection choices, replacement/unpinning, stable
ties, conflict diversity, all-pinned overflow and deterministic random records.
The initial isolated store checkpoint did not establish production capture.
Construction/repair capture is now integrated as described below; VND capture,
candidate conflict-diversity activation and subsequent structured/protected/
restricted stage consumers remain required. No full elite-coverage or
quality-parity claim follows from the isolated store test.
Release build passed; the full native/state-replay-enabled suite passed 96 tests,
13,166 subtests and 9 skips. No Formal24 or profiling run was performed.

Construction and repair now capture complete groups into the native elite store
before recording each stage's finish time/carry. The store uses the configured
`elite_patterns_per_group` with Python's lower bound of two. Captures preserve the
actual per-passenger protection blocks and pin current group placements. Incomplete
groups are omitted. Current-state captures have no external resource conflicts;
this does not replace the pending conflict-diversity activation for generated
candidate patterns. The raw CLI exposes the pre-VND store as `rich_m1_elite_store`.

A shared scoring implementation now supports Rich affected-group components for
capture: individual and compactness terms belong only to the affected group, while
baby interference includes both outgoing and incoming interactions with other
groups. Rich preference terms sum all toilet rules. The existing full-score entry
retains its prior behavior. The prior diagnostic serializer is shared by the probe
and CLI rather than duplicated. All 33 combined pipeline scenarios execute the
actual Python nested `capture_stage_patterns` and `record_elite_pattern` functions
and compare captured identities, resource choices, scores, source and pinning after
construction and repair. Production scenarios also check complete-group coverage
and omission of wholly unassigned groups. VND capture must retain real protection
state during its migration; rebuilding that state from seat assignments alone is
not claimed equivalent. The elite store is not yet consumed by the RR adapter or
unmigrated ACTIVE pattern stages.
Release build and the full native/state-replay-enabled suite passed: 96 tests,
13,166 subtests, 9 skips. The two existing conversion warnings remain. No Formal24
checkpoint or performance experiment was run.

The native `improve_rich_ordinary_vnd` now implements the ordinary-move prefix
of frozen `improve_assignment_with_safe_neighborhoods`: frozen active-caregiver
exclusion, stable ascending current passenger-score ordering, interleaved move/
swap attempts over the supplied frozen candidate lists, first-improvement restarts,
and then the separate three-cycle phase. Candidate caps have Python's minimum
of four; configured epsilon, evaluated/accepted counters and accumulated delta
are retained. Transactions use real AssignmentState occupancy/protection/SSR
state and snapshot rollback. The ordinary prefix does not move protected/cared/
SSR/fixed/active-caregiver passengers. Infant positions stay frozen for its scores.

The differential oracle compiles the actual Python VND function prefix through
three-cycles, stopping before `keys_by_group` and later rebuilds. Public test entry
states come from actual frozen construction, including its protection choices,
rather than missing referenceAssignments fields. Twenty-two fixture/order runs
plus two cap/epsilon/multiple-preference scenarios match assignments, protection
resources, passes, evaluated/accepted moves and score improvement. Coverage asserts
actual accepted move, swap and cycle paths (observed 3 moves in the synthetic
scenario, 18 swaps and 1 cycle across public scenarios).

This new stateful prefix is exposed through the existing probe but does not yet
replace the production approximate VND. Exact small-group matching, related-group
rebuild, caregiver rebuild and the remaining Python phase order must be integrated
before declaring complete VND parity. The production path still uses its earlier
approximate moves/rebuild and does not capture VND protection state into elites.
No full-VND or Formal24 quality claim follows from prefix parity.
Final Release rebuild passed; the native/state-replay-enabled full suite passed
98 tests, 13,190 subtests and 9 skips. Only the two existing conversion warnings
remain. No Formal24 checkpoint or profiling was run.

The stateful VND prefix now additionally implements Python's ordinary related-group
rebuild: groups sorted by compactness, candidate-linked partners preferred before
row-center distance, configured partner/candidate caps, partitions over at most 12
occupied seats, strict-update bitmask matching, ascending mask enumeration and
first accepted group-pair restart. Matching backtracking preserves Python's reverse
passenger insertion order. Successful/failed commits use real state; ordinary
failed commits restore placements and reinsert touched passengers in Python order.

AssignmentState and its snapshots now retain assignment insertion order. VND
uses this order for initial group seat lists and the post-cycle `keys_by_group`,
which affects stable partner order and matching ties. The 12,000-operation replay
now checks assignment order as well as occupancy and snapshot restoration. VND
replay additionally compares the final Python assignment-dictionary order.

The oracle executes the actual frozen function through ordinary group rebuild,
stopping before `global_baby_score` and caregiver rebuilding. Its 24 scenarios
match exact assignment/resource state and search counters; accepted group rebuilds
are explicitly required by the public coverage test (four observed). The original
24 move/swap/cycle-only scenarios remain. This extended prefix still runs through
the probe, not production replacement; caregiver reconstruction, later phases and
full stateful production integration remain required. No full-VND quality or
Formal24 parity claim is attached to this checkpoint.
Final Release rebuild and the full native/state-replay-enabled suite passed:
100 tests, 13,214 subtests, 9 skips, including assignment-order checks throughout
the 12,000-operation replay and both VND prefix variants. The two pre-existing
conversion warnings remain. No Formal24 or profiling run was performed.

The stateful VND probe now includes caregiver joint rebuilding against the full
frozen `improve_assignment_with_safe_neighborhoods` function. It preserves related
partner ordering, permutations of the current joint seat order, external-state
feasibility screening, caregiver adjacency, global infant interference, strict
epsilon acceptance and non-cared-first submission/restoration order. Twenty-four
public/order and cap/epsilon scenarios match exact assignments, protection blocks,
assignment insertion order, search counters and score improvements. Two additional
BLND/BSCT synthetic scenarios require accepted joint row exchanges; the BSCT case
also includes a passenger outside the rebuilt pair in global infant scoring.

This closes the caregiver portion of the direct VND differential, not production
VND migration. Production still calls the previous approximate VND. Retaining the
actual construction/repair state and frozen rankings, integrating stateful VND and
its elite capture, and validating production stage transitions remain required.
Structured/special patterns, protected MIP, LNS/local branching and exact restricted
master integration also remain incomplete. No Formal24 quality gate or profiling
was run at this checkpoint.

Release /O2 build passed with the two existing conversion warnings. The full
native/state-replay-enabled suite passed 103 tests and 13,238 subtests, with 9
skips. The subsequently expanded BLND/BSCT acceptance test separately passed both
subtests. The full suite includes the 12,000-operation state differential.

The production VND entry now replaces the superseded array-only approximation
with the fully differential-tested stateful function. M1 retains its actual repair
snapshot and construction candidate rankings. M2 searches that Rich trajectory,
then selects it only if complete/legal and strictly better than the independently
retained Q0/Q1/Q2A/M1 incumbent. A tied candidate does not relabel the fallback as
`rich-m2-vnd`. Incomplete Rich repair states skip quality search.

VND capture uses the resulting real protection resources and assignment order.
The CLI retains `rich_m1_elite_store` as the construction/repair checkpoint and
adds `rich_elite_store` after VND, plus the caregiver-rebuild count. Existing
`rich_vnd_score` now describes the Rich candidate after VND, not the independently
selected fallback; when Rich repair is incomplete VND is skipped and its counters,
score and duration retain their zero defaults. Tail RR remains the earlier
adapter and is not claimed to be the frozen ACTIVE structured/master pipeline.

The combined oracle now executes construction, repair and the shared production
VND entry with frozen rankings and compares assignments, protection blocks,
insertion order, component scores, VND counters and all elite records. Ten of
its eleven public fixtures produce complete repair states and match the full
Python VND; the remaining protection-heavy fixture verifies the incomplete-state
skip contract. The original 33 construction/repair scenarios also check insertion
order. A raw-CLI regression verifies that Rich VND still improves a repair state
that lost to the fallback, while equal final scores preserve the fallback identity.
These checkpoints do not establish Formal24 60-second quality parity.

The production integration Release /O2 rebuild passed (two existing conversion
warnings). Full native/state-replay-enabled regression passed 105 tests and 13,251
subtests, with 9 skips, including the 12,000-operation state differential and
raw-CLI independent evaluator checks. `git diff --check` passed.

Conflict-diversity activation is now computed once from construction repair metrics
and retained across repair/VND in the production result. The CLI exposes
`rich_conflict_diversity_active`. The native predicate matches the frozen Python
business-time threshold, ordinary minimum-size group with negative value mismatch,
protected-resource demand, free-after-demand and aircraft shrink conditions. Raw
`newSeat` object presence is retained separately from fixed-seat identity because
Python excludes a nonempty object from ordinary value-block groups even when its
seat number is empty.

`RichEliteStore::record_candidate` now builds resource ownership from the current
AssignmentState's occupied seats followed by protected empty seats, then delegates
to the already verified identity/replacement/pinning/eviction implementation. It
uses the frozen activation flag without recomputing it after repair or VND. Direct
replay verifies moved resource ownership, single-side protection, combined occupied
and blocked conflicts, equal-score retention of old metadata, strictly better
replacement and disabled diversity. Thirteen activation-boundary scenarios execute
the actual frozen Python AST predicate and match native results; the runtime
scheduler test also checks the production CLI flag against construction metrics.

This is the structured-generation recording prerequisite, not completed M3.
Rigid/relaxed/value-block/rebuilt generation and special dual pricing still require
native implementation and integration; the new candidate API is differential-tested
but is not yet called by a production structured generator. No Formal24 gate,
profiling or 5-second compression was performed.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 107 tests and 13,264 subtests, with
9 skips, including the 12,000-operation state differential and raw-native external
legality/scoring checks. `git diff --check` passed.

The native `build_rich_placement_options` now implements the full placement domain
used by structured generation's Python pricing cache. It enumerates seats in input
order and all legal single-side protection choices in neighbor order, preserving
lexicographically sorted protection/resource signatures, exact individual costs
(including multiple toilet preferences), passenger identity, SSR row/subrow
resources, conditional flags and infant markers. It uses the existing native
fixed-seat preprocessor. Fixed single-side protection remains a choice rather than
an arbitrary global reservation. Overlapping deterministic protection resources
remain in placement options where Python defers rejection to the joint combination;
ordinary occupancy still excludes all globally reserved seats. Empty domains fail
explicitly, as in Python.

A direct oracle compares every field and candidate order against the frozen
`exact_column_generation._placement_options`, including 22 public/input-order
scenarios, protection cross-aisle and neighbor-count variants, multiple preferences,
SSR flags, unknown SSRs, reversed 2-4-2 and 3-4-3 layouts, fixed single-side choices,
deferred protection overlap and invalid/empty domains. The targeted suite passed
6 tests and 31 subtests. This native API is available to the next structured
migration step; the production structured generator, pricing cache metadata and
pattern assembly have not yet been migrated. No end-to-end M3 or Formal24 parity
claim follows from domain parity.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 113 tests and 13,295 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed.

Native `build_rich_exact_pattern` now assembles complete placement combinations
into the frozen Python pattern representation: passenger-index-ordered placement
signatures, host-ordered assignments, seat/host-ordered protection ownership,
occupied/protected/infant resource sets, SSR count rows, conditional flag rows and
master cost. A flagged location activates every supplied active SSR type, falling
back to configured rule types when the supplied list is empty. Coefficients are
stored as keyed maps; their container iteration order is not Python's repr-sorted
tuple order. Pattern search identity remains the ordered placement signature.

Master cost includes individual cost and centroid compactness, then subtracts
same-group ordered infant/occupant pair costs to cancel those master baby
variables. Self-pairs are excluded, matching `baby_interference_score`. The
separate `rich_placements_caregiver_ok` follows Python's eligible-adult and
cross-aisle rules. Assembly itself does not reject overlapping resources, SSR
multiplicity or caregiver failure, because those checks belong to its callers.

The frozen `_pattern_from_placements` and `_caregiver_ok` provide the direct
oracle. Public and synthetic combinations cover reordered input placements,
resource overlap, protection modes, multiple infants, duplicate SSR counts,
all-active-type flag expansion and valid/invalid caregiver adjacency. Together
with existing placement-domain tests, the targeted suite passed 8 tests and 492
subtests. Production structured generation and exact restricted/protected master
consumers still remain to be integrated. No M3 completion or Formal24 parity claim
is made by this assembly checkpoint.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 115 tests and 13,756 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed.

The structured generator's rigid and relaxed per-group layers now exist natively
in `generate_rich_rigid_relaxed_patterns`. They consume the full placement domain
and actual current group seats, enumerate row translations and mirror targets in
Python order, then move one original subrow forward/backward. Subrows use Python's
string ordering, including two-digit row numbers. Mirroring uses the source index
within the target row, with Python's original-column behavior when target rows are
shorter. A target DFS selects the first resource-disjoint, caregiver-valid
protection combination; it does not add SSR rejection absent from the frozen
prefix. Duplicate signatures update the pattern/source while retaining first
insertion order, so a relaxed duplicate can overwrite the rigid label.

The raw three-tier activation condition (including its default zero threshold),
nonnegative rigid-shift clamp and incomplete-group skip are implemented. This
per-group layer has no added deadline checks: the Python prefix checks the stage
clock outside these loops. Overall structured enablement, difficult-group ordering,
row windows, value-block/global-value-block/rebuilt layers, recorder acceptance
and production scheduling are still pending.

The differential oracle executes the actual frozen AST from `seat_at` through the
statement preceding `group_deadline`; it does not reimplement these transforms.
Tests compare ordered pattern content, resource signatures, costs, keyed SSR
coefficients and source labels across eleven public construction states, activation
and shift-cap variants, incomplete groups, protection DFS backtracking, caregiver
mirroring, unequal row widths, reversed input order and string-ordered subrows.
Both rigid and relaxed outputs, and relaxed source replacement, are required by
the coverage assertions. No end-to-end M3 or Formal24 gate is claimed.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 119 tests and 13,775 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed.

Structured group ordering and row-window selection are now native. The ordering
retains primary-group precedence, the full nested repair-priority tuple, special
passenger counts and stable ties. Difficult-group admission uses the frozen
small-group research threshold only for shrinking aircraft at the configured raw
business time. Full-resource global-block activation is separate and retains its
protected-demand and exact-capacity conditions. All ordered metrics are retained;
the diagnostic serializer limits the visible repair queue to 20 as in Python.

Window generation uses reachable placement-seat capacity for minimum width,
includes every fixed row, preserves gaps in actual row numbers and retains both
all windows and the capped list. Window ranking uses cabin shortage and desired
seat-value mismatch over all seats in the chosen rows, then target span, actual
span, old-seat row-center distance, width and row tuple. Missing old seats,
passenger/seat explicit values, nonpositive window limits and negative extra-row
settings follow the frozen implementation.

The oracle executes the actual ordering and window AST slices from
`generate_structured_group_patterns`. The structured test file passed 6 tests and
54 subtests, including 33 public/config window scenarios and two explicit-value /
missing-old-seat scenarios. Both active and disabled full-resource cases are
covered. These are generator prerequisites; value-block/global-value-block,
rebuilt search, final candidate recording, special pricing and production
orchestration remain incomplete. No M3 or Formal24 completion claim is made.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 121 tests and 13,810 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. The structured suite also passed after adding an explicit
repair-queue length assertion. `git diff --check` passed. No Formal24 or profiling
run was performed.

Structured value-block and global-value-block generation now exist natively in
`generate_rich_value_block_patterns`. Ordinary groups with negative value mismatch
use the first four capped windows and all lexical seat anchors; the full-resource
mode uses all windows and five deduplicated quantile anchors. Nearest blocks follow
the frozen Manhattan/vertical/x/seat ordering, are deduplicated across windows and
matched to passengers by strict-update subset DP in ascending mask/seat order.
Generated patterns replace matching rigid/relaxed entries in place and retain
Python's `value_block` / `global_value_block` source labels. The function checks the
clock at window and anchor boundaries only, matching the frozen prefix. No
algorithmic group-size cap is introduced; an unrepresentable native subset index
fails explicitly rather than invoking an undefined shift.

The oracle now executes the frozen AST through the value-block section, with
nonbinding deadlines and a separate already-expired case. The structured suite
passed 7 tests and 65 subtests. Public construction states cover global blocks;
a value-mismatch synthetic requires ordinary value blocks, and an expired deadline
requires neither value-block source. Ordered pattern contents, keyed coefficients,
master costs and source replacement all match Python. Rebuilt DFS, final recording,
special pricing and complete structured production integration remain required.
This is not a completed M3 or Formal24 quality gate.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 122 tests and 13,821 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed.

The rebuilt-search prerequisite `RichPricingCache` now has native base geometry
and structured row-window filtering. Base caches preserve seat input order,
minimum-shifted coordinates and Big-M values, lexical cross-aisle edges (including
unreachable endpoints), and first-seen-row hole specifications (whose middle may
be unreachable). Window caches preserve placement order, sort reachable seats
lexically, retain the base coordinate origin and Big-M, and filter both edge
endpoints and hole middles/sides. Empty filtered passenger domains remain visible
for the structured caller to skip before pricing.

The frozen `_build_group_pricing_cache` and actual structured-window AST provide
the field-by-field oracle. Eleven public fixtures plus a reserved-middle fixture
run with normal/reversed seat input and base/first/last/alternating/empty windows.
Explicit coverage assertions require unreachable neighbors, unreachable hole
middles, retained nonzero coordinate origins and empty windows. The structured
suite passed 8 tests and 589 subtests. This covers cache geometry, not the DFS
workspace, rebuilt search, special pricing or production structured integration.
The search migration must still preserve rectangle capacity/coupled bounds,
raw-passenger and physical-option symmetry, caregiver dominance, stable negative
column retention and node/deadline termination. M3 remains incomplete.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 123 tests and 14,345 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed for this cache checkpoint.

The rebuilt DFS geometric workspace and rectangle bounds are now implemented by
`build_rich_pricing_geometry` and `build_rich_pricing_bounds`. Geometry keeps sorted
unique coordinate indexes and the same two-dimensional seat prefix sums. Bounds
retain capacity-feasible rectangle span costs, four directional cumulative minima,
per-passenger rectangle minima and per-depth coupled bounds. Every depth sums the
remaining passenger costs in the supplied search order, matching Python's floating
addition order rather than substituting reverse suffix accumulation. Costs are
supplied per placement so the same bound builder can consume later dual pricing.

Differential tests execute the actual frozen DFS workspace and bounds AST slices
and compare every coordinate, prefix cell, span cell and suffix cell exactly.
Public groups plus reserved-middle and insufficient-capacity fixtures cover
ordinary/negative placement costs, reversed passenger order, zero span factors and
filtered last-row windows without coordinate renormalization. This is a search
prerequisite; at this checkpoint seat/resource/SSR masks, symmetry, dominance,
caregiver reachability, negative-column retention, termination and complete rebuilt
orchestration remained unimplemented in the pricing DFS. No M3 completion or
quality claim follows.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 124 tests and 14,553 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. The new bounds test contributed 208 successful subtests.
`git diff --check` passed. No Formal24 or profiling run was performed.

The pricing DFS static workspace now includes native resource/SSR flag masks,
ordered per-option flag indexes, signature lookup and caregiver specifications.
Masks use vectors of 64-bit words with the same seat-input-order bit positions
as Python's unbounded integers. Flag positions follow Python tuple-string ordering
(including row 10 before row 2), and all caregivers use the frozen adult predicate.
The workspace includes the previously verified geometric indexes/prefix sums.

Native caregiver reachability now checks already selected adults and future
resource-disjoint adult options. Its dominance-state helper distinguishes
unassigned cared passengers from satisfied care and outstanding neighbor masks.
Both helpers operate on the supplied domains and partial selection; the actual
DFS state table and traversal are still pending.

The oracle executes the frozen static workspace and both original caregiver
helper bodies. Eleven public fixtures and a 216-seat/108-flag-location synthetic
run in normal/reversed seat order with base, last-row and empty domains. Seeded
partial selections, explicit adjacent adults and occupied-resource scenarios are
compared field by field. Coverage assertions require multiword seat/flag/care masks,
cross-aisle and same-side care, satisfied care, pending care and impossible care.
The structured suite passed 10 tests and 1,115 subtests. Full search, workspace lifetime/reuse, symmetry,
negative-column retention, termination and production structured integration remain
required. M3 and the Full C++ Rich gate remain incomplete.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression, including the explicit multiword-care
coverage assertion, passed 125 tests and 14,871 subtests, with 9 skips. This includes
the 12,000-operation state differential and external raw-CLI legality/scoring
checks. `git diff --check` passed. No Formal24 or profiling run was performed.

Pricing symmetry now has native raw-passenger prefiltering, physical placement
descriptors, class/rank construction and partial-selection rejection. The parser
retains each numeric token alongside its existing double value so raw identity
does not collapse integers into floats or lose integer distinctions beyond 2^53.
Passenger fingerprints exclude only `hostnum`, retain unknown/nested fields,
distinguish boolean/integer/float and floating signed zero, and ignore object key
insertion order. These equality keys need not reproduce Python's JSON text.

Only passengers in nonsingleton raw buckets enter physical classification. As in
Python, eligible passengers from different raw buckets can then merge if their
full physical-domain descriptors and caregiver roles match. Physical keys retain
seat/block/resource ordering, SSR coefficients/flags, infant role and Python's
12-decimal cost rounding. Ranks and pruning preserve passenger-index ordering;
disabled symmetry yields no classes or pruning.

The oracle executes the frozen symmetry AST, including `symmetry_ok`. Eleven
public fixtures plus identical, singleton-type, nested-metadata and protected/SSR
fixtures compare raw equivalence, every class, every option rank and seeded partial
selection decisions. Domain clipping/reordering and exact halfway rounding with
adjacent floating values are covered. The structured suite passed 11 tests and
1,338 subtests. DFS traversal, dynamic reduced costs/infant relaxation, negative
column retention, workspace reuse, termination and structured production entry
remain required; this is not completed rebuilt search or M3.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 126 tests and 15,094 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed for this symmetry checkpoint.

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
