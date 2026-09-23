#include "native_group_constructor.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <numeric>
#include <tuple>

namespace full_cpp {

RichRemainingDiagnostics assign_rich_remaining(
    const Problem& problem, AssignmentState& state, const RichCandidateCache& cache,
    const std::vector<int>& group_indices,
    std::chrono::steady_clock::time_point global_deadline
) {
    using Clock = std::chrono::steady_clock;
    const auto deadline = std::min(global_deadline, Clock::now()
        + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(
            std::max(0.0, problem.rich.stage3_time_budget))));
    RichRemainingDiagnostics diagnostics;
    const auto needs_care = [&](int p) {
        const auto& passenger = problem.passengers[p];
        const auto rule = problem.ssr_rules.find(passenger.ssr);
        return passenger.need_cared || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
    };
    const auto priority = [&](int g) {
        int assigned = 0, minimum = 1000000000, protection = 0, care = 0;
        for (int p : problem.groups[g].passengers) {
            assigned += state.passenger_to_seat[p] >= 0;
            care += needs_care(p);
            if (needs_care(p) || state.passenger_to_seat[p] >= 0) continue;
            int domain = 0;
            for (int seat : cache.rankings[p]) domain += state.rich_seat_feasible(p, seat);
            minimum = std::min(minimum, domain);
            protection += problem.passengers[p].need_both_empty ? 2 : problem.passengers[p].need_single_empty ? 1 : 0;
        }
        return std::make_tuple(assigned > 0 ? 0 : 1, minimum, -protection, -care,
            -static_cast<int>(problem.groups[g].passengers.size()));
    };
    auto groups = group_indices;
    std::stable_sort(groups.begin(), groups.end(), [&](int left, int right) { return priority(left) < priority(right); });
    const auto old_position = [&](int p) {
        const auto found = problem.seat_index.find(problem.passengers[p].old_seat);
        if (found == problem.seat_index.end()) return std::make_pair(999, 999);
        const auto& seat = problem.seats[found->second];
        int column = 0;
        for (char c : seat.column) column = column * 26 + (c - 'A' + 1);
        return std::make_pair(seat.row, column);
    };
    struct Move { int passenger, seat, block; };
    struct Branch {
        AssignmentSnapshot snapshot;
        std::vector<Move> moves;
        double cost = 0.0, compactness = 0.0;
    };
    for (int g : groups) {
        const auto& group = problem.groups[g];
        std::vector<int> remaining, anchors;
        for (int p : group.passengers) {
            if (state.passenger_to_seat[p] >= 0) anchors.push_back(state.passenger_to_seat[p]);
            else if (!needs_care(p)) remaining.push_back(p);
        }
        if (remaining.empty()) continue;
        ++diagnostics.groups_considered;
        std::stable_sort(remaining.begin(), remaining.end(), [&](int left, int right) {
            const auto& l = problem.passengers[left];
            const auto& r = problem.passengers[right];
            return std::make_tuple(!l.need_both_empty, !l.need_single_empty, old_position(left))
                < std::make_tuple(!r.need_both_empty, !r.need_single_empty, old_position(right));
        });
        const auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets);
        const int cap = problem.rich.candidate_cap;
        const int retry_cap = std::max(cap, problem.rich.candidate_cap_retry);
        const int full_cap = std::max(retry_cap, problem.rich.candidate_cap_full_retry);
        std::vector<std::vector<int>> candidates(problem.passengers.size());
        // Python slicing retains all but the final |cap| elements for negative caps.
        const auto slice_size = [](size_t size, int limit) {
            return limit >= 0 ? std::min(size, static_cast<size_t>(limit))
                : static_cast<size_t>(std::max(0LL, static_cast<long long>(size) + limit));
        };
        for (int p : remaining) {
            for (int seat : costs.rankings[p])
                if (state.rich_seat_feasible(p, seat)) candidates[p].push_back(seat);
            candidates[p].resize(full_cap <= 0 ? 0 : std::min(candidates[p].size(), static_cast<size_t>(full_cap)));
        }
        const auto regret = [&](int p) {
            return slice_size(candidates[p].size(), cap) >= 2
                ? costs.costs[p][candidates[p][1]] - costs.costs[p][candidates[p][0]]
                : std::numeric_limits<double>::infinity();
        };
        std::stable_sort(remaining.begin(), remaining.end(), [&](int left, int right) {
            const auto& l = problem.passengers[left];
            const auto& r = problem.passengers[right];
            return std::make_tuple(!l.need_both_empty, !l.need_single_empty,
                       slice_size(candidates[left].size(), cap), -regret(left), old_position(left))
                < std::make_tuple(!r.need_both_empty, !r.need_single_empty,
                       slice_size(candidates[right].size(), cap), -regret(right), old_position(right));
        });
        const auto compactness = [&](const std::vector<Move>& moves, int extra = -1) {
            auto seats = anchors;
            for (const auto& move : moves) seats.push_back(move.seat);
            if (extra >= 0) seats.push_back(extra);
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
        const auto commit = [&](const std::vector<Move>& moves) {
            const auto before = state.save();
            for (const auto& move : moves) {
                if (!state.assign(move.passenger, move.seat, move.block)) {
                    state.restore(before);
                    ++diagnostics.transaction_failures;
                    return false;
                }
            }
            return true;
        };
        AssignmentState local(problem);
        local.restore(state.save());
        // Static candidate filtering already checked global SSR constraints.
        // Python's branch check tracks only newly selected SSRs; commit checks
        // the combined global state transactionally.
        std::fill(local.seat_ssr_passenger.begin(), local.seat_ssr_passenger.end(), -1);
        const Branch initial{local.save(), {}, 0.0, compactness({})};
        const auto blocks = [&](int p, int seat) {
            std::vector<int> result;
            if (!local.can_assign(p, seat)) return result;
            if (problem.passengers[p].need_single_empty && !problem.passengers[p].need_both_empty) {
                for (int neighbor : problem.seats[seat].same_block_neighbors)
                    if (local.can_assign(p, seat, neighbor)) result.push_back(neighbor);
            } else result.push_back(-1);
            return result;
        };
        const auto care_satisfied = [&]() {
            for (int p : group.passengers) {
                const int seat = local.passenger_to_seat[p];
                if (seat < 0 || !needs_care(p)) continue;
                const auto rule = problem.ssr_rules.find(problem.passengers[p].ssr);
                const bool cross = rule != problem.ssr_rules.end() && rule->second.caregiver_allow_cross_aisle;
                const auto& neighbors = cross ? problem.seats[seat].row_neighbors : problem.seats[seat].same_block_neighbors;
                bool found = false;
                for (int other : group.passengers) {
                    const auto& passenger = problem.passengers[other];
                    if (passenger.ssr.empty() && !passenger.need_cared && !passenger.need_both_empty && !passenger.need_single_empty
                        && std::find(neighbors.begin(), neighbors.end(), local.passenger_to_seat[other]) != neighbors.end()) found = true;
                }
                if (!found) return false;
            }
            return true;
        };
        std::vector<Move> best, selected;
        double best_cost = std::numeric_limits<double>::infinity();
        if (problem.rich.small_group_dfs_enabled && remaining.size() <= static_cast<size_t>(std::max(1, problem.rich.small_group_dfs_max_size))
            && problem.rich.small_group_dfs_time_limit > 0.0 && Clock::now() < deadline) {
            ++diagnostics.dfs_attempted;
            const auto dfs_deadline = std::min(deadline, Clock::now()
                + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(problem.rich.small_group_dfs_time_limit)));
            long long nodes = 0;
            const long long limit = std::max(1LL, problem.rich.small_group_dfs_node_limit);
            const auto dfs = [&](auto&& self, size_t depth, double cost, int active_cap) -> void {
                ++nodes;
                if (nodes > limit || Clock::now() >= dfs_deadline) return;
                if (depth == remaining.size()) {
                    if (!care_satisfied()) return;
                    const double total = cost - problem.weight_c * compactness(selected);
                    if (total < best_cost) { best_cost = total; best = selected; }
                    return;
                }
                const int p = remaining[depth];
                const auto before = local.save();
                for (size_t i = 0; i < slice_size(candidates[p].size(), active_cap); ++i) {
                    const int seat = candidates[p][i];
                    for (int block : blocks(p, seat)) {
                        if (!local.assign(p, seat, block)) continue;
                        selected.push_back({p, seat, block});
                        self(self, depth + 1, cost + costs.costs[p][seat], active_cap);
                        selected.pop_back();
                        local.restore(before);
                    }
                }
            };
            std::vector<int> caps;
            for (int value : {cap, retry_cap, full_cap})
                if (std::find(caps.begin(), caps.end(), value) == caps.end()) caps.push_back(value);
            for (int active_cap : caps) {
                dfs(dfs, 0, 0.0, active_cap);
                if (!best.empty() || nodes > limit || Clock::now() >= dfs_deadline) break;
            }
            diagnostics.dfs_nodes += nodes;
        }
        if (!best.empty() && commit(best)) { ++diagnostics.dfs_succeeded; continue; }
        ++diagnostics.beam_groups;
        const int width = std::max(1, remaining.size() <= 2 ? problem.rich.beam_width_small
            : remaining.size() <= 5 ? problem.rich.beam_width_medium : problem.rich.beam_width_large);
        const int moves_limit = std::max(1, remaining.size() <= 2 ? problem.rich.beam_moves_small
            : remaining.size() <= 5 ? problem.rich.beam_moves_medium : problem.rich.beam_moves_large);
        const auto objective = [&](const Branch& branch) {
            return std::make_pair(remaining.size() - branch.moves.size(), branch.cost - problem.weight_c * branch.compactness);
        };
        std::vector<Branch> beam{initial};
        for (int p : remaining) {
            if (Clock::now() >= deadline) break;
            std::vector<Branch> expanded;
            for (const auto& branch : beam) {
                expanded.push_back(branch);
                local.restore(branch.snapshot);
                std::vector<std::pair<double, int>> moves;
                for (size_t i = 0; i < slice_size(candidates[p].size(), cap); ++i) {
                    const int seat = candidates[p][i];
                    if (blocks(p, seat).empty()) continue;
                    moves.emplace_back(costs.costs[p][seat] - problem.weight_c * (compactness(branch.moves, seat) - branch.compactness), seat);
                }
                std::stable_sort(moves.begin(), moves.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
                if (moves.size() > static_cast<size_t>(moves_limit)) moves.resize(moves_limit);
                for (const auto& move : moves) {
                    for (int block : blocks(p, move.second)) {
                        local.assign(p, move.second, block);
                        Branch next{local.save(), branch.moves, branch.cost + costs.costs[p][move.second], 0.0};
                        next.moves.push_back({p, move.second, block});
                        next.compactness = compactness(next.moves);
                        expanded.push_back(std::move(next));
                        local.restore(branch.snapshot);
                    }
                }
            }
            using Key = std::tuple<std::vector<int>, std::vector<int>, std::vector<std::tuple<std::string, std::string, bool, bool>>>;
            std::map<Key, size_t> positions;
            std::vector<Branch> unique;
            for (auto& branch : expanded) {
                std::vector<int> occupied, blocked;
                std::vector<std::tuple<std::string, std::string, bool, bool>> ssrs;
                for (int seat = 0; seat < static_cast<int>(problem.seats.size()); ++seat) {
                    if (branch.snapshot.seat_to_passenger[seat] >= 0) occupied.push_back(seat);
                    if (branch.snapshot.blocked_count[seat] > 0) blocked.push_back(seat);
                    const int p_ssr = branch.snapshot.seat_ssr_passenger[seat];
                    if (p_ssr >= 0) {
                        const auto& passenger = problem.passengers[p_ssr];
                        ssrs.emplace_back(problem.seats[seat].id, passenger.ssr, passenger.same_subrow_no_other_ssr, passenger.same_row_no_other_ssr);
                    }
                }
                std::sort(ssrs.begin(), ssrs.end());
                const Key key{occupied, blocked, ssrs};
                const auto inserted = positions.emplace(key, unique.size());
                if (inserted.second) unique.push_back(std::move(branch));
                else if (objective(branch) < objective(unique[inserted.first->second])) unique[inserted.first->second] = std::move(branch);
            }
            std::stable_sort(unique.begin(), unique.end(), [&](const Branch& a, const Branch& b) { return objective(a) < objective(b); });
            if (unique.size() > static_cast<size_t>(width)) unique.resize(width);
            beam = std::move(unique);
            if (beam.empty()) beam.push_back(initial);
        }
        std::stable_sort(beam.begin(), beam.end(), [&](const Branch& a, const Branch& b) { return objective(a) < objective(b); });
        for (const auto& branch : beam) if (commit(branch.moves)) break;
    }
    return diagnostics;
}

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
            auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets);
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
                costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets);
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
            const auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets);
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
            const auto costs = build_rich_candidate_cache(problem, state, &cache.owner_regrets);
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

RichConstructionDiagnostics construct_rich_assignment(
    const Problem& problem, AssignmentState& state, const RichCandidateCache& cache,
    std::chrono::steady_clock::time_point construction_deadline
) {
    RichConstructionDiagnostics diagnostics;
    std::vector<int> anchored, groups;
    for (int g = 0; g < static_cast<int>(problem.groups.size()); ++g) {
        groups.push_back(g);
        if (std::any_of(problem.groups[g].passengers.begin(), problem.groups[g].passengers.end(),
                [&](int p) { return state.passenger_to_seat[p] >= 0; })) anchored.push_back(g);
    }
    const auto capture = [&](const RichRemainingDiagnostics& update) {
        diagnostics.search.groups_considered += update.groups_considered;
        diagnostics.search.transaction_failures += update.transaction_failures;
        diagnostics.search.dfs_attempted += update.dfs_attempted;
        diagnostics.search.dfs_succeeded += update.dfs_succeeded;
        diagnostics.search.dfs_nodes += update.dfs_nodes;
        diagnostics.search.beam_groups += update.beam_groups;
    };
    if (!anchored.empty()) capture(assign_rich_remaining(problem, state, cache, anchored, construction_deadline));
    while (diagnostics.paired_passes < 4 && std::chrono::steady_clock::now() < construction_deadline) {
        const int added = assign_rich_paired_ssrs(problem, state, cache, construction_deadline);
        ++diagnostics.paired_passes;
        if (added <= 0) break;
    }
    diagnostics.rescue = rescue_rich_paired_ssrs(problem, state, cache, construction_deadline);
    capture(assign_rich_remaining(problem, state, cache, groups, construction_deadline));
    return diagnostics;
}

RichOrdinaryVndDiagnostics improve_rich_ordinary_vnd(
    AssignmentState& state, const std::vector<std::vector<int>>& rankings,
    std::chrono::steady_clock::time_point deadline
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
    // Infant positions are frozen in this ordinary-only prefix, as in Python.
    const auto initial = state.passenger_to_seat;
    std::map<std::pair<int, int>, double> passenger_cache;
    const auto passenger_score = [&](int p, int s) {
        const auto key = std::make_pair(p, s);
        const auto found = passenger_cache.find(key);
        if (found != passenger_cache.end()) return found->second;
        std::vector<int> isolated(n, -1);
        isolated[p] = s;
        for (int infant = 0; infant < n; ++infant)
            if (problem.passengers[infant].ssr == "BSCT"
                && problem.passengers[infant].group != problem.passengers[p].group) isolated[infant] = initial[infant];
        const double value = evaluate_rich_group_score(problem, isolated, problem.passengers[p].group).total();
        passenger_cache[key] = value;
        return value;
    };
    std::vector<std::vector<int>> group_seats(problem.groups.size());
    for (int p = 0; p < n; ++p) if (state.passenger_to_seat[p] >= 0)
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
            delta += compact(seats) - compact(group_seats[group.first]);
            updated[group.first] = std::move(seats);
        }
        if (delta <= problem.rich.local_search_epsilon) return false;
        const auto snapshot = state.save();
        for (int p : passengers) state.remove(p);
        for (size_t i = 0; i < passengers.size(); ++i) if (!state.assign(passengers[i], targets[i])) {
            state.restore(snapshot); return false;
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
    return diagnostics;
}

}  // namespace full_cpp
