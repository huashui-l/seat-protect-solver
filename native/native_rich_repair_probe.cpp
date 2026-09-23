#include "native_group_constructor.hpp"
#include "native_feasibility_solver.hpp"

#include <iomanip>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    try {
        const auto problem = full_cpp::load_problem(argv[1], argv[2]);
        const auto replay = native_json::parse_file(argv[3]);
        if (const auto* mode = replay.find("placement_options"); mode && mode->bool_or()) {
            const auto fixed = full_cpp::preprocess_fixed_seats(problem);
            const auto seats = [&](const std::vector<int>& indices) {
                std::cout << '[';
                for (size_t i = 0; i < indices.size(); ++i) {
                    if (i) std::cout << ',';
                    std::cout << '"' << problem.seats[indices[i]].id << '"';
                }
                std::cout << ']';
            };
            const auto location = [&](const full_cpp::RichSsrLocation& item) {
                if (item.subrow < 0) std::cout << "\"row\"," << item.row;
                else std::cout << "\"subrow\",[" << item.row << ',' << item.subrow << ']';
            };
            std::cout << std::setprecision(17) << '{';
            for (int g = 0; g < static_cast<int>(problem.groups.size()); ++g) {
                if (g) std::cout << ',';
                std::cout << '"' << problem.groups[g].id << "\":[";
                const auto options = full_cpp::build_rich_placement_options(problem, g, fixed);
                for (size_t p = 0; p < options.size(); ++p) {
                    if (p) std::cout << ',';
                    std::cout << '[';
                    for (size_t i = 0; i < options[p].size(); ++i) {
                        if (i) std::cout << ',';
                        const auto& option = options[p][i];
                        std::cout << "{\"passenger_index\":" << option.passenger_index
                            << ",\"passenger_key\":[" << problem.groups[g].id << ',' << problem.passengers[option.passenger].hostnum
                            << "],\"seat_id\":\"" << problem.seats[option.seat].id << "\",\"blocked\":";
                        seats(option.blocked);
                        std::cout << ",\"resources\":";
                        seats(option.resources);
                        std::cout << ",\"individual_cost\":" << option.individual_cost << ",\"ssr_resources\":[";
                        for (size_t j = 0; j < option.ssr_resources.size(); ++j) {
                            if (j) std::cout << ',';
                            std::cout << '[';
                            location(option.ssr_resources[j].location);
                            std::cout << ",\"" << option.ssr_resources[j].ssr << "\"]";
                        }
                        std::cout << "],\"ssr_flag_locations\":[";
                        for (size_t j = 0; j < option.ssr_flag_locations.size(); ++j) {
                            if (j) std::cout << ',';
                            std::cout << '[';
                            location(option.ssr_flag_locations[j]);
                            std::cout << ']';
                        }
                        std::cout << "],\"is_infant\":" << (option.is_infant ? "true" : "false") << '}';
                    }
                    std::cout << ']';
                }
                std::cout << ']';
            }
            std::cout << "}\n";
            return 0;
        }
        if (const auto* records = replay.find("elite_records")) {
            full_cpp::RichEliteStore store(static_cast<int>(replay.at("elite_limit").number));
            std::cout << std::setprecision(17) << "{\"snapshots\":[";
            bool first_snapshot = true;
            for (const auto& record : records->array) {
                full_cpp::RichElitePattern pattern;
                for (const auto& entry : record.at("assignments").array)
                    pattern.assignments.emplace_back(static_cast<int>(entry.array[0].number), entry.array[1].string);
                for (const auto& entry : record.at("blocked_by_host").array) {
                    std::vector<std::string> seats;
                    for (const auto& seat : entry.array[1].array) seats.push_back(seat.string);
                    pattern.blocked_by_host.emplace_back(static_cast<int>(entry.array[0].number), seats);
                }
                pattern.local_score = record.at("local_score").number;
                pattern.source = record.at("source").string;
                pattern.pinned = record.at("pinned").bool_or();
                std::map<std::string, int> owners;
                for (const auto& entry : record.at("owners").object)
                    owners[entry.first] = static_cast<int>(entry.second.number);
                if (const auto* assignments = record.find("state_assignments")) {
                    full_cpp::AssignmentState state(problem);
                    for (const auto& entry : assignments->array) {
                        const int p = static_cast<int>(entry.array[0].number);
                        const int seat = problem.seat_index.at(entry.array[1].string);
                        const int block = entry.array.size() > 2 ? problem.seat_index.at(entry.array[2].string) : -1;
                        if (!state.assign(p, seat, block)) throw std::runtime_error("invalid elite replay state");
                    }
                    store.record_candidate(static_cast<int>(record.at("group_id").number), std::move(pattern), state,
                                           record.at("conflict_diversity_active").bool_or());
                } else {
                    store.record(static_cast<int>(record.at("group_id").number), std::move(pattern), owners,
                                 record.at("conflict_diversity_active").bool_or());
                }
                if (!first_snapshot) std::cout << ',';
                first_snapshot = false;
                full_cpp::write_rich_elite_store(std::cout, store);
            }
            std::cout << "]}\n";
            return 0;
        }
        const auto* pipeline_mode = replay.find("construction_pipeline");
        const bool pipeline = pipeline_mode && pipeline_mode->bool_or();
        auto state = pipeline ? full_cpp::initialize_rich_assignment(problem)
                              : full_cpp::AssignmentState(problem);
        for (const auto& entry : replay.at("assignments").array) {
            const int passenger = static_cast<int>(entry.array.at(0).number);
            const int seat = problem.seat_index.at(entry.array.at(1).string);
            const int block = entry.array.size() > 2 && !entry.array[2].is_null()
                ? problem.seat_index.at(entry.array[2].string) : -1;
            if (!state.assign(passenger, seat, block)) throw std::runtime_error("invalid replay assignment");
        }
        if (const auto* mode = replay.find("repair_metrics_only"); mode && mode->bool_or()) {
            std::cout << std::setprecision(17) << "{\"repair_queue\":";
            full_cpp::write_rich_repair_queue(std::cout,
                full_cpp::build_rich_repair_queue(problem, state.passenger_to_seat), problem.groups.size());
            std::cout << ",\"conflict_diversity_active\":" << (full_cpp::rich_conflict_diversity_active(problem,
                full_cpp::build_rich_repair_queue(problem, state.passenger_to_seat)) ? "true" : "false") << "}\n";
            return 0;
        }
        std::vector<std::vector<int>> rankings;
        if (const auto* mode = replay.find("rank_only"); mode && mode->bool_or()) {
            const auto cache = full_cpp::build_rich_candidate_cache(problem, state);
            std::cout << std::setprecision(17) << "{\"passengers\":[";
            for (size_t p = 0; p < cache.rankings.size(); ++p) {
                if (p) std::cout << ',';
                std::cout << "{\"regret\":" << cache.owner_regrets[p] << ",\"costs\":[";
                for (size_t s = 0; s < cache.costs[p].size(); ++s) {
                    if (s) std::cout << ',';
                    std::cout << cache.costs[p][s];
                }
                std::cout << "],\"order\":[";
                for (size_t s = 0; s < cache.rankings[p].size(); ++s) {
                    if (s) std::cout << ',';
                    std::cout << cache.rankings[p][s];
                }
                std::cout << "]}";
            }
            std::cout << "]}\n";
            return 0;
        }
        if (!pipeline) for (const auto& row : replay.at("rankings").array) {
            rankings.emplace_back();
            for (const auto& seat : row.array) rankings.back().push_back(problem.seat_index.at(seat.string));
        }
        if (!pipeline && rankings.size() != problem.passengers.size()) throw std::runtime_error("ranking count mismatch");
        full_cpp::RichEliteStore captures(problem.rich.elite_patterns_per_group);
        full_cpp::GroupConstructionResult diagnostics;
        full_cpp::RichRemainingDiagnostics construction;
        full_cpp::RichOrdinaryVndDiagnostics vnd;
        int paired_added = 0;
        full_cpp::RichPairedRescueDiagnostics rescue;
        full_cpp::AssignmentSnapshot construction_state;
        full_cpp::ScoreComponents construction_score;
        int paired_passes = 0;
        if (const auto* vnd_mode = replay.find("vnd_prefix"); vnd_mode && vnd_mode->bool_or()) {
            vnd = full_cpp::improve_rich_ordinary_vnd(state, rankings,
                std::chrono::steady_clock::now() + std::chrono::seconds(60),
                replay.find("vnd_group_rebuild") && replay.at("vnd_group_rebuild").bool_or(),
                replay.find("vnd_caregiver_rebuild") && replay.at("vnd_caregiver_rebuild").bool_or());
        } else if (pipeline) {
            const auto cache = full_cpp::build_rich_candidate_cache(problem, state);
            const auto combined = full_cpp::construct_rich_assignment(problem, state, cache,
                std::chrono::steady_clock::now() + std::chrono::seconds(60));
            construction = combined.search;
            rescue = combined.rescue;
            paired_passes = combined.paired_passes;
            captures.capture(state, "construction");
            construction_state = state.save();
            construction_score = full_cpp::evaluate_score_components(problem, state.passenger_to_seat);
            full_cpp::repair_rich_assignment(problem, state, cache.rankings,
                std::chrono::steady_clock::now() + std::chrono::seconds(60), diagnostics);
            captures.capture(state, "repair");
            if (const auto* mode = replay.find("pipeline_vnd"); mode && mode->bool_or()) {
                diagnostics.rich_state = state.save();
                diagnostics.rich_rankings = cache.rankings;
                diagnostics.rich_candidate_complete = full_cpp::validate_complete_assignment(problem, state.passenger_to_seat) == 0;
                diagnostics.passenger_to_seat = state.passenger_to_seat;
                diagnostics.group_construction_score = full_cpp::evaluate_soft_score(problem, state.passenger_to_seat);
                diagnostics.rich_elite_store = captures;
                vnd = full_cpp::improve_rich_vnd_m2(problem,
                    std::chrono::steady_clock::now() + std::chrono::seconds(60), diagnostics);
                state.restore(diagnostics.rich_state);
                captures = diagnostics.rich_elite_store;
            }
        } else if (const auto* rescue_mode = replay.find("paired_rescue"); rescue_mode && rescue_mode->bool_or()) {
            auto cache = full_cpp::build_rich_candidate_cache(problem, state);
            cache.rankings = rankings;
            rescue = full_cpp::rescue_rich_paired_ssrs(problem, state, cache,
                std::chrono::steady_clock::now() + std::chrono::seconds(60));
        } else if (const auto* paired = replay.find("paired_ssrs"); paired && paired->bool_or()) {
            auto cache = full_cpp::build_rich_candidate_cache(problem, state);
            cache.rankings = rankings;
            paired_added = full_cpp::assign_rich_paired_ssrs(problem, state, cache,
                std::chrono::steady_clock::now() + std::chrono::seconds(60));
        } else if (const auto* mode = replay.find("construct_remaining"); mode && mode->bool_or()) {
            auto cache = full_cpp::build_rich_candidate_cache(problem, state);
            cache.rankings = rankings;
            std::vector<int> groups;
            for (int g = 0; g < static_cast<int>(problem.groups.size()); ++g) groups.push_back(g);
            construction = full_cpp::assign_rich_remaining(problem, state, cache, groups,
                std::chrono::steady_clock::now() + std::chrono::seconds(60));
        } else {
            full_cpp::repair_rich_assignment(problem, state, rankings,
                std::chrono::steady_clock::now() + std::chrono::seconds(60), diagnostics);
        }
        for (size_t seat = 0; seat < state.seat_to_passenger.size(); ++seat) {
            const int owner = state.seat_to_passenger[seat];
            if (owner >= 0 && state.passenger_to_seat[owner] != static_cast<int>(seat))
                throw std::runtime_error("orphan occupied seat after repair");
        }
        std::cout << std::setprecision(17) << "{\"paired_added\":" << paired_added
                  << ",\"groups_considered\":" << construction.groups_considered
                  << ",\"dfs_attempted\":" << construction.dfs_attempted
                  << ",\"dfs_succeeded\":" << construction.dfs_succeeded
                  << ",\"dfs_nodes\":" << construction.dfs_nodes
                  << ",\"beam_groups\":" << construction.beam_groups
                  << ",\"transaction_failures\":" << construction.transaction_failures
                  << ",\"attempted\":" << diagnostics.rich_repair_attempted
                  << ",\"repaired\":" << diagnostics.rich_repair_repaired
                  << ",\"unresolved\":" << diagnostics.rich_repair_unresolved
                  << ",\"search_nodes\":" << diagnostics.rich_repair_nodes << ",\"assignments\":[";
        for (size_t p = 0; p < problem.passengers.size(); ++p) {
            if (p) std::cout << ',';
            const int seat = state.passenger_to_seat[p];
            if (seat < 0) std::cout << "null";
            else std::cout << '"' << problem.seats[seat].id << '"';
        }
        std::cout << "],\"blocked\":[";
        for (size_t p = 0; p < problem.passengers.size(); ++p) {
            if (p) std::cout << ',';
            std::cout << '[';
            for (size_t b = 0; b < state.assigned_blocked[p].size(); ++b) {
                if (b) std::cout << ',';
                std::cout << '"' << problem.seats[state.assigned_blocked[p][b]].id << '"';
            }
            std::cout << ']';
        }
        std::cout << "],\"rescue\":{\"joint_rebuilds\":" << rescue.joint_rebuilds;
        const auto emit_keys = [&](const char* name, const std::vector<int>& keys) {
            std::cout << ",\"" << name << "\":[";
            for (size_t i = 0; i < keys.size(); ++i) {
                if (i) std::cout << ',';
                const auto& passenger = problem.passengers[keys[i]];
                std::cout << '[' << passenger.group_id << ',' << passenger.hostnum << ']';
            }
            std::cout << ']';
        };
        emit_keys("attempted", rescue.attempted);
        emit_keys("rescued", rescue.rescued);
        emit_keys("unresolved", rescue.unresolved);
        std::cout << '}';
        if (pipeline) {
            std::cout << ",\"paired_passes\":" << paired_passes << ",\"construction_assignments\":[";
            for (size_t p = 0; p < problem.passengers.size(); ++p) {
                if (p) std::cout << ',';
                const int seat = construction_state.passenger_to_seat[p];
                if (seat < 0) std::cout << "null";
                else std::cout << '"' << problem.seats[seat].id << '"';
            }
            std::cout << "],\"construction_blocked\":[";
            for (size_t p = 0; p < problem.passengers.size(); ++p) {
                if (p) std::cout << ',';
                std::cout << '[';
                const auto& blocks = construction_state.assigned_blocked[p];
                for (size_t b = 0; b < blocks.size(); ++b) {
                    if (b) std::cout << ',';
                    std::cout << '"' << problem.seats[blocks[b]].id << '"';
                }
                std::cout << ']';
            }
            std::cout << ']';
            const auto emit_score = [](const char* name, const full_cpp::ScoreComponents& score) {
                std::cout << ",\"" << name << "\":{\"score_s\":" << score.score_s
                    << ",\"score_v\":" << score.score_v << ",\"score_p\":" << score.score_p
                    << ",\"score_c\":" << score.score_c << ",\"score_b\":" << score.score_b
                    << ",\"total_soft_score\":" << score.total() << '}';
            };
            std::cout << ",\"construction_repair_queue\":";
            full_cpp::write_rich_repair_queue(std::cout,
                full_cpp::build_rich_repair_queue(problem, construction_state.passenger_to_seat), problem.groups.size());
            std::cout << ",\"elite_store\":";
            full_cpp::write_rich_elite_store(std::cout, captures);
            emit_score("construction_score", construction_score);
            emit_score("repair_score", full_cpp::evaluate_score_components(problem, state.passenger_to_seat));
        }
        std::cout << ",\"assignment_order\":[";
        for (size_t i = 0; i < state.assignment_order.size(); ++i) {
            if (i) std::cout << ',';
            std::cout << state.assignment_order[i];
        }
        std::cout << "],\"vnd\":{\"passes\":" << vnd.passes
            << ",\"evaluated_moves\":" << vnd.evaluated_moves
            << ",\"accepted_moves\":" << vnd.accepted_moves
            << ",\"score_improvement\":" << vnd.score_improvement
            << ",\"stopped_by_deadline\":" << (vnd.stopped_by_deadline ? "true" : "false")
            << ",\"one_opt\":" << vnd.one_opt << ",\"swaps\":" << vnd.swaps
            << ",\"cycles\":" << vnd.cycles << ",\"group_rebuilds\":" << vnd.group_rebuilds << ",\"caregiver_rebuilds\":" << vnd.caregiver_rebuilds << "}}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 3;
    }
}
