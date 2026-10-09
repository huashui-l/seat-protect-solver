#include "native_group_constructor.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <numeric>
#include <tuple>

namespace full_cpp {

int assign_rich_paired_ssrs(
    const Problem& problem, AssignmentState& state, const RichCandidateCache& cache,
    std::chrono::steady_clock::time_point deadline
) {
    const int before = static_cast<int>(std::count_if(state.passenger_to_seat.begin(), state.passenger_to_seat.end(),
        [](int seat) { return seat >= 0; }));
    const auto needs_care = [&](int p) {
        const auto& passenger = problem.passengers[p];
        const auto rule = problem.ssr_rules.find(passenger.ssr);
        return passenger.need_cared || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
    };
    const auto adult = [&](int p) {
        const auto& passenger = problem.passengers[p];
        return passenger.ssr.empty() && !passenger.need_cared && !passenger.need_both_empty && !passenger.need_single_empty;
    };
    const auto neighbors = [&](int p, int seat) -> const std::vector<int>& {
        const auto rule = problem.ssr_rules.find(problem.passengers[p].ssr);
        const bool cross = rule != problem.ssr_rules.end() && rule->second.caregiver_allow_cross_aisle;
        return cross ? problem.seats[seat].row_neighbors : problem.seats[seat].same_block_neighbors;
    };
    const auto compactness = [&](const std::vector<int>& seats) {
        if (seats.size() <= 1) return 0.0;
        double x = 0.0, y = 0.0;
        for (int seat : seats) { x += problem.seats[seat].x; y += problem.seats[seat].y; }
        x /= seats.size(); y /= seats.size();
        double dx = 0.0, dy = 0.0;
        for (int seat : seats) {
            dx = std::max(dx, std::abs(problem.seats[seat].x - x));
            dy = std::max(dy, std::abs(problem.seats[seat].y - y));
        }
        return problem.group_centroid_x_factor * dx + problem.group_centroid_y_factor * dy;
    };
    std::vector<int> groups(problem.groups.size());
    std::iota(groups.begin(), groups.end(), 0);
    const auto priority = [&](int g) {
        bool baby = false;
        int care = 0;
        for (int p : problem.groups[g].passengers) {
            care += needs_care(p);
            baby = baby || (needs_care(p) && problem.passengers[p].ssr == "BSCT");
        }
        return std::make_tuple(baby ? 0 : 1, -care, problem.groups[g].id);
    };
    std::stable_sort(groups.begin(), groups.end(), [&](int a, int b) { return priority(a) < priority(b); });
    for (int g : groups) {
        if (std::chrono::steady_clock::now() >= deadline) break;
        std::vector<int> caregivers;
        for (int p : problem.groups[g].passengers) if (adult(p)) caregivers.push_back(p);
        for (int p : problem.groups[g].passengers) {
            if (!needs_care(p)) continue;
            if (std::chrono::steady_clock::now() >= deadline) break;
            auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets, g);
            if (state.passenger_to_seat[p] >= 0) {
                const int seat = state.passenger_to_seat[p];
                const auto& adjacent = neighbors(p, seat);
                bool satisfied = false;
                for (int cg : caregivers) {
                    if (std::find(adjacent.begin(), adjacent.end(), state.passenger_to_seat[cg]) != adjacent.end()) satisfied = true;
                }
                if (satisfied) continue;
                int best_caregiver = -1, best_seat = -1;
                double best_cost = std::numeric_limits<double>::infinity();
                for (int cg : caregivers) {
                    if (state.passenger_to_seat[cg] >= 0) continue;
                    for (int neighbor : adjacent) {
                        if (state.rich_seat_feasible(cg, neighbor, -1, seat) && costs.costs[cg][neighbor] < best_cost) {
                            best_cost = costs.costs[cg][neighbor]; best_caregiver = cg; best_seat = neighbor;
                        }
                    }
                }
                if (best_caregiver >= 0) {
                    state.assign(best_caregiver, best_seat, -1, seat);
                    continue;
                }
                if (!problem.passengers[p].fixed_seat.empty()) continue;
                state.remove(p);
                costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets, g);
            }
            std::vector<int> assigned;
            for (int other : problem.groups[g].passengers)
                if (state.passenger_to_seat[other] >= 0) assigned.push_back(state.passenger_to_seat[other]);
            const double old_compactness = compactness(assigned);
            int best_seat = -1, best_caregiver_seat = -1;
            double best_cost = std::numeric_limits<double>::infinity();
            for (int cg : caregivers) {
                const int caregiver_seat = state.passenger_to_seat[cg];
                if (caregiver_seat < 0) continue;
                for (int neighbor : neighbors(p, caregiver_seat)) {
                    if (!state.rich_seat_feasible(p, neighbor, -1, caregiver_seat)) continue;
                    auto expanded = assigned;
                    expanded.push_back(neighbor);
                    const double cost = costs.costs[p][neighbor] - problem.weight_c * (compactness(expanded) - old_compactness);
                    if (cost < best_cost) { best_cost = cost; best_seat = neighbor; best_caregiver_seat = caregiver_seat; }
                }
            }
            if (best_seat >= 0 && state.assign(p, best_seat, -1, best_caregiver_seat)) continue;
            std::vector<int> unassigned;
            for (int cg : caregivers) if (state.passenger_to_seat[cg] < 0) unassigned.push_back(cg);
            if (unassigned.empty()) continue;
            struct Pair { double cost; int seat, caregiver_seat, caregiver; };
            std::vector<Pair> pairs;
            int feasible_count = 0;
            for (int seat : cache.rankings[p]) {
                if (std::chrono::steady_clock::now() >= deadline) break;
                if (!state.rich_seat_feasible(p, seat)) continue;
                ++feasible_count;
                for (int neighbor : neighbors(p, seat)) {
                    for (int cg : unassigned) {
                        if (!state.rich_seat_feasible(cg, neighbor, -1, seat)) continue;
                        double penalty = 0.0;
                        if (!assigned.empty()) {
                            auto expanded = assigned;
                            expanded.push_back(seat); expanded.push_back(neighbor);
                            penalty = -problem.weight_c * (compactness(expanded) - old_compactness);
                        }
                        pairs.push_back({costs.costs[p][seat] + costs.costs[cg][neighbor] + penalty, seat, neighbor, cg});
                    }
                }
                if (feasible_count >= 500) break;
            }
            std::stable_sort(pairs.begin(), pairs.end(), [](const Pair& a, const Pair& b) { return a.cost < b.cost; });
            for (const auto& pair : pairs) {
                if (state.passenger_to_seat[pair.caregiver] >= 0) continue;
                if (state.assign(pair.caregiver, pair.caregiver_seat, -1, pair.seat)) {
                    if (state.assign(p, pair.seat, -1, pair.caregiver_seat)) break;
                    state.remove(pair.caregiver);
                }
            }
        }
    }
    const int after = static_cast<int>(std::count_if(state.passenger_to_seat.begin(), state.passenger_to_seat.end(),
        [](int seat) { return seat >= 0; }));
    return std::max(0, after - before);
}

RichPairedRescueDiagnostics rescue_rich_paired_ssrs(
    const Problem& problem, AssignmentState& state, const RichCandidateCache& cache,
    std::chrono::steady_clock::time_point deadline
) {
    RichPairedRescueDiagnostics diagnostics;
    const auto expired = [&]() { return std::chrono::steady_clock::now() >= deadline; };
    const auto needs_care = [&](int p) {
        const auto& passenger = problem.passengers[p];
        const auto rule = problem.ssr_rules.find(passenger.ssr);
        return passenger.need_cared || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
    };
    const auto adult = [&](int p) {
        const auto& passenger = problem.passengers[p];
        return passenger.ssr.empty() && !passenger.need_cared && !passenger.need_both_empty && !passenger.need_single_empty;
    };
    const auto neighbors = [&](int p, int seat) -> const std::vector<int>& {
        const auto rule = problem.ssr_rules.find(problem.passengers[p].ssr);
        const bool cross = rule != problem.ssr_rules.end() && rule->second.caregiver_allow_cross_aisle;
        return cross ? problem.seats[seat].row_neighbors : problem.seats[seat].same_block_neighbors;
    };
    const auto satisfied = [&](int p) {
        const int seat = state.passenger_to_seat[p];
        if (seat < 0) return false;
        const auto& adjacent = neighbors(p, seat);
        for (int cg : problem.groups[problem.passengers[p].group].passengers)
            if (cg != p && adult(cg)
                && std::find(adjacent.begin(), adjacent.end(), state.passenger_to_seat[cg]) != adjacent.end()) return true;
        return false;
    };
    using Placement = std::pair<int, int>;
    const auto rebuild = [&](const Group& group) {
        std::vector<Placement> original;
        for (int p : group.passengers) {
            if (state.passenger_to_seat[p] >= 0) {
                original.emplace_back(p, state.passenger_to_seat[p]);
                state.remove(p);
            }
        }
        const int cap = std::max(24, problem.rich.paired_joint_rebuild_candidate_cap);
        const long long limit = std::max(1LL, problem.rich.paired_joint_rebuild_node_limit);
        std::vector<std::vector<int>> candidates(problem.passengers.size());
        std::vector<int> caregivers;
        for (int p : group.passengers) {
            const auto& fixed = problem.passengers[p].fixed_seat;
            if (!fixed.empty()) {
                const auto seat = problem.seat_index.find(fixed);
                if (seat != problem.seat_index.end()) candidates[p].push_back(seat->second);
            } else {
                candidates[p] = cache.rankings[p];
                if (candidates[p].size() > static_cast<size_t>(cap)) candidates[p].resize(cap);
            }
            if (adult(p)) caregivers.push_back(p);
        }
        std::vector<Placement> selected, solution;
        bool found = false;
        long long nodes = 0;
        const auto partial_care_feasible = [&](int p, int seat) {
            if (caregivers.size() != 1) return true;
            const int cg = caregivers.front();
            if (p == cg) {
                for (const auto& placement : selected) {
                    if (!needs_care(placement.first)) continue;
                    const auto& adjacent = neighbors(placement.first, placement.second);
                    if (std::find(adjacent.begin(), adjacent.end(), seat) == adjacent.end()) return false;
                }
            } else if (needs_care(p) && state.passenger_to_seat[cg] >= 0) {
                const auto& adjacent = neighbors(p, seat);
                if (std::find(adjacent.begin(), adjacent.end(), state.passenger_to_seat[cg]) == adjacent.end()) return false;
            }
            return true;
        };
        const auto search = [&](auto&& self, const std::vector<int>& remaining) -> void {
            if (found) return;
            if (++nodes > limit || expired()) return;
            if (remaining.empty()) {
                for (int p : group.passengers) if (needs_care(p) && !satisfied(p)) return;
                solution = selected; found = true; return;
            }
            int best = -1;
            std::vector<int> feasible;
            for (int p : remaining) {
                std::vector<int> options;
                for (int seat : candidates[p])
                    if (state.rich_seat_feasible(p, seat) && partial_care_feasible(p, seat)) options.push_back(seat);
                if (best < 0 || std::make_tuple(options.size(), !needs_care(p), problem.passengers[p].hostnum)
                    < std::make_tuple(feasible.size(), !needs_care(best), problem.passengers[best].hostnum)) {
                    best = p; feasible = std::move(options);
                }
            }
            if (feasible.empty()) return;
            auto next = remaining;
            next.erase(std::find(next.begin(), next.end(), best));
            for (int seat : feasible) {
                if (state.assign(best, seat)) {
                    selected.emplace_back(best, seat);
                    self(self, next);
                    state.remove(best); selected.pop_back();
                    if (found) return;
                }
            }
        };
        search(search, group.passengers);
        const auto restore = [&](std::vector<Placement> placements) {
            std::stable_sort(placements.begin(), placements.end(), [&](const Placement& a, const Placement& b) {
                return needs_care(a.first) < needs_care(b.first);
            });
            bool restored = true;
            for (const auto& placement : placements)
                restored = state.assign(placement.first, placement.second) && restored;
            return restored;
        };
        if (!found) { restore(original); return false; }
        if (restore(solution)) return true;
        for (const auto& placement : solution)
            if (state.passenger_to_seat[placement.first] >= 0) state.remove(placement.first);
        restore(original);
        return false;
    };
    struct Option { double cost; int caregiver, seat, caregiver_seat; };
    struct TaskPlacement { int passenger, caregiver, seat, caregiver_seat; };
    for (const auto& group : problem.groups) {
        if (expired()) break;
        std::vector<int> fixed_unsatisfied;
        for (int p : group.passengers)
            if (needs_care(p) && !problem.passengers[p].fixed_seat.empty()
                && state.passenger_to_seat[p] >= 0 && !satisfied(p)) fixed_unsatisfied.push_back(p);
        for (int p : fixed_unsatisfied) {
            diagnostics.attempted.push_back(p);
            const int seat = state.passenger_to_seat[p];
            const auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets, problem.passengers[p].group);
            struct Candidate { double cost; int caregiver, seat, old_seat, displaced, new_seat; };
            std::vector<Candidate> candidates;
            for (int cg : group.passengers) {
                if (!adult(cg)) continue;
                const int old = state.passenger_to_seat[cg];
                if (!problem.passengers[cg].fixed_seat.empty() && old >= 0) continue;
                for (int cg_seat : neighbors(p, seat)) {
                    const int displaced = state.seat_to_passenger[cg_seat];
                    int new_seat = -1;
                    if (displaced >= 0 && cg_seat != old) {
                        if (!problem.passengers[displaced].fixed_seat.empty() || !adult(displaced)) continue;
                        for (int candidate : cache.rankings[displaced]) {
                            if (candidate != cg_seat && state.rich_seat_feasible(displaced, candidate)) { new_seat = candidate; break; }
                        }
                        if (new_seat < 0) continue;
                    }
                    candidates.push_back({costs.costs[cg][cg_seat], cg, cg_seat, old, displaced, new_seat});
                }
            }
            std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) { return a.cost < b.cost; });
            bool rescued = false;
            for (const auto& candidate : candidates) {
                if (expired()) break;
                const auto before = state.save();
                if (candidate.old_seat >= 0) state.remove(candidate.caregiver);
                if (candidate.displaced >= 0) state.remove(candidate.displaced);
                const bool caregiver_ok = state.assign(candidate.caregiver, candidate.seat, -1, seat);
                const bool displaced_ok = candidate.displaced < 0
                    || (caregiver_ok && state.assign(candidate.displaced, candidate.new_seat));
                if (caregiver_ok && displaced_ok) { rescued = true; diagnostics.rescued.push_back(p); break; }
                state.restore(before);
            }
            if (!rescued) diagnostics.unresolved.push_back(p);
        }
        std::vector<int> tasks;
        for (int p : group.passengers) if (needs_care(p) && state.passenger_to_seat[p] < 0) tasks.push_back(p);
        if (tasks.empty()) continue;
        diagnostics.attempted.insert(diagnostics.attempted.end(), tasks.begin(), tasks.end());
        std::vector<TaskPlacement> plan, best;
        double best_cost = std::numeric_limits<double>::infinity();
        int nodes = 0;
        const int option_cap = problem.rich.paired_rescue_option_cap;
        const auto options_for = [&](int p) {
            std::vector<Option> options;
            const auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets, problem.passengers[p].group);
            for (int cg : group.passengers) {
                if (!adult(cg)) continue;
                if (expired()) break;
                const int cg_seat = state.passenger_to_seat[cg];
                if (cg_seat >= 0) {
                    for (int seat : neighbors(p, cg_seat))
                        if (state.rich_seat_feasible(p, seat, -1, cg_seat)) options.push_back({costs.costs[p][seat], cg, seat, -1});
                    continue;
                }
                for (int seat : cache.rankings[p]) {
                    if (expired()) break;
                    if (!state.rich_seat_feasible(p, seat)) continue;
                    for (int neighbor : neighbors(p, seat))
                        if (state.rich_seat_feasible(cg, neighbor, -1, seat))
                            options.push_back({costs.costs[p][seat] + costs.costs[cg][neighbor], cg, seat, neighbor});
                    if (static_cast<int>(options.size()) >= option_cap * 3) break;
                }
            }
            std::stable_sort(options.begin(), options.end(), [](const Option& a, const Option& b) { return a.cost < b.cost; });
            if (option_cap <= 0) options.clear();
            else if (options.size() > static_cast<size_t>(option_cap)) options.resize(option_cap);
            return options;
        };
        const auto search = [&](auto&& self, size_t index, double cost) -> void {
            if (++nodes > 1000 || expired()) return;
            if (index == tasks.size()) {
                if (plan.size() > best.size() || (plan.size() == best.size() && cost < best_cost)) {
                    best = plan; best_cost = cost;
                }
                return;
            }
            const int p = tasks[index];
            self(self, index + 1, cost);
            for (const auto& option : options_for(p)) {
                if (option.caregiver_seat >= 0 && !state.assign(option.caregiver, option.caregiver_seat, -1, option.seat)) continue;
                const int cg_seat = option.caregiver_seat >= 0 ? option.caregiver_seat : state.passenger_to_seat[option.caregiver];
                if (state.assign(p, option.seat, -1, cg_seat)) {
                    plan.push_back({p, option.caregiver, option.seat, option.caregiver_seat});
                    self(self, index + 1, cost + option.cost);
                    plan.pop_back(); state.remove(p);
                }
                if (option.caregiver_seat >= 0) state.remove(option.caregiver);
            }
        };
        search(search, 0, 0.0);
        for (const auto& placement : best) {
            if (placement.caregiver_seat >= 0) state.assign(placement.caregiver, placement.caregiver_seat, -1, placement.seat);
            const int cg_seat = state.passenger_to_seat[placement.caregiver];
            state.assign(placement.passenger, placement.seat, -1, cg_seat);
            diagnostics.rescued.push_back(placement.passenger);
        }
        std::vector<int> remaining;
        for (int p : tasks) if (state.passenger_to_seat[p] < 0) remaining.push_back(p);
        if (!remaining.empty() && rebuild(group)) {
            ++diagnostics.joint_rebuilds;
            diagnostics.rescued.insert(diagnostics.rescued.end(), remaining.begin(), remaining.end());
        }
        for (int p : tasks) if (state.passenger_to_seat[p] < 0) diagnostics.unresolved.push_back(p);
        for (int p : fixed_unsatisfied)
            if (!satisfied(p) && std::find(diagnostics.unresolved.begin(), diagnostics.unresolved.end(), p) == diagnostics.unresolved.end())
                diagnostics.unresolved.push_back(p);
    }
    return diagnostics;
}

}  // namespace full_cpp
