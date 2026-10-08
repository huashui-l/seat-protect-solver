#include "full_cpp_solver_core.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <limits>
#include <map>
#include <set>
#include <ostream>
#include <iomanip>
#include <tuple>
#include <stdexcept>
#include <charconv>
#include <cstring>
#include <functional>
#include <future>
#include <numeric>

namespace full_cpp {

using native_json::Value;

// Equality key for Python json.dumps(sort_keys=True), without needing its textual float format.
// Integer literals stay exact; float literals compare as parsed doubles, including signed zero.
std::string rich_raw_fingerprint(const Value& value) {
    const auto frame = [](const std::string& text) { return std::to_string(text.size()) + ":" + text; };
    switch (value.type) {
    case Value::Type::Null: return "n";
    case Value::Type::Boolean: return value.boolean ? "t" : "f";
    case Value::Type::String: return "s" + frame(value.string);
    case Value::Type::Number: {
        if (value.number_token.find_first_of(".eE") == std::string::npos)
            return "i" + frame(value.number_token == "-0" ? "0" : value.number_token);
        std::uint64_t bits;
        std::memcpy(&bits, &value.number, sizeof(bits));
        return "d" + frame(std::to_string(bits));
    }
    case Value::Type::Array: {
        std::string result = "a";
        for (const auto& item : value.array) result += frame(rich_raw_fingerprint(item));
        return result;
    }
    case Value::Type::Object: {
        std::string result = "o";
        for (const auto& item : value.object) result += frame(item.first) + frame(rich_raw_fingerprint(item.second));
        return result;
    }
    }
    throw std::runtime_error("unknown JSON value type");
}

const Value& required(const Value& value, const std::string& key) {
    return value.at(key);
}

std::string optional_string(const Value& value, const std::string& key) {
    const Value* item = value.find(key);
    return item ? item->string_or() : std::string{};
}

bool optional_bool(const Value& value, const std::string& key) {
    const Value* item = value.find(key);
    return item && item->bool_or();
}

bool yes(const Value& value, const std::string& key) {
    return optional_string(value, key) == "Y";
}

double optional_number(const Value& value, const std::string& key) {
    const Value* item = value.find(key);
    if (!item) return std::numeric_limits<double>::quiet_NaN();
    if (item->is_number()) return item->number;
    if (item->is_string() && !item->string.empty()) {
        try { return std::stod(item->string); } catch (const std::exception&) { return std::numeric_limits<double>::quiet_NaN(); }
    }
    return std::numeric_limits<double>::quiet_NaN();
}

std::string cabin_class(const std::string& raw) {
    if (raw == "F" || raw == "A" || raw == "First") return "First";
    if (raw == "C" || raw == "J" || raw == "Business") return "Business";
    if (raw == "W" || raw == "P" || raw == "PremiumEconomy") return "PremiumEconomy";
    if (raw == "Y" || raw == "E" || raw == "Economy") return "Economy";
    return raw;
}

std::filesystem::path resolve_from_config(
    const std::filesystem::path& config_path,
    const std::string& raw
) {
    std::filesystem::path path(raw);
    if (path.is_absolute()) return path;
    return config_path.parent_path() / path;
}

void build_topology(Problem& problem) {
    std::map<int, std::vector<int>> rows;
    for (int index = 0; index < static_cast<int>(problem.seats.size()); ++index) {
        rows[problem.seats[index].row].push_back(index);
    }
    for (auto& row_item : rows) {
        auto& indices = row_item.second;
        std::sort(indices.begin(), indices.end(), [&](int left, int right) {
            return problem.seats[left].column < problem.seats[right].column;
        });
        std::vector<bool> aisle_break(indices.size(), false);
        for (size_t index = 0; index + 1 < indices.size();) {
            if (problem.seats[indices[index]].aisle
                && problem.seats[indices[index + 1]].aisle) {
                aisle_break[index] = true;
                index += 2;
            } else {
                ++index;
            }
        }
        std::vector<double> raw_x(indices.size(), 0.0);
        int subrow = 0;
        for (size_t index = 0; index < indices.size(); ++index) {
            Seat& seat = problem.seats[indices[index]];
            seat.index_in_row = static_cast<int>(index);
            seat.subrow = subrow;
            if (index + 1 < indices.size()) {
                raw_x[index + 1] = raw_x[index] + problem.seat_spacing
                    + (aisle_break[index] ? problem.aisle_gap : 0.0);
                if (aisle_break[index]) ++subrow;
            }
        }
        const double center = indices.empty()
            ? 0.0 : (raw_x.front() + raw_x.back()) / 2.0;
        for (size_t index = 0; index < indices.size(); ++index) {
            Seat& seat = problem.seats[indices[index]];
            seat.x = raw_x[index] - center;
            seat.y = seat.row * problem.row_spacing;
            for (int offset : {-1, 1}) {
                const int other_position = static_cast<int>(index) + offset;
                if (other_position < 0
                    || other_position >= static_cast<int>(indices.size())) continue;
                const int other = indices[other_position];
                seat.row_neighbors.push_back(other);
                if (problem.seats[other].subrow == seat.subrow) {
                    seat.same_block_neighbors.push_back(other);
                }
            }
        }
    }
}

void load_seats(const Value& seatmap, Problem& problem) {
    const Value& seats = required(seatmap, "seats");
    if (!seats.is_array()) throw std::runtime_error("seatmap.seats must be an array");
    problem.seats.reserve(seats.array.size());
    for (const Value& raw : seats.array) {
        Seat seat;
        seat.id = required(raw, "seatId").string_or();
        seat.row = static_cast<int>(std::llround(required(raw, "row").number_or()));
        seat.column = required(raw, "col").string_or();
        seat.window = optional_bool(raw, "isWindow");
        seat.aisle = optional_bool(raw, "isAisle");
        seat.exit_row = optional_bool(raw, "isExitRow");
        seat.bassinet = optional_bool(raw, "hasBassinet");
        seat.extra_legroom = optional_bool(raw, "extraLegroom");
        seat.near_toilet = optional_bool(raw, "nearToilet") || optional_bool(raw, "isNearToilet");
        seat.cabin = optional_string(raw, "seatClass");
        seat.explicit_value = optional_number(raw, "seatValue");
        if (std::isnan(seat.explicit_value)) seat.explicit_value = optional_number(raw, "value");
        if (!problem.seat_index.emplace(seat.id, static_cast<int>(problem.seats.size())).second) {
            throw std::runtime_error("duplicate seatId: " + seat.id);
        }
        problem.seats.push_back(std::move(seat));
    }
    build_topology(problem);
}

SsrRule ssr_rule(const Problem& problem, const Passenger& passenger) {
    SsrRule rule;
    if (passenger.ssr.empty()) {
        rule.allow_exit_row = true;
        return rule;
    }
    const auto found = problem.ssr_rules.find(passenger.ssr);
    return found == problem.ssr_rules.end() ? rule : found->second;
}

bool is_adult_caregiver(const Passenger& passenger) {
    return passenger.ssr.empty() && !passenger.need_cared
        && !passenger.need_both_empty && !passenger.need_single_empty;
}

}  // namespace full_cpp
