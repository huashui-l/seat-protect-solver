// Functional section: stage scheduling, assignment state, and LNS workspace. 
void write_rich_structured_diagnostics(std::ostream& output, const RichStructuredDiagnostics& stats) {
    output << "{\"enabled\":" << (stats.enabled ? "true" : "false")
        << ",\"stopped_by_deadline\":" << (stats.stopped_by_deadline ? "true" : "false")
        << ",\"three_tier_active\":" << (stats.three_tier_active ? "true" : "false")
        << ",\"groups_attempted\":" << stats.groups_attempted << ",\"groups_with_patterns\":" << stats.groups_with_patterns
        << ",\"patterns_generated\":" << stats.patterns_generated << ",\"row_windows_attempted\":" << stats.row_windows_attempted
        << ",\"dfs_nodes\":" << stats.dfs_nodes << ",\"extreme_groups_attempted\":" << stats.extreme_groups_attempted
        << ",\"span_reducing_patterns\":" << stats.span_reducing_patterns << ",\"seconds\":" << stats.seconds << ",\"tier_counts\":{";
    bool first = true;
    for (const auto& entry : stats.tier_counts) { if (!first) output << ','; first = false; output << '"' << entry.first << "\":" << entry.second; }
    output << "},\"repair_queue\":"; write_rich_repair_queue(output, stats.repair_queue, 20); output << '}';
}

RichStageBudgets calculate_rich_stage_budgets(
    const Problem& problem, const native_json::Value& algorithm
) {
    auto number = [&](const char* key, double fallback) {
        const auto* value = algorithm.find(key);
        return value ? value->number_or(fallback) : fallback;
    };
    auto enabled = [&](const char* key, bool fallback) {
        const auto* value = algorithm.find(key);
        return value ? value->bool_or(fallback) : fallback;
    };
    RichStageBudgets result;
    result.business_time_limit = std::max(0.1, number("business_time_limit_seconds", 5.0));
    result.scoring_reserve = std::min(result.business_time_limit * 0.2,
        std::max(0.02, number("scoring_time_reserve", 0.1)));
    result.usable_time = std::max(0.0, result.business_time_limit - result.scoring_reserve);
    auto& stages = result.stages;
    stages = {
        {"construction", number("construction_time_budget", result.usable_time * 0.20)},
        {"repair", number("repair_time_budget", result.usable_time * 0.10)},
        {"vnd", number("vnd_time_budget", result.usable_time * 0.20)},
        {"pattern_generation", number("structured_pattern_time_budget", 1.6)},
        {"lns", number("lns_time_budget", result.usable_time * 0.50)},
        {"restricted_mip", number("restricted_pattern_mip_time_budget", 0.0)},
        {"protected_multigroup_mip", number("protected_multigroup_mip_time_budget", 0.0)},
    };
    const int travelers = static_cast<int>(problem.passengers.size());
    for (const auto& passenger : problem.passengers)
        result.seat_demand += 1 + (passenger.need_both_empty ? 2 : passenger.need_single_empty ? 1 : 0);
    result.post_protected_tail_reserve_active = result.business_time_limit >= 10.0
        && problem.seats.size() < problem.old_seats.size()
        && result.seat_demand == static_cast<int>(problem.seats.size())
        && result.seat_demand > travelers
        && double(travelers) / std::max<size_t>(1, problem.seats.size()) >= 0.93;
    result.post_protected_special_pricing_active = result.post_protected_tail_reserve_active
        && enabled("enable_post_protected_special_pricing", true);
    if (enabled("adaptive_stage_budgets", true)) {
        const double low = number("adaptive_budget_low_seat_demand", 100.0);
        const double high = std::max(low + 1.0, number("adaptive_budget_high_seat_demand", 130.0));
        const double load = std::min(1.0, std::max(0.0, (result.seat_demand - low) / (high - low)));
        stages["construction"] = 1.3 + 1.7 * load;
        stages["repair"] = 0.25;
        stages["vnd"] = 0.5 + 0.2 * load;
        stages["pattern_generation"] = 2.3 - 1.9 * load;
        stages["lns"] = 0.0;
        stages["restricted_mip"] = 0.4;
        const bool priority = enabled("enable_priority_multigroup_pattern_mip", false)
            && result.business_time_limit >= number("priority_multigroup_min_business_time_seconds", 10.0);
        stages["protected_multigroup_mip"] =
            enabled("enable_protected_multigroup_pattern_mip", false) || priority
                ? number("protected_multigroup_mip_time_budget", 8.0) : 0.0;
    }
    // Match Python's insertion order when summing before proportional scaling.
    const char* order[] = {"construction", "repair", "vnd", "pattern_generation",
        "lns", "restricted_mip", "protected_multigroup_mip"};
    double total = 0.0;
    for (const char* name : order) total += std::max(0.0, stages[name]);
    if (total > result.usable_time && total > 0.0) {
        const double scale = result.usable_time / total;
        for (auto& stage : stages) stage.second = std::max(0.0, stage.second) * scale;
    }
    return result;
}

std::vector<std::pair<int, int>> rich_placement_domain(
    const Problem& problem, const FixedSeatContext& fixed, int passenger_index
) {
    const Passenger& passenger = problem.passengers[passenger_index];
    const SsrRule rule = ssr_rule(problem, passenger);
    std::vector<std::pair<int, int>> options;
    for (int index = 0; index < static_cast<int>(problem.seats.size()); ++index) {
        const Seat& seat = problem.seats[index];
        if (!passenger.fixed_seat.empty()) {
            if (seat.id != passenger.fixed_seat) continue;
        } else if (fixed.owner_by_seat[index] >= 0 || fixed.deterministic_blocked[index]) {
            continue;
        }
        if ((!passenger.cabin.empty() && passenger.cabin != seat.cabin)
            || (seat.exit_row && !rule.allow_exit_row)
            || (rule.requires_bassinet && !seat.bassinet)
            || (rule.requires_aisle && !seat.aisle)) continue;
        if (passenger.need_both_empty) {
            const auto& neighbors = problem.both_side_empty_allow_cross_aisle
                ? seat.row_neighbors : seat.same_block_neighbors;
            if (neighbors.empty()
                || (problem.require_two_real_neighbors && neighbors.size() != 2)) continue;
            if (std::any_of(neighbors.begin(), neighbors.end(),
                [&](int neighbor) { return fixed.owner_by_seat[neighbor] >= 0; })) continue;
            options.emplace_back(index, -1);
        } else if (passenger.need_single_empty) {
            for (int neighbor : seat.same_block_neighbors)
                if (fixed.owner_by_seat[neighbor] < 0) options.emplace_back(index, neighbor);
        } else {
            options.emplace_back(index, -1);
        }
    }
    return options;
}

RichCandidateCache build_rich_candidate_cache(const Problem& problem, const AssignmentState& state,
    const std::vector<double>* frozen_owner_regrets, int target_group) {
    if (target_group >= 0 && !frozen_owner_regrets)
        throw std::runtime_error("group candidate cache requires frozen owner regrets");
    RichCandidateCache result;
    const int count = static_cast<int>(problem.passengers.size());
    const int seats = static_cast<int>(problem.seats.size());
    result.owner_regrets.resize(count, 0.0);
    result.costs.resize(count);
    result.rankings.resize(count);
    std::vector<int> assigned_passengers, assigned_infants;
    for (int p = 0; p < count; ++p) if (state.passenger_to_seat[p] >= 0) {
        assigned_passengers.push_back(p);
        if (problem.passengers[p].ssr == "BSCT") assigned_infants.push_back(p);
    }
    const auto seat_value = [&](const Seat& seat) {
        if (!std::isnan(seat.explicit_value)) return seat.explicit_value;
        double value = seat.cabin == "Business" ? problem.business_seat_value : 0.0;
        if (seat.extra_legroom) value += problem.extra_legroom_value;
        if (seat.bassinet) value += problem.bassinet_value;
        return value;
    };
    std::unordered_map<std::string, int> owners;
    for (int p = 0; p < count; ++p)
        if (problem.old_seat_index.count(problem.passengers[p].old_seat))
            owners[problem.passengers[p].old_seat] = p;
    for (int p = 0; p < count; ++p) {
        const auto& passenger = problem.passengers[p];
        if (target_group >= 0 && passenger.group != target_group) continue;
        result.costs[p].resize(seats);
        const auto old_index = problem.old_seat_index.find(passenger.old_seat);
        const Seat* old = old_index == problem.old_seat_index.end() ? nullptr : &problem.old_seats[old_index->second];
        const double old_value = !std::isnan(passenger.old_seat_value) ? passenger.old_seat_value
            : old ? seat_value(*old) : 0.0;
        for (int s = 0; s < seats; ++s) {
            const auto& seat = problem.seats[s];
            double score = problem.weight_v * std::abs(seat_value(seat) - old_value);
            if (old) {
                const int dy = old->row - seat.row;
                const double factor = !problem.prioritize_front ? 1.0
                    : dy >= 0 ? problem.front_penalty_reduction : problem.back_penalty_factor;
                const double distance = std::abs(dy) * factor + std::abs(seat.x - old->x);
                const int mismatch = (seat.window != old->window) + (seat.aisle != old->aisle)
                    + (seat.exit_row != old->exit_row) + (seat.bassinet != old->bassinet)
                    + (seat.near_toilet != old->near_toilet);
                score += problem.weight_s * distance + problem.weight_p * mismatch;
            }
            for (const auto& preference : passenger.rich_toilet_preferences)
                if (seat.near_toilet != preference.first) score += problem.weight_t * preference.second;
            double impact = 0.0;
            if (passenger.group_id != -1) {
                const auto& affecting = passenger.ssr == "BSCT" ? assigned_passengers : assigned_infants;
                for (int other : affecting) {
                    const int assigned = state.passenger_to_seat[other];
                    if (problem.passengers[other].group_id == passenger.group_id) continue;
                    const auto& other_seat = problem.seats[assigned];
                    if (seat.row == other_seat.row && seat.subrow == other_seat.subrow)
                        impact += 1.0 / (1.0 + std::abs(seat.x - other_seat.x));
                    else if (std::abs(seat.row - other_seat.row) == 1)
                        impact += problem.baby_front_back_factor / (1.0 + std::abs(seat.x - other_seat.x));
                }
            }
            result.costs[p][s] = -(score + problem.weight_b * impact);
        }
    }
    if (frozen_owner_regrets) result.owner_regrets = *frozen_owner_regrets;
    for (int p = 0; p < count && !frozen_owner_regrets; ++p) {
        const auto own = problem.seat_index.find(problem.passengers[p].old_seat);
        if (own == problem.seat_index.end() || !state.rich_seat_feasible(p, own->second)) continue;
        double alternative = std::numeric_limits<double>::infinity();
        for (int s = 0; s < seats; ++s)
            if (s != own->second && state.rich_seat_feasible(p, s)) alternative = std::min(alternative, result.costs[p][s]);
        if (std::isfinite(alternative)) result.owner_regrets[p] = std::max(0.0, alternative - result.costs[p][own->second]);
    }
    for (int p = 0; p < count; ++p) {
        if (target_group >= 0 && problem.passengers[p].group != target_group) continue;
        auto& order = result.rankings[p];
        for (int s = 0; s < seats; ++s) {
            const auto owner = owners.find(problem.seats[s].id);
            if (owner != owners.end() && owner->second != p)
                result.costs[p][s] += problem.rich.old_seat_reservation_pressure * result.owner_regrets[owner->second];
            order.push_back(s);
        }
        std::stable_sort(order.begin(), order.end(), [&](int left, int right) {
            return result.costs[p][left] < result.costs[p][right];
        });
    }
    return result;
}

RichStageSchedule::RichStageSchedule(const RichStageBudgets& budgets,
                                     double allocation_start, double restricted_tail_budget,
                                     double search_deadline_limit)
    : budgets_(budgets), allocation_start_(allocation_start),
      search_deadline_(std::min(allocation_start + budgets.usable_time, search_deadline_limit)),
      restricted_tail_budget_(std::max(0.0, restricted_tail_budget)) {}

RichStageWindow RichStageSchedule::begin(const std::string& stage, double now,
                                         int construction_unassigned) {
    if (stage == "special_pricing") {
        return {pricing_reserve_, std::min(search_deadline_, now + pricing_reserve_)};
    }
    double effective = budgets_.stages.at(stage) + carry_;
    if (stage == "construction") {
        effective = budgets_.stages.at(stage);
        return {effective, std::min(search_deadline_, allocation_start_ + effective)};
    }
    if (stage == "vnd") {
        pricing_reserve_ = std::min(std::max(0.0, effective - 0.1),
            budgets_.post_protected_tail_reserve_active ? 2.0 : 0.0);
        effective -= pricing_reserve_;
    }
    if (stage == "restricted_mip") {
        effective = std::max(budgets_.stages.at(stage), restricted_tail_budget_)
            + std::max(0.0, lns_deadline_ - now);
    }
    double deadline = std::min(search_deadline_, now + effective);
    if (stage == "repair") {
        if (construction_unassigned > 0) deadline = search_deadline_;
        effective = std::max(0.0, deadline - now);
    }
    if (stage == "lns") lns_deadline_ = deadline;
    return {effective, deadline};
}

void RichStageSchedule::finish(const std::string& stage,
                               const RichStageWindow& window, double now) {
    // Python keeps protected-stage carry across special pricing. LNS carry is
    // evaluated at restricted-stage entry, after intervening bookkeeping.
    if (stage != "special_pricing" && stage != "lns" && stage != "restricted_mip") {
        carry_ = std::max(0.0, window.deadline - now);
    }
}

AssignmentState::AssignmentState(const Problem& source, const FixedSeatContext* fixed)
    : problem(source),
      seat_to_passenger(source.seats.size(), -1),
      passenger_to_seat(source.passengers.size(), -1),
      blocked_count(source.seats.size(), 0),
      assigned_blocked(source.passengers.size()),
      owner_group_by_seat(source.seats.size(), -1),
      seat_ssr_passenger(source.seats.size(), -1) {
    if (!fixed) return;
    for (int seat = 0; seat < static_cast<int>(fixed->owner_by_seat.size()); ++seat) {
        const int passenger = fixed->owner_by_seat[seat];
        if (passenger < 0) continue;
        seat_to_passenger[seat] = passenger;
        passenger_to_seat[passenger] = seat;
        owner_group_by_seat[seat] = source.passengers[passenger].group;
        if (!source.passengers[passenger].ssr.empty()) seat_ssr_passenger[seat] = passenger;
    }
    for (int passenger = 0; passenger < static_cast<int>(source.passengers.size()); ++passenger) {
        if (passenger_to_seat[passenger] >= 0) assignment_order.push_back(passenger);
        const Passenger& item = source.passengers[passenger];
        if (!item.need_both_empty || passenger_to_seat[passenger] < 0) continue;
        const Seat& seat = source.seats[passenger_to_seat[passenger]];
        const auto& neighbors = source.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        for (int neighbor : neighbors) {
            ++blocked_count[neighbor];
            assigned_blocked[passenger].push_back(neighbor);
        }
    }
}

bool AssignmentState::can_assign(int passenger_index, int seat_index, int chosen_block, int excluded_seat) const {
    if (passenger_index < 0 || passenger_index >= static_cast<int>(problem.passengers.size())
        || seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) return false;
    if (passenger_to_seat[passenger_index] >= 0) return false;
    const Passenger& passenger = problem.passengers[passenger_index];
    const Seat& seat = problem.seats[seat_index];
    if (!passenger.fixed_seat.empty() && passenger.fixed_seat != seat.id) return false;
    if (passenger.need_both_empty && (problem.both_side_empty_allow_cross_aisle
        ? seat.row_neighbors : seat.same_block_neighbors).empty()) return false;
    return rich_seat_feasible(passenger_index, seat_index, chosen_block, excluded_seat);
}

bool AssignmentState::rich_seat_feasible(int passenger_index, int seat_index, int chosen_block, int excluded_seat,
    const std::set<int>& excluded_seats) const {
    const auto excluded = [&](int seat) { return seat == excluded_seat || excluded_seats.count(seat) != 0; };
    if (passenger_index < 0 || passenger_index >= static_cast<int>(problem.passengers.size())
        || seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) return false;
    if ((seat_to_passenger[seat_index] >= 0 && !excluded(seat_index)) || blocked_count[seat_index] > 0) return false;
    const Passenger& passenger = problem.passengers[passenger_index];
    const Seat& seat = problem.seats[seat_index];
    if (!passenger.cabin.empty() && passenger.cabin != seat.cabin) return false;
    const SsrRule rule = ssr_rule(problem, passenger);
    if ((seat.exit_row && !rule.allow_exit_row)
        || (rule.requires_bassinet && !seat.bassinet)
        || (rule.requires_aisle && !seat.aisle)) return false;

    if (passenger.need_both_empty) {
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        if (problem.require_two_real_neighbors && neighbors.size() != 2) return false;
        for (int neighbor : neighbors) {
            if ((seat_to_passenger[neighbor] >= 0 && !excluded(neighbor)) || blocked_count[neighbor] > 0) return false;
        }
    }
    if (passenger.need_single_empty) {
        bool available = false;
        for (int neighbor : seat.same_block_neighbors) {
            if ((seat_to_passenger[neighbor] < 0 || excluded(neighbor)) && blocked_count[neighbor] == 0
                && (chosen_block < 0 || chosen_block == neighbor)) available = true;
        }
        if (!available) return false;
    }

    if (!passenger.ssr.empty()) {
        for (bool by_row : {true, false}) {
            bool active = by_row ? passenger.same_row_no_other_ssr
                                 : passenger.same_subrow_no_other_ssr;
            std::map<std::string, int> counts{{passenger.ssr, 1}};
            for (int other_seat = 0;
                 other_seat < static_cast<int>(seat_ssr_passenger.size()); ++other_seat) {
                const int other_index = seat_ssr_passenger[other_seat];
                if (other_index < 0 || excluded(other_seat)) continue;
                const Seat& other = problem.seats[other_seat];
                if (other.row != seat.row || (!by_row && other.subrow != seat.subrow)) continue;
                const Passenger& other_passenger = problem.passengers[other_index];
                active = active || (by_row ? other_passenger.same_row_no_other_ssr
                                           : other_passenger.same_subrow_no_other_ssr);
                ++counts[other_passenger.ssr];
            }
            if (active && std::any_of(counts.begin(), counts.end(), [](const auto& entry) { return entry.second > 1; })) return false;
        }
    }
    return true;
}

bool AssignmentState::assign(int passenger_index, int seat_index, int chosen_block, int excluded_seat) {
    if (!can_assign(passenger_index, seat_index, chosen_block, excluded_seat)) return false;
    const Passenger& passenger = problem.passengers[passenger_index];
    const Seat& seat = problem.seats[seat_index];
    int single_block = chosen_block;
    if (passenger.need_single_empty && !passenger.need_both_empty) {
        single_block = -1;
        for (int neighbor : seat.same_block_neighbors) {
            if (seat_to_passenger[neighbor] < 0 && blocked_count[neighbor] == 0 && neighbor != excluded_seat
                && (chosen_block < 0 || chosen_block == neighbor)) {
                single_block = neighbor;
                break;
            }
        }
        if (single_block < 0) return false;
    }
    seat_to_passenger[seat_index] = passenger_index;
    passenger_to_seat[passenger_index] = seat_index;
    assignment_order.push_back(passenger_index);
    owner_group_by_seat[seat_index] = passenger.group;
    if (!passenger.ssr.empty()) seat_ssr_passenger[seat_index] = passenger_index;
    if (passenger.need_both_empty) {
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        for (int neighbor : neighbors) {
            if (neighbor == excluded_seat || seat_to_passenger[neighbor] >= 0) continue;
            ++blocked_count[neighbor];
            assigned_blocked[passenger_index].push_back(neighbor);
        }
    } else if (passenger.need_single_empty) {
        ++blocked_count[single_block];
        assigned_blocked[passenger_index].push_back(single_block);
    }
    return true;
}

bool AssignmentState::assign_rich_pattern(int passenger_index, int seat_index,
    const std::set<int>& excluded_seats, int chosen_block
) {
    if (!rich_seat_feasible(passenger_index, seat_index, -1, -1, excluded_seats)) return false;
    const auto& passenger = problem.passengers[passenger_index];
    const auto& seat = problem.seats[seat_index];
    int single_block = -1;
    if (passenger.need_single_empty && !passenger.need_both_empty) {
        const auto available = [&](int s) {
            return s >= 0 && seat_to_passenger[s] < 0 && blocked_count[s] == 0 && !excluded_seats.count(s);
        };
        if (available(chosen_block)) single_block = chosen_block;
        else for (int neighbor : seat.same_block_neighbors) if (available(neighbor)) { single_block = neighbor; break; }
        if (single_block < 0) return false;
    }
    seat_to_passenger[seat_index] = passenger_index;
    passenger_to_seat[passenger_index] = seat_index;
    assignment_order.push_back(passenger_index);
    owner_group_by_seat[seat_index] = passenger.group;
    if (!passenger.ssr.empty()) seat_ssr_passenger[seat_index] = passenger_index;
    if (passenger.need_both_empty) {
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        for (int neighbor : neighbors) {
            if (seat_to_passenger[neighbor] >= 0 || excluded_seats.count(neighbor)) continue;
            ++blocked_count[neighbor];
            assigned_blocked[passenger_index].push_back(neighbor);
        }
    } else if (passenger.need_single_empty) {
        ++blocked_count[single_block];
        assigned_blocked[passenger_index].push_back(single_block);
    }
    return true;
}

void AssignmentState::remove(int passenger_index) {
    if (passenger_index < 0 || passenger_index >= static_cast<int>(passenger_to_seat.size())) return;
    const int seat = passenger_to_seat[passenger_index];
    if (seat < 0) return;
    for (int blocked : assigned_blocked[passenger_index]) {
        if (blocked_count[blocked] > 0) --blocked_count[blocked];
    }
    assigned_blocked[passenger_index].clear();
    seat_to_passenger[seat] = -1;
    passenger_to_seat[passenger_index] = -1;
    assignment_order.erase(std::remove(assignment_order.begin(), assignment_order.end(), passenger_index), assignment_order.end());
    owner_group_by_seat[seat] = -1;
    seat_ssr_passenger[seat] = -1;
}

AssignmentSnapshot AssignmentState::save() const {
    return {seat_to_passenger, passenger_to_seat, blocked_count, assigned_blocked,
            owner_group_by_seat, seat_ssr_passenger, assignment_order};
}

void AssignmentState::restore(AssignmentSnapshot snapshot) {
    seat_to_passenger = std::move(snapshot.seat_to_passenger);
    passenger_to_seat = std::move(snapshot.passenger_to_seat);
    blocked_count = std::move(snapshot.blocked_count);
    assigned_blocked = std::move(snapshot.assigned_blocked);
    owner_group_by_seat = std::move(snapshot.owner_group_by_seat);
    seat_ssr_passenger = std::move(snapshot.seat_ssr_passenger);
    assignment_order = std::move(snapshot.assignment_order);
}

RichLnsWorkspace::RichLnsWorkspace(const AssignmentState& state, std::chrono::steady_clock::time_point deadline)
    : keys_by_group(state.problem.groups.size()), state_(state), deadline_(deadline) {
    const auto& problem = state.problem;
    for (int p : state.assignment_order) {
        keys_by_group[problem.passengers[p].group].push_back(p);
        if (problem.passengers[p].ssr == "BSCT") infants_.emplace_back(problem.passengers[p].group, state.passenger_to_seat[p]);
    }
    for (int g = 0; g < static_cast<int>(keys_by_group.size()); ++g) {
        const auto& keys = keys_by_group[g];
        if (keys.empty() || keys.size() > static_cast<size_t>(problem.rich.multigroup_pattern_group_size_limit)) continue;
        if (std::all_of(keys.begin(), keys.end(), [&](int p) {
            const auto& passenger = problem.passengers[p];
            return passenger.new_seat_num_is_none && !passenger.need_both_empty && !passenger.need_single_empty;
        })) eligible_groups.insert(g);
    }
}

double RichLnsWorkspace::passenger_score(int p, int s) {
    const auto& problem = state_.problem;
    const auto individual_key = std::make_pair(p, s);
    auto individual = individual_cache_.find(individual_key);
    if (individual == individual_cache_.end()) {
        std::vector<int> isolated(problem.passengers.size(), -1); isolated[p] = s;
        individual = individual_cache_.emplace(individual_key,
            evaluate_rich_group_score(problem, isolated, problem.passengers[p].group).total()).first;
    }
    const auto baby_key = std::make_pair(problem.passengers[p].group, s);
    auto baby = baby_cache_.find(baby_key);
    if (baby == baby_cache_.end()) {
        double value = 0.0;
        for (const auto& infant : infants_) {
            if (infant.first == baby_key.first || infant.second == s) continue;
            const auto& a = problem.seats[infant.second]; const auto& b = problem.seats[s];
            if (a.cabin != b.cabin) continue;
            const double distance = 1.0 + std::abs(a.x - b.x);
            if (a.row == b.row && a.subrow == b.subrow) value += problem.weight_b / distance;
            else if (std::abs(a.row - b.row) == 1) value += problem.weight_b * problem.baby_front_back_factor / distance;
        }
        baby = baby_cache_.emplace(baby_key, value).first;
    }
    return individual->second + baby->second;
}

double RichLnsWorkspace::compact_score(const std::vector<int>& seats) {
    auto key = seats;
    const auto& problem = state_.problem;
    std::sort(key.begin(), key.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
    const auto found = compact_cache_.find(key);
    if (found != compact_cache_.end()) return found->second;
    double value = 0.0;
    if (seats.size() > 1) {
        double x = 0.0, y = 0.0, dx = 0.0, dy = 0.0;
        for (int s : seats) { x += problem.seats[s].x; y += problem.seats[s].y; }
        x /= seats.size(); y /= seats.size();
        for (int s : seats) { dx = std::max(dx, std::abs(problem.seats[s].x - x)); dy = std::max(dy, std::abs(problem.seats[s].y - y)); }
        value = problem.weight_c * (problem.group_centroid_x_factor * dx + problem.group_centroid_y_factor * dy);
    }
    compact_cache_[std::move(key)] = value;
    return value;
}

RichLnsAssignment RichLnsWorkspace::best_matching(const std::vector<int>& keys, const std::vector<int>& seats) {
    const auto popcount = [](size_t value) { int count = 0; while (value) { value &= value - 1; ++count; } return count; };
    const size_t count = size_t{1} << keys.size();
    std::vector<double> dp(count, -std::numeric_limits<double>::infinity());
    std::vector<int> parent(count, -1);
    dp[0] = 0.0;
    for (size_t mask = 0; mask < count; ++mask) {
        if (mask % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; return {}; }
        const int p = popcount(mask);
        if (p >= static_cast<int>(keys.size()) || dp[mask] == -std::numeric_limits<double>::infinity()) continue;
        for (size_t j = 0; j < seats.size(); ++j) {
            const size_t bit = size_t{1} << j;
            if (mask & bit) continue;
            const size_t next = mask | bit;
            const double value = dp[mask] + passenger_score(keys[p], seats[j]);
            if (value > dp[next]) { dp[next] = value; parent[next] = static_cast<int>(j); }
        }
    }
    RichLnsAssignment result; result.score = dp.back(); result.seats.resize(keys.size());
    size_t mask = count - 1;
    while (mask) {
        const int j = parent[mask]; const size_t previous = mask ^ (size_t{1} << j);
        result.seats[popcount(previous)] = seats[j]; mask = previous;
    }
    return result;
}

RichLnsAssignment RichLnsWorkspace::best_group_assignment(int group_index, const std::vector<int>& seats,
    const std::set<int>& released_seats
) {
    const auto& problem = state_.problem;
    const auto& keys = keys_by_group[group_index];
    const auto needs_care = [&](int p) { return problem.passengers[p].need_cared || ssr_rule(problem, problem.passengers[p]).requires_caregiver; };
    if (std::all_of(keys.begin(), keys.end(), [&](int p) { return problem.passengers[p].ssr.empty() && !needs_care(p); }))
        return best_matching(keys, seats);
    RichLnsAssignment result;
    const size_t n = keys.size();
    std::vector<std::vector<bool>> feasible(n, std::vector<bool>(n));
    std::vector<std::vector<double>> scores(n, std::vector<double>(n));
    for (size_t i = 0; i < n; ++i) for (size_t j = 0; j < n; ++j) {
        feasible[i][j] = state_.rich_seat_feasible(keys[i], seats[j], -1, -1, released_seats);
        if (feasible[i][j]) scores[i][j] = passenger_score(keys[i], seats[j]);
    }
    std::vector<int> selected(n, -1);
    std::vector<bool> used(n);
    size_t nodes = 0;
    // Input order preserves the first permutation on equal scores. Independent
    // row maxima are an admissible bound even before enforcing seat uniqueness.
    const auto search = [&](auto&& self, size_t depth, double score) -> void {
        if (stopped_by_deadline) return;
        if (nodes++ % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) {
            stopped_by_deadline = true; return;
        }
        double bound = score;
        for (size_t i = depth; i < n; ++i) {
            double best = -std::numeric_limits<double>::infinity();
            for (size_t j = 0; j < n; ++j)
                if (!used[j] && feasible[i][j]) best = std::max(best, scores[i][j]);
            if (!std::isfinite(best)) return;
            bound += best;
        }
        if (bound < result.score - 1e-9) return;
        for (size_t i = 0; i < depth; ++i) {
            if (!needs_care(keys[i])) continue;
            const auto rule = ssr_rule(problem, problem.passengers[keys[i]]);
            const auto& seat = problem.seats[seats[selected[i]]];
            const auto& neighbors = rule.caregiver_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
            bool possible = false;
            for (size_t k = 0; k < n && !possible; ++k) {
                if (k == i || !problem.passengers[keys[k]].ssr.empty()) continue;
                for (size_t j = 0; j < n && !possible; ++j) {
                    const bool available = k < depth ? selected[k] == static_cast<int>(j) : !used[j] && feasible[k][j];
                    if (available && std::find(neighbors.begin(), neighbors.end(), seats[j]) != neighbors.end()) possible = true;
                }
            }
            if (!possible) return;
        }
        if (depth == n) {
            if (score > result.score) {
                result.score = score; result.seats.clear();
                for (int j : selected) result.seats.push_back(seats[j]);
            }
            return;
        }
        // With only ordinary passengers left, caregiver availability depends
        // on the remaining seat set, not on the identities assigned to it.
        // Collapse that factorial tail to an exact subset assignment DP.
        if (std::all_of(keys.begin() + depth, keys.end(), [&](int p) {
                return problem.passengers[p].ssr.empty() && !needs_care(p);
            })) {
            std::vector<size_t> remaining;
            for (size_t j = 0; j < n; ++j) if (!used[j]) remaining.push_back(j);
            const size_t count = size_t{1} << remaining.size();
            const auto popcount = [](size_t mask) {
                size_t value = 0; while (mask) { mask &= mask - 1; ++value; } return value;
            };
            std::vector<double> best(count, -std::numeric_limits<double>::infinity());
            best.back() = 0.0;
            for (size_t mask = count - 1; mask-- > 0;) {
                if (mask % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) {
                    stopped_by_deadline = true; return;
                }
                const size_t i = depth + popcount(mask);
                for (size_t j = 0; j < remaining.size(); ++j) {
                    const size_t bit = size_t{1} << j;
                    if (!(mask & bit) && feasible[i][remaining[j]])
                        best[mask] = std::max(best[mask], scores[i][remaining[j]] + best[mask | bit]);
                }
            }
            if (!std::isfinite(best[0])) return;
            size_t mask = 0;
            double total = score;
            for (size_t i = depth; i < n; ++i) {
                for (size_t j = 0; j < remaining.size(); ++j) {
                    const size_t bit = size_t{1} << j;
                    if (!(mask & bit) && feasible[i][remaining[j]]
                        && scores[i][remaining[j]] + best[mask | bit] >= best[mask] - 1e-10) {
                        selected[i] = static_cast<int>(remaining[j]);
                        total += scores[i][remaining[j]]; mask |= bit; break;
                    }
                }
            }
            if (total > result.score) {
                result.score = total; result.seats.clear();
                for (int j : selected) result.seats.push_back(seats[j]);
            }
            for (size_t i = depth; i < n; ++i) selected[i] = -1;
            return;
        }
        for (size_t j = 0; j < n; ++j) if (!used[j] && feasible[depth][j]) {
            selected[depth] = static_cast<int>(j); used[j] = true;
            self(self, depth + 1, score + scores[depth][j]);
            used[j] = false; selected[depth] = -1;
        }
    };
    search(search, 0, 0.0);
    return result;
}

std::vector<std::vector<int>> RichLnsWorkspace::candidate_subsets(int group_index, const std::vector<int>& seat_pool) {
    const auto& problem = state_.problem;
    const auto seat_less = [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; };
    const auto tuple_less = [&](const std::vector<int>& a, const std::vector<int>& b) {
        return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end(), seat_less);
    };
    // Python iterates a hash set without a frozen hash seed. Use a documented
    // lexical traversal of the identical set; equal-score cutoff identity can differ.
    std::set<std::vector<int>, decltype(tuple_less)> subsets(tuple_less);
    const auto& keys = keys_by_group[group_index];
    const std::set<int> released(seat_pool.begin(), seat_pool.end());
    std::vector<int> current;
    for (int p : keys) current.push_back(state_.passenger_to_seat[p]);
    if (std::all_of(current.begin(), current.end(), [&](int s) { return released.count(s) != 0; })) {
        std::sort(current.begin(), current.end(), seat_less); subsets.insert(current);
    }
    const size_t neighborhood_size = std::min(seat_pool.size(), keys.size() + problem.rich.multigroup_neighborhood_extra_seats);
    for (size_t center_index = 0; center_index < seat_pool.size(); ++center_index) {
        if (center_index % 8 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; break; }
        const int center = seat_pool[center_index];
        const auto& c = problem.seats[center];
        auto nearest = seat_pool;
        std::sort(nearest.begin(), nearest.end(), [&](int a, int b) {
            const auto& x = problem.seats[a]; const auto& y = problem.seats[b];
            return std::make_tuple(std::abs(x.row - c.row), std::abs(x.x - c.x), x.id)
                < std::make_tuple(std::abs(y.row - c.row), std::abs(y.x - c.x), y.id);
        });
        nearest.resize(neighborhood_size);
        if (keys.size() > nearest.size()) continue;
        std::vector<size_t> indexes(keys.size());
        for (size_t i = 0; i < indexes.size(); ++i) indexes[i] = i;
        size_t combination_index = 0;
        while (true) {
            if (combination_index++ % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; break; }
            std::vector<int> seats;
            for (size_t i : indexes) seats.push_back(nearest[i]);
            if (std::find(seats.begin(), seats.end(), center) != seats.end()) {
                std::sort(seats.begin(), seats.end(), seat_less); subsets.insert(std::move(seats));
            }
            int i = static_cast<int>(indexes.size()) - 1;
            while (i >= 0 && indexes[i] == nearest.size() - indexes.size() + i) --i;
            if (i < 0) break;
            ++indexes[i];
            for (size_t j = i + 1; j < indexes.size(); ++j) indexes[j] = indexes[j - 1] + 1;
        }
    }
    return {subsets.begin(), subsets.end()};
}

std::vector<RichLnsOption> RichLnsWorkspace::group_options(int group_index, const std::vector<int>& seat_pool,
    const std::function<void(int, const RichLnsOption&)>& recorder
) {
    const auto& problem = state_.problem;
    const std::set<int> released(seat_pool.begin(), seat_pool.end());
    const auto& keys = keys_by_group[group_index];
    struct Entry { RichLnsOption option; int sequence; };
    const auto greater = [](const Entry& a, const Entry& b) {
        return std::make_pair(a.option.score, a.sequence) > std::make_pair(b.option.score, b.sequence);
    };
    std::vector<Entry> heap;
    int sequence = 0;
    for (const auto& seats : candidate_subsets(group_index, seat_pool)) {
        if (std::chrono::steady_clock::now() >= deadline_) break;
        auto matching = best_group_assignment(group_index, seats, released);
        if (matching.seats.empty()) continue;
        auto proposal = state_.passenger_to_seat;
        for (size_t i = 0; i < keys.size(); ++i) proposal[keys[i]] = matching.seats[i];
        Entry entry{{evaluate_rich_group_score(problem, proposal, group_index).total(),
            std::set<int>(seats.begin(), seats.end()), std::move(matching.seats)}, sequence++};
        if (heap.size() < static_cast<size_t>(problem.rich.multigroup_option_limit)) {
            heap.push_back(std::move(entry)); std::push_heap(heap.begin(), heap.end(), greater);
        } else if (entry.option.score > heap.front().option.score) {
            std::pop_heap(heap.begin(), heap.end(), greater); heap.back() = std::move(entry);
            std::push_heap(heap.begin(), heap.end(), greater);
        }
    }
    options_generated += static_cast<int>(heap.size());
    std::sort(heap.begin(), heap.end(), greater);
    std::vector<RichLnsOption> result;
    for (auto& entry : heap) result.push_back(std::move(entry.option));
    if (recorder) for (size_t i = 0; i < std::min(result.size(), static_cast<size_t>(problem.rich.elite_patterns_from_pricing_per_call)); ++i)
        recorder(group_index, result[i]);
    return result;
}

