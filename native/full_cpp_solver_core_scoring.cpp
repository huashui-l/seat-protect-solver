#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {
// Functional section: individual, group, and full soft-score evaluation.
IndividualScoreComponents evaluate_individual_score(
    const Problem& problem, int passenger_index, int seat_index
) {
    if (passenger_index < 0
        || passenger_index >= static_cast<int>(problem.passengers.size())
        || seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) {
        throw std::runtime_error("individual score index out of range");
    }
    auto derived_value = [&](const Seat& seat) {
        if (!std::isnan(seat.explicit_value)) return seat.explicit_value;
        double value = 0.0;
        if (seat.cabin == "Business") value += problem.business_seat_value;
        if (seat.extra_legroom) value += problem.extra_legroom_value;
        if (seat.bassinet) value += problem.bassinet_value;
        return value;
    };
    IndividualScoreComponents result;
    const Passenger& passenger = problem.passengers[passenger_index];
    const auto old_item = problem.old_seat_index.find(passenger.old_seat);
    if (old_item == problem.old_seat_index.end()) return result;
    const Seat& old_seat = problem.old_seats[old_item->second];
    const Seat& new_seat = problem.seats[seat_index];
    const int row_diff = new_seat.row - old_seat.row;
    const double row_penalty = row_diff < 0
        ? std::abs(row_diff) * (problem.prioritize_front ? problem.front_penalty_reduction : 1.0)
        : row_diff * (problem.prioritize_front ? problem.back_penalty_factor : 1.0);
    result.score_s = problem.weight_s * (
        std::abs(old_seat.x - new_seat.x) + row_penalty
    );
    const double old_value = std::isnan(passenger.old_seat_value)
        ? derived_value(old_seat) : passenger.old_seat_value;
    result.score_v = problem.weight_v * std::abs(derived_value(new_seat) - old_value);
    const int mismatch = int(new_seat.window != old_seat.window)
        + int(new_seat.aisle != old_seat.aisle)
        + int(new_seat.exit_row != old_seat.exit_row)
        + int(new_seat.bassinet != old_seat.bassinet)
        + int(new_seat.near_toilet != old_seat.near_toilet);
    result.score_p = problem.weight_p * mismatch;
    if (passenger.has_near_toilet_preference
        && new_seat.near_toilet != passenger.prefer_near_toilet) {
        result.score_p += problem.weight_t * passenger.near_toilet_preference_weight;
    }
    return result;
}

IndividualScoreComponents evaluate_rich_individual_score(const Problem& problem, int p, int s) {
    auto result = evaluate_individual_score(problem, p, s);
    const auto& passenger = problem.passengers[p];
    if (problem.old_seat_index.count(passenger.old_seat)) {
        if (passenger.has_near_toilet_preference && problem.seats[s].near_toilet != passenger.prefer_near_toilet)
            result.score_p -= problem.weight_t * passenger.near_toilet_preference_weight;
        for (const auto& preference : passenger.rich_toilet_preferences)
            if (problem.seats[s].near_toilet != preference.first)
                result.score_p += problem.weight_t * preference.second;
    }
    return result;
}

static ScoreComponents score_components_impl(
    const Problem& problem, const std::vector<int>& assignment, int affected_group, bool rich_preferences,
    const std::set<int>* affected_groups = nullptr
) {
    if (assignment.size() != problem.passengers.size()) {
        throw std::runtime_error("score assignment size mismatch");
    }
    ScoreComponents result;
    const auto includes = [&](int g) { return affected_groups ? affected_groups->count(g) != 0 : affected_group < 0 || g == affected_group; };
    for (int passenger_index = 0;
         passenger_index < static_cast<int>(problem.passengers.size()); ++passenger_index) {
        const int new_index = assignment[passenger_index];
        if (new_index < 0 || !includes(problem.passengers[passenger_index].group)) continue;
        const auto individual = rich_preferences
            ? evaluate_rich_individual_score(problem, passenger_index, new_index)
            : evaluate_individual_score(problem, passenger_index, new_index);
        result.score_s += individual.score_s;
        result.score_v += individual.score_v;
        result.score_p += individual.score_p;
    }
    for (const Group& group : problem.groups) {
        if (group.passengers.size() <= 1) continue;
        if (!includes(problem.passengers[group.passengers.front()].group)) continue;
        double sum_x = 0.0, sum_y = 0.0;
        int count = 0;
        for (int passenger : group.passengers) {
            const int seat = assignment[passenger];
            if (seat < 0) continue;
            sum_x += problem.seats[seat].x;
            sum_y += problem.seats[seat].y;
            ++count;
        }
        if (count <= 1) continue;
        const double center_x = sum_x / count;
        const double center_y = sum_y / count;
        double max_x = 0.0, max_y = 0.0;
        for (int passenger : group.passengers) {
            const int seat = assignment[passenger];
            if (seat < 0) continue;
            max_x = std::max(max_x, std::abs(problem.seats[seat].x - center_x));
            max_y = std::max(max_y, std::abs(problem.seats[seat].y - center_y));
        }
        result.score_c += problem.weight_c * (
            problem.group_centroid_x_factor * max_x
            + problem.group_centroid_y_factor * max_y
        );
    }
    for (int infant = 0; infant < static_cast<int>(problem.passengers.size()); ++infant) {
        if (problem.passengers[infant].ssr != "BSCT" || assignment[infant] < 0) continue;
        const Seat& infant_seat = problem.seats[assignment[infant]];
        for (int other = 0; other < static_cast<int>(problem.passengers.size()); ++other) {
            if (assignment[other] < 0
                || assignment[other] == assignment[infant]
                || problem.passengers[other].group == problem.passengers[infant].group) continue;
            if (!includes(problem.passengers[infant].group) && !includes(problem.passengers[other].group)) continue;
            const Seat& other_seat = problem.seats[assignment[other]];
            if (other_seat.cabin != infant_seat.cabin) continue;
            const double distance = 1.0 + std::abs(infant_seat.x - other_seat.x);
            if (other_seat.row == infant_seat.row && other_seat.subrow == infant_seat.subrow) {
                result.score_b += problem.weight_b / distance;
            } else if (std::abs(other_seat.row - infant_seat.row) == 1) {
                result.score_b += problem.weight_b * problem.baby_front_back_factor / distance;
            }
        }
    }
    return result;
}

ScoreComponents evaluate_score_components(const Problem& problem, const std::vector<int>& assignment) {
    return score_components_impl(problem, assignment, -1, false);
}

ScoreComponents evaluate_rich_group_score(const Problem& problem, const std::vector<int>& assignment, int group_index) {
    return score_components_impl(problem, assignment, group_index, true);
}

ScoreComponents evaluate_rich_groups_score(const Problem& problem, const std::vector<int>& assignment, const std::set<int>& groups) {
    return score_components_impl(problem, assignment, -1, true, &groups);
}

double evaluate_soft_score(const Problem& problem, const std::vector<int>& assignment) {
    return evaluate_score_components(problem, assignment).total();
}
}  // namespace full_cpp
