#pragma once

#include "full_cpp_solver_core.hpp"

#include <string>
#include <vector>

namespace full_cpp {

enum class ConstructionObjective {
    Feasibility,
    IndividualSoft,
    GroupSoft,
};

ConstructionObjective parse_construction_objective(const std::string& value);
const char* construction_objective_name(ConstructionObjective objective);

struct FeasibilityResult {
    std::string status;
    std::string q0_solver_status;
    std::vector<int> passenger_to_seat;
    int native_hard_violations = 0;
    double wall_seconds = 0.0;
    std::string selected_incumbent;
    std::string fallback_reason;
    double q0_score = 0.0;
    double group_construction_score = 0.0;
    double score_delta = 0.0;
    ScoreComponents q0_components;
    ScoreComponents selected_components;
    long long dfs_nodes = 0;
    long long beam_nodes = 0;
    int dfs_groups = 0;
    int beam_groups = 0;
    int groups_improved = 0;
};

FeasibilityResult solve_feasibility_mip(
    const Problem& problem,
    double time_limit_seconds,
    int seed,
    ConstructionObjective objective = ConstructionObjective::Feasibility
);

int validate_complete_assignment(
    const Problem& problem,
    const std::vector<int>& passenger_to_seat
);

}  // namespace full_cpp
