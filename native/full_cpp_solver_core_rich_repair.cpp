#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {
// Functional section: repair metrics and fixed-seat preprocessing.
std::vector<RichGroupRepairMetric> build_rich_repair_queue(
    const Problem& problem, const std::vector<int>& assignment
) {
    std::vector<RichGroupRepairMetric> queue;
    for (const auto& group : problem.groups) {
        RichGroupRepairMetric metric;
        metric.group_id = group.id;
        metric.size = static_cast<int>(group.passengers.size());
        std::vector<int> seats;
        std::set<int> rows;
        double sum_x = 0.0, sum_y = 0.0;
        for (int p : group.passengers) {
            const int s = assignment[p];
            if (s < 0) continue;
            seats.push_back(s);
            rows.insert(problem.seats[s].row);
            sum_x += problem.seats[s].x;
            sum_y += problem.seats[s].y;
            if (problem.rich_quality_repair_active) {
                const auto parts = evaluate_individual_score(problem, p, s);
                metric.value_mismatch_score += parts.score_v;
                double preference = parts.score_p;
                const auto& passenger = problem.passengers[p];
                // The incremental evaluator sums all toilet preference rules;
                // the older full-score path stores a single preference as well.
                if (problem.old_seat_index.count(passenger.old_seat)) {
                    if (passenger.has_near_toilet_preference
                        && problem.seats[s].near_toilet != passenger.prefer_near_toilet)
                        preference -= problem.weight_t * passenger.near_toilet_preference_weight;
                    for (const auto& rule : passenger.rich_toilet_preferences)
                        if (problem.seats[s].near_toilet != rule.first)
                            preference += problem.weight_t * rule.second;
                }
                metric.preference_mismatch_score += preference;
            }
        }
        metric.assigned = static_cast<int>(seats.size());
        metric.row_count = static_cast<int>(rows.size());
        if (!rows.empty()) metric.row_span = *rows.rbegin() - *rows.begin();
        if (seats.size() > 1) {
            double max_x = 0.0, max_y = 0.0;
            for (int s : seats) {
                max_x = std::max(max_x, std::abs(problem.seats[s].x - sum_x / seats.size()));
                max_y = std::max(max_y, std::abs(problem.seats[s].y - sum_y / seats.size()));
            }
            metric.compactness_penalty = problem.group_centroid_x_factor * max_x
                + problem.group_centroid_y_factor * max_y;
        }
        metric.compactness_score = problem.weight_c * metric.compactness_penalty;
        metric.priority_loss = -(metric.compactness_score + metric.value_mismatch_score);
        metric.extreme_dispersion = metric.row_span >= 2;
        queue.push_back(metric);
    }
    std::stable_sort(queue.begin(), queue.end(), [](const auto& left, const auto& right) {
        const auto key = [](const auto& m) {
            return std::make_tuple(-m.priority_loss, -m.row_span, -m.compactness_penalty, m.group_id);
        };
        return key(left) < key(right);
    });
    return queue;
}

RichStructuredOrder build_rich_structured_order(const Problem& problem, const std::vector<int>& assignment) {
    RichStructuredOrder result;
    result.min_group_size = problem.rich.structured_pattern_min_group_size;
    if (problem.rich_structured_small_groups_active)
        result.min_group_size = std::min(result.min_group_size, problem.rich.structured_research_min_group_size);
    result.full_resource_global_blocks = problem.rich_full_resource_global_blocks;
    const auto metrics = build_rich_repair_queue(problem, assignment);
    std::map<int, RichGroupRepairMetric> by_group;
    for (const auto& metric : metrics) by_group[metric.group_id] = metric;
    std::vector<int> special(problem.groups.size(), 0);
    for (int g = 0; g < static_cast<int>(problem.groups.size()); ++g) {
        result.ordered_groups.push_back(g);
        for (int p : problem.groups[g].passengers) {
            const auto& item = problem.passengers[p];
            special[g] += !item.ssr.empty() || item.need_cared || item.need_single_empty || item.need_both_empty;
        }
    }
    const auto key = [&](int g) {
        const auto& group = problem.groups[g];
        const auto& metric = by_group.at(group.id);
        const bool primary = group.passengers.size() >= static_cast<size_t>(problem.rich.structured_pattern_min_group_size) || special[g] > 0;
        return std::make_tuple(!primary,
            std::make_tuple(-metric.priority_loss, -metric.row_span, -metric.compactness_penalty, metric.group_id),
            -special[g], -static_cast<int>(group.passengers.size()), group.id);
    };
    std::stable_sort(result.ordered_groups.begin(), result.ordered_groups.end(), [&](int a, int b) { return key(a) < key(b); });
    for (int g : result.ordered_groups) {
        result.repair_queue.push_back(by_group.at(problem.groups[g].id));
        if (special[g] || problem.groups[g].passengers.size() >= static_cast<size_t>(result.min_group_size))
            result.difficult_groups.push_back(g);
    }
    return result;
}

void write_rich_repair_queue(std::ostream& output,
    const std::vector<RichGroupRepairMetric>& queue, size_t limit
) {
    output << '[';
    for (size_t i = 0; i < std::min(limit, queue.size()); ++i) {
        if (i) output << ',';
        const auto& m = queue[i];
        output << "{\"group_id\":" << m.group_id << ",\"size\":" << m.size
            << ",\"assigned\":" << m.assigned << ",\"row_span\":" << m.row_span
            << ",\"row_count\":" << m.row_count
            << ",\"compactness_penalty\":" << m.compactness_penalty
            << ",\"compactness_score\":" << m.compactness_score
            << ",\"value_mismatch_score\":" << m.value_mismatch_score
            << ",\"preference_mismatch_score\":" << m.preference_mismatch_score
            << ",\"priority_loss\":" << m.priority_loss
            << ",\"extreme_dispersion\":" << (m.extreme_dispersion ? "true" : "false") << '}';
    }
    output << ']';
}

FixedSeatContext preprocess_fixed_seats(const Problem& problem) {
    FixedSeatContext context;
    context.owner_by_seat.assign(problem.seats.size(), -1);
    context.deterministic_blocked.assign(problem.seats.size(), false);
    std::vector<int> fixed_passengers;
    for (int passenger_index = 0;
         passenger_index < static_cast<int>(problem.passengers.size()); ++passenger_index) {
        const Passenger& passenger = problem.passengers[passenger_index];
        if (passenger.fixed_seat.empty()) continue;
        const auto seat_item = problem.seat_index.find(passenger.fixed_seat);
        if (seat_item == problem.seat_index.end()) {
            throw std::runtime_error("fixed seat does not exist: " + passenger.fixed_seat);
        }
        const int seat_index = seat_item->second;
        if (context.owner_by_seat[seat_index] >= 0) {
            throw std::runtime_error("duplicate fixed seat: " + passenger.fixed_seat);
        }
        const Seat& seat = problem.seats[seat_index];
        const SsrRule rule = ssr_rule(problem, passenger);
        if (seat.exit_row && !rule.allow_exit_row) {
            throw std::runtime_error("fixed seat violates exit-row rule: " + passenger.fixed_seat);
        }
        if (rule.requires_bassinet && !seat.bassinet) {
            throw std::runtime_error("fixed seat violates bassinet rule: " + passenger.fixed_seat);
        }
        if (rule.requires_aisle && !seat.aisle) {
            throw std::runtime_error("fixed seat violates aisle rule: " + passenger.fixed_seat);
        }
        context.owner_by_seat[seat_index] = passenger_index;
        fixed_passengers.push_back(passenger_index);
    }

    for (int passenger_index : fixed_passengers) {
        const Passenger& passenger = problem.passengers[passenger_index];
        const int seat_index = problem.seat_index.at(passenger.fixed_seat);
        const Seat& seat = problem.seats[seat_index];
        if (passenger.need_both_empty) {
            const auto& neighbors = problem.both_side_empty_allow_cross_aisle
                ? seat.row_neighbors : seat.same_block_neighbors;
            if ((problem.require_two_real_neighbors && neighbors.size() != 2) || neighbors.empty()) {
                throw std::runtime_error("fixed protected passenger lacks two neighbors: " + passenger.fixed_seat);
            }
            for (int neighbor : neighbors) {
                if (context.owner_by_seat[neighbor] >= 0) {
                    throw std::runtime_error("fixed seat conflicts with protected neighbor: " + passenger.fixed_seat);
                }
                context.deterministic_blocked[neighbor] = true;
            }
        }
        if (passenger.need_single_empty) {
            bool available = false;
            for (int neighbor : seat.same_block_neighbors) {
                available = available || context.owner_by_seat[neighbor] < 0;
            }
            if (!available) {
                throw std::runtime_error("fixed protected passenger lacks an available neighbor: " + passenger.fixed_seat);
            }
        }

        const SsrRule rule = ssr_rule(problem, passenger);
        const bool requires_caregiver = passenger.need_cared || rule.requires_caregiver;
        if (requires_caregiver) {
            const bool allow_cross = passenger.need_cared && passenger.ssr.empty()
                ? false : rule.caregiver_allow_cross_aisle;
            const Group& group = problem.groups[passenger.group];
            std::vector<int> adults;
            for (int other : group.passengers) {
                if (other != passenger_index && is_adult_caregiver(problem.passengers[other])) {
                    adults.push_back(other);
                }
            }
            if (adults.empty()) {
                throw std::runtime_error("fixed cared passenger has no eligible caregiver");
            }
            bool all_fixed = true;
            bool adjacent = false;
            const auto& neighbors = allow_cross ? seat.row_neighbors : seat.same_block_neighbors;
            for (int adult : adults) {
                const std::string& fixed = problem.passengers[adult].fixed_seat;
                all_fixed = all_fixed && !fixed.empty();
                if (!fixed.empty()) {
                    const int adult_seat = problem.seat_index.at(fixed);
                    adjacent = adjacent || std::find(neighbors.begin(), neighbors.end(), adult_seat) != neighbors.end();
                }
            }
            if (all_fixed && !adjacent) {
                throw std::runtime_error("fixed cared passenger is not adjacent to a fixed caregiver");
            }
        }
    }

    for (bool by_row : {true, false}) {
        std::set<int> flagged_locations;
        std::map<std::pair<int, std::string>, int> counts;
        for (int passenger_index : fixed_passengers) {
            const Passenger& passenger = problem.passengers[passenger_index];
            if (passenger.ssr.empty()) continue;
            const Seat& seat = problem.seats[problem.seat_index.at(passenger.fixed_seat)];
            const int location = by_row ? seat.row : seat.row * 100 + seat.subrow;
            ++counts[{location, passenger.ssr}];
            if ((by_row && passenger.same_row_no_other_ssr)
                || (!by_row && passenger.same_subrow_no_other_ssr)) {
                flagged_locations.insert(location);
            }
        }
        for (const auto& count : counts) {
            if (flagged_locations.count(count.first.first) && count.second > 1) {
                throw std::runtime_error("fixed SSR conditional resource conflict");
            }
        }
    }
    return context;
}
}  // namespace full_cpp
