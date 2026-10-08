// Functional section: elite pattern storage and relocation diagnostics. 
void RichEliteStore::capture(const AssignmentState& state, const std::string& source) {
    const auto& problem = state.problem;
    for (int g = 0; g < static_cast<int>(problem.groups.size()); ++g) {
        const auto& group = problem.groups[g];
        RichElitePattern pattern;
        bool complete = true;
        for (int p : group.passengers) {
            const int seat = state.passenger_to_seat[p];
            if (seat < 0) { complete = false; break; }
            const int host = problem.passengers[p].hostnum;
            pattern.assignments.emplace_back(host, problem.seats[seat].id);
            std::vector<std::string> blocks;
            for (int blocked : state.assigned_blocked[p]) blocks.push_back(problem.seats[blocked].id);
            pattern.blocked_by_host.emplace_back(host, std::move(blocks));
        }
        if (!complete) continue;
        pattern.local_score = evaluate_rich_group_score(problem, state.passenger_to_seat, g).total();
        pattern.source = source;
        pattern.pinned = true;
        // A captured current placement owns all of its resources, so it has
        // no conflicts even when candidate conflict diversity is enabled.
        record(group.id, std::move(pattern), {}, false);
    }
}

void RichEliteStore::record_candidate(int group_id, RichElitePattern pattern,
    const AssignmentState& state, bool conflict_diversity_active
) {
    std::map<std::string, int> owners;
    for (int p : state.assignment_order)
        owners[state.problem.seats[state.passenger_to_seat[p]].id] = state.problem.passengers[p].group_id;
    for (int p : state.assignment_order)
        for (int seat : state.assigned_blocked[p])
            owners[state.problem.seats[seat].id] = state.problem.passengers[p].group_id;
    record(group_id, std::move(pattern), owners, conflict_diversity_active);
}

bool rich_patterns_have_conditional_ssr_conflict(const Problem& problem,
    int left_group_id, const RichElitePattern& left, int right_group_id, const RichElitePattern& right
) {
    for (bool by_row : {true, false}) {
        std::map<std::pair<std::pair<int, int>, std::string>, int> counts;
        std::set<std::pair<int, int>> active;
        const auto profile = [&](int gid, const RichElitePattern& pattern) {
            const auto group = std::find_if(problem.groups.begin(), problem.groups.end(), [&](const Group& g) { return g.id == gid; });
            for (const auto& entry : pattern.assignments) {
                const auto p = std::find_if(group->passengers.begin(), group->passengers.end(),
                    [&](int index) { return problem.passengers[index].hostnum == entry.first; });
                const auto& passenger = problem.passengers[*p];
                if (passenger.ssr.empty()) continue;
                const auto& seat = problem.seats[problem.seat_index.at(entry.second)];
                const auto location = std::make_pair(seat.row, by_row ? -1 : seat.subrow);
                ++counts[{location, passenger.ssr}];
                if (by_row ? passenger.same_row_no_other_ssr : passenger.same_subrow_no_other_ssr) active.insert(location);
            }
        };
        profile(left_group_id, left); profile(right_group_id, right);
        for (const auto& entry : counts) if (entry.second > 1 && active.count(entry.first.first)) return true;
    }
    return false;
}

bool rebuild_rich_pattern_component(const AssignmentState& state,
    const std::map<int, RichElitePattern>& choices, AssignmentSnapshot& rebuilt
) {
    const auto& problem = state.problem;
    AssignmentState candidate(problem);
    candidate.restore(state.save());
    for (const auto& group : problem.groups) if (choices.count(group.id))
        for (int p : group.passengers) candidate.remove(p);
    for (const auto& choice : choices) {
        const auto group = std::find_if(problem.groups.begin(), problem.groups.end(), [&](const Group& g) { return g.id == choice.first; });
        std::vector<std::pair<int, int>> proposed;
        std::set<int> seats;
        for (const auto& entry : choice.second.assignments) {
            const auto p = std::find_if(group->passengers.begin(), group->passengers.end(),
                [&](int index) { return problem.passengers[index].hostnum == entry.first; });
            const int seat = problem.seat_index.at(entry.second);
            proposed.emplace_back(*p, seat); seats.insert(seat);
        }
        const auto requires_care = [&](int p) {
            return problem.passengers[p].need_cared || ssr_rule(problem, problem.passengers[p]).requires_caregiver;
        };
        std::stable_sort(proposed.begin(), proposed.end(), [&](const auto& a, const auto& b) { return requires_care(a.first) < requires_care(b.first); });
        for (const auto& entry : proposed) {
            const auto& passenger = problem.passengers[entry.first];
            int chosen = -1;
            for (const auto& blocked : choice.second.blocked_by_host)
                if (passenger.need_single_empty && blocked.first == passenger.hostnum && !blocked.second.empty()) {
                    chosen = problem.seat_index.at(blocked.second.front()); break;
                }
            auto excluded = seats; excluded.erase(entry.second);
            if (!candidate.assign_rich_pattern(entry.first, entry.second, excluded, chosen)) return false;
        }
    }
    rebuilt = candidate.save();
    return true;
}

static void normalize_rich_elite_pattern(RichElitePattern& pattern) {
    std::sort(pattern.assignments.begin(), pattern.assignments.end());
    pattern.blocked_by_host.erase(std::remove_if(pattern.blocked_by_host.begin(), pattern.blocked_by_host.end(),
        [](const auto& entry) { return entry.second.empty(); }), pattern.blocked_by_host.end());
    for (auto& entry : pattern.blocked_by_host) std::sort(entry.second.begin(), entry.second.end());
    std::sort(pattern.blocked_by_host.begin(), pattern.blocked_by_host.end());
    pattern.occupied_seats.clear();
    for (const auto& entry : pattern.assignments) pattern.occupied_seats.push_back(entry.second);
    std::sort(pattern.occupied_seats.begin(), pattern.occupied_seats.end());
    std::set<std::string> blocked;
    for (const auto& entry : pattern.blocked_by_host) blocked.insert(entry.second.begin(), entry.second.end());
    pattern.blocked_seats.assign(blocked.begin(), blocked.end());
    blocked.insert(pattern.occupied_seats.begin(), pattern.occupied_seats.end());
    pattern.seat_resources.assign(blocked.begin(), blocked.end());
}

void RichEliteStore::record(int group_id, RichElitePattern pattern,
    const std::map<std::string, int>& owner_by_resource, bool conflict_diversity_active
) {
    normalize_rich_elite_pattern(pattern);
    std::set<int> conflicts;
    if (conflict_diversity_active) for (const auto& seat : pattern.seat_resources) {
        const auto owner = owner_by_resource.find(seat);
        if (owner != owner_by_resource.end() && owner->second != group_id) conflicts.insert(owner->second);
    }
    pattern.conflict_groups.assign(conflicts.begin(), conflicts.end());
    auto& patterns = groups_[group_id];
    const auto existing = std::find_if(patterns.begin(), patterns.end(), [&](const auto& old) {
        return old.assignments == pattern.assignments && old.blocked_by_host == pattern.blocked_by_host;
    });
    if (existing == patterns.end()) patterns.push_back(std::move(pattern));
    else if (pattern.local_score > existing->local_score) *existing = std::move(pattern);
    else if (pattern.pinned) existing->pinned = true;
    if (patterns.size() <= static_cast<size_t>(limit_)) return;
    std::map<std::vector<int>, int> conflict_counts;
    for (const auto& item : patterns) ++conflict_counts[item.conflict_groups];
    bool has_duplicate_conflict = false;
    for (const auto& item : patterns)
        if (!item.pinned && conflict_counts[item.conflict_groups] > 1) has_duplicate_conflict = true;
    auto worst = patterns.end();
    for (auto item = patterns.begin(); item != patterns.end(); ++item) {
        if (item->pinned || (has_duplicate_conflict && conflict_counts[item->conflict_groups] <= 1)) continue;
        if (worst == patterns.end() || item->local_score < worst->local_score) worst = item;
    }
    if (worst != patterns.end()) patterns.erase(worst);
}

bool RichEliteStore::insert_relocation(int group_id, RichElitePattern pattern, const std::function<double()>& score) {
    normalize_rich_elite_pattern(pattern);
    auto& patterns = groups_[group_id];
    for (const auto& old : patterns)
        if (old.assignments == pattern.assignments && old.blocked_by_host == pattern.blocked_by_host) return false;
    pattern.local_score = score();
    patterns.push_back(std::move(pattern));
    return true;
}

RichDynamicRelocationDiagnostics add_rich_dynamic_relocation_patterns(
    const Problem& problem, const AssignmentState& state, int group_index,
    const std::set<int>& outside_resources, std::chrono::steady_clock::time_point deadline,
    const FixedSeatContext& fixed, const std::vector<RichBabyCost>& baby,
    std::map<int, RichPricingCache>& pricing_caches, RichEliteStore& elite
) {
    using Clock = std::chrono::steady_clock;
    RichDynamicRelocationDiagnostics diagnostics;
    if (!problem.rich.protected_dynamic_relocation_enabled || Clock::now() >= deadline) return diagnostics;
    auto found = pricing_caches.find(group_index);
    if (found == pricing_caches.end())
        found = pricing_caches.emplace(group_index, build_rich_pricing_cache(problem, group_index, fixed)).first;
    auto released = filter_rich_pricing_resources(problem, found->second, outside_resources);
    if (std::any_of(released.all_options.begin(), released.all_options.end(),
        [](const auto& options) { return options.empty(); })) return diagnostics;
    const auto call_deadline = std::min(deadline, Clock::now()
        + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(problem.rich.protected_dynamic_relocation_seconds)));
    auto config = problem.rich_pricing_config;
    config.type = native_json::Value::Type::Object;
    for (const auto& entry : std::vector<std::pair<std::string, double>>{
        {"quick_pricing_columns_per_group", static_cast<double>(problem.rich.protected_dynamic_relocation_columns)},
        {"dfs_discovery_time_limit", std::max(.01, std::chrono::duration<double>(call_deadline - Clock::now()).count())}}) {
        auto& value = config.object[entry.first];
        value.type = native_json::Value::Type::Number; value.number = entry.second;
    }
    const int gid = problem.groups[group_index].id;
    RichPricingDuals duals; duals.group[gid] = 1e12;
    ++diagnostics.calls;
    const auto priced = price_rich_group_dfs(problem, group_index, config, duals, baby,
        {}, {}, call_deadline, released, false, false, false);
    elite.ensure_group(gid);
    for (size_t i = 0; i < priced.patterns.size() && i < static_cast<size_t>(problem.rich.protected_dynamic_relocation_columns); ++i) {
        const auto& candidate = priced.patterns[i];
        RichElitePattern pattern;
        auto proposal = state.passenger_to_seat;
        for (const auto& entry : candidate.assignments) {
            pattern.assignments.emplace_back(problem.passengers[entry.first].hostnum, problem.seats[entry.second].id);
            proposal[entry.first] = entry.second;
        }
        std::map<int, std::vector<std::string>> blocked;
        for (const auto& entry : candidate.blocked_by)
            blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
        pattern.blocked_by_host.assign(blocked.begin(), blocked.end());
        pattern.source = "released_component_pricing";
        if (elite.insert_relocation(gid, std::move(pattern),
            [&]() { return evaluate_rich_group_score(problem, proposal, group_index).total(); })) ++diagnostics.patterns;
    }
    return diagnostics;
}

void write_rich_elite_store(std::ostream& output, const RichEliteStore& store) {
    output << '{';
    bool first_group = true;
    for (const auto& group : store.groups()) {
        if (!first_group) output << ',';
        first_group = false;
        output << std::quoted(std::to_string(group.first)) << ":[";
        bool first_pattern = true;
        for (const auto& item : group.second) {
            if (!first_pattern) output << ',';
            first_pattern = false;
            output << "{\"assignments\":[";
            for (size_t i = 0; i < item.assignments.size(); ++i) {
                if (i) output << ',';
                output << '[' << item.assignments[i].first << ',' << std::quoted(item.assignments[i].second) << ']';
            }
            output << "],\"blocked_by_host\":[";
            const auto strings = [&](const auto& values) {
                output << '[';
                for (size_t i = 0; i < values.size(); ++i) {
                    if (i) output << ',';
                    output << std::quoted(values[i]);
                }
                output << ']';
            };
            for (size_t i = 0; i < item.blocked_by_host.size(); ++i) {
                if (i) output << ',';
                output << '[' << item.blocked_by_host[i].first << ',';
                strings(item.blocked_by_host[i].second);
                output << ']';
            }
            output << "],\"occupied_seats\":"; strings(item.occupied_seats);
            output << ",\"blocked_seats\":"; strings(item.blocked_seats);
            output << ",\"seat_resources\":"; strings(item.seat_resources);
            output << ",\"conflict_groups\":[";
            for (size_t i = 0; i < item.conflict_groups.size(); ++i) {
                if (i) output << ',';
                output << item.conflict_groups[i];
            }
            output << "],\"local_score\":" << item.local_score
                << ",\"source\":" << std::quoted(item.source)
                << ",\"pinned\":" << (item.pinned ? "true" : "false") << '}';
        }
        output << ']';
    }
    output << '}';
}

