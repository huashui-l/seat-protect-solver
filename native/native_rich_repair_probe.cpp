#include "native_group_constructor.hpp"
#include "native_feasibility_solver.hpp"

#include <iomanip>
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <cmath>

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    try {
        const auto problem = full_cpp::load_problem(argv[1], argv[2]);
        const auto replay = native_json::parse_file(argv[3]);
        if (const auto* input = replay.find("pattern_context")) {
            full_cpp::AssignmentState state(problem);
            for (const auto& entry : input->at("initial").array) {
                const int chosen = entry.array.size() > 2 ? problem.seat_index.at(entry.array[2].string) : -1;
                if (!state.assign_rich_pattern(static_cast<int>(entry.array[0].number), problem.seat_index.at(entry.array[1].string), {}, chosen))
                    throw std::runtime_error("pattern context initial assignment failed");
            }
            if (const auto* calls = input->find("lns_master")) {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(120);
                full_cpp::RichLnsWorkspace workspace(state, deadline);
                std::cout << "[";
                bool first_call = true;
                for (const auto& call : calls->array) {
                    std::vector<int> component;
                    std::map<int, std::vector<full_cpp::RichLnsOption>> options;
                    for (const auto& group : call.at("component").array) component.push_back(static_cast<int>(group.number));
                    for (int g : component) {
                        auto& group_options = options[g];
                        for (const auto& item : call.at("options").at(std::to_string(g)).array) {
                            full_cpp::RichLnsOption option;
                            option.score = item.array[0].number;
                            for (const auto& seat : item.array[1].array) option.seats.insert(problem.seat_index.at(seat.string));
                            for (const auto& seat : item.array[2].array) option.assignment.push_back(problem.seat_index.at(seat.string));
                            group_options.push_back(std::move(option));
                        }
                    }
                    const auto choices = full_cpp::solve_rich_lns_master(state, workspace, component, options,
                        static_cast<int>(call.at("root").number), .75, deadline);
                    if (!first_call) std::cout << ','; first_call = false;
                    std::cout << '{'; bool first_group = true;
                    for (const auto& choice : choices) {
                        if (!first_group) std::cout << ','; first_group = false;
                        std::cout << '"' << choice.first << "\":[";
                        for (size_t i = 0; i < choice.second.size(); ++i) {
                            if (i) std::cout << ',';
                            std::cout << '"' << problem.seats[choice.second[i]].id << '"';
                        }
                        std::cout << ']';
                    }
                    std::cout << '}';
                }
                std::cout << "]\n";
                return 0;
            }
            if (const auto* calls = input->find("lns_matching")) {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    std::chrono::duration<double>(input->at("deadline_seconds").number));
                full_cpp::RichLnsWorkspace workspace(state, deadline);
                std::cout << std::setprecision(17) << "{\"eligible\":[";
                bool first = true;
                for (int g : workspace.eligible_groups) { if (!first) std::cout << ','; first = false; std::cout << problem.groups[g].id; }
                std::cout << "],\"keys\":[";
                for (size_t g = 0; g < workspace.keys_by_group.size(); ++g) {
                    if (g) std::cout << ','; std::cout << '[';
                    for (size_t i = 0; i < workspace.keys_by_group[g].size(); ++i) { if (i) std::cout << ','; std::cout << workspace.keys_by_group[g][i]; }
                    std::cout << ']';
                }
                std::cout << "],\"calls\":["; first = true;
                for (const auto& call : calls->array) {
                    if (const auto* moves = call.find("moves")) {
                        for (const auto& move : moves->array) state.remove(static_cast<int>(move.array[0].number));
                        for (const auto& move : moves->array)
                            if (!state.assign_rich_pattern(static_cast<int>(move.array[0].number), problem.seat_index.at(move.array[1].string), {}))
                                throw std::runtime_error("LNS matching move failed");
                    }
                    std::vector<int> seats; std::set<int> released;
                    for (const auto& seat : call.at("seats").array) seats.push_back(problem.seat_index.at(seat.string));
                    for (const auto& seat : call.at("released").array) released.insert(problem.seat_index.at(seat.string));
                    const int g = static_cast<int>(call.at("group_index").number);
                    const auto result = workspace.best_group_assignment(g, seats, released);
                    if (!first) std::cout << ','; first = false;
                    std::cout << "{\"score\":";
                    if (std::isfinite(result.score)) std::cout << result.score; else std::cout << "null";
                    std::cout << ",\"assignment\":[";
                    for (size_t i = 0; i < result.seats.size(); ++i) { if (i) std::cout << ','; std::cout << '"' << problem.seats[result.seats[i]].id << '"'; }
                    std::cout << "],\"compact\":" << workspace.compact_score(seats)
                        << ",\"stopped\":" << (workspace.stopped_by_deadline ? "true" : "false") << ",\"scores\":[";
                    for (size_t i = 0; i < call.at("scores").array.size(); ++i) {
                        if (i) std::cout << ',';
                        const auto& item = call.at("scores").array[i];
                        std::cout << workspace.passenger_score(static_cast<int>(item.array[0].number), problem.seat_index.at(item.array[1].string));
                    }
                    std::cout << "]}";
                }
                std::cout << "]}\n";
                return 0;
            }
            std::vector<std::pair<int, full_cpp::RichElitePattern>> patterns;
            for (const auto& item : input->at("patterns").array) {
                full_cpp::RichElitePattern pattern;
                if (const auto* value = item.find("local_score")) pattern.local_score = value->number;
                if (const auto* value = item.find("source")) pattern.source = value->string;
                if (const auto* value = item.find("pinned")) pattern.pinned = value->bool_or();
                if (const auto* values = item.find("conflict_groups"))
                    for (const auto& value : values->array) pattern.conflict_groups.push_back(static_cast<int>(value.number));
                for (const auto& entry : item.at("assignments").array)
                    pattern.assignments.emplace_back(static_cast<int>(entry.array[0].number), entry.array[1].string);
                for (const auto& entry : item.at("blocked_by_host").array) {
                    std::vector<std::string> blocked;
                    for (const auto& seat : entry.array[1].array) blocked.push_back(seat.string);
                    pattern.blocked_by_host.emplace_back(static_cast<int>(entry.array[0].number), blocked);
                }
                patterns.emplace_back(static_cast<int>(item.at("group_id").number), std::move(pattern));
            }
            std::cout << "{\"conflicts\":[";
            bool first = true;
            for (const auto& pair : input->at("pairs").array) {
                const auto& left = patterns.at(static_cast<int>(pair.array[0].number));
                const auto& right = patterns.at(static_cast<int>(pair.array[1].number));
                if (!first) std::cout << ','; first = false;
                std::cout << (full_cpp::rich_patterns_have_conditional_ssr_conflict(problem, left.first, left.second, right.first, right.second) ? "true" : "false");
            }
            const auto write_snapshot = [&](const full_cpp::AssignmentSnapshot& snapshot) {
                const auto ints = [&](const auto& values) {
                    std::cout << '[';
                    for (size_t i = 0; i < values.size(); ++i) { if (i) std::cout << ','; std::cout << values[i]; }
                    std::cout << ']';
                };
                std::cout << "{\"assignments\":[";
                for (size_t i = 0; i < snapshot.passenger_to_seat.size(); ++i) {
                    if (i) std::cout << ',';
                    if (snapshot.passenger_to_seat[i] < 0) std::cout << "null";
                    else std::cout << '"' << problem.seats[snapshot.passenger_to_seat[i]].id << '"';
                }
                std::cout << "],\"blocked\":[";
                for (size_t p = 0; p < snapshot.assigned_blocked.size(); ++p) {
                    if (p) std::cout << ',';
                    std::cout << '[';
                    for (size_t j = 0; j < snapshot.assigned_blocked[p].size(); ++j) {
                        if (j) std::cout << ',';
                        std::cout << '"' << problem.seats[snapshot.assigned_blocked[p][j]].id << '"';
                    }
                    std::cout << ']';
                }
                std::cout << "],\"blocked_count\":"; ints(snapshot.blocked_count);
                std::cout << ",\"seat_to_passenger\":"; ints(snapshot.seat_to_passenger);
                std::cout << ",\"owner_group_by_seat\":"; ints(snapshot.owner_group_by_seat);
                std::cout << ",\"seat_ssr_passenger\":"; ints(snapshot.seat_ssr_passenger);
                std::cout << ",\"assignment_order\":"; ints(snapshot.assignment_order);
                std::cout << '}';
            };
            if (const auto* mode = input->find("protected_mip"); mode && mode->bool_or()) {
                full_cpp::RichEliteStore elite;
                for (const auto& entry : patterns)
                    elite.insert_relocation(entry.first, entry.second, [&]() { return entry.second.local_score; });
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    std::chrono::duration<double>(input->at("deadline_seconds").number));
                full_cpp::RichProtectedMipDiagnostics stats;
                full_cpp::RichSpecialPricingDiagnostics special;
                full_cpp::GroupConstructionResult result;
                const auto* stage = input->find("protected_stage");
                if (stage && stage->bool_or()) {
                    result.rich_candidate_complete = true;
                    result.rich_state = state.save();
                    result.rich_elite_store = elite;
                    result.rich_conflict_diversity_active = true;
                    result.group_construction_score = full_cpp::evaluate_soft_score(problem, state.passenger_to_seat);
                    // Deliberately empty selected incumbent: stage continuation must use rich_state.
                    stats = full_cpp::improve_rich_protected_stage(problem, deadline, result);
                    const auto* enabled = input->find("special_enabled");
                    special = full_cpp::generate_rich_special_pricing_stage(problem, deadline, enabled && enabled->bool_or(), result);
                    state.restore(result.rich_state); elite = result.rich_elite_store;
                } else stats = full_cpp::improve_rich_protected_mip(problem, state, elite, deadline);
                std::cout << std::setprecision(17) << "],\"diagnostics\":";
                full_cpp::write_rich_protected_mip_diagnostics(std::cout, stats);
                if (stage && stage->bool_or()) {
                    std::cout << ",\"special\":"; full_cpp::write_rich_special_pricing_diagnostics(std::cout, problem, special);
                    std::cout << ",\"selected_score\":" << result.group_construction_score
                        << ",\"selected_count\":" << result.passenger_to_seat.size();
                }
                std::cout << ",\"elite\":"; full_cpp::write_rich_elite_store(std::cout, elite);
                std::cout << ",\"state\":"; write_snapshot(state.save());
                std::cout << "}\n";
                return 0;
            }
            std::cout << "],\"rebuilt\":["; first = true;
            for (const auto& indexes : input->at("components").array) {
                std::map<int, full_cpp::RichElitePattern> choices;
                for (const auto& index : indexes.array) {
                    const auto& pattern = patterns.at(static_cast<int>(index.number));
                    choices[pattern.first] = pattern.second;
                }
                full_cpp::AssignmentSnapshot rebuilt;
                const bool okay = full_cpp::rebuild_rich_pattern_component(state, choices, rebuilt);
                if (!first) std::cout << ','; first = false;
                if (okay) write_snapshot(rebuilt); else std::cout << "null";
            }
            std::cout << "],\"initial\":"; write_snapshot(state.save());
            std::cout << "}\n";
            return 0;
        }
        if (const auto* calls = replay.find("dynamic_relocation")) {
            full_cpp::AssignmentState state(problem);
            for (const auto& entry : replay.at("assignments").array)
                state.passenger_to_seat.at(static_cast<int>(entry.array[0].number)) = problem.seat_index.at(entry.array[1].string);
            const auto fixed = full_cpp::preprocess_fixed_seats(problem);
            const auto baby = full_cpp::build_rich_baby_costs(problem);
            std::map<int, full_cpp::RichPricingCache> caches;
            full_cpp::RichEliteStore elite(2);
            std::cout << std::setprecision(17) << '[';
            bool first = true;
            for (const auto& request : calls->array) {
                std::set<int> outside;
                for (const auto& seat : request.at("outside_resources").array) outside.insert(problem.seat_index.at(seat.string));
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    std::chrono::duration<double>(request.at("deadline_seconds").number));
                const auto stats = full_cpp::add_rich_dynamic_relocation_patterns(problem, state,
                    static_cast<int>(request.at("group_index").number), outside, deadline, fixed, baby, caches, elite);
                if (!first) std::cout << ','; first = false;
                std::cout << "{\"calls\":" << stats.calls << ",\"patterns\":" << stats.patterns
                    << ",\"cache_count\":" << caches.size() << ",\"elite\":";
                full_cpp::write_rich_elite_store(std::cout, elite);
                std::cout << '}';
            }
            std::cout << "]\n";
            return 0;
        }
        if (const auto* mode = replay.find("special_pricing"); mode && mode->bool_or()) {
            full_cpp::AssignmentState state(problem);
            for (const auto& entry : replay.at("assignments").array) {
                const int p = static_cast<int>(entry.array[0].number);
                state.passenger_to_seat[p] = problem.seat_index.at(entry.array[1].string);
            }
            for (const auto& entry : replay.at("blocked").array)
                for (const auto& seat : entry.array[1].array)
                    state.assigned_blocked.at(static_cast<int>(entry.array[0].number)).push_back(problem.seat_index.at(seat.string));
            full_cpp::RichEliteStore elite(1000);
            for (const auto& entry : replay.at("elite").array) {
                full_cpp::RichElitePattern pattern;
                pattern.local_score = entry.at("local_score").number;
                for (const auto& a : entry.at("assignments").array)
                    pattern.assignments.emplace_back(static_cast<int>(a.array[0].number), a.array[1].string);
                for (const auto& a : entry.at("blocked_by_host").array) {
                    std::vector<std::string> seats;
                    for (const auto& seat : a.array[1].array) seats.push_back(seat.string);
                    pattern.blocked_by_host.emplace_back(static_cast<int>(a.array[0].number), seats);
                }
                elite.record(static_cast<int>(entry.at("group_id").number), std::move(pattern), {}, false);
            }
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                std::chrono::duration<double>(replay.at("deadline_seconds").number));
            std::vector<double> scores;
            const auto diagnostics = full_cpp::generate_rich_special_dual_patterns(problem, state, elite, deadline,
                replay.at("enabled").bool_or(), [&](int, const full_cpp::RichExactPattern&, double score) { scores.push_back(score); });
            std::cout << std::setprecision(17) << "{\"diagnostics\":";
            full_cpp::write_rich_special_pricing_diagnostics(std::cout, problem, diagnostics);
            std::cout << ",\"scores\":[";
            for (size_t i = 0; i < scores.size(); ++i) { if (i) std::cout << ','; std::cout << scores[i]; }
            std::cout << "]}\n";
            return 0;
        }
        if (const auto* mode = replay.find("structured_generation"); mode && mode->bool_or()) {
            std::vector<int> assignment(problem.passengers.size(), -1);
            for (const auto& entry : replay.at("assignments").array)
                assignment.at(static_cast<size_t>(entry.array[0].number)) = problem.seat_index.at(entry.array[1].string);
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                std::chrono::duration<double>(replay.at("deadline_seconds").number));
            std::cout << std::setprecision(17) << "{\"candidates\":[";
            bool first = true;
            const auto stats = full_cpp::generate_rich_structured_patterns(problem, assignment, deadline,
                [&](int g, const full_cpp::RichTieredPattern& item, double score, bool pinned) {
                    if (!first) std::cout << ','; first = false;
                    std::cout << "{\"group_id\":" << problem.groups[g].id << ",\"source\":\"structured_" << item.source
                        << "\",\"local_score\":" << score << ",\"pinned\":" << (pinned ? "true" : "false") << ",\"assignments\":[";
                    for (size_t i = 0; i < item.pattern.assignments.size(); ++i) {
                        if (i) std::cout << ',';
                        const auto& a = item.pattern.assignments[i];
                        std::cout << "[[" << problem.groups[g].id << ',' << problem.passengers[a.first].hostnum << "],\"" << problem.seats[a.second].id << "\"]";
                    }
                    std::map<int, std::vector<std::string>> blocked;
                    for (const auto& entry : item.pattern.blocked_by) blocked[problem.passengers[entry.second].hostnum].push_back(problem.seats[entry.first].id);
                    std::cout << "],\"blocked_by_host\":[";
                    bool first_host = true;
                    for (const auto& entry : blocked) {
                        if (!first_host) std::cout << ','; first_host = false;
                        std::cout << '[' << entry.first << ",[";
                        for (size_t i = 0; i < entry.second.size(); ++i) { if (i) std::cout << ','; std::cout << '"' << entry.second[i] << '"'; }
                        std::cout << "]]";
                    }
                    std::cout << "]}";
                });
            std::cout << "],\"diagnostics\":{\"enabled\":" << (stats.enabled ? "true" : "false")
                << ",\"stopped_by_deadline\":" << (stats.stopped_by_deadline ? "true" : "false")
                << ",\"three_tier_active\":" << (stats.three_tier_active ? "true" : "false")
                << ",\"groups_attempted\":" << stats.groups_attempted << ",\"groups_with_patterns\":" << stats.groups_with_patterns
                << ",\"patterns_generated\":" << stats.patterns_generated << ",\"row_windows_attempted\":" << stats.row_windows_attempted
                << ",\"dfs_nodes\":" << stats.dfs_nodes << ",\"extreme_groups_attempted\":" << stats.extreme_groups_attempted
                << ",\"span_reducing_patterns\":" << stats.span_reducing_patterns << ",\"tier_counts\":{";
            bool first_tier = true;
            for (const auto& entry : stats.tier_counts) { if (!first_tier) std::cout << ','; first_tier = false; std::cout << '"' << entry.first << "\":" << entry.second; }
            std::cout << "},\"repair_queue\":"; full_cpp::write_rich_repair_queue(std::cout, stats.repair_queue, 20);
            std::cout << "}}\n";
            return 0;
        }
        if (const auto* requests = replay.find("pricing_cache")) {
            const auto fixed = full_cpp::preprocess_fixed_seats(problem);
            const auto emit_seats = [&](const std::vector<int>& seats) {
                std::cout << '[';
                for (size_t i = 0; i < seats.size(); ++i) { if (i) std::cout << ','; std::cout << '"' << problem.seats[seats[i]].id << '"'; }
                std::cout << ']';
            };
            std::cout << std::setprecision(17) << '[';
            bool first = true;
            for (const auto& request : requests->array) {
                const int g = static_cast<int>(request.at("group_index").number);
                auto cache = full_cpp::build_rich_pricing_cache(problem, g, fixed);
                if (const auto* window = request.find("window")) {
                    std::vector<int> rows;
                    for (const auto& row : window->array) rows.push_back(static_cast<int>(row.number));
                    cache = full_cpp::filter_rich_pricing_window(problem, cache, rows);
                }
                if (!first) std::cout << ',';
                first = false;
                if (const auto* outside = request.find("outside_resources")) {
                    std::set<int> resources;
                    for (const auto& seat : outside->array) resources.insert(problem.seat_index.at(seat.string));
                    cache = full_cpp::filter_rich_pricing_resources(problem, cache, resources);
                }
                if (const auto* mode = request.find("pricing_costs"); mode && mode->bool_or()) {
                    full_cpp::RichPricingDuals duals;
                    const auto& input = request.at("duals");
                    for (const auto& e : input.at("group").array) duals.group[static_cast<int>(e.array[0].number)] = e.array[1].number;
                    for (const auto& e : input.at("seat").array) duals.seat[problem.seat_index.at(e.array[0].string)] = e.array[1].number;
                    for (const auto& e : input.at("ssr_all").array)
                        duals.ssr_all[{{static_cast<int>(e.array[0].number), static_cast<int>(e.array[1].number)}, e.array[2].string}] = e.array[3].number;
                    for (const auto& e : input.at("ssr_flag").array)
                        duals.ssr_flag.emplace_back(static_cast<int>(e.array[0].number), full_cpp::RichSsrResource{
                            {static_cast<int>(e.array[1].number), static_cast<int>(e.array[2].number)}, e.array[3].string}, e.array[4].number);
                    for (const auto& e : input.at("baby").array) {
                        const auto pair = std::make_pair(problem.seat_index.at(e.array[0].string), problem.seat_index.at(e.array[1].string));
                        duals.baby_lower[pair] = e.array[2].number; duals.baby_infant_upper[pair] = e.array[3].number;
                        duals.baby_occupant_upper[pair] = e.array[4].number;
                    }
                    const auto baby = full_cpp::build_rich_baby_costs(problem);
                    if (const auto* calls = request.find("dfs_calls")) {
                        const auto read_signatures = [&](const native_json::Value* input) {
                            std::vector<full_cpp::RichPlacementSignature> signatures;
                            if (input) for (const auto& e : input->array) {
                                std::vector<int> blocked;
                                for (const auto& s : e.array[2].array) blocked.push_back(problem.seat_index.at(s.string));
                                signatures.emplace_back(static_cast<int>(e.array[0].number), problem.seat_index.at(e.array[1].string), blocked);
                            }
                            return signatures;
                        };
                        const auto emit_signatures = [&](const std::vector<full_cpp::RichPlacementSignature>& signatures) {
                            std::cout << '[';
                            for (size_t i = 0; i < signatures.size(); ++i) {
                                if (i) std::cout << ',';
                                const auto& s = signatures[i];
                                std::cout << '[' << std::get<0>(s) << ",\"" << problem.seats[std::get<1>(s)].id << "\",";
                                emit_seats(std::get<2>(s)); std::cout << ']';
                            }
                            std::cout << ']';
                        };
                        const auto emit_number = [&](double value) { if (std::isfinite(value)) std::cout << value; else std::cout << "null"; };
                        std::cout << '[';
                        bool first_call = true;
                        for (const auto& call : calls->array) {
                            if (!first_call) std::cout << ',';
                            first_call = false;
                            if (call.find("history")) cache.historical_start = read_signatures(call.find("history"));
                            const auto forced = read_signatures(call.find("forced")), forbidden = read_signatures(call.find("forbidden"));
                            const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                                std::chrono::duration<double>(call.at("deadline_seconds").number));
                            const bool phase_one = call.at("phase_one").bool_or();
                            const auto result = full_cpp::price_rich_group_dfs(problem, g, call.at("pricing_config"), duals, baby,
                                {forced.begin(), forced.end()}, {forbidden.begin(), forbidden.end()}, deadline, cache,
                                call.at("exact").bool_or(), phase_one, call.at("stop_on_negative").bool_or());
                            std::cout << "{\"reduced_cost\":"; emit_number(result.reduced_cost);
                            std::cout << ",\"lower_bound\":"; emit_number(result.lower_bound);
                            std::cout << ",\"proven_optimal\":" << (result.proven_optimal ? "true" : "false")
                                << ",\"incumbent_seeded\":" << (result.incumbent_seeded ? "true" : "false")
                                << ",\"nodes\":" << result.nodes << ",\"priced_placements\":" << result.priced_placements
                                << ",\"bound_prunes\":" << result.bound_prunes << ",\"resource_prunes\":" << result.resource_prunes
                                << ",\"symmetry_prunes\":" << result.symmetry_prunes << ",\"symmetry_classes\":" << result.symmetry_classes
                                << ",\"negative_patterns_seen\":" << result.negative_patterns_seen << ",\"unique_negative_patterns\":" << result.unique_negative_patterns
                                << ",\"workspace_builds\":" << result.workspace_builds << ",\"workspace_reuses\":" << result.workspace_reuses
                                << ",\"termination\":\"" << result.termination << "\",\"history\":";
                            emit_signatures(cache.historical_start);
                            std::cout << ",\"patterns\":[";
                            for (size_t i = 0; i < result.patterns.size(); ++i) {
                                if (i) std::cout << ',';
                                const auto& pattern = result.patterns[i];
                                std::vector<full_cpp::RichPlacementSignature> signatures;
                                for (const auto& o : pattern.placements) signatures.emplace_back(o.passenger_index, o.seat, o.blocked);
                                std::cout << "{\"signature\":"; emit_signatures(signatures);
                                std::cout << ",\"master_cost\":" << pattern.master_cost << ",\"rc\":";
                                emit_number(full_cpp::rich_pattern_reduced_cost(pattern, duals, baby, phase_one)); std::cout << '}';
                            }
                            std::cout << "]}";
                        }
                        std::cout << ']';
                        continue;
                    }
                    const auto workspace = full_cpp::build_rich_pricing_workspace(problem, g, cache);
                    const bool phase_one = request.at("phase_one").bool_or();
                    const auto costs = full_cpp::build_rich_pricing_costs(problem.groups[g].id, cache, workspace, duals, baby, phase_one);
                    std::cout << "{\"baby_pairs\":[";
                    for (size_t i = 0; i < baby.size(); ++i) {
                        if (i) std::cout << ',';
                        std::cout << "[\"" << problem.seats[baby[i].infant].id << "\",\"" << problem.seats[baby[i].occupant].id << "\"," << baby[i].cost << ']';
                    }
                    std::cout << "],\"base\":[";
                    for (size_t p = 0; p < costs.base.size(); ++p) {
                        if (p) std::cout << ','; std::cout << '[';
                        for (size_t i = 0; i < costs.base[p].size(); ++i) { if (i) std::cout << ','; std::cout << costs.base[p][i]; }
                        std::cout << ']';
                    }
                    std::cout << "],\"flags\":[";
                    for (size_t i = 0; i < costs.flags.size(); ++i) { if (i) std::cout << ','; std::cout << costs.flags[i]; }
                    std::cout << "],\"baby_relaxation\":" << costs.baby_relaxation << ",\"upper_bounds\":[";
                    for (int count = 0; count <= static_cast<int>(cache.all_options.size()) + 1; ++count) {
                        if (count) std::cout << ',';
                        std::cout << full_cpp::rich_same_group_baby_upper_bound(cache, baby, count);
                    }
                    std::cout << "],\"column_rc\":[";
                    bool first_pattern = true;
                    for (const auto& selection : request.at("patterns").array) {
                        if (!first_pattern) std::cout << ',';
                        first_pattern = false;
                        std::vector<full_cpp::RichPlacement> placements;
                        for (size_t p = 0; p < selection.array.size(); ++p) placements.push_back(cache.all_options[p].at(static_cast<size_t>(selection.array[p].number)));
                        const auto pattern = full_cpp::build_rich_exact_pattern(problem, g, placements, {});
                        std::cout << full_cpp::rich_pattern_reduced_cost(pattern, duals, baby, phase_one);
                    }
                    std::cout << "]}";
                    continue;
                }
                if (const auto* mode = request.find("symmetry"); mode && mode->bool_or()) {
                    if (const auto* keep = request.find("keep_options")) for (size_t p = 0; p < cache.all_options.size(); ++p) {
                        const auto original = cache.all_options[p]; cache.all_options[p].clear();
                        for (const auto& i : keep->array[p].array) cache.all_options[p].push_back(original.at(static_cast<size_t>(i.number)));
                    }
                    if (const auto* costs = request.find("option_costs")) for (size_t p = 0; p < cache.all_options.size(); ++p)
                        for (size_t i = 0; i < cache.all_options[p].size(); ++i) cache.all_options[p][i].individual_cost = costs->array[p].array[i].number;
                    const auto symmetry = full_cpp::build_rich_pricing_symmetry(problem, g, cache, request.at("enabled").bool_or());
                    const auto emit_ints = [&](const std::vector<int>& values) {
                        std::cout << '[';
                        for (size_t i = 0; i < values.size(); ++i) { if (i) std::cout << ','; std::cout << values[i]; }
                        std::cout << ']';
                    };
                    std::vector<int> raw_classes;
                    const auto& passengers = problem.groups[g].passengers;
                    for (size_t p = 0; p < passengers.size(); ++p) {
                        size_t first_equal = 0;
                        while (problem.passengers[passengers[first_equal]].rich_symmetry_fingerprint != problem.passengers[passengers[p]].rich_symmetry_fingerprint) ++first_equal;
                        raw_classes.push_back(static_cast<int>(first_equal));
                    }
                    std::cout << "{\"raw_classes\":"; emit_ints(raw_classes);
                    std::cout << ",\"class_count\":" << symmetry.class_count << ",\"classes\":[";
                    for (size_t p = 0; p < symmetry.classes.size(); ++p) { if (p) std::cout << ','; emit_ints(symmetry.classes[p]); }
                    std::cout << "],\"ranks\":[";
                    for (size_t p = 0; p < symmetry.ranks.size(); ++p) { if (p) std::cout << ','; emit_ints(symmetry.ranks[p]); }
                    std::cout << "],\"allowed\":[";
                    bool first_query = true;
                    for (const auto& query : request.at("queries").array) {
                        if (!first_query) std::cout << ',';
                        first_query = false;
                        std::vector<int> selected;
                        for (const auto& i : query.at("selected").array) selected.push_back(static_cast<int>(i.number));
                        std::cout << (full_cpp::rich_pricing_symmetry_ok(symmetry, selected,
                            static_cast<int>(query.at("passenger").number), static_cast<int>(query.at("option").number)) ? "true" : "false");
                    }
                    std::cout << "]}";
                    continue;
                }
                if (const auto* mode = request.find("workspace"); mode && mode->bool_or()) {
                    const auto workspace = full_cpp::build_rich_pricing_workspace(problem, g, cache);
                    std::cout << "{\"flag_locations\":[";
                    for (size_t i = 0; i < workspace.flag_locations.size(); ++i) {
                        if (i) std::cout << ',';
                        const auto& location = workspace.flag_locations[i];
                        if (location.subrow < 0) std::cout << "[\"row\"," << location.row << ']';
                        else std::cout << "[\"subrow\",[" << location.row << ',' << location.subrow << "]]";
                    }
                    const auto emit_masks = [&](const char* name, const auto& passengers, bool quote) {
                        std::cout << "],\"" << name << "\":[";
                        for (size_t p = 0; p < passengers.size(); ++p) {
                            if (p) std::cout << ',';
                            std::cout << '[';
                            for (size_t i = 0; i < passengers[p].size(); ++i) {
                                if (i) std::cout << ',';
                                std::cout << '[';
                                for (size_t j = 0; j < passengers[p][i].size(); ++j) {
                                    if (j) std::cout << ',';
                                    if (quote) std::cout << '"';
                                    std::cout << passengers[p][i][j];
                                    if (quote) std::cout << '"';
                                }
                                std::cout << ']';
                            }
                            std::cout << ']';
                        }
                    };
                    emit_masks("resource_masks", workspace.resource_masks, true);
                    emit_masks("flag_masks", workspace.flag_masks, true);
                    emit_masks("option_flag_indexes", workspace.option_flag_indexes, false);
                    std::cout << "],\"caregiver_specs\":[";
                    for (size_t i = 0; i < workspace.caregiver_specs.size(); ++i) {
                        if (i) std::cout << ',';
                        const auto& spec = workspace.caregiver_specs[i];
                        std::cout << '[' << spec.passenger_index << ',' << (spec.allow_cross_aisle ? "true" : "false") << ",[";
                        for (size_t j = 0; j < spec.caregivers.size(); ++j) { if (j) std::cout << ','; std::cout << spec.caregivers[j]; }
                        std::cout << "]]";
                    }
                    std::cout << "],\"option_lookup\":[";
                    bool first_option = true;
                    for (const auto& options : cache.all_options) for (const auto& option : options) {
                        const auto& found = workspace.option_by_signature.at({option.passenger_index, option.seat, option.blocked});
                        if (!first_option) std::cout << ',';
                        first_option = false;
                        std::cout << '[' << found.first << ',' << found.second << ']';
                    }
                    std::cout << "],\"care_queries\":[";
                    bool first_query = true;
                    for (const auto& query : request.at("care_queries").array) {
                        if (!first_query) std::cout << ',';
                        first_query = false;
                        std::vector<int> selected;
                        for (const auto& entry : query.at("selected").array) selected.push_back(static_cast<int>(entry.number));
                        full_cpp::RichPricingMask used((problem.seats.size() + 63) / 64, 0);
                        for (const auto& entry : query.at("used_seats").array) {
                            const int seat = problem.seat_index.at(entry.string);
                            used[seat / 64] |= std::uint64_t{1} << (seat % 64);
                        }
                        std::cout << "{\"possible\":" << (full_cpp::rich_pricing_caregiver_possible(problem, cache, workspace, selected, used) ? "true" : "false");
                        std::cout << ",\"state\":[";
                        const auto state = full_cpp::rich_pricing_caregiver_state(problem, cache, workspace, selected);
                        for (size_t i = 0; i < state.size(); ++i) {
                            if (i) std::cout << ',';
                            if (state[i].empty()) { std::cout << "null"; continue; }
                            std::cout << '[';
                            for (size_t j = 0; j < state[i].size(); ++j) { if (j) std::cout << ','; std::cout << '"' << state[i][j] << '"'; }
                            std::cout << ']';
                        }
                        std::cout << "]}";
                    }
                    std::cout << "]}";
                    continue;
                }
                if (const auto* mode = request.find("bounds"); mode && mode->bool_or()) {
                    const auto geometry = full_cpp::build_rich_pricing_geometry(cache);
                    std::vector<int> order;
                    for (const auto& entry : request.at("order").array) order.push_back(static_cast<int>(entry.number));
                    std::vector<std::vector<double>> costs;
                    for (const auto& passenger : request.at("base_cost").array) {
                        costs.emplace_back();
                        for (const auto& entry : passenger.array) costs.back().push_back(entry.number);
                    }
                    const auto bounds = full_cpp::build_rich_pricing_bounds(cache, geometry, order, costs,
                        request.at("row_span_cost").number, request.at("column_span_cost").number);
                    const auto emit_numbers = [&](const auto& values) {
                        std::cout << '[';
                        for (size_t i = 0; i < values.size(); ++i) {
                            if (i) std::cout << ',';
                            if (std::isfinite(static_cast<double>(values[i]))) std::cout << values[i];
                            else std::cout << "null";
                        }
                        std::cout << ']';
                    };
                    std::cout << "{\"row_values\":"; emit_numbers(geometry.row_values);
                    std::cout << ",\"x_values\":"; emit_numbers(geometry.x_values);
                    std::cout << ",\"seat_prefix\":"; emit_numbers(geometry.seat_prefix);
                    std::cout << ",\"span\":"; emit_numbers(bounds.span);
                    std::cout << ",\"suffix\":[";
                    for (size_t d = 0; d < bounds.suffix.size(); ++d) { if (d) std::cout << ','; emit_numbers(bounds.suffix[d]); }
                    std::cout << "],\"root_span\":";
                    if (std::isfinite(bounds.root_span)) std::cout << bounds.root_span; else std::cout << "null";
                    std::cout << '}';
                    continue;
                }
                std::cout << "{\"seat_ids\":"; emit_seats(cache.seat_ids);
                std::cout << ",\"row_big_m\":" << cache.row_big_m << ",\"x_big_m\":" << cache.x_big_m;
                const auto emit_coordinates = [&](const char* name, const auto& values) {
                    std::cout << ",\"" << name << "\":{";
                    bool first_value = true;
                    for (const auto& entry : values) {
                        if (!first_value) std::cout << ',';
                        first_value = false;
                        std::cout << '"' << problem.seats[entry.first].id << "\":" << entry.second;
                    }
                    std::cout << '}';
                };
                emit_coordinates("row_coordinate", cache.row_coordinate); emit_coordinates("x_coordinate", cache.x_coordinate);
                std::cout << ",\"adjacency_edges\":[";
                for (size_t i = 0; i < cache.adjacency_edges.size(); ++i) {
                    if (i) std::cout << ',';
                    emit_seats({cache.adjacency_edges[i].first, cache.adjacency_edges[i].second});
                }
                std::cout << "],\"hole_specs\":[";
                for (size_t i = 0; i < cache.hole_specs.size(); ++i) {
                    if (i) std::cout << ',';
                    const auto& hole = cache.hole_specs[i];
                    std::cout << "[\"" << problem.seats[hole.middle].id << "\",";
                    emit_seats(hole.left); std::cout << ','; emit_seats(hole.right); std::cout << ']';
                }
                std::cout << "],\"all_options\":[";
                for (size_t p = 0; p < cache.all_options.size(); ++p) {
                    if (p) std::cout << ',';
                    std::cout << '[';
                    for (size_t i = 0; i < cache.all_options[p].size(); ++i) {
                        if (i) std::cout << ',';
                        const auto& option = cache.all_options[p][i];
                        std::cout << '[' << option.passenger_index << ",\"" << problem.seats[option.seat].id << "\",";
                        emit_seats(option.blocked); std::cout << ']';
                    }
                    std::cout << ']';
                }
                std::cout << "]}";
            }
            std::cout << "]\n";
            return 0;
        }
        if (const auto* mode = replay.find("structured_windows"); mode && mode->bool_or()) {
            std::vector<int> assignment(problem.passengers.size(), -1);
            for (const auto& entry : replay.at("assignments").array)
                assignment.at(static_cast<size_t>(entry.array[0].number)) = problem.seat_index.at(entry.array[1].string);
            const auto order = full_cpp::build_rich_structured_order(problem, assignment);
            const auto emit_groups = [&](const std::vector<int>& groups) {
                std::cout << '[';
                for (size_t i = 0; i < groups.size(); ++i) { if (i) std::cout << ','; std::cout << problem.groups[groups[i]].id; }
                std::cout << ']';
            };
            const auto emit_windows = [&](const std::vector<std::vector<int>>& windows) {
                std::cout << '[';
                for (size_t i = 0; i < windows.size(); ++i) {
                    if (i) std::cout << ',';
                    std::cout << '[';
                    for (size_t j = 0; j < windows[i].size(); ++j) { if (j) std::cout << ','; std::cout << windows[i][j]; }
                    std::cout << ']';
                }
                std::cout << ']';
            };
            std::cout << std::setprecision(17) << "{\"min_group_size\":" << order.min_group_size
                << ",\"full_resource_global_blocks\":" << (order.full_resource_global_blocks ? "true" : "false") << ",\"ordered_groups\":";
            emit_groups(order.ordered_groups);
            std::cout << ",\"difficult_groups\":"; emit_groups(order.difficult_groups);
            std::cout << ",\"repair_queue\":"; full_cpp::write_rich_repair_queue(std::cout, order.repair_queue, 20);
            std::cout << ",\"windows\":{";
            const auto fixed = full_cpp::preprocess_fixed_seats(problem);
            for (size_t i = 0; i < order.ordered_groups.size(); ++i) {
                if (i) std::cout << ',';
                const int g = order.ordered_groups[i];
                const auto options = full_cpp::build_rich_placement_options(problem, g, fixed);
                const auto windows = full_cpp::build_rich_structured_windows(problem, g, options, order.repair_queue[i]);
                std::cout << '"' << problem.groups[g].id << "\":{\"minimum_width\":" << windows.minimum_width
                    << ",\"old_center\":" << windows.old_center << ",\"all_row_windows\":";
                emit_windows(windows.all_row_windows);
                std::cout << ",\"row_windows\":"; emit_windows(windows.row_windows);
                std::cout << '}';
            }
            std::cout << "}}\n";
            return 0;
        }
        if ((replay.find("placement_options") && replay.at("placement_options").bool_or()) || replay.find("pattern_assembly") || replay.find("rigid_relaxed")) {
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
            const bool rigid_relaxed = replay.find("rigid_relaxed") != nullptr;
            if (const auto* requests = rigid_relaxed ? replay.find("rigid_relaxed") : replay.find("pattern_assembly")) {
                std::vector<std::string> active_types;
                for (const auto& type : replay.at("active_ssr_types").array) active_types.push_back(type.string);
                std::cout << std::setprecision(17) << '[';
                bool first = true;
                for (const auto& request : requests->array) {
                    const int g = static_cast<int>(request.at("group_index").number);
                    const auto options = full_cpp::build_rich_placement_options(problem, g, fixed);
                    std::vector<full_cpp::RichTieredPattern> patterns;
                    if (rigid_relaxed) {
                        std::vector<int> targets;
                        for (const auto& seat : request.at("current_targets").array)
                            targets.push_back(seat.is_null() ? -1 : problem.seat_index.at(seat.string));
                        patterns = full_cpp::generate_rich_rigid_relaxed_patterns(problem, g, targets, options, active_types);
                        if (const auto* value_blocks = replay.find("value_blocks"); value_blocks && value_blocks->bool_or()) {
                            std::vector<int> assignment(problem.passengers.size(), -1);
                            for (size_t p = 0; p < targets.size(); ++p) assignment[problem.groups[g].passengers[p]] = targets[p];
                            const auto metrics = full_cpp::build_rich_repair_queue(problem, assignment);
                            const auto metric = std::find_if(metrics.begin(), metrics.end(), [&](const auto& m) { return m.group_id == problem.groups[g].id; });
                            const auto windows = full_cpp::build_rich_structured_windows(problem, g, options, *metric);
                            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(
                                replay.find("expired") && replay.at("expired").bool_or() ? -1 : 60);
                            full_cpp::generate_rich_value_block_patterns(problem, g, options, *metric, windows, active_types, deadline, patterns);
                        }
                    } else {
                        std::vector<full_cpp::RichPlacement> selected;
                        for (const auto& choice : request.at("choices").array)
                            selected.push_back(options.at(static_cast<size_t>(choice.array[0].number)).at(static_cast<size_t>(choice.array[1].number)));
                        patterns.push_back({full_cpp::build_rich_exact_pattern(problem, g, selected, active_types), ""});
                    }
                    for (const auto& generated : patterns) {
                        const auto& pattern = generated.pattern;
                        if (!first) std::cout << ',';
                        first = false;
                        std::cout << "{\"group_id\":" << pattern.group_id << ",\"signature\":[";
                        for (size_t i = 0; i < pattern.placements.size(); ++i) {
                            if (i) std::cout << ',';
                            const auto& placement = pattern.placements[i];
                            std::cout << '[' << placement.passenger_index << ",\"" << problem.seats[placement.seat].id << "\",";
                            seats(placement.blocked);
                            std::cout << ']';
                        }
                        std::cout << "],\"assignments\":[";
                        for (size_t i = 0; i < pattern.assignments.size(); ++i) {
                            if (i) std::cout << ',';
                            const auto& entry = pattern.assignments[i];
                            std::cout << "[[" << pattern.group_id << ',' << problem.passengers[entry.first].hostnum
                                << "],\"" << problem.seats[entry.second].id << "\"]";
                        }
                        std::cout << "],\"blocked_by\":[";
                        for (size_t i = 0; i < pattern.blocked_by.size(); ++i) {
                            if (i) std::cout << ',';
                            const auto& entry = pattern.blocked_by[i];
                            std::cout << "[\"" << problem.seats[entry.first].id << "\",[" << pattern.group_id << ','
                                << problem.passengers[entry.second].hostnum << "]]";
                        }
                        std::cout << "],\"seat_resources\":"; seats(pattern.seat_resources);
                        std::cout << ",\"infant_seats\":"; seats(pattern.infant_seats);
                        std::cout << ",\"occupied_seats\":"; seats(pattern.occupied_seats);
                        const auto coefficients = [&](const char* name, const auto& values) {
                            std::cout << ",\"" << name << "\":[";
                            bool first_coefficient = true;
                            for (const auto& entry : values) {
                                if (!first_coefficient) std::cout << ',';
                                first_coefficient = false;
                                std::cout << "[["; location(entry.first.location);
                                std::cout << ",\"" << entry.first.ssr << "\"]," << entry.second << ']';
                            }
                            std::cout << ']';
                        };
                        coefficients("ssr_all", pattern.ssr_all);
                        coefficients("ssr_flagged", pattern.ssr_flagged);
                        if (rigid_relaxed) std::cout << ",\"source\":\"" << generated.source << "\"";
                        std::cout << ",\"master_cost\":" << pattern.master_cost << ",\"caregiver_ok\":"
                            << (full_cpp::rich_placements_caregiver_ok(problem, g, pattern.placements) ? "true" : "false") << '}';
                    }
                }
                std::cout << "]\n";
                return 0;
            }
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
                if (const auto* structured = replay.find("pipeline_structured"); structured && structured->bool_or()) {
                    diagnostics.rich_conflict_diversity_active = full_cpp::rich_conflict_diversity_active(problem,
                        full_cpp::build_rich_repair_queue(problem, construction_state.passenger_to_seat));
                    if (const auto* alternate = replay.find("structured_alternate_selected"); alternate && alternate->bool_or())
                        diagnostics.passenger_to_seat.assign(problem.passengers.size(), -1);
                    full_cpp::generate_rich_patterns_m3(problem,
                        std::chrono::steady_clock::now() + std::chrono::seconds(60), diagnostics);
                }
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
            std::cout << ",\"structured\":";
            full_cpp::write_rich_structured_diagnostics(std::cout, diagnostics.rich_structured);
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
