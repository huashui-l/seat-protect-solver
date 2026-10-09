#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {
using native_json::Value;
// Functional section: input normalization and topology-independent problem construction.
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
        if (const Value* item = algorithm->find("enable_tail_exchange")) problem.rich.tail_exchange_enabled = item->bool_or(false);
        if (const Value* item = algorithm->find("structured_pattern_window_limit")) problem.rich.structured_pattern_window_limit = std::max(1, static_cast<int>(item->number_or(12)));
        if (const Value* item = algorithm->find("prioritize_front")) problem.prioritize_front = item->bool_or(problem.prioritize_front);
        if (const Value* item = algorithm->find("front_penalty_reduction")) problem.front_penalty_reduction = item->number_or(problem.front_penalty_reduction);
        if (const Value* item = algorithm->find("back_penalty_factor")) problem.back_penalty_factor = item->number_or(problem.back_penalty_factor);
        if (const Value* item = algorithm->find("group_centroid_x_factor")) problem.group_centroid_x_factor = item->number_or(problem.group_centroid_x_factor);
        if (const Value* item = algorithm->find("group_centroid_y_factor")) problem.group_centroid_y_factor = item->number_or(problem.group_centroid_y_factor);
        if (const Value* item = algorithm->find("baby_front_back_factor")) problem.baby_front_back_factor = item->number_or(problem.baby_front_back_factor);
        if (const Value* item = algorithm->find("stage3_time_budget")) problem.rich.stage3_time_budget = item->number_or(problem.rich.stage3_time_budget);
        if (const Value* item = algorithm->find("enable_restricted_pattern_mip")) problem.rich.enable_restricted_pattern_mip = item->bool_or(problem.rich.enable_restricted_pattern_mip);
        if (const Value* item = algorithm->find("enable_restricted_mip_portfolio")) problem.rich.enable_restricted_mip_portfolio = item->bool_or(false);
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
        if (const Value* item = algorithm->find("small_group_dfs_node_limit")) problem.rich.small_group_dfs_node_limit = static_cast<long long>(item->number_or(static_cast<double>(problem.rich.small_group_dfs_node_limit)));
        if (const Value* item = algorithm->find("small_group_dfs_time_limit")) problem.rich.small_group_dfs_time_limit = item->number_or(problem.rich.small_group_dfs_time_limit);
        if (const Value* item = algorithm->find("old_seat_reservation_pressure")) problem.rich.old_seat_reservation_pressure = item->number_or(problem.rich.old_seat_reservation_pressure);
        if (const Value* item = algorithm->find("paired_rescue_option_cap")) problem.rich.paired_rescue_option_cap = static_cast<int>(item->number_or(problem.rich.paired_rescue_option_cap));
        if (const Value* item = algorithm->find("paired_joint_rebuild_candidate_cap")) problem.rich.paired_joint_rebuild_candidate_cap = static_cast<int>(item->number_or(problem.rich.paired_joint_rebuild_candidate_cap));
        if (const Value* item = algorithm->find("paired_joint_rebuild_node_limit")) problem.rich.paired_joint_rebuild_node_limit = static_cast<long long>(item->number_or(static_cast<double>(problem.rich.paired_joint_rebuild_node_limit)));
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
}  // namespace full_cpp
