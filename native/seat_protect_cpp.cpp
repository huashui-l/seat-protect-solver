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
            << "\"complete\":" << (unassigned == 0 ? "true" : "false") << ','
            << "\"unassigned\":" << unassigned << ','
            << "\"native_hard_violations\":" << result.native_hard_violations << ','
            << "\"native_score\":" << native_score << ','
            << "\"individual_score\":" << individual_score << ','
            << "\"wall_seconds\":" << result.wall_seconds << ','
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
