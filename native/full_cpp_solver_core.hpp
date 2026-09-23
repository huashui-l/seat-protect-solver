#pragma once

#include "native_json.hpp"

#include <string>
#include <iosfwd>
#include <limits>
#include <unordered_map>
#include <vector>
#include <utility>
#include <tuple>

namespace full_cpp {

struct Seat {
    std::string id;
    std::string column;
    std::string cabin;
    int row = 0;
    int index_in_row = 0;
    int subrow = 0;
    double x = 0.0;
    double y = 0.0;
    bool window = false;
    bool aisle = false;
    bool exit_row = false;
    bool bassinet = false;
    bool extra_legroom = false;
    bool near_toilet = false;
    double explicit_value = std::numeric_limits<double>::quiet_NaN();
    std::vector<int> same_block_neighbors;
    std::vector<int> row_neighbors;
};

struct Passenger {
    int group = -1;
    int group_id = -1;
    int hostnum = -1;
    std::string cabin;
    std::string ssr;
    std::string old_seat;
    std::string fixed_seat;
    bool has_new_seat = false;
    double old_seat_value = std::numeric_limits<double>::quiet_NaN();
    bool has_near_toilet_preference = false;
    bool prefer_near_toilet = true;
    double near_toilet_preference_weight = 1.0;
    std::vector<std::pair<bool, double>> rich_toilet_preferences;
    bool need_cared = false;
    bool need_both_empty = false;
    bool need_single_empty = false;
    bool same_subrow_no_other_ssr = false;
    bool same_row_no_other_ssr = false;
};

struct Group {
    int id = -1;
    std::vector<int> passengers;
};

struct SsrRule {
    bool allow_exit_row = false;
    bool requires_caregiver = false;
    bool caregiver_allow_cross_aisle = false;
    bool requires_aisle = false;
    bool requires_bassinet = false;
};

struct RichConstructionConfig {
    int elite_patterns_per_group = 12;
    int structured_pattern_min_group_size = 5;
    int structured_rigid_shift_rows = 3;
    int structured_research_min_group_size = 2;
    int structured_pattern_extra_rows = 2;
    int structured_pattern_window_limit = 12;
    double stage3_time_budget = 20.0;
    bool enable_restricted_pattern_mip = true;
    double restricted_pattern_mip_time_budget = 0.0;
    double restricted_pattern_mip_tail_budget = 0.10;
    int restricted_pattern_mip_attempts = 3;
    bool enable_pattern_local_branching = true;
    int pattern_local_branching_initial_radius = 4;
    int pattern_local_branching_radius_growth = 2;
    int pattern_local_branching_max_radius = std::numeric_limits<int>::max();
    int candidate_cap = 48;
    int candidate_cap_retry = 96;
    int candidate_cap_full_retry = 192;
    int beam_width_small = 24;
    int beam_moves_small = 20;
    int beam_width_medium = 40;
    int beam_moves_medium = 20;
    int beam_width_large = 96;
    int beam_moves_large = 28;
    bool small_group_dfs_enabled = true;
    int small_group_dfs_max_size = 4;
    long long small_group_dfs_node_limit = 20000;
    double small_group_dfs_time_limit = 0.02;
    double old_seat_reservation_pressure = 1.0;
    int paired_rescue_option_cap = 12;
    int paired_joint_rebuild_candidate_cap = 80;
    long long paired_joint_rebuild_node_limit = 50000;
    int final_repair_node_limit = 20000;
    double final_repair_time_limit = 2.0;
    int local_search_candidate_cap = 32;
    double local_search_epsilon = 1e-9;
    int local_search_cycle_candidate_cap = 12;
    int local_search_related_candidate_cap = 64;
    int local_search_related_group_cap = 8;
};

struct RichStageBudgets {
    double business_time_limit = 0.0;
    double scoring_reserve = 0.0;
    double usable_time = 0.0;
    int seat_demand = 0;
    bool post_protected_tail_reserve_active = false;
    bool post_protected_special_pricing_active = false;
    std::map<std::string, double> stages;
};

struct Problem {
    std::string case_id;
    std::string direction;
    double seat_spacing = 1.0;
    double aisle_gap = 0.8;
    double row_spacing = 1.5;
    std::vector<Seat> seats;
    std::vector<Seat> old_seats;
    std::vector<Passenger> passengers;
    std::vector<Group> groups;
    std::unordered_map<std::string, int> seat_index;
    std::unordered_map<std::string, int> old_seat_index;
    std::unordered_map<std::string, SsrRule> ssr_rules;
    bool both_side_empty_allow_cross_aisle = false;
    bool require_two_real_neighbors = true;
    double weight_s = -1.0;
    double weight_v = -0.5;
    double weight_p = -0.8;
    double weight_c = -2.0;
    double weight_b = -0.3;
    double weight_t = -0.8;
    double business_seat_value = 50.0;
    double extra_legroom_value = 20.0;
    double bassinet_value = 10.0;
    bool prioritize_front = true;
    double front_penalty_reduction = 0.1;
    double back_penalty_factor = 0.2;
    double group_centroid_x_factor = 1.0;
    double group_centroid_y_factor = 1.0;
    double baby_front_back_factor = 1.0;
    RichConstructionConfig rich;
    RichStageBudgets rich_stage_budgets;
    bool rich_quality_repair_active = false;
    bool rich_conflict_diversity_time_active = false;
    bool rich_three_tier_active = false;
    bool rich_structured_small_groups_active = false;
    bool rich_full_resource_global_blocks = false;
};

struct RichGroupRepairMetric {
    int group_id = 0, size = 0, assigned = 0, row_span = 0, row_count = 0;
    double compactness_penalty = 0.0, compactness_score = 0.0;
    double value_mismatch_score = 0.0, preference_mismatch_score = 0.0;
    double priority_loss = 0.0;
    bool extreme_dispersion = false;
};

std::vector<RichGroupRepairMetric> build_rich_repair_queue(
    const Problem& problem, const std::vector<int>& assignment);
void write_rich_repair_queue(std::ostream& output,
    const std::vector<RichGroupRepairMetric>& queue, size_t limit);
bool rich_conflict_diversity_active(const Problem& problem,
    const std::vector<RichGroupRepairMetric>& construction_metrics);

struct RichStructuredOrder {
    int min_group_size = 0;
    bool full_resource_global_blocks = false;
    std::vector<int> ordered_groups, difficult_groups;
    std::vector<RichGroupRepairMetric> repair_queue;
};

RichStructuredOrder build_rich_structured_order(const Problem& problem, const std::vector<int>& assignment);

struct FixedSeatContext {
    std::vector<int> owner_by_seat;
    std::vector<bool> deterministic_blocked;
};

struct RichSsrLocation {
    int row = 0;
    int subrow = -1;  // -1 denotes the whole row.
    bool operator<(const RichSsrLocation& other) const {
        return std::make_tuple(subrow >= 0, row, subrow) < std::make_tuple(other.subrow >= 0, other.row, other.subrow);
    }
};

struct RichSsrResource {
    RichSsrLocation location;
    std::string ssr;
    bool operator<(const RichSsrResource& other) const {
        return std::tie(location, ssr) < std::tie(other.location, other.ssr);
    }
};

struct RichPlacement {
    int passenger_index = -1;  // Within the group, matching Python Placement.
    int passenger = -1;
    int seat = -1;
    std::vector<int> blocked, resources;
    double individual_cost = 0.0;
    std::vector<RichSsrResource> ssr_resources;
    std::vector<RichSsrLocation> ssr_flag_locations;
    bool is_infant = false;
};

std::vector<std::vector<RichPlacement>> build_rich_placement_options(
    const Problem& problem, int group_index, const FixedSeatContext& fixed);

struct RichExactPattern {
    int group_id = -1;
    std::vector<RichPlacement> placements;
    std::vector<std::pair<int, int>> assignments;  // global passenger, seat; sorted by hostnum.
    std::vector<std::pair<int, int>> blocked_by;   // seat, global passenger; sorted by seat ID/hostnum.
    std::vector<int> seat_resources, infant_seats, occupied_seats;
    std::map<RichSsrResource, int> ssr_all, ssr_flagged;
    double master_cost = 0.0;
};

bool rich_placements_caregiver_ok(const Problem& problem, int group_index,
    const std::vector<RichPlacement>& placements);
RichExactPattern build_rich_exact_pattern(const Problem& problem, int group_index,
    const std::vector<RichPlacement>& placements, const std::vector<std::string>& active_ssr_types);

struct RichTieredPattern {
    RichExactPattern pattern;
    std::string source;
};

std::vector<RichTieredPattern> generate_rich_rigid_relaxed_patterns(
    const Problem& problem, int group_index, const std::vector<int>& current_targets,
    const std::vector<std::vector<RichPlacement>>& options, const std::vector<std::string>& active_ssr_types);

struct RichStructuredWindows {
    int minimum_width = 0;
    double old_center = 0.0;
    std::vector<std::vector<int>> all_row_windows, row_windows;
};

RichStructuredWindows build_rich_structured_windows(const Problem& problem, int group_index,
    const std::vector<std::vector<RichPlacement>>& options, const RichGroupRepairMetric& current_metric);

RichStageBudgets calculate_rich_stage_budgets(
    const Problem& problem, const native_json::Value& algorithm
);

struct RichStageWindow {
    double effective_budget = 0.0;
    double deadline = 0.0;
};

struct RichStageTiming {
    double base_budget = 0.0, effective_budget = 0.0;
    double started = 0.0, finished = 0.0, deadline = 0.0;
    double carry = 0.0, pricing_reserve = 0.0;
};

// Times use one monotonic clock origin, supplied by the caller for replay tests.
class RichStageSchedule {
public:
    RichStageSchedule(const RichStageBudgets& budgets, double allocation_start,
                      double restricted_tail_budget,
                      double search_deadline_limit = std::numeric_limits<double>::infinity());
    RichStageWindow begin(const std::string& stage, double now,
                          int construction_unassigned = 0);
    void finish(const std::string& stage, const RichStageWindow& window, double now);
    double carry() const { return carry_; }
    double pricing_reserve() const { return pricing_reserve_; }
private:
    RichStageBudgets budgets_;
    double allocation_start_, search_deadline_, restricted_tail_budget_;
    double carry_ = 0.0, pricing_reserve_ = 0.0, lns_deadline_ = 0.0;
};

struct AssignmentSnapshot {
    std::vector<int> seat_to_passenger;
    std::vector<int> passenger_to_seat;
    std::vector<int> blocked_count;
    std::vector<std::vector<int>> assigned_blocked;
    std::vector<int> owner_group_by_seat;
    std::vector<int> seat_ssr_passenger;
    std::vector<int> assignment_order;
};

class AssignmentState {
public:
    explicit AssignmentState(
        const Problem& problem,
        const FixedSeatContext* fixed = nullptr
    );

    bool can_assign(int passenger, int seat, int chosen_block = -1, int excluded_seat = -1) const;
    // Python is_seat_feasible: does not enforce fixed-seat identity or require
    // the passenger to be unassigned; used for construction owner regret.
    bool rich_seat_feasible(int passenger, int seat, int chosen_block = -1, int excluded_seat = -1) const;
    bool assign(int passenger, int seat, int chosen_block = -1, int excluded_seat = -1);
    void remove(int passenger);
    AssignmentSnapshot save() const;
    void restore(AssignmentSnapshot snapshot);

    const Problem& problem;
    std::vector<int> seat_to_passenger;
    std::vector<int> passenger_to_seat;
    std::vector<int> blocked_count;
    std::vector<std::vector<int>> assigned_blocked;
    std::vector<int> owner_group_by_seat;
    std::vector<int> seat_ssr_passenger;
    std::vector<int> assignment_order;
};

struct RichCandidateCache {
    std::vector<double> owner_regrets;
    std::vector<std::vector<double>> costs;
    std::vector<std::vector<int>> rankings;
};

RichCandidateCache build_rich_candidate_cache(const Problem& problem, const AssignmentState& state,
    const std::vector<double>* frozen_owner_regrets = nullptr);

Problem load_problem(
    const std::string& case_path,
    const std::string& config_path
);

FixedSeatContext preprocess_fixed_seats(const Problem& problem);

// (occupied seat, chosen single-empty seat or -1); both-empty resources
// follow the problem topology. This is a static domain, not mutable legality.
std::vector<std::pair<int, int>> rich_placement_domain(
    const Problem& problem, const FixedSeatContext& fixed, int passenger
);

struct IndividualScoreComponents {
    double score_s = 0.0;
    double score_v = 0.0;
    double score_p = 0.0;

    double total() const { return score_s + score_v + score_p; }
};

struct ScoreComponents {
    double score_s = 0.0;
    double score_v = 0.0;
    double score_p = 0.0;
    double score_c = 0.0;
    double score_b = 0.0;

    double total() const {
        return score_s + score_v + score_p + score_c + score_b;
    }
};

IndividualScoreComponents evaluate_individual_score(
    const Problem& problem,
    int passenger_index,
    int seat_index
);

ScoreComponents evaluate_score_components(
    const Problem& problem,
    const std::vector<int>& passenger_to_seat
);

ScoreComponents evaluate_rich_group_score(const Problem& problem, const std::vector<int>& assignment, int group_index);

double evaluate_soft_score(
    const Problem& problem,
    const std::vector<int>& passenger_to_seat
);

struct RichElitePattern {
    std::vector<std::pair<int, std::string>> assignments;
    std::vector<std::pair<int, std::vector<std::string>>> blocked_by_host;
    std::vector<std::string> occupied_seats, blocked_seats, seat_resources;
    std::vector<int> conflict_groups;
    double local_score = 0.0;
    std::string source;
    bool pinned = false;
};

class RichEliteStore {
public:
    explicit RichEliteStore(int limit = 12) : limit_(limit < 2 ? 2 : limit) {}
    void capture(const AssignmentState& state, const std::string& source);
    void record(int group_id, RichElitePattern pattern,
                const std::map<std::string, int>& owner_by_resource, bool conflict_diversity_active);
    void record_candidate(int group_id, RichElitePattern pattern,
                          const AssignmentState& state, bool conflict_diversity_active);
    const std::map<int, std::vector<RichElitePattern>>& groups() const { return groups_; }
private:
    int limit_;
    std::map<int, std::vector<RichElitePattern>> groups_;
};

void write_rich_elite_store(std::ostream& output, const RichEliteStore& store);

}  // namespace full_cpp
