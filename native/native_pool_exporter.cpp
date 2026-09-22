#include "native_master_types.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

namespace {

std::string budget_label(double budget) {
    std::ostringstream label;
    label << std::fixed << std::setprecision(3) << budget;
    std::string value = label.str();
    while (!value.empty() && value.back() == '0') value.pop_back();
    if (!value.empty() && value.back() == '.') value.pop_back();
    std::replace(value.begin(), value.end(), '.', 'p');
    return value;
}

void write_diagnostics(
    const std::filesystem::path& path,
    const std::vector<native_solver::PatternDiagnostic>& rows
) {
    std::ofstream output(path);
    output << "pattern_id\tgroup\tgroup_id\tdiscovery_ms\tsource\thint\t"
              "hint_index\tvariant\tgroup_size\thas_ssr\thas_protection\t"
              "hard_keep\tretained_after_truncation\tdisposition\n";
    for (const auto& row : rows) {
        output << row.id << '\t' << row.group << '\t' << row.group_id << '\t'
               << std::setprecision(17) << row.discovery_ms << '\t'
               << row.source << '\t' << row.hint << '\t' << row.hint_index
               << '\t' << row.variant << '\t' << row.group_size << '\t'
               << int(row.has_ssr) << '\t' << int(row.has_protection) << '\t'
               << int(row.hard_keep) << '\t'
               << int(row.retained_after_truncation) << '\t'
               << row.disposition << '\n';
    }
}

void write_ids(std::ostream& output, const std::vector<uint64_t>& ids) {
    for (size_t index = 0; index < ids.size(); ++index) {
        if (index) output << ',';
        output << ids[index];
    }
}

void write_task_diagnostics(
    const std::filesystem::path& path,
    const std::vector<native_solver::TaskDiagnostic>& rows
) {
    std::ofstream output(path);
    output << "task_index\tgroup\tgroup_id\tgroup_size\thas_ssr\thas_protection\t"
              "hint_index\tvariant\tstart_ms\tend_ms\truntime_ms\t"
              "beam_expansion_count\tproduced_pattern_count\t"
              "beam_prune_count\tcomplete_state_count\t"
              "legal_complete_pattern_count\tunique_complete_resource_signatures\t"
              "new_unique_pattern_count\tduplicate_pattern_count\t"
              "retained_pattern_count\tadmitted\tcompleted\ttermination_reason\t"
              "committed_pattern_count\tpool_hash_after_task\tproduced_pattern_ids\t"
              "first_discovered_pattern_ids\n";
    for (const auto& row : rows) {
        output << row.task_index << '\t' << row.group << '\t' << row.group_id
               << '\t' << row.group_size
               << '\t' << int(row.has_ssr) << '\t' << int(row.has_protection)
               << '\t' << row.hint_index << '\t' << row.variant << '\t'
               << std::setprecision(17) << row.start_ms << '\t' << row.end_ms
               << '\t' << row.end_ms - row.start_ms << '\t'
               << row.beam_expansion_count << '\t'
               << row.produced_pattern_count << '\t'
               << row.beam_prune_count << '\t'
               << row.complete_state_count << '\t'
               << row.legal_complete_pattern_count << '\t'
               << row.unique_complete_resource_signatures << '\t'
               << row.new_unique_pattern_count << '\t'
               << row.duplicate_pattern_count << '\t'
               << row.retained_pattern_count << '\t' << int(row.admitted)
               << '\t' << int(row.completed) << '\t' << row.termination_reason
               << '\t' << row.committed_pattern_count << '\t'
               << row.pool_hash_after_task
               << '\t';
        write_ids(output, row.produced_pattern_ids);
        output << '\t';
        write_ids(output, row.first_discovered_pattern_ids);
        output << '\n';
    }
}

void write_pass0_diagnostics(
    const std::filesystem::path& path,
    const std::vector<native_solver::Pass0GroupDiagnostic>& rows
) {
    std::ofstream output(path);
    output << "group\tgroup_id\tgroup_size\thas_ssr\thas_protection\t"
              "pattern_count\tbest_local_pattern_score\tincumbent_pattern_score\t"
              "best_vs_incumbent_local_gap\tresource_signature_count\t"
              "resource_signature_diversity\n";
    for (const auto& row : rows) {
        output << row.group << '\t' << row.group_id << '\t' << row.group_size
               << '\t' << int(row.has_ssr) << '\t' << int(row.has_protection)
               << '\t' << row.pattern_count << '\t' << std::setprecision(17)
               << row.best_local_pattern_score << '\t'
               << row.incumbent_pattern_score << '\t'
               << row.best_vs_incumbent_local_gap << '\t'
               << row.resource_signature_count << '\t'
               << row.resource_signature_diversity << '\n';
    }
}

std::vector<native_solver::TargetTraceSpec> read_target_trace_specs(
    const std::filesystem::path& path
) {
    std::ifstream input(path);
    std::vector<native_solver::TargetTraceSpec> specs;
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty()) continue;
        std::istringstream row(line);
        uint64_t pattern_id;
        int group_id, choice_count;
        if (!(row >> pattern_id >> group_id >> choice_count)) {
            throw std::runtime_error("invalid target trace specification");
        }
        native_solver::TargetTraceSpec spec{pattern_id, group_id, {}};
        spec.choices.resize(choice_count);
        for (int& choice : spec.choices) row >> choice;
        if (!row) throw std::runtime_error("invalid target trace specification");
        if (row >> spec.snapshot_hint_index) {
            if (!(row >> spec.snapshot_variant >> spec.snapshot_depth)) {
                throw std::runtime_error("incomplete layer snapshot specification");
            }
        }
        std::string extra;
        if (row >> extra) throw std::runtime_error("trailing target trace specification");
        specs.push_back(std::move(spec));
    }
    return specs;
}

std::vector<native_solver::GroupResourcePressure> read_component_pressure(
    const std::filesystem::path& path
) {
    std::ifstream input(path);
    std::string tag;
    int group_count, seat_count, ssr_count;
    if (!(input >> tag >> group_count >> seat_count >> ssr_count)
        || tag != "PRESSURE_V1" || group_count < 0 || seat_count < 0
        || ssr_count < 0) {
        throw std::runtime_error("invalid component pressure header");
    }
    std::vector<native_solver::GroupResourcePressure> rows;
    rows.reserve(group_count);
    for (int group = 0; group < group_count; ++group) {
        native_solver::GroupResourcePressure row;
        if (!(input >> tag >> row.group_id) || tag != "GROUP") {
            throw std::runtime_error("invalid component pressure group");
        }
        row.seat_resources.resize(seat_count);
        row.ssr_resources.resize(ssr_count);
        if (!(input >> tag) || tag != "SEAT") {
            throw std::runtime_error("invalid component seat pressure");
        }
        for (double& value : row.seat_resources) input >> value;
        if (!(input >> tag) || tag != "SSR") {
            throw std::runtime_error("invalid component SSR pressure");
        }
        for (double& value : row.ssr_resources) input >> value;
        if (!input) throw std::runtime_error("truncated component pressure");
        rows.push_back(std::move(row));
    }
    std::string extra;
    if (input >> extra) throw std::runtime_error("trailing component pressure data");
    return rows;
}

void write_target_trace(
    const std::filesystem::path& path,
    const std::vector<native_solver::TargetTraceStep>& rows
) {
    std::ofstream output(path);
    output << "pattern_id\tgroup_id\thint_index\tvariant\tdepth\ttotal_depth\t"
              "target_option\tcandidate_rank_before_cap\tcandidate_count_before_cap\t"
              "in_candidate_domain\tafter_candidate_cap\tparent_prefix_in_beam\t"
              "expanded\tchild_prefix_generated\tchild_rank_before_beam_cap\t"
              "child_count_before_beam_cap\tsurvived_beam\tbeam_width\t"
              "survived_main_beam\tsurvived_reserve_beam\t"
              "child_rank_score\tdeadline_hit\tchild_individual_score\t"
              "cutoff_rank_score\tcutoff_individual_score\tmedian_rank_score\t"
              "median_individual_score\ttop_decile_rank_score\t"
              "top_decile_individual_score\tcutoff_band_state_count\t"
              "cutoff_band_unique_resource_signatures\t"
              "cutoff_band_unique_row_signatures\t"
              "cutoff_band_max_row_signature_multiplicity\n";
    for (const auto& row : rows) {
        output << row.pattern_id << '\t' << row.group_id << '\t'
               << row.hint_index << '\t' << row.variant << '\t' << row.depth
               << '\t' << row.total_depth << '\t' << row.target_option << '\t'
               << row.candidate_rank_before_cap << '\t'
               << row.candidate_count_before_cap << '\t'
               << int(row.in_candidate_domain) << '\t'
               << int(row.after_candidate_cap) << '\t'
               << int(row.parent_prefix_in_beam) << '\t' << int(row.expanded)
               << '\t' << int(row.child_prefix_generated) << '\t'
               << row.child_rank_before_beam_cap << '\t'
               << row.child_count_before_beam_cap << '\t'
               << int(row.survived_beam) << '\t' << row.beam_width << '\t'
               << int(row.survived_main_beam) << '\t'
               << int(row.survived_reserve_beam) << '\t'
               << std::setprecision(17) << row.child_rank_score << '\t'
               << int(row.deadline_hit) << '\t' << row.child_individual_score
               << '\t' << row.cutoff_rank_score << '\t'
               << row.cutoff_individual_score << '\t' << row.median_rank_score
               << '\t' << row.median_individual_score << '\t'
               << row.top_decile_rank_score << '\t'
               << row.top_decile_individual_score << '\t'
               << row.cutoff_band_state_count << '\t'
               << row.cutoff_band_unique_resource_signatures << '\t'
               << row.cutoff_band_unique_row_signatures << '\t'
               << row.cutoff_band_max_row_signature_multiplicity << '\n';
    }
}

void write_layer_snapshots(
    const std::filesystem::path& path,
    const std::vector<native_solver::LayerStateSnapshot>& rows
) {
    std::ofstream output(path);
    output << "pattern_id\tgroup_id\thint_index\tvariant\tdepth\trank\tstate_count\t"
              "individual_score\trank_score\tl1_valid\tl1_completion_individual_score\t"
              "l1_counterfactual_key\tl2_valid\tl2_completion_individual_score\t"
              "l2_counterfactual_key\tchoices\n";
    for (const auto& row : rows) {
        output << row.pattern_id << '\t' << row.group_id << '\t' << row.hint_index
               << '\t' << row.variant << '\t' << row.depth << '\t' << row.rank
               << '\t' << row.state_count << '\t' << std::setprecision(17)
               << row.individual_score << '\t' << row.rank_score << '\t';
        output << int(row.l1_valid) << '\t' << row.l1_completion_individual_score
               << '\t' << row.l1_counterfactual_key << '\t' << int(row.l2_valid)
               << '\t' << row.l2_completion_individual_score << '\t'
               << row.l2_counterfactual_key << '\t';
        for (size_t index = 0; index < row.choices.size(); ++index) {
            if (index) output << ',';
            output << row.choices[index];
        }
        output << '\n';
    }
}

}  // namespace

int main(int argc, char** argv) {
    const auto process_main_started = std::chrono::steady_clock::now();
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    if (argc < 4) return 2;
    const std::filesystem::path output_dir(argv[1]);
    const std::string schedule_name(argv[2]);
    const native_solver::PatternSchedule schedule = schedule_name == "round_robin"
        ? native_solver::PatternSchedule::RoundRobin
        : native_solver::PatternSchedule::GroupMajor;
    if (schedule_name != "group_major" && schedule_name != "round_robin") return 2;
    int first_budget_argument = 3;
    bool collect_task_diagnostics = false;
    bool master_only = false;
    native_solver::BeamReserveConfig beam_reserve;
    native_solver::PatternGenerationScope generation_scope;
    double rr_task_admission_guard_seconds = 0.0;
    std::vector<native_solver::TargetTraceSpec> target_trace_specs;
    while (first_budget_argument < argc) {
        const std::string option(argv[first_budget_argument]);
        if (option == "--task-diagnostics") {
            collect_task_diagnostics = true;
            ++first_budget_argument;
        } else if (option == "--master-only") {
            master_only = true;
            ++first_budget_argument;
        } else if (option == "--target-trace") {
            if (++first_budget_argument >= argc) return 2;
            target_trace_specs = read_target_trace_specs(argv[first_budget_argument++]);
        } else if (option == "--beam-reserve") {
            if (first_budget_argument + 2 >= argc) return 2;
            beam_reserve.enabled = true;
            beam_reserve.reserve_cap = std::stoi(argv[first_budget_argument + 1]);
            beam_reserve.per_bucket_cap = std::stoi(argv[first_budget_argument + 2]);
            first_budget_argument += 3;
            if (beam_reserve.reserve_cap <= 0 || beam_reserve.per_bucket_cap <= 0) {
                return 2;
            }
        } else if (option == "--component-aware-pressure") {
            if (++first_budget_argument >= argc) return 2;
            generation_scope.component_aware_ordering = true;
            generation_scope.diverse_complete_retention = true;
            generation_scope.group_resource_pressure = read_component_pressure(
                argv[first_budget_argument++]
            );
        } else if (option == "--group") {
            if (++first_budget_argument >= argc) return 2;
            generation_scope.group_ids.push_back(
                std::stoi(argv[first_budget_argument++])
            );
        } else if (option == "--pattern-limit") {
            if (++first_budget_argument >= argc) return 2;
            generation_scope.pattern_limit_override = std::stoi(
                argv[first_budget_argument++]
            );
            if (generation_scope.pattern_limit_override <= 0) return 2;
        } else if (option == "--task-limit-per-group") {
            if (++first_budget_argument >= argc) return 2;
            generation_scope.task_limit_per_group = std::stoi(
                argv[first_budget_argument++]
            );
            if (generation_scope.task_limit_per_group <= 0) return 2;
        } else if (option == "--rr-task-admission-guard-ms") {
            if (++first_budget_argument >= argc) return 2;
            rr_task_admission_guard_seconds =
                std::stod(argv[first_budget_argument++]) / 1000.0;
            if (rr_task_admission_guard_seconds < 0.0) return 2;
        } else {
            break;
        }
    }
    std::sort(generation_scope.group_ids.begin(), generation_scope.group_ids.end());
    generation_scope.group_ids.erase(std::unique(
        generation_scope.group_ids.begin(), generation_scope.group_ids.end()
    ), generation_scope.group_ids.end());
    std::vector<uint64_t> watched_target_ids;
    watched_target_ids.reserve(target_trace_specs.size());
    for (const auto& spec : target_trace_specs) {
        watched_target_ids.push_back(spec.pattern_id);
    }
    const bool collect_layer_snapshots = std::any_of(
        target_trace_specs.begin(), target_trace_specs.end(),
        [](const auto& spec) { return spec.snapshot_depth >= 0; }
    );
    std::vector<native_solver::TargetTraceSpec> formal_trace_specs = target_trace_specs;
    for (auto& spec : formal_trace_specs) {
        spec.snapshot_hint_index = -1;
        spec.snapshot_variant = -1;
        spec.snapshot_depth = -1;
    }
    if (first_budget_argument >= argc) return 2;
    std::vector<double> budgets;
    for (int argument = first_budget_argument; argument < argc; ++argument) {
        budgets.push_back(std::stod(argv[argument]));
    }
    if (!std::is_sorted(budgets.begin(), budgets.end())) return 2;
    const std::string input_payload{
        std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>()
    };
    const double payload_read_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - process_main_started
    ).count();
    if (input_payload.empty()) return 2;
    std::filesystem::create_directories(output_dir);

    native_solver::MasterProblem cumulative;
    std::unordered_set<uint64_t> cumulative_ids;
    std::cout << "{\n  \"schema\":\"native_pool_export_v1\",\n"
              << "  \"schedule\":\"" << schedule_name << "\",\n"
              << "  \"beam_reserve\":{\"enabled\":"
              << (beam_reserve.enabled ? "true" : "false")
              << ",\"reserve_cap\":" << beam_reserve.reserve_cap
              << ",\"per_bucket_cap\":" << beam_reserve.per_bucket_cap << "},\n"
              << "  \"generation_scope\":{\"group_ids\":[";
    for (size_t index = 0; index < generation_scope.group_ids.size(); ++index) {
        if (index) std::cout << ',';
        std::cout << generation_scope.group_ids[index];
    }
    std::cout << "],\"pattern_limit_override\":"
              << generation_scope.pattern_limit_override
              << ",\"task_limit_per_group\":"
              << generation_scope.task_limit_per_group
              << ",\"component_aware_ordering\":"
              << (generation_scope.component_aware_ordering ? "true" : "false")
              << ",\"diverse_complete_retention\":"
              << (generation_scope.diverse_complete_retention ? "true" : "false")
              << "},\n"
              << "  \"retention_protocol\":\"union_all_shorter_formal_pools\",\n"
              << "  \"runs\":[";
    for (size_t budget_index = 0; budget_index < budgets.size(); ++budget_index) {
        const double budget = budgets[budget_index];
        const auto started = std::chrono::steady_clock::now();
        const auto deadline = started + std::chrono::duration_cast<
            std::chrono::steady_clock::duration
        >(std::chrono::duration<double>(budget));
        native_solver::MasterProblem generated;
        bool cutoff = false;
        std::istringstream input(input_payload);
        std::ostringstream unused_output;
        if (run_native_pattern_kernel(
            input, unused_output, std::cerr, false, &generated, &deadline,
            &cutoff,
            watched_target_ids.empty() ? nullptr : &watched_target_ids,
            schedule, collect_task_diagnostics,
            formal_trace_specs.empty() ? nullptr : &formal_trace_specs,
            beam_reserve, generation_scope, rr_task_admission_guard_seconds
        ) != 0) return 3;
        if (collect_layer_snapshots) {
            native_solver::MasterProblem shadow;
            std::istringstream shadow_input(input_payload);
            std::ostringstream shadow_output;
            if (run_native_pattern_kernel(
                shadow_input, shadow_output, std::cerr, false, &shadow, nullptr,
                nullptr, watched_target_ids.empty() ? nullptr : &watched_target_ids,
                schedule, false, &target_trace_specs, beam_reserve,
                generation_scope
            ) != 0) return 3;
            generated.layer_state_snapshots = std::move(shadow.layer_state_snapshots);
        }
        const double generation_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - started
        ).count();
        if (cumulative.group_count == 0) {
            cumulative.group_count = generated.group_count;
            cumulative.seat_count = generated.seat_count;
            cumulative.ssr_count = generated.ssr_count;
            cumulative.full_load = generated.full_load;
            cumulative.group_ids = generated.group_ids;
            cumulative.baby_pairs = generated.baby_pairs;
        }
        std::unordered_set<uint64_t> generated_ids;
        for (const auto& pattern : generated.patterns) {
            generated_ids.insert(pattern.id);
        }
        int added = 0;
        for (auto& pattern : generated.patterns) {
            if (cumulative_ids.insert(pattern.id).second) {
                cumulative.patterns.push_back(std::move(pattern));
                ++added;
            }
        }
        const std::string label = budget_label(budget);
        const auto pool_path = output_dir / ("pool_" + label + ".master_v1.txt");
        const auto serialization_started = std::chrono::steady_clock::now();
        std::ofstream pool_output(pool_path);
        native_solver::write_master_problem(pool_output, cumulative);
        pool_output.close();
        const double pool_serialization_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - serialization_started
        ).count();
        if (!master_only) {
            write_diagnostics(
                output_dir / ("diagnostics_" + label + ".tsv"),
                generated.pattern_diagnostics
            );
        }
        if (collect_task_diagnostics) {
            write_task_diagnostics(
                output_dir / ("task_diagnostics_" + label + ".tsv"),
                generated.task_diagnostics
            );
            write_pass0_diagnostics(
                output_dir / ("pass0_group_diagnostics_" + label + ".tsv"),
                generated.pass0_group_diagnostics
            );
        }
        if (!target_trace_specs.empty()) {
            write_target_trace(
                output_dir / ("target_trace_" + label + ".tsv"),
                generated.target_trace_steps
            );
            write_layer_snapshots(
                output_dir / ("layer_states_" + label + ".tsv"),
                generated.layer_state_snapshots
            );
        }
        if (budget_index) std::cout << ',';
        std::cout << "{\"budget_seconds\":" << budget
                  << ",\"generation_ms\":" << std::setprecision(17)
                  << generation_ms
                  << ",\"payload_read_ms\":" << payload_read_ms
                  << ",\"input_parse_ms\":" << generated.input_parse_ms
                  << ",\"group_preprocess_ms\":"
                  << generated.group_preprocess_ms
                  << ",\"search_ms\":" << generated.search_ms
                  << ",\"pattern_materialization_ms\":"
                  << generated.pattern_materialization_ms
                  << ",\"pool_serialization_ms\":" << pool_serialization_ms
                  << ",\"cutoff\":" << (cutoff ? "true" : "false")
                  << ",\"generated_pattern_count\":"
                  << generated.patterns.size()
                  << ",\"unique_generated_pattern_count\":"
                  << generated_ids.size()
                  << ",\"new_unique_patterns\":" << added
                  << ",\"cumulative_unique_pattern_count\":"
                  << cumulative.patterns.size()
                  << ",\"task_diagnostic_count\":"
                  << generated.task_diagnostics.size()
                  << ",\"pass0_group_diagnostic_count\":"
                  << generated.pass0_group_diagnostics.size()
                  << ",\"layer_snapshot_shadow_replay\":"
                  << (collect_layer_snapshots ? "true" : "false") << '}';
    }
    std::cout << "]\n}\n";
    return 0;
}
