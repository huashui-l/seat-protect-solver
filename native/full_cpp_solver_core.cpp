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
#include <charconv>
#include <cstring>
#include <functional>
#include <numeric>

namespace full_cpp {
namespace {

using native_json::Value;

// Equality key for Python json.dumps(sort_keys=True), without needing its textual float format.
// Integer literals stay exact; float literals compare as parsed doubles, including signed zero.
std::string rich_raw_fingerprint(const Value& value) {
    const auto frame = [](const std::string& text) { return std::to_string(text.size()) + ":" + text; };
    switch (value.type) {
    case Value::Type::Null: return "n";
    case Value::Type::Boolean: return value.boolean ? "t" : "f";
    case Value::Type::String: return "s" + frame(value.string);
    case Value::Type::Number: {
        if (value.number_token.find_first_of(".eE") == std::string::npos)
            return "i" + frame(value.number_token == "-0" ? "0" : value.number_token);
        std::uint64_t bits;
        std::memcpy(&bits, &value.number, sizeof(bits));
        return "d" + frame(std::to_string(bits));
    }
    case Value::Type::Array: {
        std::string result = "a";
        for (const auto& item : value.array) result += frame(rich_raw_fingerprint(item));
        return result;
    }
    case Value::Type::Object: {
        std::string result = "o";
        for (const auto& item : value.object) result += frame(item.first) + frame(rich_raw_fingerprint(item.second));
        return result;
    }
    }
    throw std::runtime_error("unknown JSON value type");
}

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
    const auto& direction = required(required(required(config, "input_contract"), "seatmaps_by_direction"),
        optional_string(case_json, "direction"));
    return make_problem(case_json, config,
        native_json::parse_file(resolve_from_config(config_path, required(direction, "new").string_or()).string()),
        native_json::parse_file(resolve_from_config(config_path, required(direction, "old").string_or()).string()));
}

Problem make_problem(const Value& case_json, const Value& config, const Value& seatmap, const Value& old_seatmap) {
    Problem problem;
    if (const auto* pricing = config.find("column_generation")) problem.rich_pricing_config = *pricing;
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
        if (const Value* item = algorithm->find("multigroup_pattern_group_size_limit")) problem.rich.multigroup_pattern_group_size_limit = std::max(2, static_cast<int>(item->number_or(6)));
        if (const Value* item = algorithm->find("multigroup_option_limit")) problem.rich.multigroup_option_limit = std::max(10, static_cast<int>(item->number_or(60)));
        if (const Value* item = algorithm->find("multigroup_neighborhood_extra_seats")) problem.rich.multigroup_neighborhood_extra_seats = std::max(1, static_cast<int>(item->number_or(2)));
        if (const Value* item = algorithm->find("elite_patterns_from_pricing_per_call")) problem.rich.elite_patterns_from_pricing_per_call = std::max(1, static_cast<int>(item->number_or(3)));
        if (const Value* item = algorithm->find("enable_conflict_component_lns")) problem.rich.conflict_component_lns_enabled = item->bool_or(false);
        if (const Value* item = algorithm->find("multigroup_related_seat_cap")) problem.rich.lns_related_seat_cap = std::max(16, static_cast<int>(item->number_or(80)));
        if (const Value* item = algorithm->find("multigroup_related_group_cap")) problem.rich.lns_related_group_cap = std::max(3, static_cast<int>(item->number_or(10)));
        if (const Value* item = algorithm->find("multigroup_min_component_size")) problem.rich.lns_min_component_size = std::min(8, std::max(2, static_cast<int>(item->number_or(2))));
        if (const Value* item = algorithm->find("multigroup_max_component_size")) problem.rich.lns_max_component_size = static_cast<int>(item->number_or(0));
        if (const Value* item = algorithm->find("multigroup_component_size")) problem.rich.lns_component_size = static_cast<int>(item->number_or(6));
        if (const Value* item = algorithm->find("multigroup_mip_solve_limit")) problem.rich.lns_mip_solve_limit = std::max(1, static_cast<int>(item->number_or(200)));
        if (const Value* item = algorithm->find("multigroup_passenger_limit")) problem.rich.lns_passenger_limit = std::max(6, static_cast<int>(item->number_or(14)));
        if (const Value* item = algorithm->find("multigroup_root_limit")) problem.rich.lns_root_limit = std::max(1, static_cast<int>(item->number_or(12)));
        if (const Value* item = algorithm->find("multigroup_free_seat_cap")) problem.rich.lns_free_seat_cap = std::max(0, static_cast<int>(item->number_or(5)));
        if (const Value* item = algorithm->find("multigroup_late_history_length")) problem.rich.lns_late_history_length = std::max(2, static_cast<int>(item->number_or(8)));
        if (const Value* item = algorithm->find("multigroup_stagnation_rounds")) problem.rich.lns_stagnation_rounds = std::max(1, static_cast<int>(item->number_or(3)));
        if (const Value* item = algorithm->find("multigroup_dynamic_pool_growth")) problem.rich.lns_dynamic_pool_growth = std::max(1, static_cast<int>(item->number_or(12)));
        if (const Value* item = algorithm->find("multigroup_mip_time_limit")) problem.rich.lns_mip_time_limit = std::max(.05, item->number_or(.75));
        if (const Value* item = algorithm->find("multigroup_allowed_drop")) problem.rich.lns_allowed_drop = std::max(0.0, item->number_or(20.0));
        if (const Value* item = algorithm->find("enable_protected_multigroup_pattern_mip")) problem.rich.protected_multigroup_enabled = item->bool_or(false);
        if (const Value* item = algorithm->find("protected_multigroup_max_passes")) problem.rich.protected_multigroup_max_passes = std::max(1, static_cast<int>(item->number_or(3)));
        if (const Value* item = algorithm->find("protected_multigroup_min_pass_gain")) problem.rich.protected_multigroup_min_pass_gain = std::max(0.0, item->number_or(1.0));
        if (const Value* item = algorithm->find("protected_multigroup_root_limit")) problem.rich.protected_multigroup_root_limit = std::max(1, static_cast<int>(item->number_or(8)));
        if (const Value* item = algorithm->find("protected_multigroup_max_groups")) problem.rich.protected_multigroup_max_groups = std::max(2, static_cast<int>(item->number_or(4)));
        if (const Value* item = algorithm->find("protected_multigroup_component_limit")) problem.rich.protected_multigroup_component_limit = std::max(1, static_cast<int>(item->number_or(30)));
        if (const Value* item = algorithm->find("protected_multigroup_options_per_group")) problem.rich.protected_multigroup_options_per_group = std::max(2, static_cast<int>(item->number_or(20)));
        if (const Value* item = algorithm->find("protected_dynamic_relocation_enabled")) problem.rich.protected_dynamic_relocation_enabled = item->bool_or(true);
        if (const Value* item = algorithm->find("protected_dynamic_relocation_seconds")) problem.rich.protected_dynamic_relocation_seconds = std::max(0.01, item->number_or(0.08));
        if (const Value* item = algorithm->find("protected_dynamic_relocation_columns")) problem.rich.protected_dynamic_relocation_columns = std::max(1, static_cast<int>(item->number_or(6)));
        if (const Value* item = algorithm->find("elite_patterns_per_group")) problem.rich.elite_patterns_per_group = std::max(2, static_cast<int>(item->number_or(12)));
        if (const Value* item = algorithm->find("enable_structured_pattern_generation")) problem.rich.enable_structured_pattern_generation = item->bool_or(true);
        if (const Value* item = algorithm->find("structured_pattern_dfs_per_group")) problem.rich.structured_pattern_dfs_per_group = std::max(0.005, item->number_or(0.08));
        if (const Value* item = algorithm->find("structured_patterns_per_group")) problem.rich.structured_patterns_per_group = std::max(2, static_cast<int>(item->number_or(12)));
        if (const Value* item = algorithm->find("structured_pattern_min_group_size")) problem.rich.structured_pattern_min_group_size = std::max(1, static_cast<int>(item->number_or(5)));
        if (const Value* item = algorithm->find("structured_rigid_shift_rows")) problem.rich.structured_rigid_shift_rows = std::max(0, static_cast<int>(item->number_or(3)));
        if (const Value* item = algorithm->find("structured_research_min_group_size")) problem.rich.structured_research_min_group_size = std::max(1, static_cast<int>(item->number_or(2)));
        if (const Value* item = algorithm->find("structured_pattern_extra_rows")) problem.rich.structured_pattern_extra_rows = static_cast<int>(item->number_or(2));
        if (const Value* item = algorithm->find("structured_pattern_window_limit")) problem.rich.structured_pattern_window_limit = std::max(1, static_cast<int>(item->number_or(12)));
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
    load_seats(seatmap, problem);
    Problem old_problem;
    old_problem.seat_spacing = problem.seat_spacing;
    old_problem.aisle_gap = problem.aisle_gap;
    old_problem.row_spacing = problem.row_spacing;
    load_seats(old_seatmap, old_problem);
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
            auto symmetry_raw = raw;
            symmetry_raw.object.erase("hostnum");
            passenger.rich_symmetry_fingerprint = rich_raw_fingerprint(symmetry_raw);
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
                const auto* fixed_number = fixed->find("seatNum");
                passenger.new_seat_num_is_none = !fixed_number || fixed_number->is_null();
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
    const auto* priority_mip = stage_algorithm ? stage_algorithm->find("enable_priority_multigroup_pattern_mip") : nullptr;
    problem.rich.priority_multigroup_enabled = priority_mip && priority_mip->bool_or(false)
        && setting("business_time_limit_seconds", 5.0) >= setting("priority_multigroup_min_business_time_seconds", 10.0);
    problem.rich_conflict_diversity_time_active = problem.rich_stage_budgets.business_time_limit
        >= setting("three_tier_min_business_time_seconds", 10.0);
    const auto* three_tier = stage_algorithm ? stage_algorithm->find("enable_three_tier_patterns") : nullptr;
    problem.rich_three_tier_active = (!three_tier || three_tier->bool_or(true))
        && setting("business_time_limit_seconds", 5.0) >= setting("three_tier_min_business_time_seconds", 0.0);
    problem.rich_structured_small_groups_active = problem.seats.size() < problem.old_seats.size()
        && setting("business_time_limit_seconds", 5.0) >= setting("structured_small_group_min_business_time_seconds", 10.0);
    const auto* global_blocks = stage_algorithm ? stage_algorithm->find("enable_global_full_resource_blocks") : nullptr;
    problem.rich_full_resource_global_blocks = (!global_blocks || global_blocks->bool_or(true))
        && setting("business_time_limit_seconds", 5.0) >= 10.0 && problem.seats.size() < problem.old_seats.size()
        && problem.rich_stage_budgets.seat_demand == static_cast<int>(problem.seats.size())
        && problem.rich_stage_budgets.seat_demand > static_cast<int>(problem.passengers.size());
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

std::vector<std::vector<RichPlacement>> build_rich_placement_options(
    const Problem& problem, int group_index, const FixedSeatContext& fixed
) {
    const auto& group = problem.groups[group_index];
    std::vector<std::vector<RichPlacement>> result;
    std::vector<int> isolated(problem.passengers.size(), -1);
    const auto seat_less = [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; };
    for (int p : group.passengers) {
        const auto& passenger = problem.passengers[p];
        const auto rule = ssr_rule(problem, passenger);
        std::vector<RichPlacement> options;
        for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) {
            const auto& seat = problem.seats[s];
            if (!passenger.fixed_seat.empty() && seat.id != passenger.fixed_seat) continue;
            if (passenger.fixed_seat.empty() && (fixed.owner_by_seat[s] >= 0 || fixed.deterministic_blocked[s])) continue;
            if (!passenger.cabin.empty() && seat.cabin != passenger.cabin) continue;
            if ((seat.exit_row && !rule.allow_exit_row) || (rule.requires_bassinet && !seat.bassinet)
                || (rule.requires_aisle && !seat.aisle)) continue;
            std::vector<std::vector<int>> modes(1);
            if (passenger.need_both_empty) {
                const auto& neighbors = problem.both_side_empty_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
                if ((problem.require_two_real_neighbors && neighbors.size() != 2) || neighbors.empty()) continue;
                if (std::any_of(neighbors.begin(), neighbors.end(), [&](int n) { return fixed.owner_by_seat[n] >= 0; })) continue;
                modes = {neighbors};
            } else if (passenger.need_single_empty) {
                modes.clear();
                for (int neighbor : seat.same_block_neighbors)
                    if (fixed.owner_by_seat[neighbor] < 0) modes.push_back({neighbor});
                if (modes.empty()) continue;
            }
            isolated[p] = s;
            const double cost = -evaluate_rich_group_score(problem, isolated, group_index).total();
            for (auto& blocked : modes) {
                RichPlacement option;
                option.passenger_index = static_cast<int>(result.size());
                option.passenger = p;
                option.seat = s;
                std::sort(blocked.begin(), blocked.end(), seat_less);
                option.blocked = blocked;
                option.resources = blocked;
                option.resources.push_back(s);
                std::sort(option.resources.begin(), option.resources.end(), seat_less);
                option.individual_cost = cost;
                if (!passenger.ssr.empty()) {
                    option.ssr_resources = {{{seat.row, -1}, passenger.ssr}, {{seat.row, seat.subrow}, passenger.ssr}};
                    if (passenger.same_row_no_other_ssr) option.ssr_flag_locations.push_back({seat.row, -1});
                    if (passenger.same_subrow_no_other_ssr) option.ssr_flag_locations.push_back({seat.row, seat.subrow});
                }
                option.is_infant = passenger.ssr == "BSCT";
                options.push_back(std::move(option));
            }
        }
        isolated[p] = -1;
        if (options.empty()) throw std::runtime_error("passenger has no legal Rich placement: "
            + std::to_string(group.id) + ":" + std::to_string(passenger.hostnum));
        result.push_back(std::move(options));
    }
    return result;
}

bool rich_placements_caregiver_ok(const Problem& problem, int group_index,
    const std::vector<RichPlacement>& placements
) {
    const auto& group = problem.groups[group_index];
    std::vector<int> seats(group.passengers.size(), -1);
    for (const auto& placement : placements) seats[placement.passenger_index] = placement.seat;
    for (size_t i = 0; i < group.passengers.size(); ++i) {
        const auto& passenger = problem.passengers[group.passengers[i]];
        const auto rule = ssr_rule(problem, passenger);
        if (!passenger.need_cared && !rule.requires_caregiver) continue;
        const auto& seat = problem.seats[seats[i]];
        const auto& neighbors = rule.caregiver_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        bool found = false;
        for (size_t j = 0; j < group.passengers.size(); ++j)
            if (j != i && is_adult_caregiver(problem.passengers[group.passengers[j]])
                && std::find(neighbors.begin(), neighbors.end(), seats[j]) != neighbors.end()) found = true;
        if (!found) return false;
    }
    return true;
}

RichExactPattern build_rich_exact_pattern(const Problem& problem, int group_index,
    const std::vector<RichPlacement>& placements, const std::vector<std::string>& active_ssr_types
) {
    RichExactPattern pattern;
    pattern.group_id = problem.groups[group_index].id;
    pattern.placements = placements;
    std::stable_sort(pattern.placements.begin(), pattern.placements.end(),
        [](const auto& a, const auto& b) { return a.passenger_index < b.passenger_index; });
    std::set<int> occupied, resources, infants;
    auto active_types = active_ssr_types;
    if (active_types.empty()) for (const auto& rule : problem.ssr_rules) active_types.push_back(rule.first);
    double individual_cost = 0.0;
    for (const auto& placement : placements) {
        pattern.assignments.emplace_back(placement.passenger, placement.seat);
        for (int seat : placement.blocked) pattern.blocked_by.emplace_back(seat, placement.passenger);
        occupied.insert(placement.seat);
        resources.insert(placement.resources.begin(), placement.resources.end());
        if (placement.is_infant) infants.insert(placement.seat);
        for (const auto& resource : placement.ssr_resources) ++pattern.ssr_all[resource];
        for (const auto& location : placement.ssr_flag_locations)
            for (const auto& ssr : active_types) pattern.ssr_flagged[{location, ssr}] = 1;
        individual_cost += placement.individual_cost;
    }
    std::sort(pattern.assignments.begin(), pattern.assignments.end(), [&](const auto& a, const auto& b) {
        return std::make_pair(problem.passengers[a.first].hostnum, problem.seats[a.second].id)
            < std::make_pair(problem.passengers[b.first].hostnum, problem.seats[b.second].id);
    });
    std::sort(pattern.blocked_by.begin(), pattern.blocked_by.end(), [&](const auto& a, const auto& b) {
        return std::make_pair(problem.seats[a.first].id, problem.passengers[a.second].hostnum)
            < std::make_pair(problem.seats[b.first].id, problem.passengers[b.second].hostnum);
    });
    const auto ordered_seats = [&](const std::set<int>& values) {
        std::vector<int> result(values.begin(), values.end());
        std::sort(result.begin(), result.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
        return result;
    };
    pattern.occupied_seats = ordered_seats(occupied);
    pattern.seat_resources = ordered_seats(resources);
    pattern.infant_seats = ordered_seats(infants);
    double x = 0.0, y = 0.0, dx = 0.0, dy = 0.0;
    for (const auto& assignment : pattern.assignments) {
        x += problem.seats[assignment.second].x;
        y += problem.seats[assignment.second].y;
    }
    if (!pattern.assignments.empty()) {
        x /= pattern.assignments.size(); y /= pattern.assignments.size();
        for (const auto& assignment : pattern.assignments) {
            dx = std::max(dx, std::abs(problem.seats[assignment.second].x - x));
            dy = std::max(dy, std::abs(problem.seats[assignment.second].y - y));
        }
    }
    pattern.master_cost = individual_cost - problem.weight_c
        * (problem.group_centroid_x_factor * dx + problem.group_centroid_y_factor * dy);
    // The master's baby variables count same-group occupants too. Cancel those
    // ordered pairs in the column. The evaluator excludes self-pairs.
    for (int u : pattern.infant_seats) for (int v : pattern.occupied_seats) {
        if (u == v) continue;
        const auto& infant = problem.seats[u];
        const auto& other = problem.seats[v];
        if (infant.cabin != other.cabin) continue;
        double factor = 0.0;
        if (infant.row == other.row && infant.subrow == other.subrow) factor = 1.0;
        else if (std::abs(infant.row - other.row) == 1) factor = problem.baby_front_back_factor;
        pattern.master_cost += problem.weight_b * factor / (1.0 + std::abs(infant.x - other.x));
    }
    return pattern;
}

std::vector<RichTieredPattern> generate_rich_rigid_relaxed_patterns(
    const Problem& problem, int group_index, const std::vector<int>& current_targets,
    const std::vector<std::vector<RichPlacement>>& options, const std::vector<std::string>& active_ssr_types
) {
    std::vector<RichTieredPattern> result;
    if (!problem.rich_three_tier_active || std::find(current_targets.begin(), current_targets.end(), -1) != current_targets.end()) return result;
    std::map<std::pair<int, std::string>, int> seat_at;
    std::map<int, std::vector<int>> rows;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) {
        const auto& seat = problem.seats[s];
        seat_at[{seat.row, seat.column}] = s;
        rows[seat.row].push_back(s);
    }
    for (auto& row : rows) std::sort(row.second.begin(), row.second.end(), [&](int a, int b) {
        return problem.seats[a].index_in_row < problem.seats[b].index_in_row;
    });
    using Signature = std::vector<std::pair<int, std::vector<int>>>;
    std::map<Signature, size_t> positions;
    const auto add_targets = [&](const std::vector<int>& targets, const char* source) {
        std::vector<RichPlacement> selected;
        std::vector<bool> used(problem.seats.size(), false);
        const auto choose = [&](auto&& self, size_t p) -> bool {
            if (p == options.size()) return rich_placements_caregiver_ok(problem, group_index, selected);
            for (const auto& option : options[p]) {
                if (option.seat != targets[p]) continue;
                if (std::any_of(option.resources.begin(), option.resources.end(), [&](int s) { return used[s]; })) continue;
                selected.push_back(option);
                for (int s : option.resources) used[s] = true;
                if (self(self, p + 1)) return true;
                selected.pop_back();
                for (int s : option.resources) used[s] = false;
            }
            return false;
        };
        if (!choose(choose, 0)) return;
        auto pattern = build_rich_exact_pattern(problem, group_index, selected, active_ssr_types);
        Signature signature;
        for (const auto& placement : pattern.placements) signature.emplace_back(placement.seat, placement.blocked);
        const auto old = positions.find(signature);
        if (old == positions.end()) {
            positions.emplace(std::move(signature), result.size());
            result.push_back({std::move(pattern), source});
        } else {
            // Python dict assignment replaces value/source but keeps insertion order.
            result[old->second] = {std::move(pattern), source};
        }
    };
    for (int delta = -problem.rich.structured_rigid_shift_rows; delta <= problem.rich.structured_rigid_shift_rows; ++delta) {
        for (bool mirrored : {false, true}) {
            std::vector<int> targets;
            for (int s : current_targets) {
                const auto& seat = problem.seats[s];
                const int row = seat.row + delta;
                std::string column = seat.column;
                const auto row_it = rows.find(row);
                if (mirrored && row_it != rows.end() && seat.index_in_row < static_cast<int>(row_it->second.size()))
                    column = problem.seats[row_it->second[row_it->second.size() - 1 - seat.index_in_row]].column;
                const auto target = seat_at.find({row, column});
                if (target == seat_at.end()) { targets.clear(); break; }
                targets.push_back(target->second);
            }
            if (!targets.empty()) add_targets(targets, "rigid");
        }
    }
    std::map<std::string, std::pair<int, int>> subrows;
    for (int s : current_targets) {
        const auto& seat = problem.seats[s];
        subrows["(" + std::to_string(seat.row) + ", " + std::to_string(seat.subrow) + ")"] = {seat.row, seat.subrow};
    }
    for (const auto& subrow : subrows) for (int delta : {-1, 1}) {
        auto targets = current_targets;
        bool valid = true;
        for (size_t p = 0; p < current_targets.size(); ++p) {
            const auto& seat = problem.seats[current_targets[p]];
            if (std::make_pair(seat.row, seat.subrow) != subrow.second) continue;
            const auto target = seat_at.find({seat.row + delta, seat.column});
            if (target == seat_at.end()) { valid = false; break; }
            targets[p] = target->second;
        }
        if (valid) add_targets(targets, "relaxed");
    }
    return result;
}

RichStructuredWindows build_rich_structured_windows(const Problem& problem, int group_index,
    const std::vector<std::vector<RichPlacement>>& options, const RichGroupRepairMetric& current_metric
) {
    RichStructuredWindows result;
    const auto& group = problem.groups[group_index];
    std::map<int, std::set<int>> reachable;
    for (const auto& row : options) for (const auto& option : row) reachable[problem.seats[option.seat].row].insert(option.seat);
    std::vector<int> rows;
    int capacity = 1, protected_demand = 0;
    for (const auto& row : reachable) { rows.push_back(row.first); capacity = std::max(capacity, static_cast<int>(row.second.size())); }
    std::set<int> fixed_rows;
    std::map<std::string, int> required_classes;
    std::vector<std::pair<std::string, double>> desired_values;
    double old_sum = 0.0;
    int old_count = 0;
    const auto value = [&](const Seat& seat) {
        return std::isnan(seat.explicit_value)
            ? (seat.cabin == "Business" ? problem.business_seat_value : 0.0)
                + (seat.extra_legroom ? problem.extra_legroom_value : 0.0) + (seat.bassinet ? problem.bassinet_value : 0.0)
            : seat.explicit_value;
    };
    for (int p : group.passengers) {
        const auto& passenger = problem.passengers[p];
        protected_demand += passenger.need_both_empty ? 2 : passenger.need_single_empty ? 1 : 0;
        const auto fixed = problem.seat_index.find(passenger.fixed_seat);
        if (fixed != problem.seat_index.end()) fixed_rows.insert(problem.seats[fixed->second].row);
        if (!passenger.cabin.empty()) ++required_classes[passenger.cabin];
        const auto old = problem.old_seat_index.find(passenger.old_seat);
        if (old != problem.old_seat_index.end()) {
            const auto& seat = problem.old_seats[old->second];
            old_sum += seat.row; ++old_count;
            desired_values.emplace_back(passenger.cabin, std::isnan(passenger.old_seat_value) ? value(seat) : passenger.old_seat_value);
        }
    }
    result.minimum_width = std::max(1, (static_cast<int>(group.passengers.size()) + protected_demand + capacity - 1) / capacity);
    result.old_center = rows.empty() ? 0.0 : old_count ? old_sum / old_count : rows.front();
    const int max_width = std::min(static_cast<int>(rows.size()), result.minimum_width + problem.rich.structured_pattern_extra_rows);
    for (int width = result.minimum_width; width <= max_width; ++width)
        for (int start = 0; start + width <= static_cast<int>(rows.size()); ++start) {
            std::vector<int> window(rows.begin() + start, rows.begin() + start + width);
            if (std::includes(window.begin(), window.end(), fixed_rows.begin(), fixed_rows.end())) result.all_row_windows.push_back(std::move(window));
        }
    const auto key = [&](const std::vector<int>& window) {
        std::map<std::string, int> available;
        std::map<std::string, std::vector<double>> values;
        for (const auto& seat : problem.seats) if (std::binary_search(window.begin(), window.end(), seat.row)) {
            ++available[seat.cabin]; values[seat.cabin].push_back(value(seat));
        }
        int shortage = 0;
        for (const auto& demand : required_classes) shortage += std::max(0, demand.second - available[demand.first]);
        double mismatch = 0.0, center = 0.0;
        if (current_metric.value_mismatch_score < 0.0) for (const auto& desired : desired_values) {
            double best = std::numeric_limits<double>::infinity();
            for (double candidate : values[desired.first]) best = std::min(best, std::abs(candidate - desired.second));
            mismatch += best;
        }
        for (int row : window) center += row;
        center /= window.size();
        const int span = window.back() - window.front();
        return std::make_tuple(shortage, mismatch, span <= std::max(0, current_metric.row_span - 2) ? 0 : 1,
            span, std::abs(center - result.old_center), window.size(), window);
    };
    std::stable_sort(result.all_row_windows.begin(), result.all_row_windows.end(), [&](const auto& a, const auto& b) { return key(a) < key(b); });
    result.row_windows = result.all_row_windows;
    if (result.row_windows.size() > static_cast<size_t>(problem.rich.structured_pattern_window_limit))
        result.row_windows.resize(problem.rich.structured_pattern_window_limit);
    return result;
}

void generate_rich_value_block_patterns(const Problem& problem, int group_index,
    const std::vector<std::vector<RichPlacement>>& options, const RichGroupRepairMetric& current_metric,
    const RichStructuredWindows& windows, const std::vector<std::string>& active_ssr_types,
    std::chrono::steady_clock::time_point deadline, std::vector<RichTieredPattern>& patterns
) {
    const auto& group = problem.groups[group_index];
    const bool global = problem.rich_full_resource_global_blocks;
    if (current_metric.value_mismatch_score >= 0.0 && !global) return;
    for (int p : group.passengers) {
        const auto& item = problem.passengers[p];
        if (!item.ssr.empty() || item.need_cared || item.has_new_seat || item.need_single_empty || item.need_both_empty) return;
    }
    const auto& block_windows = global ? windows.all_row_windows : windows.row_windows;
    const size_t window_count = global ? block_windows.size() : std::min(size_t(4), block_windows.size());
    const auto seat_less = [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; };
    std::set<std::vector<int>> seen;
    const size_t size = group.passengers.size();
    const auto popcount = [](size_t mask) { size_t count = 0; while (mask) { mask &= mask - 1; ++count; } return count; };
    for (size_t w = 0; w < window_count; ++w) {
        if (std::chrono::steady_clock::now() >= deadline) break;
        const auto& window = block_windows[w];
        std::set<int> pool_set;
        for (const auto& row : options) for (const auto& option : row)
            if (std::binary_search(window.begin(), window.end(), problem.seats[option.seat].row)) pool_set.insert(option.seat);
        std::vector<int> pool(pool_set.begin(), pool_set.end());
        std::sort(pool.begin(), pool.end(), seat_less);
        if (pool.size() < size || pool.empty()) continue;
        const auto anchors = global ? std::vector<int>{pool[0], pool[pool.size()/4], pool[pool.size()/2], pool[3*pool.size()/4], pool.back()} : pool;
        std::set<int> used_anchors;
        for (int anchor : anchors) {
            if (!used_anchors.insert(anchor).second) continue;
            if (std::chrono::steady_clock::now() >= deadline) break;
            auto block = pool;
            const auto key = [&](int s) {
                const auto& seat = problem.seats[s]; const auto& origin = problem.seats[anchor];
                return std::make_tuple(std::abs(seat.x-origin.x)+std::abs(seat.y-origin.y),
                    std::abs(seat.y-origin.y), seat.x, seat.id);
            };
            std::stable_sort(block.begin(), block.end(), [&](int a, int b) { return key(a) < key(b); });
            block.resize(size);
            std::sort(block.begin(), block.end(), seat_less);
            if (!seen.insert(block).second) continue;
            // The frozen algorithm uses exact subset matching without an internal
            // clock check or a group-size cap; retain those search semantics.
            if (size >= std::numeric_limits<size_t>::digits) throw std::runtime_error("value block exceeds native subset index width");
            const size_t count = size_t(1) << size;
            std::vector<double> dp(count, -std::numeric_limits<double>::infinity());
            std::vector<int> parent(count, -1);
            std::vector<std::vector<const RichPlacement*>> choices(size, std::vector<const RichPlacement*>(size, nullptr));
            for (size_t p = 0; p < size; ++p) for (size_t s = 0; s < size; ++s)
                for (const auto& option : options[p]) if (option.seat == block[s]) { choices[p][s] = &option; break; }
            dp[0] = 0.0;
            for (size_t mask = 0; mask < count; ++mask) {
                const size_t p = popcount(mask);
                if (p >= size || dp[mask] == -std::numeric_limits<double>::infinity()) continue;
                for (size_t s = 0; s < size; ++s) {
                    const size_t bit = size_t(1) << s;
                    if ((mask & bit) || !choices[p][s]) continue;
                    const double score = dp[mask] - choices[p][s]->individual_cost;
                    if (score > dp[mask | bit]) { dp[mask | bit] = score; parent[mask | bit] = static_cast<int>(s); }
                }
            }
            if (parent.back() < 0) continue;
            std::vector<RichPlacement> selected(size);
            for (size_t mask = count-1; mask;) {
                const int s = parent[mask]; const size_t previous = mask ^ (size_t(1) << s);
                const size_t p = popcount(previous); selected[p] = *choices[p][s]; mask = previous;
            }
            auto pattern = build_rich_exact_pattern(problem, group_index, selected, active_ssr_types);
            auto existing = std::find_if(patterns.begin(), patterns.end(), [&](const auto& old) {
                return old.pattern.assignments == pattern.assignments && old.pattern.blocked_by == pattern.blocked_by;
            });
            RichTieredPattern item{std::move(pattern), global ? "global_value_block" : "value_block"};
            if (existing == patterns.end()) patterns.push_back(std::move(item)); else *existing = std::move(item);
        }
    }
}

RichPricingCache build_rich_pricing_cache(const Problem& problem, int group_index, const FixedSeatContext& fixed) {
    RichPricingCache cache;
    cache.all_options = build_rich_placement_options(problem, group_index, fixed);
    std::set<int> reachable;
    for (const auto& options : cache.all_options) for (const auto& option : options) reachable.insert(option.seat);
    double min_y = std::numeric_limits<double>::infinity(), min_x = min_y;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) if (reachable.count(s)) {
        cache.seat_ids.push_back(s);
        min_y = std::min(min_y, problem.seats[s].y); min_x = std::min(min_x, problem.seats[s].x);
    }
    std::set<std::pair<std::string, std::string>> edges;
    for (int s : cache.seat_ids) {
        cache.row_coordinate[s] = problem.seats[s].y - min_y;
        cache.x_coordinate[s] = problem.seats[s].x - min_x;
        cache.row_big_m = std::max(cache.row_big_m, cache.row_coordinate[s]);
        cache.x_big_m = std::max(cache.x_big_m, cache.x_coordinate[s]);
        for (int neighbor : problem.seats[s].row_neighbors) if (neighbor != s) {
            auto a = problem.seats[s].id, b = problem.seats[neighbor].id;
            if (b < a) std::swap(a, b);
            edges.emplace(a, b);
        }
    }
    for (const auto& edge : edges) cache.adjacency_edges.emplace_back(problem.seat_index.at(edge.first), problem.seat_index.at(edge.second));
    // SeatTopology retains first-seen row order, then sorts seats within each row.
    std::vector<int> row_order;
    std::map<int, std::vector<int>> row_seats;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s) {
        const int row = problem.seats[s].row;
        if (!row_seats.count(row)) row_order.push_back(row);
        row_seats[row].push_back(s);
    }
    for (int row : row_order) {
        auto& seats = row_seats[row];
        std::sort(seats.begin(), seats.end(), [&](int a, int b) { return problem.seats[a].index_in_row < problem.seats[b].index_in_row; });
        for (size_t i = 1; i + 1 < seats.size(); ++i) {
            RichHoleSpec hole; hole.middle = seats[i];
            for (size_t j = 0; j < i; ++j) if (reachable.count(seats[j])) hole.left.push_back(seats[j]);
            for (size_t j = i + 1; j < seats.size(); ++j) if (reachable.count(seats[j])) hole.right.push_back(seats[j]);
            if (!hole.left.empty() && !hole.right.empty()) cache.hole_specs.push_back(std::move(hole));
        }
    }
    return cache;
}

RichPricingCache filter_rich_pricing_window(const Problem& problem, const RichPricingCache& cache,
    const std::vector<int>& rows
) {
    RichPricingCache filtered;
    filtered.row_big_m = cache.row_big_m; filtered.x_big_m = cache.x_big_m;
    std::set<int> seats;
    for (const auto& options : cache.all_options) {
        filtered.all_options.emplace_back();
        for (const auto& option : options) if (std::find(rows.begin(), rows.end(), problem.seats[option.seat].row) != rows.end()) {
            filtered.all_options.back().push_back(option); seats.insert(option.seat);
        }
    }
    filtered.seat_ids.assign(seats.begin(), seats.end());
    std::sort(filtered.seat_ids.begin(), filtered.seat_ids.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
    for (int s : filtered.seat_ids) {
        filtered.row_coordinate[s] = cache.row_coordinate.at(s); filtered.x_coordinate[s] = cache.x_coordinate.at(s);
    }
    for (const auto& edge : cache.adjacency_edges) if (seats.count(edge.first) && seats.count(edge.second)) filtered.adjacency_edges.push_back(edge);
    for (const auto& hole : cache.hole_specs) {
        if (!seats.count(hole.middle)) continue;
        RichHoleSpec item; item.middle = hole.middle;
        for (int s : hole.left) if (seats.count(s)) item.left.push_back(s);
        for (int s : hole.right) if (seats.count(s)) item.right.push_back(s);
        if (!item.left.empty() && !item.right.empty()) filtered.hole_specs.push_back(std::move(item));
    }
    return filtered;
}

RichPricingCache filter_rich_pricing_resources(const Problem& problem, const RichPricingCache& cache,
    const std::set<int>& outside_resources
) {
    // Python shallow-copies the base cache; history/workspace keep their identity.
    auto filtered = cache;
    filtered.all_options.clear();
    filtered.row_coordinate.clear(); filtered.x_coordinate.clear();
    filtered.adjacency_edges.clear(); filtered.hole_specs.clear();
    std::set<int> seats;
    for (const auto& options : cache.all_options) {
        filtered.all_options.emplace_back();
        for (const auto& option : options) {
            if (std::any_of(option.resources.begin(), option.resources.end(),
                [&](int s) { return outside_resources.count(s) != 0; })) continue;
            filtered.all_options.back().push_back(option);
            seats.insert(option.seat);
        }
    }
    filtered.seat_ids.assign(seats.begin(), seats.end());
    std::sort(filtered.seat_ids.begin(), filtered.seat_ids.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
    for (int s : filtered.seat_ids) {
        filtered.row_coordinate[s] = cache.row_coordinate.at(s); filtered.x_coordinate[s] = cache.x_coordinate.at(s);
    }
    for (const auto& edge : cache.adjacency_edges) if (seats.count(edge.first) && seats.count(edge.second)) filtered.adjacency_edges.push_back(edge);
    for (const auto& hole : cache.hole_specs) {
        if (!seats.count(hole.middle)) continue;
        RichHoleSpec item; item.middle = hole.middle;
        for (int s : hole.left) if (seats.count(s)) item.left.push_back(s);
        for (int s : hole.right) if (seats.count(s)) item.right.push_back(s);
        if (!item.left.empty() && !item.right.empty()) filtered.hole_specs.push_back(std::move(item));
    }
    return filtered;
}

size_t RichPricingGeometry::rectangle_index(int row_low, int row_high, int x_low, int x_high) const {
    return ((static_cast<size_t>(row_low) * row_values.size() + row_high) * x_values.size() + x_low) * x_values.size() + x_high;
}

RichPricingGeometry build_rich_pricing_geometry(const RichPricingCache& cache) {
    RichPricingGeometry geometry;
    for (const auto& entry : cache.row_coordinate) geometry.row_values.push_back(entry.second);
    for (const auto& entry : cache.x_coordinate) geometry.x_values.push_back(entry.second);
    for (auto* values : {&geometry.row_values, &geometry.x_values}) {
        std::sort(values->begin(), values->end());
        values->erase(std::unique(values->begin(), values->end()), values->end());
    }
    const int nr = static_cast<int>(geometry.row_values.size()), nx = static_cast<int>(geometry.x_values.size());
    geometry.seat_prefix.assign(static_cast<size_t>(nr) * nx, 0);
    for (int seat : cache.seat_ids) {
        const int row = static_cast<int>(std::lower_bound(geometry.row_values.begin(), geometry.row_values.end(), cache.row_coordinate.at(seat)) - geometry.row_values.begin());
        const int x = static_cast<int>(std::lower_bound(geometry.x_values.begin(), geometry.x_values.end(), cache.x_coordinate.at(seat)) - geometry.x_values.begin());
        geometry.seat_row_index[seat] = row; geometry.seat_x_index[seat] = x;
        ++geometry.seat_prefix[static_cast<size_t>(row) * nx + x];
    }
    for (int row = 1; row < nr; ++row) for (int x = 0; x < nx; ++x)
        geometry.seat_prefix[static_cast<size_t>(row) * nx + x] += geometry.seat_prefix[static_cast<size_t>(row - 1) * nx + x];
    for (int row = 0; row < nr; ++row) for (int x = 1; x < nx; ++x)
        geometry.seat_prefix[static_cast<size_t>(row) * nx + x] += geometry.seat_prefix[static_cast<size_t>(row) * nx + x - 1];
    return geometry;
}

RichPricingBounds build_rich_pricing_bounds(const RichPricingCache& cache,
    const RichPricingGeometry& geometry, const std::vector<int>& order,
    const std::vector<std::vector<double>>& base_cost, double row_span_cost, double column_span_cost
) {
    const size_t nr = geometry.row_values.size(), nx = geometry.x_values.size();
    if (!nr || !nx) throw std::runtime_error("pricing bounds require a nonempty seat domain");
    const double infinity = std::numeric_limits<double>::infinity();
    const size_t count = nr * nr * nx * nx;
    const auto index = [&](size_t a, size_t b, size_t c, size_t d) { return ((a * nr + b) * nx + c) * nx + d; };
    const auto capacity = [&](size_t a, size_t b, size_t c, size_t d) {
        int value = geometry.seat_prefix[b * nx + d];
        if (a) value -= geometry.seat_prefix[(a - 1) * nx + d];
        if (c) value -= geometry.seat_prefix[b * nx + c - 1];
        if (a && c) value += geometry.seat_prefix[(a - 1) * nx + c - 1];
        return value;
    };
    // Same four cumulative minima as NumPy: low axes forward, high axes backward.
    const auto enclosing_minimum = [&](std::vector<double>& values) {
        const size_t widths[] = {nr, nr, nx, nx};
        const size_t strides[] = {nr * nx * nx, nx * nx, nx, 1};
        for (int axis = 0; axis < 4; ++axis) {
            const size_t width = widths[axis], stride = strides[axis], block = width * stride;
            for (size_t base = 0; base < count; base += block) for (size_t offset = 0; offset < stride; ++offset)
                for (size_t step = 1; step < width; ++step) {
                    const size_t position = axis % 2 ? width - 1 - step : step;
                    const size_t current = base + position * stride + offset;
                    const size_t previous = axis % 2 ? current + stride : current - stride;
                    values[current] = std::min(values[current], values[previous]);
                }
        }
    };
    std::vector<double> exact(count, infinity);
    for (size_t a = 0; a < nr; ++a) for (size_t b = a; b < nr; ++b) {
        const double row_cost = row_span_cost * (geometry.row_values[b] - geometry.row_values[a]);
        for (size_t c = 0; c < nx; ++c) for (size_t d = c; d < nx; ++d)
            if (capacity(a, b, c, d) >= static_cast<int>(cache.all_options.size()))
                exact[index(a, b, c, d)] = row_cost + column_span_cost * (geometry.x_values[d] - geometry.x_values[c]);
    }
    RichPricingBounds bounds;
    bounds.span = exact; enclosing_minimum(bounds.span);
    bounds.root_span = *std::min_element(bounds.span.begin(), bounds.span.end());
    std::vector<std::vector<double>> passenger_cost(cache.all_options.size());
    for (int p : order) {
        std::vector<double> grid(nr * nx, infinity);
        const auto& options = cache.all_options[p];
        for (size_t i = 0; i < options.size(); ++i) {
            const size_t cell = static_cast<size_t>(geometry.seat_row_index.at(options[i].seat)) * nx + geometry.seat_x_index.at(options[i].seat);
            grid[cell] = std::min(grid[cell], base_cost.at(p).at(i));
        }
        auto& minimum = passenger_cost[p]; minimum.assign(count, infinity);
        for (size_t a = 0; a < nr; ++a) for (size_t b = a; b < nr; ++b) {
            std::vector<double> columns(nx, infinity);
            for (size_t row = a; row <= b; ++row) for (size_t x = 0; x < nx; ++x)
                columns[x] = std::min(columns[x], grid[row * nx + x]);
            for (size_t c = 0; c < nx; ++c) {
                double running = infinity;
                for (size_t d = c; d < nx; ++d) {
                    running = std::min(running, columns[d]); minimum[index(a, b, c, d)] = running;
                }
            }
        }
    }
    for (size_t depth = 0; depth <= order.size(); ++depth) {
        auto combined = exact;
        // Preserve Python's addition order at every depth (no reverse suffix sums).
        for (size_t next = depth; next < order.size(); ++next)
            for (size_t i = 0; i < count; ++i) combined[i] += passenger_cost[order[next]][i];
        enclosing_minimum(combined); bounds.suffix.push_back(std::move(combined));
    }
    return bounds;
}

RichPricingWorkspace build_rich_pricing_workspace(const Problem& problem, int group_index,
    const RichPricingCache& cache
) {
    RichPricingWorkspace workspace;
    workspace.geometry = build_rich_pricing_geometry(cache);
    // Python sorts flag locations by their tuple string, not by numeric row.
    std::map<std::string, RichSsrLocation> sorted_flags;
    for (const auto& options : cache.all_options) for (const auto& option : options)
        for (const auto& location : option.ssr_flag_locations) {
            const auto key = location.subrow < 0 ? "('row', " + std::to_string(location.row) + ")"
                : "('subrow', (" + std::to_string(location.row) + ", " + std::to_string(location.subrow) + "))";
            sorted_flags[key] = location;
        }
    std::map<RichSsrLocation, int> flag_index;
    for (const auto& entry : sorted_flags) {
        flag_index[entry.second] = static_cast<int>(workspace.flag_locations.size());
        workspace.flag_locations.push_back(entry.second);
    }
    const size_t seat_words = (problem.seats.size() + 63) / 64;
    const size_t flag_words = (workspace.flag_locations.size() + 63) / 64;
    for (size_t p = 0; p < cache.all_options.size(); ++p) {
        workspace.resource_masks.emplace_back(); workspace.flag_masks.emplace_back();
        workspace.option_flag_indexes.emplace_back();
        for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
            const auto& option = cache.all_options[p][i];
            RichPricingMask resources(seat_words, 0), flags(flag_words, 0);
            std::vector<int> indexes;
            for (int seat : option.resources) resources[seat / 64] |= std::uint64_t{1} << (seat % 64);
            for (const auto& location : option.ssr_flag_locations) {
                const int bit = flag_index.at(location);
                indexes.push_back(bit); flags[bit / 64] |= std::uint64_t{1} << (bit % 64);
            }
            workspace.resource_masks.back().push_back(std::move(resources));
            workspace.flag_masks.back().push_back(std::move(flags));
            workspace.option_flag_indexes.back().push_back(std::move(indexes));
            workspace.option_by_signature[{option.passenger_index, option.seat, option.blocked}] =
                {static_cast<int>(p), static_cast<int>(i)};
        }
    }
    const auto& group = problem.groups[group_index];
    for (size_t p = 0; p < group.passengers.size(); ++p) {
        const auto& passenger = problem.passengers[group.passengers[p]];
        const auto rule = ssr_rule(problem, passenger);
        if (!passenger.need_cared && !rule.requires_caregiver) continue;
        RichPricingCaregiver spec;
        spec.passenger_index = static_cast<int>(p);
        spec.allow_cross_aisle = passenger.need_cared && passenger.ssr.empty() ? false : rule.caregiver_allow_cross_aisle;
        for (size_t other = 0; other < group.passengers.size(); ++other)
            if (other != p && is_adult_caregiver(problem.passengers[group.passengers[other]]))
                spec.caregivers.push_back(static_cast<int>(other));
        workspace.caregiver_specs.push_back(std::move(spec));
    }
    return workspace;
}

bool rich_pricing_caregiver_possible(const Problem& problem, const RichPricingCache& cache,
    const RichPricingWorkspace& workspace, const std::vector<int>& selected, const RichPricingMask& used
) {
    for (const auto& spec : workspace.caregiver_specs) {
        if (selected[spec.passenger_index] < 0) continue;
        const auto& seat = problem.seats[cache.all_options[spec.passenger_index][selected[spec.passenger_index]].seat];
        const auto& neighbors = spec.allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        bool possible = false;
        for (int p : spec.caregivers) {
            if (selected[p] >= 0) {
                const int chosen = cache.all_options[p][selected[p]].seat;
                if (std::find(neighbors.begin(), neighbors.end(), chosen) != neighbors.end()) { possible = true; break; }
                continue;
            }
            for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
                if (std::find(neighbors.begin(), neighbors.end(), cache.all_options[p][i].seat) == neighbors.end()) continue;
                bool overlap = false;
                for (size_t word = 0; word < used.size(); ++word)
                    if (workspace.resource_masks[p][i][word] & used[word]) { overlap = true; break; }
                if (!overlap) { possible = true; break; }
            }
            if (possible) break;
        }
        if (!possible) return false;
    }
    return true;
}

std::vector<RichPricingMask> rich_pricing_caregiver_state(const Problem& problem, const RichPricingCache& cache,
    const RichPricingWorkspace& workspace, const std::vector<int>& selected
) {
    std::vector<RichPricingMask> state;
    for (const auto& spec : workspace.caregiver_specs) {
        if (selected[spec.passenger_index] < 0) { state.emplace_back(); continue; }
        const auto& seat = problem.seats[cache.all_options[spec.passenger_index][selected[spec.passenger_index]].seat];
        const auto& neighbors = spec.allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
        RichPricingMask mask((problem.seats.size() + 63) / 64, 0);
        for (int neighbor : neighbors) mask[neighbor / 64] |= std::uint64_t{1} << (neighbor % 64);
        for (int p : spec.caregivers) if (selected[p] >= 0) {
            const int chosen = cache.all_options[p][selected[p]].seat;
            if (mask[chosen / 64] & (std::uint64_t{1} << (chosen % 64))) {
                std::fill(mask.begin(), mask.end(), 0); break;
            }
        }
        state.push_back(std::move(mask));
    }
    return state;
}

RichPricingSymmetry build_rich_pricing_symmetry(const Problem& problem, int group_index,
    const RichPricingCache& domains, bool enabled
) {
    RichPricingSymmetry result;
    const auto& group = problem.groups[group_index];
    result.classes.resize(group.passengers.size()); result.ranks.resize(group.passengers.size());
    if (!enabled) return result;
    using Location = std::tuple<bool, int, int>;
    using Resource = std::tuple<bool, int, int, std::string>;
    using Physical = std::tuple<std::string, std::vector<std::string>, std::vector<std::string>, double,
        std::vector<Resource>, std::vector<Location>, bool>;
    using Descriptor = std::tuple<std::vector<Physical>, int, bool>;
    const auto physical = [&](const RichPlacement& option) {
        std::vector<std::string> blocked, resources;
        for (int seat : option.blocked) blocked.push_back(problem.seats[seat].id);
        for (int seat : option.resources) resources.push_back(problem.seats[seat].id);
        std::vector<Resource> ssr;
        for (const auto& r : option.ssr_resources) ssr.emplace_back(r.location.subrow >= 0, r.location.row, r.location.subrow, r.ssr);
        std::vector<Location> flags;
        for (const auto& l : option.ssr_flag_locations) flags.emplace_back(l.subrow >= 0, l.row, l.subrow);
        char decimal[512];
        const auto converted = std::to_chars(decimal, decimal + sizeof(decimal), option.individual_cost, std::chars_format::fixed, 12);
        if (converted.ec != std::errc{}) throw std::runtime_error("pricing symmetry cost rounding failed");
        const double rounded = std::stod(std::string(decimal, converted.ptr));
        return Physical{problem.seats[option.seat].id, blocked, resources, rounded, ssr, flags, option.is_infant};
    };
    std::map<std::string, std::vector<int>> raw_buckets;
    for (size_t p = 0; p < group.passengers.size(); ++p)
        raw_buckets[problem.passengers[group.passengers[p]].rich_symmetry_fingerprint].push_back(static_cast<int>(p));
    std::map<Descriptor, std::vector<int>> buckets;
    std::vector<std::vector<Physical>> keys(group.passengers.size());
    for (const auto& bucket : raw_buckets) if (bucket.second.size() >= 2) for (int p : bucket.second) {
        for (const auto& option : domains.all_options[p]) keys[p].push_back(physical(option));
        auto sorted = keys[p]; std::sort(sorted.begin(), sorted.end());
        const auto& passenger = problem.passengers[group.passengers[p]];
        const auto rule = ssr_rule(problem, passenger);
        const int care = !passenger.need_cared && !rule.requires_caregiver ? -1
            : passenger.need_cared && passenger.ssr.empty() ? 0 : static_cast<int>(rule.caregiver_allow_cross_aisle);
        buckets[{sorted, care, is_adult_caregiver(passenger)}].push_back(p);
    }
    for (auto& bucket : buckets) if (bucket.second.size() > 1) {
        auto& members = bucket.second; std::sort(members.begin(), members.end());
        ++result.class_count;
        auto ordered = keys[members.front()]; std::sort(ordered.begin(), ordered.end());
        ordered.erase(std::unique(ordered.begin(), ordered.end()), ordered.end());
        for (int p : members) {
            result.classes[p] = members;
            for (const auto& key : keys[p]) result.ranks[p].push_back(static_cast<int>(std::lower_bound(ordered.begin(), ordered.end(), key) - ordered.begin()));
        }
    }
    return result;
}

bool rich_pricing_symmetry_ok(const RichPricingSymmetry& symmetry, const std::vector<int>& selected,
    int passenger_index, int option_index
) {
    if (symmetry.classes[passenger_index].empty()) return true;
    const int rank = symmetry.ranks[passenger_index][option_index];
    for (int other : symmetry.classes[passenger_index]) if (selected[other] >= 0) {
        const int other_rank = symmetry.ranks[other][selected[other]];
        if ((other < passenger_index && other_rank > rank) || (other > passenger_index && other_rank < rank)) return false;
    }
    return true;
}

std::vector<RichBabyCost> build_rich_baby_costs(const Problem& problem) {
    std::vector<RichBabyCost> costs;
    if (std::none_of(problem.passengers.begin(), problem.passengers.end(), [](const auto& p) { return p.ssr == "BSCT"; })) return costs;
    for (int u = 0; u < static_cast<int>(problem.seats.size()); ++u) for (int v = 0; v < static_cast<int>(problem.seats.size()); ++v) {
        const auto& infant = problem.seats[u]; const auto& other = problem.seats[v];
        if (u == v || infant.cabin != other.cabin) continue;
        double score = 0.0;
        const double distance = 1.0 + std::abs(infant.x - other.x);
        if (infant.row == other.row && infant.subrow == other.subrow) score = problem.weight_b / distance;
        else if (std::abs(infant.row - other.row) == 1) score = problem.weight_b * problem.baby_front_back_factor / distance;
        if (score != 0.0) costs.push_back({u, v, -score});
    }
    return costs;
}

double rich_same_group_baby_upper_bound(const RichPricingCache& cache,
    const std::vector<RichBabyCost>& baby_cost, int passenger_count
) {
    if (baby_cost.empty() || passenger_count <= 0) return 0.0;
    int infants = 0;
    std::set<int> infant_seats, occupied(cache.seat_ids.begin(), cache.seat_ids.end());
    for (const auto& options : cache.all_options) {
        bool infant = false;
        for (const auto& option : options) if (option.is_infant) { infant = true; infant_seats.insert(option.seat); }
        infants += infant;
    }
    std::vector<double> per_infant;
    for (int u : infant_seats) {
        std::vector<double> costs;
        for (const auto& pair : baby_cost) if (pair.infant == u && occupied.count(pair.occupant)) costs.push_back(std::max(0.0, pair.cost));
        std::sort(costs.begin(), costs.end(), std::greater<double>());
        double total = 0.0;
        for (size_t i = 0; i < costs.size() && i < static_cast<size_t>(passenger_count); ++i) total += costs[i];
        per_infant.push_back(total);
    }
    std::sort(per_infant.begin(), per_infant.end(), std::greater<double>());
    double total = 0.0;
    for (size_t i = 0; i < per_infant.size() && i < static_cast<size_t>(infants); ++i) total += per_infant[i];
    return total;
}

RichPricingCosts build_rich_pricing_costs(int group_id, const RichPricingCache& cache,
    const RichPricingWorkspace& workspace, const RichPricingDuals& duals,
    const std::vector<RichBabyCost>& baby_cost, bool phase_one
) {
    const auto get = [](const auto& values, const auto& key) { const auto found = values.find(key); return found == values.end() ? 0.0 : found->second; };
    std::map<int, double> infant_dual, occupant_dual;
    for (const auto& pair : baby_cost) {
        const auto key = std::make_pair(pair.infant, pair.occupant);
        infant_dual[pair.infant] += get(duals.baby_lower, key) + get(duals.baby_infant_upper, key);
        occupant_dual[pair.occupant] += get(duals.baby_lower, key) + get(duals.baby_occupant_upper, key);
    }
    std::map<std::pair<int, RichSsrLocation>, double> flag_duals;
    for (const auto& entry : duals.ssr_flag) flag_duals[{std::get<0>(entry), std::get<1>(entry).location}] += std::get<2>(entry);
    RichPricingCosts costs;
    for (const auto& options : cache.all_options) {
        costs.base.emplace_back();
        for (const auto& option : options) {
            double value = phase_one ? 0.0 : option.individual_cost;
            for (int seat : option.resources) value -= get(duals.seat, seat);
            for (const auto& resource : option.ssr_resources) value -= get(duals.ssr_all, resource);
            value -= get(occupant_dual, option.seat);
            if (option.is_infant) value -= get(infant_dual, option.seat);
            costs.base.back().push_back(value);
        }
    }
    for (const auto& location : workspace.flag_locations) costs.flags.push_back(-get(flag_duals, std::make_pair(group_id, location)));
    if (!phase_one && !baby_cost.empty()) costs.baby_relaxation = -rich_same_group_baby_upper_bound(cache, baby_cost, static_cast<int>(cache.all_options.size()));
    return costs;
}

double rich_pattern_reduced_cost(const RichExactPattern& pattern, const RichPricingDuals& duals,
    const std::vector<RichBabyCost>& baby_cost, bool phase_one
) {
    const auto get = [](const auto& values, const auto& key) { const auto found = values.find(key); return found == values.end() ? 0.0 : found->second; };
    double value = (phase_one ? 0.0 : pattern.master_cost) - get(duals.group, pattern.group_id);
    double seats = 0.0;
    for (int seat : pattern.seat_resources) seats += get(duals.seat, seat);
    value -= seats;
    const auto ordered = [](const auto& coefficients) {
        std::map<std::string, std::pair<RichSsrResource, int>> result;
        for (const auto& entry : coefficients) {
            const auto& r = entry.first; const auto& l = r.location;
            const auto location = l.subrow < 0 ? "'row', " + std::to_string(l.row)
                : "'subrow', (" + std::to_string(l.row) + ", " + std::to_string(l.subrow) + ")";
            result["(" + location + ", '" + r.ssr + "')"] = entry;
        }
        return result;
    };
    std::map<std::pair<int, RichSsrResource>, double> flagged;
    for (const auto& entry : duals.ssr_flag) flagged[{std::get<0>(entry), std::get<1>(entry)}] = std::get<2>(entry);
    for (const auto& item : ordered(pattern.ssr_flagged)) value -= get(flagged, std::make_pair(pattern.group_id, item.second.first)) * item.second.second;
    for (const auto& item : ordered(pattern.ssr_all)) value -= get(duals.ssr_all, item.second.first) * item.second.second;
    for (const auto& pair : baby_cost) {
        const auto key = std::make_pair(pair.infant, pair.occupant);
        const int infant = std::find(pattern.infant_seats.begin(), pattern.infant_seats.end(), pair.infant) != pattern.infant_seats.end();
        const int occupied = std::find(pattern.occupied_seats.begin(), pattern.occupied_seats.end(), pair.occupant) != pattern.occupied_seats.end();
        value -= get(duals.baby_lower, key) * (infant + occupied);
        value -= get(duals.baby_infant_upper, key) * infant;
        value -= get(duals.baby_occupant_upper, key) * occupied;
    }
    return value;
}

RichPricingResult price_rich_group_dfs(const Problem& problem, int group_index,
    const native_json::Value& cfg, const RichPricingDuals& duals,
    const std::vector<RichBabyCost>& baby_cost, const std::set<RichPlacementSignature>& forced,
    const std::set<RichPlacementSignature>& forbidden, std::chrono::steady_clock::time_point deadline,
    RichPricingCache& cache, bool exact, bool phase_one, bool stop_on_negative,
    const std::vector<std::string>& active_ssr_types
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto number = [&](const char* key, double fallback) { return cfg.find(key) ? optional_number(cfg, key) : fallback; };
    const auto boolean = [&](const char* key, bool fallback) { return cfg.find(key) ? optional_bool(cfg, key) : fallback; };
    const auto after = [](Clock::time_point origin, double seconds) { return origin + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)); };
    const auto seconds = [](Clock::time_point from) { return std::chrono::duration<double>(Clock::now() - from).count(); };
    const auto signature = [](const RichPlacement& o) { return RichPlacementSignature{o.passenger_index, o.seat, o.blocked}; };
    const auto pattern_signature = [&](const RichExactPattern& pattern) {
        std::vector<RichPlacementSignature> result;
        for (const auto& o : pattern.placements) result.push_back(signature(o));
        return result;
    };
    const auto intersects = [](const RichPricingMask& a, const RichPricingMask& b) {
        for (size_t i = 0; i < a.size(); ++i) if (a[i] & b[i]) return true;
        return false;
    };
    const auto unite = [](RichPricingMask a, const RichPricingMask& b) {
        for (size_t i = 0; i < a.size(); ++i) a[i] |= b[i];
        return a;
    };
    const auto& group = problem.groups[group_index];
    const int n = static_cast<int>(group.passengers.size());
    const double tolerance = number("tolerance", 1e-7);
    const bool legacy = boolean("benchmark_legacy_pricing", false);
    const int pool_limit = legacy ? 1 : std::max(1, static_cast<int>(number(exact ? "exact_pricing_columns_per_group" : "quick_pricing_columns_per_group",
        number("pricing_columns_per_group", exact ? 4 : 3))));
    RichPricingResult result;
    std::map<int, RichPlacementSignature> forced_by_passenger;
    for (const auto& item : forced) forced_by_passenger[std::get<0>(item)] = item;
    if (forced_by_passenger.size() != forced.size()) {
        result.termination = "infeasible_branch"; result.elapsed = seconds(started); return result;
    }
    double time_limit = std::max(0.0, number(exact ? "dfs_exact_time_limit" : "dfs_discovery_time_limit", exact ? 15.0 : 1.0));
    long long node_limit = std::max(1LL, static_cast<long long>(number("dfs_node_limit", 2000000)));
    if (exact && n >= std::max(2, static_cast<int>(number("dfs_large_group_min_size", 9)))) {
        time_limit = std::max(time_limit, number("dfs_large_group_exact_time_limit", 15.0));
        node_limit = std::max(node_limit, static_cast<long long>(number("dfs_large_group_node_limit", 5000000)));
    }
    const auto local_deadline = std::min(deadline, after(started, time_limit));
    const auto workspace_started = Clock::now();
    const bool reused = static_cast<bool>(cache.dfs_workspace);
    auto workspace = cache.dfs_workspace;
    if (!workspace) {
        workspace = std::make_shared<RichPricingWorkspace>(build_rich_pricing_workspace(problem, group_index, cache));
        if (!legacy) cache.dfs_workspace = workspace;
    }
    const double workspace_seconds = seconds(workspace_started);
    const auto dynamic_started = Clock::now();
    const auto costs = build_rich_pricing_costs(group.id, cache, *workspace, duals, baby_cost, phase_one);
    RichPricingCache domains = cache;
    RichPricingWorkspace domain_workspace = *workspace;
    std::vector<std::vector<double>> base(n);
    for (const auto& options : cache.all_options) result.priced_placements += static_cast<int>(options.size());
    for (int p = 0; p < n; ++p) {
        std::vector<int> indexes;
        const auto required = forced_by_passenger.find(p);
        for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
            const auto sig = signature(cache.all_options[p][i]);
            if (!forbidden.count(sig) && (required == forced_by_passenger.end() || required->second == sig)) indexes.push_back(static_cast<int>(i));
        }
        if (indexes.empty()) { result.termination = "infeasible_branch"; result.elapsed = seconds(started); return result; }
        const auto key = [&](int i) {
            const auto& o = cache.all_options[p][i];
            std::vector<std::string> blocked;
            for (int s : o.blocked) blocked.push_back(problem.seats[s].id);
            return std::make_tuple(costs.base[p][i], o.resources.size(), problem.seats[o.seat].id, blocked);
        };
        std::stable_sort(indexes.begin(), indexes.end(), [&](int a, int b) { return key(a) < key(b); });
        domains.all_options[p].clear(); domain_workspace.resource_masks[p].clear();
        domain_workspace.flag_masks[p].clear(); domain_workspace.option_flag_indexes[p].clear();
        for (int i : indexes) {
            domains.all_options[p].push_back(cache.all_options[p][i]); base[p].push_back(costs.base[p][i]);
            domain_workspace.resource_masks[p].push_back(workspace->resource_masks[p][i]);
            domain_workspace.flag_masks[p].push_back(workspace->flag_masks[p][i]);
            domain_workspace.option_flag_indexes[p].push_back(workspace->option_flag_indexes[p][i]);
        }
    }
    result.workspace_builds = !reused; result.workspace_reuses = reused;
    result.workspace_build_seconds = reused ? 0.0 : workspace_seconds;
    std::vector<int> order(n); std::iota(order.begin(), order.end(), 0);
    std::set<int> cared;
    for (const auto& spec : workspace->caregiver_specs) cared.insert(spec.passenger_index);
    const auto priority = [&](int p) {
        const auto& passenger = problem.passengers[group.passengers[p]];
        return std::make_tuple(!forced_by_passenger.count(p), !(passenger.need_both_empty || passenger.need_single_empty),
            !cared.count(p), domains.all_options[p].size(), p);
    };
    std::sort(order.begin(), order.end(), [&](int a, int b) { return priority(a) < priority(b); });
    const std::set<RichPlacementSignature> historical(cache.historical_start.begin(), cache.historical_start.end());
    std::vector<std::vector<int>> branch(n);
    for (int p : order) {
        branch[p].resize(domains.all_options[p].size()); std::iota(branch[p].begin(), branch[p].end(), 0);
        const auto key = [&](int i) { const auto& o = domains.all_options[p][i]; return std::make_tuple(!historical.count(signature(o)), base[p][i], problem.seats[o.seat].id); };
        std::stable_sort(branch[p].begin(), branch[p].end(), [&](int a, int b) { return key(a) < key(b); });
    }
    const auto& geometry = workspace->geometry;
    const auto bounds = build_rich_pricing_bounds(domains, geometry, order, base,
        phase_one ? 0.0 : -problem.weight_c * problem.group_centroid_y_factor / 2.0,
        phase_one ? 0.0 : -problem.weight_c * problem.group_centroid_x_factor / 2.0);
    const auto group_dual_entry = duals.group.find(group.id);
    const double group_dual = group_dual_entry == duals.group.end() ? 0.0 : group_dual_entry->second;
    std::vector<int> selected(n, -1);
    RichExactPattern best_pattern;
    bool have_best = false;
    std::vector<RichExactPattern> negative;
    std::set<std::vector<RichPlacementSignature>> seen;
    const auto reduced_cost = [&](const RichExactPattern& pattern) { return rich_pattern_reduced_cost(pattern, duals, baby_cost, phase_one); };
    const auto retain = [&](const RichExactPattern& pattern) {
        ++result.negative_patterns_seen;
        const auto sig = pattern_signature(pattern); seen.insert(sig);
        auto found = std::find_if(negative.begin(), negative.end(), [&](const auto& item) { return pattern_signature(item) == sig; });
        if (found == negative.end()) negative.push_back(pattern); else *found = pattern;
        if (static_cast<int>(negative.size()) > pool_limit) {
            auto worst = negative.begin();
            for (auto it = negative.begin() + 1; it != negative.end(); ++it) if (reduced_cost(*it) > reduced_cost(*worst)) worst = it;
            negative.erase(worst);
        }
    };
    if (boolean("dfs_use_historical_incumbent", true)) {
        std::map<int, RichPlacementSignature> by_passenger;
        for (const auto& sig : cache.historical_start) by_passenger[std::get<0>(sig)] = sig;
        if (static_cast<int>(by_passenger.size()) == n) {
            std::vector<RichPlacement> chosen;
            std::set<int> used;
            bool valid = true;
            for (int p = 0; p < n; ++p) {
                const auto sig = by_passenger.find(p);
                if (sig == by_passenger.end()) { valid = false; break; }
                const auto entry = workspace->option_by_signature.find(sig->second);
                if (entry == workspace->option_by_signature.end()) { valid = false; break; }
                const auto& option = cache.all_options[entry->second.first][entry->second.second];
                chosen.push_back(option);
                for (int seat : option.resources) if (!used.insert(seat).second) valid = false;
            }
            if (valid && rich_placements_caregiver_ok(problem, group_index, chosen)) {
                best_pattern = build_rich_exact_pattern(problem, group_index, chosen, active_ssr_types);
                result.reduced_cost = reduced_cost(best_pattern); have_best = true; result.incumbent_seeded = true;
                if (result.reduced_cost < -tolerance) retain(best_pattern);
            }
        }
    }
    const auto symmetry = build_rich_pricing_symmetry(problem, group_index, domains, boolean("dfs_symmetry_breaking_enabled", true));
    result.symmetry_classes = symmetry.class_count;
    const auto unset_time = Clock::time_point::max();
    auto negative_stop = unset_time;
    const auto set_negative_stop = [&]() { negative_stop = std::min(local_deadline, after(Clock::now(), std::max(0.0, number("dfs_negative_refinement_time", 0.25)))); };
    if (stop_on_negative && result.reduced_cost < -tolerance) set_negative_stop();
    const auto lower_bound = [&](int depth, const RichPricingMask& used, const RichPricingMask& flags, double additive,
        int row_low, int row_high, int x_low, int x_high) {
        double remaining_flags = 0.0;
        for (size_t bit = 0; bit < costs.flags.size(); ++bit) if (costs.flags[bit] < 0.0 && !(flags[bit / 64] & (std::uint64_t{1} << (bit % 64)))) remaining_flags += costs.flags[bit];
        const double constant = additive + remaining_flags + costs.baby_relaxation - group_dual;
        double span, coupled;
        if (row_low >= 0) {
            const size_t cell = geometry.rectangle_index(row_low, row_high, x_low, x_high);
            span = bounds.span[cell]; coupled = bounds.suffix[depth][cell];
        } else { span = bounds.root_span; coupled = *std::min_element(bounds.suffix[depth].begin(), bounds.suffix[depth].end()); }
        double independent = constant + span;
        for (int d = depth; d < n; ++d) {
            const int p = order[d]; double minimum = std::numeric_limits<double>::infinity();
            for (size_t i = 0; i < domains.all_options[p].size(); ++i) if (!intersects(domain_workspace.resource_masks[p][i], used)) { minimum = base[p][i]; break; }
            if (!std::isfinite(minimum)) return std::numeric_limits<double>::infinity();
            independent += minimum;
        }
        return std::max(independent, constant + coupled);
    };
    using State = std::tuple<int, RichPricingMask, RichPricingMask, RichPricingMask, RichPricingMask, std::vector<RichPricingMask>>;
    std::map<State, double> best_cost;
    std::string aborted;
    std::function<bool(int, const RichPricingMask&, const RichPricingMask&, const RichPricingMask&, const RichPricingMask&, double, int, int, int, int)> search;
    search = [&](int depth, const RichPricingMask& used, const RichPricingMask& occupied, const RichPricingMask& infants,
        const RichPricingMask& flags, double additive, int row_low, int row_high, int x_low, int x_high) {
        ++result.nodes;
        if (negative_stop != unset_time && Clock::now() >= negative_stop) { aborted = "dfs_negative_refinement_limit"; return true; }
        if (result.nodes > node_limit) { aborted = "dfs_node_limit"; return true; }
        if (Clock::now() >= local_deadline) { aborted = "dfs_time_limit"; return true; }
        State state{depth, used, occupied, infants, flags, rich_pricing_caregiver_state(problem, domains, domain_workspace, selected)};
        const auto previous = best_cost.find(state);
        if (previous != best_cost.end() && previous->second <= additive + tolerance) return false;
        best_cost[std::move(state)] = additive;
        const double bound = lower_bound(depth, used, flags, additive, row_low, row_high, x_low, x_high);
        double cutoff = result.reduced_cost - tolerance;
        if (stop_on_negative) cutoff = -tolerance;
        else if (result.reduced_cost < -tolerance) {
            if (static_cast<int>(negative.size()) < pool_limit) cutoff = -tolerance;
            else { cutoff = -std::numeric_limits<double>::infinity(); for (const auto& pattern : negative) cutoff = std::max(cutoff, reduced_cost(pattern)); cutoff -= tolerance; }
        }
        if (bound >= cutoff) { ++result.bound_prunes; return false; }
        if (depth == n) {
            std::vector<RichPlacement> chosen;
            for (int p = 0; p < n; ++p) if (selected[p] >= 0) chosen.push_back(domains.all_options[p][selected[p]]);
            if (static_cast<int>(chosen.size()) != n || !rich_placements_caregiver_ok(problem, group_index, chosen)) return false;
            const auto pattern = build_rich_exact_pattern(problem, group_index, chosen, active_ssr_types);
            const double rc = reduced_cost(pattern);
            if (rc < result.reduced_cost) {
                result.reduced_cost = rc; best_pattern = pattern; have_best = true; cache.historical_start = pattern_signature(pattern);
                if (stop_on_negative && rc < -tolerance && negative_stop == unset_time) set_negative_stop();
            }
            if (rc < -tolerance) retain(pattern);
            return false;
        }
        const int p = order[depth];
        for (int i : branch[p]) {
            const auto& option = domains.all_options[p][i];
            if (intersects(domain_workspace.resource_masks[p][i], used)) { ++result.resource_prunes; continue; }
            if (!rich_pricing_symmetry_ok(symmetry, selected, p, i)) { ++result.symmetry_prunes; continue; }
            selected[p] = i;
            const auto next_used = unite(used, domain_workspace.resource_masks[p][i]);
            if (!rich_pricing_caregiver_possible(problem, domains, domain_workspace, selected, next_used)) { selected[p] = -1; continue; }
            const int row = geometry.seat_row_index.at(option.seat), x = geometry.seat_x_index.at(option.seat);
            double new_flag_cost = 0.0;
            for (int bit : domain_workspace.option_flag_indexes[p][i]) if (!(flags[bit / 64] & (std::uint64_t{1} << (bit % 64)))) new_flag_cost += costs.flags[bit];
            auto next_occupied = occupied, next_infants = infants;
            next_occupied[option.seat / 64] |= std::uint64_t{1} << (option.seat % 64);
            if (option.is_infant) next_infants[option.seat / 64] |= std::uint64_t{1} << (option.seat % 64);
            const bool stopped = search(depth + 1, next_used, next_occupied, next_infants, unite(flags, domain_workspace.flag_masks[p][i]),
                additive + base[p][i] + new_flag_cost, row_low < 0 ? row : std::min(row_low, row), row_low < 0 ? row : std::max(row_high, row),
                x_low < 0 ? x : std::min(x_low, x), x_low < 0 ? x : std::max(x_high, x));
            selected[p] = -1;
            if (stopped) return true;
        }
        return false;
    };
    result.dynamic_refresh_seconds = seconds(dynamic_started);
    const RichPricingMask empty_seats((problem.seats.size() + 63) / 64, 0), empty_flags((workspace->flag_locations.size() + 63) / 64, 0);
    const double root_bound = lower_bound(0, empty_seats, empty_flags, 0.0, -1, -1, -1, -1);
    search(0, empty_seats, empty_seats, empty_seats, empty_flags, 0.0, -1, -1, -1, -1);
    result.proven_optimal = aborted.empty();
    result.termination = aborted == "dfs_negative_refinement_limit" ? "dfs_negative" : !aborted.empty() ? aborted : result.reduced_cost < -tolerance ? "dfs_negative" : "dfs_optimal";
    std::stable_sort(negative.begin(), negative.end(), [&](const auto& a, const auto& b) { return reduced_cost(a) < reduced_cost(b); });
    if (negative.empty() && have_best) negative.push_back(best_pattern);
    result.patterns = std::move(negative); result.unique_negative_patterns = static_cast<long long>(seen.size());
    result.lower_bound = result.proven_optimal ? result.reduced_cost : root_bound;
    result.elapsed = seconds(started);
    return result;
}

RichStructuredDiagnostics generate_rich_structured_patterns(const Problem& problem,
    const std::vector<int>& assignment, std::chrono::steady_clock::time_point deadline,
    const std::function<void(int, const RichTieredPattern&, double, bool)>& recorder
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    RichStructuredDiagnostics diagnostics;
    diagnostics.enabled = problem.rich.enable_structured_pattern_generation;
    if (!diagnostics.enabled || started >= deadline) {
        diagnostics.stopped_by_deadline = started >= deadline;
        diagnostics.seconds = std::chrono::duration<double>(Clock::now() - started).count(); return diagnostics;
    }
    auto pricing = problem.rich_pricing_config;
    pricing.type = Value::Type::Object;
    const auto set_number = [&](const std::string& key, double number) {
        auto& value = pricing.object[key]; value.type = Value::Type::Number; value.number = number;
    };
    const double per_group_limit = problem.rich.structured_pattern_dfs_per_group;
    set_number("dfs_discovery_time_limit", per_group_limit);
    set_number("quick_pricing_columns_per_group", problem.rich.structured_patterns_per_group);
    std::set<std::string> active;
    for (const auto& passenger : problem.passengers) if (!passenger.ssr.empty()) active.insert(passenger.ssr);
    const std::vector<std::string> active_types(active.begin(), active.end());
    const auto fixed = preprocess_fixed_seats(problem);
    const auto baby = build_rich_baby_costs(problem);
    const auto order = build_rich_structured_order(problem, assignment);
    diagnostics.repair_queue = order.repair_queue;
    if (diagnostics.repair_queue.size() > 20) diagnostics.repair_queue.resize(20);
    const std::set<int> difficult(order.difficult_groups.begin(), order.difficult_groups.end());
    std::map<int, RichGroupRepairMetric> metrics;
    for (const auto& metric : order.repair_queue) metrics[metric.group_id] = metric;
    const auto after = [](Clock::time_point origin, double seconds) { return origin + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)); };
    using Key = std::pair<std::vector<RichPlacementSignature>, std::vector<std::pair<int, int>>>;
    const auto key = [](const RichExactPattern& pattern) {
        std::vector<RichPlacementSignature> signatures;
        for (const auto& o : pattern.placements) signatures.emplace_back(o.passenger_index, o.seat, o.blocked);
        return Key{signatures, pattern.blocked_by};
    };
    for (int g : order.ordered_groups) {
        if (Clock::now() >= deadline) { diagnostics.stopped_by_deadline = true; break; }
        if (!difficult.count(g)) continue;
        const auto& group = problem.groups[g]; const auto& metric = metrics.at(group.id);
        const int target_span = std::max(0, metric.row_span - 2);
        if (metric.row_span >= 2) ++diagnostics.extreme_groups_attempted;
        ++diagnostics.groups_attempted;
        const auto cache = build_rich_pricing_cache(problem, g, fixed);
        std::vector<int> targets;
        for (int p : group.passengers) targets.push_back(assignment[p]);
        diagnostics.three_tier_active = problem.rich_three_tier_active;
        auto patterns = generate_rich_rigid_relaxed_patterns(problem, g, targets, cache.all_options, active_types);
        const auto group_deadline = std::min(deadline, after(Clock::now(), order.full_resource_global_blocks ? std::max(per_group_limit, 0.16) : per_group_limit));
        const auto windows = build_rich_structured_windows(problem, g, cache.all_options, metric);
        generate_rich_value_block_patterns(problem, g, cache.all_options, metric, windows, active_types, group_deadline, patterns);
        std::map<Key, size_t> position;
        for (size_t i = 0; i < patterns.size(); ++i) position[key(patterns[i].pattern)] = i;
        const double per_window_limit = std::max(0.06, per_group_limit / static_cast<double>(std::max(size_t{1}, windows.row_windows.size())));
        for (const auto& rows : windows.row_windows) {
            if (Clock::now() >= group_deadline) break;
            set_number("dfs_discovery_time_limit", per_window_limit);
            auto window_cache = filter_rich_pricing_window(problem, cache, rows);
            if (std::any_of(window_cache.all_options.begin(), window_cache.all_options.end(), [](const auto& options) { return options.empty(); })) continue;
            ++diagnostics.row_windows_attempted;
            RichPricingDuals duals; duals.group[group.id] = 1.0e12;
            const auto priced = price_rich_group_dfs(problem, g, pricing, duals, baby, {}, {},
                std::min(group_deadline, after(Clock::now(), per_window_limit)), window_cache, false, false, false, active_types);
            diagnostics.dfs_nodes += priced.nodes;
            for (const auto& pattern : priced.patterns) {
                const auto identity = key(pattern); const auto found = position.find(identity);
                if (found == position.end()) { position[identity] = patterns.size(); patterns.push_back({pattern, "rebuilt"}); }
                else patterns[found->second] = {pattern, "rebuilt"};
            }
        }
        int accepted = 0;
        for (const auto& item : patterns) {
            auto proposal = assignment;
            for (int p : group.passengers) proposal[p] = -1;
            int low = std::numeric_limits<int>::max(), high = std::numeric_limits<int>::min();
            for (const auto& entry : item.pattern.assignments) {
                proposal[entry.first] = entry.second;
                low = std::min(low, problem.seats[entry.second].row); high = std::max(high, problem.seats[entry.second].row);
            }
            const double score = evaluate_rich_group_score(problem, proposal, g).total();
            const int span = item.pattern.assignments.empty() ? 0 : high - low;
            if (metric.row_span >= 2 && span <= target_span) ++diagnostics.span_reducing_patterns;
            recorder(g, item, score, item.source == "global_value_block");
            ++diagnostics.tier_counts.at(item.source); ++accepted;
        }
        if (accepted) { ++diagnostics.groups_with_patterns; diagnostics.patterns_generated += accepted; }
    }
    diagnostics.seconds = std::chrono::duration<double>(Clock::now() - started).count();
    diagnostics.stopped_by_deadline = diagnostics.stopped_by_deadline || Clock::now() >= deadline;
    return diagnostics;
}

void write_rich_structured_diagnostics(std::ostream& output, const RichStructuredDiagnostics& stats) {
    output << "{\"enabled\":" << (stats.enabled ? "true" : "false")
        << ",\"stopped_by_deadline\":" << (stats.stopped_by_deadline ? "true" : "false")
        << ",\"three_tier_active\":" << (stats.three_tier_active ? "true" : "false")
        << ",\"groups_attempted\":" << stats.groups_attempted << ",\"groups_with_patterns\":" << stats.groups_with_patterns
        << ",\"patterns_generated\":" << stats.patterns_generated << ",\"row_windows_attempted\":" << stats.row_windows_attempted
        << ",\"dfs_nodes\":" << stats.dfs_nodes << ",\"extreme_groups_attempted\":" << stats.extreme_groups_attempted
        << ",\"span_reducing_patterns\":" << stats.span_reducing_patterns << ",\"seconds\":" << stats.seconds << ",\"tier_counts\":{";
    bool first = true;
    for (const auto& entry : stats.tier_counts) { if (!first) output << ','; first = false; output << '"' << entry.first << "\":" << entry.second; }
    output << "},\"repair_queue\":"; write_rich_repair_queue(output, stats.repair_queue, 20); output << '}';
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

RichLnsWorkspace::RichLnsWorkspace(const AssignmentState& state, std::chrono::steady_clock::time_point deadline)
    : keys_by_group(state.problem.groups.size()), state_(state), deadline_(deadline) {
    const auto& problem = state.problem;
    for (int p : state.assignment_order) {
        keys_by_group[problem.passengers[p].group].push_back(p);
        if (problem.passengers[p].ssr == "BSCT") infants_.emplace_back(problem.passengers[p].group, state.passenger_to_seat[p]);
    }
    for (int g = 0; g < static_cast<int>(keys_by_group.size()); ++g) {
        const auto& keys = keys_by_group[g];
        if (keys.empty() || keys.size() > static_cast<size_t>(problem.rich.multigroup_pattern_group_size_limit)) continue;
        if (std::all_of(keys.begin(), keys.end(), [&](int p) {
            const auto& passenger = problem.passengers[p];
            return passenger.new_seat_num_is_none && !passenger.need_both_empty && !passenger.need_single_empty;
        })) eligible_groups.insert(g);
    }
}

double RichLnsWorkspace::passenger_score(int p, int s) {
    const auto& problem = state_.problem;
    const auto individual_key = std::make_pair(p, s);
    auto individual = individual_cache_.find(individual_key);
    if (individual == individual_cache_.end()) {
        std::vector<int> isolated(problem.passengers.size(), -1); isolated[p] = s;
        individual = individual_cache_.emplace(individual_key,
            evaluate_rich_group_score(problem, isolated, problem.passengers[p].group).total()).first;
    }
    const auto baby_key = std::make_pair(problem.passengers[p].group, s);
    auto baby = baby_cache_.find(baby_key);
    if (baby == baby_cache_.end()) {
        double value = 0.0;
        for (const auto& infant : infants_) {
            if (infant.first == baby_key.first || infant.second == s) continue;
            const auto& a = problem.seats[infant.second]; const auto& b = problem.seats[s];
            if (a.cabin != b.cabin) continue;
            const double distance = 1.0 + std::abs(a.x - b.x);
            if (a.row == b.row && a.subrow == b.subrow) value += problem.weight_b / distance;
            else if (std::abs(a.row - b.row) == 1) value += problem.weight_b * problem.baby_front_back_factor / distance;
        }
        baby = baby_cache_.emplace(baby_key, value).first;
    }
    return individual->second + baby->second;
}

double RichLnsWorkspace::compact_score(const std::vector<int>& seats) {
    auto key = seats;
    const auto& problem = state_.problem;
    std::sort(key.begin(), key.end(), [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; });
    const auto found = compact_cache_.find(key);
    if (found != compact_cache_.end()) return found->second;
    double value = 0.0;
    if (seats.size() > 1) {
        double x = 0.0, y = 0.0, dx = 0.0, dy = 0.0;
        for (int s : seats) { x += problem.seats[s].x; y += problem.seats[s].y; }
        x /= seats.size(); y /= seats.size();
        for (int s : seats) { dx = std::max(dx, std::abs(problem.seats[s].x - x)); dy = std::max(dy, std::abs(problem.seats[s].y - y)); }
        value = problem.weight_c * (problem.group_centroid_x_factor * dx + problem.group_centroid_y_factor * dy);
    }
    compact_cache_[std::move(key)] = value;
    return value;
}

RichLnsAssignment RichLnsWorkspace::best_matching(const std::vector<int>& keys, const std::vector<int>& seats) {
    const auto popcount = [](size_t value) { int count = 0; while (value) { value &= value - 1; ++count; } return count; };
    const size_t count = size_t{1} << keys.size();
    std::vector<double> dp(count, -std::numeric_limits<double>::infinity());
    std::vector<int> parent(count, -1);
    dp[0] = 0.0;
    for (size_t mask = 0; mask < count; ++mask) {
        if (mask % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; return {}; }
        const int p = popcount(mask);
        if (p >= static_cast<int>(keys.size()) || dp[mask] == -std::numeric_limits<double>::infinity()) continue;
        for (size_t j = 0; j < seats.size(); ++j) {
            const size_t bit = size_t{1} << j;
            if (mask & bit) continue;
            const size_t next = mask | bit;
            const double value = dp[mask] + passenger_score(keys[p], seats[j]);
            if (value > dp[next]) { dp[next] = value; parent[next] = static_cast<int>(j); }
        }
    }
    RichLnsAssignment result; result.score = dp.back(); result.seats.resize(keys.size());
    size_t mask = count - 1;
    while (mask) {
        const int j = parent[mask]; const size_t previous = mask ^ (size_t{1} << j);
        result.seats[popcount(previous)] = seats[j]; mask = previous;
    }
    return result;
}

RichLnsAssignment RichLnsWorkspace::best_group_assignment(int group_index, const std::vector<int>& seats,
    const std::set<int>& released_seats
) {
    const auto& problem = state_.problem;
    const auto& keys = keys_by_group[group_index];
    const auto needs_care = [&](int p) { return problem.passengers[p].need_cared || ssr_rule(problem, problem.passengers[p]).requires_caregiver; };
    if (std::all_of(keys.begin(), keys.end(), [&](int p) { return problem.passengers[p].ssr.empty() && !needs_care(p); }))
        return best_matching(keys, seats);
    RichLnsAssignment result;
    std::vector<size_t> permutation(seats.size());
    for (size_t i = 0; i < seats.size(); ++i) permutation[i] = i;
    size_t index = 0;
    do {
        if (index % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; break; }
        ++index;
        bool feasible = true;
        for (size_t i = 0; i < keys.size(); ++i)
            if (!state_.rich_seat_feasible(keys[i], seats[permutation[i]], -1, -1, released_seats)) { feasible = false; break; }
        if (!feasible) continue;
        for (size_t i = 0; i < keys.size(); ++i) {
            if (!needs_care(keys[i])) continue;
            const auto rule = ssr_rule(problem, problem.passengers[keys[i]]);
            const auto& seat = problem.seats[seats[permutation[i]]];
            const auto& neighbors = rule.caregiver_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
            bool caregiver = false;
            for (size_t j = 0; j < keys.size(); ++j)
                if (j != i && problem.passengers[keys[j]].ssr.empty()
                    && std::find(neighbors.begin(), neighbors.end(), seats[permutation[j]]) != neighbors.end()) caregiver = true;
            if (!caregiver) { feasible = false; break; }
        }
        if (!feasible) continue;
        double score = 0.0;
        for (size_t i = 0; i < keys.size(); ++i) score += passenger_score(keys[i], seats[permutation[i]]);
        if (score > result.score) {
            result.score = score; result.seats.clear();
            for (size_t j : permutation) result.seats.push_back(seats[j]);
        }
    } while (std::next_permutation(permutation.begin(), permutation.end()));
    return result;
}

std::vector<std::vector<int>> RichLnsWorkspace::candidate_subsets(int group_index, const std::vector<int>& seat_pool) {
    const auto& problem = state_.problem;
    const auto seat_less = [&](int a, int b) { return problem.seats[a].id < problem.seats[b].id; };
    const auto tuple_less = [&](const std::vector<int>& a, const std::vector<int>& b) {
        return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end(), seat_less);
    };
    // Python iterates a hash set without a frozen hash seed. Use a documented
    // lexical traversal of the identical set; equal-score cutoff identity can differ.
    std::set<std::vector<int>, decltype(tuple_less)> subsets(tuple_less);
    const auto& keys = keys_by_group[group_index];
    const std::set<int> released(seat_pool.begin(), seat_pool.end());
    std::vector<int> current;
    for (int p : keys) current.push_back(state_.passenger_to_seat[p]);
    if (std::all_of(current.begin(), current.end(), [&](int s) { return released.count(s) != 0; })) {
        std::sort(current.begin(), current.end(), seat_less); subsets.insert(current);
    }
    const size_t neighborhood_size = std::min(seat_pool.size(), keys.size() + problem.rich.multigroup_neighborhood_extra_seats);
    for (size_t center_index = 0; center_index < seat_pool.size(); ++center_index) {
        if (center_index % 8 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; break; }
        const int center = seat_pool[center_index];
        const auto& c = problem.seats[center];
        auto nearest = seat_pool;
        std::sort(nearest.begin(), nearest.end(), [&](int a, int b) {
            const auto& x = problem.seats[a]; const auto& y = problem.seats[b];
            return std::make_tuple(std::abs(x.row - c.row), std::abs(x.x - c.x), x.id)
                < std::make_tuple(std::abs(y.row - c.row), std::abs(y.x - c.x), y.id);
        });
        nearest.resize(neighborhood_size);
        if (keys.size() > nearest.size()) continue;
        std::vector<size_t> indexes(keys.size());
        for (size_t i = 0; i < indexes.size(); ++i) indexes[i] = i;
        size_t combination_index = 0;
        while (true) {
            if (combination_index++ % 128 == 0 && std::chrono::steady_clock::now() >= deadline_) { stopped_by_deadline = true; break; }
            std::vector<int> seats;
            for (size_t i : indexes) seats.push_back(nearest[i]);
            if (std::find(seats.begin(), seats.end(), center) != seats.end()) {
                std::sort(seats.begin(), seats.end(), seat_less); subsets.insert(std::move(seats));
            }
            int i = static_cast<int>(indexes.size()) - 1;
            while (i >= 0 && indexes[i] == nearest.size() - indexes.size() + i) --i;
            if (i < 0) break;
            ++indexes[i];
            for (size_t j = i + 1; j < indexes.size(); ++j) indexes[j] = indexes[j - 1] + 1;
        }
    }
    return {subsets.begin(), subsets.end()};
}

std::vector<RichLnsOption> RichLnsWorkspace::group_options(int group_index, const std::vector<int>& seat_pool,
    const std::function<void(int, const RichLnsOption&)>& recorder
) {
    const auto& problem = state_.problem;
    const std::set<int> released(seat_pool.begin(), seat_pool.end());
    const auto& keys = keys_by_group[group_index];
    struct Entry { RichLnsOption option; int sequence; };
    const auto greater = [](const Entry& a, const Entry& b) {
        return std::make_pair(a.option.score, a.sequence) > std::make_pair(b.option.score, b.sequence);
    };
    std::vector<Entry> heap;
    int sequence = 0;
    for (const auto& seats : candidate_subsets(group_index, seat_pool)) {
        if (std::chrono::steady_clock::now() >= deadline_) break;
        auto matching = best_group_assignment(group_index, seats, released);
        if (matching.seats.empty()) continue;
        auto proposal = state_.passenger_to_seat;
        for (size_t i = 0; i < keys.size(); ++i) proposal[keys[i]] = matching.seats[i];
        Entry entry{{evaluate_rich_group_score(problem, proposal, group_index).total(),
            std::set<int>(seats.begin(), seats.end()), std::move(matching.seats)}, sequence++};
        if (heap.size() < static_cast<size_t>(problem.rich.multigroup_option_limit)) {
            heap.push_back(std::move(entry)); std::push_heap(heap.begin(), heap.end(), greater);
        } else if (entry.option.score > heap.front().option.score) {
            std::pop_heap(heap.begin(), heap.end(), greater); heap.back() = std::move(entry);
            std::push_heap(heap.begin(), heap.end(), greater);
        }
    }
    options_generated += static_cast<int>(heap.size());
    std::sort(heap.begin(), heap.end(), greater);
    std::vector<RichLnsOption> result;
    for (auto& entry : heap) result.push_back(std::move(entry.option));
    if (recorder) for (size_t i = 0; i < std::min(result.size(), static_cast<size_t>(problem.rich.elite_patterns_from_pricing_per_call)); ++i)
        recorder(group_index, result[i]);
    return result;
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

bool rich_patterns_have_conditional_ssr_conflict(const Problem& problem,
    int left_group_id, const RichElitePattern& left, int right_group_id, const RichElitePattern& right
) {
    for (bool by_row : {true, false}) {
        std::map<std::pair<std::pair<int, int>, std::string>, int> counts;
        std::set<std::pair<int, int>> active;
        const auto profile = [&](int gid, const RichElitePattern& pattern) {
            const auto group = std::find_if(problem.groups.begin(), problem.groups.end(), [&](const Group& g) { return g.id == gid; });
            for (const auto& entry : pattern.assignments) {
                const auto p = std::find_if(group->passengers.begin(), group->passengers.end(),
                    [&](int index) { return problem.passengers[index].hostnum == entry.first; });
                const auto& passenger = problem.passengers[*p];
                if (passenger.ssr.empty()) continue;
                const auto& seat = problem.seats[problem.seat_index.at(entry.second)];
                const auto location = std::make_pair(seat.row, by_row ? -1 : seat.subrow);
                ++counts[{location, passenger.ssr}];
                if (by_row ? passenger.same_row_no_other_ssr : passenger.same_subrow_no_other_ssr) active.insert(location);
            }
        };
        profile(left_group_id, left); profile(right_group_id, right);
        for (const auto& entry : counts) if (entry.second > 1 && active.count(entry.first.first)) return true;
    }
    return false;
}

bool rebuild_rich_pattern_component(const AssignmentState& state,
    const std::map<int, RichElitePattern>& choices, AssignmentSnapshot& rebuilt
) {
    const auto& problem = state.problem;
    AssignmentState candidate(problem);
    candidate.restore(state.save());
    for (const auto& group : problem.groups) if (choices.count(group.id))
        for (int p : group.passengers) candidate.remove(p);
    for (const auto& choice : choices) {
        const auto group = std::find_if(problem.groups.begin(), problem.groups.end(), [&](const Group& g) { return g.id == choice.first; });
        std::vector<std::pair<int, int>> proposed;
        std::set<int> seats;
        for (const auto& entry : choice.second.assignments) {
            const auto p = std::find_if(group->passengers.begin(), group->passengers.end(),
                [&](int index) { return problem.passengers[index].hostnum == entry.first; });
            const int seat = problem.seat_index.at(entry.second);
            proposed.emplace_back(*p, seat); seats.insert(seat);
        }
        const auto requires_care = [&](int p) {
            return problem.passengers[p].need_cared || ssr_rule(problem, problem.passengers[p]).requires_caregiver;
        };
        std::stable_sort(proposed.begin(), proposed.end(), [&](const auto& a, const auto& b) { return requires_care(a.first) < requires_care(b.first); });
        for (const auto& entry : proposed) {
            const auto& passenger = problem.passengers[entry.first];
            int chosen = -1;
            for (const auto& blocked : choice.second.blocked_by_host)
                if (passenger.need_single_empty && blocked.first == passenger.hostnum && !blocked.second.empty()) {
                    chosen = problem.seat_index.at(blocked.second.front()); break;
                }
            auto excluded = seats; excluded.erase(entry.second);
            if (!candidate.assign_rich_pattern(entry.first, entry.second, excluded, chosen)) return false;
        }
    }
    rebuilt = candidate.save();
    return true;
}

static void normalize_rich_elite_pattern(RichElitePattern& pattern) {
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
}

void RichEliteStore::record(int group_id, RichElitePattern pattern,
    const std::map<std::string, int>& owner_by_resource, bool conflict_diversity_active
) {
    normalize_rich_elite_pattern(pattern);
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

bool RichEliteStore::insert_relocation(int group_id, RichElitePattern pattern, const std::function<double()>& score) {
    normalize_rich_elite_pattern(pattern);
    auto& patterns = groups_[group_id];
    for (const auto& old : patterns)
        if (old.assignments == pattern.assignments && old.blocked_by_host == pattern.blocked_by_host) return false;
    pattern.local_score = score();
    patterns.push_back(std::move(pattern));
    return true;
}

RichDynamicRelocationDiagnostics add_rich_dynamic_relocation_patterns(
    const Problem& problem, const AssignmentState& state, int group_index,
    const std::set<int>& outside_resources, std::chrono::steady_clock::time_point deadline,
    const FixedSeatContext& fixed, const std::vector<RichBabyCost>& baby,
    std::map<int, RichPricingCache>& pricing_caches, RichEliteStore& elite
) {
    using Clock = std::chrono::steady_clock;
    RichDynamicRelocationDiagnostics diagnostics;
    if (!problem.rich.protected_dynamic_relocation_enabled || Clock::now() >= deadline) return diagnostics;
    auto found = pricing_caches.find(group_index);
    if (found == pricing_caches.end())
        found = pricing_caches.emplace(group_index, build_rich_pricing_cache(problem, group_index, fixed)).first;
    auto released = filter_rich_pricing_resources(problem, found->second, outside_resources);
    if (std::any_of(released.all_options.begin(), released.all_options.end(),
        [](const auto& options) { return options.empty(); })) return diagnostics;
    const auto call_deadline = std::min(deadline, Clock::now()
        + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(problem.rich.protected_dynamic_relocation_seconds)));
    auto config = problem.rich_pricing_config;
    config.type = native_json::Value::Type::Object;
    for (const auto& entry : std::vector<std::pair<std::string, double>>{
        {"quick_pricing_columns_per_group", static_cast<double>(problem.rich.protected_dynamic_relocation_columns)},
        {"dfs_discovery_time_limit", std::max(.01, std::chrono::duration<double>(call_deadline - Clock::now()).count())}}) {
        auto& value = config.object[entry.first];
        value.type = native_json::Value::Type::Number; value.number = entry.second;
    }
    const int gid = problem.groups[group_index].id;
    RichPricingDuals duals; duals.group[gid] = 1e12;
    ++diagnostics.calls;
    const auto priced = price_rich_group_dfs(problem, group_index, config, duals, baby,
        {}, {}, call_deadline, released, false, false, false);
    elite.ensure_group(gid);
    for (size_t i = 0; i < priced.patterns.size() && i < static_cast<size_t>(problem.rich.protected_dynamic_relocation_columns); ++i) {
        const auto& candidate = priced.patterns[i];
        RichElitePattern pattern;
        auto proposal = state.passenger_to_seat;
        for (const auto& entry : candidate.assignments) {
            pattern.assignments.emplace_back(problem.passengers[entry.first].hostnum, problem.seats[entry.second].id);
            proposal[entry.first] = entry.second;
        }
        std::map<int, std::vector<std::string>> blocked;
        for (const auto& entry : candidate.blocked_by)
            blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
        pattern.blocked_by_host.assign(blocked.begin(), blocked.end());
        pattern.source = "released_component_pricing";
        if (elite.insert_relocation(gid, std::move(pattern),
            [&]() { return evaluate_rich_group_score(problem, proposal, group_index).total(); })) ++diagnostics.patterns;
    }
    return diagnostics;
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
