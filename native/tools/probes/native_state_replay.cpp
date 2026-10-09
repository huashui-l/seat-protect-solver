#include "full_cpp_solver_core.hpp"

#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

namespace {

int integer(const native_json::Value& value, const std::string& key) {
    return static_cast<int>(value.at(key).number_or(-1));
}

void write_vector(std::ostream& output, const std::vector<int>& values) {
    output << '[';
    for (size_t index = 0; index < values.size(); ++index) {
        if (index) output << ',';
        output << values[index];
    }
    output << ']';
}

}  // namespace

int main(int argc, char** argv) {
    std::string input_path, config_path, operations_path;
    for (int index = 1; index < argc;) {
        const std::string option = argv[index++];
        if (option == "--input" && index < argc) input_path = argv[index++];
        else if (option == "--config" && index < argc) config_path = argv[index++];
        else if (option == "--operations" && index < argc) operations_path = argv[index++];
        else return 2;
    }
    if (input_path.empty() || config_path.empty() || operations_path.empty()) return 2;
    try {
        const full_cpp::Problem problem = full_cpp::load_problem(input_path, config_path);
        const native_json::Value operations = native_json::parse_file(operations_path);
        if (!operations.is_array()) throw std::runtime_error("operations must be an array");
        full_cpp::AssignmentState state(problem);
        std::optional<full_cpp::AssignmentSnapshot> saved;
        std::cout << "{\"operation_count\":" << operations.array.size()
                  << ",\"steps\":[";
        for (size_t index = 0; index < operations.array.size(); ++index) {
            const native_json::Value& operation = operations.array[index];
            const std::string name = operation.at("op").string_or();
            int result = -1;
            if (name == "query") {
                result = state.can_assign(
                    integer(operation, "passenger"), integer(operation, "seat")
                );
            } else if (name == "assign") {
                result = state.assign(
                    integer(operation, "passenger"), integer(operation, "seat")
                );
            } else if (name == "remove") {
                state.remove(integer(operation, "passenger"));
            } else if (name == "save") {
                saved = state.save();
            } else if (name == "restore") {
                if (saved) state.restore(*saved);
            } else {
                throw std::runtime_error("unknown operation: " + name);
            }
            if (index) std::cout << ',';
            std::cout << "{\"result\":" << result << ",\"passenger_to_seat\":";
            write_vector(std::cout, state.passenger_to_seat);
            std::cout << ",\"seat_to_passenger\":";
            write_vector(std::cout, state.seat_to_passenger);
            std::cout << ",\"blocked_count\":";
            write_vector(std::cout, state.blocked_count);
            std::cout << ",\"owner_group_by_seat\":";
            write_vector(std::cout, state.owner_group_by_seat);
            std::cout << ",\"seat_ssr_passenger\":";
            write_vector(std::cout, state.seat_ssr_passenger);
            std::cout << ",\"assignment_order\":";
            write_vector(std::cout, state.assignment_order);
            std::cout << '}';
        }
        std::cout << "]}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 3;
    }
}
