#include "native_rich_master_ssr.hpp"

#include <algorithm>
#include <map>
#include <stdexcept>
#include <tuple>

namespace full_cpp {

int add_rich_master_ssr_constraints(GRBmodel* model, const Problem& problem,
    const std::vector<std::pair<int, const RichElitePattern*>>& columns
) {
    using Location = std::tuple<std::string, int, int>; // cabin, row, subrow (-1 for row)
    std::map<std::pair<int, int>, const Passenger*> passengers;
    for (const auto& p : problem.passengers) passengers[{p.group_id, p.hostnum}] = &p;
    std::map<Location, std::map<int, std::vector<int>>> flags;
    std::map<Location, std::map<std::string, std::map<int, int>>> counts;
    for (size_t i = 0; i < columns.size(); ++i) {
        const int gid = columns[i].first, column = static_cast<int>(i);
        std::set<Location> active;
        for (const auto& entry : columns[i].second->assignments) {
            const auto& p = *passengers.at({gid, entry.first});
            if (p.ssr.empty()) continue;
            const auto& seat = problem.seats[problem.seat_index.at(entry.second)];
            for (bool by_row : {true, false}) {
                const Location loc{seat.cabin, seat.row, by_row ? -1 : seat.subrow};
                ++counts[loc][p.ssr][column];
                if (by_row ? p.same_row_no_other_ssr : p.same_subrow_no_other_ssr) active.insert(loc);
            }
        }
        for (const auto& loc : active) flags[loc][gid].push_back(column);
    }
    const auto check = [](int code) {
        if (code) throw std::runtime_error("Gurobi conditional SSR model error: " + std::to_string(code));
    };
    std::map<Location, int> activation;
    for (const auto& loc : flags) {
        activation[loc.first] = static_cast<int>(columns.size() + activation.size());
        check(GRBaddvar(model, 0, nullptr, nullptr, 0.0, 0.0, 1.0, GRB_CONTINUOUS, nullptr));
    }
    check(GRBupdatemodel(model));
    int rows = 0;
    for (const auto& loc : flags) {
        const int z = activation.at(loc.first);
        // One selected flagged pattern forces z=1. Unflagged locations may use z=0.
        for (const auto& group : loc.second) {
            auto indexes = group.second;
            std::vector<double> values(indexes.size(), 1.0);
            indexes.push_back(z); values.push_back(-1.0);
            check(GRBaddconstr(model, static_cast<int>(indexes.size()), indexes.data(), values.data(), GRB_LESS_EQUAL, 0.0, nullptr));
            ++rows;
        }
        for (const auto& type : counts.at(loc.first)) {
            std::map<int, int> group_maximum;
            std::vector<int> indexes;
            std::vector<double> values;
            for (const auto& count : type.second) {
                const int gid = columns[count.first].first;
                group_maximum[gid] = std::max(group_maximum[gid], count.second);
                indexes.push_back(count.first); values.push_back(count.second);
            }
            int maximum = 0;
            for (const auto& group : group_maximum) maximum += group.second;
            if (maximum <= 1) continue;
            // count + (M-1)z <= M: only an active location restricts each SSR type to one.
            indexes.push_back(z); values.push_back(maximum - 1.0);
            check(GRBaddconstr(model, static_cast<int>(indexes.size()), indexes.data(), values.data(), GRB_LESS_EQUAL,
                static_cast<double>(maximum), nullptr));
            ++rows;
        }
    }
    check(GRBupdatemodel(model));
    return rows;
}

}  // namespace full_cpp
