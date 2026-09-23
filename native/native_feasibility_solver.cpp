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

}  // namespace full_cpp
