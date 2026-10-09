#pragma once

#include "full_cpp_solver_core.hpp"
#include "gurobi_c.h"

namespace full_cpp {

// Add exact conditional row/subrow SSR constraints to a group-pattern master.
// Pattern variables occupy indices [0, columns.size()); activation variables follow.
int add_rich_master_ssr_constraints(GRBmodel* model, const Problem& problem,
    const std::vector<std::pair<int, const RichElitePattern*>>& columns);

}  // namespace full_cpp
