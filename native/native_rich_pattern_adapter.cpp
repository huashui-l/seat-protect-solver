#include "native_rich_pattern_adapter.hpp"

#include "native_master_types.hpp"
#include "native_feasibility_solver.hpp"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <set>
#include <map>
#include <sstream>
#include <string>
#include <tuple>
#include <unordered_map>

namespace full_cpp {
namespace {

struct LocationKey {
    int row;
    int subrow;
    std::string ssr;
    bool operator<(const LocationKey& other) const {
        return std::tie(row, subrow, ssr) < std::tie(other.row, other.subrow, other.ssr);
    }
};

bool adult(const Passenger& passenger) {
    return passenger.ssr.empty() && !passenger.need_cared
        && !passenger.need_both_empty && !passenger.need_single_empty;
}

}  // namespace

RichPatternResult run_rich_pattern_master(
    const Problem& problem, const std::vector<int>& incumbent,
    std::chrono::steady_clock::time_point deadline
) {
    RichPatternResult result;
    result.passenger_to_seat = incumbent;
    if (validate_complete_assignment(problem, incumbent) != 0) return result;

    std::map<LocationKey, int> location_ids;
    auto location_id = [&](const LocationKey& key) {
        auto found = location_ids.find(key);
        if (found != location_ids.end()) return found->second;
        const int id = static_cast<int>(location_ids.size());
        location_ids.emplace(key, id);
        return id;
    };
    std::set<std::string> ssr_types;
    for (const auto& passenger : problem.passengers)
        if (!passenger.ssr.empty()) ssr_types.insert(passenger.ssr);
    for (const auto& seat : problem.seats) {
        for (const auto& ssr : ssr_types) {
            location_id({seat.row, -1, ssr});
            location_id({seat.row, seat.subrow, ssr});
        }
    }
    // Match empty-seat ownership globally, including protection in other groups.
    std::set<int> unavailable(incumbent.begin(), incumbent.end());
    for (int p = 0; p < static_cast<int>(incumbent.size()); ++p) {
        if (!problem.passengers[p].need_both_empty) continue;
        const auto& seat = problem.seats[incumbent[p]];
        const auto& neighbors = problem.both_side_empty_allow_cross_aisle
            ? seat.row_neighbors : seat.same_block_neighbors;
        unavailable.insert(neighbors.begin(), neighbors.end());
    }
    std::map<int, int> empty_owner;
    std::vector<int> incumbent_blocks(incumbent.size(), -1);
    auto augment = [&](auto&& self, int passenger, std::set<int>& visited) -> bool {
        for (int empty : problem.seats[incumbent[passenger]].same_block_neighbors) {
            if (unavailable.count(empty) || !visited.insert(empty).second) continue;
            const auto found = empty_owner.find(empty);
            if (found == empty_owner.end() || self(self, found->second, visited)) {
                empty_owner[empty] = passenger;
                incumbent_blocks[passenger] = empty;
                return true;
            }
        }
        return false;
    };
    for (int p = 0; p < static_cast<int>(incumbent.size()); ++p) {
        if (!problem.passengers[p].need_single_empty
            || problem.passengers[p].need_both_empty) continue;
        std::set<int> visited;
        if (!augment(augment, p, visited)) return result;
    }
    std::ostringstream input;
    input << std::setprecision(17);
    const int seat_count = static_cast<int>(problem.seats.size());
    const int group_count = static_cast<int>(problem.groups.size());
    input << "HEADER_V2 " << seat_count << ' ' << seat_count << ' ' << location_ids.size() << ' '
          << group_count << " 0 0 " << problem.weight_c << ' '
          << problem.group_centroid_x_factor << ' ' << problem.group_centroid_y_factor << '\n';
    for (int seat = 0; seat < seat_count; ++seat) {
        const Seat& item = problem.seats[seat];
        input << "SEAT " << seat << ' ' << item.row << ' ' << item.x << ' ' << item.y << ' '
              << item.same_block_neighbors.size();
        for (int neighbor : item.same_block_neighbors) input << ' ' << neighbor;
        input << ' ' << item.row_neighbors.size();
        for (int neighbor : item.row_neighbors) input << ' ' << neighbor;
        input << '\n';
    }

    // Keep every seat in the raw adapter so the incumbent can always be
    // represented; the native kernel still applies its own pattern limits.
    const int candidate_cap = seat_count;
    for (int group_index = 0; group_index < group_count; ++group_index) {
        const Group& group = problem.groups[group_index];
        const int group_size = static_cast<int>(group.passengers.size());
        const int beam_width = group_size <= 2 ? problem.rich.beam_width_small
            : group_size <= 5 ? problem.rich.beam_width_medium : problem.rich.beam_width_large;
        const int pattern_limit = std::max(1, std::min(12, candidate_cap));
        const double current_hint = [&]() {
            double total = 0.0;
            for (int passenger : group.passengers) total += problem.seats[incumbent[passenger]].row;
            return group_size ? total / group_size : 0.0;
        }();
        input << "GROUP " << group.id << ' ' << group_size << " 1 16 "
              << beam_width << ' ' << pattern_limit << " 1 " << current_hint << '\n';
        AssignmentState domain(problem);
        std::vector<int> incumbent_choices;
        for (int passenger : group.passengers) {
            const Passenger& item = problem.passengers[passenger];
            const auto rule = problem.ssr_rules.find(item.ssr);
            const bool cared = item.need_cared || (rule != problem.ssr_rules.end() && rule->second.requires_caregiver);
            const bool cross = rule != problem.ssr_rules.end() && rule->second.caregiver_allow_cross_aisle;
            input << "PASSENGER " << passenger << ' ' << adult(item) << ' ' << cared << ' ' << cross << ' ';
            std::vector<std::tuple<int, int, double>> options;
            for (int seat = 0; seat < seat_count; ++seat) {
                std::vector<int> blocks;
                if (item.need_single_empty && !item.need_both_empty) {
                    blocks.assign(problem.seats[seat].same_block_neighbors.begin(),
                                  problem.seats[seat].same_block_neighbors.end());
                } else {
                    blocks.push_back(-1);
                }
                for (int block : blocks) {
                    if (!domain.can_assign(passenger, seat, block)) continue;
                    options.emplace_back(seat, block, 0.0);
                }
            }
            if (options.empty()) return result;
            input << options.size() << '\n';
            int incumbent_choice = -1;
            for (size_t option_index = 0; option_index < options.size(); ++option_index) {
                const int seat = std::get<0>(options[option_index]);
                const int block = std::get<1>(options[option_index]);
                const double score = evaluate_individual_score(problem, passenger, seat).total();
                if (seat == incumbent[passenger] && block == incumbent_blocks[passenger])
                    incumbent_choice = static_cast<int>(option_index);
                input << "OPTION " << seat << ' ' << score << ' ' << score << ' ';
                std::vector<int> resources{seat};
                if (item.need_both_empty) {
                    const auto& neighbors = problem.both_side_empty_allow_cross_aisle
                        ? problem.seats[seat].row_neighbors : problem.seats[seat].same_block_neighbors;
                    resources.insert(resources.end(), neighbors.begin(), neighbors.end());
                } else if (block >= 0) {
                    resources.push_back(block);
                }
                input << resources.size();
                for (int resource : resources) input << ' ' << resource;
                std::vector<int> ssr_locations, flagged;
                if (!item.ssr.empty()) {
                    const auto& seat_data = problem.seats[seat];
                    ssr_locations = {location_id({seat_data.row, -1, item.ssr}),
                        location_id({seat_data.row, seat_data.subrow, item.ssr})};
                    for (const auto& ssr : ssr_types) {
                        if (item.same_row_no_other_ssr)
                            flagged.push_back(location_id({seat_data.row, -1, ssr}));
                        if (item.same_subrow_no_other_ssr)
                            flagged.push_back(location_id({seat_data.row, seat_data.subrow, ssr}));
                    }
                }
                input << ' ' << ssr_locations.size();
                for (int location : ssr_locations) input << ' ' << location;
                input << ' ' << flagged.size();
                for (int location : flagged) input << ' ' << location;
                input << ' ' << (item.ssr == "BSCT" ? 1 : 0) << '\n';
            }
            if (incumbent_choice < 0) return result;
            incumbent_choices.push_back(incumbent_choice);
        }
        input << "INCUMBENT " << group.id << ' ' << group_size;
        for (int p = 0; p < group_size; ++p) {
            input << ' ' << group.passengers[p] << ' ' << incumbent_choices[p];
        }
        input << '\n';
    }
    native_solver::MasterProblem master;
    bool deadline_hit = false;
    std::istringstream stream(input.str());
    std::ostringstream kernel_output;
    std::ostringstream kernel_diagnostics;
    const int kernel_status = run_native_pattern_kernel(
        stream, kernel_output, kernel_diagnostics, true, &master, &deadline,
        &deadline_hit
    );
    if (kernel_status != 0 || master.patterns.empty()) {
        result.pattern_count = kernel_status != 0 ? -kernel_status : 0;
        return result;
    }
    result.pattern_count = static_cast<int>(master.patterns.size());
    std::vector<uint64_t> selected;
    std::ostringstream master_output;
    const double remaining = std::chrono::duration<double>(
        deadline - std::chrono::steady_clock::now()).count();
    if (remaining > 0.0
        && native_master::solve(master, master_output, std::min(0.5, remaining), false, &selected) == 0) {
        std::unordered_map<uint64_t, const native_solver::PatternRecord*> by_id;
        for (const auto& pattern : master.patterns) by_id[pattern.id] = &pattern;
        std::vector<int> candidate = incumbent;
        int selected_count = 0;
        for (uint64_t id : selected) {
            auto found = by_id.find(id);
            if (found == by_id.end()) continue;
            ++selected_count;
            for (const auto& assignment : found->second->assignments)
                if (assignment.first >= 0 && assignment.first < static_cast<int>(candidate.size()))
                    candidate[assignment.first] = assignment.second;
        }
        if (selected_count == static_cast<int>(problem.groups.size())
            && validate_complete_assignment(problem, candidate) == 0
            && evaluate_soft_score(problem, candidate) >= evaluate_soft_score(problem, incumbent) - 1e-9) {
            result.passenger_to_seat = std::move(candidate);
            result.complete = true;
            result.score = evaluate_soft_score(problem, result.passenger_to_seat);
            result.selected_pattern_count = selected_count;
        }
    }
    return result;
}

}  // namespace full_cpp
