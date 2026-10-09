#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {

std::vector<RichTieredPattern> generate_rich_rigid_relaxed_patterns(
    const Problem& problem, int group_index, const std::vector<int>& current_targets,
    const std::vector<std::vector<RichPlacement>>& options, const std::vector<std::string>& active_ssr_types
) {
    std::vector<RichTieredPattern> result;
    if (!problem.rich_three_tier_active || std::find(current_targets.begin(), current_targets.end(), -1) != current_targets.end()) return result;
    std::map<std::pair<int, std::string>, int> seat_at;
    std::map<int, std::vector<int>> rows;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) {
        const auto& seat = problem.seats[s];
        seat_at[{seat.row, seat.column}] = s;
        rows[seat.row].push_back(s);
    }
    for (auto& row : rows) std::sort(row.second.begin(), row.second.end(), [&](int a, int b) {
        return problem.seats[a].index_in_row < problem.seats[b].index_in_row;
    });
    using Signature = std::vector<std::pair<int, std::vector<int>>>;
    std::map<Signature, size_t> positions;
    const auto add_targets = [&](const std::vector<int>& targets, const char* source) {
        std::vector<RichPlacement> selected;
        std::vector<bool> used(problem.seats.size(), false);
        const auto choose = [&](auto&& self, size_t p) -> bool {
            if (p == options.size()) return rich_placements_caregiver_ok(problem, group_index, selected);
            for (const auto& option : options[p]) {
                if (option.seat != targets[p]) continue;
                if (std::any_of(option.resources.begin(), option.resources.end(), [&](int s) { return used[s]; })) continue;
                selected.push_back(option);
                for (int s : option.resources) used[s] = true;
                if (self(self, p + 1)) return true;
                selected.pop_back();
                for (int s : option.resources) used[s] = false;
            }
            return false;
        };
        if (!choose(choose, 0)) return;
        auto pattern = build_rich_exact_pattern(problem, group_index, selected, active_ssr_types);
        Signature signature;
        for (const auto& placement : pattern.placements) signature.emplace_back(placement.seat, placement.blocked);
        const auto old = positions.find(signature);
        if (old == positions.end()) {
            positions.emplace(std::move(signature), result.size());
            result.push_back({std::move(pattern), source});
        } else {
            // Python dict assignment replaces value/source but keeps insertion order.
            result[old->second] = {std::move(pattern), source};
        }
    };
    for (int delta = -problem.rich.structured_rigid_shift_rows; delta <= problem.rich.structured_rigid_shift_rows; ++delta) {
        for (bool mirrored : {false, true}) {
            std::vector<int> targets;
            for (int s : current_targets) {
                const auto& seat = problem.seats[s];
                const int row = seat.row + delta;
                std::string column = seat.column;
                const auto row_it = rows.find(row);
                if (mirrored && row_it != rows.end() && seat.index_in_row < static_cast<int>(row_it->second.size()))
                    column = problem.seats[row_it->second[row_it->second.size() - 1 - seat.index_in_row]].column;
                const auto target = seat_at.find({row, column});
                if (target == seat_at.end()) { targets.clear(); break; }
                targets.push_back(target->second);
            }
            if (!targets.empty()) add_targets(targets, "rigid");
        }
    }
    std::map<std::string, std::pair<int, int>> subrows;
    for (int s : current_targets) {
        const auto& seat = problem.seats[s];
        subrows["(" + std::to_string(seat.row) + ", " + std::to_string(seat.subrow) + ")"] = {seat.row, seat.subrow};
    }
    for (const auto& subrow : subrows) for (int delta : {-1, 1}) {
        auto targets = current_targets;
        bool valid = true;
        for (size_t p = 0; p < current_targets.size(); ++p) {
            const auto& seat = problem.seats[current_targets[p]];
            if (std::make_pair(seat.row, seat.subrow) != subrow.second) continue;
            const auto target = seat_at.find({seat.row + delta, seat.column});
            if (target == seat_at.end()) { valid = false; break; }
            targets[p] = target->second;
        }
        if (valid) add_targets(targets, "relaxed");
    }
    return result;
}

RichStructuredWindows build_rich_structured_windows(const Problem& problem, int group_index,
    const std::vector<std::vector<RichPlacement>>& options, const RichGroupRepairMetric& current_metric
) {
    RichStructuredWindows result;
    const auto& group = problem.groups[group_index];
    std::map<int, std::set<int>> reachable;
    for (const auto& row : options) for (const auto& option : row) reachable[problem.seats[option.seat].row].insert(option.seat);
    std::vector<int> rows;
    int capacity = 1, protected_demand = 0;
    for (const auto& row : reachable) { rows.push_back(row.first); capacity = std::max(capacity, static_cast<int>(row.second.size())); }
    std::set<int> fixed_rows;
    std::map<std::string, int> required_classes;
    std::vector<std::pair<std::string, double>> desired_values;
    double old_sum = 0.0;
    int old_count = 0;
    const auto value = [&](const Seat& seat) {
        return std::isnan(seat.explicit_value)
            ? (seat.cabin == "Business" ? problem.business_seat_value : 0.0)
                + (seat.extra_legroom ? problem.extra_legroom_value : 0.0) + (seat.bassinet ? problem.bassinet_value : 0.0)
            : seat.explicit_value;
    };
    for (int p : group.passengers) {
        const auto& passenger = problem.passengers[p];
        protected_demand += passenger.need_both_empty ? 2 : passenger.need_single_empty ? 1 : 0;
        const auto fixed = problem.seat_index.find(passenger.fixed_seat);
        if (fixed != problem.seat_index.end()) fixed_rows.insert(problem.seats[fixed->second].row);
        if (!passenger.cabin.empty()) ++required_classes[passenger.cabin];
        const auto old = problem.old_seat_index.find(passenger.old_seat);
        if (old != problem.old_seat_index.end()) {
            const auto& seat = problem.old_seats[old->second];
            old_sum += seat.row; ++old_count;
            desired_values.emplace_back(passenger.cabin, std::isnan(passenger.old_seat_value) ? value(seat) : passenger.old_seat_value);
        }
    }
    result.minimum_width = std::max(1, (static_cast<int>(group.passengers.size()) + protected_demand + capacity - 1) / capacity);
    result.old_center = rows.empty() ? 0.0 : old_count ? old_sum / old_count : rows.front();
    const int max_width = std::min(static_cast<int>(rows.size()), result.minimum_width + problem.rich.structured_pattern_extra_rows);
    for (int width = result.minimum_width; width <= max_width; ++width)
        for (int start = 0; start + width <= static_cast<int>(rows.size()); ++start) {
            std::vector<int> window(rows.begin() + start, rows.begin() + start + width);
            if (!std::includes(window.begin(), window.end(), fixed_rows.begin(), fixed_rows.end())) continue;
            const bool covered = std::all_of(options.begin(), options.end(), [&](const auto& domain) {
                return std::any_of(domain.begin(), domain.end(), [&](const auto& option) {
                    return std::binary_search(window.begin(), window.end(), problem.seats[option.seat].row);
                });
            });
            if (covered) result.all_row_windows.push_back(std::move(window));
        }
    const auto key = [&](const std::vector<int>& window) {
        std::map<std::string, int> available;
        std::map<std::string, std::vector<double>> values;
        for (const auto& seat : problem.seats) if (std::binary_search(window.begin(), window.end(), seat.row)) {
            ++available[seat.cabin]; values[seat.cabin].push_back(value(seat));
        }
        int shortage = 0;
        for (const auto& demand : required_classes) shortage += std::max(0, demand.second - available[demand.first]);
        double mismatch = 0.0, center = 0.0;
        if (current_metric.value_mismatch_score < 0.0) for (const auto& desired : desired_values) {
            double best = std::numeric_limits<double>::infinity();
            for (double candidate : values[desired.first]) best = std::min(best, std::abs(candidate - desired.second));
            mismatch += best;
        }
        for (int row : window) center += row;
        center /= window.size();
        const int span = window.back() - window.front();
        return std::make_tuple(shortage, mismatch, span <= std::max(0, current_metric.row_span - 2) ? 0 : 1,
            span, std::abs(center - result.old_center), window.size(), window);
    };
    // Window priorities are immutable: compute each seat/value scan only once.
    using WindowKey = decltype(key(std::vector<int>{}));
    std::vector<WindowKey> window_keys;
    for (const auto& window : result.all_row_windows) window_keys.push_back(key(window));
    std::stable_sort(window_keys.begin(), window_keys.end());
    for (size_t i = 0; i < window_keys.size(); ++i)
        result.all_row_windows[i] = std::get<6>(window_keys[i]);
    result.row_windows = result.all_row_windows;
    if (result.row_windows.size() > static_cast<size_t>(problem.rich.structured_pattern_window_limit))
        result.row_windows.resize(problem.rich.structured_pattern_window_limit);
    return result;
}

void generate_rich_value_block_patterns(const Problem& problem, int group_index,
    const std::vector<std::vector<RichPlacement>>& options, const RichGroupRepairMetric& current_metric,
    const RichStructuredWindows& windows, const std::vector<std::string>& active_ssr_types,
    std::chrono::steady_clock::time_point deadline, std::vector<RichTieredPattern>& patterns
) {
    const auto& group = problem.groups[group_index];
    const bool global = problem.rich_full_resource_global_blocks;
    if (current_metric.value_mismatch_score >= 0.0 && !global) return;
    for (int p : group.passengers) {
        const auto& item = problem.passengers[p];
        if (!item.ssr.empty() || item.need_cared || item.has_new_seat || item.need_single_empty || item.need_both_empty) return;
    }
    const auto& block_windows = global ? windows.all_row_windows : windows.row_windows;
    const size_t window_count = global ? block_windows.size() : std::min(size_t(4), block_windows.size());
    const auto seat_less = [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; };
    std::set<std::vector<int>> seen;
    const size_t size = group.passengers.size();
    const auto popcount = [](size_t mask) { size_t count = 0; while (mask) { mask &= mask - 1; ++count; } return count; };
    for (size_t w = 0; w < window_count; ++w) {
        if (std::chrono::steady_clock::now() >= deadline) break;
        const auto& window = block_windows[w];
        std::set<int> pool_set;
        for (const auto& row : options) for (const auto& option : row)
            if (std::binary_search(window.begin(), window.end(), problem.seats[option.seat].row)) pool_set.insert(option.seat);
        std::vector<int> pool(pool_set.begin(), pool_set.end());
        std::sort(pool.begin(), pool.end(), seat_less);
        if (pool.size() < size || pool.empty()) continue;
        const auto anchors = global ? std::vector<int>{pool[0], pool[pool.size()/4], pool[pool.size()/2], pool[3*pool.size()/4], pool.back()} : pool;
        std::set<int> used_anchors;
        for (int anchor : anchors) {
            if (!used_anchors.insert(anchor).second) continue;
            if (std::chrono::steady_clock::now() >= deadline) break;
            auto block = pool;
            const auto key = [&](int s) {
                const auto& seat = problem.seats[s]; const auto& origin = problem.seats[anchor];
                return std::make_tuple(std::abs(seat.x-origin.x)+std::abs(seat.y-origin.y),
                    std::abs(seat.y-origin.y), seat.x, seat.id);
            };
            std::stable_sort(block.begin(), block.end(), [&](int a, int b) { return key(a) < key(b); });
            block.resize(size);
            std::sort(block.begin(), block.end(), seat_less);
            if (!seen.insert(block).second) continue;
            // The frozen algorithm uses exact subset matching without an internal
            // clock check or a group-size cap; retain those search semantics.
            if (size >= std::numeric_limits<size_t>::digits) throw std::runtime_error("value block exceeds native subset index width");
            const size_t count = size_t(1) << size;
            std::vector<double> dp(count, -std::numeric_limits<double>::infinity());
            std::vector<int> parent(count, -1);
            std::vector<std::vector<const RichPlacement*>> choices(size, std::vector<const RichPlacement*>(size, nullptr));
            for (size_t p = 0; p < size; ++p) for (size_t s = 0; s < size; ++s)
                for (const auto& option : options[p]) if (option.seat == block[s]) { choices[p][s] = &option; break; }
            dp[0] = 0.0;
            for (size_t mask = 0; mask < count; ++mask) {
                const size_t p = popcount(mask);
                if (p >= size || dp[mask] == -std::numeric_limits<double>::infinity()) continue;
                for (size_t s = 0; s < size; ++s) {
                    const size_t bit = size_t(1) << s;
                    if ((mask & bit) || !choices[p][s]) continue;
                    const double score = dp[mask] - choices[p][s]->individual_cost;
                    if (score > dp[mask | bit]) { dp[mask | bit] = score; parent[mask | bit] = static_cast<int>(s); }
                }
            }
            if (parent.back() < 0) continue;
            std::vector<RichPlacement> selected(size);
            for (size_t mask = count-1; mask;) {
                const int s = parent[mask]; const size_t previous = mask ^ (size_t(1) << s);
                const size_t p = popcount(previous); selected[p] = *choices[p][s]; mask = previous;
            }
            auto pattern = build_rich_exact_pattern(problem, group_index, selected, active_ssr_types);
            auto existing = std::find_if(patterns.begin(), patterns.end(), [&](const auto& old) {
                return old.pattern.assignments == pattern.assignments && old.pattern.blocked_by == pattern.blocked_by;
            });
            RichTieredPattern item{std::move(pattern), global ? "global_value_block" : "value_block"};
            if (existing == patterns.end()) patterns.push_back(std::move(item)); else *existing = std::move(item);
        }
    }
}

RichStructuredDiagnostics generate_rich_structured_patterns(const Problem& problem,
    const std::vector<int>& assignment, std::chrono::steady_clock::time_point deadline,
    const std::function<void(int, const RichTieredPattern&, double, bool)>& recorder
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    RichStructuredDiagnostics diagnostics;
    diagnostics.enabled = problem.rich.enable_structured_pattern_generation;
    if (!diagnostics.enabled || started >= deadline) {
        diagnostics.stopped_by_deadline = started >= deadline;
        diagnostics.seconds = std::chrono::duration<double>(Clock::now() - started).count(); return diagnostics;
    }
    auto pricing = problem.rich_pricing_config;
    pricing.type = Value::Type::Object;
    const auto set_number = [&](const std::string& key, double number) {
        auto& value = pricing.object[key]; value.type = Value::Type::Number; value.number = number;
    };
    const bool spare_capacity = problem.rich_stage_budgets.business_time_limit <= 5.0
        && problem.rich_stage_budgets.seat_demand < 0.6 * static_cast<double>(problem.seats.size());
    const double per_group_limit = problem.rich.structured_pattern_dfs_per_group * (spare_capacity ? 2.0 : 1.0);
    set_number("dfs_discovery_time_limit", per_group_limit);
    set_number("quick_pricing_columns_per_group", problem.rich.structured_patterns_per_group);
    std::set<std::string> active;
    for (const auto& passenger : problem.passengers) if (!passenger.ssr.empty()) active.insert(passenger.ssr);
    const std::vector<std::string> active_types(active.begin(), active.end());
    const auto fixed = preprocess_fixed_seats(problem);
    const auto baby = build_rich_baby_costs(problem);
    const auto order = build_rich_structured_order(problem, assignment);
    diagnostics.repair_queue = order.repair_queue;
    if (diagnostics.repair_queue.size() > 20) diagnostics.repair_queue.resize(20);
    const std::set<int> difficult(order.difficult_groups.begin(), order.difficult_groups.end());
    std::map<int, RichGroupRepairMetric> metrics;
    for (const auto& metric : order.repair_queue) metrics[metric.group_id] = metric;
    const auto after = [](Clock::time_point origin, double seconds) { return origin + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)); };
    using Key = std::pair<std::vector<RichPlacementSignature>, std::vector<std::pair<int, int>>>;
    const auto key = [](const RichExactPattern& pattern) {
        std::vector<RichPlacementSignature> signatures;
        for (const auto& o : pattern.placements) signatures.emplace_back(o.passenger_index, o.seat, o.blocked);
        return Key{signatures, pattern.blocked_by};
    };
    struct GroupSearch {
        std::vector<RichTieredPattern> patterns;
        long long nodes = 0;
        int windows = 0;
    };
    const auto search_group = [&](int g, double group_budget) {
        GroupSearch result;
        auto local_pricing = pricing;
        const auto& group = problem.groups[g]; const auto& metric = metrics.at(group.id);
        const auto cache = build_rich_pricing_cache(problem, g, fixed);
        std::vector<int> targets;
        for (int p : group.passengers) targets.push_back(assignment[p]);
        auto patterns = generate_rich_rigid_relaxed_patterns(problem, g, targets, cache.all_options, active_types);
        const auto group_deadline = std::min(deadline, after(Clock::now(), group_budget));
        const auto windows = build_rich_structured_windows(problem, g, cache.all_options, metric);
        generate_rich_value_block_patterns(problem, g, cache.all_options, metric, windows, active_types, group_deadline, patterns);
        std::map<Key, size_t> position;
        for (size_t i = 0; i < patterns.size(); ++i) position[key(patterns[i].pattern)] = i;
        const double per_window_limit = std::max(0.008, per_group_limit / static_cast<double>(std::max(size_t{1}, windows.row_windows.size())));
        for (const auto& rows : windows.row_windows) {
            if (Clock::now() >= group_deadline) break;
            local_pricing.object["dfs_discovery_time_limit"].number = per_window_limit;
            auto window_cache = filter_rich_pricing_window(problem, cache, rows);
            if (std::any_of(window_cache.all_options.begin(), window_cache.all_options.end(), [](const auto& options) { return options.empty(); })) continue;
            ++result.windows;
            RichPricingDuals duals; duals.group[group.id] = 1.0e12;
            const auto priced = price_rich_group_dfs(problem, g, local_pricing, duals, baby, {}, {},
                std::min(group_deadline, after(Clock::now(), per_window_limit)), window_cache, false, false, false, active_types);
            result.nodes += priced.nodes;
            for (const auto& pattern : priced.patterns) {
                const auto identity = key(pattern); const auto found = position.find(identity);
                if (found == position.end()) { position[identity] = patterns.size(); patterns.push_back({pattern, "rebuilt"}); }
                else patterns[found->second] = {pattern, "rebuilt"};
            }
        }
        result.patterns = std::move(patterns);
        return result;
    };
    std::vector<int> pending;
    for (int g : order.ordered_groups) if (difficult.count(g)) pending.push_back(g);
    const size_t workers = problem.rich_stage_budgets.business_time_limit <= 5.0 ? 5 : 1;
    for (size_t begin = 0; begin < pending.size(); begin += workers) {
        if (Clock::now() >= deadline) { diagnostics.stopped_by_deadline = true; break; }
        const size_t end = std::min(pending.size(), begin + workers);
        const double share = std::chrono::duration<double>(deadline - Clock::now()).count()
            * static_cast<double>(workers) / static_cast<double>(pending.size() - begin);
        const double group_budget = workers > 1
            ? std::min(per_group_limit, std::max(0.008, share * 1.4))
            : (order.full_resource_global_blocks ? std::max(per_group_limit, 0.16) : per_group_limit);
        std::vector<std::future<GroupSearch>> futures;
        for (size_t i = begin; i < end; ++i)
            futures.push_back(std::async(workers > 1 ? std::launch::async : std::launch::deferred,
                search_group, pending[i], group_budget));
        // Merge in the original priority order, independent of worker completion.
        for (size_t i = begin; i < end; ++i) {
            const int g = pending[i];
            auto searched = futures[i - begin].get();
            const auto& group = problem.groups[g];
            const auto& metric = metrics.at(group.id);
            const int target_span = std::max(0, metric.row_span - 2);
            diagnostics.three_tier_active = problem.rich_three_tier_active;
            diagnostics.extreme_groups_attempted += metric.row_span >= 2;
            ++diagnostics.groups_attempted;
            diagnostics.row_windows_attempted += searched.windows;
            diagnostics.dfs_nodes += searched.nodes;
            int accepted = 0;
            for (const auto& item : searched.patterns) {
                auto proposal = assignment;
                for (int p : group.passengers) proposal[p] = -1;
                int low = std::numeric_limits<int>::max(), high = std::numeric_limits<int>::min();
                for (const auto& entry : item.pattern.assignments) {
                    proposal[entry.first] = entry.second;
                    low = std::min(low, problem.seats[entry.second].row); high = std::max(high, problem.seats[entry.second].row);
                }
                const double score = evaluate_rich_group_score(problem, proposal, g).total();
                const int span = item.pattern.assignments.empty() ? 0 : high - low;
                if (metric.row_span >= 2 && span <= target_span) ++diagnostics.span_reducing_patterns;
                recorder(g, item, score, item.source == "global_value_block");
                ++diagnostics.tier_counts.at(item.source); ++accepted;
            }
            if (accepted) { ++diagnostics.groups_with_patterns; diagnostics.patterns_generated += accepted; }
        }
    }
    diagnostics.seconds = std::chrono::duration<double>(Clock::now() - started).count();
    diagnostics.stopped_by_deadline = diagnostics.stopped_by_deadline || Clock::now() >= deadline;
    return diagnostics;
}

void write_rich_structured_diagnostics(std::ostream& output, const RichStructuredDiagnostics& stats) {
    // Geometry belongs to a separate portfolio pool, not the original DFS counts.
    output << "{\"enabled\":" << (stats.enabled ? "true" : "false")
        << ",\"stopped_by_deadline\":" << (stats.stopped_by_deadline ? "true" : "false")
        << ",\"three_tier_active\":" << (stats.three_tier_active ? "true" : "false")
        << ",\"groups_attempted\":" << stats.groups_attempted << ",\"groups_with_patterns\":" << stats.groups_with_patterns
        << ",\"patterns_generated\":" << stats.patterns_generated << ",\"row_windows_attempted\":" << stats.row_windows_attempted
        << ",\"dfs_nodes\":" << stats.dfs_nodes << ",\"extreme_groups_attempted\":" << stats.extreme_groups_attempted
        << ",\"span_reducing_patterns\":" << stats.span_reducing_patterns << ",\"seconds\":" << stats.seconds;
    if (stats.geometry_pool_patterns) output << ",\"geometry_pool_patterns\":" << stats.geometry_pool_patterns;
    if (stats.owner_pool_patterns) output << ",\"owner_pool_patterns\":" << stats.owner_pool_patterns;
    output << ",\"tier_counts\":{";
    bool first = true;
    for (const auto& entry : stats.tier_counts) { if (!first) output << ','; first = false; output << '"' << entry.first << "\":" << entry.second; }
    output << "},\"repair_queue\":"; write_rich_repair_queue(output, stats.repair_queue, 20); output << '}';
}

}  // namespace full_cpp
