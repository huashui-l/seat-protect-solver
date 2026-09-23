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

}  // namespace full_cpp
