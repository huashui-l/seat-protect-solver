#include "native_feasibility_solver.hpp"

#include "native_group_constructor.hpp"

#include "interfaces/highs_c_api.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <future>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace full_cpp {

RichRestrictedDiagnostics improve_rich_restricted_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, GroupConstructionResult& result
) {
    if (!result.rich_candidate_complete) return {};
    AssignmentState state(problem); state.restore(result.rich_state);
    using MasterResult = std::pair<AssignmentSnapshot, RichRestrictedDiagnostics>;
    std::vector<std::future<MasterResult>> alternatives;
    if (problem.rich.enable_restricted_pattern_mip && problem.rich_stage_budgets.business_time_limit <= 5.0
        && std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count() > 0.5) {
        for (int seed : {1, 2}) alternatives.push_back(std::async(std::launch::async, [&, seed]() {
            auto candidate_problem = problem;
            // Keep one small-change neighborhood alongside the open master.
            // This varies search structure as well as the solver's random seed.
            if (problem.rich.enable_restricted_mip_portfolio)
                candidate_problem.rich.enable_pattern_local_branching = seed == 1;
            AssignmentState candidate(problem); candidate.restore(result.rich_state);
            auto diagnostics = improve_rich_restricted_mip(candidate_problem, candidate, result.rich_elite_store, deadline, seed);
            if (problem.rich.enable_restricted_mip_portfolio && seed == 1 && !result.rich_rankings.empty()
                && std::chrono::steady_clock::now() < deadline) {
                const auto started = std::chrono::steady_clock::now();
                const auto before = candidate.save();
                const double before_score = evaluate_soft_score(problem, candidate.passenger_to_seat);
                improve_rich_ordinary_vnd(candidate, result.rich_rankings, deadline, true, true);
                const double after_score = evaluate_soft_score(problem, candidate.passenger_to_seat);
                if (after_score > before_score + 1e-9
                    && validate_complete_assignment(problem, candidate.passenger_to_seat) == 0)
                    diagnostics.portfolio_vnd_improvement = after_score - before_score;
                else candidate.restore(before);
                diagnostics.portfolio_vnd_seconds = std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - started).count();
            }
            return MasterResult{candidate.save(), std::move(diagnostics)};
        }));
    }
    auto diagnostics = improve_rich_restricted_mip(problem, state, result.rich_elite_store, deadline);
    double best_score = evaluate_soft_score(problem, state.passenger_to_seat);
    for (auto& alternative : alternatives) {
        auto candidate = alternative.get();
        const double candidate_score = evaluate_soft_score(problem, candidate.first.passenger_to_seat);
        if (candidate_score > best_score + 1e-9
            && validate_complete_assignment(problem, candidate.first.passenger_to_seat) == 0) {
            state.restore(std::move(candidate.first));
            diagnostics = std::move(candidate.second);
            best_score = candidate_score;
        }
    }
    result.rich_state = state.save();
    const double score = evaluate_soft_score(problem, state.passenger_to_seat);
    if (validate_complete_assignment(problem, state.passenger_to_seat) == 0 && score > result.group_construction_score + 1e-9) {
        result.passenger_to_seat = state.passenger_to_seat;
        result.group_construction_score = score;
        result.selected_components = evaluate_score_components(problem, state.passenger_to_seat);
        result.selected_incumbent = "rich-m6-restricted-mip";
        result.score_delta = score - result.q0_score;
    }
    return diagnostics;
}

RichRestrictedDiagnostics improve_rich_restricted_mip(const Problem& problem, AssignmentState& state,
    const RichEliteStore& elite, std::chrono::steady_clock::time_point deadline, int seed
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - started).count(); };
    RichRestrictedDiagnostics d; d.enabled = problem.rich.enable_restricted_pattern_mip;
    for (const auto& group : elite.groups()) for (const auto& pattern : group.second) {
        ++d.patterns; ++d.resource_complete_patterns;
        if (!pattern.blocked_seats.empty()) ++d.protected_resource_patterns;
    }
    if (!d.enabled || started >= deadline) { d.stopped_by_deadline = started >= deadline; d.seconds = elapsed(); return d; }
    std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
    void* highs = solver.get();
    const auto check = [](HighsInt status, const char* operation = "model construction") {
        if (status == kHighsStatusError) throw std::runtime_error(std::string("Rich restricted MIP API error: ") + operation);
    };
    check(Highs_setBoolOptionValue(highs, "output_flag", 0));
    check(Highs_setIntOptionValue(highs, "threads", 1));
    check(Highs_setIntOptionValue(highs, "random_seed", seed));
    check(Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0));
    std::map<int, int> groups;
    for (size_t g = 0; g < problem.groups.size(); ++g) groups[problem.groups[g].id] = static_cast<int>(g);
    std::map<int, HighsInt> group_rows;
    std::map<std::string, HighsInt> seat_rows;
    HighsInt row = 0;
    for (const auto& group : groups) { group_rows[group.first] = row++; check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr)); }
    for (const auto& seat : problem.seats) seat_rows[seat.id] = 0;
    for (auto& seat : seat_rows) { seat.second = row++; check(Highs_addRow(highs, -Highs_getInfinity(highs), 1.0, 0, nullptr, nullptr)); }
    const auto signature = [&](int g) {
        std::vector<std::pair<int, std::string>> result;
        for (int p : problem.groups[g].passengers) result.emplace_back(problem.passengers[p].hostnum,
            state.passenger_to_seat[p] < 0 ? std::string{} : problem.seats[state.passenger_to_seat[p]].id);
        std::sort(result.begin(), result.end()); return result;
    };
    const auto resource_signature = [&](int g) {
        std::set<std::string> resources;
        for (int p : problem.groups[g].passengers) {
            if (state.passenger_to_seat[p] >= 0) resources.insert(problem.seats[state.passenger_to_seat[p]].id);
            for (int seat : state.assigned_blocked[p]) resources.insert(problem.seats[seat].id);
        }
        return resources;
    };
    std::vector<std::pair<int, const RichElitePattern*>> columns;
    std::vector<HighsInt> incumbent;
    bool global_value_block = false;
    for (const auto& group : elite.groups()) for (const auto& pattern : group.second)
        global_value_block |= pattern.source == "structured_global_value_block";
    for (const auto& group : groups) {
        const auto found = elite.groups().find(group.first);
        if (found == elite.groups().end()) continue;
        const auto current_resources = resource_signature(group.second);
        for (const auto& pattern : found->second) {
            std::set<std::string> occupied, resources;
            bool valid = pattern.assignments.size() == problem.groups[group.second].passengers.size();
            for (const auto& entry : pattern.assignments)
                if (!occupied.insert(entry.second).second || !seat_rows.count(entry.second)) valid = false;
            for (const auto& seat : pattern.seat_resources)
                if (!resources.insert(seat).second || !seat_rows.count(seat)) valid = false;
            if (!valid) continue;
            std::vector<HighsInt> indexes{group_rows.at(group.first)};
            for (const auto& seat : pattern.seat_resources) indexes.push_back(seat_rows.at(seat));
            std::vector<double> values(indexes.size(), 1.0);
            const auto column = static_cast<HighsInt>(columns.size());
            check(Highs_addCol(highs, -pattern.local_score, 0.0, 1.0, static_cast<HighsInt>(indexes.size()), indexes.data(), values.data()));
            check(Highs_changeColIntegrality(highs, column, kHighsVarTypeInteger));
            columns.emplace_back(group.first, &pattern);
            if (pattern.assignments == signature(group.second)) {
                const bool already_added = std::any_of(incumbent.begin(), incumbent.end(),
                    [&](HighsInt index) { return columns[index].first == group.first; });
                if (!already_added && resources == current_resources) incumbent.push_back(column);
            }
        }
    }
    if (columns.empty()) { d.reason = "no_usable_patterns"; d.seconds = elapsed(); return d; }
    d.usable_columns = static_cast<int>(columns.size());
    d.branching_initialized = true;
    d.branching_enabled = problem.rich.enable_pattern_local_branching && incumbent.size() == groups.size() && !global_value_block;
    d.initial_radius = std::max(1, problem.rich.pattern_local_branching_initial_radius);
    d.radius_growth = std::max(1, problem.rich.pattern_local_branching_radius_growth);
    const int configured_radius = problem.rich.pattern_local_branching_max_radius == std::numeric_limits<int>::max()
        ? static_cast<int>(groups.size()) : problem.rich.pattern_local_branching_max_radius;
    d.maximum_radius = std::max(d.initial_radius, configured_radius);
    HighsInt branching_row = -1;
    if (d.branching_enabled) {
        branching_row = row++;
        std::vector<double> values(incumbent.size(), 1.0);
        check(Highs_addRow(highs, static_cast<double>(groups.size() - std::min(static_cast<size_t>(d.initial_radius), groups.size())),
            Highs_getInfinity(highs), static_cast<HighsInt>(incumbent.size()), incumbent.data(), values.data()));
    }
    if (incumbent.size() == groups.size()) {
        std::vector<double> values(columns.size(), 0.0);
        for (HighsInt column : incumbent) values[column] = 1.0;
        d.mip_start_supplied = Highs_setSolution(highs, values.data(), nullptr, nullptr, nullptr) == kHighsStatusOk;
    }
    const double initial_score = evaluate_rich_group_score(problem, state.passenger_to_seat, -1).total();
    double best_score = initial_score;
    // Production enters this stage only after complete legal Rich construction.
    const auto quality = [&](const std::vector<int>& assignment, double score) {
        const int unassigned = static_cast<int>(std::count(assignment.begin(), assignment.end(), -1));
        return std::make_tuple(-validate_complete_assignment(problem, assignment), -unassigned, score);
    };
    auto best_quality = quality(state.passenger_to_seat, initial_score);
    for (int attempt = 0; attempt < std::max(1, problem.rich.restricted_pattern_mip_attempts); ++attempt) {
        const double remaining = std::chrono::duration<double>(deadline - Clock::now()).count();
        if (remaining <= .01) break;
        int radius = -1;
        if (branching_row >= 0) {
            radius = std::min({static_cast<int>(groups.size()), d.maximum_radius, d.initial_radius + attempt * d.radius_growth});
            check(Highs_changeRowBounds(highs, branching_row, static_cast<double>(groups.size()) - radius, Highs_getInfinity(highs)));
            d.radius_history.push_back(radius);
        }
        // HiGHS returns after postsolve; leave time for extraction and legality checks.
        const double solve_remaining = remaining
            - (problem.rich_stage_budgets.business_time_limit <= 5.0 ? 0.05 : 0.0);
        if (solve_remaining <= 0.01) break;
        // HiGHS otherwise compares the new limit against cumulative run time.
        check(Highs_zeroAllClocks(highs));
        check(Highs_setDoubleOptionValue(highs, "time_limit", solve_remaining));
        // This is an optional improvement stage. A failed solve must leave
        // the best validated assignment intact, including earlier attempts.
        if (Highs_run(highs) == kHighsStatusError) {
            d.solver_status = "Solve error";
            d.reason = "solver_error";
            break;
        }
        const char* names[] = {"Not Set", "Load error", "Model error", "Presolve error", "Solve error", "Postsolve error", "Empty", "Optimal",
            "Infeasible", "Primal infeasible or unbounded", "Unbounded", "Bound on objective reached", "Target for objective reached", "Time limit reached",
            "Iteration limit reached", "Unknown", "Solution limit reached", "Interrupted by user", "Memory limit reached", "Interrupted by HiGHS"};
        const auto status = Highs_getModelStatus(highs);
        d.solver_status = status >= 0 && status < 20 ? names[status] : "Unknown";
        std::vector<double> solution(columns.size()); check(Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr), "Highs_getSolution");
        std::vector<HighsInt> selected;
        std::map<int, RichElitePattern> choices, changed;
        for (size_t i = 0; i < columns.size(); ++i) if (solution[i] > .5) {
            selected.push_back(static_cast<HighsInt>(i)); choices[columns[i].first] = *columns[i].second;
        }
        if (selected.empty()) break;
        ++d.attempts;
        if (choices.size() != groups.size()) break;
        for (const auto& choice : choices) {
            const int g = groups.at(choice.first);
            const auto& resources = choice.second.seat_resources;
            if (choice.second.assignments != signature(g)
                || std::set<std::string>(resources.begin(), resources.end()) != resource_signature(g)) changed.insert(choice);
        }
        AssignmentSnapshot candidate;
        const bool rebuilt = rebuild_rich_pattern_component(state, changed, candidate);
        bool accepted = false;
        if (rebuilt) {
            const double score = evaluate_rich_group_score(problem, candidate.passenger_to_seat, -1).total();
            const auto candidate_quality = quality(candidate.passenger_to_seat, score);
            if (candidate_quality > best_quality) {
                const double improvement = score - best_score;
                state.restore(std::move(candidate)); best_score = score; best_quality = candidate_quality;
                ++d.accepted; accepted = true;
                d.accepted_attempts.push_back({attempt + 1, radius, improvement, changed});
            } else if (std::get<0>(candidate_quality) != 0 || std::get<1>(candidate_quality) != 0) ++d.hard_invalid_candidates;
            else ++d.nonimproving_candidates;
        } else {
            ++d.rebuild_failures;
            for (size_t a = 0; a < selected.size(); ++a) for (size_t b = a + 1; b < selected.size(); ++b) {
                const auto& left = columns[selected[a]]; const auto& right = columns[selected[b]];
                if (left.first == right.first || !rich_patterns_have_conditional_ssr_conflict(problem, left.first, *left.second, right.first, *right.second)) continue;
                const HighsInt indexes[] = {selected[a], selected[b]}; const double values[] = {1.0, 1.0};
                check(Highs_addRow(highs, -Highs_getInfinity(highs), 1.0, 2, indexes, values)); ++d.conditional_ssr_rows;
            }
        }
        if (!accepted) ++d.invalid_candidates;
        std::vector<double> values(selected.size(), 1.0);
        check(Highs_addRow(highs, -Highs_getInfinity(highs), static_cast<double>(selected.size()) - 1.0,
            static_cast<HighsInt>(selected.size()), selected.data(), values.data()));
    }
    d.score_improvement = best_score - initial_score; d.seconds = elapsed(); d.stopped_by_deadline = Clock::now() >= deadline;
    return d;
}

void write_rich_restricted_diagnostics(std::ostream& output, const RichRestrictedDiagnostics& d) {
    output << "{\"enabled\":" << (d.enabled ? "true" : "false") << ",\"patterns\":" << d.patterns
        << ",\"resource_complete_patterns\":" << d.resource_complete_patterns << ",\"protected_resource_patterns\":" << d.protected_resource_patterns
        << ",\"solver_status\":";
    if (d.solver_status.empty()) output << "null"; else output << '"' << d.solver_status << '"';
    output << ",\"usable_columns\":" << d.usable_columns << ",\"attempts\":" << d.attempts << ",\"invalid_candidates\":" << d.invalid_candidates
        << ",\"rebuild_failures\":" << d.rebuild_failures << ",\"hard_invalid_candidates\":" << d.hard_invalid_candidates
        << ",\"nonimproving_candidates\":" << d.nonimproving_candidates << ",\"conditional_ssr_rows\":" << d.conditional_ssr_rows
        << ",\"accepted\":" << d.accepted << ",\"mip_start_supplied\":" << (d.mip_start_supplied ? "true" : "false")
        << ",\"score_improvement\":" << d.score_improvement << ",\"seconds\":" << d.seconds
        << ",\"portfolio_vnd_seconds\":" << d.portfolio_vnd_seconds
        << ",\"portfolio_vnd_improvement\":" << d.portfolio_vnd_improvement
        << ",\"stopped_by_deadline\":" << (d.stopped_by_deadline ? "true" : "false");
    if (!d.reason.empty()) output << ",\"reason\":\"" << d.reason << '"';
    if (d.branching_initialized) {
        output << ",\"local_branching\":{\"enabled\":" << (d.branching_enabled ? "true" : "false")
            << ",\"initial_radius\":" << d.initial_radius << ",\"radius_growth\":" << d.radius_growth << ",\"maximum_radius\":" << d.maximum_radius << ",\"radius_history\":[";
        for (size_t i = 0; i < d.radius_history.size(); ++i) { if (i) output << ','; output << d.radius_history[i]; } output << "]}";
    }
    output << ",\"accepted_attempts\":[";
    for (size_t i = 0; i < d.accepted_attempts.size(); ++i) {
        if (i) output << ','; const auto& a = d.accepted_attempts[i];
        output << "{\"attempt\":" << a.attempt << ",\"radius\":";
        if (a.radius < 0) output << "null"; else output << a.radius;
        output << ",\"score_improvement\":" << a.score_improvement << ",\"changed_groups\":" << a.selected_patterns.size() << ",\"selected_patterns\":[";
        bool first = true;
        for (const auto& item : a.selected_patterns) {
            if (!first) output << ','; first = false;
            output << "{\"group_id\":" << item.first << ",\"source\":\"" << item.second.source << "\",\"assignments\":[";
            for (size_t j = 0; j < item.second.assignments.size(); ++j) {
                if (j) output << ','; const auto& entry = item.second.assignments[j]; output << '[' << entry.first << ",\"" << entry.second << "\"]";
            }
            output << "],\"blocked_by_host\":[";
            for (size_t j = 0; j < item.second.blocked_by_host.size(); ++j) {
                if (j) output << ','; const auto& entry = item.second.blocked_by_host[j]; output << '[' << entry.first << ",[";
                for (size_t k = 0; k < entry.second.size(); ++k) { if (k) output << ','; output << '"' << entry.second[k] << '"'; } output << "]]";
            }
            output << "]}";
        }
        output << "]}";
    }
    output << "]}";
}

}  // namespace full_cpp
