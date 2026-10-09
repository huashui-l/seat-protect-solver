#include "native_rich_geometry.hpp"
#include "full_cpp_solver_core_internal.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>
#include <tuple>

namespace full_cpp {
namespace {
using Clock = std::chrono::steady_clock;
constexpr double kForbidden = 1e12;

// Square Hungarian assignment. Dummy rows consume spare seats but may not
// consume caregiver support seats required by the selected special passengers.
std::vector<int> match(const std::vector<std::vector<double>>& cost) {
    const int n = static_cast<int>(cost.size());
    std::vector<double> u(n + 1), v(n + 1);
    std::vector<int> owner(n + 1), previous(n + 1);
    for (int i = 1; i <= n; ++i) {
        owner[0] = i;
        int j0 = 0;
        std::vector<double> best(n + 1, kForbidden);
        std::vector<bool> used(n + 1, false);
        do {
            used[j0] = true;
            const int i0 = owner[j0];
            double delta = kForbidden;
            int j1 = 0;
            for (int j = 1; j <= n; ++j) if (!used[j]) {
                const double value = cost[i0 - 1][j - 1] - u[i0] - v[j];
                if (value < best[j]) { best[j] = value; previous[j] = j0; }
                if (best[j] < delta) { delta = best[j]; j1 = j; }
            }
            if (!j1) return {};
            for (int j = 0; j <= n; ++j) {
                if (used[j]) { u[owner[j]] += delta; v[j] -= delta; }
                else best[j] -= delta;
            }
            j0 = j1;
        } while (owner[j0]);
        do {
            const int j1 = previous[j0]; owner[j0] = owner[j1]; j0 = j1;
        } while (j0);
    }
    std::vector<int> assignment(n, -1);
    for (int j = 1; j <= n; ++j) {
        if (cost[owner[j] - 1][j - 1] >= kForbidden / 2) return {};
        assignment[owner[j] - 1] = j - 1;
    }
    return assignment;
}

bool ssr_ok(const std::vector<RichPlacement>& selected) {
    std::set<RichSsrLocation> flags;
    std::map<RichSsrResource, int> counts;
    for (const auto& option : selected) {
        flags.insert(option.ssr_flag_locations.begin(), option.ssr_flag_locations.end());
        for (const auto& resource : option.ssr_resources) ++counts[resource];
    }
    for (const auto& count : counts) if (count.second > 1 && flags.count(count.first.location)) return false;
    return true;
}
}  // namespace

RichGeometryOrders build_rich_geometry_orders(const Problem& problem,
    const FixedSeatContext& fixed, Clock::time_point deadline) {
    std::map<std::string, std::vector<int>> cabins;
    for (int s = 0; s < static_cast<int>(problem.seats.size()); ++s)
        if (fixed.owner_by_seat[s] < 0 && !fixed.deterministic_blocked[s]) cabins[problem.seats[s].cabin].push_back(s);
    RichGeometryOrders result;
    for (const auto& cabin : cabins) for (int anchor : cabin.second)
        for (double offset : {0.0, 0.5, -0.5}) for (double aspect : {0.35, 0.7, 1.5, 3.0}) {
            if (Clock::now() >= deadline) return result;
            auto order = cabin.second;
            std::vector<double> distances(problem.seats.size());
            for (int s : order) distances[s] = std::abs(problem.seats[s].x - problem.seats[anchor].x - offset) * aspect
                    + std::abs(problem.seats[s].y - problem.seats[anchor].y);
            std::sort(order.begin(), order.end(), [&](int a, int b) {
                return distances[a] == distances[b] ? problem.seats[a].id < problem.seats[b].id
                    : distances[a] < distances[b];
            });
            result[cabin.first].push_back(std::move(order));
        }
    return result;
}

void generate_rich_geometry_patterns(const Problem& problem, int g,
    const std::vector<std::vector<RichPlacement>>& options, const RichGeometryOrders& orders,
    const std::vector<std::string>& active_types, Clock::time_point deadline,
    std::vector<RichTieredPattern>& patterns) {
    const auto& group = problem.groups[g];
    if (group.passengers.size() < 2 || Clock::now() >= deadline) return;
    // Mixed-cabin groups keep the existing general DFS path.
    const auto& cabin = problem.passengers[group.passengers.front()].cabin;
    for (int p : group.passengers) if (problem.passengers[p].cabin != cabin) return;
    const auto found = orders.find(cabin);
    if (found == orders.end()) return;
    const auto started = Clock::now();
    const auto slice = deadline - started;
    const auto shapes_deadline = started + slice * 35 / 100;
    const auto ranking_deadline = started + slice * 55 / 100;
    std::set<int> fixed_resources;
    int demand = 0;
    std::vector<int> ordinary, special;
    std::vector<std::map<int, const RichPlacement*>> ordinary_options(options.size());
    for (size_t i = 0; i < options.size(); ++i) {
        if (options[i].empty()) return;
        size_t resources = options[i].front().resources.size();
        for (const auto& option : options[i]) resources = std::min(resources, option.resources.size());
        demand += static_cast<int>(resources);
        const auto& p = problem.passengers[group.passengers[i]];
        if (!p.fixed_seat.empty()) {
            fixed_resources.insert(problem.seat_index.at(p.fixed_seat));
            if (p.need_both_empty) fixed_resources.insert(options[i].front().blocked.begin(), options[i].front().blocked.end());
        }
        if (is_adult_caregiver(p)) {
            ordinary.push_back(static_cast<int>(i));
            for (const auto& option : options[i]) ordinary_options[i][option.seat] = &option;
        } else special.push_back(static_cast<int>(i));
    }
    std::sort(special.begin(), special.end(), [&](int a, int b) { return options[a].size() < options[b].size(); });
    const int movable = demand - static_cast<int>(fixed_resources.size());
    std::set<std::vector<int>> shapes;
    if (movable <= 0) shapes.insert(std::vector<int>(fixed_resources.begin(), fixed_resources.end()));
    for (const auto& order : found->second) {
        if (Clock::now() >= shapes_deadline) break;
        for (int spare : {0, 1}) {
            const int count = movable + spare;
            if (count <= 0 || count > static_cast<int>(order.size())) continue;
            auto shape = fixed_resources;
            shape.insert(order.begin(), order.begin() + count);
            shapes.insert(std::vector<int>(shape.begin(), shape.end()));
            if (count >= 2 && count < static_cast<int>(order.size())) {
                shape.erase(order[count - 1]); shape.insert(order[count]);
                shapes.insert(std::vector<int>(shape.begin(), shape.end()));
            }
        }
    }
    struct RankedShape { double cost; std::vector<int> seats; int first_row; };
    std::vector<RankedShape> exact, spare;
    for (const auto& shape : shapes) {
        if (Clock::now() >= ranking_deadline) break;
        double cost = 0.0;
        for (const auto& domain : options) {
            double best = kForbidden;
            for (const auto& option : domain)
                if (std::binary_search(shape.begin(), shape.end(), option.seat)) best = std::min(best, option.individual_cost);
            cost += best;
        }
        if (cost >= kForbidden / 2) continue;
        int row = std::numeric_limits<int>::max();
        for (int s : shape) row = std::min(row, problem.seats[s].row);
        (shape.size() == static_cast<size_t>(demand) ? exact : spare).push_back({cost, shape, row});
    }
    const auto less = [](const RankedShape& a, const RankedShape& b) { return std::tie(a.cost, a.seats) < std::tie(b.cost, b.seats); };
    std::sort(exact.begin(), exact.end(), less); std::sort(spare.begin(), spare.end(), less);
    std::vector<const RankedShape*> ranked;
    std::set<int> rows;
    for (const auto& shape : exact) if (rows.insert(shape.first_row).second) ranked.push_back(&shape);
    for (size_t i = 0; i < exact.size(); ++i) {
        ranked.push_back(&exact[i]);
        if (i % 3 == 2 && i / 3 < spare.size()) ranked.push_back(&spare[i / 3]);
    }
    for (const auto& shape : spare) ranked.push_back(&shape);
    int generated = 0;
    std::set<std::vector<int>> attempted;
    for (const auto* ranked_shape : ranked) {
        if (Clock::now() >= deadline || generated >= 64) break;
        const auto& shape = ranked_shape->seats;
        if (!attempted.insert(shape).second) continue;
        std::set<int> used;
        std::vector<RichPlacement> selected;
        std::vector<std::pair<double, RichExactPattern>> fitted;
        int nodes = 0;
        const auto visit = [&](auto&& self, size_t i) -> void {
            if (++nodes > 4096 || Clock::now() >= deadline) return;
            if (i < special.size()) {
                for (const auto& option : options[special[i]]) {
                    if (Clock::now() >= deadline) break;
                    if (std::any_of(option.resources.begin(), option.resources.end(), [&](int s) {
                        return used.count(s) || !std::binary_search(shape.begin(), shape.end(), s);
                    })) continue;
                    selected.push_back(option);
                    if (ssr_ok(selected)) {
                        used.insert(option.resources.begin(), option.resources.end());
                        self(self, i + 1);
                        for (int s : option.resources) used.erase(s);
                    }
                    selected.pop_back();
                }
                return;
            }
            std::vector<int> rest;
            for (int s : shape) if (!used.count(s)) rest.push_back(s);
            if (rest.size() < ordinary.size()) return;
            std::set<std::set<int>> support_sets{{}};
            for (const auto& option : selected) {
                const auto& p = problem.passengers[option.passenger];
                const auto rule = ssr_rule(problem, p);
                if (!p.need_cared && !rule.requires_caregiver) continue;
                const auto& seat = problem.seats[option.seat];
                const auto& neighbors = rule.caregiver_allow_cross_aisle ? seat.row_neighbors : seat.same_block_neighbors;
                std::set<std::set<int>> expanded;
                for (const auto& previous : support_sets) for (int s : neighbors) {
                    if (Clock::now() >= deadline) return;
                    if (std::find(rest.begin(), rest.end(), s) != rest.end()) {
                        auto next = previous; next.insert(s);
                        if (next.size() <= ordinary.size()) expanded.insert(std::move(next));
                    }
                }
                support_sets = std::move(expanded);
                if (support_sets.empty()) return;
            }
            for (const auto& forced : support_sets) {
                if (Clock::now() >= deadline) return;
                std::vector<std::vector<double>> cost(rest.size(), std::vector<double>(rest.size()));
                for (size_t p = 0; p < rest.size(); ++p) for (size_t s = 0; s < rest.size(); ++s) {
                    if (p >= ordinary.size()) cost[p][s] = forced.count(rest[s]) ? kForbidden : 0.0;
                    else {
                        const auto option = ordinary_options[ordinary[p]].find(rest[s]);
                        cost[p][s] = option == ordinary_options[ordinary[p]].end() ? kForbidden : option->second->individual_cost;
                    }
                }
                const auto matching = match(cost);
                if (matching.size() != rest.size()) continue;
                const size_t special_count = selected.size();
                for (size_t p = 0; p < ordinary.size(); ++p)
                    selected.push_back(*ordinary_options[ordinary[p]].at(rest[matching[p]]));
                if (rich_placements_caregiver_ok(problem, g, selected)) {
                    auto pattern = build_rich_exact_pattern(problem, g, selected, active_types);
                    std::vector<int> assignment(problem.passengers.size(), -1);
                    for (const auto& entry : pattern.assignments) assignment[entry.first] = entry.second;
                    fitted.emplace_back(-evaluate_rich_group_score(problem, assignment, g).total(), std::move(pattern));
                }
                selected.resize(special_count);
            }
        };
        visit(visit, 0);
        std::stable_sort(fitted.begin(), fitted.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
        for (size_t i = 0; i < std::min(size_t{3}, fitted.size()) && generated < 64; ++i) {
            patterns.push_back({std::move(fitted[i].second), "geometry"}); ++generated;
        }
    }
}
}  // namespace full_cpp
