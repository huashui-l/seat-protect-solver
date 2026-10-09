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

RichSpecialPricingDiagnostics generate_rich_special_pricing_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, bool enabled, GroupConstructionResult& result
) {
    if (!result.rich_candidate_complete) return {};
    AssignmentState state(problem); state.restore(result.rich_state);
    return generate_rich_special_dual_patterns(problem, state, result.rich_elite_store, deadline, enabled,
        [&](int group, const RichExactPattern& candidate, double score) {
            RichElitePattern pattern;
            pattern.local_score = score; pattern.source = "special_dual_pricing"; pattern.pinned = true;
            for (const auto& entry : candidate.assignments)
                pattern.assignments.emplace_back(problem.passengers[entry.first].hostnum, problem.seats[entry.second].id);
            std::map<int, std::vector<std::string>> blocked;
            for (const auto& entry : candidate.blocked_by)
                blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
            pattern.blocked_by_host.assign(blocked.begin(), blocked.end());
            result.rich_elite_store.record_candidate(problem.groups[group].id, std::move(pattern), state, result.rich_conflict_diversity_active);
        });
}

RichSpecialPricingDiagnostics generate_rich_special_dual_patterns(
    const Problem& problem, const AssignmentState& state, const RichEliteStore& elite,
    std::chrono::steady_clock::time_point deadline, bool enabled,
    const std::function<void(int, const RichExactPattern&, double)>& recorder
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    RichSpecialPricingDiagnostics result;
    result.enabled = enabled;
    if (!enabled || started >= deadline) return result;
    std::set<int> special;
    for (size_t g = 0; g < problem.groups.size(); ++g) {
        bool fixed = false, relevant = false;
        for (int p : problem.groups[g].passengers) {
            const auto& passenger = problem.passengers[p];
            fixed |= passenger.has_new_seat;
            relevant |= !passenger.ssr.empty() || passenger.need_cared
                || passenger.need_single_empty || passenger.need_both_empty;
        }
        if (!fixed && relevant) special.insert(static_cast<int>(g));
    }
    if (special.empty()) { result.lp_status = "no_special_groups"; return result; }
    std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
    void* highs = solver.get();
    const auto check = [](HighsInt status) {
        if (status == kHighsStatusError) throw std::runtime_error("special pricing LP API error");
    };
    check(Highs_setBoolOptionValue(highs, "output_flag", 0));
    check(Highs_setIntOptionValue(highs, "threads", 1));
    check(Highs_setDoubleOptionValue(highs, "time_limit",
        std::max(.01, std::chrono::duration<double>(deadline - started).count())));
    const double infinity = Highs_getInfinity(highs);
    std::map<int, HighsInt> group_rows;
    std::map<std::string, HighsInt> seat_rows;
    for (const auto& group : problem.groups) group_rows[group.id] = 0;
    for (const auto& seat : problem.seats) seat_rows[seat.id] = 0;
    HighsInt row = 0;
    for (auto& entry : group_rows) {
        entry.second = row++;
        check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr));
    }
    for (auto& entry : seat_rows) {
        entry.second = row++;
        check(Highs_addRow(highs, -infinity, 1.0, 0, nullptr, nullptr));
    }
    const auto add_column = [&](int gid, double score, const auto& resources) {
        std::vector<HighsInt> rows{group_rows.at(gid)};
        for (const auto& seat : resources) rows.push_back(seat_rows.at(seat));
        std::vector<double> values(rows.size(), 1.0);
        check(Highs_addCol(highs, -score, 0.0, infinity,
            static_cast<HighsInt>(rows.size()), rows.data(), values.data()));
        ++result.lp_columns;
    };
    for (size_t g = 0; g < problem.groups.size(); ++g) {
        const auto& group = problem.groups[g];
        std::set<std::string> resources;
        for (int p : group.passengers) {
            resources.insert(problem.seats.at(state.passenger_to_seat.at(p)).id);
            for (int s : state.assigned_blocked[p]) resources.insert(problem.seats[s].id);
        }
        add_column(group.id, evaluate_rich_group_score(problem, state.passenger_to_seat, static_cast<int>(g)).total(), resources);
        const auto found = elite.groups().find(group.id);
        if (found != elite.groups().end())
            for (const auto& pattern : found->second)
                add_column(group.id, pattern.local_score, pattern.seat_resources);
    }
    const auto run_status = Highs_run(highs);
    const auto status = Highs_getModelStatus(highs);
    const char* names[] = {"Not Set", "Load error", "Model error", "Presolve error", "Solve error",
        "Postsolve error", "Empty", "Optimal", "Infeasible", "Primal infeasible or unbounded",
        "Unbounded", "Bound on objective reached", "Target for objective reached", "Time limit reached",
        "Iteration limit reached", "Unknown", "Solution limit reached", "Interrupted by user",
        "Memory limit reached", "Interrupted by HiGHS"};
    result.lp_status = status >= 0 && status < 20 ? names[status] : "Unknown";
    if (run_status != kHighsStatusError && status == kHighsModelStatusOptimal) {
        std::vector<double> row_duals(row);
        check(Highs_getSolution(highs, nullptr, nullptr, nullptr, row_duals.data()));
        RichPricingDuals duals;
        for (const auto& entry : group_rows) duals.group[entry.first] = row_duals[entry.second];
        for (const auto& entry : seat_rows) duals.seat[problem.seat_index.at(entry.first)] = row_duals[entry.second];
        auto config = problem.rich_pricing_config;
        config.type = native_json::Value::Type::Object;
        for (const auto& entry : std::vector<std::pair<std::string, double>>{
            {"quick_pricing_columns_per_group", 4.0}, {"dfs_discovery_time_limit", .05}}) {
            auto& value = config.object[entry.first];
            value.type = native_json::Value::Type::Number; value.number = entry.second;
        }
        std::set<std::string> ssr;
        for (const auto& passenger : problem.passengers) if (!passenger.ssr.empty()) ssr.insert(passenger.ssr);
        const std::vector<std::string> active_ssr(ssr.begin(), ssr.end());
        const auto fixed = preprocess_fixed_seats(problem);
        const auto baby = build_rich_baby_costs(problem);
        const auto queue = build_rich_repair_queue(problem, state.passenger_to_seat);
        for (const auto& metric : queue) {
            const auto found = std::find_if(problem.groups.begin(), problem.groups.end(),
                [&](const Group& group) { return group.id == metric.group_id; });
            const int g = static_cast<int>(found - problem.groups.begin());
            if (!special.count(g)) continue;
            if (result.groups_attempted >= 13 || Clock::now() >= deadline) break;
            auto cache = build_rich_pricing_cache(problem, g, fixed);
            const auto priced = price_rich_group_dfs(problem, g, config, duals, baby, {}, {},
                std::min(deadline, Clock::now() + std::chrono::milliseconds(50)), cache,
                false, false, false, active_ssr);
            ++result.groups_attempted;
            result.dfs_nodes += priced.nodes;
            for (const auto& pattern : priced.patterns) {
                const double reduced = rich_pattern_reduced_cost(pattern, duals, baby, false);
                if (reduced >= -1e-7) continue;
                auto proposal = state.passenger_to_seat;
                for (const auto& entry : pattern.assignments) proposal[entry.first] = entry.second;
                recorder(g, pattern, evaluate_rich_group_score(problem, proposal, g).total());
                ++result.negative_patterns;
                result.accepted_patterns.emplace_back(pattern, reduced);
            }
        }
    }
    result.seconds = std::chrono::duration<double>(Clock::now() - started).count();
    return result;
}

void write_rich_special_pricing_diagnostics(std::ostream& output,
    const Problem& problem, const RichSpecialPricingDiagnostics& d
) {
    output << "{\"enabled\":" << (d.enabled ? "true" : "false")
        << ",\"lp_status\":\"" << d.lp_status << "\",\"lp_columns\":" << d.lp_columns
        << ",\"groups_attempted\":" << d.groups_attempted << ",\"negative_patterns\":" << d.negative_patterns
        << ",\"dfs_nodes\":" << d.dfs_nodes << ",\"seconds\":" << d.seconds << ",\"accepted_patterns\":[";
    for (size_t i = 0; i < d.accepted_patterns.size(); ++i) {
        if (i) output << ',';
        const auto& pattern = d.accepted_patterns[i].first;
        const int gid = problem.passengers[pattern.assignments.front().first].group_id;
        output << "{\"group_id\":" << gid << ",\"reduced_cost\":" << d.accepted_patterns[i].second << ",\"assignments\":[";
        for (size_t j = 0; j < pattern.assignments.size(); ++j) {
            if (j) output << ',';
            const auto& a = pattern.assignments[j];
            output << "[[" << gid << ',' << problem.passengers[a.first].hostnum << "],\"" << problem.seats[a.second].id << "\"]";
        }
        output << "],\"blocked_by\":[";
        for (size_t j = 0; j < pattern.blocked_by.size(); ++j) {
            if (j) output << ',';
            const auto& a = pattern.blocked_by[j];
            output << "[\"" << problem.seats[a.first].id << "\",[" << gid << ',' << problem.passengers[a.second].hostnum << "]]";
        }
        output << "]}";
    }
    output << "]}";
}

}  // namespace full_cpp
