#pragma once

#include <cstdint>
#include <chrono>
#include <iosfwd>
#include <string>
#include <utility>
#include <vector>

namespace native_solver {

enum class PatternSchedule {
    GroupMajor,
    RoundRobin,
};

struct BeamReserveConfig {
    bool enabled = false;
    int reserve_cap = 0;
    int per_bucket_cap = 0;
};

struct GroupResourcePressure {
    int group_id = -1;
    std::vector<double> seat_resources;
    std::vector<double> ssr_resources;
};

struct PatternGenerationScope {
    std::vector<int> group_ids;
    int pattern_limit_override = 0;
    int task_limit_per_group = 0;
    bool component_aware_ordering = false;
    bool diverse_complete_retention = false;
    std::vector<GroupResourcePressure> group_resource_pressure;
};

struct BabyPair {
    int infant_seat;
    int occupant_seat;
    double cost;
};

struct PatternRecord {
    uint64_t id;
    int group;
    double master_cost;
    bool hard_keep;
    std::vector<int> seat_resources;
    std::vector<std::pair<int, int>> ssr_all;
    std::vector<std::pair<int, int>> ssr_flagged;
    std::vector<int> infant_seats;
    std::vector<int> occupied_seats;
    std::vector<std::pair<int, int>> assignments;
    std::vector<std::pair<int, int>> blocked_by;
};

struct PatternDiagnostic {
    uint64_t id;
    int group;
    int group_id;
    double discovery_ms;
    double hint;
    int hint_index;
    int variant;
    int group_size;
    bool hard_keep;
    bool has_ssr;
    bool has_protection;
    bool retained_after_truncation;
    std::string source;
    std::string disposition;
};

struct GroupCoverageDiagnostic {
    int group;
    int group_id;
    double hint0_variant0_start_ms = -1.0;
    double hint0_variant0_complete_ms = -1.0;
};

struct TaskDiagnostic {
    int task_index;
    int group;
    int group_id;
    int group_size;
    bool has_ssr;
    bool has_protection;
    int hint_index;
    int variant;
    double start_ms;
    double end_ms;
    uint64_t beam_expansion_count;
    int produced_pattern_count;
    int new_unique_pattern_count;
    int duplicate_pattern_count;
    int retained_pattern_count = 0;
    bool admitted = true;
    bool completed;
    std::string termination_reason;
    int committed_pattern_count = 0;
    uint64_t pool_hash_after_task = 0;
    std::vector<uint64_t> produced_pattern_ids;
    std::vector<uint64_t> first_discovered_pattern_ids;
    uint64_t beam_prune_count = 0;
    int complete_state_count = 0;
    int legal_complete_pattern_count = 0;
    int unique_complete_resource_signatures = 0;
};

struct Pass0GroupDiagnostic {
    int group;
    int group_id;
    int group_size;
    bool has_ssr;
    bool has_protection;
    int pattern_count;
    double best_local_pattern_score;
    double incumbent_pattern_score;
    double best_vs_incumbent_local_gap;
    int resource_signature_count;
    double resource_signature_diversity;
};

struct TargetTraceSpec {
    uint64_t pattern_id;
    int group_id;
    std::vector<int> choices;
    int snapshot_hint_index = -1;
    int snapshot_variant = -1;
    int snapshot_depth = -1;
};

struct LayerStateSnapshot {
    uint64_t pattern_id;
    int group_id;
    int hint_index;
    int variant;
    int depth;
    int rank;
    int state_count;
    double individual_score;
    double rank_score;
    bool l1_valid;
    double l1_completion_individual_score;
    double l1_counterfactual_key;
    bool l2_valid;
    double l2_completion_individual_score;
    double l2_counterfactual_key;
    std::vector<int> choices;
};

struct TargetTraceStep {
    uint64_t pattern_id;
    int group_id;
    int hint_index;
    int variant;
    int depth;
    int total_depth;
    int target_option;
    int candidate_rank_before_cap;
    int candidate_count_before_cap;
    bool in_candidate_domain;
    bool after_candidate_cap;
    bool parent_prefix_in_beam;
    bool expanded;
    bool child_prefix_generated;
    int child_rank_before_beam_cap;
    int child_count_before_beam_cap;
    bool survived_beam;
    int beam_width;
    double child_rank_score;
    bool deadline_hit;
    double child_individual_score = 0.0;
    double cutoff_rank_score = 0.0;
    double cutoff_individual_score = 0.0;
    double median_rank_score = 0.0;
    double median_individual_score = 0.0;
    double top_decile_rank_score = 0.0;
    double top_decile_individual_score = 0.0;
    int cutoff_band_state_count = 0;
    int cutoff_band_unique_resource_signatures = 0;
    int cutoff_band_unique_row_signatures = 0;
    int cutoff_band_max_row_signature_multiplicity = 0;
    bool survived_main_beam = false;
    bool survived_reserve_beam = false;
};

struct MasterProblem {
    int group_count = 0;
    int seat_count = 0;
    int ssr_count = 0;
    bool full_load = false;
    std::vector<int> group_ids;
    std::vector<BabyPair> baby_pairs;
    std::vector<PatternRecord> patterns;
    std::vector<PatternDiagnostic> pattern_diagnostics;
    std::vector<GroupCoverageDiagnostic> group_coverage_diagnostics;
    std::vector<TaskDiagnostic> task_diagnostics;
    std::vector<Pass0GroupDiagnostic> pass0_group_diagnostics;
    std::vector<TargetTraceStep> target_trace_steps;
    std::vector<LayerStateSnapshot> layer_state_snapshots;
    double input_parse_ms = 0.0;
    double group_preprocess_ms = 0.0;
    double search_ms = 0.0;
    double pattern_materialization_ms = 0.0;
    bool rr_admission_guard_stop = false;
    double rr_task_admission_guard_ms = 0.0;
};

void write_master_problem(std::ostream& output, const MasterProblem& problem);

}  // namespace native_solver

int run_native_pattern_kernel(
    std::istream& input, std::ostream& output, std::ostream& diagnostics,
    bool emit_master, native_solver::MasterProblem* direct_master = nullptr,
    const std::chrono::steady_clock::time_point* generation_deadline = nullptr,
    bool* generation_deadline_hit = nullptr,
    const std::vector<uint64_t>* watched_pattern_ids = nullptr,
    native_solver::PatternSchedule schedule =
        native_solver::PatternSchedule::GroupMajor,
    bool collect_task_diagnostics = false,
    const std::vector<native_solver::TargetTraceSpec>* target_trace_specs = nullptr,
    const native_solver::BeamReserveConfig& beam_reserve = {},
    const native_solver::PatternGenerationScope& generation_scope = {},
    double rr_task_admission_guard_seconds = 0.0
);

namespace native_master {

struct MasterSolveOptions {
    bool canonicalize_patterns = true;
    std::vector<uint64_t> incumbent_pattern_ids;
};

int read_problem(
    std::istream& input, native_solver::MasterProblem& problem
);

int solve(
    const native_solver::MasterProblem& problem, std::ostream& output,
    double time_limit, bool skip_due_to_deadline = false,
    std::vector<uint64_t>* selected_pattern_ids = nullptr,
    const MasterSolveOptions* options = nullptr
);
int solve(std::istream& input, std::ostream& output, double time_limit);

}  // namespace native_master
