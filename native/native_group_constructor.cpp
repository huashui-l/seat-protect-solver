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
    const int candidate_cap = std::max(1, problem.rich.candidate_cap);
    if (seats.size() > static_cast<size_t>(candidate_cap)) {
        seats.resize(static_cast<size_t>(candidate_cap));
    }
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
        if (candidate[passenger] < 0 || best[passenger] < 0) {
            if (candidate[passenger] != best[passenger]) {
                return candidate[passenger] > best[passenger];
            }
            continue;
        }
        const std::string& left = problem.seats[candidate[passenger]].id;
        const std::string& right = problem.seats[best[passenger]].id;
        if (left != right) return left < right;
    }
    return false;
}

struct SearchResult {
    std::vector<int> assignment;
    AssignmentSnapshot snapshot;
    double score = -std::numeric_limits<double>::infinity();
    long long nodes = 0;
};

bool group_caregivers_satisfied(
    const Problem& problem, const Group& group, const AssignmentState& state
) {
    for (int passenger : group.passengers) {
        const Passenger& cared = problem.passengers[passenger];
        const auto rule_item = problem.ssr_rules.find(cared.ssr);
        const bool requires = cared.need_cared
            || (rule_item != problem.ssr_rules.end()
                && rule_item->second.requires_caregiver);
        if (!requires) continue;
        const int seat = state.passenger_to_seat[passenger];
        if (seat < 0) return false;
        const bool allow_cross = cared.need_cared && cared.ssr.empty()
            ? false
            : rule_item != problem.ssr_rules.end()
                && rule_item->second.caregiver_allow_cross_aisle;
        const auto& neighbors = allow_cross
            ? problem.seats[seat].row_neighbors
            : problem.seats[seat].same_block_neighbors;
        bool found = false;
        for (int other : group.passengers) {
            if (other == passenger) continue;
            const Passenger& adult = problem.passengers[other];
            if (!adult.ssr.empty() || adult.need_cared
                || adult.need_both_empty || adult.need_single_empty) continue;
            found = found || std::find(
                neighbors.begin(), neighbors.end(), state.passenger_to_seat[other]
            ) != neighbors.end();
        }
        if (!found) return false;
    }
    return true;
}

bool same_group_assignment(
    const Group& group, const std::vector<int>& left, const std::vector<int>& right
) {
    if (right.empty()) return false;
    for (int passenger : group.passengers) {
        if (left[passenger] != right[passenger]) return false;
    }
    return true;
}

void consider_candidate(
    const Problem& problem, const AssignmentState& state, SearchResult& result,
    const Group* partial_group = nullptr,
    const std::vector<int>* forbidden = nullptr
) {
    double score = 0.0;
    if (partial_group) {
        for (int passenger : partial_group->passengers) {
            if (state.passenger_to_seat[passenger] < 0) return;
        }
        if (!group_caregivers_satisfied(problem, *partial_group, state)) return;
        if (forbidden && same_group_assignment(
                *partial_group, state.passenger_to_seat, *forbidden)) return;
        score = group_placement_score(
            problem, *partial_group, state.passenger_to_seat
        );
    } else {
        if (validate_complete_assignment(problem, state.passenger_to_seat) != 0) return;
        score = evaluate_soft_score(problem, state.passenger_to_seat);
    }
    if (result.assignment.empty()
        || better_assignment(problem, score, state.passenger_to_seat,
                             result.score, result.assignment)) {
        result.assignment = state.passenger_to_seat;
        result.snapshot = state.save();
        result.score = score;
    }
}

SearchResult search_dfs(
    const Problem& problem, const std::vector<int>& incumbent,
    AssignmentState state,
    const std::vector<int>& order,
    std::chrono::steady_clock::time_point deadline,
    const Group* partial_group = nullptr,
    const std::vector<int>* forbidden = nullptr
) {
    SearchResult result;
    auto visit = [&](auto&& self, int depth) -> void {
        if (result.nodes >= problem.rich.small_group_dfs_node_limit
            || std::chrono::steady_clock::now() >= deadline) return;
        ++result.nodes;
        if (depth == static_cast<int>(order.size())) {
            consider_candidate(
                problem, state, result, partial_group, forbidden
            );
            return;
        }
        const int passenger = order[depth];
        for (int seat : seat_candidates(problem, state, incumbent, passenger)) {
            for (int block : block_choices(problem, state, passenger, seat)) {
                const AssignmentSnapshot saved = state.save();
                if (state.assign(passenger, seat, block)) self(self, depth + 1);
                state.restore(saved);
                if (result.nodes >= problem.rich.small_group_dfs_node_limit
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
    std::chrono::steady_clock::time_point deadline,
    const Group* partial_group = nullptr,
    const std::vector<int>* forbidden = nullptr
) {
    const int size = static_cast<int>(order.size());
    const int width = size <= 2 ? problem.rich.beam_width_small
        : size <= 5 ? problem.rich.beam_width_medium : problem.rich.beam_width_large;
    const int move_limit = size <= 5 ? problem.rich.beam_moves_medium
        : problem.rich.beam_moves_large;
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
        consider_candidate(
            problem, state, result, partial_group, forbidden
        );
    }
    return result;
}

AssignmentState fixed_initial_state(const Problem& problem) {
    const FixedSeatContext fixed = preprocess_fixed_seats(problem);
    AssignmentState state(problem, &fixed);
    std::vector<int> singles;
    for (int passenger = 0;
         passenger < static_cast<int>(problem.passengers.size()); ++passenger) {
        if (state.passenger_to_seat[passenger] >= 0
            && problem.passengers[passenger].need_single_empty) {
            singles.push_back(passenger);
        }
    }
    std::vector<int> owner(problem.seats.size(), -1);
    auto augment = [&](auto&& self, int passenger, std::vector<bool>& seen) -> bool {
        const int seat = state.passenger_to_seat[passenger];
        for (int empty : problem.seats[seat].same_block_neighbors) {
            if (state.seat_to_passenger[empty] >= 0 || state.blocked_count[empty] > 0
                || seen[empty]) continue;
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
            throw std::runtime_error(
                "cannot reserve empty neighbor for fixed protected passenger"
            );
        }
    }
    for (int empty = 0; empty < static_cast<int>(owner.size()); ++empty) {
        if (owner[empty] < 0) continue;
        ++state.blocked_count[empty];
        state.assigned_blocked[owner[empty]].push_back(empty);
    }
    return state;
}

std::vector<int> group_first_order(
    const Problem& problem, const AssignmentState& initial
) {
    std::vector<int> order(problem.groups.size());
    std::iota(order.begin(), order.end(), 0);
    std::vector<int> minimum_domains(problem.groups.size(), 0);
    for (int group_index : order) {
        int minimum = static_cast<int>(problem.seats.size()) + 1;
        bool has_unassigned = false;
        for (int passenger : problem.groups[group_index].passengers) {
            if (initial.passenger_to_seat[passenger] >= 0) continue;
            has_unassigned = true;
            int domain = 0;
            for (int seat = 0; seat < static_cast<int>(problem.seats.size()); ++seat) {
                domain += initial.can_assign(passenger, seat);
            }
            minimum = std::min(minimum, domain);
        }
        minimum_domains[group_index] = has_unassigned ? minimum : 0;
    }
    std::stable_sort(order.begin(), order.end(), [&](int left, int right) {
        auto priority = [&](int group_index) {
            const Group& group = problem.groups[group_index];
            int fixed_count = 0, protected_demand = 0, caregiver_demand = 0;
            for (int passenger : group.passengers) {
                const Passenger& item = problem.passengers[passenger];
                fixed_count += !item.fixed_seat.empty();
                protected_demand += item.need_both_empty ? 2 : item.need_single_empty ? 1 : 0;
                const auto rule = problem.ssr_rules.find(item.ssr);
                caregiver_demand += item.need_cared
                    || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
            }
            return std::tuple(
                -fixed_count, minimum_domains[group_index], -protected_demand,
                -caregiver_demand, -static_cast<int>(group.passengers.size()), group.id
            );
        };
        return priority(left) < priority(right);
    });
    return order;
}

SearchResult search_partial_group(
    const Problem& problem, const std::vector<int>& incumbent,
    const Group& group, AssignmentState state,
    std::chrono::steady_clock::time_point deadline,
    const std::vector<int>* forbidden = nullptr
) {
    std::vector<int> order = passenger_order(problem, group, state);
    order.erase(std::remove_if(order.begin(), order.end(), [&](int passenger) {
        return state.passenger_to_seat[passenger] >= 0;
    }), order.end());
    if (order.empty()) {
        SearchResult result;
        consider_candidate(problem, state, result, &group, forbidden);
        return result;
    }
    if (problem.rich.small_group_dfs_enabled
        && static_cast<int>(order.size()) <= problem.rich.small_group_dfs_max_size) {
        return search_dfs(
            problem, incumbent, state, order, deadline, &group, forbidden
        );
    }
    return search_beam(
        problem, incumbent, group, state, order, deadline, &group, forbidden
    );
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
        if (problem.rich.small_group_dfs_enabled
            && static_cast<int>(group.passengers.size()) <= problem.rich.small_group_dfs_max_size) {
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

GroupConstructionResult construct_group_first(
    const Problem& problem, const std::vector<int>& q0_assignment,
    const GroupConstructionResult& q1_result,
    std::chrono::steady_clock::time_point deadline
) {
    GroupConstructionResult result = q1_result;
    result.fallback_reason.clear();
    result.q1_selected_incumbent = q1_result.selected_incumbent;
    result.q1_score = evaluate_soft_score(problem, q1_result.passenger_to_seat);
    result.from_scratch_score = 0.0;
    result.from_scratch_complete = false;
    if (validate_complete_assignment(problem, q1_result.passenger_to_seat) != 0) {
        result.fallback_reason = "q1_invalid";
        return result;
    }

    AssignmentState state = fixed_initial_state(problem);
    const std::vector<int> group_order = group_first_order(problem, state);
    struct CommittedGroup {
        int group_index = -1;
        AssignmentSnapshot before;
        std::vector<int> placement;
    };
    std::vector<CommittedGroup> committed;

    auto record_search = [&](const Group& group, const SearchResult& search) {
        int remaining = 0;
        for (int passenger : group.passengers) {
            remaining += state.passenger_to_seat[passenger] < 0;
        }
        if (remaining <= problem.rich.small_group_dfs_max_size) {
            ++result.from_scratch_dfs_groups;
            result.from_scratch_dfs_nodes += search.nodes;
        } else {
            ++result.from_scratch_beam_groups;
            result.from_scratch_beam_nodes += search.nodes;
        }
    };

    bool complete = true;
    for (int group_index : group_order) {
        if (std::chrono::steady_clock::now() >= deadline) {
            complete = false;
            result.fallback_reason = "q2a_deadline";
            break;
        }
        const Group& group = problem.groups[group_index];
        const AssignmentSnapshot before = state.save();
        SearchResult search = search_partial_group(
            problem, q0_assignment, group, state, deadline
        );
        record_search(group, search);
        if (!search.assignment.empty()) {
            state.restore(search.snapshot);
            committed.push_back({group_index, before, search.assignment});
            continue;
        }

        ++result.recovery_attempts;
        bool recovered = false;
        if (!committed.empty() && std::chrono::steady_clock::now() < deadline) {
            CommittedGroup previous = committed.back();
            const Group& previous_group = problem.groups[previous.group_index];
            bool has_movable = false;
            for (int passenger : previous_group.passengers) {
                has_movable = has_movable
                    || problem.passengers[passenger].fixed_seat.empty();
            }
            if (has_movable) {
                state.restore(previous.before);
                SearchResult alternate = search_partial_group(
                    problem, q0_assignment, previous_group, state, deadline,
                    &previous.placement
                );
                record_search(previous_group, alternate);
                if (!alternate.assignment.empty()) {
                    state.restore(alternate.snapshot);
                    SearchResult retry = search_partial_group(
                        problem, q0_assignment, group, state, deadline
                    );
                    record_search(group, retry);
                    if (!retry.assignment.empty()) {
                        committed.back() = {
                            previous.group_index, previous.before,
                            alternate.assignment
                        };
                        const AssignmentSnapshot retry_before = alternate.snapshot;
                        state.restore(retry.snapshot);
                        committed.push_back({group_index, retry_before, retry.assignment});
                        ++result.recovery_succeeded;
                        recovered = true;
                    }
                }
            }
        }
        if (!recovered) {
            complete = false;
            if (result.fallback_reason.empty()) {
                result.fallback_reason = "q2a_incomplete";
            }
            break;
        }
    }

    if (complete && validate_complete_assignment(problem, state.passenger_to_seat) == 0) {
        result.from_scratch_complete = true;
        result.from_scratch_score = evaluate_soft_score(
            problem, state.passenger_to_seat
        );
        if (result.from_scratch_score > result.q1_score + kTolerance) {
            result.passenger_to_seat = state.passenger_to_seat;
            result.selected_incumbent = "q2a-group-first";
            result.selected_components = evaluate_score_components(
                problem, result.passenger_to_seat
            );
            result.group_construction_score = result.from_scratch_score;
            result.score_delta = result.from_scratch_score - result.q0_score;
            result.fallback_reason.clear();
        } else if (result.fallback_reason.empty()) {
            result.fallback_reason = "q2a_not_strictly_better";
        }
    } else if (complete && result.fallback_reason.empty()) {
        result.fallback_reason = "q2a_invalid";
    }
    return result;
}

GroupConstructionResult construct_rich_m1(
    const Problem& problem, const std::vector<int>& q0_assignment,
    const GroupConstructionResult& q2a_result,
    std::chrono::steady_clock::time_point global_deadline
) {
    (void)q0_assignment;
    const auto started = std::chrono::steady_clock::now();
    GroupConstructionResult result = q2a_result;
    result.rich_construction_score = evaluate_soft_score(problem, q2a_result.passenger_to_seat);
    result.rich_construction_assigned = 0;
    for (int seat : q2a_result.passenger_to_seat) result.rich_construction_assigned += seat >= 0;
    result.rich_construction_unassigned = static_cast<int>(problem.passengers.size())
        - result.rich_construction_assigned;
    result.rich_candidate_complete = result.rich_construction_unassigned == 0
        && validate_complete_assignment(problem, q2a_result.passenger_to_seat) == 0;

    // M1 keeps Q0/Q1/Q2A as the audited fallback, then performs the native
    // construction/repair pass against the same shared deadline.
    if (!result.rich_candidate_complete && std::chrono::steady_clock::now() < global_deadline) {
        AssignmentState state = fixed_initial_state(problem);
        bool rebuilt = true;
        for (int passenger = 0; passenger < static_cast<int>(q2a_result.passenger_to_seat.size()); ++passenger) {
            const int seat = q2a_result.passenger_to_seat[passenger];
            if (seat < 0 || state.passenger_to_seat[passenger] >= 0) continue;
            if (!state.assign(passenger, seat)) { rebuilt = false; break; }
        }
        if (rebuilt) {
            for (int passenger = 0; passenger < static_cast<int>(problem.passengers.size()); ++passenger) {
                if (state.passenger_to_seat[passenger] >= 0) continue;
                ++result.rich_repair_attempted;
                const std::vector<int> empty_incumbent(problem.passengers.size(), -1);
                for (int seat : seat_candidates(problem, state, empty_incumbent, passenger)) {
                    for (int block : block_choices(problem, state, passenger, seat)) {
                        ++result.rich_repair_nodes;
                        if (state.assign(passenger, seat, block)) {
                            ++result.rich_repair_repaired;
                            break;
                        }
                    }
                    if (state.passenger_to_seat[passenger] >= 0) break;
                    if (std::chrono::steady_clock::now() >= global_deadline) break;
                }
                if (state.passenger_to_seat[passenger] < 0) ++result.rich_repair_unresolved;
                if (std::chrono::steady_clock::now() >= global_deadline) break;
            }
            if (validate_complete_assignment(problem, state.passenger_to_seat) == 0) {
                result.passenger_to_seat = state.passenger_to_seat;
                result.rich_candidate_complete = true;
                result.rich_construction_assigned = static_cast<int>(problem.passengers.size());
                result.rich_construction_unassigned = 0;
                result.rich_repair_score = evaluate_soft_score(problem, result.passenger_to_seat);
                result.selected_components = evaluate_score_components(problem, result.passenger_to_seat);
                result.group_construction_score = result.rich_repair_score;
                result.selected_incumbent = "rich-m1";
            }
        }
    }
    result.rich_construction_seconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - started).count();
    result.rich_repair_seconds = result.rich_construction_seconds;
    result.rich_construction_carry_seconds = std::max(0.0,
        problem.rich.construction_time_budget - result.rich_construction_seconds);
    return result;
}

void improve_rich_vnd_m2(
    const Problem& problem, std::vector<int>& assignment,
    std::chrono::steady_clock::time_point deadline,
    GroupConstructionResult& diagnostics
) {
    const auto started = std::chrono::steady_clock::now();
    if (validate_complete_assignment(problem, assignment) != 0) return;
    double current_score = evaluate_soft_score(problem, assignment);
    const int passenger_count = static_cast<int>(assignment.size());
    const int seat_count = static_cast<int>(problem.seats.size());
    const int seat_cap = std::max(1, problem.rich.local_search_candidate_cap);
    bool improved = true;
    while (improved && std::chrono::steady_clock::now() < deadline) {
        improved = false;
        // Python's first-improvement 1-opt analogue. Candidate seats are
        // ordered by individual score and capped by the frozen config.
        for (int passenger = 0; passenger < passenger_count && !improved; ++passenger) {
            const int old_seat = assignment[passenger];
            std::vector<int> seats(seat_count);
            std::iota(seats.begin(), seats.end(), 0);
            std::stable_sort(seats.begin(), seats.end(), [&](int left, int right) {
                const double ls = evaluate_individual_score(problem, passenger, left).total();
                const double rs = evaluate_individual_score(problem, passenger, right).total();
                if (std::abs(ls - rs) > kTolerance) return ls > rs;
                return problem.seats[left].id < problem.seats[right].id;
            });
            if (seats.size() > static_cast<size_t>(seat_cap)) seats.resize(seat_cap);
            for (int seat : seats) {
                if (seat == old_seat) continue;
                assignment[passenger] = seat;
                if (validate_complete_assignment(problem, assignment) == 0) {
                    const double score = evaluate_soft_score(problem, assignment);
                    if (score > current_score + kTolerance) {
                        current_score = score;
                        ++diagnostics.rich_vnd_one_opt_moves;
                        improved = true;
                        break;
                    }
                }
                assignment[passenger] = old_seat;
                if (std::chrono::steady_clock::now() >= deadline) break;
            }
            if (!improved) assignment[passenger] = old_seat;
        }
        if (improved) continue;

        for (int left = 0; left < passenger_count && !improved; ++left) {
            for (int right = left + 1; right < passenger_count && !improved; ++right) {
                std::swap(assignment[left], assignment[right]);
                if (validate_complete_assignment(problem, assignment) == 0) {
                    const double score = evaluate_soft_score(problem, assignment);
                    if (score > current_score + kTolerance) {
                        current_score = score;
                        ++diagnostics.rich_vnd_two_swap_moves;
                        improved = true;
                    }
                }
                if (!improved) std::swap(assignment[left], assignment[right]);
                if (std::chrono::steady_clock::now() >= deadline) break;
            }
        }
        if (improved) continue;

        const int cycle_cap = std::max(1, problem.rich.local_search_cycle_candidate_cap);
        for (int a = 0; a < passenger_count && !improved; ++a) {
            for (int b = a + 1; b < passenger_count && !improved; ++b) {
                for (int c = b + 1; c < passenger_count && !improved; ++c) {
                    const int sa = assignment[a], sb = assignment[b], sc = assignment[c];
                    assignment[a] = sb; assignment[b] = sc; assignment[c] = sa;
                    if (validate_complete_assignment(problem, assignment) == 0) {
                        const double score = evaluate_soft_score(problem, assignment);
                        if (score > current_score + kTolerance) {
                            current_score = score;
                            ++diagnostics.rich_vnd_three_cycle_moves;
                            improved = true;
                        }
                    }
                    if (!improved) {
                        assignment[a] = sa; assignment[b] = sb; assignment[c] = sc;
                    }
                    if (std::chrono::steady_clock::now() >= deadline) break;
                    if (c - b >= cycle_cap) break;
                }
                if (b - a >= cycle_cap) break;
            }
        }
    }
    diagnostics.rich_vnd_score = current_score;
    diagnostics.rich_vnd_seconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - started).count();
}

}  // namespace full_cpp
