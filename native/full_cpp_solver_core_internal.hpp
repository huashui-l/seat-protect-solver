#pragma once

#include "full_cpp_solver_core.hpp"

#include <filesystem>
#include <string>

namespace full_cpp {

using native_json::Value;

// Shared input/topology helpers used by the functional implementation units.
std::string rich_raw_fingerprint(const Value& value);
const Value& required(const Value& value, const std::string& key);
std::string optional_string(const Value& value, const std::string& key);
bool optional_bool(const Value& value, const std::string& key);
bool yes(const Value& value, const std::string& key);
double optional_number(const Value& value, const std::string& key);
std::string cabin_class(const std::string& raw);
std::filesystem::path resolve_from_config(
    const std::filesystem::path& config_path, const std::string& raw);
void build_topology(Problem& problem);
void load_seats(const Value& seatmap, Problem& problem);
SsrRule ssr_rule(const Problem& problem, const Passenger& passenger);
bool is_adult_caregiver(const Passenger& passenger);

}  // namespace full_cpp
