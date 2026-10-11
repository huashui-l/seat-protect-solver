#include "native_feasibility_solver.hpp"
#include "gurobi_c.h"

#include <algorithm>
#include <chrono>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>

namespace full_cpp {

RichSpecialPricingDiagnostics enrich_rich_dual_pool(const Problem& problem,
    const AssignmentState& state, RichEliteStore& elite,
    std::chrono::steady_clock::time_point deadline) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    RichSpecialPricingDiagnostics d; d.enabled = true;
    const auto finish = [&]() { d.seconds = std::chrono::duration<double>(Clock::now() - started).count(); return d; };
    const auto remaining = [&]() { return std::chrono::duration<double>(deadline - Clock::now()).count(); };
    if (remaining() <= .01) { d.lp_status = "deadline"; return finish(); }
    const auto check = [](int code) {
        if (code) throw std::runtime_error("dual pool Gurobi API error: " + std::to_string(code));
    };
    GRBenv* raw_env = nullptr;
    const int env_status = GRBemptyenv(&raw_env);
    std::unique_ptr<GRBenv, decltype(&GRBfreeenv)> environment(raw_env, GRBfreeenv);
    check(env_status);
    check(GRBsetintparam(raw_env, GRB_INT_PAR_OUTPUTFLAG, 0));
    check(GRBsetintparam(raw_env, GRB_INT_PAR_THREADS, 1));
    check(GRBstartenv(raw_env));
    GRBmodel* raw_model = nullptr;
    const int model_status = GRBnewmodel(raw_env, &raw_model, "dual_pattern_pool", 0,
        nullptr, nullptr, nullptr, nullptr, nullptr);
    std::unique_ptr<GRBmodel, decltype(&GRBfreemodel)> model(raw_model, GRBfreemodel);
    check(model_status);
    std::map<int, int> group_rows;
    int row = 0;
    for (const auto& group : problem.groups) {
        group_rows[group.id] = row++;
        check(GRBaddconstr(model.get(), 0, nullptr, nullptr, GRB_EQUAL, 1.0, nullptr));
    }
    const int seat_offset = row;
    for (size_t s = 0; s < problem.seats.size(); ++s) {
        ++row;
        check(GRBaddconstr(model.get(), 0, nullptr, nullptr, GRB_LESS_EQUAL, 1.0, nullptr));
    }
    check(GRBupdatemodel(model.get()));
    // The legal incumbent makes the pool LP feasible. Its duals guide discovery;
    // this resource-only, context-scored LP is not a global optimality certificate.
    auto initial_pool = elite;
    initial_pool.capture(state, "dual_pool_incumbent");
    for (const auto& group : initial_pool.groups()) {
        if (remaining() <= .01) { d.lp_status = "deadline"; return finish(); }
        for (const auto& pattern : group.second) {
            std::vector<int> rows{group_rows.at(group.first)};
            for (const auto& seat : pattern.seat_resources)
                rows.push_back(seat_offset + problem.seat_index.at(seat));
            std::vector<double> values(rows.size(), 1.0);
            check(GRBaddvar(model.get(), static_cast<int>(rows.size()), rows.data(), values.data(),
                -pattern.local_score, 0.0, GRB_INFINITY, GRB_CONTINUOUS, nullptr));
            ++d.lp_columns;
        }
    }
    if (remaining() <= .01) { d.lp_status = "deadline"; return finish(); }
    auto* solver_env = GRBgetenv(model.get());
    check(GRBsetdblparam(solver_env, GRB_DBL_PAR_TIMELIMIT, remaining() * .5));
    check(GRBoptimize(model.get()));
    int status = 0;
    check(GRBgetintattr(model.get(), GRB_INT_ATTR_STATUS, &status));
    d.lp_status = status == GRB_OPTIMAL ? "LP Optimal" : "LP status " + std::to_string(status);
    if (status != GRB_OPTIMAL) return finish();
    std::vector<double> values(row);
    check(GRBgetdblattrarray(model.get(), GRB_DBL_ATTR_PI, 0, row, values.data()));
    RichPricingDuals duals;
    for (const auto& entry : group_rows) duals.group[entry.first] = values[entry.second];
    for (size_t s = 0; s < problem.seats.size(); ++s) duals.seat[static_cast<int>(s)] = values[seat_offset + s];
    auto config = problem.rich_pricing_config;
    config.type = native_json::Value::Type::Object;
    const auto set_number = [&](const char* key, double number) {
        auto& value = config.object[key]; value.type = native_json::Value::Type::Number; value.number = number;
    };
    set_number("quick_pricing_columns_per_group", problem.rich.elite_patterns_from_pricing_per_call);
    std::set<std::string> types;
    for (const auto& passenger : problem.passengers) if (!passenger.ssr.empty()) types.insert(passenger.ssr);
    const std::vector<std::string> active(types.begin(), types.end());
    const auto fixed = preprocess_fixed_seats(problem);
    const auto baby = build_rich_baby_costs(problem);
    std::map<int, int> indexes;
    for (size_t g = 0; g < problem.groups.size(); ++g) indexes[problem.groups[g].id] = static_cast<int>(g);
    std::vector<int> order;
    for (const auto& metric : build_rich_repair_queue(problem, state.passenger_to_seat)) {
        const int g = indexes.at(metric.group_id);
        if (std::none_of(problem.groups[g].passengers.begin(), problem.groups[g].passengers.end(),
            [&](int p) { return problem.passengers[p].has_new_seat; })) order.push_back(g);
    }
    for (size_t i = 0; i < order.size() && remaining() > .01; ++i) {
        const int g = order[i];
        const double share = std::min(problem.rich.structured_pattern_dfs_per_group, remaining() / (order.size() - i));
        const auto group_deadline = std::min(deadline, Clock::now()
            + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(share)));
        set_number("dfs_discovery_time_limit", share);
        auto cache = build_rich_pricing_cache(problem, g, fixed);
        const auto priced = price_rich_group_dfs(problem, g, config, duals, baby, {}, {},
            group_deadline, cache, false, false, false, active);
        ++d.groups_attempted; d.dfs_nodes += priced.nodes;
        for (const auto& candidate : priced.patterns) {
            const double reduced = rich_pattern_reduced_cost(candidate, duals, baby, false);
            if (reduced >= -1e-7) continue;
            RichElitePattern pattern; pattern.source = "dual_pool_pricing";
            auto assignment = state.passenger_to_seat;
            for (const auto& entry : candidate.assignments) {
                assignment[entry.first] = entry.second;
                pattern.assignments.emplace_back(problem.passengers[entry.first].hostnum, problem.seats[entry.second].id);
            }
            std::map<int, std::vector<std::string>> blocked;
            for (const auto& entry : candidate.blocked_by)
                blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
            pattern.blocked_by_host.assign(blocked.begin(), blocked.end());
            if (elite.insert_relocation(problem.groups[g].id, std::move(pattern), [&]() {
                return evaluate_rich_group_score(problem, assignment, g).total();
            })) {
                ++d.negative_patterns; d.accepted_patterns.emplace_back(candidate, reduced);
            }
        }
    }
    return finish();
}

} // namespace full_cpp
