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

RichProtectedMipDiagnostics improve_rich_protected_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, GroupConstructionResult& result
) {
    RichProtectedMipDiagnostics combined;
    if (!result.rich_candidate_complete) return combined;
    AssignmentState state(problem); state.restore(result.rich_state);
    for (int i = 0; i < problem.rich.protected_multigroup_max_passes; ++i) {
        const auto pass = improve_rich_protected_mip(problem, state, result.rich_elite_store, deadline);
        result.rich_elite_store.capture(state, "protected_multigroup_mip");
        if (i == 0) combined = pass;
        else {
            combined.roots_considered += pass.roots_considered;
            combined.components_tested += pass.components_tested;
            combined.mip_columns += pass.mip_columns;
            combined.dynamic_relocation_calls += pass.dynamic_relocation_calls;
            combined.dynamic_relocation_patterns += pass.dynamic_relocation_patterns;
            combined.accepted += pass.accepted;
            combined.score_improvement += pass.score_improvement;
            combined.seconds += pass.seconds;
            combined.tested_components.insert(combined.tested_components.end(), pass.tested_components.begin(), pass.tested_components.end());
            combined.accepted_components.insert(combined.accepted_components.end(), pass.accepted_components.begin(), pass.accepted_components.end());
            combined.stopped_by_deadline |= pass.stopped_by_deadline;
            // Frozen Python retains first-pass root metadata and conditional row count.
        }
        combined.passes = i + 1;
        if (!pass.enabled || !pass.accepted || pass.score_improvement < problem.rich.protected_multigroup_min_pass_gain
            || std::chrono::steady_clock::now() >= deadline) break;
    }
    result.rich_state = state.save();
    const double score = evaluate_soft_score(problem, state.passenger_to_seat);
    if (validate_complete_assignment(problem, state.passenger_to_seat) == 0 && score > result.group_construction_score + 1e-9) {
        result.passenger_to_seat = state.passenger_to_seat;
        result.group_construction_score = score;
        result.selected_components = evaluate_score_components(problem, state.passenger_to_seat);
        result.selected_incumbent = "rich-m4-protected-mip";
        result.score_delta = score - result.q0_score;
    }
    return combined;
}

RichProtectedMipDiagnostics improve_rich_protected_mip(const Problem& problem,
    AssignmentState& state, RichEliteStore& elite, std::chrono::steady_clock::time_point deadline
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - started).count(); };
    RichProtectedMipDiagnostics d;
    d.protected_roots_enabled = problem.rich.protected_multigroup_enabled;
    d.priority_roots_enabled = problem.rich.priority_multigroup_enabled;
    d.enabled = d.protected_roots_enabled || d.priority_roots_enabled;
    if (!d.enabled || started >= deadline) {
        d.stopped_by_deadline = started >= deadline; d.seconds = elapsed(); return d;
    }
    std::set<int> protected_groups, priority_groups;
    std::map<int, int> group_indexes;
    for (size_t g = 0; g < problem.groups.size(); ++g) {
        const auto& group = problem.groups[g];
        group_indexes[group.id] = static_cast<int>(g);
        for (int p : group.passengers)
            if (problem.passengers[p].need_single_empty || problem.passengers[p].need_both_empty) protected_groups.insert(group.id);
    }
    const auto queue = build_rich_repair_queue(problem, state.passenger_to_seat);
    std::map<int, double> priority_loss;
    for (const auto& metric : queue) {
        priority_loss[metric.group_id] = metric.priority_loss;
        if (d.priority_roots_enabled && metric.extreme_dispersion) priority_groups.insert(metric.group_id);
    }
    d.roots_initialized = true;
    d.protected_root_count = static_cast<int>(protected_groups.size());
    d.priority_root_count = static_cast<int>(priority_groups.size());
    std::vector<int> roots;
    for (const auto& metric : queue)
        if ((d.protected_roots_enabled && protected_groups.count(metric.group_id)) || priority_groups.count(metric.group_id)) roots.push_back(metric.group_id);
    if (roots.empty()) { d.reason = "no_protected_or_priority_groups"; d.seconds = elapsed(); return d; }
    if (roots.size() > static_cast<size_t>(problem.rich.protected_multigroup_root_limit)) roots.resize(problem.rich.protected_multigroup_root_limit);
    const int max_groups = std::min(static_cast<int>(problem.groups.size()), problem.rich.protected_multigroup_max_groups);
    const int option_limit = problem.rich.protected_multigroup_options_per_group;
    const auto fixed = preprocess_fixed_seats(problem);
    const auto baby = build_rich_baby_costs(problem);
    std::map<int, RichPricingCache> caches;
    const auto resources_for = [&](int gid) {
        std::set<std::string> resources;
        for (int p : problem.groups[group_indexes.at(gid)].passengers) {
            if (state.passenger_to_seat[p] >= 0) resources.insert(problem.seats[state.passenger_to_seat[p]].id);
            for (int seat : state.assigned_blocked[p]) resources.insert(problem.seats[seat].id);
        }
        return resources;
    };
    const auto ranked = [&](int gid) {
        std::vector<size_t> indexes;
        const auto found = elite.groups().find(gid);
        if (found == elite.groups().end()) return indexes;
        const auto& patterns = found->second;
        for (size_t i = 0; i < patterns.size(); ++i) indexes.push_back(i);
        std::stable_sort(indexes.begin(), indexes.end(), [&](size_t a, size_t b) { return patterns[a].local_score > patterns[b].local_score; });
        if (indexes.size() > static_cast<size_t>(option_limit)) indexes.resize(option_limit);
        return indexes;
    };
    const double initial_score = evaluate_rich_group_score(problem, state.passenger_to_seat, -1).total();
    double best_score = initial_score;
    std::set<std::vector<int>> tested;
    for (int root : roots) {
        if (Clock::now() >= deadline) break;
        ++d.roots_considered;
        std::map<std::string, int> owners;
        for (const auto& group : problem.groups) for (const auto& resource : resources_for(group.id)) owners[resource] = group.id;
        std::vector<std::vector<int>> components;
        for (size_t index : ranked(root)) {
            std::set<int> conflicts;
            for (const auto& seat : elite.groups().at(root)[index].seat_resources) {
                const auto owner = owners.find(seat);
                if (owner != owners.end() && owner->second != root) conflicts.insert(owner->second);
            }
            std::vector<int> ordered(conflicts.begin(), conflicts.end());
            std::sort(ordered.begin(), ordered.end(), [&](int a, int b) { return std::make_pair(priority_loss.at(a), a) < std::make_pair(priority_loss.at(b), b); });
            if (ordered.size() > static_cast<size_t>(max_groups - 1)) ordered.resize(max_groups - 1);
            ordered.push_back(root); std::sort(ordered.begin(), ordered.end());
            if (ordered.size() >= 2 || problem.rich.tail_exchange_enabled)
                components.push_back(std::move(ordered));
        }
        for (const auto& component : components) {
            if (tested.count(component) || d.components_tested >= problem.rich.protected_multigroup_component_limit || Clock::now() >= deadline) continue;
            tested.insert(component);
            if (std::any_of(component.begin(), component.end(), [&](int gid) {
                const auto found = elite.groups().find(gid); return found == elite.groups().end() || found->second.empty();
            })) continue;
            ++d.components_tested; d.tested_components.push_back(component);
            std::set<std::string> outside;
            std::set<int> outside_indexes;
            for (const auto& group : problem.groups) if (!std::binary_search(component.begin(), component.end(), group.id))
                for (const auto& seat : resources_for(group.id)) { outside.insert(seat); outside_indexes.insert(problem.seat_index.at(seat)); }
            for (int gid : component) if (gid != root || problem.rich.tail_exchange_enabled) {
                const auto dynamic = add_rich_dynamic_relocation_patterns(problem, state, group_indexes.at(gid), outside_indexes, deadline, fixed, baby, caches, elite);
                d.dynamic_relocation_calls += dynamic.calls; d.dynamic_relocation_patterns += dynamic.patterns;
            }
            std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
            void* highs = solver.get();
            const auto check = [](HighsInt status) { if (status == kHighsStatusError) throw std::runtime_error("protected MIP API error"); };
            check(Highs_setBoolOptionValue(highs, "output_flag", 0));
            check(Highs_setIntOptionValue(highs, "threads", 1));
            check(Highs_setIntOptionValue(highs, "random_seed", 0));
            check(Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0));
            check(Highs_setDoubleOptionValue(highs, "time_limit", std::max(.01, std::chrono::duration<double>(deadline - Clock::now()).count())));
            const double infinity = Highs_getInfinity(highs);
            std::map<int, HighsInt> group_rows;
            std::map<std::string, HighsInt> resource_rows;
            HighsInt row = 0;
            for (int gid : component) {
                group_rows[gid] = row++; check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr));
                for (const auto& pattern : elite.groups().at(gid)) for (const auto& seat : pattern.seat_resources)
                    if (!outside.count(seat)) resource_rows[seat] = 0;
            }
            for (auto& resource : resource_rows) {
                resource.second = row++; check(Highs_addRow(highs, -infinity, 1.0, 0, nullptr, nullptr));
            }
            std::vector<std::pair<int, size_t>> columns;
            for (int gid : component) for (size_t index : ranked(gid)) {
                const auto& pattern = elite.groups().at(gid)[index];
                if (pattern.seat_resources.empty() || std::any_of(pattern.seat_resources.begin(), pattern.seat_resources.end(),
                    [&](const std::string& seat) { return outside.count(seat) != 0; })) continue;
                std::vector<HighsInt> rows{group_rows.at(gid)};
                for (const auto& seat : pattern.seat_resources) rows.push_back(resource_rows.at(seat));
                std::vector<double> values(rows.size(), 1.0);
                check(Highs_addCol(highs, -pattern.local_score, 0.0, 1.0, static_cast<HighsInt>(rows.size()), rows.data(), values.data()));
                check(Highs_changeColIntegrality(highs, static_cast<HighsInt>(columns.size()), kHighsVarTypeInteger));
                columns.emplace_back(gid, index);
            }
            d.mip_columns += static_cast<int>(columns.size());
            if (columns.empty()) continue;
            for (size_t left = 0; left < columns.size(); ++left) for (size_t right = left + 1; right < columns.size(); ++right) {
                const auto& a = columns[left]; const auto& b = columns[right];
                if (a.first == b.first || !rich_patterns_have_conditional_ssr_conflict(problem,
                    a.first, elite.groups().at(a.first)[a.second], b.first, elite.groups().at(b.first)[b.second])) continue;
                const HighsInt indexes[] = {static_cast<HighsInt>(left), static_cast<HighsInt>(right)};
                const double values[] = {1.0, 1.0};
                check(Highs_addRow(highs, -infinity, 1.0, 2, indexes, values)); ++d.conditional_ssr_rows;
            }
            Highs_run(highs);
            std::vector<double> solution(columns.size());
            Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr);
            std::map<int, RichElitePattern> choices;
            for (size_t i = 0; i < columns.size(); ++i) if (solution[i] > .5)
                choices[columns[i].first] = elite.groups().at(columns[i].first)[columns[i].second];
            if (choices.size() != component.size()) continue;
            AssignmentSnapshot candidate;
            if (!rebuild_rich_pattern_component(state, choices, candidate)) continue;
            const int violations = validate_complete_assignment(problem, candidate.passenger_to_seat);
            const double score = evaluate_rich_group_score(problem, candidate.passenger_to_seat, -1).total();
            if (violations || score <= best_score + problem.rich.local_search_epsilon) continue;
            const double delta = score - best_score;
            state.restore(std::move(candidate)); best_score = score;
            ++d.accepted;
            d.accepted_components.push_back({component, delta, elapsed(), d.components_tested});
        }
    }
    d.score_improvement = best_score - initial_score;
    d.seconds = elapsed(); d.stopped_by_deadline = Clock::now() >= deadline;
    return d;
}

void write_rich_protected_mip_diagnostics(std::ostream& output, const RichProtectedMipDiagnostics& d) {
    output << "{\"enabled\":" << (d.enabled ? "true" : "false")
        << ",\"protected_roots_enabled\":" << (d.protected_roots_enabled ? "true" : "false")
        << ",\"priority_roots_enabled\":" << (d.priority_roots_enabled ? "true" : "false")
        << ",\"roots_considered\":" << d.roots_considered << ",\"components_tested\":" << d.components_tested
        << ",\"mip_columns\":" << d.mip_columns << ",\"dynamic_relocation_calls\":" << d.dynamic_relocation_calls
        << ",\"dynamic_relocation_patterns\":" << d.dynamic_relocation_patterns << ",\"conditional_ssr_rows\":" << d.conditional_ssr_rows
        << ",\"accepted\":" << d.accepted << ",\"score_improvement\":" << d.score_improvement << ",\"seconds\":" << d.seconds
        << ",\"stopped_by_deadline\":" << (d.stopped_by_deadline ? "true" : "false");
    if (d.passes) output << ",\"passes\":" << d.passes;
    if (d.roots_initialized) output << ",\"protected_root_count\":" << d.protected_root_count << ",\"priority_root_count\":" << d.priority_root_count;
    if (!d.reason.empty()) output << ",\"reason\":\"" << d.reason << '"';
    const auto groups = [&](const std::vector<int>& values) {
        output << '['; for (size_t i = 0; i < values.size(); ++i) { if (i) output << ','; output << values[i]; } output << ']';
    };
    output << ",\"tested_components\":[";
    for (size_t i = 0; i < d.tested_components.size(); ++i) { if (i) output << ','; groups(d.tested_components[i]); }
    output << "],\"accepted_components\":[";
    for (size_t i = 0; i < d.accepted_components.size(); ++i) {
        if (i) output << ',';
        const auto& item = d.accepted_components[i];
        output << "{\"groups\":"; groups(item.groups);
        output << ",\"delta\":" << item.delta << ",\"test_index\":" << item.test_index << ",\"elapsed_seconds\":" << item.elapsed_seconds << '}';
    }
    output << "]}";
}

}  // namespace full_cpp
