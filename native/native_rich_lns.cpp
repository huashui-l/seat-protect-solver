#include "native_feasibility_solver.hpp"

#include "native_group_constructor.hpp"
#include "full_cpp_solver_core_internal.hpp"

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

RichLnsDiagnostics improve_rich_lns_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, GroupConstructionResult& result
) {
    if (!result.rich_candidate_complete) return {};
    AssignmentState state(problem); state.restore(result.rich_state);
    const RichLnsWorkspace workspace(state, deadline);
    const auto diagnostics = improve_rich_lns(problem, state, result.rich_rankings, deadline,
        [&](int g, const RichLnsOption& option) {
            RichElitePattern pattern; pattern.local_score = option.score; pattern.source = "lns_generated";
            for (size_t i = 0; i < option.assignment.size(); ++i)
                pattern.assignments.emplace_back(problem.passengers[workspace.keys_by_group[g][i]].hostnum, problem.seats[option.assignment[i]].id);
            result.rich_elite_store.record_candidate(problem.groups[g].id, std::move(pattern), state, result.rich_conflict_diversity_active);
        });
    result.rich_elite_store.capture(state, "lns_final");
    result.rich_state = state.save();
    const double score = evaluate_soft_score(problem, state.passenger_to_seat);
    if (validate_complete_assignment(problem, state.passenger_to_seat) == 0 && score > result.group_construction_score + 1e-9) {
        result.passenger_to_seat = state.passenger_to_seat;
        result.group_construction_score = score;
        result.selected_components = evaluate_score_components(problem, state.passenger_to_seat);
        result.selected_incumbent = "rich-m5-lns";
        result.score_delta = score - result.q0_score;
    }
    return diagnostics;
}

RichLnsDiagnostics improve_rich_lns(const Problem& problem, AssignmentState& state,
    const std::vector<std::vector<int>>& rankings, std::chrono::steady_clock::time_point deadline,
    const std::function<void(int, const RichLnsOption&)>& recorder
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - started).count(); };
    const auto& config = problem.rich;
    RichLnsDiagnostics d; d.enabled = config.conflict_component_lns_enabled;
    if (!d.enabled) { d.seconds = elapsed(); return d; }
    if (started >= deadline) { d.stopped_by_deadline = true; return d; }
    RichLnsWorkspace workspace(state, deadline);
    const auto& keys = workspace.keys_by_group;
    const auto& eligible = workspace.eligible_groups;
    if (eligible.size() < 2) { d.seconds = elapsed(); return d; }
    d.initialized = true;
    d.minimum_size = config.lns_min_component_size;
    d.configured_maximum = config.lns_max_component_size;
    d.maximum_size = std::min(static_cast<int>(eligible.size()), std::max(d.minimum_size,
        d.configured_maximum > 0 ? d.configured_maximum : static_cast<int>(eligible.size())));
    d.initial_size = std::min(d.maximum_size, std::max(d.minimum_size, config.lns_component_size));
    std::map<int, int> indexes;
    for (size_t g = 0; g < problem.groups.size(); ++g) indexes[problem.groups[g].id] = static_cast<int>(g);
    for (const auto& metric : build_rich_repair_queue(problem, state.passenger_to_seat))
        if (eligible.count(indexes.at(metric.group_id)) && d.repair_queue.size() < 20) d.repair_queue.push_back(metric);
    const auto group_ids = [&](const std::vector<int>& component) {
        std::vector<int> ids; for (int g : component) ids.push_back(problem.groups[g].id); return ids;
    };
    const auto needs_care = [&](int p) { return problem.passengers[p].need_cared || ssr_rule(problem, problem.passengers[p]).requires_caregiver; };
    const double epsilon = config.local_search_epsilon;
    const double initial_score = evaluate_rich_group_score(problem, state.passenger_to_seat, -1).total();
    double current_score = initial_score, best_score = initial_score;
    auto best_context = state.save();
    auto owners = state.seat_to_passenger;
    std::vector<double> history(config.lns_late_history_length, current_score - config.lns_allowed_drop);
    int history_index = 0, root_cursor = 0, stagnation = 0;
    while (Clock::now() < deadline && d.search_nodes < config.lns_mip_solve_limit) {
        std::map<int, std::vector<int>> group_seats;
        for (int g : eligible) for (int p : keys[g]) group_seats[g].push_back(state.passenger_to_seat[p]);
        std::vector<int> roots;
        std::map<int, double> priority_loss;
        for (const auto& metric : build_rich_repair_queue(problem, state.passenger_to_seat)) {
            const int g = indexes.at(metric.group_id); priority_loss[g] = metric.priority_loss;
            if (eligible.count(g)) roots.push_back(g);
        }
        std::rotate(roots.begin(), roots.begin() + root_cursor % roots.size(), roots.end());
        bool accepted = false;
        std::set<std::vector<int>> seen, ejection_signatures;
        std::map<int, std::vector<int>> candidate_cache;
        const int dynamic_cap = config.lns_related_seat_cap + stagnation * config.lns_dynamic_pool_growth;
        const auto candidates_for = [&](int p) -> const std::vector<int>& {
            auto found = candidate_cache.find(p);
            if (found != candidate_cache.end()) return found->second;
            auto candidates = rankings[p];
            candidates.resize(std::min(candidates.size(), static_cast<size_t>(stagnation ? dynamic_cap : config.lns_related_seat_cap)));
            if (stagnation) {
                std::vector<int> others;
                for (int s : group_seats.at(problem.passengers[p].group)) if (s != state.passenger_to_seat[p]) others.push_back(s);
                std::map<int, double> score;
                for (int s : candidates) {
                    auto seats = others; seats.push_back(s);
                    score[s] = workspace.passenger_score(p, s) + workspace.compact_score(seats);
                }
                ++d.dynamic_reorders;
                std::sort(candidates.begin(), candidates.end(), [&](int a, int b) {
                    return std::make_pair(-score.at(a), problem.seats[a].id) < std::make_pair(-score.at(b), problem.seats[b].id);
                });
            }
            return candidate_cache.emplace(p, std::move(candidates)).first->second;
        };
        for (size_t root_index = 0; root_index < std::min(roots.size(), static_cast<size_t>(config.lns_root_limit)); ++root_index) {
            const int root = roots[root_index];
            std::map<int, int> conflicts;
            for (int p : keys[root]) for (int s : candidates_for(p)) {
                const int owner = owners[s];
                if (owner >= 0 && eligible.count(problem.passengers[owner].group) && problem.passengers[owner].group != root)
                    ++conflicts[problem.passengers[owner].group];
            }
            std::vector<int> related;
            for (const auto& entry : conflicts) related.push_back(entry.first);
            std::sort(related.begin(), related.end(), [&](int a, int b) {
                return std::make_tuple(-conflicts.at(a), priority_loss.at(a), problem.groups[a].id)
                    < std::make_tuple(-conflicts.at(b), priority_loss.at(b), problem.groups[b].id);
            });
            related.resize(std::min(related.size(), static_cast<size_t>(config.lns_related_group_cap)));
            std::vector<int> chain{root};
            while (chain.size() < static_cast<size_t>(d.maximum_size)) {
                std::map<int, double> pressure;
                for (int p : keys[chain.back()]) {
                    const double current = workspace.passenger_score(p, state.passenger_to_seat[p]);
                    const auto& candidates = candidates_for(p);
                    for (size_t rank = 0; rank < std::min(candidates.size(), static_cast<size_t>(config.lns_related_seat_cap)); ++rank) {
                        const int s = candidates[rank], owner = owners[s];
                        if (owner < 0) continue;
                        const int g = problem.passengers[owner].group;
                        if (!eligible.count(g) || std::find(chain.begin(), chain.end(), g) != chain.end()) continue;
                        pressure[g] += std::max(0.0, workspace.passenger_score(p, s) - current) + 1.0 / (rank + 1);
                    }
                }
                if (pressure.empty()) break;
                const auto next = std::max_element(pressure.begin(), pressure.end(), [&](const auto& a, const auto& b) {
                    return std::make_pair(a.second, -problem.groups[a.first].id) < std::make_pair(b.second, -problem.groups[b.first].id);
                });
                chain.push_back(next->first);
            }
            const bool chain_valid = chain.size() >= static_cast<size_t>(d.minimum_size);
            if (chain_valid) { ++d.ejection_chains_generated; auto signature = chain; std::sort(signature.begin(), signature.end()); ejection_signatures.insert(signature); }
            std::vector<std::vector<int>> seeds;
            const std::vector<int> chain_tail(chain.begin() + 1, chain.end());
            if (!stagnation && chain_valid) seeds.push_back(chain_tail);
            if (stagnation) for (int g : related) seeds.push_back({g});
            for (size_t a = 0; a < related.size(); ++a) for (size_t b = a + 1; b < related.size(); ++b) seeds.push_back({related[a], related[b]});
            std::vector<int> sizes{d.initial_size};
            if (stagnation) {
                sizes.clear(); for (int n = d.minimum_size; n <= d.maximum_size; ++n) sizes.push_back(n);
                if (sizes.empty()) throw std::runtime_error("LNS component size range is empty");
                std::rotate(sizes.begin(), sizes.begin() + stagnation % sizes.size(), sizes.end());
            }
            for (size_t seed_index = 0; seed_index < seeds.size(); ++seed_index) {
                if (Clock::now() >= deadline || d.search_nodes >= config.lns_mip_solve_limit) break;
                std::vector<int> component{root}; component.insert(component.end(), seeds[seed_index].begin(), seeds[seed_index].end());
                for (int g : related) if (std::find(component.begin(), component.end(), g) == component.end()) component.push_back(g);
                component.resize(std::min(component.size(), static_cast<size_t>(sizes[seed_index % sizes.size()])));
                if (component.size() < static_cast<size_t>(d.minimum_size)) continue;
                auto signature = component; std::sort(signature.begin(), signature.end());
                if (chain_valid && seeds[seed_index] == chain_tail) ejection_signatures.insert(signature);
                if (!seen.insert(signature).second) continue;
                std::vector<int> component_keys;
                for (int g : component) component_keys.insert(component_keys.end(), keys[g].begin(), keys[g].end());
                if (component_keys.size() > static_cast<size_t>(config.lns_passenger_limit)) continue;
                ++d.components_tested; d.max_tested_component_size = std::max(d.max_tested_component_size, static_cast<int>(component.size()));
                const auto ids = group_ids(component);
                if (d.tested_group_components.size() < 20 && std::find(d.tested_group_components.begin(), d.tested_group_components.end(), ids) == d.tested_group_components.end())
                    d.tested_group_components.push_back(ids);
                std::vector<int> pool;
                for (int p : component_keys) pool.push_back(state.passenger_to_seat[p]);
                std::map<int, int> frequency;
                for (int p : component_keys) for (int s : candidates_for(p))
                    if (state.seat_to_passenger[s] < 0 && state.blocked_count[s] == 0) ++frequency[s];
                std::vector<int> free;
                for (const auto& item : frequency) free.push_back(item.first);
                std::sort(free.begin(), free.end(), [&](int a, int b) { return std::make_pair(-frequency.at(a), problem.seats[a].id) < std::make_pair(-frequency.at(b), problem.seats[b].id); });
                free.resize(std::min(free.size(), static_cast<size_t>(config.lns_free_seat_cap)));
                pool.insert(pool.end(), free.begin(), free.end());
                std::map<int, std::vector<RichLnsOption>> options;
                for (int g : component) {
                    if (Clock::now() >= deadline) { d.stopped_by_deadline = true; break; }
                    options[g] = workspace.group_options(g, pool, recorder);
                }
                if (options.size() != component.size()) break;
                if (std::any_of(options.begin(), options.end(), [](const auto& item) { return item.second.empty(); })) continue;
                const auto choice = solve_rich_lns_master(state, workspace, component, options, root, config.lns_mip_time_limit, deadline, &d.solver_errors);
                ++d.search_nodes;
                if (choice.empty()) continue;
                const auto before = state.passenger_to_seat;
                for (int p : component_keys) state.remove(p);
                std::vector<int> rebuilt;
                for (int g : component) {
                    std::vector<std::pair<int, int>> proposed;
                    for (size_t i = 0; i < keys[g].size(); ++i) proposed.emplace_back(keys[g][i], choice.at(g)[i]);
                    std::stable_sort(proposed.begin(), proposed.end(), [&](const auto& a, const auto& b) { return needs_care(a.first) < needs_care(b.first); });
                    for (const auto& item : proposed) {
                        if (state.assign_rich_pattern(item.first, item.second, {})) rebuilt.push_back(item.first);
                        else break;
                    }
                }
                if (rebuilt.size() != component_keys.size()) {
                    for (int p : rebuilt) state.remove(p);
                    for (int p : component_keys) state.assign_rich_pattern(p, before[p], {});
                    continue;
                }
                const int violations = validate_complete_assignment(problem, state.passenger_to_seat);
                const std::set<int> affected(component.begin(), component.end());
                const double rebuilt_score = current_score + (evaluate_rich_groups_score(problem, state.passenger_to_seat, affected).total()
                    - evaluate_rich_groups_score(problem, before, affected).total());
                bool acceptable = rebuilt_score > current_score + epsilon;
                if (config.lns_allowed_drop > epsilon) acceptable |= rebuilt_score >= history[history_index] - epsilon;
                history[history_index] = current_score; history_index = (history_index + 1) % history.size();
                if (violations || !acceptable) {
                    for (int p : component_keys) if (state.passenger_to_seat[p] >= 0) state.remove(p);
                    auto restore = component_keys;
                    std::stable_sort(restore.begin(), restore.end(), [&](int a, int b) { return needs_care(a) < needs_care(b); });
                    for (int p : restore) state.assign_rich_pattern(p, before[p], {});
                    continue;
                }
                owners = state.seat_to_passenger;
                ++d.accepted_rebuilds;
                if (ejection_signatures.count(signature)) ++d.ejection_chains_accepted;
                if (rebuilt_score < current_score - epsilon) ++d.accepted_worsening;
                const double delta = rebuilt_score - current_score;
                current_score = rebuilt_score;
                if (rebuilt_score > best_score + epsilon) { best_score = rebuilt_score; best_context = state.save(); }
                if (d.accepted_components.size() < 20) d.accepted_components.push_back({ids, delta, best_score});
                ++root_cursor; accepted = true; break;
            }
            if (accepted) break;
        }
        if (!accepted) {
            ++stagnation; d.stagnation_rounds = stagnation; root_cursor += config.lns_root_limit + stagnation;
            if (stagnation >= config.lns_stagnation_rounds) break;
        } else stagnation = 0;
    }
    if (current_score < best_score - epsilon) state.restore(std::move(best_context));
    d.options_generated = workspace.options_generated;
    d.score_improvement = best_score - initial_score; d.best_soft_score = best_score;
    d.seconds = elapsed(); d.stopped_by_deadline = Clock::now() >= deadline;
    return d;
}

void write_rich_lns_diagnostics(std::ostream& output, const RichLnsDiagnostics& d) {
    output << "{\"enabled\":" << (d.enabled ? "true" : "false") << ",\"stopped_by_deadline\":" << (d.stopped_by_deadline ? "true" : "false")
        << ",\"components_tested\":" << d.components_tested << ",\"max_tested_component_size\":" << d.max_tested_component_size
        << ",\"options_generated\":" << d.options_generated << ",\"search_nodes\":" << d.search_nodes
        << ",\"solver_errors\":" << d.solver_errors
        << ",\"accepted_rebuilds\":" << d.accepted_rebuilds << ",\"accepted_worsening\":" << d.accepted_worsening
        << ",\"stagnation_rounds\":" << d.stagnation_rounds << ",\"dynamic_reorders\":" << d.dynamic_reorders
        << ",\"ejection_chains_generated\":" << d.ejection_chains_generated << ",\"ejection_chains_accepted\":" << d.ejection_chains_accepted
        << ",\"score_improvement\":" << d.score_improvement << ",\"seconds\":" << d.seconds;
    if (d.initialized) {
        output << ",\"best_soft_score\":" << d.best_soft_score << ",\"adaptive_component_sizes\":{\"minimum\":" << d.minimum_size
            << ",\"initial\":" << d.initial_size << ",\"maximum\":" << d.maximum_size << ",\"configured_maximum\":" << d.configured_maximum << '}'
            << ",\"repair_queue\":"; write_rich_repair_queue(output, d.repair_queue, 20);
    }
    const auto write_groups = [&](const std::vector<int>& groups) {
        output << '['; for (size_t i = 0; i < groups.size(); ++i) { if (i) output << ','; output << groups[i]; } output << ']';
    };
    output << ",\"tested_group_components\":[";
    for (size_t i = 0; i < d.tested_group_components.size(); ++i) { if (i) output << ','; write_groups(d.tested_group_components[i]); }
    output << "],\"accepted_components\":[";
    for (size_t i = 0; i < d.accepted_components.size(); ++i) {
        if (i) output << ',';
        const auto& item = d.accepted_components[i]; output << "{\"groups\":"; write_groups(item.groups);
        output << ",\"delta\":" << item.delta << ",\"best_score\":" << item.best_score << '}';
    }
    output << "]}";
}

std::map<int, std::vector<int>> solve_rich_lns_master(const AssignmentState& state,
    const RichLnsWorkspace& workspace, const std::vector<int>& component,
    const std::map<int, std::vector<RichLnsOption>>& options, int root,
    double local_time_limit, std::chrono::steady_clock::time_point deadline, int* solver_errors
) {
    std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
    void* highs = solver.get();
    const auto check = [](HighsInt status, const char* operation = "model construction") {
        if (status == kHighsStatusError) throw std::runtime_error(std::string("LNS MIP API error: ") + operation);
    };
    check(Highs_setBoolOptionValue(highs, "output_flag", 0));
    check(Highs_setIntOptionValue(highs, "threads", 1));
    check(Highs_setIntOptionValue(highs, "random_seed", 0));
    check(Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0));
    check(Highs_setDoubleOptionValue(highs, "time_limit", std::min(local_time_limit,
        std::max(.01, std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count()))));
    std::map<int, HighsInt> group_rows;
    std::map<std::string, HighsInt> seat_rows;
    HighsInt row = 0;
    for (int g : component) {
        group_rows[g] = row++;
        check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr));
        for (const auto& option : options.at(g)) for (int s : option.seats) seat_rows[state.problem.seats[s].id] = 0;
    }
    for (auto& seat : seat_rows) {
        seat.second = row++;
        check(Highs_addRow(highs, -Highs_getInfinity(highs), 1.0, 0, nullptr, nullptr));
    }
    std::vector<int> root_current;
    for (int p : workspace.keys_by_group[root]) root_current.push_back(state.passenger_to_seat[p]);
    std::vector<std::pair<int, const RichLnsOption*>> columns;
    for (int g : component) for (const auto& option : options.at(g)) {
        if (g == root && option.assignment == root_current) continue;
        std::vector<HighsInt> rows{group_rows.at(g)};
        for (int s : option.seats) rows.push_back(seat_rows.at(state.problem.seats[s].id));
        std::vector<double> values(rows.size(), 1.0);
        check(Highs_addCol(highs, -option.score, 0.0, 1.0, static_cast<HighsInt>(rows.size()), rows.data(), values.data()));
        check(Highs_changeColIntegrality(highs, static_cast<HighsInt>(columns.size()), kHighsVarTypeInteger));
        columns.emplace_back(g, &option);
    }
    if (columns.empty()) return {};
    const auto run_status = Highs_run(highs);
    // Python reads the solution even when run() returns kError. An incomplete
    // choice is discarded below; a complete choice still faces reconstruction
    // and full legality checks in the caller. Do not abort the legal incumbent.
    if (run_status == kHighsStatusError && solver_errors) ++*solver_errors;
    std::vector<double> solution(columns.size());
    check(Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr), "Highs_getSolution");
    std::map<int, std::vector<int>> choices;
    for (size_t i = 0; i < columns.size(); ++i) if (solution[i] > .5) choices[columns[i].first] = columns[i].second->assignment;
    if (choices.size() != component.size()) return {};
    return choices;
}

}  // namespace full_cpp
