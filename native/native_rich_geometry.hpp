#pragma once

#include "full_cpp_solver_core.hpp"

namespace full_cpp {
using RichGeometryOrders = std::map<std::string, std::vector<std::vector<int>>>;

// Geometry prefixes are shared across group sizes and read by parallel workers.
RichGeometryOrders build_rich_geometry_orders(const Problem& problem,
    const FixedSeatContext& fixed, std::chrono::steady_clock::time_point deadline);

// Enumerate special placements; solve ordinary identities by exact matching.
void generate_rich_geometry_patterns(const Problem& problem, int group_index,
    const std::vector<std::vector<RichPlacement>>& options, const RichGeometryOrders& orders,
    const std::vector<std::string>& active_ssr_types, std::chrono::steady_clock::time_point deadline,
    std::vector<RichTieredPattern>& patterns);
}  // namespace full_cpp
