// Functional section: placement, structured pattern, and pricing search. 
std::vector<std::vector<RichPlacement>> build_rich_placement_options(
    const Problem& problem, int group_index, const FixedSeatContext& fixed
) {
    const auto& group = problem.groups[group_index];
    std::vector<std::vector<RichPlacement>> result;
    const auto seat_less = [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; };
    for (int p : group.passengers) {
        const auto& passenger = problem.passengers[p];
        const auto rule = ssr_rule(problem, passenger);
        std::vector<RichPlacement> options;
        for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) {
            const auto& seat = problem.seats[s];
            if (!passenger.fixed_seat.empty() && seat.id != passenger.fixed_seat) continue;
            if (passenger.fixed_seat.empty() && (fixed.owner_by_seat[s] >= 0 || fixed.deterministic_blocked[s])) continue;
            if (!passenger.cabin.empty() && seat.cabin != passenger.cabin) continue;
            if ((seat.exit_row && !rule.allow_exit_row) || (rule.requires_bassinet && !seat.bassinet)
                || (rule.requires_aisle && !seat.aisle)) continue;
            std::vector<std::vector<int>> modes(1);
            if (passenger.need_both_empty) {
                const auto& neighbors = problem.both_side_empty_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
                if ((problem.require_two_real_neighbors && neighbors.size() != 2) || neighbors.empty()) continue;
                if (std::any_of(neighbors.begin(), neighbors.end(), [&](int n) { return fixed.owner_by_seat[n] >= 0; })) continue;
                modes = {neighbors};
            } else if (passenger.need_single_empty) {
                modes.clear();
                for (int neighbor : seat.same_block_neighbors)
                    if (fixed.owner_by_seat[neighbor] < 0) modes.push_back({neighbor});
                if (modes.empty()) continue;
            }
            const double cost = -evaluate_rich_individual_score(problem, p, s).total();
            for (auto& blocked : modes) {
                RichPlacement option;
                option.passenger_index = static_cast<int>(result.size());
                option.passenger = p;
                option.seat = s;
                std::sort(blocked.begin(), blocked.end(), seat_less);
                option.blocked = blocked;
                option.resources = blocked;
                option.resources.push_back(s);
                std::sort(option.resources.begin(), option.resources.end(), seat_less);
                option.individual_cost = cost;
                if (!passenger.ssr.empty()) {
                    option.ssr_resources = {{{seat.row, -1}, passenger.ssr}, {{seat.row, seat.subrow}, passenger.ssr}};
                    if (passenger.same_row_no_other_ssr) option.ssr_flag_locations.push_back({seat.row, -1});
                    if (passenger.same_subrow_no_other_ssr) option.ssr_flag_locations.push_back({seat.row, seat.subrow});
                }
                option.is_infant = passenger.ssr == "BSCT";
                options.push_back(std::move(option));
            }
        }
        if (options.empty()) throw std::runtime_error("passenger has no legal Rich placement: "
            + std::to_string(group.id) + ":" + std::to_string(passenger.hostnum));
        result.push_back(std::move(options));
    }
    return result;
}

bool rich_placements_caregiver_ok(const Problem& problem, int group_index,
    const std::vector<RichPlacement>& placements
) {
    const auto& group = problem.groups[group_index];
    std::vector<int> seats(group.passengers.size(), -1);
    for (const auto& placement : placements) seats[placement.passenger_index] = placement.seat;
    for (size_t i = 0; i < group.passengers.size(); ++i) {
        const auto& passenger = problem.passengers[group.passengers[i]];
        const auto rule = ssr_rule(problem, passenger);
        if (!passenger.need_cared && !rule.requires_caregiver) continue;
        const auto& seat = problem.seats[seats[i]];
        const auto& neighbors = rule.caregiver_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        bool found = false;
        for (size_t j = 0; j < group.passengers.size(); ++j)
            if (j != i && is_adult_caregiver(problem.passengers[group.passengers[j]])
                && std::find(neighbors.begin(), neighbors.end(), seats[j]) != neighbors.end()) found = true;
        if (!found) return false;
    }
    return true;
}

RichExactPattern build_rich_exact_pattern(const Problem& problem, int group_index,
    const std::vector<RichPlacement>& placements, const std::vector<std::string>& active_ssr_types
) {
    RichExactPattern pattern;
    pattern.group_id = problem.groups[group_index].id;
    pattern.placements = placements;
    std::stable_sort(pattern.placements.begin(), pattern.placements.end(),
        [](const auto& a, const auto& b) { return a.passenger_index < b.passenger_index; });
    std::set<int> occupied, resources, infants;
    auto active_types = active_ssr_types;
    if (active_types.empty()) for (const auto& rule : problem.ssr_rules) active_types.push_back(rule.first);
    double individual_cost = 0.0;
    for (const auto& placement : placements) {
        pattern.assignments.emplace_back(placement.passenger, placement.seat);
        for (int seat : placement.blocked) pattern.blocked_by.emplace_back(seat, placement.passenger);
        occupied.insert(placement.seat);
        resources.insert(placement.resources.begin(), placement.resources.end());
        if (placement.is_infant) infants.insert(placement.seat);
        for (const auto& resource : placement.ssr_resources) ++pattern.ssr_all[resource];
        for (const auto& location : placement.ssr_flag_locations)
            for (const auto& ssr : active_types) pattern.ssr_flagged[{location, ssr}] = 1;
        individual_cost += placement.individual_cost;
    }
    std::sort(pattern.assignments.begin(), pattern.assignments.end(), [&](const auto& a, const auto& b) {
        return std::make_pair(problem.passengers[a.first].hostnum, problem.seats[a.second].id)
            < std::make_pair(problem.passengers[b.first].hostnum, problem.seats[b.second].id);
    });
    std::sort(pattern.blocked_by.begin(), pattern.blocked_by.end(), [&](const auto& a, const auto& b) {
        return std::make_pair(problem.seats[a.first].id, problem.passengers[a.second].hostnum)
            < std::make_pair(problem.seats[b.first].id, problem.passengers[b.second].hostnum);
    });
    const auto ordered_seats = [&](const std::set<int>& values) {
        std::vector<int> result(values.begin(), values.end());
        std::sort(result.begin(), result.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
        return result;
    };
    pattern.occupied_seats = ordered_seats(occupied);
    pattern.seat_resources = ordered_seats(resources);
    pattern.infant_seats = ordered_seats(infants);
    double x = 0.0, y = 0.0, dx = 0.0, dy = 0.0;
    for (const auto& assignment : pattern.assignments) {
        x += problem.seats[assignment.second].x;
        y += problem.seats[assignment.second].y;
    }
    if (!pattern.assignments.empty()) {
        x /= pattern.assignments.size(); y /= pattern.assignments.size();
        for (const auto& assignment : pattern.assignments) {
            dx = std::max(dx, std::abs(problem.seats[assignment.second].x - x));
            dy = std::max(dy, std::abs(problem.seats[assignment.second].y - y));
        }
    }
    pattern.master_cost = individual_cost - problem.weight_c
        * (problem.group_centroid_x_factor * dx + problem.group_centroid_y_factor * dy);
    // The master's baby variables count same-group occupants too. Cancel those
    // ordered pairs in the column. The evaluator excludes self-pairs.
    for (int u : pattern.infant_seats) for (int v : pattern.occupied_seats) {
        if (u == v) continue;
        const auto& infant = problem.seats[u];
        const auto& other = problem.seats[v];
        if (infant.cabin != other.cabin) continue;
        double factor = 0.0;
        if (infant.row == other.row && infant.subrow == other.subrow) factor = 1.0;
        else if (std::abs(infant.row - other.row) == 1) factor = problem.baby_front_back_factor;
        pattern.master_cost += problem.weight_b * factor / (1.0 + std::abs(infant.x - other.x));
    }
    return pattern;
}

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

RichPricingCache build_rich_pricing_cache(const Problem& problem, int group_index, const FixedSeatContext& fixed) {
    RichPricingCache cache;
    cache.all_options = build_rich_placement_options(problem, group_index, fixed);
    std::set<int> reachable;
    for (const auto& options : cache.all_options) for (const auto& option : options) reachable.insert(option.seat);
    double min_y = std::numeric_limits<double>::infinity(), min_x = min_y;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) if (reachable.count(s)) {
        cache.seat_ids.push_back(s);
        min_y = std::min(min_y, problem.seats[s].y); min_x = std::min(min_x, problem.seats[s].x);
    }
    std::set<std::pair<std::string, std::string>> edges;
    for (int s : cache.seat_ids) {
        cache.row_coordinate[s] = problem.seats[s].y - min_y;
        cache.x_coordinate[s] = problem.seats[s].x - min_x;
        cache.row_big_m = std::max(cache.row_big_m, cache.row_coordinate[s]);
        cache.x_big_m = std::max(cache.x_big_m, cache.x_coordinate[s]);
        for (int neighbor : problem.seats[s].row_neighbors) if (neighbor != s) {
            auto a = problem.seats[s].id, b = problem.seats[neighbor].id;
            if (b < a) std::swap(a, b);
            edges.emplace(a, b);
        }
    }
    for (const auto& edge : edges) cache.adjacency_edges.emplace_back(problem.seat_index.at(edge.first), problem.seat_index.at(edge.second));
    // SeatTopology retains first-seen row order, then sorts seats within each row.
    std::vector<int> row_order;
    std::map<int, std::vector<int>> row_seats;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) {
        const int row = problem.seats[s].row;
        if (!row_seats.count(row)) row_order.push_back(row);
        row_seats[row].push_back(s);
    }
    for (int row : row_order) {
        auto& seats = row_seats[row];
        std::sort(seats.begin(), seats.end(), [&](int a, int b) { return problem.seats[a].index_in_row < problem.seats[b].index_in_row; });
        for (size_t i = 1; i + 1 < seats.size(); ++i) {
            RichHoleSpec hole; hole.middle = seats[i];
            for (size_t j = 0; j < i; ++j) if (reachable.count(seats[j])) hole.left.push_back(seats[j]);
            for (size_t j = i + 1; j < seats.size(); ++j) if (reachable.count(seats[j])) hole.right.push_back(seats[j]);
            if (!hole.left.empty() && !hole.right.empty()) cache.hole_specs.push_back(std::move(hole));
        }
    }
    return cache;
}

RichPricingCache filter_rich_pricing_window(const Problem& problem, const RichPricingCache& cache,
    const std::vector<int>& rows
) {
    RichPricingCache filtered;
    filtered.row_big_m = cache.row_big_m; filtered.x_big_m = cache.x_big_m;
    std::set<int> seats;
    for (const auto& options : cache.all_options) {
        filtered.all_options.emplace_back();
        for (const auto& option : options) if (std::find(rows.begin(), rows.end(), problem.seats[option.seat].row) != rows.end()) {
            filtered.all_options.back().push_back(option); seats.insert(option.seat);
        }
    }
    filtered.seat_ids.assign(seats.begin(), seats.end());
    std::sort(filtered.seat_ids.begin(), filtered.seat_ids.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
    for (int s : filtered.seat_ids) {
        filtered.row_coordinate[s] = cache.row_coordinate.at(s); filtered.x_coordinate[s] = cache.x_coordinate.at(s);
    }
    for (const auto& edge : cache.adjacency_edges) if (seats.count(edge.first) && seats.count(edge.second)) filtered.adjacency_edges.push_back(edge);
    for (const auto& hole : cache.hole_specs) {
        if (!seats.count(hole.middle)) continue;
        RichHoleSpec item; item.middle = hole.middle;
        for (int s : hole.left) if (seats.count(s)) item.left.push_back(s);
        for (int s : hole.right) if (seats.count(s)) item.right.push_back(s);
        if (!item.left.empty() && !item.right.empty()) filtered.hole_specs.push_back(std::move(item));
    }
    return filtered;
}

RichPricingCache filter_rich_pricing_resources(const Problem& problem, const RichPricingCache& cache,
    const std::set<int>& outside_resources
) {
    // Python shallow-copies the base cache; history/workspace keep their identity.
    auto filtered = cache;
    filtered.all_options.clear();
    filtered.row_coordinate.clear(); filtered.x_coordinate.clear();
    filtered.adjacency_edges.clear(); filtered.hole_specs.clear();
    std::set<int> seats;
    for (const auto& options : cache.all_options) {
        filtered.all_options.emplace_back();
        for (const auto& option : options) {
            if (std::any_of(option.resources.begin(), option.resources.end(),
                [&](int s) { return outside_resources.count(s) != 0; })) continue;
            filtered.all_options.back().push_back(option);
            seats.insert(option.seat);
        }
    }
    filtered.seat_ids.assign(seats.begin(), seats.end());
    std::sort(filtered.seat_ids.begin(), filtered.seat_ids.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
    for (int s : filtered.seat_ids) {
        filtered.row_coordinate[s] = cache.row_coordinate.at(s); filtered.x_coordinate[s] = cache.x_coordinate.at(s);
    }
    for (const auto& edge : cache.adjacency_edges) if (seats.count(edge.first) && seats.count(edge.second)) filtered.adjacency_edges.push_back(edge);
    for (const auto& hole : cache.hole_specs) {
        if (!seats.count(hole.middle)) continue;
        RichHoleSpec item; item.middle = hole.middle;
        for (int s : hole.left) if (seats.count(s)) item.left.push_back(s);
        for (int s : hole.right) if (seats.count(s)) item.right.push_back(s);
        if (!item.left.empty() && !item.right.empty()) filtered.hole_specs.push_back(std::move(item));
    }
    return filtered;
}

size_t RichPricingGeometry::rectangle_index(int row_low, int row_high, int x_low, int x_high) const {
    return ((static_cast<size_t>(row_low) * row_values.size() + row_high) * x_values.size() + x_low) * x_values.size() + x_high;
}

RichPricingGeometry build_rich_pricing_geometry(const RichPricingCache& cache) {
    RichPricingGeometry geometry;
    for (const auto& entry : cache.row_coordinate) geometry.row_values.push_back(entry.second);
    for (const auto& entry : cache.x_coordinate) geometry.x_values.push_back(entry.second);
    for (auto* values : {&geometry.row_values, &geometry.x_values}) {
        std::sort(values->begin(), values->end());
        values->erase(std::unique(values->begin(), values->end()), values->end());
    }
    const int nr = static_cast<int>(geometry.row_values.size()), nx = static_cast<int>(geometry.x_values.size());
    geometry.seat_prefix.assign(static_cast<size_t>(nr) * nx, 0);
    for (int seat : cache.seat_ids) {
        const int row = static_cast<int>(std::lower_bound(geometry.row_values.begin(), geometry.row_values.end(), cache.row_coordinate.at(seat)) - geometry.row_values.begin());
        const int x = static_cast<int>(std::lower_bound(geometry.x_values.begin(), geometry.x_values.end(), cache.x_coordinate.at(seat)) - geometry.x_values.begin());
        geometry.seat_row_index[seat] = row; geometry.seat_x_index[seat] = x;
        ++geometry.seat_prefix[static_cast<size_t>(row) * nx + x];
    }
    for (int row = 1; row < nr; ++row) for (int x = 0; x < nx; ++x)
        geometry.seat_prefix[static_cast<size_t>(row) * nx + x] += geometry.seat_prefix[static_cast<size_t>(row - 1) * nx + x];
    for (int row = 0; row < nr; ++row) for (int x = 1; x < nx; ++x)
        geometry.seat_prefix[static_cast<size_t>(row) * nx + x] += geometry.seat_prefix[static_cast<size_t>(row) * nx + x - 1];
    return geometry;
}

RichPricingBounds build_rich_pricing_bounds(const RichPricingCache& cache,
    const RichPricingGeometry& geometry, const std::vector<int>& order,
    const std::vector<std::vector<double>>& base_cost, double row_span_cost, double column_span_cost
) {
    const size_t nr = geometry.row_values.size(), nx = geometry.x_values.size();
    if (!nr || !nx) throw std::runtime_error("pricing bounds require a nonempty seat domain");
    const double infinity = std::numeric_limits<double>::infinity();
    const size_t count = nr * nr * nx * nx;
    const auto index = [&](size_t a, size_t b, size_t c, size_t d) { return ((a * nr + b) * nx + c) * nx + d; };
    const auto capacity = [&](size_t a, size_t b, size_t c, size_t d) {
        int value = geometry.seat_prefix[b * nx + d];
        if (a) value -= geometry.seat_prefix[(a - 1) * nx + d];
        if (c) value -= geometry.seat_prefix[b * nx + c - 1];
        if (a && c) value += geometry.seat_prefix[(a - 1) * nx + c - 1];
        return value;
    };
    // Same four cumulative minima as NumPy: low axes forward, high axes backward.
    const auto enclosing_minimum = [&](std::vector<double>& values) {
        const size_t widths[] = {nr, nr, nx, nx};
        const size_t strides[] = {nr * nx * nx, nx * nx, nx, 1};
        for (int axis = 0; axis < 4; ++axis) {
            const size_t width = widths[axis], stride = strides[axis], block = width * stride;
            for (size_t base = 0; base < count; base += block) for (size_t offset = 0; offset < stride; ++offset)
                for (size_t step = 1; step < width; ++step) {
                    const size_t position = axis % 2 ? width - 1 - step : step;
                    const size_t current = base + position * stride + offset;
                    const size_t previous = axis % 2 ? current + stride : current - stride;
                    values[current] = std::min(values[current], values[previous]);
                }
        }
    };
    std::vector<double> exact(count, infinity);
    for (size_t a = 0; a < nr; ++a) for (size_t b = a; b < nr; ++b) {
        const double row_cost = row_span_cost * (geometry.row_values[b] - geometry.row_values[a]);
        for (size_t c = 0; c < nx; ++c) for (size_t d = c; d < nx; ++d)
            if (capacity(a, b, c, d) >= static_cast<int>(cache.all_options.size()))
                exact[index(a, b, c, d)] = row_cost + column_span_cost * (geometry.x_values[d] - geometry.x_values[c]);
    }
    RichPricingBounds bounds;
    bounds.span = exact; enclosing_minimum(bounds.span);
    bounds.root_span = *std::min_element(bounds.span.begin(), bounds.span.end());
    std::vector<std::vector<double>> passenger_cost(cache.all_options.size());
    for (int p : order) {
        std::vector<double> grid(nr * nx, infinity);
        const auto& options = cache.all_options[p];
        for (size_t i = 0; i < options.size(); ++i) {
            const size_t cell = static_cast<size_t>(geometry.seat_row_index.at(options[i].seat)) * nx + geometry.seat_x_index.at(options[i].seat);
            grid[cell] = std::min(grid[cell], base_cost.at(p).at(i));
        }
        auto& minimum = passenger_cost[p]; minimum.assign(count, infinity);
        for (size_t a = 0; a < nr; ++a) for (size_t b = a; b < nr; ++b) {
            std::vector<double> columns(nx, infinity);
            for (size_t row = a; row <= b; ++row) for (size_t x = 0; x < nx; ++x)
                columns[x] = std::min(columns[x], grid[row * nx + x]);
            for (size_t c = 0; c < nx; ++c) {
                double running = infinity;
                for (size_t d = c; d < nx; ++d) {
                    running = std::min(running, columns[d]); minimum[index(a, b, c, d)] = running;
                }
            }
        }
    }
    for (size_t depth = 0; depth <= order.size(); ++depth) {
        auto combined = exact;
        // Preserve Python's addition order at every depth (no reverse suffix sums).
        for (size_t next = depth; next < order.size(); ++next)
            for (size_t i = 0; i < count; ++i) combined[i] += passenger_cost[order[next]][i];
        enclosing_minimum(combined); bounds.suffix.push_back(std::move(combined));
    }
    return bounds;
}

RichPricingWorkspace build_rich_pricing_workspace(const Problem& problem, int group_index,
    const RichPricingCache& cache
) {
    RichPricingWorkspace workspace;
    workspace.geometry = build_rich_pricing_geometry(cache);
    // Python sorts flag locations by their tuple string, not by numeric row.
    std::map<std::string, RichSsrLocation> sorted_flags;
    for (const auto& options : cache.all_options) for (const auto& option : options)
        for (const auto& location : option.ssr_flag_locations) {
            const auto key = location.subrow < 0 ? "('row', " + std::to_string(location.row) + ")"
                : "('subrow', (" + std::to_string(location.row) + ", " + std::to_string(location.subrow) + "))";
            sorted_flags[key] = location;
        }
    std::map<RichSsrLocation, int> flag_index;
    for (const auto& entry : sorted_flags) {
        flag_index[entry.second] = static_cast<int>(workspace.flag_locations.size());
        workspace.flag_locations.push_back(entry.second);
    }
    const size_t seat_words = (problem.seats.size() + 63) / 64;
    const size_t flag_words = (workspace.flag_locations.size() + 63) / 64;
    for (size_t p = 0; p < cache.all_options.size(); ++p) {
        workspace.resource_masks.emplace_back(); workspace.flag_masks.emplace_back();
        workspace.option_flag_indexes.emplace_back();
        for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
            const auto& option = cache.all_options[p][i];
            RichPricingMask resources(seat_words, 0), flags(flag_words, 0);
            std::vector<int> indexes;
            for (int seat : option.resources) resources[seat / 64] |= std::uint64_t{1} << (seat % 64);
            for (const auto& location : option.ssr_flag_locations) {
                const int bit = flag_index.at(location);
                indexes.push_back(bit); flags[bit / 64] |= std::uint64_t{1} << (bit % 64);
            }
            workspace.resource_masks.back().push_back(std::move(resources));
            workspace.flag_masks.back().push_back(std::move(flags));
            workspace.option_flag_indexes.back().push_back(std::move(indexes));
            workspace.option_by_signature[{option.passenger_index, option.seat, option.blocked}] =
                {static_cast<int>(p), static_cast<int>(i)};
        }
    }
    const auto& group = problem.groups[group_index];
    for (size_t p = 0; p < group.passengers.size(); ++p) {
        const auto& passenger = problem.passengers[group.passengers[p]];
        const auto rule = ssr_rule(problem, passenger);
        if (!passenger.need_cared && !rule.requires_caregiver) continue;
        RichPricingCaregiver spec;
        spec.passenger_index = static_cast<int>(p);
        spec.allow_cross_aisle = passenger.need_cared && passenger.ssr.empty() ? false : rule.caregiver_allow_cross_aisle;
        for (size_t other = 0; other < group.passengers.size(); ++other)
            if (other != p && is_adult_caregiver(problem.passengers[group.passengers[other]]))
                spec.caregivers.push_back(static_cast<int>(other));
        workspace.caregiver_specs.push_back(std::move(spec));
    }
    return workspace;
}

bool rich_pricing_caregiver_possible(const Problem& problem, const RichPricingCache& cache,
    const RichPricingWorkspace& workspace, const std::vector<int>& selected, const RichPricingMask& used
) {
    for (const auto& spec : workspace.caregiver_specs) {
        if (selected[spec.passenger_index] < 0) continue;
        const auto& seat = problem.seats[cache.all_options[spec.passenger_index][selected[spec.passenger_index]].seat];
        const auto& neighbors = spec.allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        bool possible = false;
        for (int p : spec.caregivers) {
            if (selected[p] >= 0) {
                const int chosen = cache.all_options[p][selected[p]].seat;
                if (std::find(neighbors.begin(), neighbors.end(), chosen) != neighbors.end()) { possible = true; break; }
                continue;
            }
            for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
                if (std::find(neighbors.begin(), neighbors.end(), cache.all_options[p][i].seat) == neighbors.end()) continue;
                bool overlap = false;
                for (size_t word = 0; word < used.size(); ++word)
                    if (workspace.resource_masks[p][i][word] & used[word]) { overlap = true; break; }
                if (!overlap) { possible = true; break; }
            }
            if (possible) break;
        }
        if (!possible) return false;
    }
    return true;
}

std::vector<RichPricingMask> rich_pricing_caregiver_state(const Problem& problem, const RichPricingCache& cache,
    const RichPricingWorkspace& workspace, const std::vector<int>& selected
) {
    std::vector<RichPricingMask> state;
    for (const auto& spec : workspace.caregiver_specs) {
        if (selected[spec.passenger_index] < 0) { state.emplace_back(); continue; }
        const auto& seat = problem.seats[cache.all_options[spec.passenger_index][selected[spec.passenger_index]].seat];
        const auto& neighbors = spec.allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        RichPricingMask mask((problem.seats.size() + 63) / 64, 0);
        for (int neighbor : neighbors) mask[neighbor / 64] |= std::uint64_t{1} << (neighbor % 64);
        for (int p : spec.caregivers) if (selected[p] >= 0) {
            const int chosen = cache.all_options[p][selected[p]].seat;
            if (mask[chosen / 64] & (std::uint64_t{1} << (chosen % 64))) {
                std::fill(mask.begin(), mask.end(), 0); break;
            }
        }
        state.push_back(std::move(mask));
    }
    return state;
}

RichPricingSymmetry build_rich_pricing_symmetry(const Problem& problem, int group_index,
    const RichPricingCache& domains, bool enabled
) {
    RichPricingSymmetry result;
    const auto& group = problem.groups[group_index];
    result.classes.resize(group.passengers.size()); result.ranks.resize(group.passengers.size());
    if (!enabled) return result;
    using Location = std::tuple<bool, int, int>;
    using Resource = std::tuple<bool, int, int, std::string>;
    using Physical = std::tuple<std::string, std::vector<std::string>, std::vector<std::string>, double,
        std::vector<Resource>, std::vector<Location>, bool>;
    using Descriptor = std::tuple<std::vector<Physical>, int, bool>;
    const auto physical = [&](const RichPlacement& option) {
        std::vector<std::string> blocked, resources;
        for (int seat : option.blocked) blocked.push_back(problem.seats[seat].id);
        for (int seat : option.resources) resources.push_back(problem.seats[seat].id);
        std::vector<Resource> ssr;
        for (const auto& r : option.ssr_resources) ssr.emplace_back(r.location.subrow >= 0, r.location.row, r.location.subrow, r.ssr);
        std::vector<Location> flags;
        for (const auto& l : option.ssr_flag_locations) flags.emplace_back(l.subrow >= 0, l.row, l.subrow);
        char decimal[512];
        const auto converted = std::to_chars(decimal, decimal + sizeof(decimal), option.individual_cost, std::chars_format::fixed, 12);
        if (converted.ec != std::errc{}) throw std::runtime_error("pricing symmetry cost rounding failed");
        const double rounded = std::stod(std::string(decimal, converted.ptr));
        return Physical{problem.seats[option.seat].id, blocked, resources, rounded, ssr, flags, option.is_infant};
    };
    std::map<std::string, std::vector<int>> raw_buckets;
    for (size_t p = 0; p < group.passengers.size(); ++p)
        raw_buckets[problem.passengers[group.passengers[p]].rich_symmetry_fingerprint].push_back(static_cast<int>(p));
    std::map<Descriptor, std::vector<int>> buckets;
    std::vector<std::vector<Physical>> keys(group.passengers.size());
    for (const auto& bucket : raw_buckets) if (bucket.second.size() >= 2) for (int p : bucket.second) {
        for (const auto& option : domains.all_options[p]) keys[p].push_back(physical(option));
        auto sorted = keys[p]; std::sort(sorted.begin(), sorted.end());
        const auto& passenger = problem.passengers[group.passengers[p]];
        const auto rule = ssr_rule(problem, passenger);
        const int care = !passenger.need_cared && !rule.requires_caregiver ? -1
            : passenger.need_cared && passenger.ssr.empty() ? 0 : static_cast<int>(rule.caregiver_allow_cross_aisle);
        buckets[{sorted, care, is_adult_caregiver(passenger)}].push_back(p);
    }
    for (auto& bucket : buckets) if (bucket.second.size() > 1) {
        auto& members = bucket.second; std::sort(members.begin(), members.end());
        ++result.class_count;
        auto ordered = keys[members.front()]; std::sort(ordered.begin(), ordered.end());
        ordered.erase(std::unique(ordered.begin(), ordered.end()), ordered.end());
        for (int p : members) {
            result.classes[p] = members;
            for (const auto& key : keys[p]) result.ranks[p].push_back(static_cast<int>(std::lower_bound(ordered.begin(), ordered.end(), key) - ordered.begin()));
        }
    }
    return result;
}

bool rich_pricing_symmetry_ok(const RichPricingSymmetry& symmetry, const std::vector<int>& selected,
    int passenger_index, int option_index
) {
    if (symmetry.classes[passenger_index].empty()) return true;
    const int rank = symmetry.ranks[passenger_index][option_index];
    for (int other : symmetry.classes[passenger_index]) if (selected[other] >= 0) {
        const int other_rank = symmetry.ranks[other][selected[other]];
        if ((other < passenger_index && other_rank > rank) || (other > passenger_index && other_rank < rank)) return false;
    }
    return true;
}

std::vector<RichBabyCost> build_rich_baby_costs(const Problem& problem) {
    std::vector<RichBabyCost> costs;
    if (std::none_of(problem.passengers.begin(), problem.passengers.end(), [](const auto& p) { return p.ssr == "BSCT"; })) return costs;
    for (int u = 0; u < static_cast<int>(problem.seats.size()); ++u) for (int v = 0; v < static_cast<int>(problem.seats.size()); ++v) {
        const auto& infant = problem.seats[u]; const auto& other = problem.seats[v];
        if (u == v || infant.cabin != other.cabin) continue;
        double score = 0.0;
        const double distance = 1.0 + std::abs(infant.x - other.x);
        if (infant.row == other.row && infant.subrow == other.subrow) score = problem.weight_b / distance;
        else if (std::abs(infant.row - other.row) == 1) score = problem.weight_b * problem.baby_front_back_factor / distance;
        if (score != 0.0) costs.push_back({u, v, -score});
    }
    return costs;
}

double rich_same_group_baby_upper_bound(const RichPricingCache& cache,
    const std::vector<RichBabyCost>& baby_cost, int passenger_count
) {
    if (baby_cost.empty() || passenger_count <= 0) return 0.0;
    int infants = 0;
    std::set<int> infant_seats, occupied(cache.seat_ids.begin(), cache.seat_ids.end());
    for (const auto& options : cache.all_options) {
        bool infant = false;
        for (const auto& option : options) if (option.is_infant) { infant = true; infant_seats.insert(option.seat); }
        infants += infant;
    }
    std::vector<double> per_infant;
    for (int u : infant_seats) {
        std::vector<double> costs;
        for (const auto& pair : baby_cost) if (pair.infant == u && occupied.count(pair.occupant)) costs.push_back(std::max(0.0, pair.cost));
        std::sort(costs.begin(), costs.end(), std::greater<double>());
        double total = 0.0;
        for (size_t i = 0; i < costs.size() && i < static_cast<size_t>(passenger_count); ++i) total += costs[i];
        per_infant.push_back(total);
    }
    std::sort(per_infant.begin(), per_infant.end(), std::greater<double>());
    double total = 0.0;
    for (size_t i = 0; i < per_infant.size() && i < static_cast<size_t>(infants); ++i) total += per_infant[i];
    return total;
}

RichPricingCosts build_rich_pricing_costs(int group_id, const RichPricingCache& cache,
    const RichPricingWorkspace& workspace, const RichPricingDuals& duals,
    const std::vector<RichBabyCost>& baby_cost, bool phase_one
) {
    const auto get = [](const auto& values, const auto& key) { const auto found = values.find(key); return found == values.end() ? 0.0 : found->second; };
    std::map<int, double> infant_dual, occupant_dual;
    for (const auto& pair : baby_cost) {
        const auto key = std::make_pair(pair.infant, pair.occupant);
        infant_dual[pair.infant] += get(duals.baby_lower, key) + get(duals.baby_infant_upper, key);
        occupant_dual[pair.occupant] += get(duals.baby_lower, key) + get(duals.baby_occupant_upper, key);
    }
    std::map<std::pair<int, RichSsrLocation>, double> flag_duals;
    for (const auto& entry : duals.ssr_flag) flag_duals[{std::get<0>(entry), std::get<1>(entry).location}] += std::get<2>(entry);
    RichPricingCosts costs;
    for (const auto& options : cache.all_options) {
        costs.base.emplace_back();
        for (const auto& option : options) {
            double value = phase_one ? 0.0 : option.individual_cost;
            for (int seat : option.resources) value -= get(duals.seat, seat);
            for (const auto& resource : option.ssr_resources) value -= get(duals.ssr_all, resource);
            value -= get(occupant_dual, option.seat);
            if (option.is_infant) value -= get(infant_dual, option.seat);
            costs.base.back().push_back(value);
        }
    }
    for (const auto& location : workspace.flag_locations) costs.flags.push_back(-get(flag_duals, std::make_pair(group_id, location)));
    if (!phase_one && !baby_cost.empty()) costs.baby_relaxation = -rich_same_group_baby_upper_bound(cache, baby_cost, static_cast<int>(cache.all_options.size()));
    return costs;
}

double rich_pattern_reduced_cost(const RichExactPattern& pattern, const RichPricingDuals& duals,
    const std::vector<RichBabyCost>& baby_cost, bool phase_one
) {
    const auto get = [](const auto& values, const auto& key) { const auto found = values.find(key); return found == values.end() ? 0.0 : found->second; };
    double value = (phase_one ? 0.0 : pattern.master_cost) - get(duals.group, pattern.group_id);
    // Structured discovery has only a group reward, so all other terms are zero.
    if (duals.seat.empty() && duals.ssr_all.empty() && duals.ssr_flag.empty()
        && duals.baby_lower.empty() && duals.baby_infant_upper.empty() && duals.baby_occupant_upper.empty())
        return value;
    double seats = 0.0;
    for (int seat : pattern.seat_resources) seats += get(duals.seat, seat);
    value -= seats;
    const auto ordered = [](const auto& coefficients) {
        std::map<std::string, std::pair<RichSsrResource, int>> result;
        for (const auto& entry : coefficients) {
            const auto& r = entry.first; const auto& l = r.location;
            const auto location = l.subrow < 0 ? "'row', " + std::to_string(l.row)
                : "'subrow', (" + std::to_string(l.row) + ", " + std::to_string(l.subrow) + ")";
            result["(" + location + ", '" + r.ssr + "')"] = entry;
        }
        return result;
    };
    std::map<std::pair<int, RichSsrResource>, double> flagged;
    for (const auto& entry : duals.ssr_flag) flagged[{std::get<0>(entry), std::get<1>(entry)}] = std::get<2>(entry);
    for (const auto& item : ordered(pattern.ssr_flagged)) value -= get(flagged, std::make_pair(pattern.group_id, item.second.first)) * item.second.second;
    for (const auto& item : ordered(pattern.ssr_all)) value -= get(duals.ssr_all, item.second.first) * item.second.second;
    for (const auto& pair : baby_cost) {
        const auto key = std::make_pair(pair.infant, pair.occupant);
        const int infant = std::find(pattern.infant_seats.begin(), pattern.infant_seats.end(), pair.infant) != pattern.infant_seats.end();
        const int occupied = std::find(pattern.occupied_seats.begin(), pattern.occupied_seats.end(), pair.occupant) != pattern.occupied_seats.end();
        value -= get(duals.baby_lower, key) * (infant + occupied);
        value -= get(duals.baby_infant_upper, key) * infant;
        value -= get(duals.baby_occupant_upper, key) * occupied;
    }
    return value;
}

RichPricingResult price_rich_group_dfs(const Problem& problem, int group_index,
    const native_json::Value& cfg, const RichPricingDuals& duals,
    const std::vector<RichBabyCost>& baby_cost, const std::set<RichPlacementSignature>& forced,
    const std::set<RichPlacementSignature>& forbidden, std::chrono::steady_clock::time_point deadline,
    RichPricingCache& cache, bool exact, bool phase_one, bool stop_on_negative,
    const std::vector<std::string>& active_ssr_types
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto number = [&](const char* key, double fallback) { return cfg.find(key) ? optional_number(cfg, key) : fallback; };
    const auto boolean = [&](const char* key, bool fallback) { return cfg.find(key) ? optional_bool(cfg, key) : fallback; };
    const auto after = [](Clock::time_point origin, double seconds) { return origin + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)); };
    const auto seconds = [](Clock::time_point from) { return std::chrono::duration<double>(Clock::now() - from).count(); };
    const auto signature = [](const RichPlacement& o) { return RichPlacementSignature{o.passenger_index, o.seat, o.blocked}; };
    const auto pattern_signature = [&](const RichExactPattern& pattern) {
        std::vector<RichPlacementSignature> result;
        for (const auto& o : pattern.placements) result.push_back(signature(o));
        return result;
    };
    const auto intersects = [](const RichPricingMask& a, const RichPricingMask& b) {
        for (size_t i = 0; i < a.size(); ++i) if (a[i] & b[i]) return true;
        return false;
    };
    const auto unite = [](RichPricingMask a, const RichPricingMask& b) {
        for (size_t i = 0; i < a.size(); ++i) a[i] |= b[i];
        return a;
    };
    const auto& group = problem.groups[group_index];
    const int n = static_cast<int>(group.passengers.size());
    const double tolerance = number("tolerance", 1e-7);
    const bool legacy = boolean("benchmark_legacy_pricing", false);
    const int pool_limit = legacy ? 1 : std::max(1, static_cast<int>(number(exact ? "exact_pricing_columns_per_group" : "quick_pricing_columns_per_group",
        number("pricing_columns_per_group", exact ? 4 : 3))));
    RichPricingResult result;
    std::map<int, RichPlacementSignature> forced_by_passenger;
    for (const auto& item : forced) forced_by_passenger[std::get<0>(item)] = item;
    if (forced_by_passenger.size() != forced.size()) {
        result.termination = "infeasible_branch"; result.elapsed = seconds(started); return result;
    }
    double time_limit = std::max(0.0, number(exact ? "dfs_exact_time_limit" : "dfs_discovery_time_limit", exact ? 15.0 : 1.0));
    long long node_limit = std::max(1LL, static_cast<long long>(number("dfs_node_limit", 2000000)));
    if (exact && n >= std::max(2, static_cast<int>(number("dfs_large_group_min_size", 9)))) {
        time_limit = std::max(time_limit, number("dfs_large_group_exact_time_limit", 15.0));
        node_limit = std::max(node_limit, static_cast<long long>(number("dfs_large_group_node_limit", 5000000)));
    }
    const auto local_deadline = std::min(deadline, after(started, time_limit));
    const auto workspace_started = Clock::now();
    const bool reused = static_cast<bool>(cache.dfs_workspace);
    auto workspace = cache.dfs_workspace;
    if (!workspace) {
        workspace = std::make_shared<RichPricingWorkspace>(build_rich_pricing_workspace(problem, group_index, cache));
        if (!legacy) cache.dfs_workspace = workspace;
    }
    const double workspace_seconds = seconds(workspace_started);
    const auto dynamic_started = Clock::now();
    const auto costs = build_rich_pricing_costs(group.id, cache, *workspace, duals, baby_cost, phase_one);
    RichPricingCache domains = cache;
    RichPricingWorkspace domain_workspace = *workspace;
    std::vector<std::vector<double>> base(n);
    for (const auto& options : cache.all_options) result.priced_placements += static_cast<int>(options.size());
    for (int p = 0; p < n; ++p) {
        std::vector<int> indexes;
        const auto required = forced_by_passenger.find(p);
        for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
            const auto sig = signature(cache.all_options[p][i]);
            if (!forbidden.count(sig) && (required == forced_by_passenger.end() || required->second == sig)) indexes.push_back(static_cast<int>(i));
        }
        if (indexes.empty()) { result.termination = "infeasible_branch"; result.elapsed = seconds(started); return result; }
        const auto key = [&](int i) {
            const auto& o = cache.all_options[p][i];
            std::vector<std::string> blocked;
            for (int s : o.blocked) blocked.push_back(problem.seats[s].id);
            return std::make_tuple(costs.base[p][i], o.resources.size(), problem.seats[o.seat].id, blocked);
        };
        std::stable_sort(indexes.begin(), indexes.end(), [&](int a, int b) { return key(a) < key(b); });
        domains.all_options[p].clear(); domain_workspace.resource_masks[p].clear();
        domain_workspace.flag_masks[p].clear(); domain_workspace.option_flag_indexes[p].clear();
        for (int i : indexes) {
            domains.all_options[p].push_back(cache.all_options[p][i]); base[p].push_back(costs.base[p][i]);
            domain_workspace.resource_masks[p].push_back(workspace->resource_masks[p][i]);
            domain_workspace.flag_masks[p].push_back(workspace->flag_masks[p][i]);
            domain_workspace.option_flag_indexes[p].push_back(workspace->option_flag_indexes[p][i]);
        }
    }
    result.workspace_builds = !reused; result.workspace_reuses = reused;
    result.workspace_build_seconds = reused ? 0.0 : workspace_seconds;
    std::vector<int> order(n); std::iota(order.begin(), order.end(), 0);
    std::set<int> cared;
    for (const auto& spec : workspace->caregiver_specs) cared.insert(spec.passenger_index);
    const auto priority = [&](int p) {
        const auto& passenger = problem.passengers[group.passengers[p]];
        return std::make_tuple(!forced_by_passenger.count(p), !(passenger.need_both_empty || passenger.need_single_empty),
            !cared.count(p), domains.all_options[p].size(), p);
    };
    std::sort(order.begin(), order.end(), [&](int a, int b) { return priority(a) < priority(b); });
    const std::set<RichPlacementSignature> historical(cache.historical_start.begin(), cache.historical_start.end());
    std::vector<std::vector<int>> branch(n);
    for (int p : order) {
        branch[p].resize(domains.all_options[p].size()); std::iota(branch[p].begin(), branch[p].end(), 0);
        const auto key = [&](int i) { const auto& o = domains.all_options[p][i]; return std::make_tuple(!historical.count(signature(o)), base[p][i], problem.seats[o.seat].id); };
        std::stable_sort(branch[p].begin(), branch[p].end(), [&](int a, int b) { return key(a) < key(b); });
    }
    const auto& geometry = workspace->geometry;
    const auto bounds = build_rich_pricing_bounds(domains, geometry, order, base,
        phase_one ? 0.0 : -problem.weight_c * problem.group_centroid_y_factor / 2.0,
        phase_one ? 0.0 : -problem.weight_c * problem.group_centroid_x_factor / 2.0);
    const auto group_dual_entry = duals.group.find(group.id);
    const double group_dual = group_dual_entry == duals.group.end() ? 0.0 : group_dual_entry->second;
    std::vector<int> selected(n, -1);
    RichExactPattern best_pattern;
    bool have_best = false;
    std::vector<RichExactPattern> negative;
    std::set<std::vector<RichPlacementSignature>> seen;
    const auto reduced_cost = [&](const RichExactPattern& pattern) { return rich_pattern_reduced_cost(pattern, duals, baby_cost, phase_one); };
    const auto retain = [&](const RichExactPattern& pattern) {
        ++result.negative_patterns_seen;
        const auto sig = pattern_signature(pattern); seen.insert(sig);
        auto found = std::find_if(negative.begin(), negative.end(), [&](const auto& item) { return pattern_signature(item) == sig; });
        if (found == negative.end()) negative.push_back(pattern); else *found = pattern;
        if (static_cast<int>(negative.size()) > pool_limit) {
            auto worst = negative.begin();
            for (auto it = negative.begin() + 1; it != negative.end(); ++it) if (reduced_cost(*it) > reduced_cost(*worst)) worst = it;
            negative.erase(worst);
        }
    };
    if (boolean("dfs_use_historical_incumbent", true)) {
        std::map<int, RichPlacementSignature> by_passenger;
        for (const auto& sig : cache.historical_start) by_passenger[std::get<0>(sig)] = sig;
        if (static_cast<int>(by_passenger.size()) == n) {
            std::vector<RichPlacement> chosen;
            std::set<int> used;
            bool valid = true;
            for (int p = 0; p < n; ++p) {
                const auto sig = by_passenger.find(p);
                if (sig == by_passenger.end()) { valid = false; break; }
                const auto entry = workspace->option_by_signature.find(sig->second);
                if (entry == workspace->option_by_signature.end()) { valid = false; break; }
                const auto& option = cache.all_options[entry->second.first][entry->second.second];
                chosen.push_back(option);
                for (int seat : option.resources) if (!used.insert(seat).second) valid = false;
            }
            if (valid && rich_placements_caregiver_ok(problem, group_index, chosen)) {
                best_pattern = build_rich_exact_pattern(problem, group_index, chosen, active_ssr_types);
                result.reduced_cost = reduced_cost(best_pattern); have_best = true; result.incumbent_seeded = true;
                if (result.reduced_cost < -tolerance) retain(best_pattern);
            }
        }
    }
    const auto symmetry = build_rich_pricing_symmetry(problem, group_index, domains, boolean("dfs_symmetry_breaking_enabled", true));
    result.symmetry_classes = symmetry.class_count;
    const auto unset_time = Clock::time_point::max();
    auto negative_stop = unset_time;
    const auto set_negative_stop = [&]() { negative_stop = std::min(local_deadline, after(Clock::now(), std::max(0.0, number("dfs_negative_refinement_time", 0.25)))); };
    if (stop_on_negative && result.reduced_cost < -tolerance) set_negative_stop();
    const auto lower_bound = [&](int depth, const RichPricingMask& used, const RichPricingMask& flags, double additive,
        int row_low, int row_high, int x_low, int x_high) {
        double remaining_flags = 0.0;
        for (size_t bit = 0; bit < costs.flags.size(); ++bit) if (costs.flags[bit] < 0.0 && !(flags[bit / 64] & (std::uint64_t{1} << (bit % 64)))) remaining_flags += costs.flags[bit];
        const double constant = additive + remaining_flags + costs.baby_relaxation - group_dual;
        double span, coupled;
        if (row_low >= 0) {
            const size_t cell = geometry.rectangle_index(row_low, row_high, x_low, x_high);
            span = bounds.span[cell]; coupled = bounds.suffix[depth][cell];
        } else { span = bounds.root_span; coupled = *std::min_element(bounds.suffix[depth].begin(), bounds.suffix[depth].end()); }
        double independent = constant + span;
        for (int d = depth; d < n; ++d) {
            const int p = order[d]; double minimum = std::numeric_limits<double>::infinity();
            for (size_t i = 0; i < domains.all_options[p].size(); ++i) if (!intersects(domain_workspace.resource_masks[p][i], used)) { minimum = base[p][i]; break; }
            if (!std::isfinite(minimum)) return std::numeric_limits<double>::infinity();
            independent += minimum;
        }
        return std::max(independent, constant + coupled);
    };
    using State = std::tuple<int, RichPricingMask, RichPricingMask, RichPricingMask, RichPricingMask, std::vector<RichPricingMask>>;
    std::map<State, double> best_cost;
    std::string aborted;
    std::function<bool(int, const RichPricingMask&, const RichPricingMask&, const RichPricingMask&, const RichPricingMask&, double, int, int, int, int)> search;
    search = [&](int depth, const RichPricingMask& used, const RichPricingMask& occupied, const RichPricingMask& infants,
        const RichPricingMask& flags, double additive, int row_low, int row_high, int x_low, int x_high) {
        ++result.nodes;
        if (negative_stop != unset_time && Clock::now() >= negative_stop) { aborted = "dfs_negative_refinement_limit"; return true; }
        if (result.nodes > node_limit) { aborted = "dfs_node_limit"; return true; }
        if (Clock::now() >= local_deadline) { aborted = "dfs_time_limit"; return true; }
        State state{depth, used, occupied, infants, flags, rich_pricing_caregiver_state(problem, domains, domain_workspace, selected)};
        const auto previous = best_cost.find(state);
        if (previous != best_cost.end() && previous->second <= additive + tolerance) return false;
        best_cost[std::move(state)] = additive;
        const double bound = lower_bound(depth, used, flags, additive, row_low, row_high, x_low, x_high);
        double cutoff = result.reduced_cost - tolerance;
        if (stop_on_negative) cutoff = -tolerance;
        else if (result.reduced_cost < -tolerance) {
            if (static_cast<int>(negative.size()) < pool_limit) cutoff = -tolerance;
            else { cutoff = -std::numeric_limits<double>::infinity(); for (const auto& pattern : negative) cutoff = std::max(cutoff, reduced_cost(pattern)); cutoff -= tolerance; }
        }
        if (bound >= cutoff) { ++result.bound_prunes; return false; }
        if (depth == n) {
            std::vector<RichPlacement> chosen;
            for (int p = 0; p < n; ++p) if (selected[p] >= 0) chosen.push_back(domains.all_options[p][selected[p]]);
            if (static_cast<int>(chosen.size()) != n || !rich_placements_caregiver_ok(problem, group_index, chosen)) return false;
            const auto pattern = build_rich_exact_pattern(problem, group_index, chosen, active_ssr_types);
            const double rc = reduced_cost(pattern);
            if (rc < result.reduced_cost) {
                result.reduced_cost = rc; best_pattern = pattern; have_best = true; cache.historical_start = pattern_signature(pattern);
                if (stop_on_negative && rc < -tolerance && negative_stop == unset_time) set_negative_stop();
            }
            if (rc < -tolerance) retain(pattern);
            return false;
        }
        const int p = order[depth];
        for (int i : branch[p]) {
            const auto& option = domains.all_options[p][i];
            if (intersects(domain_workspace.resource_masks[p][i], used)) { ++result.resource_prunes; continue; }
            if (!rich_pricing_symmetry_ok(symmetry, selected, p, i)) { ++result.symmetry_prunes; continue; }
            selected[p] = i;
            const auto next_used = unite(used, domain_workspace.resource_masks[p][i]);
            if (!rich_pricing_caregiver_possible(problem, domains, domain_workspace, selected, next_used)) { selected[p] = -1; continue; }
            const int row = geometry.seat_row_index.at(option.seat), x = geometry.seat_x_index.at(option.seat);
            double new_flag_cost = 0.0;
            for (int bit : domain_workspace.option_flag_indexes[p][i]) if (!(flags[bit / 64] & (std::uint64_t{1} << (bit % 64)))) new_flag_cost += costs.flags[bit];
            auto next_occupied = occupied, next_infants = infants;
            next_occupied[option.seat / 64] |= std::uint64_t{1} << (option.seat % 64);
            if (option.is_infant) next_infants[option.seat / 64] |= std::uint64_t{1} << (option.seat % 64);
            const bool stopped = search(depth + 1, next_used, next_occupied, next_infants, unite(flags, domain_workspace.flag_masks[p][i]),
                additive + base[p][i] + new_flag_cost, row_low < 0 ? row : std::min(row_low, row), row_low < 0 ? row : std::max(row_high, row),
                x_low < 0 ? x : std::min(x_low, x), x_low < 0 ? x : std::max(x_high, x));
            selected[p] = -1;
            if (stopped) return true;
        }
        return false;
    };
    result.dynamic_refresh_seconds = seconds(dynamic_started);
    const RichPricingMask empty_seats((problem.seats.size() + 63) / 64, 0), empty_flags((workspace->flag_locations.size() + 63) / 64, 0);
    const double root_bound = lower_bound(0, empty_seats, empty_flags, 0.0, -1, -1, -1, -1);
    search(0, empty_seats, empty_seats, empty_seats, empty_flags, 0.0, -1, -1, -1, -1);
    result.proven_optimal = aborted.empty();
    result.termination = aborted == "dfs_negative_refinement_limit" ? "dfs_negative" : !aborted.empty() ? aborted : result.reduced_cost < -tolerance ? "dfs_negative" : "dfs_optimal";
    std::stable_sort(negative.begin(), negative.end(), [&](const auto& a, const auto& b) { return reduced_cost(a) < reduced_cost(b); });
    if (negative.empty() && have_best) negative.push_back(best_pattern);
    result.patterns = std::move(negative); result.unique_negative_patterns = static_cast<long long>(seen.size());
    result.lower_bound = result.proven_optimal ? result.reduced_cost : root_bound;
    result.elapsed = seconds(started);
    return result;
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

