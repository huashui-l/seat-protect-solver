#include "full_cpp_solver_core_sections.hpp"

namespace full_cpp {

RichStageBudgets calculate_rich_stage_budgets(
    const Problem& problem, const native_json::Value& algorithm
) {
    auto number = [&](const char* key, double fallback) {
        const auto* value = algorithm.find(key);
        return value ? value->number_or(fallback) : fallback;
    };
    auto enabled = [&](const char* key, bool fallback) {
        const auto* value = algorithm.find(key);
        return value ? value->bool_or(fallback) : fallback;
    };
    RichStageBudgets result;
    result.business_time_limit = std::max(0.1, number("business_time_limit_seconds", 5.0));
    result.scoring_reserve = std::min(result.business_time_limit * 0.2,
        std::max(0.02, number("scoring_time_reserve", 0.1)));
    result.usable_time = std::max(0.0, result.business_time_limit - result.scoring_reserve);
    auto& stages = result.stages;
    stages = {
        {"construction", number("construction_time_budget", result.usable_time * 0.20)},
        {"repair", number("repair_time_budget", result.usable_time * 0.10)},
        {"vnd", number("vnd_time_budget", result.usable_time * 0.20)},
        {"pattern_generation", number("structured_pattern_time_budget", 1.6)},
        {"lns", number("lns_time_budget", result.usable_time * 0.50)},
        {"restricted_mip", number("restricted_pattern_mip_time_budget", 0.0)},
        {"protected_multigroup_mip", number("protected_multigroup_mip_time_budget", 0.0)},
    };
    const int travelers = static_cast<int>(problem.passengers.size());
    for (const auto& passenger : problem.passengers)
        result.seat_demand += 1 + (passenger.need_both_empty ? 2 : passenger.need_single_empty ? 1 : 0);
    result.post_protected_tail_reserve_active = result.business_time_limit >= 10.0
        && problem.seats.size() < problem.old_seats.size()
        && result.seat_demand == static_cast<int>(problem.seats.size())
        && result.seat_demand > travelers
        && double(travelers) / std::max<size_t>(1, problem.seats.size()) >= 0.93;
    result.post_protected_special_pricing_active = result.post_protected_tail_reserve_active
        && enabled("enable_post_protected_special_pricing", true);
    if (enabled("adaptive_stage_budgets", true)) {
        const double low = number("adaptive_budget_low_seat_demand", 100.0);
        const double high = std::max(low + 1.0, number("adaptive_budget_high_seat_demand", 130.0));
        const double load = std::min(1.0, std::max(0.0, (result.seat_demand - low) / (high - low)));
        stages["construction"] = 1.3 + 1.7 * load;
        stages["repair"] = 0.25;
        stages["vnd"] = 0.5 + 0.2 * load;
        stages["pattern_generation"] = 2.3 - 1.9 * load;
        stages["lns"] = 0.0;
        stages["restricted_mip"] = 0.4;
        const bool priority = enabled("enable_priority_multigroup_pattern_mip", false)
            && result.business_time_limit >= number("priority_multigroup_min_business_time_seconds", 10.0);
        stages["protected_multigroup_mip"] =
            enabled("enable_protected_multigroup_pattern_mip", false) || priority
                ? number("protected_multigroup_mip_time_budget", 8.0) : 0.0;
    }
    // Match Python's insertion order when summing before proportional scaling.
    const char* order[] = {"construction", "repair", "vnd", "pattern_generation",
        "lns", "restricted_mip", "protected_multigroup_mip"};
    double total = 0.0;
    for (const char* name : order) total += std::max(0.0, stages[name]);
    if (total > result.usable_time && total > 0.0) {
        const double scale = result.usable_time / total;
        for (auto& stage : stages) stage.second = std::max(0.0, stage.second) * scale;
    }
    return result;
}

RichStageSchedule::RichStageSchedule(const RichStageBudgets& budgets,
                                     double allocation_start, double restricted_tail_budget,
                                     double search_deadline_limit)
    : budgets_(budgets), allocation_start_(allocation_start),
      search_deadline_(std::min(allocation_start + budgets.usable_time, search_deadline_limit)),
      restricted_tail_budget_(std::max(0.0, restricted_tail_budget)) {}

RichStageWindow RichStageSchedule::begin(const std::string& stage, double now,
                                         int construction_unassigned) {
    if (stage == "special_pricing") {
        return {pricing_reserve_, std::min(search_deadline_, now + pricing_reserve_)};
    }
    double effective = budgets_.stages.at(stage) + carry_;
    if (stage == "construction") {
        effective = budgets_.stages.at(stage);
        return {effective, std::min(search_deadline_, allocation_start_ + effective)};
    }
    if (stage == "vnd") {
        pricing_reserve_ = std::min(std::max(0.0, effective - 0.1),
            budgets_.post_protected_tail_reserve_active ? 2.0 : 0.0);
        effective -= pricing_reserve_;
    }
    if (stage == "restricted_mip") {
        effective = std::max(budgets_.stages.at(stage), restricted_tail_budget_)
            + std::max(0.0, lns_deadline_ - now);
    }
    double deadline = std::min(search_deadline_, now + effective);
    if (stage == "repair") {
        if (construction_unassigned > 0) deadline = search_deadline_;
        effective = std::max(0.0, deadline - now);
    }
    if (stage == "lns") lns_deadline_ = deadline;
    return {effective, deadline};
}

void RichStageSchedule::finish(const std::string& stage,
                               const RichStageWindow& window, double now) {
    // Python keeps protected-stage carry across special pricing. LNS carry is
    // evaluated at restricted-stage entry, after intervening bookkeeping.
    if (stage != "special_pricing" && stage != "lns" && stage != "restricted_mip") {
        carry_ = std::max(0.0, window.deadline - now);
    }
}

}  // namespace full_cpp
