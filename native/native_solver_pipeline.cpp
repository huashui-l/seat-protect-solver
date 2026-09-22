#include "native_master_types.hpp"

#include <chrono>
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

static void emit_group_coverage(
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

static uint64_t hash_pattern_ids(const std::vector<uint64_t>& identifiers) {
    uint64_t value = 1469598103934665603ULL;
    for (uint64_t identifier : identifiers) {
        for (int byte = 0; byte < 8; ++byte) {
            value ^= identifier & 0xffULL;
            value *= 1099511628211ULL;
            identifier >>= 8;
        }
    }
    return value;
}

static double json_number(const std::string& payload, const std::string& key) {
    const std::string marker = "\"" + key + "\":";
    const size_t begin = payload.find(marker);
    if (begin == std::string::npos) return -std::numeric_limits<double>::infinity();
    size_t used = 0;
    try {
        return std::stod(payload.substr(begin + marker.size()), &used);
    } catch (...) {
        return -std::numeric_limits<double>::infinity();
    }
}

static bool same_static_problem(
    const native_solver::MasterProblem& left,
    const native_solver::MasterProblem& right
) {
    if (
        left.group_count != right.group_count
        || left.seat_count != right.seat_count
        || left.ssr_count != right.ssr_count
        || left.full_load != right.full_load
        || left.group_ids != right.group_ids
        || left.baby_pairs.size() != right.baby_pairs.size()
    ) return false;
    for (size_t index = 0; index < left.baby_pairs.size(); ++index) {
        const auto& a = left.baby_pairs[index];
        const auto& b = right.baby_pairs[index];
        if (
            a.infant_seat != b.infant_seat
            || a.occupant_seat != b.occupant_seat
            || a.cost != b.cost
        ) return false;
    }
    return true;
}

static void merge_memory_patterns(
    native_solver::MasterProblem& master_problem,
    native_solver::MasterProblem& memory_problem,
    std::vector<uint64_t>& added
) {
    std::unordered_set<uint64_t> identifiers;
    for (const auto& pattern : master_problem.patterns) identifiers.insert(pattern.id);
    for (auto& pattern : memory_problem.patterns) {
        if (pattern.hard_keep || !identifiers.insert(pattern.id).second) continue;
        added.push_back(pattern.id);
        master_problem.patterns.push_back(std::move(pattern));
    }
}

int main(int argc, char** argv) {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    double master_time_limit = 0.2;
    double total_time_limit = 5.0;
    native_solver::PatternSchedule schedule = native_solver::PatternSchedule::GroupMajor;
    std::string input_path;
    std::string output_path;
    uint64_t seed = 0;
    native_solver::BeamReserveConfig beam_reserve;
    std::string memory_master_path;
    std::string deferred_memory_master_path;
    std::string memory_control_path;
    bool emit_rr_ids = false;
    bool collect_task_diagnostics = false;
    bool baseline_preserving_memory = false;
    double global_deadline_seconds = 5.0;
    double minimum_memory_start_slack_ms = 650.0;
    double final_safety_ms = 200.0;
    double rr_task_admission_guard_ms = 0.0;
    int first_option = 1;
    // Preserve the frozen V1 positional interface while exposing a normal native CLI.
    if (first_option < argc && std::string(argv[first_option]).rfind("--", 0) != 0) {
        master_time_limit = std::stod(argv[first_option++]);
        if (first_option < argc && std::string(argv[first_option]).rfind("--", 0) != 0) {
            total_time_limit = std::stod(argv[first_option++]);
        }
        if (first_option < argc && std::string(argv[first_option]).rfind("--", 0) != 0) {
            schedule = std::string(argv[first_option++]) == "round_robin"
                ? native_solver::PatternSchedule::RoundRobin
                : native_solver::PatternSchedule::GroupMajor;
        }
    }
    for (int index = first_option; index < argc;) {
        const std::string option = argv[index++];
        if (option == "--input" && index < argc) {
            input_path = argv[index++];
        } else if (option == "--output" && index < argc) {
            output_path = argv[index++];
        } else if (option == "--time-limit" && index < argc) {
            total_time_limit = std::stod(argv[index++]);
        } else if (option == "--master-time-limit" && index < argc) {
            master_time_limit = std::stod(argv[index++]);
        } else if (option == "--seed" && index < argc) {
            seed = std::stoull(argv[index++]);
        } else if (option == "--schedule" && index < argc) {
            const std::string value = argv[index++];
            if (value == "round_robin") {
                schedule = native_solver::PatternSchedule::RoundRobin;
            } else if (value == "group_major") {
                schedule = native_solver::PatternSchedule::GroupMajor;
            } else {
                return 2;
            }
        } else if (option == "--beam-reserve" && index + 1 < argc) {
            beam_reserve.enabled = true;
            beam_reserve.reserve_cap = std::stoi(argv[index++]);
            beam_reserve.per_bucket_cap = std::stoi(argv[index++]);
            if (beam_reserve.reserve_cap <= 0 || beam_reserve.per_bucket_cap <= 0) {
                return 2;
            }
        } else if (option == "--memory-master" && index < argc) {
            memory_master_path = argv[index++];
        } else if (option == "--emit-rr-ids") {
            emit_rr_ids = true;
        } else if (option == "--task-diagnostics") {
            collect_task_diagnostics = true;
        } else if (option == "--baseline-preserving-memory") {
            baseline_preserving_memory = true;
        } else if (option == "--deferred-memory-master" && index < argc) {
            deferred_memory_master_path = argv[index++];
        } else if (option == "--memory-control" && index < argc) {
            memory_control_path = argv[index++];
        } else if (option == "--global-deadline" && index < argc) {
            global_deadline_seconds = std::stod(argv[index++]);
        } else if (option == "--minimum-memory-start-slack-ms" && index < argc) {
            minimum_memory_start_slack_ms = std::stod(argv[index++]);
        } else if (option == "--final-safety-ms" && index < argc) {
            final_safety_ms = std::stod(argv[index++]);
        } else if (option == "--rr-task-admission-guard-ms" && index < argc) {
            rr_task_admission_guard_ms = std::stod(argv[index++]);
        } else {
            return 2;
        }
    }
    if (master_time_limit < 0.0 || total_time_limit < 0.0) return 2;
    // The current native search is deterministic. Accepting only seed zero makes
    // that contract explicit instead of silently pretending to use another seed.
    if (seed != 0) return 2;
    std::ifstream input_file;
    std::ofstream output_file;
    std::istream* solver_input = &std::cin;
    std::ostream* solver_output = &std::cout;
    if (!input_path.empty()) {
        input_file.open(input_path);
        if (!input_file) return 2;
        solver_input = &input_file;
    }
    if (!output_path.empty()) {
        output_file.open(output_path);
        if (!output_file) return 2;
        solver_output = &output_file;
    }
    if (baseline_preserving_memory && (
        !memory_master_path.empty()
        || deferred_memory_master_path.empty()
        || memory_control_path.empty()
        || global_deadline_seconds <= 0.0
        || minimum_memory_start_slack_ms < 0.0
        || final_safety_ms < 0.0
    )) return 2;
    const auto started = std::chrono::steady_clock::now();
    native_solver::MasterProblem memory_problem;
    if (!memory_master_path.empty()) {
        std::ifstream memory_input(memory_master_path);
        if (!memory_input || native_master::read_problem(memory_input, memory_problem) != 0) {
            return 2;
        }
    }
    const auto memory_loaded = std::chrono::steady_clock::now();
    const auto generation_started = std::chrono::steady_clock::now();
    const auto compute_started = generation_started;
    const double master_safety_margin = memory_master_path.empty() ? 0.05 : 0.0;
    const double master_reserve = std::max(
        0.15, master_time_limit + master_safety_margin
    );
    const auto generation_deadline = generation_started + std::chrono::duration_cast<
        std::chrono::steady_clock::duration
    >(std::chrono::duration<double>(std::max(0.0, total_time_limit - master_reserve)));
    native_solver::MasterProblem master_problem;
    std::ostringstream unused_output;
    bool generation_deadline_hit = false;
    if (run_native_pattern_kernel(
        *solver_input, unused_output, std::cerr, false, &master_problem,
        &generation_deadline, &generation_deadline_hit, nullptr, schedule,
        collect_task_diagnostics, nullptr, beam_reserve, {},
        rr_task_admission_guard_ms / 1000.0
    ) != 0) return 2;
    std::vector<uint64_t> rr_pattern_ids;
    rr_pattern_ids.reserve(master_problem.patterns.size());
    for (const auto& pattern : master_problem.patterns) {
        rr_pattern_ids.push_back(pattern.id);
    }
    std::vector<uint64_t> memory_pattern_ids_added;
    const auto memory_merge_started = std::chrono::steady_clock::now();
    if (!memory_master_path.empty()) {
        if (!same_static_problem(memory_problem, master_problem)) return 2;
        merge_memory_patterns(master_problem, memory_problem, memory_pattern_ids_added);
    }
    const auto memory_merged = std::chrono::steady_clock::now();
    const auto generated = std::chrono::steady_clock::now();
    const double remaining = std::chrono::duration<double>(
        compute_started + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(total_time_limit)
        ) - generated
    ).count();
    const bool master_skipped_due_to_deadline = (
        remaining <= master_safety_margin
    );
    const double effective_master_limit = master_skipped_due_to_deadline
        ? 0.0
        : std::min(master_time_limit, remaining - master_safety_margin);
    std::stringstream result;
    std::vector<uint64_t> baseline_selected_ids;
    if (native_master::solve(
        master_problem, result, effective_master_limit,
        master_skipped_due_to_deadline, &baseline_selected_ids
    ) != 0) return 3;
    const auto baseline_finished = std::chrono::steady_clock::now();
    const std::string baseline_payload = result.str();
    bool memory_not_started = baseline_preserving_memory;
    bool memory_reconstructed = false;
    bool augmented_master_started = false;
    bool augmentation_accepted = false;
    double memory_start_slack_ms = std::chrono::duration<double, std::milli>(
        started + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(global_deadline_seconds)
        ) - baseline_finished
    ).count();
    double deferred_memory_load_ms = 0.0;
    double augmented_master_ms = 0.0;
    if (baseline_preserving_memory
        && memory_start_slack_ms > minimum_memory_start_slack_ms) {
        memory_not_started = false;
        std::cerr << "V1R_MEMORY_REQUEST remaining_ms="
                  << std::setprecision(17) << memory_start_slack_ms << '\n'
                  << std::flush;
        const auto hard_deadline = started
            + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                std::chrono::duration<double>(global_deadline_seconds)
            );
        const auto final_safety = std::chrono::duration_cast<
            std::chrono::steady_clock::duration
        >(std::chrono::duration<double, std::milli>(final_safety_ms));
        while (std::chrono::steady_clock::now() < hard_deadline - final_safety) {
            std::ifstream control(memory_control_path);
            std::string state;
            if (control && std::getline(control, state) && state == "READY") {
                memory_reconstructed = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (memory_reconstructed) {
            const auto load_started = std::chrono::steady_clock::now();
            native_solver::MasterProblem deferred_problem;
            std::ifstream memory_input(deferred_memory_master_path);
            if (!memory_input
                || native_master::read_problem(memory_input, deferred_problem) != 0
                || !same_static_problem(deferred_problem, master_problem)) return 2;
            deferred_memory_load_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - load_started
            ).count();
            merge_memory_patterns(
                master_problem, deferred_problem, memory_pattern_ids_added
            );
            const double augmented_remaining = std::chrono::duration<double>(
                hard_deadline - std::chrono::steady_clock::now()
            ).count() - final_safety_ms / 1000.0;
            if (augmented_remaining > 0.0) {
                augmented_master_started = true;
                native_master::MasterSolveOptions options;
                options.incumbent_pattern_ids = baseline_selected_ids;
                std::stringstream augmented_result;
                std::vector<uint64_t> augmented_selected_ids;
                const auto augmented_started = std::chrono::steady_clock::now();
                if (native_master::solve(
                    master_problem, augmented_result,
                    std::min(master_time_limit, augmented_remaining), false,
                    &augmented_selected_ids, &options
                ) != 0) return 3;
                augmented_master_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - augmented_started
                ).count();
                if (json_number(augmented_result.str(), "soft_score")
                    > json_number(baseline_payload, "soft_score") + 1e-7) {
                    result.str(augmented_result.str());
                    result.clear();
                    augmentation_accepted = true;
                }
            }
        }
    }
    const auto finished = std::chrono::steady_clock::now();
    const double generation_ms = std::chrono::duration<double, std::milli>(
        generated - started
    ).count();
    const double master_ms = std::chrono::duration<double, std::milli>(
        finished - generated
    ).count();
    const double total_ms = std::chrono::duration<double, std::milli>(
        finished - started
    ).count();
    const double memory_load_ms = std::chrono::duration<double, std::milli>(
        memory_loaded - started
    ).count();
    const double memory_merge_ms = std::chrono::duration<double, std::milli>(
        memory_merged - memory_merge_started
    ).count();
    std::string payload = augmentation_accepted ? result.str() : baseline_payload;
    if (payload.rfind("{\n", 0) != 0) return 3;
    std::ostream& output = *solver_output;
    output << "{\n  \"pipeline_generation_ms\":" << std::setprecision(17)
              << generation_ms << ",\n  \"pipeline_master_ms\":" << master_ms
              << ",\n  \"pipeline_total_ms\":" << total_ms
              << ",\n  \"input_parse_ms\":" << master_problem.input_parse_ms
              << ",\n  \"group_preprocess_ms\":" << master_problem.group_preprocess_ms
              << ",\n  \"search_ms\":" << master_problem.search_ms
              << ",\n  \"pattern_materialization_ms\":"
              << master_problem.pattern_materialization_ms
              << ",\n  \"memory_load_ms\":" << memory_load_ms
              << ",\n  \"memory_merge_ms\":" << memory_merge_ms
              << ",\n  \"baseline_master_ms\":"
              << std::chrono::duration<double, std::milli>(
                     baseline_finished - generated
                 ).count()
              << ",\n  \"deferred_memory_load_ms\":" << deferred_memory_load_ms
              << ",\n  \"augmented_master_ms\":" << augmented_master_ms
              << ",\n  \"memory_start_slack_ms\":" << memory_start_slack_ms
              << ",\n  \"minimum_memory_start_slack_ms\":"
              << minimum_memory_start_slack_ms
              << ",\n  \"memory_not_started\":"
              << (memory_not_started ? "true" : "false")
              << ",\n  \"memory_reconstructed\":"
              << (memory_reconstructed ? "true" : "false")
              << ",\n  \"augmented_master_started\":"
              << (augmented_master_started ? "true" : "false")
              << ",\n  \"augmentation_accepted\":"
              << (augmentation_accepted ? "true" : "false")
              << ",\n  \"baseline_soft_score\":"
              << json_number(baseline_payload, "soft_score")
              << ",\n  \"baseline_selected_pattern_ids\":[";
    for (size_t index = 0; index < baseline_selected_ids.size(); ++index) {
        if (index) output << ',';
        output << '"' << baseline_selected_ids[index] << '"';
    }
    output << ']'
              << ",\n  \"pipeline_budget_ms\":" << total_time_limit * 1000.0
              << ",\n  \"generation_deadline_hit\":"
              << (generation_deadline_hit ? "true" : "false")
              << ",\n  \"rr_admission_guard_stop\":"
              << (master_problem.rr_admission_guard_stop ? "true" : "false")
              << ",\n  \"rr_task_admission_guard_ms\":"
              << master_problem.rr_task_admission_guard_ms
              << ",\n  \"effective_master_limit_ms\":"
              << effective_master_limit * 1000.0
              << ",\n  \"master_safety_margin_ms\":"
              << master_safety_margin * 1000.0
              << ",\n  \"schedule\":\""
              << (schedule == native_solver::PatternSchedule::RoundRobin
                  ? "round_robin" : "group_major") << '\"'
              << ",\n  \"beam_reserve\":{\"enabled\":"
              << (beam_reserve.enabled ? "true" : "false")
              << ",\"reserve_cap\":" << beam_reserve.reserve_cap
              << ",\"per_bucket_cap\":" << beam_reserve.per_bucket_cap << '}'
              << ",\n  \"memory_patterns_added\":"
              << memory_pattern_ids_added.size()
              << ",\n  \"rr_pattern_id_hash\":\""
              << hash_pattern_ids(rr_pattern_ids) << '"'
              << ",\n  \"memory_pattern_ids_added\":[";
    for (size_t index = 0; index < memory_pattern_ids_added.size(); ++index) {
        if (index) output << ',';
        output << '"' << memory_pattern_ids_added[index] << '"';
    }
    output << ']'
              << ",\n  \"task_diagnostics\":[";
    for (size_t index = 0; index < master_problem.task_diagnostics.size(); ++index) {
        if (index) output << ',';
        const auto& row = master_problem.task_diagnostics[index];
        output << "{\"task_index\":" << row.task_index
                  << ",\"group\":" << row.group
                  << ",\"group_id\":" << row.group_id
                  << ",\"hint_index\":" << row.hint_index
                  << ",\"variant\":" << row.variant
                  << ",\"start_ms\":" << row.start_ms
                  << ",\"end_ms\":" << row.end_ms
                  << ",\"duration_ms\":" << row.end_ms - row.start_ms
                  << ",\"admitted\":" << (row.admitted ? "true" : "false")
                  << ",\"completed\":" << (row.completed ? "true" : "false")
                  << ",\"termination_reason\":\"" << row.termination_reason << '"'
                  << ",\"patterns_committed\":" << row.committed_pattern_count
                  << ",\"pool_hash_after_task\":\""
                  << row.pool_hash_after_task << "\"}";
    }
    output << ']'
              << ",\n  \"rr_pattern_ids\":[";
    if (emit_rr_ids) {
        for (size_t index = 0; index < rr_pattern_ids.size(); ++index) {
            if (index) output << ',';
            output << '"' << rr_pattern_ids[index] << '"';
        }
    }
    output << ']'
              << ",\n  \"group_hint0_variant0_coverage\":";
    emit_group_coverage(
        output, master_problem.group_coverage_diagnostics
    );
    output
              << ",\n  \"pipeline_bridge_bytes\":0"
              << ",\n" << payload.substr(2);
    return 0;
}
