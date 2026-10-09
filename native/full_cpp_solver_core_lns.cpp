#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {

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

}  // namespace full_cpp
