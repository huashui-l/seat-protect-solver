#pragma once

#include "full_cpp_solver_core.hpp"

#include <chrono>
#include <string>
#include <vector>

namespace full_cpp {

struct GroupConstructionResult {
    std::vector<int> passenger_to_seat;
    std::string selected_incumbent = "q0";
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

GroupConstructionResult construct_group_aware(
    const Problem& problem,
    const std::vector<int>& q0_assignment,
    std::chrono::steady_clock::time_point deadline
);

}  // namespace full_cpp
