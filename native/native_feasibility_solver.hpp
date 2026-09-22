#pragma once

#include "full_cpp_solver_core.hpp"

#include <string>
#include <vector>

namespace full_cpp {

struct FeasibilityResult {
    std::string status;
    std::vector<int> passenger_to_seat;
    int native_hard_violations = 0;
    double wall_seconds = 0.0;
};

FeasibilityResult solve_feasibility_mip(
    const Problem& problem,
    double time_limit_seconds,
    int seed
);

int validate_complete_assignment(
    const Problem& problem,
    const std::vector<int>& passenger_to_seat
);

}  // namespace full_cpp
