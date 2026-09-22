#include "native_master_types.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <map>
#include <queue>
#include <set>
#include <string>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>

struct Seat {
    int row;
    double x;
    double y;
    std::unordered_set<int> adjacent;
    std::unordered_set<int> adjacent_cross;
};

struct Option {
    int seat;
    double score;
    double rank_score;
    std::vector<int> resources;
    std::vector<int> ssr_locations;
    std::vector<int> flag_locations;
    bool infant = false;
};

struct Passenger {
    int original_index;
    bool adult;
    bool cared;
    bool allow_cross;
    std::vector<Option> options;
};

struct State {
    std::vector<int> choices;
    std::vector<uint64_t> resources;
    std::vector<uint16_t> ssr_counts;
    std::vector<uint8_t> flagged;
    double individual_score = 0.0;
    double rank_score = 0.0;
};

struct Pattern {
    double score;
    double master_cost;
    std::vector<int> choices;
    std::vector<int> seats;
    std::vector<int> resources;
    std::vector<std::pair<int, int>> ssr_all;
    std::vector<int> ssr_flagged;
    std::vector<int> infant_seats;
    double discovery_ms = 0.0;
    double hint = 0.0;
    int hint_index = -1;
    int variant = -1;
    std::string source = "incumbent";
};

struct GroupInput {
    int group_id;
    int row_radius;
    int beam_width;
    int pattern_limit;
    int ranking_variants;
    std::vector<double> hints;
    std::vector<Passenger> passengers;
    std::vector<int> incumbent_choices;
};

struct GroupWork {
    std::vector<Pattern> combined;
    std::set<std::vector<int>> signatures;
};

using BabyPair = native_solver::BabyPair;

static bool group_has_ssr(const GroupInput& group) {
    for (const Passenger& passenger : group.passengers) {
        for (const Option& option : passenger.options) {
            if (!option.ssr_locations.empty() || !option.flag_locations.empty()) {
                return true;
            }
        }
    }
    return false;
}

static bool group_has_protection(const GroupInput& group) {
    for (const Passenger& passenger : group.passengers) {
        for (const Option& option : passenger.options) {
            if (option.resources.size() > 1) return true;
        }
    }
    return false;
}

using BeamBucket = std::tuple<int, int, int, int, int, int>;

static int bit_count(const std::vector<uint64_t>& bits) {
    int count = 0;
    for (uint64_t word : bits) {
        while (word) {
            word &= word - 1;
            ++count;
        }
    }
    return count;
}

static BeamBucket beam_bucket(
    const State& state, const std::vector<Passenger>& passengers,
    const std::vector<Seat>& seats, size_t depth
) {
    int min_row = int(seats.size());
    int max_row = -1;
    int last_row = -1;
    for (size_t index = 0; index < depth; ++index) {
        const int row = seats[
            passengers[index].options[state.choices[index]].seat
        ].row;
        min_row = std::min(min_row, row);
        max_row = std::max(max_row, row);
        last_row = row;
    }
    int ssr_count = 0;
    for (uint16_t count : state.ssr_counts) ssr_count += count;
    int flagged_count = 0;
    for (uint8_t flagged : state.flagged) flagged_count += flagged != 0;
    const int protected_count = std::max(0, bit_count(state.resources) - int(depth));
    return {min_row, max_row, last_row, protected_count, ssr_count, flagged_count};
}

static std::vector<uint8_t> select_beam_channels(
    const std::vector<State>& states, const std::vector<Passenger>& passengers,
    const std::vector<Seat>& seats, size_t depth, int beam_width,
    const native_solver::BeamReserveConfig& config
) {
    const size_t total_cap = std::min(states.size(), size_t(std::max(0, beam_width)));
    std::vector<uint8_t> channel(states.size(), 0);
    if (!config.enabled || config.reserve_cap <= 0 || config.per_bucket_cap <= 0
        || total_cap == 0) {
        for (size_t index = 0; index < total_cap; ++index) channel[index] = 1;
        return channel;
    }

    const size_t reserve_cap = std::min(
        total_cap, size_t(std::max(0, config.reserve_cap))
    );
    const size_t main_cap = total_cap - reserve_cap;
    for (size_t index = 0; index < main_cap; ++index) channel[index] = 1;

    std::map<BeamBucket, int> bucket_counts;
    size_t reserved = 0;
    for (size_t index = main_cap; index < states.size() && reserved < reserve_cap;
         ++index) {
        int& bucket_count = bucket_counts[
            beam_bucket(states[index], passengers, seats, depth)
        ];
        if (bucket_count >= config.per_bucket_cap) continue;
        ++bucket_count;
        channel[index] = 2;
        ++reserved;
    }
    for (size_t index = main_cap; index < states.size() && reserved < reserve_cap;
         ++index) {
        if (channel[index]) continue;
        channel[index] = 1;
        ++reserved;
    }

    return channel;
}

static bool bit_test(const std::vector<uint64_t>& bits, int index) {
    return bits[index >> 6] & (uint64_t{1} << (index & 63));
}

static void bit_set(std::vector<uint64_t>& bits, int index) {
    bits[index >> 6] |= uint64_t{1} << (index & 63);
}

static void compact_selected_beam(
    std::vector<State>& states, const std::vector<uint8_t>& channels
) {
    std::vector<State> retained;
    retained.reserve(states.size());
    std::set<std::vector<int>> retained_signatures;
    for (size_t index = 0; index < states.size(); ++index) {
        if (!channels[index]
            || !retained_signatures.insert(states[index].choices).second) continue;
        retained.push_back(std::move(states[index]));
    }
    states.swap(retained);
}

#ifndef NATIVE_PATTERN_KERNEL_LIBRARY
static bool beam_reserve_self_test() {
    std::vector<Seat> seats(5);
    for (int index = 0; index < 5; ++index) seats[index].row = 1 + index / 2;
    std::vector<Passenger> passengers(2);
    for (Passenger& passenger : passengers) {
        for (int seat = 0; seat < 5; ++seat) {
            Option option;
            option.seat = seat;
            option.resources = {seat};
            passenger.options.push_back(option);
        }
    }
    std::vector<State> states(5);
    const int choices[5][2] = {{0, 1}, {1, 0}, {0, 2}, {1, 2}, {0, 4}};
    for (int index = 0; index < 5; ++index) {
        states[index].choices = {choices[index][0], choices[index][1]};
        states[index].resources.assign(1, 0);
        bit_set(states[index].resources, choices[index][0]);
        bit_set(states[index].resources, choices[index][1]);
        states[index].ssr_counts.assign(1, 0);
        states[index].flagged.assign(1, 0);
    }
    native_solver::BeamReserveConfig disabled;
    if (select_beam_channels(states, passengers, seats, 2, 4, disabled)
        != std::vector<uint8_t>({1, 1, 1, 1, 0})) return false;
    native_solver::BeamReserveConfig enabled{true, 2, 1};
    const auto selected = select_beam_channels(
        states, passengers, seats, 2, 4, enabled
    );
    if (selected != std::vector<uint8_t>({1, 1, 2, 0, 2})) return false;
    if (selected != select_beam_channels(states, passengers, seats, 2, 4, enabled)) {
        return false;
    }
    State protected_state = states[2];
    bit_set(protected_state.resources, 3);
    if (beam_bucket(states[2], passengers, seats, 2)
        == beam_bucket(protected_state, passengers, seats, 2)) return false;
    State ssr_state = states[2];
    ssr_state.ssr_counts[0] = 1;
    ssr_state.flagged[0] = 1;
    if (beam_bucket(states[2], passengers, seats, 2)
        == beam_bucket(ssr_state, passengers, seats, 2)) return false;
    std::vector<State> merged = states;
    merged[4].choices = merged[2].choices;
    compact_selected_beam(merged, selected);
    return merged.size() == 3;
}
#endif

static bool option_compatible(const State& state, const Option& option) {
    for (int resource : option.resources) {
        if (bit_test(state.resources, resource)) return false;
    }
    for (int location : option.ssr_locations) {
        if (state.flagged[location] && state.ssr_counts[location] >= 1) return false;
    }
    for (int location : option.flag_locations) {
        int added = int(std::count(
            option.ssr_locations.begin(), option.ssr_locations.end(), location
        ));
        if (state.ssr_counts[location] + added > 1) return false;
    }
    return true;
}

static void hash_integer(uint64_t& hash, uint64_t value) {
    for (int byte = 0; byte < 8; ++byte) {
        hash ^= value & 0xffULL;
        hash *= 1099511628211ULL;
        value >>= 8;
    }
}

static uint64_t stable_pattern_id(
    int group_id, const Pattern& pattern, const std::vector<Passenger>& passengers
) {
    uint64_t hash = 1469598103934665603ULL;
    hash_integer(hash, uint64_t(group_id));
    hash_integer(hash, uint64_t(pattern.choices.size()));
    for (size_t index = 0; index < pattern.choices.size(); ++index) {
        const Option& option = passengers[index].options[pattern.choices[index]];
        std::vector<int> blocked;
        for (int resource : option.resources) {
            if (resource != option.seat) blocked.push_back(resource);
        }
        std::sort(blocked.begin(), blocked.end());
        hash_integer(hash, uint64_t(passengers[index].original_index));
        hash_integer(hash, uint64_t(option.seat));
        hash_integer(hash, uint64_t(blocked.size()));
        for (int seat : blocked) hash_integer(hash, uint64_t(seat));
    }
    return hash;
}

static double perturbed_rank(const Option& option, int variant) {
    if (variant <= 0) return option.rank_score;
    const double amplitude = 0.5 * (1 << ((variant - 1) % 4));
    uint64_t hash = 1469598103934665603ULL;
    hash ^= uint64_t(option.seat + 1) * 1099511628211ULL;
    for (int resource : option.resources) {
        hash ^= uint64_t(resource + 17);
        hash *= 1099511628211ULL;
    }
    hash ^= uint64_t(variant) * 0x9e3779b97f4a7c15ULL;
    const double unit = double(hash % 2000001ULL) / 1000000.0 - 1.0;
    return option.rank_score + amplitude * unit;
}

static std::vector<int> diagnostic_candidates(
    const Passenger& passenger, const std::vector<Seat>& seats, double hint,
    int row_radius, int ranking_variant
) {
    std::vector<int> candidates;
    for (size_t option_index = 0; option_index < passenger.options.size(); ++option_index) {
        const int row = seats[passenger.options[option_index].seat].row;
        if (std::abs(row - hint) <= row_radius) candidates.push_back(int(option_index));
    }
    if (candidates.empty()) {
        for (size_t option_index = 0; option_index < passenger.options.size(); ++option_index) {
            candidates.push_back(int(option_index));
        }
    }
    std::sort(candidates.begin(), candidates.end(), [&](int left, int right) {
        const Option& a = passenger.options[left];
        const Option& b = passenger.options[right];
        const double rank_a = perturbed_rank(a, ranking_variant)
            - 0.15 * std::abs(seats[a.seat].row - hint);
        const double rank_b = perturbed_rank(b, ranking_variant)
            - 0.15 * std::abs(seats[b.seat].row - hint);
        return rank_a > rank_b;
    });
    if (candidates.size() > 48) candidates.resize(48);
    return candidates;
}

struct CompletionEstimate {
    bool l1_valid = true;
    double l1_score = 0.0;
    bool l2_valid = true;
    double l2_score = 0.0;
};

static CompletionEstimate completion_estimate(
    const State& state, const std::vector<Passenger>& passengers,
    const std::vector<Seat>& seats, size_t first_remaining, double hint,
    int row_radius, int ranking_variant
) {
    CompletionEstimate result;
    std::vector<std::vector<std::pair<int, double>>> options_by_passenger;
    std::set<int> seat_set;
    for (size_t passenger_index = first_remaining;
         passenger_index < passengers.size(); ++passenger_index) {
        const Passenger& passenger = passengers[passenger_index];
        std::map<int, double> best_by_seat;
        double best = -1e100;
        for (int option_index : diagnostic_candidates(
                 passenger, seats, hint, row_radius, ranking_variant)) {
            const Option& option = passenger.options[option_index];
            if (!option_compatible(state, option)) continue;
            best = std::max(best, option.score);
            auto found = best_by_seat.find(option.seat);
            if (found == best_by_seat.end() || option.score > found->second) {
                best_by_seat[option.seat] = option.score;
            }
        }
        if (best <= -1e99) {
            result.l1_valid = false;
            result.l2_valid = false;
            return result;
        }
        result.l1_score += best;
        options_by_passenger.emplace_back(best_by_seat.begin(), best_by_seat.end());
        for (const auto& item : best_by_seat) seat_set.insert(item.first);
    }
    const int passenger_count = int(options_by_passenger.size());
    if (passenger_count == 0) return result;
    std::vector<int> seat_ids(seat_set.begin(), seat_set.end());
    if (int(seat_ids.size()) < passenger_count) {
        result.l2_valid = false;
        return result;
    }
    std::map<int, int> seat_column;
    for (size_t index = 0; index < seat_ids.size(); ++index) {
        seat_column[seat_ids[index]] = int(index);
    }
    const int column_count = int(seat_ids.size());
    const double invalid_cost = 1e12;
    std::vector<std::vector<double>> cost(
        passenger_count, std::vector<double>(column_count, invalid_cost)
    );
    for (int passenger = 0; passenger < passenger_count; ++passenger) {
        for (const auto& item : options_by_passenger[passenger]) {
            cost[passenger][seat_column[item.first]] = -item.second;
        }
    }
    std::vector<double> u(passenger_count + 1), v(column_count + 1);
    std::vector<int> matched_row(column_count + 1), way(column_count + 1);
    for (int row = 1; row <= passenger_count; ++row) {
        matched_row[0] = row;
        int column0 = 0;
        std::vector<double> minimum(column_count + 1, invalid_cost);
        std::vector<uint8_t> used(column_count + 1, 0);
        do {
            used[column0] = 1;
            const int row0 = matched_row[column0];
            double delta = invalid_cost;
            int column1 = 0;
            for (int column = 1; column <= column_count; ++column) {
                if (used[column]) continue;
                const double current = cost[row0 - 1][column - 1]
                    - u[row0] - v[column];
                if (current < minimum[column]) {
                    minimum[column] = current;
                    way[column] = column0;
                }
                if (minimum[column] < delta) {
                    delta = minimum[column];
                    column1 = column;
                }
            }
            for (int column = 0; column <= column_count; ++column) {
                if (used[column]) {
                    u[matched_row[column]] += delta;
                    v[column] -= delta;
                } else {
                    minimum[column] -= delta;
                }
            }
            column0 = column1;
        } while (matched_row[column0] != 0);
        do {
            const int column1 = way[column0];
            matched_row[column0] = matched_row[column1];
            column0 = column1;
        } while (column0 != 0);
    }
    double assignment_cost = 0.0;
    for (int column = 1; column <= column_count; ++column) {
        if (!matched_row[column]) continue;
        const double selected = cost[matched_row[column] - 1][column - 1];
        if (selected >= invalid_cost / 2) {
            result.l2_valid = false;
            return result;
        }
        assignment_cost += selected;
    }
    result.l2_score = -assignment_cost;
    return result;
}

static double compactness_score(
    const std::vector<int>& seats, const std::vector<Seat>& seat_data,
    double weight, double x_factor, double y_factor
) {
    if (seats.size() <= 1) return 0.0;
    double mean_x = 0.0, mean_y = 0.0;
    for (int seat : seats) {
        mean_x += seat_data[seat].x;
        mean_y += seat_data[seat].y;
    }
    mean_x /= seats.size();
    mean_y /= seats.size();
    double max_x = 0.0, max_y = 0.0;
    for (int seat : seats) {
        max_x = std::max(max_x, std::abs(seat_data[seat].x - mean_x));
        max_y = std::max(max_y, std::abs(seat_data[seat].y - mean_y));
    }
    return weight * (x_factor * max_x + y_factor * max_y);
}

static bool caregiver_ok(
    const std::vector<Passenger>& passengers, const std::vector<int>& choices,
    const std::vector<Seat>& seats
) {
    for (size_t index = 0; index < passengers.size(); ++index) {
        if (!passengers[index].cared) continue;
        int cared_seat = passengers[index].options[choices[index]].seat;
        const auto& neighbors = passengers[index].allow_cross
            ? seats[cared_seat].adjacent_cross : seats[cared_seat].adjacent;
        bool found = false;
        for (size_t other = 0; other < passengers.size(); ++other) {
            if (other == index || !passengers[other].adult) continue;
            int other_seat = passengers[other].options[choices[other]].seat;
            if (neighbors.count(other_seat)) {
                found = true;
                break;
            }
        }
        if (!found) return false;
    }
    return true;
}

static bool pattern_from_choices(
    const std::vector<Passenger>& passengers, const std::vector<Seat>& seats,
    const std::vector<BabyPair>& baby_pairs, int resource_count,
    int location_count, const std::vector<int>& choices, double compact_weight,
    double x_factor, double y_factor, Pattern& pattern
) {
    if (choices.size() != passengers.size()) return false;
    State state;
    state.choices = choices;
    state.resources.assign((resource_count + 63) / 64, 0);
    state.ssr_counts.assign(location_count, 0);
    state.flagged.assign(location_count, 0);
    for (size_t index = 0; index < passengers.size(); ++index) {
        if (choices[index] < 0
            || choices[index] >= int(passengers[index].options.size())) return false;
        const Option& option = passengers[index].options[choices[index]];
        if (!option_compatible(state, option)) return false;
        state.individual_score += option.score;
        for (int resource : option.resources) bit_set(state.resources, resource);
        for (int location : option.ssr_locations) ++state.ssr_counts[location];
        for (int location : option.flag_locations) state.flagged[location] = 1;
    }
    if (!caregiver_ok(passengers, choices, seats)) return false;
    pattern.choices = choices;
    pattern.seats.resize(passengers.size());
    for (size_t index = 0; index < passengers.size(); ++index) {
        pattern.seats[index] = passengers[index].options[choices[index]].seat;
    }
    pattern.score = state.individual_score + compactness_score(
        pattern.seats, seats, compact_weight, x_factor, y_factor
    );
    pattern.master_cost = -pattern.score;
    for (int resource = 0; resource < resource_count; ++resource) {
        if (bit_test(state.resources, resource)) pattern.resources.push_back(resource);
    }
    for (int location = 0; location < location_count; ++location) {
        if (state.ssr_counts[location]) {
            pattern.ssr_all.emplace_back(location, state.ssr_counts[location]);
        }
        if (state.flagged[location]) pattern.ssr_flagged.push_back(location);
    }
    for (size_t index = 0; index < passengers.size(); ++index) {
        const Option& option = passengers[index].options[choices[index]];
        if (option.infant) pattern.infant_seats.push_back(option.seat);
    }
    for (const BabyPair& pair : baby_pairs) {
        if (std::find(pattern.infant_seats.begin(), pattern.infant_seats.end(), pair.infant_seat)
                != pattern.infant_seats.end()
            && std::find(pattern.seats.begin(), pattern.seats.end(), pair.occupant_seat)
                != pattern.seats.end()) {
            pattern.master_cost -= pair.cost;
        }
    }
    return true;
}

static std::vector<Pattern> search_hint(
    const std::vector<Passenger>& passengers, const std::vector<Seat>& seats,
    const std::vector<BabyPair>& baby_pairs, int resource_count,
    int location_count, int group_id, double hint, int hint_index, int row_radius,
    int beam_width, int pattern_limit, int ranking_variant, double compact_weight,
    double x_factor, double y_factor,
    const std::chrono::steady_clock::time_point* deadline,
    const std::chrono::steady_clock::time_point& diagnostic_start,
    const std::unordered_set<uint64_t>& watched_pattern_ids,
    int group_number, std::vector<native_solver::PatternDiagnostic>& pattern_diagnostics,
    uint64_t& expansion_count,
    const std::vector<native_solver::TargetTraceSpec>& trace_specs,
    std::vector<native_solver::TargetTraceStep>& trace_steps,
    std::vector<native_solver::LayerStateSnapshot>& layer_snapshots,
    const native_solver::BeamReserveConfig& beam_reserve,
    const native_solver::GroupResourcePressure* resource_pressure,
    bool diverse_complete_retention, uint64_t& beam_prune_count,
    int& complete_state_count, int& legal_complete_pattern_count,
    int& unique_complete_resource_signatures, bool& search_completed,
    std::string& termination_reason
) {
    std::vector<State> beam(1);
    beam[0].choices.assign(passengers.size(), -1);
    beam[0].resources.assign((resource_count + 63) / 64, 0);
    beam[0].ssr_counts.assign(location_count, 0);
    beam[0].flagged.assign(location_count, 0);
    for (size_t passenger_index = 0; passenger_index < passengers.size(); ++passenger_index) {
        if (deadline && std::chrono::steady_clock::now() >= *deadline) {
            search_completed = false;
            termination_reason = "deadline_before_depth";
            for (const auto& spec : trace_specs) {
                trace_steps.push_back(native_solver::TargetTraceStep{
                    spec.pattern_id, group_id, hint_index, ranking_variant,
                    int(passenger_index), int(passengers.size()),
                    spec.choices[passenger_index], -1, 0, false, false, false,
                    false, false, -1, 0, false, beam_width, 0.0, true
                });
            }
            return {};
        }
        const Passenger& passenger = passengers[passenger_index];
        std::vector<int> candidates;
        for (size_t option_index = 0; option_index < passenger.options.size(); ++option_index) {
            int row = seats[passenger.options[option_index].seat].row;
            if (std::abs(row - hint) <= row_radius) candidates.push_back(int(option_index));
        }
        if (candidates.empty()) {
            for (size_t option_index = 0; option_index < passenger.options.size(); ++option_index) {
                candidates.push_back(int(option_index));
            }
        }
        std::sort(candidates.begin(), candidates.end(), [&](int left, int right) {
            const Option& a = passenger.options[left];
            const Option& b = passenger.options[right];
            double rank_a = perturbed_rank(a, ranking_variant)
                - 0.15 * std::abs(seats[a.seat].row - hint);
            double rank_b = perturbed_rank(b, ranking_variant)
                - 0.15 * std::abs(seats[b.seat].row - hint);
            return rank_a > rank_b;
        });
        const std::vector<int> candidates_before_cap = candidates;
        if (candidates.size() > 48) candidates.resize(48);
        const auto prefix_matches = [&](const State& state, const auto& spec, size_t depth) {
            for (size_t index = 0; index < depth; ++index) {
                if (state.choices[index] != spec.choices[index]) return false;
            }
            return true;
        };
        std::vector<native_solver::TargetTraceStep> depth_traces;
        depth_traces.reserve(trace_specs.size());
        for (const auto& spec : trace_specs) {
            const int target_option = spec.choices[passenger_index];
            const auto domain_position = std::find(
                candidates_before_cap.begin(), candidates_before_cap.end(), target_option
            );
            const bool in_domain = domain_position != candidates_before_cap.end();
            const bool after_cap = std::find(
                candidates.begin(), candidates.end(), target_option
            ) != candidates.end();
            const auto parent = std::find_if(
                beam.begin(), beam.end(), [&](const State& state) {
                    return prefix_matches(state, spec, passenger_index);
                }
            );
            depth_traces.push_back(native_solver::TargetTraceStep{
                spec.pattern_id, group_id, hint_index, ranking_variant,
                int(passenger_index), int(passengers.size()), target_option,
                in_domain ? int(domain_position - candidates_before_cap.begin()) + 1 : -1,
                int(candidates_before_cap.size()), in_domain, after_cap,
                parent != beam.end(), false, false, -1, 0, false, beam_width,
                0.0, false
            });
        }
        std::vector<State> next;
        next.reserve(std::min<size_t>(beam.size() * candidates.size(), beam_width * 3));
        bool final_depth_cutoff = false;
        for (const State& state : beam) {
            for (int option_index : candidates) {
                if ((++expansion_count & 4095ULL) == 0 && deadline
                    && std::chrono::steady_clock::now() >= *deadline) {
                    search_completed = false;
                    termination_reason = "deadline_during_expansion";
                    for (auto& trace : depth_traces) {
                        trace.deadline_hit = true;
                        trace_steps.push_back(trace);
                    }
                    if (passenger_index + 1 == passengers.size() && !next.empty()) {
                        final_depth_cutoff = true;
                        break;
                    }
                    return {};
                }
                const Option& option = passenger.options[option_index];
                if (!option_compatible(state, option)) continue;
                State child = state;
                child.choices[passenger_index] = option_index;
                child.individual_score += option.score;
                child.rank_score += perturbed_rank(option, ranking_variant);
                if (resource_pressure) {
                    for (int resource : option.resources) {
                        if (resource >= 0 && resource < int(
                            resource_pressure->seat_resources.size()
                        )) {
                            child.rank_score -=
                                resource_pressure->seat_resources[resource];
                        }
                    }
                    for (int location : option.ssr_locations) {
                        if (location >= 0 && location < int(
                            resource_pressure->ssr_resources.size()
                        )) {
                            child.rank_score -=
                                resource_pressure->ssr_resources[location];
                        }
                    }
                }
                for (int resource : option.resources) bit_set(child.resources, resource);
                for (int location : option.ssr_locations) ++child.ssr_counts[location];
                for (int location : option.flag_locations) child.flagged[location] = 1;
                next.push_back(std::move(child));
            }
            if (final_depth_cutoff) break;
        }
        const int reserve_cap = std::max(
            0, std::min(beam_reserve.reserve_cap, beam_width)
        );
        const size_t main_cap = std::min(
            next.size(), size_t(std::max(0, beam_width - reserve_cap))
        );
        const std::vector<uint8_t> retained_channels = beam_reserve.enabled
            ? select_beam_channels(
                next, passengers, seats, passenger_index + 1,
                beam_width, beam_reserve
            ) : std::vector<uint8_t>();
        for (size_t trace_index = 0; trace_index < trace_specs.size(); ++trace_index) {
            auto& trace = depth_traces[trace_index];
            const auto& spec = trace_specs[trace_index];
            trace.expanded = trace.parent_prefix_in_beam && trace.after_candidate_cap;
            const auto child = std::find_if(
                next.begin(), next.end(), [&](const State& state) {
                    return prefix_matches(state, spec, passenger_index + 1);
                }
            );
            trace.child_prefix_generated = child != next.end();
            if (child != next.end()) {
                trace.child_rank_score = child->rank_score;
                trace.child_individual_score = child->individual_score;
            }
        }
        if (next.empty()) {
            for (auto& trace : depth_traces) trace_steps.push_back(trace);
            return {};
        }
        std::sort(next.begin(), next.end(), [](const State& a, const State& b) {
            return a.rank_score > b.rank_score;
        });
        for (const auto& spec : trace_specs) {
            if (spec.snapshot_hint_index != hint_index
                || spec.snapshot_variant != ranking_variant
                || spec.snapshot_depth != int(passenger_index)) continue;
            for (size_t state_index = 0; state_index < next.size(); ++state_index) {
                const State& state = next[state_index];
                const CompletionEstimate estimate = completion_estimate(
                    state, passengers, seats, passenger_index + 1, hint,
                    row_radius, ranking_variant
                );
                const double existing_dual_adjustment =
                    state.rank_score - state.individual_score;
                layer_snapshots.push_back(native_solver::LayerStateSnapshot{
                    spec.pattern_id, group_id, hint_index, ranking_variant,
                    int(passenger_index), int(state_index) + 1, int(next.size()),
                    state.individual_score, state.rank_score,
                    estimate.l1_valid, estimate.l1_score,
                    state.individual_score + estimate.l1_score + existing_dual_adjustment,
                    estimate.l2_valid, estimate.l2_score,
                    state.individual_score + estimate.l2_score + existing_dual_adjustment,
                    std::vector<int>(
                        state.choices.begin(), state.choices.begin() + passenger_index + 1
                    )
                });
            }
        }
        const size_t retained_count = std::min(next.size(), size_t(beam_width));
        const size_t cutoff_index = retained_count - 1;
        const size_t median_index = (retained_count - 1) / 2;
        const size_t top_decile_index = std::max<size_t>(
            1, size_t(std::ceil(0.1 * retained_count))
        ) - 1;
        const size_t band_radius = std::max<size_t>(1, size_t(beam_width) / 10);
        const size_t band_begin = cutoff_index > band_radius
            ? cutoff_index - band_radius : 0;
        const size_t band_end = std::min(next.size(), cutoff_index + band_radius + 1);
        std::set<std::vector<uint64_t>> band_resource_signatures;
        std::map<std::vector<int>, int> band_row_signature_counts;
        int band_max_row_multiplicity = 0;
        for (size_t index = band_begin; index < band_end; ++index) {
            band_resource_signatures.insert(next[index].resources);
            std::vector<int> row_signature;
            row_signature.reserve(passenger_index + 1);
            for (size_t prefix = 0; prefix <= passenger_index; ++prefix) {
                row_signature.push_back(seats[
                    passengers[prefix].options[next[index].choices[prefix]].seat
                ].row);
            }
            std::sort(row_signature.begin(), row_signature.end());
            band_max_row_multiplicity = std::max(
                band_max_row_multiplicity, ++band_row_signature_counts[row_signature]
            );
        }
        for (size_t trace_index = 0; trace_index < trace_specs.size(); ++trace_index) {
            auto& trace = depth_traces[trace_index];
            const auto& spec = trace_specs[trace_index];
            const auto child = std::find_if(
                next.begin(), next.end(), [&](const State& state) {
                    return prefix_matches(state, spec, passenger_index + 1);
                }
            );
            trace.child_count_before_beam_cap = int(next.size());
            if (child != next.end()) {
                trace.child_rank_before_beam_cap = int(child - next.begin()) + 1;
                const size_t child_index = size_t(child - next.begin());
                trace.survived_main_beam = beam_reserve.enabled
                    ? child_index < main_cap : child_index < size_t(beam_width);
                trace.survived_beam = beam_reserve.enabled
                    ? retained_channels[child_index] != 0
                    : child_index < size_t(beam_width);
                trace.survived_reserve_beam = beam_reserve.enabled
                    && retained_channels[child_index] == 2;
            }
            trace.cutoff_rank_score = next[cutoff_index].rank_score;
            trace.cutoff_individual_score = next[cutoff_index].individual_score;
            trace.median_rank_score = next[median_index].rank_score;
            trace.median_individual_score = next[median_index].individual_score;
            trace.top_decile_rank_score = next[top_decile_index].rank_score;
            trace.top_decile_individual_score = next[top_decile_index].individual_score;
            trace.cutoff_band_state_count = int(band_end - band_begin);
            trace.cutoff_band_unique_resource_signatures = int(
                band_resource_signatures.size()
            );
            trace.cutoff_band_unique_row_signatures = int(
                band_row_signature_counts.size()
            );
            trace.cutoff_band_max_row_signature_multiplicity =
                band_max_row_multiplicity;
            trace_steps.push_back(trace);
        }
        if (beam_reserve.enabled) {
            compact_selected_beam(next, retained_channels);
        } else if (next.size() > size_t(beam_width)) {
            beam_prune_count += next.size() - size_t(beam_width);
            next.resize(beam_width);
        }
        beam.swap(next);
    }
    std::vector<Pattern> patterns;
    complete_state_count = int(beam.size());
    size_t completed_states = 0;
    for (const State& state : beam) {
        if ((++completed_states & 255ULL) == 0 && deadline
            && std::chrono::steady_clock::now() >= *deadline) {
            search_completed = false;
            termination_reason = "deadline_during_materialization";
            break;
        }
        if (!caregiver_ok(passengers, state.choices, seats)) continue;
        Pattern pattern;
        pattern.choices = state.choices;
        pattern.seats.resize(passengers.size());
        for (size_t index = 0; index < passengers.size(); ++index) {
            pattern.seats[index] = passengers[index].options[state.choices[index]].seat;
        }
        pattern.score = state.individual_score + compactness_score(
            pattern.seats, seats, compact_weight, x_factor, y_factor
        );
        pattern.master_cost = -pattern.score;
        for (int resource = 0; resource < resource_count; ++resource) {
            if (bit_test(state.resources, resource)) pattern.resources.push_back(resource);
        }
        for (int location = 0; location < location_count; ++location) {
            if (state.ssr_counts[location]) {
                pattern.ssr_all.emplace_back(location, state.ssr_counts[location]);
            }
            if (state.flagged[location]) pattern.ssr_flagged.push_back(location);
        }
        for (size_t index = 0; index < passengers.size(); ++index) {
            const Option& option = passengers[index].options[state.choices[index]];
            if (option.infant) pattern.infant_seats.push_back(option.seat);
        }
        for (const BabyPair& pair : baby_pairs) {
            if (
                std::find(
                    pattern.infant_seats.begin(), pattern.infant_seats.end(),
                    pair.infant_seat
                ) != pattern.infant_seats.end()
                && std::find(
                    pattern.seats.begin(), pattern.seats.end(), pair.occupant_seat
                ) != pattern.seats.end()
            ) {
                pattern.master_cost -= pair.cost;
            }
        }
        patterns.push_back(std::move(pattern));
    }
    legal_complete_pattern_count = int(patterns.size());
    {
        std::set<std::tuple<
            std::vector<int>, std::vector<std::pair<int, int>>, std::vector<int>
        >> signatures;
        for (const Pattern& pattern : patterns) {
            signatures.insert({
                pattern.resources, pattern.ssr_all, pattern.ssr_flagged
            });
        }
        unique_complete_resource_signatures = int(signatures.size());
    }
    std::sort(patterns.begin(), patterns.end(), [](const Pattern& a, const Pattern& b) {
        return a.score > b.score;
    });
    const double discovered_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - diagnostic_start
    ).count();
    for (Pattern& pattern : patterns) {
        pattern.discovery_ms = discovered_ms;
        pattern.hint = hint;
        pattern.hint_index = hint_index;
        pattern.variant = ranking_variant;
        pattern.source = "hint_beam";
    }
    for (size_t index = size_t(pattern_limit); index < patterns.size(); ++index) {
        const Pattern& pattern = patterns[index];
        const uint64_t identifier = stable_pattern_id(group_id, pattern, passengers);
        if (!watched_pattern_ids.count(identifier)) continue;
        pattern_diagnostics.push_back(native_solver::PatternDiagnostic{
            identifier, group_number, group_id, pattern.discovery_ms, pattern.hint,
            pattern.hint_index, pattern.variant, int(passengers.size()), false,
            !pattern.ssr_all.empty() || !pattern.ssr_flagged.empty(),
            pattern.resources.size() > passengers.size(), false,
            pattern.source, "per_hint_topk_truncation"
        });
    }
    if (diverse_complete_retention && patterns.size() > size_t(pattern_limit)) {
        using Signature = std::tuple<
            std::vector<int>, std::vector<std::pair<int, int>>, std::vector<int>
        >;
        std::set<Signature> signatures;
        std::vector<uint8_t> selected(patterns.size(), 0);
        std::vector<Pattern> retained;
        retained.reserve(pattern_limit);
        for (size_t index = 0;
             index < patterns.size() && retained.size() < size_t(pattern_limit);
             ++index) {
            const Signature signature{
                patterns[index].resources, patterns[index].ssr_all,
                patterns[index].ssr_flagged
            };
            if (!signatures.insert(signature).second) continue;
            selected[index] = 1;
            retained.push_back(std::move(patterns[index]));
        }
        for (size_t index = 0;
             index < patterns.size() && retained.size() < size_t(pattern_limit);
             ++index) {
            if (selected[index]) continue;
            retained.push_back(std::move(patterns[index]));
        }
        std::sort(retained.begin(), retained.end(), [](const Pattern& a, const Pattern& b) {
            return a.score > b.score;
        });
        patterns.swap(retained);
    } else if (patterns.size() > size_t(pattern_limit)) {
        patterns.resize(pattern_limit);
    }
    return patterns;
}

void native_solver::write_master_problem(
    std::ostream& output, const MasterProblem& problem
) {
    output << "MASTER_V1 " << problem.group_count << ' ' << problem.seat_count
           << ' ' << problem.ssr_count << ' ' << problem.baby_pairs.size()
           << ' ' << int(problem.full_load) << ' ' << problem.patterns.size()
           << '\n';
    for (int group = 0; group < problem.group_count; ++group) {
        output << "GROUP " << group << ' ' << problem.group_ids[group] << '\n';
    }
    for (const native_solver::BabyPair& pair : problem.baby_pairs) {
        output << "BABY " << pair.infant_seat << ' ' << pair.occupant_seat
               << ' ' << std::setprecision(17) << pair.cost << '\n';
    }
    for (const native_solver::PatternRecord& record : problem.patterns) {
        output << "PATTERN " << record.id << ' ' << record.group << ' '
               << std::setprecision(17) << record.master_cost << ' '
               << int(record.hard_keep) << ' ' << record.seat_resources.size();
        for (int value : record.seat_resources) output << ' ' << value;
        output << ' ' << record.ssr_all.size();
        for (const auto& item : record.ssr_all) {
            output << ' ' << item.first << ' ' << item.second;
        }
        output << ' ' << record.ssr_flagged.size();
        for (const auto& item : record.ssr_flagged) {
            output << ' ' << item.first << ' ' << item.second;
        }
        output << ' ' << record.infant_seats.size();
        for (int value : record.infant_seats) output << ' ' << value;
        output << ' ' << record.occupied_seats.size();
        for (int value : record.occupied_seats) output << ' ' << value;
        output << ' ' << record.assignments.size();
        for (const auto& item : record.assignments) {
            output << ' ' << item.first << ' ' << item.second;
        }
        output << ' ' << record.blocked_by.size();
        for (const auto& item : record.blocked_by) {
            output << ' ' << item.first << ' ' << item.second;
        }
        output << '\n';
    }
}

int run_native_pattern_kernel(
    std::istream& input, std::ostream& output, std::ostream& diagnostics,
    bool emit_master, native_solver::MasterProblem* direct_master,
    const std::chrono::steady_clock::time_point* generation_deadline,
    bool* generation_deadline_hit,
    const std::vector<uint64_t>* watched_pattern_ids,
    native_solver::PatternSchedule schedule,
    bool collect_task_diagnostics,
    const std::vector<native_solver::TargetTraceSpec>* target_trace_specs,
    const native_solver::BeamReserveConfig& beam_reserve,
    const native_solver::PatternGenerationScope& generation_scope,
    double rr_task_admission_guard_seconds
) {
    std::ios::sync_with_stdio(false);
    const auto kernel_started = std::chrono::steady_clock::now();
    std::string tag;
    int seat_count, resource_count, location_count, group_count, baby_pair_count = 0;
    int full_load = 0;
    double compact_weight, x_factor, y_factor;
    if (!(input >> tag)) return 2;
    const bool protocol_v2 = tag == "HEADER_V2";
    const bool protocol_v1 = protocol_v2 || tag == "HEADER_V1";
    if (!protocol_v1 && tag != "HEADER") return 2;
    if (!(input >> seat_count >> resource_count >> location_count >> group_count)) return 2;
    if (protocol_v1 && !(input >> baby_pair_count)) return 2;
    if (protocol_v2 && !(input >> full_load)) return 2;
    if (!(input >> compact_weight >> x_factor >> y_factor)) return 2;
    std::vector<Seat> seats(seat_count);
    for (int i = 0; i < seat_count; ++i) {
        int index, adjacent_count, cross_count;
        input >> tag >> index >> seats[index].row >> seats[index].x >> seats[index].y;
        input >> adjacent_count;
        for (int j = 0, value; j < adjacent_count; ++j) {
            input >> value;
            seats[index].adjacent.insert(value);
        }
        input >> cross_count;
        for (int j = 0, value; j < cross_count; ++j) {
            input >> value;
            seats[index].adjacent_cross.insert(value);
        }
    }
    std::vector<BabyPair> baby_pairs(baby_pair_count);
    for (BabyPair& pair : baby_pairs) {
        if (!(input >> tag >> pair.infant_seat >> pair.occupant_seat >> pair.cost)
            || tag != "BABY") return 2;
    }
    auto started = std::chrono::steady_clock::now();
    size_t total_patterns = 0;
    std::vector<int> group_ids(group_count);
    std::vector<native_solver::PatternRecord> master_patterns;
    std::vector<native_solver::PatternDiagnostic> pattern_diagnostics;
    std::vector<native_solver::TaskDiagnostic> task_diagnostics;
    std::vector<native_solver::Pass0GroupDiagnostic> pass0_group_diagnostics;
    std::vector<native_solver::TargetTraceStep> target_trace_steps;
    std::vector<native_solver::LayerStateSnapshot> layer_state_snapshots;
    std::map<std::tuple<int, int, int>, size_t> task_diagnostic_index;
    std::unordered_set<uint64_t> watched_ids;
    if (watched_pattern_ids) {
        watched_ids.insert(
            watched_pattern_ids->begin(), watched_pattern_ids->end()
        );
    }
    const bool build_master = emit_master || direct_master != nullptr;
    bool deadline_hit = false;
    bool stop_search = false;
    bool admission_guard_stop = false;
    std::vector<GroupInput> groups(group_count);
    for (int group_number = 0; group_number < group_count; ++group_number) {
        GroupInput& group = groups[group_number];
        int group_id, passenger_count, hint_count, row_radius, beam_width;
        int pattern_limit, ranking_variants;
        input >> tag >> group_id >> passenger_count >> hint_count >> row_radius
                 >> beam_width >> pattern_limit >> ranking_variants;
        group.group_id = group_id;
        group.row_radius = row_radius;
        group.beam_width = beam_width;
        group.pattern_limit = generation_scope.pattern_limit_override > 0
            ? generation_scope.pattern_limit_override : pattern_limit;
        group.ranking_variants = std::max(1, ranking_variants);
        group.hints.resize(hint_count);
        for (double& hint : group.hints) input >> hint;
        group.passengers.resize(passenger_count);
        for (int p = 0; p < passenger_count; ++p) {
            int adult, cared, allow_cross, option_count;
            Passenger& passenger = group.passengers[p];
            input >> tag >> passenger.original_index >> adult >> cared
                     >> allow_cross >> option_count;
            passenger.adult = adult;
            passenger.cared = cared;
            passenger.allow_cross = allow_cross;
            passenger.options.resize(option_count);
            for (Option& option : passenger.options) {
                int resource_size, ssr_size, flag_size;
                input >> tag >> option.seat >> option.score >> option.rank_score
                         >> resource_size;
                option.resources.resize(resource_size);
                for (int& value : option.resources) input >> value;
                input >> ssr_size;
                option.ssr_locations.resize(ssr_size);
                for (int& value : option.ssr_locations) input >> value;
                input >> flag_size;
                option.flag_locations.resize(flag_size);
                for (int& value : option.flag_locations) input >> value;
                if (protocol_v1) {
                    int infant;
                    input >> infant;
                    option.infant = infant;
                }
            }
        }
        if (protocol_v2) {
            int incumbent_group, incumbent_count;
            if (!(input >> tag >> incumbent_group >> incumbent_count)
                || tag != "INCUMBENT" || incumbent_group != group_id
                || incumbent_count != passenger_count) return 2;
            for (int p = 0; p < incumbent_count; ++p) {
                int passenger_index, option_index;
                if (!(input >> passenger_index >> option_index)
                    || passenger_index != group.passengers[p].original_index
                    || option_index < 0
                    || option_index >= int(group.passengers[p].options.size())) return 2;
                group.incumbent_choices.push_back(option_index);
            }
        }
        group_ids[group_number] = group_id;
    }
    const auto input_parse_finished = std::chrono::steady_clock::now();

    std::vector<GroupWork> work(group_count);
    std::vector<native_solver::GroupCoverageDiagnostic> group_coverage;
    group_coverage.reserve(group_count);
    for (int group_number = 0; group_number < group_count; ++group_number) {
        GroupInput& group = groups[group_number];
        GroupWork& current = work[group_number];
        group_coverage.push_back(native_solver::GroupCoverageDiagnostic{
            group_number, group.group_id, -1.0, -1.0
        });
        if (protocol_v2) {
            Pattern incumbent_pattern;
            if (!pattern_from_choices(
                group.passengers, seats, baby_pairs, resource_count, location_count,
                group.incumbent_choices, compact_weight, x_factor, y_factor,
                incumbent_pattern
            )) return 2;
            current.signatures.insert(group.incumbent_choices);
            current.combined.push_back(std::move(incumbent_pattern));
        }
    }
    const auto group_preprocess_finished = std::chrono::steady_clock::now();

    const auto group_enabled = [&](int group_number) {
        return generation_scope.group_ids.empty() || std::find(
            generation_scope.group_ids.begin(), generation_scope.group_ids.end(),
            groups[group_number].group_id
        ) != generation_scope.group_ids.end();
    };

    const auto hash_current_pool = [&]() {
        uint64_t value = 1469598103934665603ULL;
        for (int pool_group = 0; pool_group < group_count; ++pool_group) {
            for (const Pattern& pattern : work[pool_group].combined) {
                uint64_t identifier = stable_pattern_id(
                    groups[pool_group].group_id, pattern,
                    groups[pool_group].passengers
                );
                for (int byte = 0; byte < 8; ++byte) {
                    value ^= identifier & 0xffULL;
                    value *= 1099511628211ULL;
                    identifier >>= 8;
                }
            }
        }
        return value;
    };

    std::vector<int> completed_task_count(group_count, 0);
    int task_sequence = 0;
    const auto run_task = [&](int group_number, int hint_index, int variant) {
        if (generation_scope.task_limit_per_group > 0
            && completed_task_count[group_number]
                >= generation_scope.task_limit_per_group) return;
        if (generation_deadline
            && std::chrono::steady_clock::now() >= *generation_deadline) {
            deadline_hit = true;
            stop_search = true;
            return;
        }
        if (generation_deadline && rr_task_admission_guard_seconds > 0.0) {
            const double remaining_seconds = std::chrono::duration<double>(
                *generation_deadline - std::chrono::steady_clock::now()
            ).count();
            if (remaining_seconds <= rr_task_admission_guard_seconds) {
                admission_guard_stop = true;
                stop_search = true;
                if (collect_task_diagnostics) {
                    const double now_ms = std::chrono::duration<double, std::milli>(
                        std::chrono::steady_clock::now() - started
                    ).count();
                    task_diagnostics.push_back(native_solver::TaskDiagnostic{
                        task_sequence++, group_number,
                        groups[group_number].group_id,
                        int(groups[group_number].passengers.size()),
                        group_has_ssr(groups[group_number]),
                        group_has_protection(groups[group_number]), hint_index,
                        variant, now_ms, now_ms, 0, 0, 0, 0, 0,
                        false, false, "admission_guard", 0,
                        hash_current_pool(), {}, {}
                    });
                }
                return;
            }
        }
        ++completed_task_count[group_number];
        GroupInput& group = groups[group_number];
        GroupWork& current = work[group_number];
        const auto task_started = collect_task_diagnostics
            ? std::chrono::steady_clock::now() : started;
        const double task_start_ms = collect_task_diagnostics
            ? std::chrono::duration<double, std::milli>(task_started - started).count()
            : 0.0;
        if (hint_index == 0 && variant == 0) {
            group_coverage[group_number].hint0_variant0_start_ms =
                std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - started
                ).count();
        }
        uint64_t expansion_count = 0;
        uint64_t beam_prune_count = 0;
        int complete_state_count = 0;
        int legal_complete_pattern_count = 0;
        int unique_complete_resource_signatures = 0;
        const native_solver::GroupResourcePressure* resource_pressure = nullptr;
        if (generation_scope.component_aware_ordering) {
            const auto found_pressure = std::find_if(
                generation_scope.group_resource_pressure.begin(),
                generation_scope.group_resource_pressure.end(),
                [&](const auto& row) { return row.group_id == group.group_id; }
            );
            if (found_pressure != generation_scope.group_resource_pressure.end()) {
                resource_pressure = &*found_pressure;
            }
        }
        static const std::vector<native_solver::TargetTraceSpec> no_trace_specs;
        bool search_completed = true;
        std::string termination_reason = "completed";
        std::vector<native_solver::TargetTraceSpec> group_trace_specs;
        if (target_trace_specs) {
            for (const auto& spec : *target_trace_specs) {
                if (spec.group_id == group.group_id) group_trace_specs.push_back(spec);
            }
        }
        std::vector<Pattern> found = search_hint(
            group.passengers, seats, baby_pairs, resource_count, location_count,
            group.group_id, group.hints[hint_index], hint_index, group.row_radius,
            group.beam_width, group.pattern_limit, variant, compact_weight,
            x_factor, y_factor, generation_deadline, started, watched_ids,
            group_number, pattern_diagnostics, expansion_count,
            target_trace_specs ? group_trace_specs : no_trace_specs,
            target_trace_steps, layer_state_snapshots, beam_reserve,
            resource_pressure, generation_scope.diverse_complete_retention,
            beam_prune_count, complete_state_count, legal_complete_pattern_count,
            unique_complete_resource_signatures, search_completed,
            termination_reason
        );
        const auto task_finished = std::chrono::steady_clock::now();
        const bool task_completed = (
            search_completed
            && (!generation_deadline || task_finished < *generation_deadline)
        );
        if (!task_completed && termination_reason == "completed") {
            termination_reason = "deadline_after_search";
        }
        if (hint_index == 0 && variant == 0
            && (!generation_deadline || task_finished < *generation_deadline)) {
            group_coverage[group_number].hint0_variant0_complete_ms =
                std::chrono::duration<double, std::milli>(
                    task_finished - started
                ).count();
        }
        if (collect_task_diagnostics) {
            std::vector<uint64_t> produced_ids;
            std::vector<uint64_t> first_discovered_ids;
            produced_ids.reserve(found.size());
            first_discovered_ids.reserve(found.size());
            for (Pattern& pattern : found) {
                const uint64_t identifier = stable_pattern_id(
                    group.group_id, pattern, group.passengers
                );
                produced_ids.push_back(identifier);
                if (!current.signatures.count(pattern.choices)) {
                    first_discovered_ids.push_back(identifier);
                    if (task_completed) {
                        current.signatures.insert(pattern.choices);
                        current.combined.push_back(std::move(pattern));
                    }
                }
            }
            const double task_end_ms = std::chrono::duration<double, std::milli>(
                task_finished - started
            ).count();
            task_diagnostic_index[{group_number, hint_index, variant}] =
                task_diagnostics.size();
            task_diagnostics.push_back(native_solver::TaskDiagnostic{
                task_sequence++, group_number, group.group_id,
                int(group.passengers.size()),
                group_has_ssr(group), group_has_protection(group), hint_index,
                variant, task_start_ms, task_end_ms, expansion_count,
                int(produced_ids.size()), int(first_discovered_ids.size()),
                int(produced_ids.size() - first_discovered_ids.size()), 0,
                true, task_completed,
                termination_reason,
                task_completed ? int(first_discovered_ids.size()) : 0,
                hash_current_pool(),
                std::move(produced_ids), std::move(first_discovered_ids)
            });
            auto& diagnostic = task_diagnostics.back();
            diagnostic.beam_prune_count = beam_prune_count;
            diagnostic.complete_state_count = complete_state_count;
            diagnostic.legal_complete_pattern_count = legal_complete_pattern_count;
            diagnostic.unique_complete_resource_signatures =
                unique_complete_resource_signatures;
        } else {
            if (task_completed) {
                for (Pattern& pattern : found) {
                    if (current.signatures.insert(pattern.choices).second) {
                        current.combined.push_back(std::move(pattern));
                    }
                }
            }
        }
        if (!collect_task_diagnostics) ++task_sequence;
        if (generation_deadline && task_finished >= *generation_deadline) {
            deadline_hit = true;
            stop_search = true;
        }
    };

    const auto capture_pass0 = [&]() {
        pass0_group_diagnostics.clear();
        pass0_group_diagnostics.reserve(group_count);
        for (int group_number = 0; group_number < group_count; ++group_number) {
            const GroupInput& group = groups[group_number];
            const GroupWork& current = work[group_number];
            double best_score = -1e100;
            double incumbent_score = -1e100;
            std::set<std::vector<int>> resource_signatures;
            for (const Pattern& pattern : current.combined) {
                best_score = std::max(best_score, pattern.score);
                if (pattern.choices == group.incumbent_choices) {
                    incumbent_score = pattern.score;
                }
                resource_signatures.insert(pattern.resources);
            }
            const int pattern_count = int(current.combined.size());
            const int signature_count = int(resource_signatures.size());
            pass0_group_diagnostics.push_back(
                native_solver::Pass0GroupDiagnostic{
                    group_number, group.group_id, int(group.passengers.size()),
                    group_has_ssr(group), group_has_protection(group),
                    pattern_count, best_score, incumbent_score,
                    best_score - incumbent_score, signature_count,
                    pattern_count > 0
                        ? double(signature_count) / pattern_count : 0.0
                }
            );
        }
    };

    if (schedule == native_solver::PatternSchedule::GroupMajor) {
        for (int group_number = 0; group_number < group_count && !stop_search;
             ++group_number) {
            if (!group_enabled(group_number)) continue;
            const GroupInput& group = groups[group_number];
            for (int hint_index = 0;
                 hint_index < int(group.hints.size()) && !stop_search;
                 ++hint_index) {
                for (int variant = 0;
                     variant < group.ranking_variants && !stop_search;
                     ++variant) {
                    run_task(group_number, hint_index, variant);
                }
            }
        }
    } else {
        size_t max_hint_count = 0;
        int max_variants = 0;
        for (const GroupInput& group : groups) {
            max_hint_count = std::max(max_hint_count, group.hints.size());
            max_variants = std::max(max_variants, group.ranking_variants);
        }
        for (size_t hint_index = 0;
             hint_index < max_hint_count && !stop_search; ++hint_index) {
            for (int group_number = 0;
                 group_number < group_count && !stop_search; ++group_number) {
                if (group_enabled(group_number)
                    && hint_index < groups[group_number].hints.size()) {
                    run_task(group_number, int(hint_index), 0);
                }
            }
            if (hint_index == 0 && !stop_search && collect_task_diagnostics) {
                capture_pass0();
            }
        }
        for (int variant = 1; variant < max_variants && !stop_search; ++variant) {
            for (size_t hint_index = 0;
                 hint_index < max_hint_count && !stop_search; ++hint_index) {
                for (int group_number = 0;
                     group_number < group_count && !stop_search; ++group_number) {
                    const GroupInput& group = groups[group_number];
                    if (group_enabled(group_number)
                        && variant < group.ranking_variants
                        && hint_index < group.hints.size()) {
                        run_task(group_number, int(hint_index), variant);
                    }
                }
            }
        }
    }
    const auto search_finished = std::chrono::steady_clock::now();

    for (int group_number = 0; group_number < group_count; ++group_number) {
        GroupInput& group = groups[group_number];
        GroupWork& current = work[group_number];
        std::vector<Pattern>& combined = current.combined;
        const std::vector<Passenger>& passengers = group.passengers;
        const std::vector<int>& incumbent_choices = group.incumbent_choices;
        const int group_id = group.group_id;
        const int hint_count = int(group.hints.size());
        const int pattern_limit = group.pattern_limit;
        const int ranking_variants = group.ranking_variants;
        std::sort(combined.begin(), combined.end(), [](const Pattern& a, const Pattern& b) {
            return a.score > b.score;
        });
        size_t combined_limit = size_t(pattern_limit) * std::max(1, hint_count)
            * std::max(1, ranking_variants);
        for (size_t index = combined_limit; index < combined.size(); ++index) {
            const Pattern& pattern = combined[index];
            if (protocol_v2 && pattern.choices == incumbent_choices) continue;
            const uint64_t identifier = stable_pattern_id(
                group_id, pattern, passengers
            );
            if (!watched_ids.count(identifier)) continue;
            pattern_diagnostics.push_back(native_solver::PatternDiagnostic{
                identifier, group_number, group_id, pattern.discovery_ms,
                pattern.hint, pattern.hint_index, pattern.variant,
                int(passengers.size()), false,
                !pattern.ssr_all.empty() || !pattern.ssr_flagged.empty(),
                pattern.resources.size() > passengers.size(), false,
                pattern.source, "per_group_topk_truncation"
            });
        }
        if (combined.size() > combined_limit) {
            combined.resize(combined_limit);
        }
        if (protocol_v2) {
            const bool incumbent_present = std::any_of(
                combined.begin(), combined.end(), [&](const Pattern& value) {
                    return value.choices == incumbent_choices;
                }
            );
            if (!incumbent_present) {
                Pattern incumbent_pattern;
                if (!pattern_from_choices(
                    passengers, seats, baby_pairs, resource_count, location_count,
                    incumbent_choices, compact_weight, x_factor, y_factor,
                    incumbent_pattern
                )) return 2;
                combined.push_back(std::move(incumbent_pattern));
            }
        }
        group_ids[group_number] = group_id;
        for (const Pattern& pattern : combined) {
            const uint64_t identifier = stable_pattern_id(
                group_id, pattern, passengers
            );
            std::vector<std::pair<int, int>> blocked_by;
            std::vector<std::pair<int, int>> assignments;
            for (size_t p = 0; p < pattern.choices.size(); ++p) {
                const Option& option = passengers[p].options[pattern.choices[p]];
                assignments.emplace_back(passengers[p].original_index, option.seat);
                for (int resource : option.resources) {
                    if (resource != option.seat) {
                        blocked_by.emplace_back(resource, passengers[p].original_index);
                    }
                }
            }
            std::sort(blocked_by.begin(), blocked_by.end());
            if (build_master) {
                const bool hard_keep = (
                    protocol_v2 && pattern.choices == incumbent_choices
                );
                if (collect_task_diagnostics && !hard_keep) {
                    const auto found_task = task_diagnostic_index.find({
                        group_number, pattern.hint_index, pattern.variant
                    });
                    if (found_task != task_diagnostic_index.end()) {
                        ++task_diagnostics[found_task->second].retained_pattern_count;
                    }
                }
                pattern_diagnostics.push_back(native_solver::PatternDiagnostic{
                    identifier, group_number, group_id, pattern.discovery_ms,
                    pattern.hint, pattern.hint_index, pattern.variant,
                    int(passengers.size()), hard_keep,
                    !pattern.ssr_all.empty() || !pattern.ssr_flagged.empty(),
                    pattern.resources.size() > passengers.size(), true,
                    hard_keep ? "incumbent" : pattern.source, "retained"
                });
                std::vector<std::pair<int, int>> ssr_flagged;
                for (int value : pattern.ssr_flagged) {
                    ssr_flagged.emplace_back(value, 1);
                }
                std::vector<int> infant_seats = pattern.infant_seats;
                std::vector<int> occupied_seats = pattern.seats;
                std::sort(infant_seats.begin(), infant_seats.end());
                std::sort(occupied_seats.begin(), occupied_seats.end());
                master_patterns.push_back(native_solver::PatternRecord{
                    identifier, group_number, pattern.master_cost,
                    hard_keep,
                    pattern.resources, pattern.ssr_all, std::move(ssr_flagged),
                    std::move(infant_seats), std::move(occupied_seats),
                    std::move(assignments), std::move(blocked_by)
                });
                continue;
            }
            output << "PATTERN " << group_id << ' ' << std::setprecision(17)
                      << pattern.score << ' ' << pattern.choices.size();
            for (size_t p = 0; p < pattern.choices.size(); ++p) {
                output << ' ' << passengers[p].original_index << ' '
                          << pattern.choices[p] << ' ' << pattern.seats[p];
            }
            output << ' ' << pattern.resources.size();
            for (int resource : pattern.resources) output << ' ' << resource;
            if (protocol_v1) {
                output << " V1 " << identifier << ' ' << pattern.master_cost;
                output << ' ' << blocked_by.size();
                for (const auto& item : blocked_by) {
                    output << ' ' << item.first << ' ' << item.second;
                }
                output << ' ' << pattern.ssr_all.size();
                for (const auto& item : pattern.ssr_all) {
                    output << ' ' << item.first << ' ' << item.second;
                }
                output << ' ' << pattern.ssr_flagged.size();
                for (int resource : pattern.ssr_flagged) {
                    output << ' ' << resource << " 1";
                }
                output << ' ' << pattern.infant_seats.size();
                for (int seat : pattern.infant_seats) output << ' ' << seat;
                output << ' ' << pattern.seats.size();
                for (int seat : pattern.seats) output << ' ' << seat;
            }
            output << '\n';
        }
        total_patterns += combined.size();
    }
    const auto pattern_materialization_finished = std::chrono::steady_clock::now();
    if (build_master) {
        if (!protocol_v2) return 2;
        if (direct_master) {
            direct_master->group_count = group_count;
            direct_master->seat_count = seat_count;
            direct_master->ssr_count = location_count;
            direct_master->full_load = full_load != 0;
            direct_master->group_ids = group_ids;
            direct_master->baby_pairs = baby_pairs;
            direct_master->pattern_diagnostics = pattern_diagnostics;
            direct_master->group_coverage_diagnostics = group_coverage;
            direct_master->task_diagnostics = std::move(task_diagnostics);
            direct_master->pass0_group_diagnostics = std::move(
                pass0_group_diagnostics
            );
            direct_master->target_trace_steps = std::move(target_trace_steps);
            direct_master->layer_state_snapshots = std::move(layer_state_snapshots);
            direct_master->input_parse_ms = std::chrono::duration<double, std::milli>(
                input_parse_finished - kernel_started
            ).count();
            direct_master->group_preprocess_ms = std::chrono::duration<double, std::milli>(
                group_preprocess_finished - input_parse_finished
            ).count();
            direct_master->search_ms = std::chrono::duration<double, std::milli>(
                search_finished - group_preprocess_finished
            ).count();
            direct_master->pattern_materialization_ms =
                std::chrono::duration<double, std::milli>(
                    pattern_materialization_finished - search_finished
                ).count();
            direct_master->rr_admission_guard_stop = admission_guard_stop;
            direct_master->rr_task_admission_guard_ms =
                rr_task_admission_guard_seconds * 1000.0;
            if (emit_master) {
                direct_master->patterns = master_patterns;
            } else {
                direct_master->patterns = std::move(master_patterns);
            }
        }
    }
    if (emit_master) {
        output << "MASTER_V1 " << group_count << ' ' << seat_count << ' '
                  << location_count << ' ' << baby_pair_count << ' ' << full_load
                  << ' ' << master_patterns.size() << '\n';
        for (int group = 0; group < group_count; ++group) {
            output << "GROUP " << group << ' ' << group_ids[group] << '\n';
        }
        for (const BabyPair& pair : baby_pairs) {
            output << "BABY " << pair.infant_seat << ' ' << pair.occupant_seat
                      << ' ' << std::setprecision(17) << pair.cost << '\n';
        }
        for (const native_solver::PatternRecord& record : master_patterns) {
            output << "PATTERN " << record.id << ' ' << record.group << ' '
                      << std::setprecision(17) << record.master_cost << ' '
                      << int(record.hard_keep) << ' ' << record.seat_resources.size();
            for (int value : record.seat_resources) output << ' ' << value;
            output << ' ' << record.ssr_all.size();
            for (const auto& item : record.ssr_all) {
                output << ' ' << item.first << ' ' << item.second;
            }
            output << ' ' << record.ssr_flagged.size();
            for (const auto& item : record.ssr_flagged) {
                output << ' ' << item.first << ' ' << item.second;
            }
            output << ' ' << record.infant_seats.size();
            for (int value : record.infant_seats) output << ' ' << value;
            output << ' ' << record.occupied_seats.size();
            for (int value : record.occupied_seats) output << ' ' << value;
            output << ' ' << record.assignments.size();
            for (const auto& item : record.assignments) {
                output << ' ' << item.first << ' ' << item.second;
            }
            output << ' ' << record.blocked_by.size();
            for (const auto& item : record.blocked_by) {
                output << ' ' << item.first << ' ' << item.second;
            }
            output << '\n';
        }
    }
    double elapsed = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - started
    ).count();
    diagnostics << "groups=" << group_count << " patterns=" << total_patterns
              << " seconds=" << elapsed << " deadline_hit=" << deadline_hit << '\n';
    if (generation_deadline_hit) *generation_deadline_hit = deadline_hit;
    return 0;
}

#ifndef NATIVE_PATTERN_KERNEL_LIBRARY
int main(int argc, char** argv) {
    std::cin.tie(nullptr);
    if (argc > 1 && std::string(argv[1]) == "--beam-reserve-self-test") {
        const bool passed = beam_reserve_self_test();
        std::cout << "{\"beam_reserve_self_test\":"
                  << (passed ? "true" : "false") << "}\n";
        return passed ? 0 : 1;
    }
    const bool emit_master = argc > 1 && std::string(argv[1]) == "--master-input";
    return run_native_pattern_kernel(std::cin, std::cout, std::cerr, emit_master);
}
#endif
