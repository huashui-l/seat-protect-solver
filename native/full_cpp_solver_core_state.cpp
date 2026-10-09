#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {

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

}  // namespace full_cpp
