#include "full_cpp_solver_core.hpp"
#include "native_feasibility_solver.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <sstream>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

std::string escape_json(const std::string& value) {
    std::string result;
    for (unsigned char character : value) {
        switch (character) {
            case '\\': result += "\\\\"; break;
            case '"': result += "\\\""; break;
            case '\b': result += "\\b"; break;
            case '\f': result += "\\f"; break;
            case '\n': result += "\\n"; break;
            case '\r': result += "\\r"; break;
            case '\t': result += "\\t"; break;
            default:
                if (character < 0x20) throw std::runtime_error("control character in JSON string");
                result.push_back(static_cast<char>(character));
        }
    }
    return result;
}

int write_result(std::ostream* output, const full_cpp::Problem& problem,
    const full_cpp::FeasibilityResult& result, full_cpp::ConstructionObjective construction_objective) {
        int unassigned = 0;
        for (int seat : result.passenger_to_seat) unassigned += seat < 0;
        const double native_score = full_cpp::evaluate_soft_score(
            problem, result.passenger_to_seat
        );
        double individual_score = 0.0;
        for (int passenger = 0;
             passenger < static_cast<int>(result.passenger_to_seat.size()); ++passenger) {
            const int seat = result.passenger_to_seat[passenger];
            if (seat >= 0) {
                individual_score += full_cpp::evaluate_individual_score(
                    problem, passenger, seat
                ).total();
            }
        }
        *output << std::setprecision(17)
            << "{\"schema\":\"seat_protect_raw_native_v1\","
            << "\"mode\":\"RAW_NATIVE\","
            << "\"construction_objective\":\""
            << full_cpp::construction_objective_name(construction_objective) << "\","
            << "\"case_id\":\"" << escape_json(problem.case_id) << "\","
            << "\"status\":\"" << result.status << "\","
            << "\"q0_solver_status\":\""
            << escape_json(result.q0_solver_status) << "\","
            << "\"complete\":" << (unassigned == 0 ? "true" : "false") << ','
            << "\"unassigned\":" << unassigned << ','
            << "\"native_hard_violations\":" << result.native_hard_violations << ','
            << "\"native_score\":" << native_score << ','
            << "\"individual_score\":" << individual_score << ','
            << "\"wall_seconds\":" << result.wall_seconds << ','
            << "\"selected_incumbent\":\"" << result.selected_incumbent << "\","
            << "\"q0_score\":" << result.q0_score << ','
            << "\"group_construction_score\":" << result.group_construction_score << ','
            << "\"q1_selected_incumbent\":\""
            << escape_json(result.q1_selected_incumbent) << "\","
            << "\"q1_score\":" << result.q1_score << ','
            << "\"from_scratch_score\":" << result.from_scratch_score << ','
            << "\"from_scratch_complete\":"
            << (result.from_scratch_complete ? "true" : "false") << ','
            << "\"score_delta\":" << result.score_delta << ','
            << "\"component_deltas\":{"
            << "\"score_s\":" << result.selected_components.score_s - result.q0_components.score_s << ','
            << "\"score_v\":" << result.selected_components.score_v - result.q0_components.score_v << ','
            << "\"score_p\":" << result.selected_components.score_p - result.q0_components.score_p << ','
            << "\"score_c\":" << result.selected_components.score_c - result.q0_components.score_c << ','
            << "\"score_b\":" << result.selected_components.score_b - result.q0_components.score_b << "},"
            << "\"dfs_nodes\":" << result.dfs_nodes << ','
            << "\"beam_nodes\":" << result.beam_nodes << ','
            << "\"dfs_groups\":" << result.dfs_groups << ','
            << "\"beam_groups\":" << result.beam_groups << ','
            << "\"groups_improved\":" << result.groups_improved << ','
            << "\"from_scratch_dfs_nodes\":" << result.from_scratch_dfs_nodes << ','
            << "\"from_scratch_beam_nodes\":" << result.from_scratch_beam_nodes << ','
            << "\"from_scratch_dfs_groups\":" << result.from_scratch_dfs_groups << ','
            << "\"from_scratch_beam_groups\":" << result.from_scratch_beam_groups << ','
            << "\"recovery_attempts\":" << result.recovery_attempts << ','
            << "\"recovery_succeeded\":" << result.recovery_succeeded << ','
            << "\"rich_vnd_one_opt_moves\":" << result.rich_vnd_one_opt_moves << ','
            << "\"rich_vnd_two_swap_moves\":" << result.rich_vnd_two_swap_moves << ','
            << "\"rich_vnd_three_cycle_moves\":" << result.rich_vnd_three_cycle_moves << ','
            << "\"rich_vnd_group_rebuild_moves\":" << result.rich_vnd_group_rebuild_moves << ','
            << "\"rich_vnd_caregiver_rebuild_moves\":" << result.rich_vnd_caregiver_rebuild_moves << ','
            << "\"rich_vnd_score\":" << result.rich_vnd_score << ','
            << "\"rich_vnd_seconds\":" << result.rich_vnd_seconds << ','
            << "\"rich_repair_attempted\":" << result.rich_repair_attempted << ','
            << "\"rich_construction_assigned\":" << result.rich_construction_assigned << ','
            << "\"rich_construction_unassigned\":" << result.rich_construction_unassigned << ','
            << "\"rich_candidate_complete\":" << (result.rich_candidate_complete ? "true" : "false") << ','
            << "\"rich_dfs_attempted\":" << result.rich_dfs_attempted << ','
            << "\"rich_dfs_succeeded\":" << result.rich_dfs_succeeded << ','
            << "\"rich_dfs_nodes\":" << result.rich_dfs_nodes << ','
            << "\"rich_beam_groups\":" << result.rich_beam_groups << ','
            << "\"rich_paired_ssr_passes\":" << result.rich_paired_ssr_passes << ','
            << "\"rich_paired_rescue_attempted\":" << result.rich_paired_rescue_attempted << ','
            << "\"rich_paired_rescue_rescued\":" << result.rich_paired_rescue_rescued << ','
            << "\"rich_paired_rescue_unresolved\":" << result.rich_paired_rescue_unresolved << ','
            << "\"rich_paired_joint_rebuilds\":" << result.rich_paired_joint_rebuilds << ','
            << "\"rich_repair_score\":" << result.rich_repair_score << ','
            << "\"rich_m1_selected_score\":" << result.rich_m1_selected_score << ','
            << "\"rich_repair_repaired\":" << result.rich_repair_repaired << ','
            << "\"rich_repair_unresolved\":" << result.rich_repair_unresolved << ','
            << "\"rich_repair_nodes\":" << result.rich_repair_nodes << ','
            << "\"rich_pattern_count\":" << result.rich_pattern_count << ','
            << "\"rich_selected_pattern_count\":" << result.rich_selected_pattern_count << ','
            << "\"rich_pattern_score\":" << result.rich_pattern_score << ','
            << "\"rich_baby_pair_count\":" << result.rich_baby_pair_count << ','
            << "\"rich_master_score\":" << result.rich_master_score << ','
            << "\"rich_master_time_limit\":" << result.rich_master_time_limit << ','
            << "\"rich_master_attempts\":" << result.rich_master_attempts << ','
            << "\"rich_master_last_radius\":" << result.rich_master_last_radius << ','
            << "\"fallback_reason\":\"" << escape_json(result.fallback_reason) << "\","
            << "\"rich_business_time_limit\":" << result.rich_stage_budgets.business_time_limit << ','
            << "\"rich_scoring_reserve\":" << result.rich_stage_budgets.scoring_reserve << ','
            << "\"rich_search_deadline\":" << result.rich_search_deadline << ','
            << "\"rich_allocation_start\":" << result.rich_allocation_start << ','
            << "\"fallback_improvement_started\":" << result.fallback_improvement_started << ','
            << "\"fallback_improvement_finished\":" << result.fallback_improvement_finished << ','
            << "\"rich_stage_budgets\":{";
        bool first_budget = true;
        for (const auto& entry : result.rich_stage_budgets.stages) {
            if (!first_budget) *output << ',';
            first_budget = false;
            *output << '"' << entry.first << "\":" << entry.second;
        }
        *output << "},\"rich_stage_timing\":{";
        bool first_stage = true;
        for (const auto& entry : result.rich_stage_timing) {
            if (!first_stage) *output << ',';
            first_stage = false;
            const auto& timing = entry.second;
            *output << '"' << entry.first << "\":{\"base_budget\":" << timing.base_budget
                << ",\"effective_budget\":" << timing.effective_budget
                << ",\"started\":" << timing.started << ",\"finished\":" << timing.finished
                << ",\"deadline\":" << timing.deadline << ",\"carry\":" << timing.carry
                << ",\"pricing_reserve\":" << timing.pricing_reserve << '}';
        }
        *output << "},\"rich_m1_elite_store\":";
        full_cpp::write_rich_elite_store(*output, result.rich_m1_elite_store);
        *output << ",\"rich_elite_store\":";
        full_cpp::write_rich_elite_store(*output, result.rich_elite_store);
        *output << ",\"rich_structured_pattern_generation\":";
        full_cpp::write_rich_structured_diagnostics(*output, result.rich_structured);
        *output << ",\"rich_protected_multigroup_mip\":";
        full_cpp::write_rich_protected_mip_diagnostics(*output, result.rich_protected);
        *output << ",\"rich_special_dual_pricing\":";
        full_cpp::write_rich_special_pricing_diagnostics(*output, problem, result.rich_special_pricing);
        *output << ",\"rich_multigroup_lns\":";
        full_cpp::write_rich_lns_diagnostics(*output, result.rich_lns);
        *output << ",\"rich_restricted_pattern_mip\":";
        full_cpp::write_rich_restricted_diagnostics(*output, result.rich_restricted);
        *output << ",\"rich_conflict_diversity_active\":" << (result.rich_conflict_diversity_active ? "true" : "false");
        *output << ",\"rich_construction_repair_queue\":";
        full_cpp::write_rich_repair_queue(*output, result.rich_construction_repair_queue, 20);
        int extreme = 0;
        for (const auto& metric : result.rich_construction_repair_queue) extreme += metric.extreme_dispersion;
        *output << ",\"rich_extreme_dispersion_group_count\":" << extreme << ",\"assignments\":[";
        bool first = true;
        for (int passenger = 0;
             passenger < static_cast<int>(result.passenger_to_seat.size()); ++passenger) {
            const int seat = result.passenger_to_seat[passenger];
            if (seat < 0) continue;
            if (!first) *output << ',';
            first = false;
            const auto& item = problem.passengers[passenger];
            *output << "{\"groupId\":" << item.group_id
                    << ",\"hostnum\":" << item.hostnum
                    << ",\"seatId\":\"" << escape_json(problem.seats[seat].id) << "\"}";
        }
        *output << "]}\n";
        return result.native_hard_violations == 0 && unassigned == 0 ? 0 : 4;
}

// The outer frozen Python allocator solves homogeneous cabins in increasing
// target-seat count order; each call constructs its own topology and budgets.
int solve_cabins(std::ostream* output, const full_cpp::Problem& problem,
    const native_json::Value& config, const std::string& input_path,
    const std::string& config_path, double time_limit, int seed,
    full_cpp::ConstructionObjective objective, const std::map<std::string, std::set<int>>& groups) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - started).count(); };
    const auto& algorithm = config.at("algorithm");
    const auto setting = [&](const char* key, double fallback) {
        const auto* value = algorithm.find(key); return value ? value->number_or(fallback) : fallback;
    };
    const double total = std::min(time_limit, std::max(.1, setting("business_time_limit_seconds", 5.0)));
    const double minimum = std::min(total / groups.size(), std::max(.1, setting("cabin_min_time_seconds", 1.0)));
    const auto raw = native_json::parse_file(input_path);
    const auto& paths = config.at("input_contract").at("seatmaps_by_direction").at(problem.direction);
    const auto load_seats = [&](const char* key) {
        auto path = std::filesystem::path(paths.at(key).string);
        if (path.is_relative()) path = std::filesystem::path(config_path).parent_path() / path;
        return native_json::parse_file(path.string());
    };
    const auto new_map = load_seats("new"), old_map = load_seats("old");
    std::vector<std::pair<size_t, std::string>> cabins;
    size_t target_total = 0;
    for (const auto& cabin : groups) {
        const size_t count = std::count_if(problem.seats.begin(), problem.seats.end(),
            [&](const auto& seat) { return seat.cabin == cabin.first; });
        if (!count) throw std::runtime_error("target seatmap has no seats for cabin " + cabin.first);
        cabins.emplace_back(count, cabin.first); target_total += count;
    }
    std::sort(cabins.begin(), cabins.end());
    std::map<std::pair<int, int>, int> passenger_indexes;
    for (int p = 0; p < static_cast<int>(problem.passengers.size()); ++p)
        passenger_indexes[{problem.passengers[p].group_id, problem.passengers[p].hostnum}] = p;
    full_cpp::FeasibilityResult merged;
    merged.passenger_to_seat.assign(problem.passengers.size(), -1);
    merged.rich_candidate_complete = true;
    merged.selected_incumbent = "rich-cabin-decomposition";
    std::ostringstream diagnostics;
    diagnostics << std::setprecision(17) << "{\"enabled\":true,\"total_time_limit_seconds\":" << total
        << ",\"minimum_time_seconds\":" << minimum << ",\"solve_order\":[";
    for (size_t i = 0; i < cabins.size(); ++i) {
        if (i) diagnostics << ',';
        diagnostics << '"' << escape_json(cabins[i].second) << '"';
    }
    diagnostics << "],\"cabins\":{";
    for (size_t i = 0; i < cabins.size(); ++i) {
        const auto& cabin = cabins[i].second;
        auto cabin_config = config, cabin_raw = raw, cabin_new = new_map, cabin_old = old_map;
        auto& raw_groups = cabin_raw.object.at("groups").array;
        raw_groups.erase(std::remove_if(raw_groups.begin(), raw_groups.end(), [&](const auto& group) {
            return !groups.at(cabin).count(static_cast<int>(group.at("groupId").number));
        }), raw_groups.end());
        for (auto* seatmap : {&cabin_new, &cabin_old}) {
            auto& seats = seatmap->object.at("seats").array;
            seats.erase(std::remove_if(seats.begin(), seats.end(), [&](const auto& seat) {
                return seat.at("seatClass").string != cabin;
            }), seats.end());
        }
        const double budget_started = elapsed();
        const double remaining = std::max(.1, total - budget_started);
        const size_t future = cabins.size() - i - 1;
        const double budget = future == 0 ? remaining : std::min(
            std::max(minimum, total * cabins[i].first / target_total),
            std::max(.1, remaining - future * minimum));
        auto& limit = cabin_config.object.at("algorithm").object["business_time_limit_seconds"];
        limit.type = native_json::Value::Type::Number; limit.number = budget;
        const auto subproblem = full_cpp::make_problem(cabin_raw, cabin_config, cabin_new, cabin_old);
        const auto result = full_cpp::solve_feasibility_mip(subproblem, budget, seed, objective);
        for (size_t p = 0; p < subproblem.passengers.size(); ++p) {
            const int seat = result.passenger_to_seat[p];
            if (seat < 0) continue;
            const auto& passenger = subproblem.passengers[p];
            merged.passenger_to_seat[passenger_indexes.at({passenger.group_id, passenger.hostnum})]
                = problem.seat_index.at(subproblem.seats[seat].id);
        }
        merged.rich_candidate_complete &= result.rich_candidate_complete;
        merged.q0_score += result.q0_score;
        merged.rich_repair_score += result.rich_repair_score;
        merged.rich_vnd_score += result.rich_vnd_score;
        merged.rich_m1_selected_score += result.rich_m1_selected_score;
        merged.rich_master_score += result.rich_master_score;
        merged.rich_construction_assigned += result.rich_construction_assigned;
        merged.rich_construction_unassigned += result.rich_construction_unassigned;
        if (i) diagnostics << ',';
        diagnostics << '"' << escape_json(cabin) << "\":{\"time_budget_seconds\":" << budget
            << ",\"budget_started\":" << budget_started << ",\"finished\":" << elapsed()
            << ",\"traveler_count\":" << subproblem.passengers.size()
            << ",\"target_seats\":" << subproblem.seats.size() << ",\"result\":";
        write_result(&diagnostics, subproblem, result, objective);
        diagnostics << '}';
    }
    diagnostics << "}}";
    merged.wall_seconds = elapsed();
    merged.native_hard_violations = full_cpp::validate_complete_assignment(problem, merged.passenger_to_seat);
    merged.status = merged.native_hard_violations == 0 ? "HeuristicComplete" : "HeuristicFailed";
    // Keep per-cabin diagnostics intact. A merged timestamp or MIP status would
    // misrepresent independent solver calls, so do not manufacture one.
    double individual = 0.0;
    int unassigned = 0;
    for (int p = 0; p < static_cast<int>(problem.passengers.size()); ++p) {
        const int seat = merged.passenger_to_seat[p];
        if (seat < 0) ++unassigned;
        else individual += full_cpp::evaluate_individual_score(problem, p, seat).total();
    }
    *output << std::setprecision(17) << "{\"schema\":\"seat_protect_raw_native_v1\",\"mode\":\"RAW_NATIVE\","
        << "\"case_id\":\"" << escape_json(problem.case_id) << "\",\"construction_objective\":\""
        << full_cpp::construction_objective_name(objective) << "\",\"status\":\"" << merged.status
        << "\",\"complete\":" << (unassigned == 0 ? "true" : "false") << ",\"unassigned\":" << unassigned
        << ",\"native_hard_violations\":" << merged.native_hard_violations
        << ",\"native_score\":" << full_cpp::evaluate_soft_score(problem, merged.passenger_to_seat)
        << ",\"individual_score\":" << individual << ",\"wall_seconds\":" << merged.wall_seconds
        << ",\"selected_incumbent\":\"" << merged.selected_incumbent << "\",\"q0_score\":" << merged.q0_score
        << ",\"rich_candidate_complete\":" << (merged.rich_candidate_complete ? "true" : "false")
        << ",\"rich_repair_score\":" << merged.rich_repair_score << ",\"rich_vnd_score\":" << merged.rich_vnd_score
        << ",\"rich_m1_selected_score\":" << merged.rich_m1_selected_score << ",\"rich_master_score\":" << merged.rich_master_score
        << ",\"rich_construction_assigned\":" << merged.rich_construction_assigned
        << ",\"rich_construction_unassigned\":" << merged.rich_construction_unassigned
        << ",\"cabin_decomposition\":" << diagnostics.str() << ",\"assignments\":[";
    bool first = true;
    for (size_t p = 0; p < problem.passengers.size(); ++p) {
        const int seat = merged.passenger_to_seat[p]; if (seat < 0) continue;
        if (!first) *output << ','; first = false;
        *output << "{\"groupId\":" << problem.passengers[p].group_id << ",\"hostnum\":" << problem.passengers[p].hostnum
            << ",\"seatId\":\"" << escape_json(problem.seats[seat].id) << "\"}";
    }
    *output << "]}\n";
    return merged.native_hard_violations == 0 && unassigned == 0 ? 0 : 4;
}

}  // namespace

int main(int argc, char** argv) {
    std::string input_path;
    std::string config_path = "rich_python_reference_config.json";
    std::string output_path;
    double time_limit = 300.0;
    int seed = 0;
    full_cpp::ConstructionObjective construction_objective =
        full_cpp::ConstructionObjective::Feasibility;
    for (int index = 1; index < argc;) {
        const std::string option = argv[index++];
        if (option == "--input" && index < argc) input_path = argv[index++];
        else if (option == "--config" && index < argc) config_path = argv[index++];
        else if (option == "--output" && index < argc) output_path = argv[index++];
        else if (option == "--time-limit" && index < argc) time_limit = std::stod(argv[index++]);
        else if (option == "--seed" && index < argc) seed = std::stoi(argv[index++]);
        else if (option == "--construction-objective" && index < argc) {
            construction_objective = full_cpp::parse_construction_objective(argv[index++]);
        }
        else return 2;
    }
    if (input_path.empty() || time_limit <= 0.0) return 2;
    try {
        const full_cpp::Problem problem = full_cpp::load_problem(input_path, config_path);
        std::ofstream file;
        std::ostream* output = &std::cout;
        if (!output_path.empty()) {
            file.open(output_path, std::ios::binary);
            if (!file) return 2;
            output = &file;
        }
        if (construction_objective == full_cpp::ConstructionObjective::GroupFirst
            || construction_objective == full_cpp::ConstructionObjective::GroupSoft) {
            std::map<std::string, std::set<int>> cabins;
            for (const auto& group : problem.groups) {
                std::set<std::string> classes;
                for (int p : group.passengers) if (!problem.passengers[p].cabin.empty()) classes.insert(problem.passengers[p].cabin);
                if (classes.size() != 1) throw std::runtime_error("Rich group must have one booking cabin: " + std::to_string(group.id));
                cabins[*classes.begin()].insert(group.id);
            }
            if (cabins.empty()) throw std::runtime_error("no groups to allocate");
            const auto config = native_json::parse_file(config_path);
            const auto* algorithm = config.find("algorithm");
            const auto* enabled = algorithm ? algorithm->find("cabin_decomposition_enabled") : nullptr;
            if (cabins.size() > 1 && (!enabled || enabled->bool_or(true)))
                return solve_cabins(output, problem, config, input_path, config_path, time_limit, seed, construction_objective, cabins);
        }
        const auto result = full_cpp::solve_feasibility_mip(problem, time_limit, seed, construction_objective);
        return write_result(output, problem, result, construction_objective);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 3;
    }
}
