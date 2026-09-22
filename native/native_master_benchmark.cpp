#include "native_master_types.hpp"

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <random>
#include <sstream>
#include <string>

int main(int argc, char** argv) {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    const double time_limit = argc > 1 ? std::stod(argv[1]) : 0.2;
    const std::string input_order = argc > 2 ? argv[2] : "original";
    const uint64_t permutation_seed = argc > 3 ? std::stoull(argv[3]) : 0;
    const bool canonicalize = argc > 4 ? std::stoi(argv[4]) != 0 : true;

    native_solver::MasterProblem problem;
    if (native_master::read_problem(std::cin, problem) != 0) return 2;
    if (input_order == "reversed") {
        std::reverse(problem.patterns.begin(), problem.patterns.end());
    } else if (input_order == "random") {
        std::mt19937_64 random(permutation_seed);
        std::shuffle(problem.patterns.begin(), problem.patterns.end(), random);
    } else if (input_order != "original") {
        return 2;
    }

    native_master::MasterSolveOptions options;
    options.canonicalize_patterns = canonicalize;
    if (argc > 5 && std::string(argv[5]) != "-") {
        std::istringstream ids(argv[5]);
        std::string item;
        while (std::getline(ids, item, ',')) {
            if (!item.empty()) {
                options.incumbent_pattern_ids.push_back(std::stoull(item));
            }
        }
    }
    return native_master::solve(
        problem, std::cout, time_limit, false, nullptr, &options
    );
}
