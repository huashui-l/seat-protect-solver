#pragma once

#include "full_cpp_solver_core.hpp"

#include <string>
#include <vector>

namespace full_cpp {

enum class ConstructionObjective {
    Feasibility,
    IndividualSoft,
    GroupSoft,
    GroupFirst,
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
    std::string q1_selected_incumbent;
    double q1_score = 0.0;
    double from_scratch_score = 0.0;
    bool from_scratch_complete = false;
    long long from_scratch_dfs_nodes = 0;
    long long from_scratch_beam_nodes = 0;
    int from_scratch_dfs_groups = 0;
    int from_scratch_beam_groups = 0;
    int recovery_attempts = 0;
    int recovery_succeeded = 0;
    int rich_vnd_one_opt_moves = 0;
    int rich_vnd_two_swap_moves = 0;
    int rich_vnd_three_cycle_moves = 0;
    int rich_vnd_group_rebuild_moves = 0;
    double rich_vnd_score = 0.0;
    double rich_vnd_seconds = 0.0;
    int rich_repair_attempted = 0;
    int rich_repair_repaired = 0;
    int rich_repair_unresolved = 0;
    long long rich_repair_nodes = 0;
    int rich_pattern_count = 0;
    int rich_selected_pattern_count = 0;
    double rich_pattern_score = 0.0;
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
