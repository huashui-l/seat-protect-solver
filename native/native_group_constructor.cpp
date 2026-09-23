#include "native_group_constructor.hpp"

#include "native_feasibility_solver.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <set>
#include <stdexcept>
#include <tuple>

namespace full_cpp {
namespace {

constexpr int kDfsMaxGroupSize = 4;
constexpr long long kDfsNodeLimit = 20000;
constexpr int kCandidateCap = 48;
constexpr double kTolerance = 1e-9;

double group_placement_score(
    const Problem& problem, const Group& group,
    const std::vector<int>& assignment
) {
    double score = 0.0;
    std::vector<int> seats;
    for (int passenger : group.passengers) {
        const int seat = assignment[passenger];
        if (seat < 0) continue;
        seats.push_back(seat);
        score += evaluate_individual_score(problem, passenger, seat).total();
    }
    if (seats.size() <= 1) return score;
    double center_x = 0.0, center_y = 0.0;
    for (int seat : seats) {
        center_x += problem.seats[seat].x;
        center_y += problem.seats[seat].y;
    }
    center_x /= seats.size();
    center_y /= seats.size();
    double max_x = 0.0, max_y = 0.0;
    for (int seat : seats) {
        max_x = std::max(max_x, std::abs(problem.seats[seat].x - center_x));
        max_y = std::max(max_y, std::abs(problem.seats[seat].y - center_y));
    }
    return score + problem.weight_c * (
        problem.group_centroid_x_factor * max_x
        + problem.group_centroid_y_factor * max_y
    );
}

std::vector<int> block_choices(
    const Problem& problem, const AssignmentState& state,
    int passenger, int seat
) {
    if (!problem.passengers[passenger].need_single_empty) return {-1};
    std::vector<int> choices;
    for (int neighbor : problem.seats[seat].same_block_neighbors) {
        if (state.can_assign(passenger, seat, neighbor)) choices.push_back(neighbor);
    }
    return choices;
}

AssignmentState state_without_group(
    const Problem& problem, const std::vector<int>& assignment, int excluded_group
) {
    AssignmentState state(problem);
    std::vector<bool> occupied(problem.seats.size(), false);
    for (int passenger = 0; passenger < static_cast<int>(assignment.size()); ++passenger) {
        if (problem.passengers[passenger].group == excluded_group) continue;
        const int seat = assignment[passenger];
        if (seat < 0 || occupied[seat]) throw std::runtime_error("invalid incumbent assignment");
        occupied[seat] = true;
        state.passenger_to_seat[passenger] = seat;
        state.seat_to_passenger[seat] = passenger;
        state.owner_group_by_seat[seat] = problem.passengers[passenger].group;
        if (!problem.passengers[passenger].ssr.empty()) {
            state.seat_ssr_passenger[seat] = passenger;
        }
    }
    std::set<int> reserved;
    for (int passenger = 0; passenger < static_cast<int>(assignment.size()); ++passenger) {
        if (problem.passengers[passenger].group == excluded_group) continue;
        const Passenger& item = problem.passengers[passenger];
        const int seat = assignment[passenger];
        if (!item.need_both_empty) continue;
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? problem.seats[seat].row_neighbors
            : problem.seats[seat].same_block_neighbors;
        for (int neighbor : neighbors) {
            if (occupied[neighbor] || !reserved.insert(neighbor).second) {
                throw std::runtime_error("invalid protected incumbent assignment");
            }
            ++state.blocked_count[neighbor];
            state.assigned_blocked[passenger].push_back(neighbor);
        }
    }
    std::vector<int> singles;
    for (int passenger = 0; passenger < static_cast<int>(assignment.size()); ++passenger) {
        if (problem.passengers[passenger].group != excluded_group
            && problem.passengers[passenger].need_single_empty) singles.push_back(passenger);
    }
    std::vector<int> owner(problem.seats.size(), -1);
    auto augment = [&](auto&& self, int passenger, std::vector<bool>& seen) -> bool {
        const int seat = assignment[passenger];
        for (int empty : problem.seats[seat].same_block_neighbors) {
            if (occupied[empty] || reserved.count(empty) || seen[empty]) continue;
            seen[empty] = true;
            if (owner[empty] < 0 || self(self, owner[empty], seen)) {
                owner[empty] = passenger;
                return true;
            }
        }
        return false;
    };
    for (int passenger : singles) {
        std::vector<bool> seen(problem.seats.size(), false);
        if (!augment(augment, passenger, seen)) {
            throw std::runtime_error("cannot reconstruct incumbent empty-seat reservations");
        }
    }
    for (int empty = 0; empty < static_cast<int>(owner.size()); ++empty) {
        if (owner[empty] < 0) continue;
        ++state.blocked_count[empty];
        state.assigned_blocked[owner[empty]].push_back(empty);
    }
    return state;
}

std::vector<int> passenger_order(
    const Problem& problem, const Group& group, const AssignmentState& state
) {
    std::vector<int> order = group.passengers;
    auto domain_size = [&](int passenger) {
        int count = 0;
        for (int seat = 0; seat < static_cast<int>(problem.seats.size()); ++seat) {
            count += state.can_assign(passenger, seat);
        }
        return count;
    };
    std::stable_sort(order.begin(), order.end(), [&](int left, int right) {
        const Passenger& a = problem.passengers[left];
        const Passenger& b = problem.passengers[right];
        return std::tuple(
            !a.need_both_empty, !a.need_single_empty, a.fixed_seat.empty(),
            domain_size(left), a.old_seat, a.hostnum
        ) < std::tuple(
            !b.need_both_empty, !b.need_single_empty, b.fixed_seat.empty(),
            domain_size(right), b.old_seat, b.hostnum
        );
    });
    return order;
}

std::vector<int> seat_candidates(
    const Problem& problem, const AssignmentState& state,
    const std::vector<int>& incumbent, int passenger
) {
    std::vector<int> seats;
    for (int seat = 0; seat < static_cast<int>(problem.seats.size()); ++seat) {
        if (state.can_assign(passenger, seat)) seats.push_back(seat);
    }
    std::sort(seats.begin(), seats.end(), [&](int left, int right) {
        const double left_score = evaluate_individual_score(problem, passenger, left).total();
        const double right_score = evaluate_individual_score(problem, passenger, right).total();
        if (std::abs(left_score - right_score) > kTolerance) return left_score > right_score;
        return problem.seats[left].id < problem.seats[right].id;
    });
    if (seats.size() > kCandidateCap) seats.resize(kCandidateCap);
    const int current = incumbent[passenger];
    if (current >= 0 && std::find(seats.begin(), seats.end(), current) == seats.end()
        && state.can_assign(passenger, current)) seats.push_back(current);
    return seats;
}

bool better_assignment(
    const Problem& problem, double score, const std::vector<int>& candidate,
    double best_score, const std::vector<int>& best
) {
    if (score > best_score + kTolerance) return true;
    if (std::abs(score - best_score) > kTolerance) return false;
    for (int passenger = 0; passenger < static_cast<int>(candidate.size()); ++passenger) {
        const std::string& left = problem.seats[candidate[passenger]].id;
        const std::string& right = problem.seats[best[passenger]].id;
        if (left != right) return left < right;
    }
    return false;
}

struct SearchResult {
    std::vector<int> assignment;
    double score = -std::numeric_limits<double>::infinity();
    long long nodes = 0;
};

void consider_complete(
    const Problem& problem, const AssignmentState& state, SearchResult& result
) {
    if (validate_complete_assignment(problem, state.passenger_to_seat) != 0) return;
    const double score = evaluate_soft_score(problem, state.passenger_to_seat);
    if (result.assignment.empty()
        || better_assignment(problem, score, state.passenger_to_seat,
                             result.score, result.assignment)) {
        result.assignment = state.passenger_to_seat;
        result.score = score;
    }
}

SearchResult search_dfs(
    const Problem& problem, const std::vector<int>& incumbent,
    AssignmentState state,
    const std::vector<int>& order,
    std::chrono::steady_clock::time_point deadline
) {
    SearchResult result;
    auto visit = [&](auto&& self, int depth) -> void {
        if (result.nodes >= kDfsNodeLimit
            || std::chrono::steady_clock::now() >= deadline) return;
        ++result.nodes;
        if (depth == static_cast<int>(order.size())) {
            consider_complete(problem, state, result);
            return;
        }
        const int passenger = order[depth];
        for (int seat : seat_candidates(problem, state, incumbent, passenger)) {
            for (int block : block_choices(problem, state, passenger, seat)) {
                const AssignmentSnapshot saved = state.save();
                if (state.assign(passenger, seat, block)) self(self, depth + 1);
                state.restore(saved);
                if (result.nodes >= kDfsNodeLimit
                    || std::chrono::steady_clock::now() >= deadline) return;
            }
        }
    };
    visit(visit, 0);
    return result;
}

struct BeamState {
    AssignmentSnapshot snapshot;
    std::vector<int> assignment;
    double placement_score = -std::numeric_limits<double>::infinity();
};

SearchResult search_beam(
    const Problem& problem, const std::vector<int>& incumbent,
    const Group& group, AssignmentState state,
    const std::vector<int>& order,
    std::chrono::steady_clock::time_point deadline
) {
    const int size = static_cast<int>(order.size());
    const int width = size <= 2 ? 24 : size <= 5 ? 40 : 96;
    const int move_limit = size <= 5 ? 20 : 28;
    SearchResult result;
    std::vector<BeamState> beam{{state.save(), state.passenger_to_seat, 0.0}};
    for (int passenger : order) {
        std::vector<BeamState> next;
        for (const BeamState& parent : beam) {
            state.restore(parent.snapshot);
            std::vector<std::tuple<double, int, int>> moves;
            for (int seat : seat_candidates(problem, state, incumbent, passenger)) {
                for (int block : block_choices(problem, state, passenger, seat)) {
                    const AssignmentSnapshot saved = state.save();
                    if (state.assign(passenger, seat, block)) {
                        moves.emplace_back(
                            -group_placement_score(problem, group, state.passenger_to_seat),
                            seat, block
                        );
                    }
                    state.restore(saved);
                }
            }
            std::sort(moves.begin(), moves.end(), [&](const auto& left, const auto& right) {
                if (std::get<0>(left) != std::get<0>(right)) {
                    return std::get<0>(left) < std::get<0>(right);
                }
                const std::string& left_id = problem.seats[std::get<1>(left)].id;
                const std::string& right_id = problem.seats[std::get<1>(right)].id;
                return std::tie(left_id, std::get<2>(left))
                    < std::tie(right_id, std::get<2>(right));
            });
            if (moves.size() > static_cast<size_t>(move_limit)) moves.resize(move_limit);
            for (const auto& move : moves) {
                state.restore(parent.snapshot);
                ++result.nodes;
                if (state.assign(passenger, std::get<1>(move), std::get<2>(move))) {
                    next.push_back({state.save(), state.passenger_to_seat, -std::get<0>(move)});
                }
            }
        }
        std::sort(next.begin(), next.end(), [&](const BeamState& left, const BeamState& right) {
            if (std::abs(left.placement_score - right.placement_score) > kTolerance) {
                return left.placement_score > right.placement_score;
            }
            for (int member : group.passengers) {
                const int left_seat = left.assignment[member];
                const int right_seat = right.assignment[member];
                if (left_seat != right_seat) {
                    if (left_seat < 0) return false;
                    if (right_seat < 0) return true;
                    return problem.seats[left_seat].id < problem.seats[right_seat].id;
                }
            }
            return false;
        });
        if (next.size() > static_cast<size_t>(width)) next.resize(width);
        beam = std::move(next);
        if (beam.empty() || std::chrono::steady_clock::now() >= deadline) break;
    }
    for (const BeamState& candidate : beam) {
        state.restore(candidate.snapshot);
        consider_complete(problem, state, result);
    }
    return result;
}

}  // namespace

GroupConstructionResult construct_group_aware(
    const Problem& problem, const std::vector<int>& q0_assignment,
    std::chrono::steady_clock::time_point deadline
) {
    GroupConstructionResult result;
    result.passenger_to_seat = q0_assignment;
    result.q0_components = evaluate_score_components(problem, q0_assignment);
    result.selected_components = result.q0_components;
    result.q0_score = result.q0_components.total();
    result.group_construction_score = result.q0_score;
    if (validate_complete_assignment(problem, q0_assignment) != 0) {
        result.fallback_reason = "q0_invalid";
        return result;
    }

    std::vector<int> group_order(problem.groups.size());
    std::iota(group_order.begin(), group_order.end(), 0);
    std::vector<int> minimum_domains(problem.groups.size(), 0);
    for (int group_index : group_order) {
        const AssignmentState base = state_without_group(
            problem, q0_assignment, group_index
        );
        int minimum = static_cast<int>(problem.seats.size()) + 1;
        for (int passenger : problem.groups[group_index].passengers) {
            int domain = 0;
            for (int seat = 0; seat < static_cast<int>(problem.seats.size()); ++seat) {
                domain += base.can_assign(passenger, seat);
            }
            minimum = std::min(minimum, domain);
        }
        minimum_domains[group_index] = minimum;
    }
    std::stable_sort(group_order.begin(), group_order.end(), [&](int left, int right) {
        auto priority = [&](int group_index) {
            const Group& group = problem.groups[group_index];
            int protected_demand = 0, caregiver_demand = 0;
            for (int passenger : group.passengers) {
                const Passenger& item = problem.passengers[passenger];
                protected_demand += item.need_both_empty ? 2 : item.need_single_empty ? 1 : 0;
                const auto rule = problem.ssr_rules.find(item.ssr);
                caregiver_demand += item.need_cared
                    || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
            }
            return std::tuple(minimum_domains[group_index], -protected_demand, -caregiver_demand,
                              -static_cast<int>(group.passengers.size()), group.id);
        };
        return priority(left) < priority(right);
    });

    std::vector<int> current = q0_assignment;
    double current_score = result.q0_score;
    for (int group_index : group_order) {
        if (std::chrono::steady_clock::now() >= deadline) {
            result.fallback_reason = "deadline";
            break;
        }
        const Group& group = problem.groups[group_index];
        AssignmentState base = state_without_group(problem, current, group_index);
        const std::vector<int> order = passenger_order(problem, group, base);
        SearchResult candidate;
        if (static_cast<int>(group.passengers.size()) <= kDfsMaxGroupSize) {
            ++result.dfs_groups;
            candidate = search_dfs(problem, current, base, order, deadline);
            result.dfs_nodes += candidate.nodes;
        } else {
            ++result.beam_groups;
            candidate = search_beam(problem, current, group, base, order, deadline);
            result.beam_nodes += candidate.nodes;
        }
        if (!candidate.assignment.empty() && candidate.score > current_score + kTolerance) {
            current = std::move(candidate.assignment);
            current_score = candidate.score;
            ++result.groups_improved;
        }
    }
    result.group_construction_score = current_score;
    if (validate_complete_assignment(problem, current) == 0
        && current_score > result.q0_score + kTolerance) {
        result.passenger_to_seat = std::move(current);
        result.selected_incumbent = "group-aware";
        result.selected_components = evaluate_score_components(
            problem, result.passenger_to_seat
        );
        result.score_delta = result.selected_components.total() - result.q0_score;
    } else if (result.fallback_reason.empty()) {
        result.fallback_reason = "not_strictly_better";
    }
    return result;
}

}  // namespace full_cpp
