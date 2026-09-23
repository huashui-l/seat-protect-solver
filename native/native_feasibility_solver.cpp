#include "native_feasibility_solver.hpp"

#include "native_rich_pattern_adapter.hpp"
#include "native_group_constructor.hpp"

#include "interfaces/highs_c_api.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace full_cpp {
namespace {

constexpr double kInfinity = 1e30;

struct Model {
    std::vector<double> cost;
    std::vector<double> lower;
    std::vector<double> upper;
    std::vector<HighsInt> integrality;
    std::vector<std::map<int, double>> columns;
    std::vector<double> row_lower;
    std::vector<double> row_upper;

    int add_binary(double objective = 0.0) {
        const int index = static_cast<int>(columns.size());
        cost.push_back(objective);
        lower.push_back(0.0);
        upper.push_back(1.0);
        integrality.push_back(kHighsVarTypeInteger);
        columns.emplace_back();
        return index;
    }

    int add_row(double minimum, double maximum) {
        const int index = static_cast<int>(row_lower.size());
        row_lower.push_back(minimum);
        row_upper.push_back(maximum);
        return index;
    }

    void add(int column, int row, double value) {
        columns[column][row] += value;
    }
};

SsrRule effective_rule(const Problem& problem, const Passenger& passenger) {
    if (passenger.ssr.empty()) {
        SsrRule rule;
        rule.allow_exit_row = true;
        return rule;
    }
    const auto found = problem.ssr_rules.find(passenger.ssr);
    return found == problem.ssr_rules.end() ? SsrRule{} : found->second;
}

bool adult_caregiver(const Passenger& passenger) {
    return passenger.ssr.empty() && !passenger.need_cared
        && !passenger.need_both_empty && !passenger.need_single_empty;
}

bool static_eligible(const Problem& problem, int passenger_index, int seat_index) {
    const Passenger& passenger = problem.passengers[passenger_index];
    const Seat& seat = problem.seats[seat_index];
    if (!passenger.fixed_seat.empty() && passenger.fixed_seat != seat.id) return false;
    if (!passenger.cabin.empty() && passenger.cabin != seat.cabin) return false;
    const SsrRule rule = effective_rule(problem, passenger);
    if (seat.exit_row && !rule.allow_exit_row) return false;
    if (rule.requires_bassinet && !seat.bassinet) return false;
    if (rule.requires_aisle && !seat.aisle) return false;
    if (passenger.need_both_empty) {
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        if (neighbors.empty()) return false;
        if (problem.require_two_real_neighbors && neighbors.size() != 2) return false;
    }
    if (passenger.need_single_empty && seat.same_block_neighbors.empty()) return false;
    return true;
}

using Location = std::tuple<std::string, int, int>;  // cabin, row, subrow (-1 for row)

Location location_of(const Seat& seat, bool by_row) {
    return {seat.cabin, seat.row, by_row ? -1 : seat.subrow};
}

bool same_location(const Seat& left, const Seat& right, bool by_row) {
    return left.cabin == right.cabin && left.row == right.row
        && (by_row || left.subrow == right.subrow);
}

}  // namespace

ConstructionObjective parse_construction_objective(const std::string& value) {
    if (value == "feasibility") return ConstructionObjective::Feasibility;
    if (value == "individual-soft") return ConstructionObjective::IndividualSoft;
    if (value == "group-soft") return ConstructionObjective::GroupSoft;
    if (value == "group-first") return ConstructionObjective::GroupFirst;
    throw std::runtime_error("unknown construction objective: " + value);
}

const char* construction_objective_name(ConstructionObjective objective) {
    if (objective == ConstructionObjective::IndividualSoft) return "individual-soft";
    if (objective == ConstructionObjective::GroupSoft) return "group-soft";
    if (objective == ConstructionObjective::GroupFirst) return "group-first";
    return "feasibility";
}

FeasibilityResult solve_feasibility_mip(
    const Problem& problem, double time_limit_seconds, int seed,
    ConstructionObjective objective
) {
    const auto started = std::chrono::steady_clock::now();
    if (objective == ConstructionObjective::GroupSoft
        || objective == ConstructionObjective::GroupFirst) {
        using Clock = std::chrono::steady_clock;
        const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - started).count(); };
        const auto at = [&](double seconds) { return started
            + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)); };
        const auto& budgets = problem.rich_stage_budgets;
        // The CLI is an additional outer limit; the frozen configuration still
        // determines stage budgets. Charge fallback work to this same origin.
        const double search_seconds = std::min(budgets.usable_time,
            time_limit_seconds - std::min(budgets.scoring_reserve, time_limit_seconds * 0.2));
        const auto search_deadline = at(search_seconds);
        RichStageSchedule schedule(budgets, 0.0,
            problem.rich.restricted_pattern_mip_tail_budget, search_seconds);
        FeasibilityResult result = solve_feasibility_mip(
            problem, std::max(0.0, search_seconds - elapsed()), seed, ConstructionObjective::IndividualSoft
        );
        result.rich_stage_budgets = budgets;
        result.rich_search_deadline = search_seconds;
        result.q0_solver_status = result.status;
        if (result.native_hard_violations == 0) {
            GroupConstructionResult group_result = construct_group_aware(
                problem, result.passenger_to_seat,
                search_deadline
            );
            if (objective == ConstructionObjective::GroupFirst) {
                group_result = construct_group_first(
                    problem, result.passenger_to_seat, group_result,
                    search_deadline
                );
            }
            group_result = construct_rich_m1(
                problem, result.passenger_to_seat, group_result, started, schedule
            );
            result.rich_m1_elite_store = group_result.rich_elite_store;
            result.rich_m1_selected_score = group_result.group_construction_score;
            // A failed construction/repair candidate leaves the legal fallback
            // selected. Never confuse candidate completeness with that incumbent.
            if (validate_complete_assignment(problem, group_result.passenger_to_seat) == 0) {
                const double vnd_started = elapsed();
                const auto vnd_window = schedule.begin("vnd", vnd_started);
                improve_rich_vnd_m2(
                    problem, at(vnd_window.deadline), group_result
                );
                const double vnd_finished = elapsed();
                schedule.finish("vnd", vnd_window, vnd_finished);
                group_result.rich_stage_timing["vnd"] = {
                    budgets.stages.at("vnd"), vnd_window.effective_budget,
                    vnd_started, vnd_finished, vnd_window.deadline,
                    schedule.carry(), schedule.pricing_reserve()};
                if (group_result.rich_candidate_complete) {
                    const double pattern_started = elapsed();
                    const auto pattern_window = schedule.begin("pattern_generation", pattern_started);
                    generate_rich_patterns_m3(problem, at(pattern_window.deadline), group_result);
                    const double pattern_finished = elapsed();
                    schedule.finish("pattern_generation", pattern_window, pattern_finished);
                    group_result.rich_stage_timing["pattern_generation"] = {
                        budgets.stages.at("pattern_generation"), pattern_window.effective_budget,
                        pattern_started, pattern_finished, pattern_window.deadline,
                        schedule.carry(), schedule.pricing_reserve()};
                    const double protected_started = elapsed();
                    const auto protected_window = schedule.begin("protected_multigroup_mip", protected_started);
                    result.rich_protected = improve_rich_protected_stage(problem, at(protected_window.deadline), group_result);
                    const double protected_finished = elapsed();
                    schedule.finish("protected_multigroup_mip", protected_window, protected_finished);
                    group_result.rich_stage_timing["protected_multigroup_mip"] = {
                        budgets.stages.at("protected_multigroup_mip"), protected_window.effective_budget,
                        protected_started, protected_finished, protected_window.deadline,
                        schedule.carry(), schedule.pricing_reserve()};
                    const double pricing_started = elapsed();
                    const auto pricing_window = schedule.begin("special_pricing", pricing_started);
                    result.rich_special_pricing = generate_rich_special_pricing_stage(problem, at(pricing_window.deadline),
                        budgets.post_protected_special_pricing_active, group_result);
                    const double pricing_finished = elapsed();
                    schedule.finish("special_pricing", pricing_window, pricing_finished);
                    group_result.rich_stage_timing["special_pricing"] = {
                        0.0, pricing_window.effective_budget, pricing_started, pricing_finished, pricing_window.deadline,
                        schedule.carry(), schedule.pricing_reserve()};
                }
                const RichPatternResult pattern_result = run_rich_pattern_master(
                    problem, group_result.passenger_to_seat,
                    search_deadline
                );
                result.rich_pattern_count = pattern_result.pattern_count;
                result.rich_selected_pattern_count = pattern_result.selected_pattern_count;
                result.rich_pattern_score = pattern_result.score;
                result.rich_baby_pair_count = pattern_result.baby_pair_count;
                result.rich_master_score = pattern_result.master_score;
                result.rich_master_time_limit = pattern_result.master_time_limit;
                result.rich_master_attempts = pattern_result.master_attempts;
                result.rich_master_last_radius = pattern_result.master_last_radius;
                if (pattern_result.complete
                    && pattern_result.score > group_result.group_construction_score + 1e-9) {
                    group_result.passenger_to_seat = pattern_result.passenger_to_seat;
                    group_result.group_construction_score = pattern_result.score;
                    group_result.selected_components = evaluate_score_components(
                        problem, pattern_result.passenger_to_seat
                    );
                    group_result.selected_incumbent = "rich-m3-pattern-master";
                    group_result.score_delta = pattern_result.score - group_result.q0_score;
                }
            }
            result.rich_elite_store = group_result.rich_elite_store;
            result.rich_structured = group_result.rich_structured;
            result.rich_conflict_diversity_active = group_result.rich_conflict_diversity_active;
            result.rich_construction_repair_queue = group_result.rich_construction_repair_queue;
            result.rich_stage_timing = group_result.rich_stage_timing;
            result.passenger_to_seat = group_result.passenger_to_seat;
            result.selected_incumbent = group_result.selected_incumbent;
            result.fallback_reason = group_result.fallback_reason;
            result.q0_score = group_result.q0_score;
            result.group_construction_score = group_result.group_construction_score;
            result.score_delta = group_result.score_delta;
            result.q0_components = group_result.q0_components;
            result.selected_components = group_result.selected_components;
            result.dfs_nodes = group_result.dfs_nodes;
            result.beam_nodes = group_result.beam_nodes;
            result.dfs_groups = group_result.dfs_groups;
            result.beam_groups = group_result.beam_groups;
            result.groups_improved = group_result.groups_improved;
            result.q1_selected_incumbent = group_result.q1_selected_incumbent;
            result.q1_score = group_result.q1_score;
            result.from_scratch_score = group_result.from_scratch_score;
            result.from_scratch_complete = group_result.from_scratch_complete;
            result.from_scratch_dfs_nodes = group_result.from_scratch_dfs_nodes;
            result.from_scratch_beam_nodes = group_result.from_scratch_beam_nodes;
            result.from_scratch_dfs_groups = group_result.from_scratch_dfs_groups;
            result.from_scratch_beam_groups = group_result.from_scratch_beam_groups;
            result.recovery_attempts = group_result.recovery_attempts;
            result.recovery_succeeded = group_result.recovery_succeeded;
            result.rich_vnd_one_opt_moves = group_result.rich_vnd_one_opt_moves;
            result.rich_vnd_two_swap_moves = group_result.rich_vnd_two_swap_moves;
            result.rich_vnd_three_cycle_moves = group_result.rich_vnd_three_cycle_moves;
            result.rich_vnd_group_rebuild_moves = group_result.rich_vnd_group_rebuild_moves;
            result.rich_vnd_caregiver_rebuild_moves = group_result.rich_vnd_caregiver_rebuild_moves;
            result.rich_vnd_score = group_result.rich_vnd_score;
            result.rich_vnd_seconds = group_result.rich_vnd_seconds;
            result.rich_repair_attempted = group_result.rich_repair_attempted;
            result.rich_construction_assigned = group_result.rich_construction_assigned;
            result.rich_construction_unassigned = group_result.rich_construction_unassigned;
            result.rich_candidate_complete = group_result.rich_candidate_complete;
            result.rich_dfs_attempted = group_result.rich_dfs_attempted;
            result.rich_dfs_succeeded = group_result.rich_dfs_succeeded;
            result.rich_dfs_nodes = group_result.rich_dfs_nodes;
            result.rich_beam_groups = group_result.rich_beam_groups;
            result.rich_paired_ssr_passes = group_result.rich_paired_ssr_passes;
            result.rich_paired_rescue_attempted = group_result.rich_paired_rescue_attempted;
            result.rich_paired_rescue_rescued = group_result.rich_paired_rescue_rescued;
            result.rich_paired_rescue_unresolved = group_result.rich_paired_rescue_unresolved;
            result.rich_paired_joint_rebuilds = group_result.rich_paired_joint_rebuilds;
            result.rich_repair_score = group_result.rich_repair_score;
            result.rich_repair_repaired = group_result.rich_repair_repaired;
            result.rich_repair_unresolved = group_result.rich_repair_unresolved;
            result.rich_repair_nodes = group_result.rich_repair_nodes;
            result.native_hard_violations = validate_complete_assignment(
                problem, result.passenger_to_seat
            );
            result.status = result.native_hard_violations == 0
                ? "HeuristicComplete" : "HeuristicFailed";
        } else {
            result.selected_incumbent = "q0";
            result.fallback_reason = "q0_invalid";
            result.status = "HeuristicFailed";
        }
        result.wall_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started
        ).count();
        return result;
    }
    preprocess_fixed_seats(problem);
    Model model;
    const int passenger_count = static_cast<int>(problem.passengers.size());
    const int seat_count = static_cast<int>(problem.seats.size());
    std::vector<std::vector<int>> x(
        passenger_count, std::vector<int>(seat_count, -1)
    );
    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        for (int seat = 0; seat < seat_count; ++seat) {
            if (!static_eligible(problem, passenger, seat)) continue;
            const double cost = objective == ConstructionObjective::IndividualSoft
                ? -evaluate_individual_score(problem, passenger, seat).total()
                : 1e-8 * (seat + 1);
            x[passenger][seat] = model.add_binary(cost);
        }
        if (std::find_if(x[passenger].begin(), x[passenger].end(),
                        [](int value) { return value >= 0; }) == x[passenger].end()) {
            throw std::runtime_error("passenger has no native legal-domain seat");
        }
    }

    struct SingleReservation { int passenger; int occupied_seat; int empty_seat; int column; };
    std::vector<SingleReservation> single_reservations;
    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        if (!problem.passengers[passenger].need_single_empty) continue;
        for (int seat = 0; seat < seat_count; ++seat) {
            if (x[passenger][seat] < 0) continue;
            for (int empty : problem.seats[seat].same_block_neighbors) {
                single_reservations.push_back(
                    {passenger, seat, empty, model.add_binary()}
                );
            }
        }
    }

    std::map<Location, int> row_activation;
    std::map<Location, int> subrow_activation;
    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        const Passenger& item = problem.passengers[passenger];
        if (item.ssr.empty()) continue;
        for (int seat = 0; seat < seat_count; ++seat) {
            if (x[passenger][seat] < 0) continue;
            if (item.same_row_no_other_ssr) {
                row_activation.try_emplace(location_of(problem.seats[seat], true), -1);
            }
            if (item.same_subrow_no_other_ssr) {
                subrow_activation.try_emplace(location_of(problem.seats[seat], false), -1);
            }
        }
    }
    for (auto& item : row_activation) item.second = model.add_binary();
    for (auto& item : subrow_activation) item.second = model.add_binary();

    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        const int row = model.add_row(1.0, 1.0);
        for (int seat = 0; seat < seat_count; ++seat) {
            if (x[passenger][seat] >= 0) model.add(x[passenger][seat], row, 1.0);
        }
    }

    for (int seat = 0; seat < seat_count; ++seat) {
        const int row = model.add_row(-kInfinity, 1.0);
        for (int passenger = 0; passenger < passenger_count; ++passenger) {
            if (x[passenger][seat] >= 0) model.add(x[passenger][seat], row, 1.0);
            if (!problem.passengers[passenger].need_both_empty) continue;
            for (int occupied = 0; occupied < seat_count; ++occupied) {
                if (x[passenger][occupied] < 0) continue;
                const auto& neighbors = problem.both_side_empty_allow_cross_aisle
                    ? problem.seats[occupied].row_neighbors
                    : problem.seats[occupied].same_block_neighbors;
                if (std::find(neighbors.begin(), neighbors.end(), seat) != neighbors.end()) {
                    model.add(x[passenger][occupied], row, 1.0);
                }
            }
        }
        for (const auto& reservation : single_reservations) {
            if (reservation.empty_seat == seat) model.add(reservation.column, row, 1.0);
        }
    }

    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        if (!problem.passengers[passenger].need_single_empty) continue;
        for (int seat = 0; seat < seat_count; ++seat) {
            if (x[passenger][seat] < 0) continue;
            const int row = model.add_row(0.0, 0.0);
            model.add(x[passenger][seat], row, -1.0);
            for (const auto& reservation : single_reservations) {
                if (reservation.passenger == passenger && reservation.occupied_seat == seat) {
                    model.add(reservation.column, row, 1.0);
                }
            }
        }
    }

    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        const Passenger& cared = problem.passengers[passenger];
        const SsrRule rule = effective_rule(problem, cared);
        if (!cared.need_cared && !rule.requires_caregiver) continue;
        const bool allow_cross = cared.need_cared && cared.ssr.empty()
            ? false : rule.caregiver_allow_cross_aisle;
        const Group& group = problem.groups[cared.group];
        for (int seat = 0; seat < seat_count; ++seat) {
            if (x[passenger][seat] < 0) continue;
            const int row = model.add_row(-kInfinity, 0.0);
            model.add(x[passenger][seat], row, 1.0);
            const auto& neighbors = allow_cross ? problem.seats[seat].row_neighbors
                                                : problem.seats[seat].same_block_neighbors;
            for (int other : group.passengers) {
                if (other == passenger || !adult_caregiver(problem.passengers[other])) continue;
                for (int neighbor : neighbors) {
                    if (x[other][neighbor] >= 0) model.add(x[other][neighbor], row, -1.0);
                }
            }
        }
    }

    auto add_isolation = [&](bool by_row, std::map<Location, int>& activations) {
        for (const auto& activation : activations) {
            const Location& location = activation.first;
            const int z = activation.second;
            const int upper_link = model.add_row(-kInfinity, 0.0);
            model.add(z, upper_link, 1.0);
            for (int passenger = 0; passenger < passenger_count; ++passenger) {
                const Passenger& item = problem.passengers[passenger];
                if (item.ssr.empty()) continue;
                const bool flagged = by_row ? item.same_row_no_other_ssr
                                            : item.same_subrow_no_other_ssr;
                for (int seat = 0; seat < seat_count; ++seat) {
                    if (x[passenger][seat] < 0
                        || location_of(problem.seats[seat], by_row) != location) continue;
                    if (flagged) {
                        model.add(x[passenger][seat], upper_link, -1.0);
                        const int lower_link = model.add_row(-kInfinity, 0.0);
                        model.add(x[passenger][seat], lower_link, 1.0);
                        model.add(z, lower_link, -1.0);
                    }
                }
            }
            std::set<std::string> ssrs;
            for (const Passenger& item : problem.passengers) if (!item.ssr.empty()) ssrs.insert(item.ssr);
            for (const std::string& ssr : ssrs) {
                const int row = model.add_row(-kInfinity, passenger_count + 1.0);
                model.add(z, row, passenger_count);
                for (int passenger = 0; passenger < passenger_count; ++passenger) {
                    if (problem.passengers[passenger].ssr != ssr) continue;
                    for (int seat = 0; seat < seat_count; ++seat) {
                        if (x[passenger][seat] >= 0
                            && location_of(problem.seats[seat], by_row) == location) {
                            model.add(x[passenger][seat], row, 1.0);
                        }
                    }
                }
            }
        }
    };
    add_isolation(true, row_activation);
    add_isolation(false, subrow_activation);

    const int column_count = static_cast<int>(model.columns.size());
    const int row_count = static_cast<int>(model.row_lower.size());
    std::vector<HighsInt> starts(column_count + 1, 0), indices;
    std::vector<double> values;
    for (int column = 0; column < column_count; ++column) {
        starts[column] = static_cast<HighsInt>(indices.size());
        for (const auto& coefficient : model.columns[column]) {
            if (std::abs(coefficient.second) <= 1e-15) continue;
            indices.push_back(coefficient.first);
            values.push_back(coefficient.second);
        }
    }
    starts[column_count] = static_cast<HighsInt>(indices.size());

    void* highs = Highs_create();
    Highs_setBoolOptionValue(highs, "output_flag", 0);
    Highs_setIntOptionValue(highs, "threads", 1);
    Highs_setIntOptionValue(highs, "random_seed", seed);
    Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0);
    Highs_setDoubleOptionValue(highs, "time_limit", time_limit_seconds);
    const HighsInt pass_status = Highs_passMip(
        highs, column_count, row_count, static_cast<HighsInt>(indices.size()),
        kHighsMatrixFormatColwise, kHighsObjSenseMinimize, 0.0,
        model.cost.data(), model.lower.data(), model.upper.data(),
        model.row_lower.data(), model.row_upper.data(), starts.data(),
        indices.data(), values.data(), model.integrality.data()
    );
    if (pass_status == kHighsStatusError) {
        Highs_destroy(highs);
        throw std::runtime_error("HiGHS rejected native feasibility model");
    }
    Highs_run(highs);
    const HighsInt model_status = Highs_getModelStatus(highs);
    std::vector<double> solution(column_count, 0.0);
    Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr);
    Highs_destroy(highs);

    FeasibilityResult result;
    result.status = model_status == kHighsModelStatusOptimal ? "Optimal"
        : model_status == kHighsModelStatusTimeLimit ? "TimeLimit"
        : model_status == kHighsModelStatusInfeasible ? "Infeasible" : "Other";
    result.passenger_to_seat.assign(passenger_count, -1);
    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        for (int seat = 0; seat < seat_count; ++seat) {
            const int column = x[passenger][seat];
            if (column >= 0 && solution[column] > 0.5) {
                if (result.passenger_to_seat[passenger] >= 0) {
                    result.passenger_to_seat[passenger] = -2;
                } else {
                    result.passenger_to_seat[passenger] = seat;
                }
            }
        }
    }
    result.native_hard_violations = validate_complete_assignment(
        problem, result.passenger_to_seat
    );
    result.wall_seconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - started
    ).count();
    result.selected_incumbent = construction_objective_name(objective);
    return result;
}

int validate_complete_assignment(
    const Problem& problem, const std::vector<int>& assignment
) {
    int violations = 0;
    if (assignment.size() != problem.passengers.size()) return 1;
    std::vector<int> seat_owner(problem.seats.size(), -1);
    for (int passenger = 0; passenger < static_cast<int>(assignment.size()); ++passenger) {
        const int seat_index = assignment[passenger];
        if (seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) {
            ++violations;
            continue;
        }
        if (seat_owner[seat_index] >= 0) ++violations;
        else seat_owner[seat_index] = passenger;
        if (!static_eligible(problem, passenger, seat_index)) ++violations;
    }
    std::set<int> protected_empty;
    std::vector<std::vector<int>> single_candidates;
    for (int passenger = 0; passenger < static_cast<int>(assignment.size()); ++passenger) {
        const int seat_index = assignment[passenger];
        if (seat_index < 0 || seat_index >= static_cast<int>(problem.seats.size())) continue;
        const Passenger& item = problem.passengers[passenger];
        const Seat& seat = problem.seats[seat_index];
        if (item.need_both_empty) {
            const auto& neighbors = problem.both_side_empty_allow_cross_aisle
                ? seat.row_neighbors : seat.same_block_neighbors;
            for (int neighbor : neighbors) {
                if (seat_owner[neighbor] >= 0 || !protected_empty.insert(neighbor).second) ++violations;
            }
        }
        if (item.need_single_empty) {
            std::vector<int> candidates;
            for (int neighbor : seat.same_block_neighbors) {
                if (seat_owner[neighbor] < 0 && !protected_empty.count(neighbor)) candidates.push_back(neighbor);
            }
            single_candidates.push_back(std::move(candidates));
        }
        const SsrRule rule = effective_rule(problem, item);
        if (item.need_cared || rule.requires_caregiver) {
            const bool allow_cross = item.need_cared && item.ssr.empty()
                ? false : rule.caregiver_allow_cross_aisle;
            const auto& neighbors = allow_cross ? seat.row_neighbors : seat.same_block_neighbors;
            bool found = false;
            for (int neighbor : neighbors) {
                const int other = seat_owner[neighbor];
                found = found || (other >= 0
                    && problem.passengers[other].group == item.group
                    && adult_caregiver(problem.passengers[other]));
            }
            if (!found) ++violations;
        }
    }
    std::map<int, int> empty_owner;
    auto augment = [&](auto&& self, int demand, std::set<int>& visited) -> bool {
        for (int empty : single_candidates[demand]) {
            if (!visited.insert(empty).second) continue;
            const auto owner = empty_owner.find(empty);
            if (owner == empty_owner.end() || self(self, owner->second, visited)) {
                empty_owner[empty] = demand;
                return true;
            }
        }
        return false;
    };
    for (int demand = 0; demand < static_cast<int>(single_candidates.size()); ++demand) {
        std::set<int> visited;
        if (!augment(augment, demand, visited)) ++violations;
    }
    for (bool by_row : {true, false}) {
        std::set<Location> activated;
        std::map<std::pair<Location, std::string>, int> counts;
        for (int passenger = 0; passenger < static_cast<int>(assignment.size()); ++passenger) {
            const int seat_index = assignment[passenger];
            if (seat_index < 0 || problem.passengers[passenger].ssr.empty()) continue;
            const Passenger& item = problem.passengers[passenger];
            const Location location = location_of(problem.seats[seat_index], by_row);
            ++counts[{location, item.ssr}];
            if ((by_row && item.same_row_no_other_ssr)
                || (!by_row && item.same_subrow_no_other_ssr)) activated.insert(location);
        }
        for (const auto& count : counts) {
            if (activated.count(count.first.first) && count.second > 1) {
                violations += count.second - 1;
            }
        }
    }
    return violations;
}


RichProtectedMipDiagnostics improve_rich_protected_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, GroupConstructionResult& result
) {
    RichProtectedMipDiagnostics combined;
    if (!result.rich_candidate_complete) return combined;
    AssignmentState state(problem); state.restore(result.rich_state);
    for (int i = 0; i < problem.rich.protected_multigroup_max_passes; ++i) {
        const auto pass = improve_rich_protected_mip(problem, state, result.rich_elite_store, deadline);
        result.rich_elite_store.capture(state, "protected_multigroup_mip");
        if (i == 0) combined = pass;
        else {
            combined.roots_considered += pass.roots_considered;
            combined.components_tested += pass.components_tested;
            combined.mip_columns += pass.mip_columns;
            combined.dynamic_relocation_calls += pass.dynamic_relocation_calls;
            combined.dynamic_relocation_patterns += pass.dynamic_relocation_patterns;
            combined.accepted += pass.accepted;
            combined.score_improvement += pass.score_improvement;
            combined.seconds += pass.seconds;
            combined.tested_components.insert(combined.tested_components.end(), pass.tested_components.begin(), pass.tested_components.end());
            combined.accepted_components.insert(combined.accepted_components.end(), pass.accepted_components.begin(), pass.accepted_components.end());
            combined.stopped_by_deadline |= pass.stopped_by_deadline;
            // Frozen Python retains first-pass root metadata and conditional row count.
        }
        combined.passes = i + 1;
        if (!pass.enabled || !pass.accepted || pass.score_improvement < problem.rich.protected_multigroup_min_pass_gain
            || std::chrono::steady_clock::now() >= deadline) break;
    }
    result.rich_state = state.save();
    const double score = evaluate_soft_score(problem, state.passenger_to_seat);
    if (validate_complete_assignment(problem, state.passenger_to_seat) == 0 && score > result.group_construction_score + 1e-9) {
        result.passenger_to_seat = state.passenger_to_seat;
        result.group_construction_score = score;
        result.selected_components = evaluate_score_components(problem, state.passenger_to_seat);
        result.selected_incumbent = "rich-m4-protected-mip";
        result.score_delta = score - result.q0_score;
    }
    return combined;
}

RichSpecialPricingDiagnostics generate_rich_special_pricing_stage(const Problem& problem,
    std::chrono::steady_clock::time_point deadline, bool enabled, GroupConstructionResult& result
) {
    if (!result.rich_candidate_complete) return {};
    AssignmentState state(problem); state.restore(result.rich_state);
    return generate_rich_special_dual_patterns(problem, state, result.rich_elite_store, deadline, enabled,
        [&](int group, const RichExactPattern& candidate, double score) {
            RichElitePattern pattern;
            pattern.local_score = score; pattern.source = "special_dual_pricing"; pattern.pinned = true;
            for (const auto& entry : candidate.assignments)
                pattern.assignments.emplace_back(problem.passengers[entry.first].hostnum, problem.seats[entry.second].id);
            std::map<int, std::vector<std::string>> blocked;
            for (const auto& entry : candidate.blocked_by)
                blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
            pattern.blocked_by_host.assign(blocked.begin(), blocked.end());
            result.rich_elite_store.record_candidate(problem.groups[group].id, std::move(pattern), state, result.rich_conflict_diversity_active);
        });
}

RichProtectedMipDiagnostics improve_rich_protected_mip(const Problem& problem,
    AssignmentState& state, RichEliteStore& elite, std::chrono::steady_clock::time_point deadline
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    const auto elapsed = [&]() { return std::chrono::duration<double>(Clock::now() - started).count(); };
    RichProtectedMipDiagnostics d;
    d.protected_roots_enabled = problem.rich.protected_multigroup_enabled;
    d.priority_roots_enabled = problem.rich.priority_multigroup_enabled;
    d.enabled = d.protected_roots_enabled || d.priority_roots_enabled;
    if (!d.enabled || started >= deadline) {
        d.stopped_by_deadline = started >= deadline; d.seconds = elapsed(); return d;
    }
    std::set<int> protected_groups, priority_groups;
    std::map<int, int> group_indexes;
    for (size_t g = 0; g < problem.groups.size(); ++g) {
        const auto& group = problem.groups[g];
        group_indexes[group.id] = static_cast<int>(g);
        for (int p : group.passengers)
            if (problem.passengers[p].need_single_empty || problem.passengers[p].need_both_empty) protected_groups.insert(group.id);
    }
    const auto queue = build_rich_repair_queue(problem, state.passenger_to_seat);
    std::map<int, double> priority_loss;
    for (const auto& metric : queue) {
        priority_loss[metric.group_id] = metric.priority_loss;
        if (d.priority_roots_enabled && metric.extreme_dispersion) priority_groups.insert(metric.group_id);
    }
    d.roots_initialized = true;
    d.protected_root_count = static_cast<int>(protected_groups.size());
    d.priority_root_count = static_cast<int>(priority_groups.size());
    std::vector<int> roots;
    for (const auto& metric : queue)
        if ((d.protected_roots_enabled && protected_groups.count(metric.group_id)) || priority_groups.count(metric.group_id)) roots.push_back(metric.group_id);
    if (roots.empty()) { d.reason = "no_protected_or_priority_groups"; d.seconds = elapsed(); return d; }
    if (roots.size() > static_cast<size_t>(problem.rich.protected_multigroup_root_limit)) roots.resize(problem.rich.protected_multigroup_root_limit);
    const int max_groups = std::min(static_cast<int>(problem.groups.size()), problem.rich.protected_multigroup_max_groups);
    const int option_limit = problem.rich.protected_multigroup_options_per_group;
    const auto fixed = preprocess_fixed_seats(problem);
    const auto baby = build_rich_baby_costs(problem);
    std::map<int, RichPricingCache> caches;
    const auto resources_for = [&](int gid) {
        std::set<std::string> resources;
        for (int p : problem.groups[group_indexes.at(gid)].passengers) {
            if (state.passenger_to_seat[p] >= 0) resources.insert(problem.seats[state.passenger_to_seat[p]].id);
            for (int seat : state.assigned_blocked[p]) resources.insert(problem.seats[seat].id);
        }
        return resources;
    };
    const auto ranked = [&](int gid) {
        std::vector<size_t> indexes;
        const auto found = elite.groups().find(gid);
        if (found == elite.groups().end()) return indexes;
        const auto& patterns = found->second;
        for (size_t i = 0; i < patterns.size(); ++i) indexes.push_back(i);
        std::stable_sort(indexes.begin(), indexes.end(), [&](size_t a, size_t b) { return patterns[a].local_score > patterns[b].local_score; });
        if (indexes.size() > static_cast<size_t>(option_limit)) indexes.resize(option_limit);
        return indexes;
    };
    const double initial_score = evaluate_rich_group_score(problem, state.passenger_to_seat, -1).total();
    double best_score = initial_score;
    std::set<std::vector<int>> tested;
    for (int root : roots) {
        if (Clock::now() >= deadline) break;
        ++d.roots_considered;
        std::map<std::string, int> owners;
        for (const auto& group : problem.groups) for (const auto& resource : resources_for(group.id)) owners[resource] = group.id;
        std::vector<std::vector<int>> components;
        for (size_t index : ranked(root)) {
            std::set<int> conflicts;
            for (const auto& seat : elite.groups().at(root)[index].seat_resources) {
                const auto owner = owners.find(seat);
                if (owner != owners.end() && owner->second != root) conflicts.insert(owner->second);
            }
            std::vector<int> ordered(conflicts.begin(), conflicts.end());
            std::sort(ordered.begin(), ordered.end(), [&](int a, int b) { return std::make_pair(priority_loss.at(a), a) < std::make_pair(priority_loss.at(b), b); });
            if (ordered.size() > static_cast<size_t>(max_groups - 1)) ordered.resize(max_groups - 1);
            ordered.push_back(root); std::sort(ordered.begin(), ordered.end());
            if (ordered.size() >= 2) components.push_back(std::move(ordered));
        }
        for (const auto& component : components) {
            if (tested.count(component) || d.components_tested >= problem.rich.protected_multigroup_component_limit || Clock::now() >= deadline) continue;
            tested.insert(component);
            if (std::any_of(component.begin(), component.end(), [&](int gid) {
                const auto found = elite.groups().find(gid); return found == elite.groups().end() || found->second.empty();
            })) continue;
            ++d.components_tested; d.tested_components.push_back(component);
            std::set<std::string> outside;
            std::set<int> outside_indexes;
            for (const auto& group : problem.groups) if (!std::binary_search(component.begin(), component.end(), group.id))
                for (const auto& seat : resources_for(group.id)) { outside.insert(seat); outside_indexes.insert(problem.seat_index.at(seat)); }
            for (int gid : component) if (gid != root) {
                const auto dynamic = add_rich_dynamic_relocation_patterns(problem, state, group_indexes.at(gid), outside_indexes, deadline, fixed, baby, caches, elite);
                d.dynamic_relocation_calls += dynamic.calls; d.dynamic_relocation_patterns += dynamic.patterns;
            }
            std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
            void* highs = solver.get();
            const auto check = [](HighsInt status) { if (status == kHighsStatusError) throw std::runtime_error("protected MIP API error"); };
            check(Highs_setBoolOptionValue(highs, "output_flag", 0));
            check(Highs_setIntOptionValue(highs, "threads", 1));
            check(Highs_setIntOptionValue(highs, "random_seed", 0));
            check(Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0));
            check(Highs_setDoubleOptionValue(highs, "time_limit", std::max(.01, std::chrono::duration<double>(deadline - Clock::now()).count())));
            const double infinity = Highs_getInfinity(highs);
            std::map<int, HighsInt> group_rows;
            std::map<std::string, HighsInt> resource_rows;
            HighsInt row = 0;
            for (int gid : component) {
                group_rows[gid] = row++; check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr));
                for (const auto& pattern : elite.groups().at(gid)) for (const auto& seat : pattern.seat_resources)
                    if (!outside.count(seat)) resource_rows[seat] = 0;
            }
            for (auto& resource : resource_rows) {
                resource.second = row++; check(Highs_addRow(highs, -infinity, 1.0, 0, nullptr, nullptr));
            }
            std::vector<std::pair<int, size_t>> columns;
            for (int gid : component) for (size_t index : ranked(gid)) {
                const auto& pattern = elite.groups().at(gid)[index];
                if (pattern.seat_resources.empty() || std::any_of(pattern.seat_resources.begin(), pattern.seat_resources.end(),
                    [&](const std::string& seat) { return outside.count(seat) != 0; })) continue;
                std::vector<HighsInt> rows{group_rows.at(gid)};
                for (const auto& seat : pattern.seat_resources) rows.push_back(resource_rows.at(seat));
                std::vector<double> values(rows.size(), 1.0);
                check(Highs_addCol(highs, -pattern.local_score, 0.0, 1.0, static_cast<HighsInt>(rows.size()), rows.data(), values.data()));
                check(Highs_changeColIntegrality(highs, static_cast<HighsInt>(columns.size()), kHighsVarTypeInteger));
                columns.emplace_back(gid, index);
            }
            d.mip_columns += static_cast<int>(columns.size());
            if (columns.empty()) continue;
            for (size_t left = 0; left < columns.size(); ++left) for (size_t right = left + 1; right < columns.size(); ++right) {
                const auto& a = columns[left]; const auto& b = columns[right];
                if (a.first == b.first || !rich_patterns_have_conditional_ssr_conflict(problem,
                    a.first, elite.groups().at(a.first)[a.second], b.first, elite.groups().at(b.first)[b.second])) continue;
                const HighsInt indexes[] = {static_cast<HighsInt>(left), static_cast<HighsInt>(right)};
                const double values[] = {1.0, 1.0};
                check(Highs_addRow(highs, -infinity, 1.0, 2, indexes, values)); ++d.conditional_ssr_rows;
            }
            Highs_run(highs);
            std::vector<double> solution(columns.size());
            Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr);
            std::map<int, RichElitePattern> choices;
            for (size_t i = 0; i < columns.size(); ++i) if (solution[i] > .5)
                choices[columns[i].first] = elite.groups().at(columns[i].first)[columns[i].second];
            if (choices.size() != component.size()) continue;
            AssignmentSnapshot candidate;
            if (!rebuild_rich_pattern_component(state, choices, candidate)) continue;
            const int violations = validate_complete_assignment(problem, candidate.passenger_to_seat);
            const double score = evaluate_rich_group_score(problem, candidate.passenger_to_seat, -1).total();
            if (violations || score <= best_score + problem.rich.local_search_epsilon) continue;
            const double delta = score - best_score;
            state.restore(std::move(candidate)); best_score = score;
            ++d.accepted;
            d.accepted_components.push_back({component, delta, elapsed(), d.components_tested});
        }
    }
    d.score_improvement = best_score - initial_score;
    d.seconds = elapsed(); d.stopped_by_deadline = Clock::now() >= deadline;
    return d;
}

std::map<int, std::vector<int>> solve_rich_lns_master(const AssignmentState& state,
    const RichLnsWorkspace& workspace, const std::vector<int>& component,
    const std::map<int, std::vector<RichLnsOption>>& options, int root,
    double local_time_limit, std::chrono::steady_clock::time_point deadline
) {
    std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
    void* highs = solver.get();
    const auto check = [](HighsInt status) { if (status == kHighsStatusError) throw std::runtime_error("LNS MIP API error"); };
    check(Highs_setBoolOptionValue(highs, "output_flag", 0));
    check(Highs_setIntOptionValue(highs, "threads", 1));
    check(Highs_setIntOptionValue(highs, "random_seed", 0));
    check(Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0));
    check(Highs_setDoubleOptionValue(highs, "time_limit", std::min(local_time_limit,
        std::max(.01, std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count()))));
    std::map<int, HighsInt> group_rows;
    std::map<std::string, HighsInt> seat_rows;
    HighsInt row = 0;
    for (int g : component) {
        group_rows[g] = row++;
        check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr));
        for (const auto& option : options.at(g)) for (int s : option.seats) seat_rows[state.problem.seats[s].id] = 0;
    }
    for (auto& seat : seat_rows) {
        seat.second = row++;
        check(Highs_addRow(highs, -Highs_getInfinity(highs), 1.0, 0, nullptr, nullptr));
    }
    std::vector<int> root_current;
    for (int p : workspace.keys_by_group[root]) root_current.push_back(state.passenger_to_seat[p]);
    std::vector<std::pair<int, const RichLnsOption*>> columns;
    for (int g : component) for (const auto& option : options.at(g)) {
        if (g == root && option.assignment == root_current) continue;
        std::vector<HighsInt> rows{group_rows.at(g)};
        for (int s : option.seats) rows.push_back(seat_rows.at(state.problem.seats[s].id));
        std::vector<double> values(rows.size(), 1.0);
        check(Highs_addCol(highs, -option.score, 0.0, 1.0, static_cast<HighsInt>(rows.size()), rows.data(), values.data()));
        check(Highs_changeColIntegrality(highs, static_cast<HighsInt>(columns.size()), kHighsVarTypeInteger));
        columns.emplace_back(g, &option);
    }
    if (columns.empty()) return {};
    check(Highs_run(highs));
    std::vector<double> solution(columns.size());
    check(Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr));
    std::map<int, std::vector<int>> choices;
    for (size_t i = 0; i < columns.size(); ++i) if (solution[i] > .5) choices[columns[i].first] = columns[i].second->assignment;
    if (choices.size() != component.size()) return {};
    return choices;
}

void write_rich_protected_mip_diagnostics(std::ostream& output, const RichProtectedMipDiagnostics& d) {
    output << "{\"enabled\":" << (d.enabled ? "true" : "false")
        << ",\"protected_roots_enabled\":" << (d.protected_roots_enabled ? "true" : "false")
        << ",\"priority_roots_enabled\":" << (d.priority_roots_enabled ? "true" : "false")
        << ",\"roots_considered\":" << d.roots_considered << ",\"components_tested\":" << d.components_tested
        << ",\"mip_columns\":" << d.mip_columns << ",\"dynamic_relocation_calls\":" << d.dynamic_relocation_calls
        << ",\"dynamic_relocation_patterns\":" << d.dynamic_relocation_patterns << ",\"conditional_ssr_rows\":" << d.conditional_ssr_rows
        << ",\"accepted\":" << d.accepted << ",\"score_improvement\":" << d.score_improvement << ",\"seconds\":" << d.seconds
        << ",\"stopped_by_deadline\":" << (d.stopped_by_deadline ? "true" : "false");
    if (d.passes) output << ",\"passes\":" << d.passes;
    if (d.roots_initialized) output << ",\"protected_root_count\":" << d.protected_root_count << ",\"priority_root_count\":" << d.priority_root_count;
    if (!d.reason.empty()) output << ",\"reason\":\"" << d.reason << '"';
    const auto groups = [&](const std::vector<int>& values) {
        output << '['; for (size_t i = 0; i < values.size(); ++i) { if (i) output << ','; output << values[i]; } output << ']';
    };
    output << ",\"tested_components\":[";
    for (size_t i = 0; i < d.tested_components.size(); ++i) { if (i) output << ','; groups(d.tested_components[i]); }
    output << "],\"accepted_components\":[";
    for (size_t i = 0; i < d.accepted_components.size(); ++i) {
        if (i) output << ',';
        const auto& item = d.accepted_components[i];
        output << "{\"groups\":"; groups(item.groups);
        output << ",\"delta\":" << item.delta << ",\"test_index\":" << item.test_index << ",\"elapsed_seconds\":" << item.elapsed_seconds << '}';
    }
    output << "]}";
}

RichSpecialPricingDiagnostics generate_rich_special_dual_patterns(
    const Problem& problem, const AssignmentState& state, const RichEliteStore& elite,
    std::chrono::steady_clock::time_point deadline, bool enabled,
    const std::function<void(int, const RichExactPattern&, double)>& recorder
) {
    using Clock = std::chrono::steady_clock;
    const auto started = Clock::now();
    RichSpecialPricingDiagnostics result;
    result.enabled = enabled;
    if (!enabled || started >= deadline) return result;
    std::set<int> special;
    for (size_t g = 0; g < problem.groups.size(); ++g) {
        bool fixed = false, relevant = false;
        for (int p : problem.groups[g].passengers) {
            const auto& passenger = problem.passengers[p];
            fixed |= passenger.has_new_seat;
            relevant |= !passenger.ssr.empty() || passenger.need_cared
                || passenger.need_single_empty || passenger.need_both_empty;
        }
        if (!fixed && relevant) special.insert(static_cast<int>(g));
    }
    if (special.empty()) { result.lp_status = "no_special_groups"; return result; }
    std::unique_ptr<void, decltype(&Highs_destroy)> solver(Highs_create(), Highs_destroy);
    void* highs = solver.get();
    const auto check = [](HighsInt status) {
        if (status == kHighsStatusError) throw std::runtime_error("special pricing LP API error");
    };
    check(Highs_setBoolOptionValue(highs, "output_flag", 0));
    check(Highs_setIntOptionValue(highs, "threads", 1));
    check(Highs_setDoubleOptionValue(highs, "time_limit",
        std::max(.01, std::chrono::duration<double>(deadline - started).count())));
    const double infinity = Highs_getInfinity(highs);
    std::map<int, HighsInt> group_rows;
    std::map<std::string, HighsInt> seat_rows;
    for (const auto& group : problem.groups) group_rows[group.id] = 0;
    for (const auto& seat : problem.seats) seat_rows[seat.id] = 0;
    HighsInt row = 0;
    for (auto& entry : group_rows) {
        entry.second = row++;
        check(Highs_addRow(highs, 1.0, 1.0, 0, nullptr, nullptr));
    }
    for (auto& entry : seat_rows) {
        entry.second = row++;
        check(Highs_addRow(highs, -infinity, 1.0, 0, nullptr, nullptr));
    }
    const auto add_column = [&](int gid, double score, const auto& resources) {
        std::vector<HighsInt> rows{group_rows.at(gid)};
        for (const auto& seat : resources) rows.push_back(seat_rows.at(seat));
        std::vector<double> values(rows.size(), 1.0);
        check(Highs_addCol(highs, -score, 0.0, infinity,
            static_cast<HighsInt>(rows.size()), rows.data(), values.data()));
        ++result.lp_columns;
    };
    for (size_t g = 0; g < problem.groups.size(); ++g) {
        const auto& group = problem.groups[g];
        std::set<std::string> resources;
        for (int p : group.passengers) {
            resources.insert(problem.seats.at(state.passenger_to_seat.at(p)).id);
            for (int s : state.assigned_blocked[p]) resources.insert(problem.seats[s].id);
        }
        add_column(group.id, evaluate_rich_group_score(problem, state.passenger_to_seat, static_cast<int>(g)).total(), resources);
        const auto found = elite.groups().find(group.id);
        if (found != elite.groups().end())
            for (const auto& pattern : found->second)
                add_column(group.id, pattern.local_score, pattern.seat_resources);
    }
    const auto run_status = Highs_run(highs);
    const auto status = Highs_getModelStatus(highs);
    const char* names[] = {"Not Set", "Load error", "Model error", "Presolve error", "Solve error",
        "Postsolve error", "Empty", "Optimal", "Infeasible", "Primal infeasible or unbounded",
        "Unbounded", "Bound on objective reached", "Target for objective reached", "Time limit reached",
        "Iteration limit reached", "Unknown", "Solution limit reached", "Interrupted by user",
        "Memory limit reached", "Interrupted by HiGHS"};
    result.lp_status = status >= 0 && status < 20 ? names[status] : "Unknown";
    if (run_status != kHighsStatusError && status == kHighsModelStatusOptimal) {
        std::vector<double> row_duals(row);
        check(Highs_getSolution(highs, nullptr, nullptr, nullptr, row_duals.data()));
        RichPricingDuals duals;
        for (const auto& entry : group_rows) duals.group[entry.first] = row_duals[entry.second];
        for (const auto& entry : seat_rows) duals.seat[problem.seat_index.at(entry.first)] = row_duals[entry.second];
        auto config = problem.rich_pricing_config;
        config.type = native_json::Value::Type::Object;
        for (const auto& entry : std::vector<std::pair<std::string, double>>{
            {"quick_pricing_columns_per_group", 4.0}, {"dfs_discovery_time_limit", .05}}) {
            auto& value = config.object[entry.first];
            value.type = native_json::Value::Type::Number; value.number = entry.second;
        }
        std::set<std::string> ssr;
        for (const auto& passenger : problem.passengers) if (!passenger.ssr.empty()) ssr.insert(passenger.ssr);
        const std::vector<std::string> active_ssr(ssr.begin(), ssr.end());
        const auto fixed = preprocess_fixed_seats(problem);
        const auto baby = build_rich_baby_costs(problem);
        const auto queue = build_rich_repair_queue(problem, state.passenger_to_seat);
        for (const auto& metric : queue) {
            const auto found = std::find_if(problem.groups.begin(), problem.groups.end(),
                [&](const Group& group) { return group.id == metric.group_id; });
            const int g = static_cast<int>(found - problem.groups.begin());
            if (!special.count(g)) continue;
            if (result.groups_attempted >= 13 || Clock::now() >= deadline) break;
            auto cache = build_rich_pricing_cache(problem, g, fixed);
            const auto priced = price_rich_group_dfs(problem, g, config, duals, baby, {}, {},
                std::min(deadline, Clock::now() + std::chrono::milliseconds(50)), cache,
                false, false, false, active_ssr);
            ++result.groups_attempted;
            result.dfs_nodes += priced.nodes;
            for (const auto& pattern : priced.patterns) {
                const double reduced = rich_pattern_reduced_cost(pattern, duals, baby, false);
                if (reduced >= -1e-7) continue;
                auto proposal = state.passenger_to_seat;
                for (const auto& entry : pattern.assignments) proposal[entry.first] = entry.second;
                recorder(g, pattern, evaluate_rich_group_score(problem, proposal, g).total());
                ++result.negative_patterns;
                result.accepted_patterns.emplace_back(pattern, reduced);
            }
        }
    }
    result.seconds = std::chrono::duration<double>(Clock::now() - started).count();
    return result;
}

void write_rich_special_pricing_diagnostics(std::ostream& output,
    const Problem& problem, const RichSpecialPricingDiagnostics& d
) {
    output << "{\"enabled\":" << (d.enabled ? "true" : "false")
        << ",\"lp_status\":\"" << d.lp_status << "\",\"lp_columns\":" << d.lp_columns
        << ",\"groups_attempted\":" << d.groups_attempted << ",\"negative_patterns\":" << d.negative_patterns
        << ",\"dfs_nodes\":" << d.dfs_nodes << ",\"seconds\":" << d.seconds << ",\"accepted_patterns\":[";
    for (size_t i = 0; i < d.accepted_patterns.size(); ++i) {
        if (i) output << ',';
        const auto& pattern = d.accepted_patterns[i].first;
        const int gid = problem.passengers[pattern.assignments.front().first].group_id;
        output << "{\"group_id\":" << gid << ",\"reduced_cost\":" << d.accepted_patterns[i].second << ",\"assignments\":[";
        for (size_t j = 0; j < pattern.assignments.size(); ++j) {
            if (j) output << ',';
            const auto& a = pattern.assignments[j];
            output << "[[" << gid << ',' << problem.passengers[a.first].hostnum << "],\"" << problem.seats[a.second].id << "\"]";
        }
        output << "],\"blocked_by\":[";
        for (size_t j = 0; j < pattern.blocked_by.size(); ++j) {
            if (j) output << ',';
            const auto& a = pattern.blocked_by[j];
            output << "[\"" << problem.seats[a.first].id << "\",[" << gid << ',' << problem.passengers[a.second].hostnum << "]]";
        }
        output << "]}";
    }
    output << "]}";
}

}  // namespace full_cpp
