# Full C++ Rich Reference Status

Status: **Native Rich migration ACCEPTED with user-approved quality exceptions (2026-09-24).**

## Current acceptance decision

The user explicitly accepted the remaining small score differences and instructed that further time should not be spent eliminating isolated minor gaps. This supersedes the requirement to eliminate the three observed per-case regressions before closing this migration. It does not change the frozen reference, numerical tolerance, solver parameters, or historical benchmark verdict.

All named Rich ACTIVE stages, including outer cabin orchestration, have native production implementations. The frozen `c6a41f3` run passes 24/24 completeness, legality and evaluator consistency; production raw Rich has no Python callback or `.native_v2` dependency. Its 21 improve / 0 tie / 3 regress result is accepted for this migration. Strict 24/24 score nonregression remains **FAIL**, not a newly passing benchmark. The accepted exceptions are forward:full_normal (-3.05), reverse:50_normal (-1.20), and reverse:full_edge (-4.554323673).

Completion review reused the unchanged Release /O2 build and its full regression record: 166 tests passed, 9 skipped, 37,230 subtests passed. The current binary SHA-256 matches the frozen result (`2f0852a75ab5660ab0dcfb4a3478a5149f0351e9949a0982133cde3fd188602e`); source changes since that implementation are documentation only. No additional solver run or best-of-runs selection was needed for this acceptance decision.

Known boundaries remain visible: wall-clock DFS and LNS set traversal can change trajectories; the frozen Python repair orphan-occupancy defect is not emulated; Q0 overhead can consume the restricted stage's time (zero attempts on reverse:full_edge). These are not claims of universal trajectory or timing equivalence. Further investigation aimed at removing the accepted score differences is deferred. CG integer / certified exact LP / certified safe LP identity-bound case counts are all 0; corresponding gaps remain unavailable. No expensive CG/LP rerun, profiling, or 5-second compression is included in this completed migration scope.

The historical checkpoints below retain their original strict verdicts and earlier pending-work descriptions. This acceptance decision governs the current migration status.

## Independent 5-second profile (2026-09-24)

The user subsequently requested a 5s C++ heuristic, explicitly accepting score
loss and authorizing component retention/removal. This is a separate short-budget
profile; the accepted 60s reference and its recorded results remain unchanged.
The previous prohibition on 5s compression is superseded for this work only.

A diagnostic run using the unchanged 60s config and `group-first --time-limit 5`
failed at the 22nd case (`reverse:full_edge`) with no complete legal assignment;
the first 21 completed cases passed legality, but observed process time reached
5.291s. Q0 consumed most or all of the search window on difficult cases, and its
MIP limit excluded model construction. The failed attempt is preserved under
`outputs/research/full_cpp_rich_5s_baseline_779b92b`; it has no completed summary
and is not a 24-case accepted baseline.

The separate `rich-fast` entry starts with Rich construction/repair, then VND,
bounded structured patterns and restricted MIP/local branching. The 5s config
disables protected MIP, special pricing and LNS; Q1/Q2A do not run. A public dense
protection fixture demonstrated that removing Q0 entirely would lose completeness,
so Q0 remains an on-demand fallback only when Rich construction/repair is incomplete
and search time remains. No complete/legal incumbent is replaced by an incomplete
candidate. The existing `group-first` schedule is unchanged.

Tracked profiles: `configs/config_native_rich_5s.json` and
`configs/config_native_rich_60s.json`. The 60s profile is the frozen config with
only relative seatmap paths adjusted. The 5s profile preserves objective weights,
hard rules, seat geometry and reference seatmap identity. Private run configs
only resolve external seatmap paths. The original 60s executable (SHA-256
`2f0852a75ab5660ab0dcfb4a3478a5149f0351e9949a0982133cde3fd188602e`), DLL and exact
config snapshot are retained in `outputs/research/full_cpp_rich_60s_c6a41f3/runtime`.
These are local ignored artifacts, not redistributed toolchains or private data.

Release /O2 build passed with the two existing conversion warnings. The directed
5s test checks 11 legal public fixtures with external evaluation, including
multi-cabin operation, direct negative-score acceptance without Q0, and the
on-demand Q0 fallback. The complete native/state-replay regression passed:
167 tests, 9 skips, 37,241 subtests in 338.33s, including the existing 60s-mode
component and schedule regressions. `git diff --check` passed.

Frozen implementation `d4b3379`, result
`outputs/research/full_cpp_rich_5s_d4b3379/summary.json`: **5s acceptance PASS**.
24/24 complete/legal/evaluator-consistent, zero unassigned and hard violations,
zero total-score error, max individual error 4.55e-13. All 24 process invocations
(including executable startup, input parsing and JSON output) finished below 5s:
mean / median / max 1.246704 / 1.052101 / 2.784321 seconds. These are measured
results on this machine, not a hard real-time guarantee for arbitrary instances.
All 48 cabin Rich candidates completed without Q0; the fallback remains covered
by the public dense-protection fixture. Summary construction counters are the
legacy Q1/Q2A counters (zero by design); actual Rich DFS/Beam diagnostics are in
the nested cabin results. No best-of-runs selection or 5s parameter sweep was used.

Binary SHA-256: `485f9b959607ff2d55afc61ea436d6abd74f6c77cba1758de234a98bc4fd367b`.
Executed config SHA-256: `8f56fa76ae684088af9ef6eac2e86a2a3a61b6999af89709351c5740689b17f0`.
The executed config differs from the tracked profile only in external seatmap
paths. Exact 5s runtime and config are preserved under this result's `runtime/`.

Against the preserved C++ 60s `c6a41f3` result: 0 improve / 0 tie / 24 regress;
mean score -607.887210 versus -583.733236, mean delta -24.153973, worst delta
-62.253333 (`forward:150_stress`). Mean per-case normalized loss is 4.336013%,
using `100 * (F_60s - F_5s) / max(1, abs(F_60s))`; this is not a CG/LP gap.
Input and old/new seatmap SHA identities match in all 24 paired rows. Measured
mean process time falls from 39.325649s to 1.246704s (31.54x ratio between the
recorded runs). Against frozen Python 60s: 7 improve / 0 tie / 17 regress,
mean delta -10.381278. Score loss is permitted by the user's 5s acceptance scope.
CG integer and certified LP references remain unavailable, with no new CG run.

| Case | F_5s - F_CPP_60s | 5s process wall (s) |
| --- | ---: | ---: |
| forward:100_edge | -11.350000 | 0.909 |
| forward:100_normal | -26.698276 | 0.741 |
| forward:100_stress | -28.875000 | 0.748 |
| forward:150_edge | -50.250000 | 1.613 |
| forward:150_normal | -42.900000 | 1.032 |
| forward:150_stress | -62.253333 | 1.639 |
| forward:50_edge | -2.533333 | 0.303 |
| forward:50_normal | -6.200000 | 0.210 |
| forward:50_stress | -10.072838 | 0.344 |
| forward:full_edge | -18.151328 | 1.903 |
| forward:full_normal | -7.640248 | 1.304 |
| forward:full_stress | -26.303465 | 1.884 |
| reverse:100_edge | -54.302160 | 1.072 |
| reverse:100_normal | -43.970000 | 0.790 |
| reverse:100_stress | -55.013233 | 0.954 |
| reverse:150_edge | -31.855714 | 2.067 |
| reverse:150_normal | -5.735000 | 1.335 |
| reverse:150_stress | -27.315000 | 2.319 |
| reverse:50_edge | -0.400000 | 0.419 |
| reverse:50_normal | -0.600000 | 0.296 |
| reverse:50_stress | -3.920000 | 0.409 |
| reverse:full_edge | -11.775000 | 2.755 |
| reverse:full_normal | -43.760000 | 2.091 |
| reverse:full_stress | -7.821429 | 2.784 |

## Rich Python

- selected budget: 60 seconds
- active stages: raw preprocessing, fixed/reserved assignment, SSR/caregiver construction and rescue, small DFS/Beam construction, relocation repair, VND 1-opt/2-swap/3-cycle/group/caregiver rebuild, structured and special pattern generation, protected multi-group MIP, conflict-component LNS, restricted pattern MIP, native-equivalent final validation/scoring semantics
- Formal24 mean / median gap: `2.7649% / 1.7361%`
- wall mean / median / P90 / max: `49.745 / 55.150 / 58.976 / 59.794s`

## Migration

The raw Rich path now implements the full single-cabin stage inventory plus
native outer cabin decomposition. Strict 60-second quality parity **FAILS** (accepted exceptions above):
21 improve / 0 tie / 3 regress against the frozen Python result. Complete/legal
and evaluator gates pass 24/24, including complete Rich candidates in every case.
No profiling or 5-second compression is permitted. The detailed sections below
record historical component checkpoints; their earlier pending-work statements
are superseded by the latest acceptance checkpoint.

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

Native pricing now generates ordered infant/occupant interference pairs and the
frozen same-group infant correction upper bound. The latter clamps pair costs to
nonnegative values, takes at most the passenger count per possible infant seat,
then retains at most the number of infant passengers. Phase-I excludes this
objective relaxation while retaining every dual contribution.

`build_rich_pricing_costs` aggregates infant/occupant duals in pair order and SSR
flag duals in their supplied insertion order. Placement costs subtract occupied
and protection-seat resources, SSR counts and occupant/infant duals. SSR flag costs
remain separate per location for first activation during DFS. Full pattern reduced
costs include group, seat, flagged/all SSR and all three infant-row dual families.
Native column evaluation preserves the frozen SSR tuple-string coefficient order;
seat-set and pattern-cost floating summation is checked with numerical tolerance.

The oracle calls the original frozen functions directly. Eleven public fixtures
plus a multi-infant/flagged synthetic cover signed sparse duals, missing defaults,
shuffled flag-row insertion, other-group flag rows, protected resources, ordinary
and Phase-I pricing, positive/negative/zero infant pair costs, and passenger-count
limits including zero. Four sampled placement combinations per group also compare
full column reduced costs; these assembly probes do not claim feasible columns.
The structured suite passed 12 tests and 1,582 subtests. Pair identities/order and
costs compare exactly; placement costs use nine decimal places, infant relaxation
twelve, and column/flag/upper-bound costs eight. DFS traversal, historical starts,
negative-column retention, termination, workspace reuse and production structured
integration remain required. No M3 completion claim is made.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 127 tests and 15,338 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed for the dynamic-cost checkpoint.

The native `price_rich_group_dfs` now connects the verified pricing prerequisites
into the frozen full-domain search. It preserves forced/forbidden domain filtering,
stable base and historical branch ordering, passenger priority, rectangle/independent
bounds, first-activation flag costs, resource and symmetry rejection, caregiver
reachability, and the full depth/resource/occupancy/infant/flag/care dominance key.
Historical patterns are reconstructed and validated before incumbent seeding.
Negative columns retain insertion order across replacements; pool overflow evicts
the first worst-cost entry. Returned columns are stably sorted by reduced cost.

Pricing caches now retain historical starts and reusable static workspaces. Legacy
mode does not save a newly built workspace, matching Python; a workspace already
present can still be reused. Search checks negative refinement, then node limit,
then local deadline after incrementing the node count. Exact large-group limits,
early infeasible branches, root lower bounds on interrupted search and all search
count diagnostics follow the frozen function. The implementation remains outside
the production structured stage until orchestration and recording are integrated.

Direct calls to the complete frozen `_price_group_exact_dfs` validate four public
fixtures plus ordinary-symmetry, protection and caregiver/infant/flag synthetics.
The replay covers structured large group duals, signed seat duals, nonzero SSR/baby
duals, legacy builds, workspace reuse, node-limit clamping, exact large-group
overrides, Phase-I, expired deadlines, zero-time negative refinement, conflicting
forced/forbidden domains, historical reuse/rejection, disabled symmetry and actual
negative-pool eviction. All returned signatures/order, history updates, search
counters and termination classifications match exactly in 786 subtests. Costs and
bounds use an absolute tolerance of max(1e-8, eight ULPs), including 1e12 group-dual
cases; pattern master costs use eight decimal places. Timing measurements are not
equated across languages, and nonbinding/expired budgets isolate deterministic
termination behavior. Structured integration and the full Rich gate remain open.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 128 tests and 16,124 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed for the standalone DFS checkpoint.

`generate_rich_structured_patterns` now connects all structured generation layers
and the native DFS in frozen group/window order. It applies activation and difficult
group rules, initializes active SSR types, preserves group/window time limits,
skips empty window domains, replaces duplicate sources without moving their first
insertion position, and synchronously records each candidate with affected-group
score, protection ownership, source and global-value pinning. Diagnostics include
repair ordering, window/node totals, extreme/span-reducing counts and every tier.
The function is verified through the probe; production scheduler and elite-store
callback wiring are the next integration step, and special pricing remains open.

Complete-function differential uses actual public construction assignments across
eleven fixtures, plus disabled, already-expired and partial-assignment scenarios.
Nonbinding stage/window budgets and a fixed DFS node cap isolate deterministic
semantics. All candidate contents/order, sources, protection lists, pins and count
diagnostics match; scores/repair metrics use eight decimal places. Fourteen
scenarios passed. The test explicitly requires rebuilt and global-value candidates.

This combined test exposed an existing scoring boundary: a hypothetical structured
proposal can overlap another group's occupied seat. Python's infant interaction
function returns zero for the same physical seat; native scoring had charged the
same-subrow penalty. Native scoring now excludes identical physical seats, and the
full-function test explicitly asserts that overlapping-infant candidates occur.
Legal complete assignments retain their prior scoring behavior. This change is
required for candidate scoring parity; it does not legalize overlapping proposals.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 129 tests and 16,138 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed for this structured orchestration checkpoint.

Structured generation is now connected to the production scheduler after VND,
through `generate_rich_patterns_m3`. The wrapper restores the preserved Rich
snapshot and records candidates synchronously into the existing elite store with
construction-frozen conflict-diversity activation, current resource ownership,
protection resources, source and pinning. It does not substitute the separately
selected fallback assignment. Incomplete Rich candidates skip this quality stage.
The scheduler accounts for the pattern-generation base budget, VND carry, global
deadline and actual completion; diagnostics are exported as
`rich_structured_pattern_generation`, and `rich_elite_store` now includes this stage.

The combined construction/repair/VND/structured differential compares final elite
contents/order, state, scores and structured diagnostics across eleven public
fixtures, plus an incomplete-candidate skip. The probe deliberately changes the
separate selected-assignment field before invoking the production wrapper, proving
that saved Rich state drives generation and conflict ownership. Runtime schedule
replay now checks pattern-generation windows at actual native stage times. Raw CLI
tests require generated/retained structured candidates when enabled and no such
candidates when disabled, with external legality/scoring audit in both cases.
The focused pipeline/runtime/group-first suites passed 23 tests and 80 subtests.

The migration map now marks the implemented structured layers and DFS prerequisites
`FULL_CPP` at component scope. This does not close M3: special dual pricing still
requires its restricted LP/orchestration. Protected multi-group MIP, dynamic
relocation, conflict LNS/local branching, Rich restricted MIP and final full-stage
diagnostic/quality acceptance remain required. The legacy RR master still consumes
its own patterns; it is not the completed Rich pattern-master consumer.

Release /O2 build passed with the two existing conversion warnings. Full
native/state-replay-enabled regression passed 131 tests and 16,151 subtests, with
9 skips, including the 12,000-operation state differential and external raw-CLI
legality/scoring checks. `git diff --check` passed. No Formal24 or profiling run
was performed for this production structured integration.

### Special dual pricing component

`generate_rich_special_dual_patterns` now implements the frozen special pricing
function in the existing HiGHS-linked feasibility compilation unit. Core-only
probes retain their existing link closure. The continuous LP preserves sorted
group/seat row order, input-group column order, incumbent/elite duplicates,
elite insertion order, protection resources and affected-group scores. Only group
and seat row duals feed the existing native pricing DFS. Repair priority, the
13-group cap, 50 ms per-group deadlines, quick pool size four and the fixed
`reduced_cost < -1e-7` acceptance threshold match Python. Accepted candidates are
reported synchronously without changing the assignment.

The full-function differential covers eleven public fixtures and a synthetic
15-special-group cap case, each with empty elite input, duplicate incumbent elite,
real structured elites, disabled activation and expired deadline (60 subtests).
It compares LP status/column count, attempted groups, DFS nodes, accepted candidate
order/resources, reduced costs and callback scores. The protection-heavy fixture
uses its legal public reference assignment because construction/repair alone is
incomplete; special pricing requires a complete context. No-special-group and
zero-duration early returns are also checked.

Production integration remains pending: frozen Python calls special pricing
**after protected multi-group MIP**, not directly after structured generation.
This component does not change the production stage sequence or complete M3.
Protected/dynamic MIPs, conflict LNS/local branching, Rich restricted MIP and the
full correctness/quality gate remain open. No Formal24 or profiling run was made.

Validation: Release /O2 build passed with the two existing conversion warnings.
Full native/state-replay-enabled regression passed 132 tests and 16,211 subtests
with 9 skips. `git diff --check` passed.

### Protected dynamic relocation prerequisite

`add_rich_dynamic_relocation_patterns` ports the protected MIP's nested dynamic
pricing function. Per-group base caches survive repeated component calls; released
caches filter complete occupied/protection resource sets, preserve coordinate
origins/Big-M and reproduce the frozen shallow-copy history/workspace behavior.
The implementation preserves empty-domain exits, discovery deadline placement,
column limits, the artificial group dual of `1e12`, affected-group rescoring and
`released_component_pricing` source. New identities enter the elite store directly:
existing identities are never replaced, ordinary capacity eviction is bypassed,
and empty group entries are retained after a pricing call with no patterns.

The frozen nested-function differential passes 720 sequential call subtests over
eleven public fixtures with active/disabled/clamped-one-column settings, repeated
calls, expired calls and partial/full outside-resource occupancy. It compares full
elite contents/order/scores, cache counts and per-call diagnostics, requiring
actual protected patterns and growth beyond the ordinary two-pattern test limit.
Resource/window cache geometry differential now passes 924 subtests, explicitly
covering exclusion caused by a blocked resource while the occupied seat remains
available. Existing elite normalization is shared without changing ordinary
replacement, pinning or eviction behavior.

The protected stage remains incomplete: root/component selection, its integer
model and conditional SSR rows, context reconstruction and strict acceptance are
still required before production integration. These component checks do not close
M3/M4 or the Full C++ Rich quality gate; no Formal24 or profiling run was made.

Final-version validation: Release /O2 build passed with the two existing conversion
warnings. Full native/state-replay-enabled regression passed 133 tests and 17,331
subtests with 9 skips. Duplicate identities skip scoring before insertion, as in
Python. `git diff --check` passed.

### Protected pattern conflicts and context reconstruction

`rich_patterns_have_conditional_ssr_conflict` reproduces Python's two-pattern
row/subrow profiles: only SSR passengers activate isolation flags, and an active
location limits every SSR type to one passenger, including duplicate types already
inside one pattern. Row and subrow activation remain distinct.

`rebuild_rich_pattern_component` copies the actual context, releases selected
groups, visits group IDs in sorted order and stably puts caregiver-independent
passengers first. The dedicated pattern placement path preserves Python's complete
proposed-seat exclusion set and chosen single-side protection fallback. Successful
rebuilds return a snapshot; failure leaves the original context intact. A rebuilt
context is not automatically accepted: the caller must still run the frozen
independent hard validation and strict full-score acceptance.

The shared Rich seat-feasibility check also now checks **all** SSR type counts
when a location becomes active. Previously it checked only the incoming
passenger's type, missing activation by a third, different-type passenger when two
unflagged passengers of another type were already present. This is a verified
migration correction, not a new constraint.

Frozen-function tests cover eleven public fixtures with deterministic candidate
pairs and 891 component selections, comparing complete assignment/protection,
resource ownership, SSR map and insertion-order state on success, and rejection
without source-state mutation on failure. Explicit cases cover third-type
activation, row versus subrow isolation, ignored non-SSR flags, and fallback from
a chosen protection seat that belongs to the proposed group allocation. The
focused suite passed 3 tests and 899 subtests.

Root/component selection, the protected integer model and strict acceptance,
production integration, LNS and Rich restricted MIP remain open. No full-stage
or Formal24 quality gate is claimed, and no profiling run was performed.

Validation: Release /O2 build passed with the two existing conversion warnings.
Full native/state-replay-enabled regression passed 136 tests and 18,230 subtests
with 9 skips. `git diff --check` passed.

### Complete protected MIP function

`improve_rich_protected_mip` now ports the complete frozen protected/priority
multi-group MIP function. It preserves activation and parameter clamps, initial
repair metrics, stable root/option ranking, conflict-priority component selection,
tested-component deduplication, outside-resource filtering **after** option
truncation, dynamic pricing of conflict groups, and sorted group/resource model
rows. The native binary model includes occupied/protected resources and conditional
SSR incompatibility rows with the same column order and HiGHS settings. Selected
columns are reconstructed in an isolated context; complete hard validation and a
strict full Rich-score improvement are required before replacing the current state.
Full scoring uses incremental Rich preference semantics, not a sum of local
pattern scores. All original diagnostic counters and component records are emitted.

Full-function replay covers eleven public fixtures with normal execution,
dynamic pricing disabled, both root types disabled and expired deadlines (44
variants), comparing diagnostics, accepted components, final state/order/protection
and complete post-pricing elite contents. Separate synthetic cases require an
accepted joint improvement, actual conditional SSR rows, rejection of an attractive
fixed-seat violation, priority-only activation at its business-time threshold and
the frozen lower clamps on root/component/group/option limits.

The component function is native; its production multi-pass loop, stage captures,
carry accounting and post-protected special pricing are not yet wired. LNS,
Rich restricted MIP and the final full-stage quality gate remain open. No Formal24
or profiling run was performed.

Validation: Release /O2 build passed with the two existing conversion warnings.
Full native/state-replay-enabled regression passed 139 tests and 18,278 subtests
with 9 skips. Coverage assertions require actual tested components, dynamic
pricing calls and newly inserted patterns in the public replay. `git diff --check`
passed.

### Production protected stage and post-protected special pricing

The raw native Rich trajectory now runs structured generation, the protected MIP
multi-pass loop, then special dual pricing before the still-independent legacy RR
adapter/master. Both new wrappers restore preserved `rich_state`; they never use
the separately selected fallback as their starting context. Protected passes
capture current patterns after every call, including disabled/expired calls, and
stop on disabled execution, zero acceptance, gain below the configured floor,
pass limit or deadline. Aggregation matches frozen Python, including retaining
first-pass root metadata and conditional-row count. A legal strict improvement can
replace the selected fallback independently of continuing the Rich trajectory.

Special pricing records accepted candidates synchronously with protection resources,
`special_dual_pricing` source, pinning and construction-frozen conflict diversity.
The production scheduler uses protected base budget plus carry, then the reserved
special-pricing deadline, preserving protected carry across special pricing.
Diagnostics are exposed as `rich_protected_multigroup_mip`,
`rich_special_dual_pricing` and their `rich_stage_timing` windows.

Wrapper replay executes the frozen Python multi-pass AST and special-pricing
function on eleven public fixtures plus five controlled pass-limit/gain/disabled/
expired cases. It compares aggregate diagnostics, current state, selected-score
behavior and the final elite store; the native probe deliberately starts with an
empty selected assignment. Tests require two-pass continuation and actual negative
special-pricing candidates. Runtime schedule replay now includes both stages at
actual native times. A dense full-resource raw CLI case verifies activation,
nonempty optimal special LP/pricing, stage order and unchanged carry, with external
legality/scoring audit for enabled and disabled runs.

Protected and special rows are `FULL_CPP` at component scope. The full Rich gate
remains open: conflict-component LNS, Rich restricted MIP, final diagnostic audit
and Formal24 quality parity are still required. The legacy RR master still does
not consume the complete Rich elite store. No Formal24 or profiling run was made.

Validation: Release /O2 build passed with the two existing conversion warnings.
The protected/special wrapper differential passed 16 subtests; runtime schedule
and raw CLI activation checks passed. Full native/state-replay-enabled regression
passed 141 tests and 18,296 subtests with 9 skips. `git diff --check` passed.

### LNS scoring and assignment matching prerequisite

Native LNS workspace preserves assignment insertion order, seat-number-null
eligibility and the initial infant positions across later context mutations.
Ordinary groups use strict-improvement bitmask matching; special groups enumerate
input-order permutations with released-resource feasibility and caregiver checks.
This prerequisite is not yet wired into production LNS.

Frozen-function differential covers eleven public fixtures, deadline expiry,
null versus empty fixed-seat numbers, group-size floors, reversed key order,
zero-weight ties and cached plus uncached queries after infant moves. Release
build passed; full regression passed 145 tests and 18,702 subtests with 9 skips.
Python candidate generation iterates a hash set; the historical hash seed has
not been established. Candidate traversal equivalence remains unresolved and
must not be inferred from matching parity. LNS candidate generation,
search/late acceptance and production integration remain incomplete. No Formal24
or profiling run was made.

### LNS local pattern master prerequisite

The native HiGHS local master preserves component/option column order, lexical
seat rows, one pattern per group and unit seat capacity. It omits only the root's
exact current passenger-to-seat tuple: a permutation within the same occupied
seat set remains eligible. Scores are negated for minimization; threads, seed,
gap and remaining-time cap follow the frozen Python function. No conditional SSR
rows are added here; the later reconstruction and evaluator acceptance must
perform their original checks. An incomplete selection returns no choice.

Differential executes the frozen local-master AST on 70 two-passenger-group
models: forced departure, no departure option, shared-seat infeasibility, empty
columns, root permutation, second-passenger resource conflict and 64 seeded
random models with varying component/column order. All choices matched. This
master is available to native LNS but is not yet a production search stage.

Validation: Release /O2 build passed with the two existing conversion warnings.
LNS tests passed 5 tests and 476 subtests. Full native/state-replay-enabled
regression passed 146 tests and 18,772 subtests with 9 skips. `git diff --check`
passed. No Formal24, profiling or 5-second compression run was made.

### LNS geometric candidate generation and traversal contract

Native LNS now enumerates the current placement and each center's nearby seat
combinations using the frozen row-distance, horizontal-distance and seat-ID
ordering. Candidate tuples are lexically sorted and deduplicated; ordinary and
special matching feed the full affected-group score. The bounded heap retains
strict score-only replacement and descending (score, sequence) output, then calls
the recorder synchronously for the configured top options. Production LNS search
and elite-store wiring are still pending.

Traversal boundary: frozen Python iterates a set of seat tuples and its reference
artifacts do not identify the process hash seed. Native code uses lexical tuple
traversal of the same set. This is an explicit deterministic tie-order choice,
not historical assignment parity: options tied at the capacity boundary and
subsequent search trajectories can differ. The final per-case quality gate is
still required. Differential separately checks the candidate set, exact option
and recorder output after normalizing only Python set traversal, and the retained
score multiset against the unmodified Python function. No frozen production
Python source or configuration is changed.

Validation: Release /O2 build passed with the two existing conversion warnings.
LNS differential passed 7 tests and 892 subtests, including eleven public cases,
expired deadlines, config floors and all-zero-score cutoff ties. Full regression
with native/state replay enabled passed 148 tests and 19,188 subtests with 9 skips.
`git diff --check` passed. No Formal24 or profiling run was made.

### LNS search, acceptance and production integration

Native LNS now runs the frozen component search over the native Rich state. It
rebuilds repair-priority roots, expands ejection chains, ranks related groups,
constructs occupied/free seat pools, generates options, solves the local master,
rebuilds component groups in component order, validates complete legality, and
keeps the Python distinction between partial rebuild restoration and rejected
complete rebuild restoration. Dynamic candidate ranking, stagnation size cycling,
late-history acceptance, score deltas, best-context restoration and diagnostics
are implemented. The stage uses the preserved Rich state, the LNS budget plus
preceding carry, synchronously records `lns_generated`, and captures `lns_final`
patterns before the restricted stage.

The native wrapper differential compared final assignments, assignment insertion
order, callback records and diagnostics on eleven public fixtures with the
candidate set traversed lexically. It also covered disabled and expired calls,
strict acceptance, production elite capture and stage state continuation. Raw
runtime checks cover enabled/disabled LNS, nonzero option/search diagnostics and
actual stage timing/carry. This is the first complete native LNS stage, but the
lexical tie traversal boundary remains documented and the full 24-case quality
parity gate has not run.

Zero LNS base budget does not disable LNS: frozen Python still consumes positive
carry. A raw production assertion covers this explicitly. Older VND-isolation
tests now disable LNS explicitly, while the overall Q0 fallback test requires a
strict improvement when the selected source is LNS. A deterministic two-group
fixture forces one worsening acceptance and verifies restoration of both the
best assignment and its insertion order.

Validation: Release /O2 passed with the two existing conversion warnings.
LNS/runtime tests passed 14 tests and 18,820 subtests before adding the directed
worsening fixture. The final full regression reported 151 passed, 9 skipped,
37,107 subtests passed and one coverage-assertion failure: public fixtures did
not exercise worsening acceptance. That assertion was moved to the directed
fixture; both the corrected full-function test and new fixture then passed
(2 tests, 9,167 subtests). No native source changed after that full regression;
no remaining observed failure is unresolved. `git diff --check` passed.
No Formal24, profiling or 5-second compression run was made. Rich restricted
MIP/local branching, final audit and the full quality gate remain outstanding.

### Rich restricted MIP and local branching component

Native restricted MIP now consumes the complete Rich elite store. Group and seat
rows use sorted IDs, columns preserve elite insertion order, occupied and blocked
resources share capacity rows, and current group signatures identify the sparse
MIP start. Local branching retains its frozen incumbent column set, grows its
radius across attempts, and is disabled by any structured global value-block
source. Each selected combination receives a no-good row; conditional SSR pair
rows are added only after failed reconstruction. Changed groups alone are rebuilt
and accepted using lexicographic legality/completeness/soft-score quality.

Differential runs the actual frozen Python function on eleven public fixtures
and controlled cases for branching floors/disable, disabled and expired entry,
empty elite store, global value-block source, conditional SSR cuts and fixed-seat
rejection. It compares all non-time diagnostics, accepted pattern provenance,
final assignment, protected resources, insertion order and unchanged elite store.
Entry contexts in these tests are complete and legal, matching production's
existing entry gate. The native validator folds missing passengers into its
violation count, so arbitrary incomplete-entry quality ordering has not been
claimed equivalent. That distinction does not change complete legal entry's
acceptance of only complete legal improvements.

The function is not yet connected to the production restricted stage; the legacy
RR master remains the production consumer. Stage deadline/carry integration,
final diagnostics audit and Formal24 parity remain outstanding.

Validation: Release /O2 build passed with two existing conversion warnings.
Restricted differential passed 3 tests and 18 subtests, with nonzero acceptance,
conditional cuts, rebuild failures and hard-invalid rejections asserted. Full
native/state-replay-enabled regression passed 156 tests and 37,217 subtests with
9 skips. `git diff --check` passed. No Formal24 or profiling run was made.

### Production Rich restricted stage integration

The raw Rich trajectory now ends with the Rich restricted MIP over the complete
elite store, after special pricing and LNS. The legacy RR adapter/master call was
removed from this production path; its standalone sources remain available for
historical replay. The stage restores the saved Rich state, preserves the elite
store, and replaces the independently selected fallback only on a legal strict
improvement. Restricted time is the maximum of base and tail budgets plus LNS
remaining time, clipped by the shared search deadline.

Output includes `rich_restricted_pattern_mip` diagnostics and the actual
`restricted_mip` timing window. Compatibility fields `rich_pattern_count`,
`rich_master_attempts` and radius now describe this Rich consumer; pattern/master
score is its final Rich state score, selected-pattern count is the group count
when a full master attempt occurs, and the obsolete RR baby-pair count stays zero.
This consumer evaluates infant effects through the native full scorer rather
than the old RR pair-variable model. No final-state recapture is introduced,
matching the frozen Python restricted-stage call site.

Wrapper replay starts with an empty selected fallback assignment to verify that
continuation uses `rich_state`; it compares final state, diagnostics, elite store
and strict selection against Python. Runtime replay includes the restricted
window at actual stage timestamps. Native production tests continue to audit
complete legality and external score consistency. The final coverage/diagnostics
audit and Formal24 60-second per-case quality gate have not yet passed; no
profiling or 5-second compression is authorized by this integration alone.

Validation: Release /O2 build passed with two existing conversion warnings.
Production-path tests passed 26 tests and 31 subtests. Full regression, including
restricted wrapper differential and native state replay, passed 157 tests and
37,227 subtests with 9 skips. `git diff --check` passed. The existing
`native/run_native_formal24.py` still uses the historical `gap_I` field and lacks
a per-case Python-60s gate; it must be audited and corrected before final use.
No Formal24 run or performance compression was performed at this checkpoint.

### Formal24 audit harness contract correction

The benchmark now reports `gap_frozen_union_reference` (fractional units) and
per-case `delta_vs_python` separately. `--require-python-parity` requires the
60-second group-first configuration and fails on any regression beyond 1e-8;
a positive mean cannot hide a failing case. CG integer and certified LP gaps
remain unavailable without identity-bound certificates. The benchmark alone
does not certify full ACTIVE coverage or the final Full Rich gate.

Before invoking any solver, the harness checks exactly 24 unique cases, input
and seatmap SHA against frozen sources, reference configuration identity in
parity mode, and all frozen Python assignments through the independent evaluator.
Output directories must be empty and failed subprocesses cannot consume stale
results. Source and reference hashes and native stage diagnostics are preserved.
`--audit-reference-only` performs this preflight without starting the solver.

Validation: the actual frozen 24-case preflight passed. All reference allocations
were complete/legal and reproduced CSV scores with maximum error 4.55e-13.
Metric boundary tests and native runtime schedule tests passed 7 tests and 14
subtests. The 60-second native comparison has not yet been completed.

### First full-pipeline Formal24 attempt: failed, not a parity result

The frozen `338b075` run in `outputs/research/full_cpp_rich_60s_338b075`
completed eleven cases (all complete/legal), then aborted on
`forward:full_stress` with `LNS MIP API error`. Of those eleven, ten improved
against Python and `forward:full_normal` regressed by 3.05. No full 24-case
summary exists; this run does not pass the gate and is retained unchanged.

The failure was reproduced as a HiGHS 1.15.1 presolve/postsolve error: the
solver reports an infeasible primal vector and `kError`. Frozen Python reads
the solution and discards an incomplete group selection. Native LNS now does
the same instead of aborting the entire allocator, with `solver_errors` in
its diagnostics. Model-construction and solution-access API errors still fail
explicitly. An independently generated 3-group/9-seat model reproduces the
failure without private data. Release /O2 passed; LNS tests passed 12 tests
and 18,901 subtests. The temporary model dump is not part of production code.

The outer `run_allocation` cabin decomposition is also missing in the native
entry point: frozen configuration enables per-cabin ordering, proportional
budgets and final-cabin remaining-time allocation. This is an outstanding
ACTIVE orchestration requirement, despite the completed single-cabin stages.
It must be migrated and tested before another full parity run. No profiling,
5-second compression, parameter tuning or reference change is justified.

### Cabin orchestration and current 60-second acceptance checkpoint

`bebcc60` adds the frozen outer `run_allocation` behavior: homogeneous group
validation, increasing target-seat-count cabin order, proportional/minimum first
budgets, last-cabin remaining time, filtered old/new topology, per-cabin stage
activation and independent solving, merged native scoring and legality. Nested
per-cabin diagnostics are authoritative; merged stages do not have one timestamp.
Public replay calls the unchanged Python outer function at actual native budget
timestamps and compares filtering, budgets, stage budgets and merged scoring.
Single-cabin, disabled decomposition and mixed-group rejection are covered.
Release /O2 and 164 tests / 37,230 subtests passed, with 9 skips.

The `bebcc60` 24-case run was complete/legal/evaluator-consistent but failed
quality (19/0/5). Its source and artifacts remain retained separately.
Two large regressions exposed an integration defect: Q0/Q1/Q2A consumed the
construction window before Rich began. `54f827c` starts the Rich stage clock
after fallback preparation while retaining the original global deadline.
The actual origin is serialized as `rich_allocation_start`; the frozen Python
schedule replay checks this offset. Budget/runtime tests passed 8 tests and
271 subtests after this change. No solver configuration was tuned.

The latest single frozen run is
`outputs/research/full_cpp_rich_60s_54f827c/summary.json`, binary SHA-256
`f9f0aa9509c319275e9aae2343b5a755e6c0e1940537c1b2fb925032f443b811`.
It reports 24/24 complete/legal, zero unassigned and native/external violations,
24/24 evaluator consistent, total-score error zero and individual-score maximum
error 2.84e-13. All 24 Rich candidates are complete. Two LNS solver errors were
contained through the reference incomplete-choice behavior. Same configured
60-second budget: 20 improve / 0 tie / 4 regress, mean delta +11.154328, minimum
-4.554324; the harness exits 1. No per-case best-of-runs union is used.

| Case | F_cpp - F_python_60s |
| --- | ---: |
| forward:100_edge | +2.633333333 |
| forward:100_normal | +3.810775862 |
| forward:100_stress | +3.600000000 |
| forward:150_edge | +26.536271930 |
| forward:150_normal | +19.192393509 |
| forward:150_stress | +17.266666667 |
| forward:50_edge | +0.583333333 |
| forward:50_normal | +15.200000000 |
| forward:50_stress | +1.600000000 |
| forward:full_edge | +59.718809524 |
| forward:full_normal | -3.850000000 |
| forward:full_stress | +11.086071429 |
| reverse:100_edge | -2.882142857 |
| reverse:100_normal | +7.220000000 |
| reverse:100_stress | +2.816124322 |
| reverse:150_edge | +42.178037767 |
| reverse:150_normal | +0.362142857 |
| reverse:150_stress | +36.407508452 |
| reverse:50_edge | +0.800000000 |
| reverse:50_normal | -1.200000000 |
| reverse:50_stress | +5.220000000 |
| reverse:full_edge | -4.554323673 |
| reverse:full_normal | +3.677616995 |
| reverse:full_stress | +20.281260504 |

Process wall mean/median/max: 41.021/51.908/60.177 seconds. Maximum native
solver wall: 59.794 seconds. A configured 60-second budget is not a claim that
every full process invocation finished within 60 seconds.
CG integer, exact LP and safe certified LP bound counts remain 0 identity-bound
cases; their gaps are unavailable. Frozen-union mean gap 0.965789% is a separate
feasible-reference metric, not a CG or LP certificate.

Remaining quality diagnosis: reverse:50_normal's Business cabin reproduces a
wall-clock DFS boundary. Frozen 0.02-second Python DFS gives construction -13.35
and VND -11.7; nonbinding Python DFS reaches the node cap and gives -12.9 before
and after VND, matching native. Nonbinding full construction/VND differential
also passes on that cabin. This explains one regression without proving the
other three; changing limits, injecting old allocations or selecting best reruns
would change the frozen protocol and is not a migration fix. LNS lexical set
traversal and known reference repair-state defects remain explicit boundaries.

The original v3 summary's top-level construction counters incorrectly show zero
for multi-cabin results; actual counters are preserved inside each cabin result.
The harness now sums cabin counts and requires all cabins for complete flags;
a directed aggregation test passes. Original benchmark artifacts are unchanged.
Latest complete regression after the clock and aggregation corrections passed
165 tests and 37,230 subtests, with 9 skips, in 376.40 seconds. Release /O2
build passed with the two pre-existing C4244 conversion warnings.
`git diff --check` passed.

### Deferred independent fallback improvements

In `group-first`, Q0 remains the initial legal fallback. Rich construction,
repair and all quality stages now run before Q1/Q2A; independent Q1/Q2A search
uses only remaining global time and replaces the selected solution only on a
complete/legal strict improvement. It does not alter the saved Rich state or
elite store. The `group-soft` entry retains the earlier Q1-first behavior.
`fallback_improvement_started/finished` expose the ordering in each cabin.
No stage parameters or algorithm limits were changed. Q0 work still consumes
the global budget; this is not a claim of zero orchestration overhead.

Release /O2 passed with the two existing conversion warnings. Targeted tests
passed 26 tests / 28 subtests, covering actual Q1/Q2A search after restricted
MIP, strict fallback selection, shared deadlines and preserved Rich state.
Complete native/state-replay regression passed 166 tests / 37,230 subtests,
with 9 skips, in 375.86 seconds. `git diff --check` passed.
The quality gate must be rerun on this frozen implementation.

### c6a41f3 acceptance after deferred Q1/Q2A

Frozen result: `outputs/research/full_cpp_rich_60s_c6a41f3/summary.json`.
24/24 complete/legal/evaluator-consistent, zero unassigned/hard violations,
zero total-score error, max individual error 3.41e-13. Quality still FAILS:
21 improve / 0 tie / 3 regress; mean delta +13.772696. Remaining regressions:
forward:full_normal -3.05, reverse:50_normal -1.2, reverse:full_edge -4.554324.
Process mean/median/max: 39.326/46.427/59.837 seconds. Frozen parameters and
reference were unchanged; this is one run, not a best-of-runs selection.

reverse:100_edge now executes five restricted attempts, gaining 29.259524,
and no longer regresses. Its Rich start dropped to 2.744 seconds. However,
reverse:full_edge still spends 14.987 seconds in Q0 before Rich. With the
shifted stage origin, pattern/protected/LNS deadlines clip against the global
limit and restricted MIP gets zero attempts. Deferring Q1/Q2A fixed part of
the integration issue, but the stage-clock adjustment in 54f827c still needs
review against the frozen budget/carry contract when Q0 is slow. The total
60-second deadline must not be extended. No profiling or parameter tuning
was performed and full parity remains unachieved.

## Correctness

### Follow-up regression isolation on the frozen 54f827c artifacts

Nonbinding construction/repair/VND replay passed against the actual frozen
Python functions for the Economy cabins of `reverse:100_edge`,
`forward:full_normal`, and `reverse:full_edge`. This isolates deterministic
component semantics; it is not a rerun of the 60-second acceptance benchmark.

For `forward:full_normal`, replaying both restricted implementations from the
recorded final assignment and the same 3,363-column native elite store produced
identical assignments and all non-time diagnostics: five attempts, zero accepted,
zero score gain. This checks that particular master entry state, not the
unrecorded historical Python generation trajectory or original pre-master state.
Local diagnostic results remain under `build/native/restricted_diagnostic`.

The other two Economy cabins exhausted the global deadline before restricted
MIP could attempt a solve. Their Q0/Q1/Q2A preparation consumed 16.611 seconds
(`reverse:100_edge`) and 24.154 seconds (`reverse:full_edge`). C++ Rich scores
before restricted were better than Python's corresponding pre-restricted scores,
but Python then gained 33.356556 and 43.947945 respectively in its restricted
stage. Thus shifting the construction origin fixed expired entry but did not
restore the reference's budget availability. Fallback scheduling remains an
integration issue to resolve while preserving the 60-second total deadline and
the required Q0/Q1/Q2A fallback behavior. No limits or benchmark parameters were
changed during this isolation, and no new full Formal24 run was made.

- Rich Python Formal24 complete: `24/24`
- unassigned: `0`
- hard violations: `0`
- evaluator consistent: `24/24`
- maximum score error: `0`
- raw-native Formal24 feasibility: `24/24` complete, `0` unassigned, `0` native/external hard violations
- native raw-core parity: `24/24` valid cases matched with assignment-state round trips; `5/5` invalid fixed-seat fixtures rejected consistently
- native/external score parity: maximum absolute error `3.183231456205249e-12`

## Quality parity

Latest frozen implementation `c6a41f3`: **FAIL**, 21 improve / 0 tie / 3 regress.
Mean delta +13.772696 does not satisfy the per-case nonregression requirement.
See the acceptance checkpoint above for each case and diagnostic boundaries.

## Timing and profile

The earlier 1.668-second native-core smoke remains a solver-ready `.native_v2` test, not a Full C++ performance result. Native profiling is intentionally deferred until raw JSON, Rich active-stage coverage, correctness, and quality parity pass.

## Implementation boundary

All named single-cabin ACTIVE stages and enabled outer cabin orchestration now
have native implementations. Full C++ Rich parity is not certified: three frozen
Python regressions remain, including a reproduced wall-clock DFS trajectory
boundary. Production Python callbacks and `.native_v2` dependencies are zero
in the raw CLI call chain; the historical V1R callback path is separate.
