#include "native_group_constructor.hpp"

#include <iomanip>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    try {
        const auto problem = full_cpp::load_problem(argv[1], argv[2]);
        const auto replay = native_json::parse_file(argv[3]);
        full_cpp::AssignmentState state(problem);
        for (const auto& entry : replay.at("assignments").array) {
            const int passenger = static_cast<int>(entry.array.at(0).number);
            const int seat = problem.seat_index.at(entry.array.at(1).string);
            const int block = entry.array.size() > 2 && !entry.array[2].is_null()
                ? problem.seat_index.at(entry.array[2].string) : -1;
            if (!state.assign(passenger, seat, block)) throw std::runtime_error("invalid replay assignment");
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
        for (const auto& row : replay.at("rankings").array) {
            rankings.emplace_back();
            for (const auto& seat : row.array) rankings.back().push_back(problem.seat_index.at(seat.string));
        }
        if (rankings.size() != problem.passengers.size()) throw std::runtime_error("ranking count mismatch");
        full_cpp::GroupConstructionResult diagnostics;
        full_cpp::RichRemainingDiagnostics construction;
        if (const auto* mode = replay.find("construct_remaining"); mode && mode->bool_or()) {
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
        std::cout << std::setprecision(17) << "{\"groups_considered\":" << construction.groups_considered
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
        std::cout << "]}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 3;
    }
}
