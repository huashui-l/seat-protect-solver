#pragma once

#include "full_cpp_solver_core.hpp"

#include <chrono>
#include <vector>

namespace full_cpp {

struct RichPatternResult {
    bool complete = false;
    std::vector<int> passenger_to_seat;
    double score = 0.0;
    int pattern_count = 0;
    int selected_pattern_count = 0;
    int baby_pair_count = 0;
    double master_score = 0.0;
};

RichPatternResult run_rich_pattern_master(
    const Problem& problem,
    const std::vector<int>& incumbent,
    std::chrono::steady_clock::time_point deadline
);

}  // namespace full_cpp
