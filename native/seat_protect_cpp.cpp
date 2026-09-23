#include "full_cpp_solver_core.hpp"
#include "native_feasibility_solver.hpp"

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
        const full_cpp::FeasibilityResult result = full_cpp::solve_feasibility_mip(
            problem, time_limit, seed, construction_objective
        );
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
        std::ofstream file;
        std::ostream* output = &std::cout;
        if (!output_path.empty()) {
            file.open(output_path, std::ios::binary);
            if (!file) return 2;
            output = &file;
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
            << "\"rich_vnd_score\":" << result.rich_vnd_score << ','
            << "\"rich_vnd_seconds\":" << result.rich_vnd_seconds << ','
            << "\"rich_repair_attempted\":" << result.rich_repair_attempted << ','
            << "\"rich_repair_repaired\":" << result.rich_repair_repaired << ','
            << "\"rich_repair_unresolved\":" << result.rich_repair_unresolved << ','
            << "\"rich_repair_nodes\":" << result.rich_repair_nodes << ','
            << "\"fallback_reason\":\"" << escape_json(result.fallback_reason) << "\","
            << "\"assignments\":[";
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
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 3;
    }
}
