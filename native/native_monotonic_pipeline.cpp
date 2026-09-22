#include "native_master_types.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace {

using native_solver::MasterProblem;
using native_solver::PatternDiagnostic;
using native_solver::PatternRecord;

struct GenerationResult {
    MasterProblem problem;
    bool cutoff = false;
    double generation_ms = 0.0;
    double remaining_seconds = 0.0;
};

GenerationResult generate(
    const std::string& input_payload, double total_seconds,
    double master_seconds, const std::vector<uint64_t>& watched,
    native_solver::PatternSchedule schedule
) {
    const auto started = std::chrono::steady_clock::now();
    const double safety_seconds = 0.05;
    const double master_reserve = std::max(
        0.15, master_seconds + safety_seconds
    );
    const auto deadline = started + std::chrono::duration_cast<
        std::chrono::steady_clock::duration
    >(std::chrono::duration<double>(
        std::max(0.0, total_seconds - master_reserve)
    ));
    std::istringstream input(input_payload);
    std::ostringstream unused_output;
    GenerationResult result;
    if (run_native_pattern_kernel(
        input, unused_output, std::cerr, false, &result.problem,
        &deadline, &result.cutoff, &watched, schedule
    ) != 0) {
        result.problem.group_count = -1;
        return result;
    }
    const auto finished = std::chrono::steady_clock::now();
    result.generation_ms = std::chrono::duration<double, std::milli>(
        finished - started
    ).count();
    result.remaining_seconds = std::chrono::duration<double>(
        started + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(total_seconds)
        ) - finished
    ).count();
    return result;
}

void emit_group_coverage(
    std::ostream& output,
    const std::vector<native_solver::GroupCoverageDiagnostic>& diagnostics
) {
    output << '[';
    for (size_t index = 0; index < diagnostics.size(); ++index) {
        if (index) output << ',';
        const auto& row = diagnostics[index];
        output << "{\"group\":" << row.group
               << ",\"group_id\":" << row.group_id
               << ",\"hint0_variant0_start_ms\":"
               << row.hint0_variant0_start_ms
               << ",\"hint0_variant0_complete_ms\":"
               << row.hint0_variant0_complete_ms << '}';
    }
    output << ']';
}

void emit_diagnostics(
    std::ostream& output, const std::vector<PatternDiagnostic>& diagnostics
) {
    output << '[';
    bool first = true;
    for (const PatternDiagnostic& row : diagnostics) {
        if (!first) output << ',';
        first = false;
        output << "{\"pattern_id\":\"" << row.id
               << "\",\"group\":" << row.group
               << ",\"group_id\":" << row.group_id
               << ",\"discovery_ms\":" << std::setprecision(17)
               << row.discovery_ms
               << ",\"source\":\"" << row.source
               << "\",\"hint\":" << row.hint
               << ",\"hint_index\":" << row.hint_index
               << ",\"variant\":" << row.variant
               << ",\"group_size\":" << row.group_size
               << ",\"has_ssr\":" << (row.has_ssr ? "true" : "false")
               << ",\"has_protection\":"
               << (row.has_protection ? "true" : "false")
               << ",\"hard_keep\":" << (row.hard_keep ? "true" : "false")
               << ",\"retained_after_truncation\":"
               << (row.retained_after_truncation ? "true" : "false")
               << ",\"disposition\":\"" << row.disposition << "\"}";
    }
    output << ']';
}

std::vector<int> group_counts(const MasterProblem& problem) {
    std::vector<int> counts(problem.group_count, 0);
    for (const PatternRecord& pattern : problem.patterns) {
        if (pattern.group >= 0 && pattern.group < problem.group_count) {
            ++counts[pattern.group];
        }
    }
    return counts;
}

void emit_counts(std::ostream& output, const std::vector<int>& counts) {
    output << '[';
    for (size_t index = 0; index < counts.size(); ++index) {
        if (index) output << ',';
        output << counts[index];
    }
    output << ']';
}

void prefer_previous_incumbent(
    MasterProblem& problem, const std::vector<uint64_t>& selected
) {
    if (selected.empty()) return;
    std::unordered_map<int, uint64_t> preferred;
    for (uint64_t id : selected) {
        const auto found = std::find_if(
            problem.patterns.begin(), problem.patterns.end(),
            [id](const PatternRecord& pattern) { return pattern.id == id; }
        );
        if (found != problem.patterns.end()) preferred[found->group] = id;
    }
    std::stable_sort(
        problem.patterns.begin(), problem.patterns.end(),
        [&preferred](const PatternRecord& left, const PatternRecord& right) {
            const bool left_preferred = preferred.count(left.group)
                && preferred.at(left.group) == left.id;
            const bool right_preferred = preferred.count(right.group)
                && preferred.at(right.group) == right.id;
            return left_preferred && !right_preferred;
        }
    );
    for (PatternRecord& pattern : problem.patterns) {
        if (preferred.count(pattern.group)
            && preferred.at(pattern.group) == pattern.id) {
            pattern.hard_keep = true;
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int argument = 1;
    native_solver::PatternSchedule schedule =
        native_solver::PatternSchedule::GroupMajor;
    std::vector<uint64_t> external_watched;
    bool probe_5_only = false;
    while (argument < argc) {
        const std::string value = argv[argument];
        if (value == "--round-robin") {
            schedule = native_solver::PatternSchedule::RoundRobin;
            ++argument;
            continue;
        }
        if (value == "--probe-5-only") {
            probe_5_only = true;
            ++argument;
            continue;
        }
        const std::string prefix = "--watch-ids=";
        if (value.rfind(prefix, 0) == 0) {
            std::istringstream ids(value.substr(prefix.size()));
            std::string item;
            while (std::getline(ids, item, ',')) {
                if (!item.empty()) external_watched.push_back(std::stoull(item));
            }
            ++argument;
            continue;
        }
        break;
    }
    const double master_seconds = argument < argc
        ? std::stod(argv[argument++]) : 0.2;
    std::vector<double> budgets;
    for (; argument < argc; ++argument) {
        budgets.push_back(std::stod(argv[argument]));
    }
    if (budgets.empty()) budgets = {5.0, 10.0, 20.0};
    if (!std::is_sorted(budgets.begin(), budgets.end())) return 2;
    const std::string input_payload{
        std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>()
    };
    if (input_payload.empty()) return 2;

    MasterProblem cumulative;
    std::unordered_set<uint64_t> cumulative_ids;
    std::vector<uint64_t> diagnostic_watched = external_watched;
    std::vector<uint64_t> previous_selected;
    std::vector<std::string> run_payloads;

    for (double budget : budgets) {
        GenerationResult generated = generate(
            input_payload, budget, master_seconds, diagnostic_watched, schedule
        );
        if (generated.problem.group_count < 0) return 3;
        if (cumulative.group_count == 0) {
            cumulative.group_count = generated.problem.group_count;
            cumulative.seat_count = generated.problem.seat_count;
            cumulative.ssr_count = generated.problem.ssr_count;
            cumulative.full_load = generated.problem.full_load;
            cumulative.group_ids = generated.problem.group_ids;
            cumulative.baby_pairs = generated.problem.baby_pairs;
        }
        std::unordered_set<uint64_t> raw_ids;
        for (const PatternRecord& pattern : generated.problem.patterns) {
            raw_ids.insert(pattern.id);
        }
        int raw_missing_previous_pool = 0;
        for (uint64_t id : cumulative_ids) {
            if (!raw_ids.count(id)) ++raw_missing_previous_pool;
        }
        int added = 0;
        for (PatternRecord& pattern : generated.problem.patterns) {
            if (cumulative_ids.insert(pattern.id).second) {
                cumulative.patterns.push_back(std::move(pattern));
                ++added;
            }
        }
        int carried = 0;
        for (const PatternRecord& pattern : cumulative.patterns) {
            if (!raw_ids.count(pattern.id)) ++carried;
        }
        prefer_previous_incumbent(cumulative, previous_selected);
        const auto master_started = std::chrono::steady_clock::now();
        const bool skip_master = generated.remaining_seconds <= 0.05;
        const double effective_master_seconds = skip_master
            ? 0.0
            : std::min(master_seconds, generated.remaining_seconds - 0.05);
        std::ostringstream master_output;
        std::vector<uint64_t> selected;
        native_master::MasterSolveOptions master_options;
        master_options.incumbent_pattern_ids = previous_selected;
        if (native_master::solve(
            cumulative, master_output, effective_master_seconds, skip_master,
            &selected, &master_options
        ) != 0) return 4;
        const double master_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - master_started
        ).count();
        for (uint64_t id : selected) {
            if (std::find(
                diagnostic_watched.begin(), diagnostic_watched.end(), id
            ) == diagnostic_watched.end()) {
                diagnostic_watched.push_back(id);
            }
        }
        previous_selected = selected;
        const std::string master_payload = master_output.str();
        if (master_payload.rfind("{\n", 0) != 0) return 4;
        std::ostringstream run;
        run << "{\n  \"budget_seconds\":" << budget
            << ",\n  \"generation_ms\":" << generated.generation_ms
            << ",\n  \"master_ms\":" << master_ms
            << ",\n  \"generation_cutoff\":"
            << (generated.cutoff ? "true" : "false")
            << ",\n  \"raw_pool_patterns\":" << raw_ids.size()
            << ",\n  \"cumulative_pool_patterns\":"
            << cumulative.patterns.size()
            << ",\n  \"new_patterns_added\":" << added
            << ",\n  \"carried_shorter_pool_patterns\":" << carried
            << ",\n  \"raw_missing_previous_pool_patterns\":"
            << raw_missing_previous_pool
            << ",\n  \"raw_group_pattern_counts\":";
        emit_counts(run, group_counts(generated.problem));
        run << ",\n  \"cumulative_group_pattern_counts\":";
        emit_counts(run, group_counts(cumulative));
        run << ",\n  \"group_hint0_variant0_coverage\":";
        emit_group_coverage(
            run, generated.problem.group_coverage_diagnostics
        );
        run << ",\n  \"raw_pattern_diagnostics\":";
        emit_diagnostics(run, generated.problem.pattern_diagnostics);
        run << ",\n" << master_payload.substr(2);
        run_payloads.push_back(run.str());
    }

    std::vector<std::string> probe_payloads;
    std::vector<uint64_t> probe_watched = external_watched;
    for (uint64_t id : previous_selected) {
        if (std::find(probe_watched.begin(), probe_watched.end(), id)
            == probe_watched.end()) probe_watched.push_back(id);
    }
    for (double budget : budgets) {
        if (budget >= budgets.back()) break;
        if (probe_5_only && budget != budgets.front()) continue;
        GenerationResult probe = generate(
            input_payload, budget, master_seconds, probe_watched, schedule
        );
        if (probe.problem.group_count < 0) return 5;
        std::ostringstream payload;
        payload << "{\"budget_seconds\":" << budget
                << ",\"generation_ms\":" << probe.generation_ms
                << ",\"generation_cutoff\":"
                << (probe.cutoff ? "true" : "false")
                << ",\"watched_final_pattern_diagnostics\":";
        emit_diagnostics(payload, probe.problem.pattern_diagnostics);
        payload << '}';
        probe_payloads.push_back(payload.str());
    }

    std::cout << "{\n  \"schema\":\"native_monotonic_retention_v1\",\n"
              << "  \"schedule\":\""
              << (schedule == native_solver::PatternSchedule::RoundRobin
                  ? "round_robin" : "group_major") << "\",\n"
              << "  \"external_watched_pattern_count\":"
              << external_watched.size() << ",\n"
              << "  \"retention_protocol\":\"union_all_shorter_formal_pools\",\n"
              << "  \"runs\":[";
    for (size_t index = 0; index < run_payloads.size(); ++index) {
        if (index) std::cout << ',';
        std::cout << run_payloads[index];
    }
    std::cout << "],\n  \"selected_final_pattern_probes\":[";
    for (size_t index = 0; index < probe_payloads.size(); ++index) {
        if (index) std::cout << ',';
        std::cout << probe_payloads[index];
    }
    std::cout << "]\n}\n";
    return 0;
}
