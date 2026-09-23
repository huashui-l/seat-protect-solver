#include "full_cpp_solver_core.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <limits>
#include <map>
#include <set>
#include <ostream>
#include <iomanip>
#include <tuple>
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
        if (const Value* item = algorithm->find("elite_patterns_per_group")) problem.rich.elite_patterns_per_group = std::max(2, static_cast<int>(item->number_or(12)));
        if (const Value* item = algorithm->find("structured_pattern_min_group_size")) problem.rich.structured_pattern_min_group_size = std::max(1, static_cast<int>(item->number_or(5)));
        if (const Value* item = algorithm->find("prioritize_front")) problem.prioritize_front = item->bool_or(problem.prioritize_front);
        if (const Value* item = algorithm->find("front_penalty_reduction")) problem.front_penalty_reduction = item->number_or(problem.front_penalty_reduction);
        if (const Value* item = algorithm->find("back_penalty_factor")) problem.back_penalty_factor = item->number_or(problem.back_penalty_factor);
        if (const Value* item = algorithm->find("group_centroid_x_factor")) problem.group_centroid_x_factor = item->number_or(problem.group_centroid_x_factor);
        if (const Value* item = algorithm->find("group_centroid_y_factor")) problem.group_centroid_y_factor = item->number_or(problem.group_centroid_y_factor);
        if (const Value* item = algorithm->find("baby_front_back_factor")) problem.baby_front_back_factor = item->number_or(problem.baby_front_back_factor);
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
        problem.rich.candidate_cap_retry = std::max(64, problem.rich.candidate_cap * 2);
        if (const Value* item = algorithm->find("candidate_cap_retry")) problem.rich.candidate_cap_retry = static_cast<int>(item->number_or(problem.rich.candidate_cap_retry));
        problem.rich.candidate_cap_full_retry = std::max(128,
            std::max(problem.rich.candidate_cap, problem.rich.candidate_cap_retry) * 2);
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
        if (const Value* item = algorithm->find("local_search_epsilon")) problem.rich.local_search_epsilon = item->number_or(1e-9);
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
            if (const Value* fixed = raw.find("newSeat")) {
                passenger.fixed_seat = optional_string(*fixed, "seatNum");
                passenger.has_new_seat = fixed->is_object() && !fixed->object.empty();
            }
            if (const Value* rules = raw.find("optionRule"); rules && rules->is_array()) {
                for (const Value& rule : rules->array) {
                    if (!rule.find("nearToilet")) continue;
                    passenger.has_near_toilet_preference = true;
                    passenger.prefer_near_toilet = optional_string(rule, "nearToilet") != "N";
                    const double weight = optional_number(rule, "weight");
                    passenger.near_toilet_preference_weight = std::isnan(weight) ? 1.0 : weight;
                    std::string desired = optional_string(rule, "nearToilet");
                    std::transform(desired.begin(), desired.end(), desired.begin(),
                        [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
                    passenger.rich_toilet_preferences.emplace_back(
                        desired == "Y", passenger.near_toilet_preference_weight);
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
    const Value* stage_algorithm = config.find("algorithm");
    problem.rich_stage_budgets = calculate_rich_stage_budgets(
        problem, stage_algorithm ? *stage_algorithm : Value{});
    const auto setting = [&](const char* key, double fallback) {
        const Value* item = stage_algorithm ? stage_algorithm->find(key) : nullptr;
        return item ? item->number_or(fallback) : fallback;
    };
    problem.rich_quality_repair_active = setting("business_time_limit_seconds", 5.0)
        >= setting("three_tier_min_business_time_seconds", 10.0);
    problem.rich_conflict_diversity_time_active = problem.rich_stage_budgets.business_time_limit
        >= setting("three_tier_min_business_time_seconds", 10.0);
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

static ScoreComponents score_components_impl(
    const Problem& problem, const std::vector<int>& assignment, int affected_group, bool rich_preferences
) {
    if (assignment.size() != problem.passengers.size()) {
        throw std::runtime_error("score assignment size mismatch");
    }
    ScoreComponents result;
    for (int passenger_index = 0;
         passenger_index < static_cast<int>(problem.passengers.size()); ++passenger_index) {
        const int new_index = assignment[passenger_index];
        if (new_index < 0 || (affected_group >= 0 && problem.passengers[passenger_index].group != affected_group)) continue;
        IndividualScoreComponents individual = evaluate_individual_score(
            problem, passenger_index, new_index
        );
        if (rich_preferences) {
            const auto& passenger = problem.passengers[passenger_index];
            if (problem.old_seat_index.count(passenger.old_seat)) {
                if (passenger.has_near_toilet_preference
                    && problem.seats[new_index].near_toilet != passenger.prefer_near_toilet)
                    individual.score_p -= problem.weight_t * passenger.near_toilet_preference_weight;
                for (const auto& preference : passenger.rich_toilet_preferences)
                    if (problem.seats[new_index].near_toilet != preference.first)
                        individual.score_p += problem.weight_t * preference.second;
            }
        }
        result.score_s += individual.score_s;
        result.score_v += individual.score_v;
        result.score_p += individual.score_p;
    }
    for (const Group& group : problem.groups) {
        if (affected_group >= 0 && group.id != problem.groups[affected_group].id) continue;
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
            if (affected_group >= 0 && problem.passengers[infant].group != affected_group
                && problem.passengers[other].group != affected_group) continue;
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

double evaluate_soft_score(const Problem& problem, const std::vector<int>& assignment) {
    return evaluate_score_components(problem, assignment).total();
}

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

bool rich_conflict_diversity_active(const Problem& problem,
    const std::vector<RichGroupRepairMetric>& construction_metrics
) {
    const int limit = problem.rich.elite_patterns_per_group;
    const int demand = problem.rich_stage_budgets.seat_demand;
    if (!problem.rich_conflict_diversity_time_active
        || problem.seats.size() >= problem.old_seats.size()
        || demand - static_cast<int>(problem.passengers.size()) < std::max(2, limit - 2)
        || static_cast<int>(problem.seats.size()) - demand < limit) return false;
    for (const auto& group : problem.groups) {
        if (group.passengers.size() < static_cast<size_t>(problem.rich.structured_pattern_min_group_size)) continue;
        if (!std::all_of(group.passengers.begin(), group.passengers.end(), [&](int p) {
            const auto& passenger = problem.passengers[p];
            return passenger.ssr.empty() && !passenger.need_cared && !passenger.has_new_seat
                && !passenger.need_single_empty && !passenger.need_both_empty;
        })) continue;
        const auto metric = std::find_if(construction_metrics.begin(), construction_metrics.end(),
            [&](const auto& m) { return m.group_id == group.id; });
        if (metric != construction_metrics.end() && metric->value_mismatch_score < 0.0) return true;
    }
    return false;
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

RichStageBudgets calculate_rich_stage_budgets(
    const Problem& problem, const native_json::Value& algorithm
) {
    auto number = [&](const char* key, double fallback) {
        const auto* value = algorithm.find(key);
        return value ? value->number_or(fallback) : fallback;
    };
    auto enabled = [&](const char* key, bool fallback) {
        const auto* value = algorithm.find(key);
        return value ? value->bool_or(fallback) : fallback;
    };
    RichStageBudgets result;
    result.business_time_limit = std::max(0.1, number("business_time_limit_seconds", 5.0));
    result.scoring_reserve = std::min(result.business_time_limit * 0.2,
        std::max(0.02, number("scoring_time_reserve", 0.1)));
    result.usable_time = std::max(0.0, result.business_time_limit - result.scoring_reserve);
    auto& stages = result.stages;
    stages = {
        {"construction", number("construction_time_budget", result.usable_time * 0.20)},
        {"repair", number("repair_time_budget", result.usable_time * 0.10)},
        {"vnd", number("vnd_time_budget", result.usable_time * 0.20)},
        {"pattern_generation", number("structured_pattern_time_budget", 1.6)},
        {"lns", number("lns_time_budget", result.usable_time * 0.50)},
        {"restricted_mip", number("restricted_pattern_mip_time_budget", 0.0)},
        {"protected_multigroup_mip", number("protected_multigroup_mip_time_budget", 0.0)},
    };
    const int travelers = static_cast<int>(problem.passengers.size());
    for (const auto& passenger : problem.passengers)
        result.seat_demand += 1 + (passenger.need_both_empty ? 2 : passenger.need_single_empty ? 1 : 0);
    result.post_protected_tail_reserve_active = result.business_time_limit >= 10.0
        && problem.seats.size() < problem.old_seats.size()
        && result.seat_demand == static_cast<int>(problem.seats.size())
        && result.seat_demand > travelers
        && double(travelers) / std::max<size_t>(1, problem.seats.size()) >= 0.93;
    result.post_protected_special_pricing_active = result.post_protected_tail_reserve_active
        && enabled("enable_post_protected_special_pricing", true);
    if (enabled("adaptive_stage_budgets", true)) {
        const double low = number("adaptive_budget_low_seat_demand", 100.0);
        const double high = std::max(low + 1.0, number("adaptive_budget_high_seat_demand", 130.0));
        const double load = std::min(1.0, std::max(0.0, (result.seat_demand - low) / (high - low)));
        stages["construction"] = 1.3 + 1.7 * load;
        stages["repair"] = 0.25;
        stages["vnd"] = 0.5 + 0.2 * load;
        stages["pattern_generation"] = 2.3 - 1.9 * load;
        stages["lns"] = 0.0;
        stages["restricted_mip"] = 0.4;
        const bool priority = enabled("enable_priority_multigroup_pattern_mip", false)
            && result.business_time_limit >= number("priority_multigroup_min_business_time_seconds", 10.0);
        stages["protected_multigroup_mip"] =
            enabled("enable_protected_multigroup_pattern_mip", false) || priority
                ? number("protected_multigroup_mip_time_budget", 8.0) : 0.0;
    }
    // Match Python's insertion order when summing before proportional scaling.
    const char* order[] = {"construction", "repair", "vnd", "pattern_generation",
        "lns", "restricted_mip", "protected_multigroup_mip"};
    double total = 0.0;
    for (const char* name : order) total += std::max(0.0, stages[name]);
    if (total > result.usable_time && total > 0.0) {
        const double scale = result.usable_time / total;
        for (auto& stage : stages) stage.second = std::max(0.0, stage.second) * scale;
    }
    return result;
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

RichCandidateCache build_rich_candidate_cache(const Problem& problem, const AssignmentState& state,
    const std::vector<double>* frozen_owner_regrets) {
    RichCandidateCache result;
    const int count = static_cast<int>(problem.passengers.size());
    const int seats = static_cast<int>(problem.seats.size());
    result.owner_regrets.resize(count, 0.0);
    result.costs.resize(count, std::vector<double>(seats));
    result.rankings.resize(count);
    const auto seat_value = [&](const Seat& seat) {
        if (!std::isnan(seat.explicit_value)) return seat.explicit_value;
        double value = seat.cabin == "Business" ? problem.business_seat_value : 0.0;
        if (seat.extra_legroom) value += problem.extra_legroom_value;
        if (seat.bassinet) value += problem.bassinet_value;
        return value;
    };
    std::unordered_map<std::string, int> owners;
    for (int p = 0; p < count; ++p) {
        const auto& passenger = problem.passengers[p];
        if (problem.old_seat_index.count(passenger.old_seat)) owners[passenger.old_seat] = p;
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
                for (int other = 0; other < count; ++other) {
                    const int assigned = state.passenger_to_seat[other];
                    if (assigned < 0 || problem.passengers[other].group_id == passenger.group_id) continue;
                    if (passenger.ssr != "BSCT" && problem.passengers[other].ssr != "BSCT") continue;
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

RichStageSchedule::RichStageSchedule(const RichStageBudgets& budgets,
                                     double allocation_start, double restricted_tail_budget,
                                     double search_deadline_limit)
    : budgets_(budgets), allocation_start_(allocation_start),
      search_deadline_(std::min(allocation_start + budgets.usable_time, search_deadline_limit)),
      restricted_tail_budget_(std::max(0.0, restricted_tail_budget)) {}

RichStageWindow RichStageSchedule::begin(const std::string& stage, double now,
                                         int construction_unassigned) {
    if (stage == "special_pricing") {
        return {pricing_reserve_, std::min(search_deadline_, now + pricing_reserve_)};
    }
    double effective = budgets_.stages.at(stage) + carry_;
    if (stage == "construction") {
        effective = budgets_.stages.at(stage);
        return {effective, std::min(search_deadline_, allocation_start_ + effective)};
    }
    if (stage == "vnd") {
        pricing_reserve_ = std::min(std::max(0.0, effective - 0.1),
            budgets_.post_protected_tail_reserve_active ? 2.0 : 0.0);
        effective -= pricing_reserve_;
    }
    if (stage == "restricted_mip") {
        effective = std::max(budgets_.stages.at(stage), restricted_tail_budget_)
            + std::max(0.0, lns_deadline_ - now);
    }
    double deadline = std::min(search_deadline_, now + effective);
    if (stage == "repair") {
        if (construction_unassigned > 0) deadline = search_deadline_;
        effective = std::max(0.0, deadline - now);
    }
    if (stage == "lns") lns_deadline_ = deadline;
    return {effective, deadline};
}

void RichStageSchedule::finish(const std::string& stage,
                               const RichStageWindow& window, double now) {
    // Python keeps protected-stage carry across special pricing. LNS carry is
    // evaluated at restricted-stage entry, after intervening bookkeeping.
    if (stage != "special_pricing" && stage != "lns" && stage != "restricted_mip") {
        carry_ = std::max(0.0, window.deadline - now);
    }
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

bool AssignmentState::rich_seat_feasible(int passenger_index, int seat_index, int chosen_block, int excluded_seat) const {
    if (passenger_index < 0 || passenger_index >= static_cast<int>(problem.passengers.size())
        || seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) return false;
    if ((seat_to_passenger[seat_index] >= 0 && seat_index != excluded_seat) || blocked_count[seat_index] > 0) return false;
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
            if ((seat_to_passenger[neighbor] >= 0 && neighbor != excluded_seat) || blocked_count[neighbor] > 0) return false;
        }
    }
    if (passenger.need_single_empty) {
        bool available = false;
        for (int neighbor : seat.same_block_neighbors) {
            if ((seat_to_passenger[neighbor] < 0 || neighbor == excluded_seat) && blocked_count[neighbor] == 0
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
                if (other_index < 0 || other_seat == excluded_seat) continue;
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

void RichEliteStore::capture(const AssignmentState& state, const std::string& source) {
    const auto& problem = state.problem;
    for (int g = 0; g < static_cast<int>(problem.groups.size()); ++g) {
        const auto& group = problem.groups[g];
        RichElitePattern pattern;
        bool complete = true;
        for (int p : group.passengers) {
            const int seat = state.passenger_to_seat[p];
            if (seat < 0) { complete = false; break; }
            const int host = problem.passengers[p].hostnum;
            pattern.assignments.emplace_back(host, problem.seats[seat].id);
            std::vector<std::string> blocks;
            for (int blocked : state.assigned_blocked[p]) blocks.push_back(problem.seats[blocked].id);
            pattern.blocked_by_host.emplace_back(host, std::move(blocks));
        }
        if (!complete) continue;
        pattern.local_score = evaluate_rich_group_score(problem, state.passenger_to_seat, g).total();
        pattern.source = source;
        pattern.pinned = true;
        // A captured current placement owns all of its resources, so it has
        // no conflicts even when candidate conflict diversity is enabled.
        record(group.id, std::move(pattern), {}, false);
    }
}

void RichEliteStore::record_candidate(int group_id, RichElitePattern pattern,
    const AssignmentState& state, bool conflict_diversity_active
) {
    std::map<std::string, int> owners;
    for (int p : state.assignment_order)
        owners[state.problem.seats[state.passenger_to_seat[p]].id] = state.problem.passengers[p].group_id;
    for (int p : state.assignment_order)
        for (int seat : state.assigned_blocked[p])
            owners[state.problem.seats[seat].id] = state.problem.passengers[p].group_id;
    record(group_id, std::move(pattern), owners, conflict_diversity_active);
}

void RichEliteStore::record(int group_id, RichElitePattern pattern,
    const std::map<std::string, int>& owner_by_resource, bool conflict_diversity_active
) {
    std::sort(pattern.assignments.begin(), pattern.assignments.end());
    pattern.blocked_by_host.erase(std::remove_if(pattern.blocked_by_host.begin(), pattern.blocked_by_host.end(),
        [](const auto& entry) { return entry.second.empty(); }), pattern.blocked_by_host.end());
    for (auto& entry : pattern.blocked_by_host) std::sort(entry.second.begin(), entry.second.end());
    std::sort(pattern.blocked_by_host.begin(), pattern.blocked_by_host.end());
    pattern.occupied_seats.clear();
    for (const auto& entry : pattern.assignments) pattern.occupied_seats.push_back(entry.second);
    std::sort(pattern.occupied_seats.begin(), pattern.occupied_seats.end());
    std::set<std::string> blocked;
    for (const auto& entry : pattern.blocked_by_host) blocked.insert(entry.second.begin(), entry.second.end());
    pattern.blocked_seats.assign(blocked.begin(), blocked.end());
    blocked.insert(pattern.occupied_seats.begin(), pattern.occupied_seats.end());
    pattern.seat_resources.assign(blocked.begin(), blocked.end());
    std::set<int> conflicts;
    if (conflict_diversity_active) for (const auto& seat : pattern.seat_resources) {
        const auto owner = owner_by_resource.find(seat);
        if (owner != owner_by_resource.end() && owner->second != group_id) conflicts.insert(owner->second);
    }
    pattern.conflict_groups.assign(conflicts.begin(), conflicts.end());
    auto& patterns = groups_[group_id];
    const auto existing = std::find_if(patterns.begin(), patterns.end(), [&](const auto& old) {
        return old.assignments == pattern.assignments && old.blocked_by_host == pattern.blocked_by_host;
    });
    if (existing == patterns.end()) patterns.push_back(std::move(pattern));
    else if (pattern.local_score > existing->local_score) *existing = std::move(pattern);
    else if (pattern.pinned) existing->pinned = true;
    if (patterns.size() <= static_cast<size_t>(limit_)) return;
    std::map<std::vector<int>, int> conflict_counts;
    for (const auto& item : patterns) ++conflict_counts[item.conflict_groups];
    bool has_duplicate_conflict = false;
    for (const auto& item : patterns)
        if (!item.pinned && conflict_counts[item.conflict_groups] > 1) has_duplicate_conflict = true;
    auto worst = patterns.end();
    for (auto item = patterns.begin(); item != patterns.end(); ++item) {
        if (item->pinned || (has_duplicate_conflict && conflict_counts[item->conflict_groups] <= 1)) continue;
        if (worst == patterns.end() || item->local_score < worst->local_score) worst = item;
    }
    if (worst != patterns.end()) patterns.erase(worst);
}

void write_rich_elite_store(std::ostream& output, const RichEliteStore& store) {
    output << '{';
    bool first_group = true;
    for (const auto& group : store.groups()) {
        if (!first_group) output << ',';
        first_group = false;
        output << std::quoted(std::to_string(group.first)) << ":[";
        bool first_pattern = true;
        for (const auto& item : group.second) {
            if (!first_pattern) output << ',';
            first_pattern = false;
            output << "{\"assignments\":[";
            for (size_t i = 0; i < item.assignments.size(); ++i) {
                if (i) output << ',';
                output << '[' << item.assignments[i].first << ',' << std::quoted(item.assignments[i].second) << ']';
            }
            output << "],\"blocked_by_host\":[";
            const auto strings = [&](const auto& values) {
                output << '[';
                for (size_t i = 0; i < values.size(); ++i) {
                    if (i) output << ',';
                    output << std::quoted(values[i]);
                }
                output << ']';
            };
            for (size_t i = 0; i < item.blocked_by_host.size(); ++i) {
                if (i) output << ',';
                output << '[' << item.blocked_by_host[i].first << ',';
                strings(item.blocked_by_host[i].second);
                output << ']';
            }
            output << "],\"occupied_seats\":"; strings(item.occupied_seats);
            output << ",\"blocked_seats\":"; strings(item.blocked_seats);
            output << ",\"seat_resources\":"; strings(item.seat_resources);
            output << ",\"conflict_groups\":[";
            for (size_t i = 0; i < item.conflict_groups.size(); ++i) {
                if (i) output << ',';
                output << item.conflict_groups[i];
            }
            output << "],\"local_score\":" << item.local_score
                << ",\"source\":" << std::quoted(item.source)
                << ",\"pinned\":" << (item.pinned ? "true" : "false") << '}';
        }
        output << ']';
    }
    output << '}';
}

}  // namespace full_cpp
