#include "native_group_constructor.hpp"

#include "native_feasibility_solver.hpp"
#include "native_rich_geometry.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <future>
#include <limits>
#include <numeric>
#include <set>
#include <stdexcept>
#include <tuple>

namespace full_cpp {
namespace {
constexpr double kTolerance = 1e-9;
}  // namespace

bool seed_rich_fallback(const Problem& problem, GroupConstructionResult& result) {
    const auto& assignment = result.passenger_to_seat;
    if (validate_complete_assignment(problem, assignment) != 0) return false;
    std::set<int> occupied(assignment.begin(), assignment.end()), protected_empty;
    for (int p = 0; p < static_cast<int>(assignment.size()); ++p) {
        if (!problem.passengers[p].need_both_empty) continue;
        const auto& seat = problem.seats[assignment[p]];
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        protected_empty.insert(neighbors.begin(), neighbors.end());
    }
    // Match single-empty resources globally; a greedy choice can strand a
    // later passenger even though the MIP's complete assignment is legal.
    std::map<int, int> empty_owner;
    const auto augment = [&](auto&& self, int p, std::set<int>& visited) -> bool {
        for (int seat : problem.seats[assignment[p]].same_block_neighbors) {
            if (occupied.count(seat) || protected_empty.count(seat) || !visited.insert(seat).second) continue;
            const auto owner = empty_owner.find(seat);
            if (owner == empty_owner.end() || self(self, owner->second, visited)) {
                empty_owner[seat] = p;
                return true;
            }
        }
        return false;
    };
    for (int p = 0; p < static_cast<int>(assignment.size()); ++p) {
        if (!problem.passengers[p].need_single_empty || problem.passengers[p].need_both_empty) continue;
        std::set<int> visited;
        if (!augment(augment, p, visited)) return false;
    }
    std::vector<int> single_block(assignment.size(), -1);
    for (const auto& owner : empty_owner) single_block[owner.second] = owner.first;
    AssignmentState state(problem);
    for (int p = 0; p < static_cast<int>(assignment.size()); ++p) {
        auto excluded = occupied;
        excluded.erase(assignment[p]);
        if (!state.assign_rich_pattern(p, assignment[p], excluded, single_block[p])) return false;
    }
    result.rich_state = state.save();
    result.rich_candidate_complete = true;
    // Keep failed-construction patterns for a separate search task. The main
    // pool keeps its original Q0/VND trajectory under the short deadline.
    result.rich_fallback_patterns = std::move(result.rich_elite_store);
    result.rich_elite_store = RichEliteStore(problem.rich.elite_patterns_per_group);
    result.rich_elite_store.capture(state, "q0_fallback");
    return true;
}

GroupConstructionResult construct_rich_m1(
    const Problem& problem, const std::vector<int>& q0_assignment,
    const GroupConstructionResult& q2a_result,
    std::chrono::steady_clock::time_point allocation_start,
    RichStageSchedule& schedule
) {
    (void)q0_assignment;
    using Clock = std::chrono::steady_clock;
    const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - allocation_start).count(); };
    const auto at = [&](double seconds) { return allocation_start
        + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)); };
    const double construction_started = elapsed();
    const auto construction_window = schedule.begin("construction", construction_started);
    GroupConstructionResult result = q2a_result;
    result.rich_elite_store = RichEliteStore(problem.rich.elite_patterns_per_group);
    AssignmentState state = initialize_rich_assignment(problem);
    const auto cache = build_rich_candidate_cache(problem, state);
    const auto construction_deadline = at(construction_window.deadline);
    std::vector<std::future<AssignmentSnapshot>> alternatives;
    if (problem.rich_stage_budgets.business_time_limit <= 5.0
        && problem.rich_stage_budgets.business_time_limit >= 1.0) {
        for (double pressure : {0.0, 2.0})
            alternatives.push_back(std::async(std::launch::async, [&, pressure]() {
                auto local = problem;
                local.rich.old_seat_reservation_pressure = pressure;
                auto candidate = initialize_rich_assignment(local);
                const auto candidate_cache = build_rich_candidate_cache(local, candidate);
                construct_rich_assignment(local, candidate, candidate_cache, construction_deadline);
                GroupConstructionResult repair_diagnostics;
                repair_rich_assignment(local, candidate, candidate_cache.rankings,
                    construction_deadline, repair_diagnostics);
                return candidate.save();
            }));
    }
    const auto diagnostics = construct_rich_assignment(problem, state, cache, construction_deadline);
    std::vector<AssignmentSnapshot> alternative_states;
    for (auto& alternative : alternatives) alternative_states.push_back(alternative.get());
    result.rich_dfs_attempted = diagnostics.search.dfs_attempted;
    result.rich_dfs_succeeded = diagnostics.search.dfs_succeeded;
    result.rich_dfs_nodes = diagnostics.search.dfs_nodes;
    result.rich_beam_groups = diagnostics.search.beam_groups;
    result.rich_paired_ssr_passes = diagnostics.paired_passes;
    result.rich_paired_rescue_attempted = static_cast<int>(diagnostics.rescue.attempted.size());
    result.rich_paired_rescue_rescued = static_cast<int>(diagnostics.rescue.rescued.size());
    result.rich_paired_rescue_unresolved = static_cast<int>(diagnostics.rescue.unresolved.size());
    result.rich_paired_joint_rebuilds = diagnostics.rescue.joint_rebuilds;
    result.rich_construction_repair_queue = build_rich_repair_queue(problem, state.passenger_to_seat);
    result.rich_conflict_diversity_active = problem.rich_conflict_diversity_time_active;
    result.rich_elite_store.capture(state, "construction");
    const double construction_finished = elapsed();
    schedule.finish("construction", construction_window, construction_finished);
    result.rich_construction_seconds = construction_finished;
    result.rich_construction_carry_seconds = schedule.carry();
    result.rich_stage_timing["construction"] = {
        problem.rich_stage_budgets.stages.at("construction"), construction_window.effective_budget,
        construction_started, construction_finished, construction_window.deadline,
        schedule.carry(), schedule.pricing_reserve()};
    result.rich_construction_unassigned = static_cast<int>(std::count(
        state.passenger_to_seat.begin(), state.passenger_to_seat.end(), -1));
    result.rich_construction_assigned = static_cast<int>(problem.passengers.size()) - result.rich_construction_unassigned;
    result.rich_construction_score = evaluate_soft_score(problem, state.passenger_to_seat);
    const double repair_started = elapsed();
    const auto repair_window = schedule.begin("repair", repair_started, result.rich_construction_unassigned);
    repair_rich_assignment(problem, state, cache.rankings, at(repair_window.deadline), result);
    result.rich_elite_store.capture(state, "repair");
    bool complete = validate_complete_assignment(problem, state.passenger_to_seat) == 0;
    double selected_score = evaluate_soft_score(problem, state.passenger_to_seat);
    for (auto& snapshot : alternative_states) {
        if (validate_complete_assignment(problem, snapshot.passenger_to_seat) != 0) continue;
        AssignmentState candidate(problem);
        candidate.restore(snapshot);
        result.rich_elite_store.capture(candidate, "construction_alternative");
        const double candidate_score = evaluate_soft_score(problem, candidate.passenger_to_seat);
        if (!complete || candidate_score > selected_score + kTolerance) {
            state.restore(std::move(snapshot));
            selected_score = candidate_score;
            complete = true;
        }
    }
    if (!complete) for (const auto& snapshot : alternative_states) {
        AssignmentState candidate(problem);
        candidate.restore(snapshot);
        result.rich_elite_store.capture(candidate, "construction_alternative");
    }
    const double repair_finished = elapsed();
    schedule.finish("repair", repair_window, repair_finished);
    result.rich_stage_timing["repair"] = {
        problem.rich_stage_budgets.stages.at("repair"), repair_window.effective_budget,
        repair_started, repair_finished, repair_window.deadline,
        schedule.carry(), schedule.pricing_reserve()};
    result.rich_state = state.save();
    result.rich_rankings = cache.rankings;
    result.rich_repair_score = evaluate_soft_score(problem, state.passenger_to_seat);
    result.rich_candidate_complete = validate_complete_assignment(problem, state.passenger_to_seat) == 0;
    // Q0/Q1/Q2A stay independent fallbacks, never the construction stage input.
    if (result.rich_candidate_complete
        && (validate_complete_assignment(problem, result.passenger_to_seat) != 0
            || result.rich_repair_score > result.group_construction_score + kTolerance)) {
        result.passenger_to_seat = state.passenger_to_seat;
        result.selected_components = evaluate_score_components(problem, result.passenger_to_seat);
        result.group_construction_score = result.rich_repair_score;
        result.score_delta = result.rich_repair_score - result.q0_score;
        result.selected_incumbent = "rich-m1";
    }
    return result;
}

RichOrdinaryVndDiagnostics improve_rich_vnd_m2(
    const Problem& problem, std::chrono::steady_clock::time_point deadline,
    GroupConstructionResult& diagnostics
) {
    const auto started = std::chrono::steady_clock::now();
    if (!diagnostics.rich_candidate_complete) return {};
    AssignmentState state(problem);
    state.restore(diagnostics.rich_state);
    const auto vnd = improve_rich_ordinary_vnd(state, diagnostics.rich_rankings, deadline, true, true);
    diagnostics.rich_state = state.save();
    diagnostics.rich_elite_store.capture(state, "vnd");
    diagnostics.rich_vnd_one_opt_moves += vnd.one_opt;
    diagnostics.rich_vnd_two_swap_moves += vnd.swaps;
    diagnostics.rich_vnd_three_cycle_moves += vnd.cycles;
    diagnostics.rich_vnd_group_rebuild_moves += vnd.group_rebuilds;
    diagnostics.rich_vnd_caregiver_rebuild_moves += vnd.caregiver_rebuilds;
    diagnostics.rich_vnd_score = evaluate_soft_score(problem, state.passenger_to_seat);
    // Continue the actual Rich trajectory even when repair lost to a fallback.
    // Selection is separate from stage state and must remain complete/legal/better.
    if (validate_complete_assignment(problem, state.passenger_to_seat) == 0
        && diagnostics.rich_vnd_score > diagnostics.group_construction_score + kTolerance) {
        diagnostics.passenger_to_seat = state.passenger_to_seat;
        diagnostics.selected_components = evaluate_score_components(problem, state.passenger_to_seat);
        diagnostics.group_construction_score = diagnostics.rich_vnd_score;
        diagnostics.score_delta = diagnostics.rich_vnd_score - diagnostics.q0_score;
        diagnostics.selected_incumbent = "rich-m2-vnd";
    }
    diagnostics.rich_vnd_seconds += std::chrono::duration<double>(
        std::chrono::steady_clock::now() - started).count();
    return vnd;
}

void generate_rich_patterns_m3(const Problem& problem, std::chrono::steady_clock::time_point deadline,
    GroupConstructionResult& diagnostics
) {
    if (!diagnostics.rich_candidate_complete) return;
    AssignmentState state(problem);
    state.restore(diagnostics.rich_state);
    const auto record = [&](RichEliteStore& pool, int g, const RichTieredPattern& candidate, double score, bool pinned) {
            RichElitePattern pattern;
            pattern.local_score = score; pattern.source = "structured_" + candidate.source; pattern.pinned = pinned;
            for (const auto& entry : candidate.pattern.assignments)
                pattern.assignments.emplace_back(problem.passengers[entry.first].hostnum, problem.seats[entry.second].id);
            std::map<int, std::vector<std::string>> blocked;
            for (const auto& entry : candidate.pattern.blocked_by)
                blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
            pattern.blocked_by_host.assign(blocked.begin(), blocked.end());
            pool.record_candidate(problem.groups[g].id, std::move(pattern), state,
                diagnostics.rich_conflict_diversity_active);
    };
    std::atomic<bool> stop_geometry{false};
    std::future<RichEliteStore> geometry;
    if (problem.rich.enable_structured_pattern_generation && problem.rich.enable_restricted_mip_portfolio
        && problem.rich_stage_budgets.business_time_limit <= 5.0
        && std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count() > 0.1) {
        geometry = std::async(std::launch::async, [&]() {
            RichEliteStore pool(problem.rich.elite_patterns_per_group);
            const auto fixed = preprocess_fixed_seats(problem);
            const auto orders = build_rich_geometry_orders(problem, fixed,
                std::min(deadline, std::chrono::steady_clock::now() + std::chrono::milliseconds(40)));
            const auto order = build_rich_structured_order(problem, state.passenger_to_seat);
            for (int g : order.difficult_groups) {
                if (stop_geometry.load() || std::chrono::steady_clock::now() >= deadline) break;
                const auto options = build_rich_placement_options(problem, g, fixed);
                std::vector<RichTieredPattern> patterns;
                generate_rich_geometry_patterns(problem, g, options, orders, {},
                    std::min(deadline, std::chrono::steady_clock::now() + std::chrono::milliseconds(30)), patterns);
                for (const auto& candidate : patterns) {
                    if (stop_geometry.load() || std::chrono::steady_clock::now() >= deadline) break;
                    auto proposal = state.passenger_to_seat;
                    for (const auto& entry : candidate.pattern.assignments) proposal[entry.first] = entry.second;
                    record(pool, g, candidate, evaluate_rich_group_score(problem, proposal, g).total(), false);
                }
            }
            return pool;
        });
    }
    diagnostics.rich_structured = generate_rich_structured_patterns(problem, state.passenger_to_seat, deadline,
        [&](int g, const RichTieredPattern& candidate, double score, bool pinned) {
            record(diagnostics.rich_elite_store, g, candidate, score, pinned);
        });
    stop_geometry.store(true);
    if (geometry.valid()) {
        diagnostics.rich_geometry_patterns = geometry.get();
        for (const auto& group : diagnostics.rich_geometry_patterns.groups())
            diagnostics.rich_structured.geometry_pool_patterns += static_cast<int>(group.second.size());
    }
}

}  // namespace full_cpp
