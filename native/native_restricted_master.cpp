#include "interfaces/highs_c_api.h"
#include "native_master_types.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <map>
#include <string>
#include <utility>
#include <unordered_set>
#include <vector>

namespace native_master {

using native_solver::BabyPair;
using native_solver::MasterProblem;
using native_solver::PatternRecord;

static bool contains(const std::vector<int>& values, int value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

static std::string status_name(HighsInt status) {
    if (status == kHighsModelStatusOptimal) return "Optimal";
    if (status == kHighsModelStatusTimeLimit) return "TimeLimit";
    if (status == kHighsModelStatusInfeasible) return "Infeasible";
    return "Other";
}

static void fingerprint_uint64(uint64_t& hash, uint64_t value) {
    for (int byte = 0; byte < 8; ++byte) {
        hash ^= value & 0xffULL;
        hash *= 1099511628211ULL;
        value >>= 8;
    }
}

static void fingerprint_double(uint64_t& hash, double value) {
    uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    fingerprint_uint64(hash, bits);
}

int solve(
    const MasterProblem& problem, std::ostream& output, double time_limit,
    bool skip_due_to_deadline, std::vector<uint64_t>* selected_pattern_ids,
    const MasterSolveOptions* options
) {
    std::ios::sync_with_stdio(false);
    const auto started = std::chrono::steady_clock::now();
    const MasterSolveOptions effective_options = options
        ? *options : MasterSolveOptions{};
    if (effective_options.diagnostics) *effective_options.diagnostics = {};
    MasterProblem ordered_problem = problem;
    if (effective_options.canonicalize_patterns) {
        std::sort(
            ordered_problem.patterns.begin(), ordered_problem.patterns.end(),
            [](const PatternRecord& left, const PatternRecord& right) {
                if (left.group != right.group) return left.group < right.group;
                if (left.hard_keep != right.hard_keep) {
                    return left.hard_keep > right.hard_keep;
                }
                return left.id < right.id;
            }
        );
    }
    const int group_count = ordered_problem.group_count;
    const int seat_count = problem.seat_count;
    const int ssr_count = problem.ssr_count;
    const int baby_count = int(problem.baby_pairs.size());
    const int pattern_count = int(problem.patterns.size());
    const bool full_load = problem.full_load;
    const std::vector<int>& group_ids = problem.group_ids;
    const std::vector<BabyPair>& baby_pairs = problem.baby_pairs;
    const std::vector<PatternRecord>& patterns = ordered_problem.patterns;
    if (group_count <= 0 || seat_count <= 0 || ssr_count < 0
        || int(group_ids.size()) != group_count || patterns.empty()) return 2;
    for (const PatternRecord& pattern : patterns) {
        if (pattern.group < 0 || pattern.group >= group_count) return 2;
    }

    std::vector<int> flag_row(group_count * ssr_count, -1);
    int row_count = group_count + seat_count;
    for (const PatternRecord& pattern : patterns) {
        for (const auto& item : pattern.ssr_flagged) {
            int& row = flag_row[pattern.group * ssr_count + item.first];
            if (row < 0) row = row_count++;
        }
    }
    const int ssr_all_start = row_count;
    row_count += ssr_count;
    const int baby_start = row_count;
    row_count += 3 * baby_count;
    const int local_branching_row = effective_options.local_branching_radius >= 0
        ? row_count++ : -1;
    std::unordered_set<uint64_t> branching_center(
        effective_options.incumbent_pattern_ids.begin(),
        effective_options.incumbent_pattern_ids.end());
    if (local_branching_row >= 0) {
        if (branching_center.empty()) {
            std::vector<bool> seen(group_count, false);
            for (const auto& pattern : patterns) {
                if (pattern.hard_keep && !seen[pattern.group]) {
                    branching_center.insert(pattern.id);
                    seen[pattern.group] = true;
                }
            }
        }
        std::vector<int> center_counts(group_count, 0);
        for (const auto& pattern : patterns)
            if (branching_center.count(pattern.id)) ++center_counts[pattern.group];
        if (int(branching_center.size()) != group_count
            || std::any_of(center_counts.begin(), center_counts.end(),
                [](int count) { return count != 1; })) return 2;
    }

    const double infinity = 1e30;
    std::vector<double> row_lower(row_count, -infinity), row_upper(row_count, infinity);
    if (local_branching_row >= 0)
        row_lower[local_branching_row] = group_count
            - std::min(group_count, effective_options.local_branching_radius);
    for (int group = 0; group < group_count; ++group) row_lower[group] = row_upper[group] = 1.0;
    for (int seat = 0; seat < seat_count; ++seat) {
        row_upper[group_count + seat] = 1.0;
        if (full_load) row_lower[group_count + seat] = 1.0;
    }
    for (int row : flag_row) if (row >= 0) row_upper[row] = 0.0;
    std::vector<int> ssr_m(ssr_count, 0);
    for (int resource = 0; resource < ssr_count; ++resource) {
        for (int group = 0; group < group_count; ++group) {
            int maximum = 0;
            for (const PatternRecord& pattern : patterns) {
                if (pattern.group != group) continue;
                for (const auto& item : pattern.ssr_all) {
                    if (item.first == resource) maximum = std::max(maximum, item.second);
                }
            }
            ssr_m[resource] += maximum;
        }
        row_upper[ssr_all_start + resource] = double(ssr_m[resource] + 1);
    }
    for (int pair = 0; pair < baby_count; ++pair) {
        row_upper[baby_start + 3 * pair] = 1.0;
        row_lower[baby_start + 3 * pair + 1] = 0.0;
        row_lower[baby_start + 3 * pair + 2] = 0.0;
    }

    const int column_count = pattern_count + ssr_count + baby_count;
    std::vector<double> col_cost(column_count, 0.0), col_lower(column_count, 0.0),
        col_upper(column_count, 1.0);
    std::vector<HighsInt> integrality(column_count, kHighsVarTypeContinuous);
    std::vector<std::map<int, double>> columns(column_count);
    for (int index = 0; index < pattern_count; ++index) {
        const PatternRecord& pattern = patterns[index];
        col_cost[index] = pattern.master_cost;
        integrality[index] = kHighsVarTypeInteger;
        columns[index][pattern.group] = 1.0;
        if (local_branching_row >= 0 && branching_center.count(pattern.id))
            columns[index][local_branching_row] = 1.0;
        for (int seat : pattern.seat_resources) columns[index][group_count + seat] += 1.0;
        for (const auto& item : pattern.ssr_all) columns[index][ssr_all_start + item.first] += item.second;
        for (const auto& item : pattern.ssr_flagged) {
            columns[index][flag_row[pattern.group * ssr_count + item.first]] += item.second;
        }
        for (int pair = 0; pair < baby_count; ++pair) {
            const int infant = int(contains(pattern.infant_seats, baby_pairs[pair].infant_seat));
            const int occupant = int(contains(pattern.occupied_seats, baby_pairs[pair].occupant_seat));
            if (infant || occupant) {
                columns[index][baby_start + 3 * pair] += infant + occupant;
                columns[index][baby_start + 3 * pair + 1] += infant;
                columns[index][baby_start + 3 * pair + 2] += occupant;
            }
        }
    }
    for (int resource = 0; resource < ssr_count; ++resource) {
        const int column = pattern_count + resource;
        columns[column][ssr_all_start + resource] = double(ssr_m[resource]);
        for (int group = 0; group < group_count; ++group) {
            const int row = flag_row[group * ssr_count + resource];
            if (row >= 0) columns[column][row] = -1.0;
        }
    }
    for (int pair = 0; pair < baby_count; ++pair) {
        const int column = pattern_count + ssr_count + pair;
        col_cost[column] = baby_pairs[pair].cost;
        columns[column][baby_start + 3 * pair] = -1.0;
        columns[column][baby_start + 3 * pair + 1] = -1.0;
        columns[column][baby_start + 3 * pair + 2] = -1.0;
    }

    std::vector<HighsInt> a_start(column_count + 1, 0), a_index;
    std::vector<double> a_value;
    for (int column = 0; column < column_count; ++column) {
        a_start[column] = HighsInt(a_index.size());
        for (const auto& item : columns[column]) {
            if (std::abs(item.second) <= 1e-15) continue;
            a_index.push_back(item.first); a_value.push_back(item.second);
        }
    }
    a_start[column_count] = HighsInt(a_index.size());
    uint64_t model_coefficient_hash = 1469598103934665603ULL;
    fingerprint_uint64(model_coefficient_hash, uint64_t(row_count));
    fingerprint_uint64(model_coefficient_hash, uint64_t(column_count));
    for (int row = 0; row < row_count; ++row) {
        fingerprint_double(model_coefficient_hash, row_lower[row]);
        fingerprint_double(model_coefficient_hash, row_upper[row]);
    }
    for (int column = 0; column < column_count; ++column) {
        if (column < pattern_count) {
            fingerprint_uint64(model_coefficient_hash, patterns[column].id);
        } else {
            fingerprint_uint64(
                model_coefficient_hash,
                0xffffffffffffffffULL - uint64_t(column - pattern_count)
            );
        }
        fingerprint_double(model_coefficient_hash, col_cost[column]);
        fingerprint_double(model_coefficient_hash, col_lower[column]);
        fingerprint_double(model_coefficient_hash, col_upper[column]);
        fingerprint_uint64(model_coefficient_hash, uint64_t(integrality[column]));
        for (const auto& item : columns[column]) {
            fingerprint_uint64(model_coefficient_hash, uint64_t(item.first));
            fingerprint_double(model_coefficient_hash, item.second);
        }
    }

    std::vector<double> warm_start(column_count, 0.0);
    std::vector<int> incumbent(group_count, -1);
    const std::unordered_set<uint64_t> explicit_incumbent(
        effective_options.incumbent_pattern_ids.begin(),
        effective_options.incumbent_pattern_ids.end()
    );
    if (!explicit_incumbent.empty()
        && int(explicit_incumbent.size()) != group_count) return 2;
    for (int index = 0; index < pattern_count; ++index) {
        const bool selected_for_warm_start = explicit_incumbent.empty()
            ? patterns[index].hard_keep
            : explicit_incumbent.count(patterns[index].id) != 0;
        if (selected_for_warm_start && incumbent[patterns[index].group] < 0) {
            incumbent[patterns[index].group] = index; warm_start[index] = 1.0;
        }
    }
    if (std::find(incumbent.begin(), incumbent.end(), -1) != incumbent.end()) return 2;
    std::vector<int> infant_count(seat_count, 0), occupied_count(seat_count, 0);
    for (int index : incumbent) {
        for (const auto& item : patterns[index].ssr_flagged) warm_start[pattern_count + item.first] = 1.0;
        for (int seat : patterns[index].infant_seats) ++infant_count[seat];
        for (int seat : patterns[index].occupied_seats) ++occupied_count[seat];
    }
    for (int pair = 0; pair < baby_count; ++pair) {
        if (infant_count[baby_pairs[pair].infant_seat]
            && occupied_count[baby_pairs[pair].occupant_seat]) {
            warm_start[pattern_count + ssr_count + pair] = 1.0;
        }
    }
    const auto selected_objective = [&](const std::vector<int>& choice) {
        std::vector<int> infants(seat_count, 0), occupants(seat_count, 0);
        double value = 0.0;
        for (int index : choice) {
            value += patterns[index].master_cost;
            for (int seat : patterns[index].infant_seats) ++infants[seat];
            for (int seat : patterns[index].occupied_seats) ++occupants[seat];
        }
        for (const BabyPair& pair : baby_pairs) {
            if (infants[pair.infant_seat] && occupants[pair.occupant_seat]) {
                value += pair.cost;
            }
        }
        return value;
    };
    const double incumbent_objective = selected_objective(incumbent);

    // The incumbent is the deadline fallback. Audit it against the exact
    // restricted-master rows before either passing it to HiGHS or returning it.
    std::vector<double> incumbent_activity(row_count, 0.0);
    for (int column = 0; column < column_count; ++column) {
        if (warm_start[column] <= 0.5) continue;
        for (const auto& item : columns[column]) {
            incumbent_activity[item.first] += item.second;
        }
    }
    for (int row = 0; row < row_count; ++row) {
        if (incumbent_activity[row] < row_lower[row] - 1e-7
            || incumbent_activity[row] > row_upper[row] + 1e-7) return 4;
    }

    void* highs = nullptr;
    std::string result_status = "DeadlineFallback";
    std::vector<int> selected = incumbent;
    bool used_fallback = skip_due_to_deadline;
    bool incumbent_preserved_due_to_objective_regression = false;
    double solver_objective = 0.0;
    double mip_gap = -1.0;
    double mip_dual_bound = 0.0;
    int attempts = 0;
    std::vector<int> radius_history;
    if (!skip_due_to_deadline) {
        highs = Highs_create();
        Highs_setBoolOptionValue(highs, "output_flag", 0);
        Highs_setIntOptionValue(highs, "threads", 1);
        Highs_setIntOptionValue(highs, "random_seed", 0);
        Highs_setDoubleOptionValue(highs, "mip_rel_gap", 0.0);
        Highs_setDoubleOptionValue(highs, "time_limit", time_limit);
        const HighsInt pass_status = Highs_passMip(
            highs, column_count, row_count, HighsInt(a_index.size()),
            kHighsMatrixFormatColwise, kHighsObjSenseMinimize, 0.0,
            col_cost.data(), col_lower.data(), col_upper.data(), row_lower.data(),
            row_upper.data(), a_start.data(), a_index.data(), a_value.data(), integrality.data()
        );
        if (pass_status == kHighsStatusError) { Highs_destroy(highs); return 3; }
        Highs_setSolution(highs, warm_start.data(), nullptr, nullptr, nullptr);
        const int max_attempts = std::max(1, effective_options.attempts);
        double best_objective = incumbent_objective;
        used_fallback = true;
        for (int attempt = 0; attempt < max_attempts; ++attempt) {
            const double remaining = time_limit - std::chrono::duration<double>(
                std::chrono::steady_clock::now() - started).count();
            if (remaining <= (max_attempts > 1 ? 0.01 : 0.0)) break;
            if (local_branching_row >= 0) {
                const int maximum_radius = effective_options.local_branching_max_radius > 0
                    ? std::max(effective_options.local_branching_radius,
                        effective_options.local_branching_max_radius) : group_count;
                const int radius = static_cast<int>(std::min<long long>(
                    std::min(group_count, maximum_radius),
                    static_cast<long long>(effective_options.local_branching_radius)
                        + static_cast<long long>(attempt)
                        * std::max(1, effective_options.local_branching_radius_growth)));
                Highs_changeRowBounds(highs, local_branching_row,
                    double(group_count - radius), infinity);
                radius_history.push_back(radius);
            }
            Highs_setDoubleOptionValue(highs, "time_limit", remaining);
            Highs_run(highs);
            const HighsInt model_status = Highs_getModelStatus(highs);
            result_status = status_name(model_status);
            HighsInt primal_status = 0;
            Highs_getIntInfoValue(highs, "primal_solution_status", &primal_status);
            if (primal_status != 2) break;
            std::vector<double> solution(column_count, 0.0);
            Highs_getSolution(highs, solution.data(), nullptr, nullptr, nullptr);
            solver_objective = Highs_getObjectiveValue(highs);
            Highs_getDoubleInfoValue(highs, "mip_gap", &mip_gap);
            Highs_getDoubleInfoValue(highs, "mip_dual_bound", &mip_dual_bound);
            std::vector<int> candidate(group_count, -1);
            for (int index = 0; index < pattern_count; ++index) {
                if (solution[index] > 0.5) {
                    const int group = patterns[index].group;
                    candidate[group] = candidate[group] < 0 ? index : -2;
                }
            }
            if (std::any_of(candidate.begin(), candidate.end(),
                [](int value) { return value < 0; })) break;
            ++attempts;
            const double objective = selected_objective(candidate);
            if (objective <= best_objective + 1e-7) {
                if (objective < best_objective - 1e-9 || max_attempts == 1) {
                    best_objective = objective;
                    selected = candidate;
                }
                used_fallback = false;
            } else {
                incumbent_preserved_due_to_objective_regression = true;
            }
            if (attempt + 1 < max_attempts) {
                std::vector<HighsInt> indexes(candidate.begin(), candidate.end());
                std::vector<double> values(indexes.size(), 1.0);
                const HighsInt status = Highs_addRow(highs, -infinity,
                    double(group_count - 1), HighsInt(indexes.size()),
                    indexes.data(), values.data());
                if (status == kHighsStatusError) { Highs_destroy(highs); return 3; }
            }
        }
    }
    std::fill(infant_count.begin(), infant_count.end(), 0);
    std::fill(occupied_count.begin(), occupied_count.end(), 0);
    double objective = 0.0;
    for (int index : selected) {
        objective += patterns[index].master_cost;
        for (int seat : patterns[index].infant_seats) ++infant_count[seat];
        for (int seat : patterns[index].occupied_seats) ++occupied_count[seat];
    }
    for (const BabyPair& pair : baby_pairs) {
        if (infant_count[pair.infant_seat] && occupied_count[pair.occupant_seat]) objective += pair.cost;
    }
    if (used_fallback) solver_objective = objective;
    if (effective_options.diagnostics) {
        effective_options.diagnostics->attempts = attempts;
        effective_options.diagnostics->radius_history = radius_history;
    }
    if (selected_pattern_ids) {
        selected_pattern_ids->clear();
        for (int index : selected) {
            selected_pattern_ids->push_back(patterns[index].id);
        }
    }
    const double elapsed_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - started
    ).count();

    output << std::setprecision(17);
    output << "{\n  \"status\":\"" << result_status << "\",\n";
    output << "  \"highs_version\":\"" << Highs_version() << "\",\n";
    output << "  \"patterns\":" << pattern_count << ",\n";
    output << "  \"attempts\":" << attempts << ",\n";
    output << "  \"radius_history\":[";
    for (size_t i = 0; i < radius_history.size(); ++i) {
        if (i) output << ',';
        output << radius_history[i];
    }
    output << "],\n";
    output << "  \"canonicalized_pattern_order\":"
           << (effective_options.canonicalize_patterns ? "true" : "false")
           << ",\n";
    output << "  \"model_coefficient_hash\":\""
           << model_coefficient_hash << "\",\n";
    output << "  \"rows\":" << row_count << ",\n";
    output << "  \"nonzeros\":" << a_index.size() << ",\n";
    output << "  \"used_fallback\":" << (used_fallback ? "true" : "false") << ",\n";
    output << "  \"master_skipped_due_to_deadline\":"
           << (skip_due_to_deadline ? "true" : "false") << ",\n";
    output << "  \"incumbent_master_audited\":true,\n";
    output << "  \"incumbent_preserved_due_to_objective_regression\":"
           << (incumbent_preserved_due_to_objective_regression
               ? "true" : "false") << ",\n";
    output << "  \"objective_cost\":" << objective << ",\n";
    output << "  \"solver_objective_cost\":" << solver_objective << ",\n";
    output << "  \"mip_gap\":";
    if (effective_options.attempts <= 1 && std::isfinite(mip_gap)) output << mip_gap;
    else output << "null";
    output << ",\n  \"mip_dual_bound\":";
    if (effective_options.attempts <= 1 && std::isfinite(mip_dual_bound)) output << mip_dual_bound;
    else output << "null";
    output << ",\n";
    output << "  \"soft_score\":" << -objective << ",\n";
    output << "  \"elapsed_ms\":" << elapsed_ms << ",\n";
    output << "  \"warm_start_pattern_ids\":[";
    for (int group = 0; group < group_count; ++group) {
        if (group) output << ',';
        output << '\"' << patterns[incumbent[group]].id << '\"';
    }
    output << "],\n  \"pattern_column_ids\":[";
    for (int index = 0; index < pattern_count; ++index) {
        if (index) output << ',';
        output << '\"' << patterns[index].id << '\"';
    }
    output << "],\n  \"selected_pattern_ids\":[";
    for (int group = 0; group < group_count; ++group) {
        if (group) output << ',';
        output << '\"' << patterns[selected[group]].id << '\"';
    }
    output << "],\n  \"assignments\":[";
    bool first = true;
    for (int group = 0; group < group_count; ++group) {
        for (const auto& item : patterns[selected[group]].assignments) {
            if (!first) output << ','; first = false;
            output << '[' << group_ids[group] << ',' << item.first << ',' << item.second << ']';
        }
    }
    output << "],\n  \"blocked_by\":[";
    first = true;
    for (int group = 0; group < group_count; ++group) {
        for (const auto& item : patterns[selected[group]].blocked_by) {
            if (!first) output << ','; first = false;
            output << '[' << item.first << ',' << group_ids[group] << ',' << item.second << ']';
        }
    }
    output << "]\n}\n";
    if (highs) Highs_destroy(highs);
    return 0;
}

int read_problem(std::istream& input, MasterProblem& problem) {
    std::string tag;
    int baby_count, full_load, pattern_count;
    if (!(input >> tag >> problem.group_count >> problem.seat_count
          >> problem.ssr_count >> baby_count >> full_load >> pattern_count)
        || tag != "MASTER_V1" || baby_count < 0 || pattern_count < 0) return 2;
    problem.full_load = full_load != 0;
    problem.group_ids.resize(problem.group_count);
    for (int index = 0; index < problem.group_count; ++index) {
        int group_index, group_id;
        if (!(input >> tag >> group_index >> group_id) || tag != "GROUP"
            || group_index < 0 || group_index >= problem.group_count) return 2;
        problem.group_ids[group_index] = group_id;
    }
    problem.baby_pairs.resize(baby_count);
    for (BabyPair& pair : problem.baby_pairs) {
        if (!(input >> tag >> pair.infant_seat >> pair.occupant_seat >> pair.cost)
            || tag != "BABY" || pair.cost < 0.0) return 2;
    }
    problem.patterns.resize(pattern_count);
    for (PatternRecord& pattern : problem.patterns) {
        int hard_keep, count;
        if (!(input >> tag >> pattern.id >> pattern.group >> pattern.master_cost
              >> hard_keep) || tag != "PATTERN") return 2;
        pattern.hard_keep = hard_keep != 0;
        input >> count; pattern.seat_resources.resize(count);
        for (int& value : pattern.seat_resources) input >> value;
        input >> count; pattern.ssr_all.resize(count);
        for (auto& item : pattern.ssr_all) input >> item.first >> item.second;
        input >> count; pattern.ssr_flagged.resize(count);
        for (auto& item : pattern.ssr_flagged) input >> item.first >> item.second;
        input >> count; pattern.infant_seats.resize(count);
        for (int& value : pattern.infant_seats) input >> value;
        input >> count; pattern.occupied_seats.resize(count);
        for (int& value : pattern.occupied_seats) input >> value;
        input >> count; pattern.assignments.resize(count);
        for (auto& item : pattern.assignments) input >> item.first >> item.second;
        input >> count; pattern.blocked_by.resize(count);
        for (auto& item : pattern.blocked_by) input >> item.first >> item.second;
        if (!input) return 2;
    }
    return 0;
}

int solve(
    std::istream& input, std::ostream& output, double time_limit
) {
    MasterProblem problem;
    const int read_status = read_problem(input, problem);
    if (read_status != 0) return read_status;
    return solve(problem, output, time_limit);
}

}  // namespace native_master

#ifndef NATIVE_RESTRICTED_MASTER_LIBRARY
int main(int argc, char** argv) {
    std::cin.tie(nullptr);
    const double time_limit = argc > 1 ? std::stod(argv[1]) : 1.0;
    return native_master::solve(std::cin, std::cout, time_limit);
}
#endif
