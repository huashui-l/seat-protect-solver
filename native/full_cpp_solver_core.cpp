#include "full_cpp_solver_core.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>

namespace full_cpp {
namespace {

using native_json::Value;

const Value& required(const Value& value, const std::string& key) {
    return value.at(key);
}

std::string optional_string(const Value& value, const std::string& key) {
    const Value* item = value.find(key);
    return item ? item->string_or() : std::string{};
}

bool optional_bool(const Value& value, const std::string& key) {
    const Value* item = value.find(key);
    return item && item->bool_or();
}

bool yes(const Value& value, const std::string& key) {
    return optional_string(value, key) == "Y";
}

double optional_number(const Value& value, const std::string& key) {
    const Value* item = value.find(key);
    if (!item) return std::numeric_limits<double>::quiet_NaN();
    if (item->is_number()) return item->number;
    if (item->is_string() && !item->string.empty()) {
        try { return std::stod(item->string); } catch (const std::exception&) { return std::numeric_limits<double>::quiet_NaN(); }
    }
    return std::numeric_limits<double>::quiet_NaN();
}

std::string cabin_class(const std::string& raw) {
    if (raw == "F" || raw == "A" || raw == "First") return "First";
    if (raw == "C" || raw == "J" || raw == "Business") return "Business";
    if (raw == "W" || raw == "P" || raw == "PremiumEconomy") return "PremiumEconomy";
    if (raw == "Y" || raw == "E" || raw == "Economy") return "Economy";
    return raw;
}

std::filesystem::path resolve_from_config(
    const std::filesystem::path& config_path,
    const std::string& raw
) {
    std::filesystem::path path(raw);
    if (path.is_absolute()) return path;
    return config_path.parent_path() / path;
}

void build_topology(Problem& problem) {
    std::map<int, std::vector<int>> rows;
    for (int index = 0; index < static_cast<int>(problem.seats.size()); ++index) {
        rows[problem.seats[index].row].push_back(index);
    }
    for (auto& row_item : rows) {
        auto& indices = row_item.second;
        std::sort(indices.begin(), indices.end(), [&](int left, int right) {
            return problem.seats[left].column < problem.seats[right].column;
        });
        std::vector<bool> aisle_break(indices.size(), false);
        for (size_t index = 0; index + 1 < indices.size();) {
            if (problem.seats[indices[index]].aisle
                && problem.seats[indices[index + 1]].aisle) {
                aisle_break[index] = true;
                index += 2;
            } else {
                ++index;
            }
        }
        std::vector<double> raw_x(indices.size(), 0.0);
        int subrow = 0;
        for (size_t index = 0; index < indices.size(); ++index) {
            Seat& seat = problem.seats[indices[index]];
            seat.index_in_row = static_cast<int>(index);
            seat.subrow = subrow;
            if (index + 1 < indices.size()) {
                raw_x[index + 1] = raw_x[index] + problem.seat_spacing
                    + (aisle_break[index] ? problem.aisle_gap : 0.0);
                if (aisle_break[index]) ++subrow;
            }
        }
        const double center = indices.empty()
            ? 0.0 : (raw_x.front() + raw_x.back()) / 2.0;
        for (size_t index = 0; index < indices.size(); ++index) {
            Seat& seat = problem.seats[indices[index]];
            seat.x = raw_x[index] - center;
            seat.y = seat.row * problem.row_spacing;
            for (int offset : {-1, 1}) {
                const int other_position = static_cast<int>(index) + offset;
                if (other_position < 0
                    || other_position >= static_cast<int>(indices.size())) continue;
                const int other = indices[other_position];
                seat.row_neighbors.push_back(other);
                if (problem.seats[other].subrow == seat.subrow) {
                    seat.same_block_neighbors.push_back(other);
                }
            }
        }
    }
}

void load_seats(const Value& seatmap, Problem& problem) {
    const Value& seats = required(seatmap, "seats");
    if (!seats.is_array()) throw std::runtime_error("seatmap.seats must be an array");
    problem.seats.reserve(seats.array.size());
    for (const Value& raw : seats.array) {
        Seat seat;
        seat.id = required(raw, "seatId").string_or();
        seat.row = static_cast<int>(std::llround(required(raw, "row").number_or()));
        seat.column = required(raw, "col").string_or();
        seat.window = optional_bool(raw, "isWindow");
        seat.aisle = optional_bool(raw, "isAisle");
        seat.exit_row = optional_bool(raw, "isExitRow");
        seat.bassinet = optional_bool(raw, "hasBassinet");
        seat.extra_legroom = optional_bool(raw, "extraLegroom");
        seat.near_toilet = optional_bool(raw, "nearToilet") || optional_bool(raw, "isNearToilet");
        seat.cabin = optional_string(raw, "seatClass");
        seat.explicit_value = optional_number(raw, "seatValue");
        if (std::isnan(seat.explicit_value)) seat.explicit_value = optional_number(raw, "value");
        if (!problem.seat_index.emplace(seat.id, static_cast<int>(problem.seats.size())).second) {
            throw std::runtime_error("duplicate seatId: " + seat.id);
        }
        problem.seats.push_back(std::move(seat));
    }
    build_topology(problem);
}

SsrRule ssr_rule(const Problem& problem, const Passenger& passenger) {
    SsrRule rule;
    if (passenger.ssr.empty()) {
        rule.allow_exit_row = true;
        return rule;
    }
    const auto found = problem.ssr_rules.find(passenger.ssr);
    return found == problem.ssr_rules.end() ? rule : found->second;
}

bool is_adult_caregiver(const Passenger& passenger) {
    return passenger.ssr.empty() && !passenger.need_cared
        && !passenger.need_both_empty && !passenger.need_single_empty;
}

}  // namespace

Problem load_problem(const std::string& case_path_raw, const std::string& config_path_raw) {
    const std::filesystem::path case_path(case_path_raw);
    const std::filesystem::path config_path(config_path_raw);
    const Value case_json = native_json::parse_file(case_path.string());
    const Value config = native_json::parse_file(config_path.string());
    Problem problem;
    problem.case_id = optional_string(case_json, "caseId");
    problem.direction = optional_string(case_json, "direction");
    const Value* geometry = config.find("seat_geometry");
    if (geometry) {
        if (const Value* item = geometry->find("seat_spacing")) problem.seat_spacing = item->number_or(1.0);
        if (const Value* item = geometry->find("aisle_gap")) problem.aisle_gap = item->number_or(0.8);
        if (const Value* item = geometry->find("row_spacing")) problem.row_spacing = item->number_or(1.5);
    }
    if (const Value* weights = config.find("weights")) {
        if (const Value* item = weights->find("w_s")) problem.weight_s = item->number_or(problem.weight_s);
        if (const Value* item = weights->find("w_v")) problem.weight_v = item->number_or(problem.weight_v);
        if (const Value* item = weights->find("w_p")) problem.weight_p = item->number_or(problem.weight_p);
        if (const Value* item = weights->find("w_c")) problem.weight_c = item->number_or(problem.weight_c);
        if (const Value* item = weights->find("w_b")) problem.weight_b = item->number_or(problem.weight_b);
        if (const Value* item = weights->find("w_t")) problem.weight_t = item->number_or(problem.weight_t);
    }
    if (const Value* values = config.find("seat_value")) {
        if (const Value* item = values->find("business_base")) problem.business_seat_value = item->number_or(problem.business_seat_value);
        if (const Value* item = values->find("extra_legroom")) problem.extra_legroom_value = item->number_or(problem.extra_legroom_value);
        if (const Value* item = values->find("bassinet")) problem.bassinet_value = item->number_or(problem.bassinet_value);
    }
    if (const Value* algorithm = config.find("algorithm")) {
        if (const Value* item = algorithm->find("prioritize_front")) problem.prioritize_front = item->bool_or(problem.prioritize_front);
        if (const Value* item = algorithm->find("front_penalty_reduction")) problem.front_penalty_reduction = item->number_or(problem.front_penalty_reduction);
        if (const Value* item = algorithm->find("back_penalty_factor")) problem.back_penalty_factor = item->number_or(problem.back_penalty_factor);
        if (const Value* item = algorithm->find("group_centroid_x_factor")) problem.group_centroid_x_factor = item->number_or(problem.group_centroid_x_factor);
        if (const Value* item = algorithm->find("group_centroid_y_factor")) problem.group_centroid_y_factor = item->number_or(problem.group_centroid_y_factor);
        if (const Value* item = algorithm->find("baby_front_back_factor")) problem.baby_front_back_factor = item->number_or(problem.baby_front_back_factor);
        if (const Value* item = algorithm->find("construction_time_budget")) problem.rich.construction_time_budget = item->number_or(problem.rich.construction_time_budget);
        if (const Value* item = algorithm->find("repair_time_budget")) problem.rich.repair_time_budget = item->number_or(problem.rich.repair_time_budget);
        if (const Value* item = algorithm->find("scoring_time_reserve")) problem.rich.scoring_time_reserve = item->number_or(problem.rich.scoring_time_reserve);
        if (const Value* item = algorithm->find("stage3_time_budget")) problem.rich.stage3_time_budget = item->number_or(problem.rich.stage3_time_budget);
        if (const Value* item = algorithm->find("enable_restricted_pattern_mip")) problem.rich.enable_restricted_pattern_mip = item->bool_or(problem.rich.enable_restricted_pattern_mip);
        if (const Value* item = algorithm->find("restricted_pattern_mip_time_budget")) problem.rich.restricted_pattern_mip_time_budget = item->number_or(problem.rich.restricted_pattern_mip_time_budget);
        if (const Value* item = algorithm->find("restricted_pattern_mip_tail_budget")) problem.rich.restricted_pattern_mip_tail_budget = item->number_or(problem.rich.restricted_pattern_mip_tail_budget);
        if (const Value* item = algorithm->find("restricted_pattern_mip_attempts")) problem.rich.restricted_pattern_mip_attempts = static_cast<int>(item->number_or(problem.rich.restricted_pattern_mip_attempts));
        if (const Value* item = algorithm->find("enable_pattern_local_branching")) problem.rich.enable_pattern_local_branching = item->bool_or(problem.rich.enable_pattern_local_branching);
        if (const Value* item = algorithm->find("pattern_local_branching_initial_radius")) problem.rich.pattern_local_branching_initial_radius = static_cast<int>(item->number_or(problem.rich.pattern_local_branching_initial_radius));
        if (const Value* item = algorithm->find("pattern_local_branching_radius_growth")) problem.rich.pattern_local_branching_radius_growth = static_cast<int>(item->number_or(problem.rich.pattern_local_branching_radius_growth));
        if (const Value* item = algorithm->find("pattern_local_branching_max_radius")) problem.rich.pattern_local_branching_max_radius = static_cast<int>(item->number_or(problem.rich.pattern_local_branching_max_radius));
        if (const Value* item = algorithm->find("candidate_cap")) problem.rich.candidate_cap = static_cast<int>(item->number_or(problem.rich.candidate_cap));
        if (const Value* item = algorithm->find("candidate_cap_retry")) problem.rich.candidate_cap_retry = static_cast<int>(item->number_or(problem.rich.candidate_cap_retry));
        if (const Value* item = algorithm->find("candidate_cap_full_retry")) problem.rich.candidate_cap_full_retry = static_cast<int>(item->number_or(problem.rich.candidate_cap_full_retry));
        if (const Value* item = algorithm->find("beam_width_small")) problem.rich.beam_width_small = static_cast<int>(item->number_or(problem.rich.beam_width_small));
        if (const Value* item = algorithm->find("beam_moves_small")) problem.rich.beam_moves_small = static_cast<int>(item->number_or(problem.rich.beam_moves_small));
        if (const Value* item = algorithm->find("beam_width_medium")) problem.rich.beam_width_medium = static_cast<int>(item->number_or(problem.rich.beam_width_medium));
        if (const Value* item = algorithm->find("beam_moves_medium")) problem.rich.beam_moves_medium = static_cast<int>(item->number_or(problem.rich.beam_moves_medium));
        if (const Value* item = algorithm->find("beam_width_large")) problem.rich.beam_width_large = static_cast<int>(item->number_or(problem.rich.beam_width_large));
        if (const Value* item = algorithm->find("beam_moves_large")) problem.rich.beam_moves_large = static_cast<int>(item->number_or(problem.rich.beam_moves_large));
        if (const Value* item = algorithm->find("small_group_dfs_enabled")) problem.rich.small_group_dfs_enabled = item->bool_or(problem.rich.small_group_dfs_enabled);
        if (const Value* item = algorithm->find("small_group_dfs_max_size")) problem.rich.small_group_dfs_max_size = static_cast<int>(item->number_or(problem.rich.small_group_dfs_max_size));
        if (const Value* item = algorithm->find("small_group_dfs_node_limit")) problem.rich.small_group_dfs_node_limit = static_cast<long long>(item->number_or(problem.rich.small_group_dfs_node_limit));
        if (const Value* item = algorithm->find("small_group_dfs_time_limit")) problem.rich.small_group_dfs_time_limit = item->number_or(problem.rich.small_group_dfs_time_limit);
        if (const Value* item = algorithm->find("old_seat_reservation_pressure")) problem.rich.old_seat_reservation_pressure = item->number_or(problem.rich.old_seat_reservation_pressure);
        if (const Value* item = algorithm->find("paired_rescue_option_cap")) problem.rich.paired_rescue_option_cap = static_cast<int>(item->number_or(problem.rich.paired_rescue_option_cap));
        if (const Value* item = algorithm->find("paired_joint_rebuild_candidate_cap")) problem.rich.paired_joint_rebuild_candidate_cap = static_cast<int>(item->number_or(problem.rich.paired_joint_rebuild_candidate_cap));
        if (const Value* item = algorithm->find("paired_joint_rebuild_node_limit")) problem.rich.paired_joint_rebuild_node_limit = static_cast<long long>(item->number_or(problem.rich.paired_joint_rebuild_node_limit));
        if (const Value* item = algorithm->find("final_repair_node_limit")) problem.rich.final_repair_node_limit = static_cast<int>(item->number_or(problem.rich.final_repair_node_limit));
        if (const Value* item = algorithm->find("final_repair_time_limit")) problem.rich.final_repair_time_limit = item->number_or(problem.rich.final_repair_time_limit);
        if (const Value* item = algorithm->find("vnd_time_budget")) problem.rich.vnd_time_budget = item->number_or(problem.rich.vnd_time_budget);
        if (const Value* item = algorithm->find("local_search_candidate_cap")) problem.rich.local_search_candidate_cap = static_cast<int>(item->number_or(problem.rich.local_search_candidate_cap));
        if (const Value* item = algorithm->find("local_search_cycle_candidate_cap")) problem.rich.local_search_cycle_candidate_cap = static_cast<int>(item->number_or(problem.rich.local_search_cycle_candidate_cap));
        if (const Value* item = algorithm->find("local_search_related_candidate_cap")) problem.rich.local_search_related_candidate_cap = static_cast<int>(item->number_or(problem.rich.local_search_related_candidate_cap));
        if (const Value* item = algorithm->find("local_search_related_group_cap")) problem.rich.local_search_related_group_cap = static_cast<int>(item->number_or(problem.rich.local_search_related_group_cap));
    }
    if (const Value* mandatory = config.find("mandatory_rules")) {
        if (const Value* item = mandatory->find("both_side_empty_allow_cross_aisle")) {
            problem.both_side_empty_allow_cross_aisle = item->bool_or();
        }
        if (const Value* item = mandatory->find("require_two_real_neighbors")) {
            problem.require_two_real_neighbors = item->bool_or(true);
        }
    }
    if (const Value* rules = config.find("ssr_rules")) {
        for (const auto& item : rules->object) {
            SsrRule rule;
            rule.allow_exit_row = optional_bool(item.second, "allow_exit_row");
            rule.requires_caregiver = optional_bool(item.second, "requires_caregiver");
            rule.caregiver_allow_cross_aisle = optional_bool(item.second, "caregiver_allow_cross_aisle");
            rule.requires_aisle = optional_bool(item.second, "requires_aisle");
            rule.requires_bassinet = optional_bool(item.second, "requires_bassinet");
            problem.ssr_rules.emplace(item.first, rule);
        }
    }
    const Value& contract = required(config, "input_contract");
    const Value& directions = required(contract, "seatmaps_by_direction");
    const Value& direction = required(directions, problem.direction);
    const std::filesystem::path seatmap_path = resolve_from_config(
        config_path, required(direction, "new").string_or()
    );
    const Value seatmap = native_json::parse_file(seatmap_path.string());
    load_seats(seatmap, problem);
    const std::filesystem::path old_seatmap_path = resolve_from_config(
        config_path, required(direction, "old").string_or()
    );
    Problem old_problem;
    old_problem.seat_spacing = problem.seat_spacing;
    old_problem.aisle_gap = problem.aisle_gap;
    old_problem.row_spacing = problem.row_spacing;
    load_seats(native_json::parse_file(old_seatmap_path.string()), old_problem);
    problem.old_seats = std::move(old_problem.seats);
    problem.old_seat_index = std::move(old_problem.seat_index);

    const Value& groups = required(case_json, "groups");
    if (!groups.is_array()) throw std::runtime_error("case.groups must be an array");
    problem.groups.reserve(groups.array.size());
    for (const Value& raw_group : groups.array) {
        Group group;
        group.id = static_cast<int>(std::llround(required(raw_group, "groupId").number_or()));
        const Value& passengers = required(raw_group, "psrs");
        for (const Value& raw : passengers.array) {
            Passenger passenger;
            passenger.group = static_cast<int>(problem.groups.size());
            passenger.group_id = group.id;
            passenger.hostnum = static_cast<int>(std::llround(required(raw, "hostnum").number_or()));
            passenger.cabin = cabin_class(required(raw, "cabin").string_or());
            passenger.ssr = optional_string(raw, "ssr");
            passenger.need_cared = yes(raw, "needCared");
            if (const Value* old_seat = raw.find("oldSeat")) passenger.old_seat = optional_string(*old_seat, "seatNum");
            if (const Value* old_seat = raw.find("oldSeat")) passenger.old_seat_value = optional_number(*old_seat, "seatValue");
            if (const Value* fixed = raw.find("newSeat")) passenger.fixed_seat = optional_string(*fixed, "seatNum");
            if (const Value* rules = raw.find("optionRule"); rules && rules->is_array()) {
                for (const Value& rule : rules->array) {
                    if (!rule.find("nearToilet")) continue;
                    passenger.has_near_toilet_preference = true;
                    passenger.prefer_near_toilet = optional_string(rule, "nearToilet") != "N";
                    const double weight = optional_number(rule, "weight");
                    passenger.near_toilet_preference_weight = std::isnan(weight) ? 1.0 : weight;
                }
            }
            if (const Value* mandatory = raw.find("mandatoryRule")) {
                passenger.need_both_empty = yes(*mandatory, "needBothSideEmpty");
                passenger.need_single_empty = yes(*mandatory, "needSingleSideEmpty");
                passenger.same_subrow_no_other_ssr = yes(*mandatory, "sameSubRowNoOtherSSR");
                passenger.same_row_no_other_ssr = yes(*mandatory, "sameRowNoOtherSSR");
            }
            group.passengers.push_back(static_cast<int>(problem.passengers.size()));
            problem.passengers.push_back(std::move(passenger));
        }
        problem.groups.push_back(std::move(group));
    }
    return problem;
}

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

ScoreComponents evaluate_score_components(
    const Problem& problem, const std::vector<int>& assignment
) {
    if (assignment.size() != problem.passengers.size()) {
        throw std::runtime_error("score assignment size mismatch");
    }
    ScoreComponents result;
    for (int passenger_index = 0;
         passenger_index < static_cast<int>(problem.passengers.size()); ++passenger_index) {
        const int new_index = assignment[passenger_index];
        if (new_index < 0) continue;
        const IndividualScoreComponents individual = evaluate_individual_score(
            problem, passenger_index, new_index
        );
        result.score_s += individual.score_s;
        result.score_v += individual.score_v;
        result.score_p += individual.score_p;
    }
    for (const Group& group : problem.groups) {
        if (group.passengers.size() <= 1) continue;
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
                || problem.passengers[other].group == problem.passengers[infant].group) continue;
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

double evaluate_soft_score(const Problem& problem, const std::vector<int>& assignment) {
    return evaluate_score_components(problem, assignment).total();
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

bool AssignmentState::can_assign(int passenger_index, int seat_index, int chosen_block) const {
    if (passenger_index < 0 || passenger_index >= static_cast<int>(problem.passengers.size())
        || seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) return false;
    if (passenger_to_seat[passenger_index] >= 0 || seat_to_passenger[seat_index] >= 0
        || blocked_count[seat_index] > 0) return false;
    const Passenger& passenger = problem.passengers[passenger_index];
    const Seat& seat = problem.seats[seat_index];
    if (!passenger.fixed_seat.empty() && passenger.fixed_seat != seat.id) return false;
    if (!passenger.cabin.empty() && passenger.cabin != seat.cabin) return false;
    const SsrRule rule = ssr_rule(problem, passenger);
    if ((seat.exit_row && !rule.allow_exit_row)
        || (rule.requires_bassinet && !seat.bassinet)
        || (rule.requires_aisle && !seat.aisle)) return false;

    if (passenger.need_both_empty) {
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        if ((problem.require_two_real_neighbors && neighbors.size() != 2) || neighbors.empty()) return false;
        for (int neighbor : neighbors) {
            if (seat_to_passenger[neighbor] >= 0 || blocked_count[neighbor] > 0) return false;
        }
    }
    if (passenger.need_single_empty) {
        bool available = false;
        for (int neighbor : seat.same_block_neighbors) {
            if (seat_to_passenger[neighbor] < 0 && blocked_count[neighbor] == 0
                && (chosen_block < 0 || chosen_block == neighbor)) available = true;
        }
        if (!available) return false;
    }

    if (!passenger.ssr.empty()) {
        for (bool by_row : {true, false}) {
            bool active = by_row ? passenger.same_row_no_other_ssr
                                 : passenger.same_subrow_no_other_ssr;
            int same_ssr = 0;
            for (int other_seat = 0;
                 other_seat < static_cast<int>(seat_ssr_passenger.size()); ++other_seat) {
                const int other_index = seat_ssr_passenger[other_seat];
                if (other_index < 0) continue;
                const Seat& other = problem.seats[other_seat];
                if (other.row != seat.row || (!by_row && other.subrow != seat.subrow)) continue;
                const Passenger& other_passenger = problem.passengers[other_index];
                active = active || (by_row ? other_passenger.same_row_no_other_ssr
                                           : other_passenger.same_subrow_no_other_ssr);
                same_ssr += other_passenger.ssr == passenger.ssr;
            }
            if (active && same_ssr > 0) return false;
        }
    }
    return true;
}

bool AssignmentState::assign(int passenger_index, int seat_index, int chosen_block) {
    if (!can_assign(passenger_index, seat_index, chosen_block)) return false;
    const Passenger& passenger = problem.passengers[passenger_index];
    const Seat& seat = problem.seats[seat_index];
    seat_to_passenger[seat_index] = passenger_index;
    passenger_to_seat[passenger_index] = seat_index;
    owner_group_by_seat[seat_index] = passenger.group;
    if (!passenger.ssr.empty()) seat_ssr_passenger[seat_index] = passenger_index;
    if (passenger.need_both_empty) {
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        for (int neighbor : neighbors) {
            ++blocked_count[neighbor];
            assigned_blocked[passenger_index].push_back(neighbor);
        }
    } else if (passenger.need_single_empty) {
        int block = chosen_block;
        if (block < 0) {
            for (int neighbor : seat.same_block_neighbors) {
                if (seat_to_passenger[neighbor] < 0 && blocked_count[neighbor] == 0) {
                    block = neighbor;
                    break;
                }
            }
        }
        ++blocked_count[block];
        assigned_blocked[passenger_index].push_back(block);
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
    owner_group_by_seat[seat] = -1;
    seat_ssr_passenger[seat] = -1;
}

AssignmentSnapshot AssignmentState::save() const {
    return {seat_to_passenger, passenger_to_seat, blocked_count, assigned_blocked,
            owner_group_by_seat, seat_ssr_passenger};
}

void AssignmentState::restore(AssignmentSnapshot snapshot) {
    seat_to_passenger = std::move(snapshot.seat_to_passenger);
    passenger_to_seat = std::move(snapshot.passenger_to_seat);
    blocked_count = std::move(snapshot.blocked_count);
    assigned_blocked = std::move(snapshot.assigned_blocked);
    owner_group_by_seat = std::move(snapshot.owner_group_by_seat);
    seat_ssr_passenger = std::move(snapshot.seat_ssr_passenger);
}

}  // namespace full_cpp
