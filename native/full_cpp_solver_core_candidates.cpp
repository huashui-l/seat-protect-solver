#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {

std::vector<std::pair<int, int>> rich_placement_domain(
    const Problem& problem, const FixedSeatContext& fixed, int passenger_index
) {
    const Passenger& passenger = problem.passengers[passenger_index];
    const SsrRule rule = ssr_rule(problem, passenger);
    std::vector<std::pair<int, int>> options;
    for (int index = 0; index < static_cast<int>(problem.seats.size()); ++index) {
        const Seat& seat = problem.seats[index];
        if (!passenger.fixed_seat.empty()) {
            if (seat.id != passenger.fixed_seat) continue;
        } else if (fixed.owner_by_seat[index] >= 0 || fixed.deterministic_blocked[index]) {
            continue;
        }
        if ((!passenger.cabin.empty() && passenger.cabin != seat.cabin)
            || (seat.exit_row && !rule.allow_exit_row)
            || (rule.requires_bassinet && !seat.bassinet)
            || (rule.requires_aisle && !seat.aisle)) continue;
        if (passenger.need_both_empty) {
            const auto& neighbors = problem.both_side_empty_allow_cross_aisle
                ? seat.row_neighbors : seat.same_block_neighbors;
            if (neighbors.empty()
                || (problem.require_two_real_neighbors && neighbors.size() != 2)) continue;
            if (std::any_of(neighbors.begin(), neighbors.end(),
                [&](int neighbor) { return fixed.owner_by_seat[neighbor] >= 0; })) continue;
            options.emplace_back(index, -1);
        } else if (passenger.need_single_empty) {
            for (int neighbor : seat.same_block_neighbors)
                if (fixed.owner_by_seat[neighbor] < 0) options.emplace_back(index, neighbor);
        } else {
            options.emplace_back(index, -1);
        }
    }
    return options;
}

RichCandidateCache build_rich_candidate_cache(const Problem& problem, const AssignmentState& state,
    const std::vector<double>* frozen_owner_regrets, int target_group) {
    if (target_group >= 0 && !frozen_owner_regrets)
        throw std::runtime_error("group candidate cache requires frozen owner regrets");
    RichCandidateCache result;
    const int count = static_cast<int>(problem.passengers.size());
    const int seats = static_cast<int>(problem.seats.size());
    result.owner_regrets.resize(count, 0.0);
    result.costs.resize(count);
    result.rankings.resize(count);
    std::vector<int> assigned_passengers, assigned_infants;
    for (int p = 0; p < count; ++p) if (state.passenger_to_seat[p] >= 0) {
        assigned_passengers.push_back(p);
        if (problem.passengers[p].ssr == "BSCT") assigned_infants.push_back(p);
    }
    const auto seat_value = [&](const Seat& seat) {
        if (!std::isnan(seat.explicit_value)) return seat.explicit_value;
        double value = seat.cabin == "Business" ? problem.business_seat_value : 0.0;
        if (seat.extra_legroom) value += problem.extra_legroom_value;
        if (seat.bassinet) value += problem.bassinet_value;
        return value;
    };
    std::unordered_map<std::string, int> owners;
    for (int p = 0; p < count; ++p)
        if (problem.old_seat_index.count(problem.passengers[p].old_seat))
            owners[problem.passengers[p].old_seat] = p;
    for (int p = 0; p < count; ++p) {
        const auto& passenger = problem.passengers[p];
        if (target_group >= 0 && passenger.group != target_group) continue;
        result.costs[p].resize(seats);
        const auto old_index = problem.old_seat_index.find(passenger.old_seat);
        const Seat* old = old_index == problem.old_seat_index.end() ? nullptr : &problem.old_seats[old_index->second];
        const double old_value = !std::isnan(passenger.old_seat_value) ? passenger.old_seat_value
            : old ? seat_value(*old) : 0.0;
        for (int s = 0; s < seats; ++s) {
            const auto& seat = problem.seats[s];
            double score = problem.weight_v * std::abs(seat_value(seat) - old_value);
            if (old) {
                const int dy = old->row - seat.row;
                const double factor = !problem.prioritize_front ? 1.0
                    : dy >= 0 ? problem.front_penalty_reduction : problem.back_penalty_factor;
                const double distance = std::abs(dy) * factor + std::abs(seat.x - old->x);
                const int mismatch = (seat.window != old->window) + (seat.aisle != old->aisle)
                    + (seat.exit_row != old->exit_row) + (seat.bassinet != old->bassinet)
                    + (seat.near_toilet != old->near_toilet);
                score += problem.weight_s * distance + problem.weight_p * mismatch;
            }
            for (const auto& preference : passenger.rich_toilet_preferences)
                if (seat.near_toilet != preference.first) score += problem.weight_t * preference.second;
            double impact = 0.0;
            if (passenger.group_id != -1) {
                const auto& affecting = passenger.ssr == "BSCT" ? assigned_passengers : assigned_infants;
                for (int other : affecting) {
                    const int assigned = state.passenger_to_seat[other];
                    if (problem.passengers[other].group_id == passenger.group_id) continue;
                    const auto& other_seat = problem.seats[assigned];
                    if (seat.row == other_seat.row && seat.subrow == other_seat.subrow)
                        impact += 1.0 / (1.0 + std::abs(seat.x - other_seat.x));
                    else if (std::abs(seat.row - other_seat.row) == 1)
                        impact += problem.baby_front_back_factor / (1.0 + std::abs(seat.x - other_seat.x));
                }
            }
            result.costs[p][s] = -(score + problem.weight_b * impact);
        }
    }
    if (frozen_owner_regrets) result.owner_regrets = *frozen_owner_regrets;
    for (int p = 0; p < count && !frozen_owner_regrets; ++p) {
        const auto own = problem.seat_index.find(problem.passengers[p].old_seat);
        if (own == problem.seat_index.end() || !state.rich_seat_feasible(p, own->second)) continue;
        double alternative = std::numeric_limits<double>::infinity();
        for (int s = 0; s < seats; ++s)
            if (s != own->second && state.rich_seat_feasible(p, s)) alternative = std::min(alternative, result.costs[p][s]);
        if (std::isfinite(alternative)) result.owner_regrets[p] = std::max(0.0, alternative - result.costs[p][own->second]);
    }
    for (int p = 0; p < count; ++p) {
        if (target_group >= 0 && problem.passengers[p].group != target_group) continue;
        auto& order = result.rankings[p];
        for (int s = 0; s < seats; ++s) {
            const auto owner = owners.find(problem.seats[s].id);
            if (owner != owners.end() && owner->second != p)
                result.costs[p][s] += problem.rich.old_seat_reservation_pressure * result.owner_regrets[owner->second];
            order.push_back(s);
        }
        std::stable_sort(order.begin(), order.end(), [&](int left, int right) {
            return result.costs[p][left] < result.costs[p][right];
        });
    }
    return result;
}

}  // namespace full_cpp
