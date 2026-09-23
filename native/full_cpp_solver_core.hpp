#pragma once

#include "native_json.hpp"

#include <string>
#include <limits>
#include <unordered_map>
#include <vector>

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
    double old_seat_value = std::numeric_limits<double>::quiet_NaN();
    bool has_near_toilet_preference = false;
    bool prefer_near_toilet = true;
    double near_toilet_preference_weight = 1.0;
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
    double construction_time_budget = 16.51276595744681;
    double repair_time_budget = 3.175531914893617;
    double scoring_time_reserve = 0.25;
    double stage3_time_budget = 20.0;
    int candidate_cap = 48;
    int candidate_cap_retry = 64;
    int candidate_cap_full_retry = 128;
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
    double vnd_time_budget = 6.351063829787234;
    int local_search_candidate_cap = 32;
    int local_search_cycle_candidate_cap = 12;
    int local_search_related_candidate_cap = 64;
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
};

struct FixedSeatContext {
    std::vector<int> owner_by_seat;
    std::vector<bool> deterministic_blocked;
};

struct AssignmentSnapshot {
    std::vector<int> seat_to_passenger;
    std::vector<int> passenger_to_seat;
    std::vector<int> blocked_count;
    std::vector<std::vector<int>> assigned_blocked;
    std::vector<int> owner_group_by_seat;
    std::vector<int> seat_ssr_passenger;
};

class AssignmentState {
public:
    explicit AssignmentState(
        const Problem& problem,
        const FixedSeatContext* fixed = nullptr
    );

    bool can_assign(int passenger, int seat, int chosen_block = -1) const;
    bool assign(int passenger, int seat, int chosen_block = -1);
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
};

Problem load_problem(
    const std::string& case_path,
    const std::string& config_path
);

FixedSeatContext preprocess_fixed_seats(const Problem& problem);

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

double evaluate_soft_score(
    const Problem& problem,
    const std::vector<int>& passenger_to_seat
);

}  // namespace full_cpp
