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
    double rich_construction_score = 0.0;
    double rich_repair_score = 0.0;
    bool rich_candidate_complete = false;
    int rich_construction_assigned = 0;
    int rich_construction_unassigned = 0;
    int rich_paired_ssr_passes = 0;
    int rich_paired_rescue_attempted = 0;
    int rich_paired_rescue_rescued = 0;
    int rich_paired_rescue_unresolved = 0;
    long long rich_dfs_nodes = 0;
    long long rich_beam_nodes = 0;
    int rich_dfs_attempted = 0;
    int rich_dfs_succeeded = 0;
    int rich_beam_groups = 0;
    int rich_repair_attempted = 0;
    int rich_repair_repaired = 0;
    int rich_repair_unresolved = 0;
    long long rich_repair_nodes = 0;
    double rich_construction_seconds = 0.0;
    double rich_repair_seconds = 0.0;
    double rich_construction_carry_seconds = 0.0;
    int rich_vnd_one_opt_moves = 0;
    int rich_vnd_two_swap_moves = 0;
    int rich_vnd_three_cycle_moves = 0;
    int rich_vnd_group_rebuild_moves = 0;
    double rich_vnd_score = 0.0;
    double rich_vnd_seconds = 0.0;
};

GroupConstructionResult construct_group_aware(
    const Problem& problem,
    const std::vector<int>& q0_assignment,
    std::chrono::steady_clock::time_point deadline
);

GroupConstructionResult construct_group_first(
    const Problem& problem,
    const std::vector<int>& q0_assignment,
    const GroupConstructionResult& q1_result,
    std::chrono::steady_clock::time_point deadline
);

GroupConstructionResult construct_rich_m1(
    const Problem& problem,
    const std::vector<int>& q0_assignment,
    const GroupConstructionResult& q2a_result,
    std::chrono::steady_clock::time_point global_deadline
);

void repair_rich_assignment(
    const Problem& problem, AssignmentState& state,
    const std::vector<std::vector<int>>& passenger_sorted_seats,
    std::chrono::steady_clock::time_point global_deadline,
    GroupConstructionResult& diagnostics
);

struct RichRemainingDiagnostics {
    int groups_considered = 0, dfs_attempted = 0, dfs_succeeded = 0;
    long long dfs_nodes = 0;
    int beam_groups = 0, transaction_failures = 0;
};

RichRemainingDiagnostics assign_rich_remaining(
    const Problem& problem, AssignmentState& state, const RichCandidateCache& cache,
    const std::vector<int>& group_indices,
    std::chrono::steady_clock::time_point global_deadline
);

int assign_rich_paired_ssrs(
    const Problem& problem, AssignmentState& state, const RichCandidateCache& cache,
    std::chrono::steady_clock::time_point deadline
);

void improve_rich_vnd_m2(
    const Problem& problem,
    std::vector<int>& assignment,
    std::chrono::steady_clock::time_point deadline,
    GroupConstructionResult& diagnostics
);

}  // namespace full_cpp
