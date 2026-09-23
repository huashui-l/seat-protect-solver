#pragma once

#include "full_cpp_solver_core.hpp"

#include <string>
#include <vector>

namespace full_cpp {

struct GroupConstructionResult;

std::map<int, std::vector<int>> solve_rich_lns_master(const AssignmentState& state,
    const RichLnsWorkspace& workspace, const std::vector<int>& component,
    const std::map<int, std::vector<RichLnsOption>>& options, int root,
    double local_time_limit, std::chrono::steady_clock::time_point deadline);

struct RichProtectedAcceptedComponent {
    std::vector<int> groups;
    double delta = 0.0, elapsed_seconds = 0.0;
    int test_index = 0;
};
struct RichProtectedMipDiagnostics {
    bool enabled = false, protected_roots_enabled = false, priority_roots_enabled = false;
    bool stopped_by_deadline = false, roots_initialized = false;
    int protected_root_count = 0, priority_root_count = 0;
    int roots_considered = 0, components_tested = 0, mip_columns = 0;
    int passes = 0;
    int dynamic_relocation_calls = 0, dynamic_relocation_patterns = 0, conditional_ssr_rows = 0, accepted = 0;
    double score_improvement = 0.0, seconds = 0.0;
    std::string reason;
    std::vector<std::vector<int>> tested_components;
    std::vector<RichProtectedAcceptedComponent> accepted_components;
};
RichProtectedMipDiagnostics improve_rich_protected_mip(const Problem& problem,
    AssignmentState& state, RichEliteStore& elite, std::chrono::steady_clock::time_point deadline);
void write_rich_protected_mip_diagnostics(std::ostream& output, const RichProtectedMipDiagnostics& diagnostics);

struct RichSpecialPricingDiagnostics {
    bool enabled = false;
    std::string lp_status = "disabled";
    int lp_columns = 0, groups_attempted = 0, negative_patterns = 0;
    long long dfs_nodes = 0;
    double seconds = 0.0;
    std::vector<std::pair<RichExactPattern, double>> accepted_patterns;
};

RichSpecialPricingDiagnostics generate_rich_special_dual_patterns(
    const Problem& problem, const AssignmentState& state, const RichEliteStore& elite,
    std::chrono::steady_clock::time_point deadline, bool enabled,
    const std::function<void(int, const RichExactPattern&, double)>& recorder);

void write_rich_special_pricing_diagnostics(std::ostream& output,
    const Problem& problem, const RichSpecialPricingDiagnostics& diagnostics);

RichProtectedMipDiagnostics improve_rich_protected_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, GroupConstructionResult& result);
RichSpecialPricingDiagnostics generate_rich_special_pricing_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, bool enabled, GroupConstructionResult& result);

enum class ConstructionObjective {
    Feasibility,
    IndividualSoft,
    GroupSoft,
    GroupFirst,
};

ConstructionObjective parse_construction_objective(const std::string& value);
const char* construction_objective_name(ConstructionObjective objective);

struct FeasibilityResult {
    RichEliteStore rich_m1_elite_store;
    RichEliteStore rich_elite_store;
    RichStructuredDiagnostics rich_structured;
    RichProtectedMipDiagnostics rich_protected;
    RichSpecialPricingDiagnostics rich_special_pricing;
    bool rich_conflict_diversity_active = false;
    RichStageBudgets rich_stage_budgets;
    std::map<std::string, RichStageTiming> rich_stage_timing;
    double rich_search_deadline = 0.0;
    double rich_m1_selected_score = 0.0;
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
    int rich_vnd_caregiver_rebuild_moves = 0;
    double rich_vnd_score = 0.0;
    double rich_vnd_seconds = 0.0;
    int rich_repair_attempted = 0;
    int rich_construction_assigned = 0;
    int rich_construction_unassigned = 0;
    bool rich_candidate_complete = false;
    int rich_dfs_attempted = 0, rich_dfs_succeeded = 0, rich_beam_groups = 0;
    int rich_paired_ssr_passes = 0;
    int rich_paired_rescue_attempted = 0, rich_paired_rescue_rescued = 0, rich_paired_rescue_unresolved = 0;
    int rich_paired_joint_rebuilds = 0;
    long long rich_dfs_nodes = 0;
    double rich_repair_score = 0.0;
    std::vector<RichGroupRepairMetric> rich_construction_repair_queue;
    int rich_repair_repaired = 0;
    int rich_repair_unresolved = 0;
    long long rich_repair_nodes = 0;
    int rich_pattern_count = 0;
    int rich_selected_pattern_count = 0;
    double rich_pattern_score = 0.0;
    int rich_baby_pair_count = 0;
    double rich_master_score = 0.0;
    double rich_master_time_limit = 0.0;
    int rich_master_attempts = 0;
    int rich_master_last_radius = -1;
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
