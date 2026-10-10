#include "native_group_constructor.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <numeric>
#include <tuple>

namespace full_cpp {

RichOrdinaryVndDiagnostics improve_rich_ordinary_vnd(
    AssignmentState& state, const std::vector<std::vector<int>>& rankings,
    std::chrono::steady_clock::time_point deadline, bool include_group_rebuild, bool include_caregiver_rebuild
) {
    RichOrdinaryVndDiagnostics diagnostics;
    const auto expired = [&]() { return std::chrono::steady_clock::now() >= deadline; };
    if (expired()) { diagnostics.stopped_by_deadline = true; return diagnostics; }
    const auto& problem = state.problem;
    const int n = static_cast<int>(problem.passengers.size());
    std::vector<bool> active(n, false), movable_set(n, false);
    for (const auto& group : problem.groups) for (int p : group.passengers) {
        const auto& passenger = problem.passengers[p];
        const auto rule = problem.ssr_rules.find(passenger.ssr);
        const int seat = state.passenger_to_seat[p];
        if (seat < 0 || (!passenger.need_cared &&
            (rule == problem.ssr_rules.end() || !rule->second.requires_caregiver))) continue;
        const bool cross = rule != problem.ssr_rules.end() && rule->second.caregiver_allow_cross_aisle;
        const auto& neighbors = cross ? problem.seats[seat].row_neighbors : problem.seats[seat].same_block_neighbors;
        for (int cg : group.passengers) {
            const auto& item = problem.passengers[cg];
            if (item.ssr.empty() && !item.need_both_empty && !item.need_single_empty
                && std::find(neighbors.begin(), neighbors.end(), state.passenger_to_seat[cg]) != neighbors.end()) active[cg] = true;
        }
    }
    std::vector<int> movable;
    for (int p = 0; p < n; ++p) {
        const auto& item = problem.passengers[p];
        if (state.passenger_to_seat[p] >= 0 && item.fixed_seat.empty() && item.ssr.empty()
            && !item.need_cared && !item.need_both_empty && !item.need_single_empty && !active[p]) {
            movable.push_back(p); movable_set[p] = true;
        }
    }
    if (movable.empty()) return diagnostics;
    // Infant positions stay fixed during ordinary moves; cache their pair costs.
    std::vector<int> infants;
    for (int p = 0; p < n; ++p)
        if (problem.passengers[p].ssr == "BSCT" && state.passenger_to_seat[p] >= 0) infants.push_back(p);
    const size_t seat_count = problem.seats.size();
    std::vector<double> passenger_cache(static_cast<size_t>(n) * seat_count,
        std::numeric_limits<double>::quiet_NaN());
    const auto passenger_score = [&](int p, int s) {
        double& cached = passenger_cache[static_cast<size_t>(p) * seat_count + s];
        if (!std::isnan(cached)) return cached;
        const auto& target = problem.seats[s];
        double baby = 0.0;
        for (int infant : infants) {
            if (problem.passengers[infant].group == problem.passengers[p].group) continue;
            const int infant_seat = state.passenger_to_seat[infant];
            const auto& seat = problem.seats[infant_seat];
            if (s == infant_seat || seat.cabin != target.cabin) continue;
            const double distance = 1.0 + std::abs(seat.x - target.x);
            if (seat.row == target.row && seat.subrow == target.subrow) baby += problem.weight_b / distance;
            else if (std::abs(seat.row - target.row) == 1)
                baby += problem.weight_b * problem.baby_front_back_factor / distance;
        }
        cached = evaluate_rich_individual_score(problem, p, s).total() + baby;
        return cached;
    };
    std::vector<std::vector<int>> group_seats(problem.groups.size());
    for (int p : state.assignment_order)
        group_seats[problem.passengers[p].group].push_back(state.passenger_to_seat[p]);
    const auto compact = [&](const std::vector<int>& seats) {
        if (seats.size() <= 1) return 0.0;
        double x = 0.0, y = 0.0, dx = 0.0, dy = 0.0;
        for (int s : seats) { x += problem.seats[s].x; y += problem.seats[s].y; }
        x /= seats.size(); y /= seats.size();
        for (int s : seats) { dx = std::max(dx, std::abs(problem.seats[s].x - x)); dy = std::max(dy, std::abs(problem.seats[s].y - y)); }
        return problem.weight_c * (problem.group_centroid_x_factor * dx + problem.group_centroid_y_factor * dy);
    };
    const auto attempt = [&](const std::vector<int>& passengers, const std::vector<int>& targets) {
        ++diagnostics.evaluated_moves;
        double delta = 0.0;
        std::map<int, std::map<int, int>> replacements;
        for (size_t i = 0; i < passengers.size(); ++i) {
            const int p = passengers[i], old = state.passenger_to_seat[p];
            delta += passenger_score(p, targets[i]) - passenger_score(p, old);
            replacements[problem.passengers[p].group][old] = targets[i];
        }
        std::map<int, std::vector<int>> updated;
        for (const auto& group : replacements) {
            auto seats = group_seats[group.first];
            for (int& seat : seats) {
                const auto replacement = group.second.find(seat);
                if (replacement != group.second.end()) seat = replacement->second;
            }
            if (passengers.size() == 2 && replacements.size() == 1) seats = group_seats[group.first];
            delta += compact(seats) - compact(group_seats[group.first]);
            updated[group.first] = std::move(seats);
        }
        if (delta <= problem.rich.local_search_epsilon) return false;
        const auto snapshot = state.save();
        for (int p : passengers) state.remove(p);
        for (size_t i = 0; i < passengers.size(); ++i) if (!state.assign(passengers[i], targets[i])) {
            state.restore(snapshot);
            for (int moved : passengers) state.remove(moved);
            for (int moved : passengers) state.assign(moved, snapshot.passenger_to_seat[moved]);
            return false;
        }
        for (auto& group : updated) group_seats[group.first] = std::move(group.second);
        ++diagnostics.accepted_moves;
        diagnostics.score_improvement += delta;
        return true;
    };
    const int cap = std::max(4, problem.rich.local_search_candidate_cap);
    while (!expired()) {
        ++diagnostics.passes;
        bool accepted = false;
        std::stable_sort(movable.begin(), movable.end(), [&](int left, int right) {
            return passenger_score(left, state.passenger_to_seat[left]) < passenger_score(right, state.passenger_to_seat[right]);
        });
        for (int p : movable) {
            if (expired()) break;
            const int old = state.passenger_to_seat[p];
            for (int i = 0; i < std::min(cap, static_cast<int>(rankings[p].size())); ++i) {
                if (expired()) break;
                const int seat = rankings[p][i], other = state.seat_to_passenger[seat];
                if (seat == old || state.blocked_count[seat] > 0 || (other >= 0 && !movable_set[other])) continue;
                accepted = other < 0 ? attempt({p}, {seat}) : attempt({p, other}, {seat, old});
                if (accepted) { if (other < 0) ++diagnostics.one_opt; else ++diagnostics.swaps; break; }
            }
            if (accepted) break;
        }
        if (!accepted) break;
    }
    const int cycle_cap = std::max(4, problem.rich.local_search_cycle_candidate_cap);
    while (!expired()) {
        bool accepted = false;
        for (int first : movable) {
            if (expired()) break;
            for (int i = 0; i < std::min(cycle_cap, static_cast<int>(rankings[first].size())); ++i) {
                const int second = state.seat_to_passenger[rankings[first][i]];
                if (second < 0 || second == first || !movable_set[second]) continue;
                for (int j = 0; j < std::min(cycle_cap, static_cast<int>(rankings[second].size())); ++j) {
                    if (expired()) break;
                    const int third = state.seat_to_passenger[rankings[second][j]];
                    if (third < 0 || third == first || third == second || !movable_set[third]) continue;
                    accepted = attempt({first, second, third}, {state.passenger_to_seat[second],
                        state.passenger_to_seat[third], state.passenger_to_seat[first]});
                    if (accepted) { ++diagnostics.cycles; break; }
                }
                if (accepted) break;
            }
            if (accepted) break;
        }
        if (!accepted) break;
    }
    if (!include_group_rebuild) return diagnostics;
    std::vector<std::vector<int>> keys_by_group(problem.groups.size());
    std::vector<int> group_order, rebuild_groups;
    for (int p : state.assignment_order) {
        const int g = problem.passengers[p].group;
        if (keys_by_group[g].empty()) group_order.push_back(g);
        keys_by_group[g].push_back(p);
    }
    for (int g : group_order)
        if (std::all_of(keys_by_group[g].begin(), keys_by_group[g].end(),
                [&](int p) { return movable_set[p]; })) rebuild_groups.push_back(g);
    const int partner_cap = std::max(2, problem.rich.local_search_related_group_cap);
    const int related_cap = std::max(cycle_cap, problem.rich.local_search_related_candidate_cap);
    const auto popcount = [](unsigned mask) {
        int count = 0;
        while (mask) { mask &= mask - 1; ++count; }
        return count;
    };
    const auto matching = [&](const std::vector<int>& keys, const std::vector<int>& seats) {
        const unsigned count = 1u << keys.size();
        std::vector<double> dp(count, -std::numeric_limits<double>::infinity());
        std::vector<int> parent(count, -1);
        dp[0] = 0.0;
        for (unsigned mask = 0; mask < count; ++mask) {
            const int index = popcount(mask);
            if (index >= static_cast<int>(keys.size()) || !std::isfinite(dp[mask])) continue;
            for (int j = 0; j < static_cast<int>(seats.size()); ++j) {
                if (mask & (1u << j)) continue;
                const unsigned next = mask | (1u << j);
                const double value = dp[mask] + passenger_score(keys[index], seats[j]);
                if (value > dp[next]) { dp[next] = value; parent[next] = j; }
            }
        }
        std::vector<std::pair<int, int>> placement;
        unsigned mask = count - 1;
        while (mask) {
            const int j = parent[mask];
            const unsigned previous = mask ^ (1u << j);
            placement.emplace_back(keys[popcount(previous)], seats[j]);
            mask = previous;
        }
        return std::make_pair(dp.back(), placement);
    };
    const auto center = [&](int g) {
        double sum = 0.0;
        for (int p : keys_by_group[g]) sum += problem.seats[state.passenger_to_seat[p]].row;
        return sum / keys_by_group[g].size();
    };
    while (!expired()) {
        bool accepted = false;
        std::stable_sort(rebuild_groups.begin(), rebuild_groups.end(), [&](int left, int right) {
            return compact(group_seats[left]) < compact(group_seats[right]);
        });
        for (int first : rebuild_groups) {
            if (expired()) break;
            std::vector<bool> preferred(problem.groups.size(), false);
            for (int p : keys_by_group[first])
                for (int i = 0; i < std::min(related_cap, static_cast<int>(rankings[p].size())); ++i) {
                    const int owner = state.seat_to_passenger[rankings[p][i]];
                    if (owner >= 0 && problem.passengers[owner].group != first) preferred[problem.passengers[owner].group] = true;
                }
            std::vector<int> partners;
            for (int g : rebuild_groups) if (g != first) partners.push_back(g);
            const double first_center = center(first);
            std::stable_sort(partners.begin(), partners.end(), [&](int left, int right) {
                return std::make_pair(!preferred[left], std::abs(first_center - center(left)))
                    < std::make_pair(!preferred[right], std::abs(first_center - center(right)));
            });
            if (partners.size() > static_cast<size_t>(partner_cap)) partners.resize(partner_cap);
            for (int second : partners) {
                auto keys = keys_by_group[first];
                keys.insert(keys.end(), keys_by_group[second].begin(), keys_by_group[second].end());
                if (keys.size() > 12) continue;
                std::vector<int> seats;
                double current = 0.0;
                for (int p : keys) { seats.push_back(state.passenger_to_seat[p]); current += passenger_score(p, seats.back()); }
                current += compact(group_seats[first]);
                current += compact(group_seats[second]);
                double best = current;
                std::vector<std::pair<int, int>> best_placement;
                for (unsigned mask = 1; mask < (1u << keys.size()) - 1; ++mask) {
                    if (expired()) break;
                    if (popcount(mask) != static_cast<int>(keys_by_group[first].size())) continue;
                    std::vector<int> left, right;
                    for (size_t i = 0; i < seats.size(); ++i) ((mask & (1u << i)) ? left : right).push_back(seats[i]);
                    const auto left_match = matching(keys_by_group[first], left);
                    const auto right_match = matching(keys_by_group[second], right);
                    const double value = left_match.first + right_match.first + compact(left) + compact(right);
                    ++diagnostics.evaluated_moves;
                    if (value > best + problem.rich.local_search_epsilon) {
                        best = value;
                        best_placement = left_match.second;
                        best_placement.insert(best_placement.end(), right_match.second.begin(), right_match.second.end());
                    }
                }
                if (best_placement.empty()) continue;
                const auto snapshot = state.save();
                for (int p : keys) state.remove(p);
                bool committed = true;
                for (const auto& item : best_placement) if (!state.assign(item.first, item.second)) { committed = false; break; }
                if (!committed) {
                    state.restore(snapshot);
                    for (int p : keys) state.remove(p);
                    for (int p : keys) state.assign(p, snapshot.passenger_to_seat[p]);
                    continue;
                }
                for (int g : {first, second}) {
                    group_seats[g].clear();
                    for (int p : keys_by_group[g]) group_seats[g].push_back(state.passenger_to_seat[p]);
                }
                ++diagnostics.accepted_moves;
                ++diagnostics.group_rebuilds;
                diagnostics.score_improvement += best - current;
                accepted = true;
                break;
            }
            if (accepted) break;
        }
        if (!accepted) break;
    }
    if (!include_caregiver_rebuild) return diagnostics;
    const auto requires_care = [&](int p) {
        const auto& item = problem.passengers[p];
        const auto rule = problem.ssr_rules.find(item.ssr);
        return item.need_cared || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
    };
    const auto individual = [&](int p, int seat) {
        return evaluate_rich_individual_score(problem, p, seat).total();
    };
    const auto baby_score = [&](const std::vector<int>& assignment) {
        return evaluate_score_components(problem, assignment).score_b;
    };
    double baseline_baby = baby_score(state.passenger_to_seat);
    for (int care_group : group_order) {
        const auto& care_keys = keys_by_group[care_group];
        if (!std::any_of(care_keys.begin(), care_keys.end(), requires_care)
            || !std::all_of(care_keys.begin(), care_keys.end(), [&](int p) {
                const auto& item = problem.passengers[p];
                return item.fixed_seat.empty() && !item.need_both_empty && !item.need_single_empty;
            })) continue;
        if (expired()) break;
        const double care_center = center(care_group);
        std::vector<bool> preferred(problem.groups.size(), false);
        for (int p : care_keys)
            for (int i = 0; i < std::min(related_cap, static_cast<int>(rankings[p].size())); ++i) {
                const int owner = state.seat_to_passenger[rankings[p][i]];
                if (owner >= 0 && problem.passengers[owner].group != care_group) preferred[problem.passengers[owner].group] = true;
            }
        std::vector<int> partners;
        for (int g : rebuild_groups) if (g != care_group && care_keys.size() + keys_by_group[g].size() <= 7) partners.push_back(g);
        std::stable_sort(partners.begin(), partners.end(), [&](int left, int right) {
            return std::make_pair(!preferred[left], std::abs(care_center - center(left)))
                < std::make_pair(!preferred[right], std::abs(care_center - center(right)));
        });
        if (partners.size() > static_cast<size_t>(partner_cap)) partners.resize(partner_cap);
        for (int ordinary_group : partners) {
            if (expired()) break;
            const auto& ordinary_keys = keys_by_group[ordinary_group];
            auto joint = care_keys;
            joint.insert(joint.end(), ordinary_keys.begin(), ordinary_keys.end());
            std::vector<int> seats;
            double current = 0.0;
            for (int p : joint) { seats.push_back(state.passenger_to_seat[p]); current += individual(p, seats.back()); }
            current += compact(group_seats[care_group]);
            current += compact(group_seats[ordinary_group]);
            current += baseline_baby;
            double best = current;
            std::vector<int> best_assignment;
            AssignmentState external(problem);
            external.restore(state.save());
            // These groups contain no protection users; removing them exactly
            // excludes their occupied and SSR seats without changing blocks.
            for (int p : joint) external.remove(p);
            std::vector<int> permutation(joint.size());
            std::iota(permutation.begin(), permutation.end(), 0);
            do {
                if (expired()) break;
                auto proposal = state.passenger_to_seat;
                bool feasible = true;
                for (size_t i = 0; i < joint.size(); ++i) {
                    proposal[joint[i]] = seats[permutation[i]];
                    if (!external.rich_seat_feasible(joint[i], proposal[joint[i]])) { feasible = false; break; }
                }
                if (!feasible) continue;
                for (int p : care_keys) {
                    if (!requires_care(p)) continue;
                    const auto rule = problem.ssr_rules.find(problem.passengers[p].ssr);
                    const bool cross = rule != problem.ssr_rules.end() && rule->second.caregiver_allow_cross_aisle;
                    const auto& seat = problem.seats[proposal[p]];
                    const auto& neighbors = cross ? seat.row_neighbors : seat.same_block_neighbors;
                    if (!std::any_of(care_keys.begin(), care_keys.end(), [&](int other) {
                        return other != p && problem.passengers[other].ssr.empty()
                            && std::find(neighbors.begin(), neighbors.end(), proposal[other]) != neighbors.end();
                    })) { feasible = false; break; }
                }
                if (!feasible) continue;
                double value = 0.0;
                for (int p : joint) value += individual(p, proposal[p]);
                for (int g : {care_group, ordinary_group}) {
                    std::vector<int> group_seat_list;
                    for (int p : keys_by_group[g]) group_seat_list.push_back(proposal[p]);
                    value += compact(group_seat_list);
                }
                value += baby_score(proposal);
                ++diagnostics.evaluated_moves;
                if (value > best + problem.rich.local_search_epsilon) { best = value; best_assignment = std::move(proposal); }
            } while (std::next_permutation(permutation.begin(), permutation.end()));
            if (best_assignment.empty()) continue;
            const auto snapshot = state.save();
            for (int p : joint) state.remove(p);
            auto submission = joint;
            std::stable_sort(submission.begin(), submission.end(), [&](int left, int right) { return requires_care(left) < requires_care(right); });
            bool committed = true;
            for (int p : submission) if (!state.assign(p, best_assignment[p])) { committed = false; break; }
            if (!committed) {
                for (int p : joint) state.remove(p);
                for (int p : submission) state.assign(p, snapshot.passenger_to_seat[p]);
                continue;
            }
            for (int g : {care_group, ordinary_group}) {
                group_seats[g].clear();
                for (int p : keys_by_group[g]) group_seats[g].push_back(state.passenger_to_seat[p]);
            }
            baseline_baby = baby_score(state.passenger_to_seat);
            ++diagnostics.accepted_moves;
            ++diagnostics.caregiver_rebuilds;
            diagnostics.score_improvement += best - current;
            break;
        }
    }
    diagnostics.stopped_by_deadline = expired();
    return diagnostics;
}

}  // namespace full_cpp
