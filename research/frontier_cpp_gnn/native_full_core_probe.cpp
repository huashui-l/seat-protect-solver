#include "full_cpp_solver_core.hpp"

#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv) {
    std::string input_path;
    std::string config_path;
    std::string output_path;
    for (int index = 1; index < argc;) {
        const std::string option = argv[index++];
        if (option == "--input" && index < argc) input_path = argv[index++];
        else if (option == "--config" && index < argc) config_path = argv[index++];
        else if (option == "--output" && index < argc) output_path = argv[index++];
        else return 2;
    }
    if (input_path.empty() || config_path.empty()) return 2;
    try {
        const full_cpp::Problem problem = full_cpp::load_problem(input_path, config_path);
        const full_cpp::FixedSeatContext fixed_context = full_cpp::preprocess_fixed_seats(problem);
        full_cpp::AssignmentState initial_state(problem, &fixed_context);
        bool state_self_test = false;
        for (int passenger = 0;
             passenger < static_cast<int>(problem.passengers.size()) && !state_self_test; ++passenger) {
            if (initial_state.passenger_to_seat[passenger] >= 0) continue;
            for (int seat = 0; seat < static_cast<int>(problem.seats.size()); ++seat) {
                if (!initial_state.can_assign(passenger, seat)) continue;
                const full_cpp::AssignmentSnapshot before = initial_state.save();
                if (!initial_state.assign(passenger, seat)) throw std::runtime_error("state assign failed");
                full_cpp::AssignmentSnapshot assigned = initial_state.save();
                initial_state.remove(passenger);
                if (initial_state.seat_to_passenger != before.seat_to_passenger
                    || initial_state.passenger_to_seat != before.passenger_to_seat
                    || initial_state.blocked_count != before.blocked_count
                    || initial_state.assigned_blocked != before.assigned_blocked
                    || initial_state.owner_group_by_seat != before.owner_group_by_seat
                    || initial_state.seat_ssr_passenger != before.seat_ssr_passenger) {
                    throw std::runtime_error("state remove did not restore the prior state");
                }
                initial_state.restore(std::move(assigned));
                if (initial_state.passenger_to_seat[passenger] != seat) {
                    throw std::runtime_error("state restore failed");
                }
                initial_state.restore(before);
                state_self_test = true;
                break;
            }
        }
        std::ofstream file;
        std::ostream* output = &std::cout;
        if (!output_path.empty()) {
            file.open(output_path);
            if (!file) return 2;
            output = &file;
        }
        int fixed = 0, ssr = 0, cared = 0, single = 0, both = 0;
        for (const auto& passenger : problem.passengers) {
            fixed += !passenger.fixed_seat.empty();
            ssr += !passenger.ssr.empty();
            cared += passenger.need_cared;
            single += passenger.need_single_empty;
            both += passenger.need_both_empty;
        }
        int deterministic_blocked = 0;
        for (bool blocked : fixed_context.deterministic_blocked) deterministic_blocked += blocked;
        int initially_assigned = 0, initially_blocked = 0;
        for (int passenger_seat : initial_state.passenger_to_seat) initially_assigned += passenger_seat >= 0;
        for (int count : initial_state.blocked_count) initially_blocked += count > 0;
        *output << std::setprecision(17)
            << "{\"schema\":\"native_full_core_probe_v1\","
            << "\"case_id\":\"" << problem.case_id << "\","
            << "\"direction\":\"" << problem.direction << "\","
            << "\"seat_count\":" << problem.seats.size() << ','
            << "\"group_count\":" << problem.groups.size() << ','
            << "\"passenger_count\":" << problem.passengers.size() << ','
            << "\"fixed_count\":" << fixed << ','
            << "\"ssr_count\":" << ssr << ','
            << "\"need_cared_count\":" << cared << ','
            << "\"single_protection_count\":" << single << ','
            << "\"double_protection_count\":" << both << ','
            << "\"deterministic_fixed_blocked_count\":" << deterministic_blocked << ','
            << "\"initially_assigned_count\":" << initially_assigned << ','
            << "\"initially_blocked_count\":" << initially_blocked << ','
            << "\"state_self_test\":" << (state_self_test ? "true" : "false") << ','
            << "\"seats\":[";
        for (size_t index = 0; index < problem.seats.size(); ++index) {
            if (index) *output << ',';
            const auto& seat = problem.seats[index];
            *output << "{\"id\":\"" << seat.id << "\",\"row\":" << seat.row
                    << ",\"column\":\"" << seat.column << "\",\"subrow\":"
                    << seat.subrow << ",\"index_in_row\":" << seat.index_in_row
                    << ",\"x\":" << seat.x << ",\"y\":" << seat.y
                    << ",\"cabin\":\"" << seat.cabin << "\",\"window\":"
                    << (seat.window ? "true" : "false") << ",\"aisle\":"
                    << (seat.aisle ? "true" : "false") << ",\"exit_row\":"
                    << (seat.exit_row ? "true" : "false") << ",\"bassinet\":"
                    << (seat.bassinet ? "true" : "false") << ",\"extra_legroom\":"
                    << (seat.extra_legroom ? "true" : "false") << ",\"near_toilet\":"
                    << (seat.near_toilet ? "true" : "false") << ",\"same_block_neighbors\":[";
            for (size_t neighbor = 0; neighbor < seat.same_block_neighbors.size(); ++neighbor) {
                if (neighbor) *output << ',';
                *output << "\"" << problem.seats[seat.same_block_neighbors[neighbor]].id << "\"";
            }
            *output << "],\"row_neighbors\":[";
            for (size_t neighbor = 0; neighbor < seat.row_neighbors.size(); ++neighbor) {
                if (neighbor) *output << ',';
                *output << "\"" << problem.seats[seat.row_neighbors[neighbor]].id << "\"";
            }
            *output << "]}";
        }
        *output << "],\"passengers\":[";
        for (size_t index = 0; index < problem.passengers.size(); ++index) {
            if (index) *output << ',';
            const auto& passenger = problem.passengers[index];
            *output << "{\"group_index\":" << passenger.group
                    << ",\"group_id\":" << passenger.group_id
                    << ",\"hostnum\":" << passenger.hostnum
                    << ",\"cabin\":\"" << passenger.cabin
                    << "\",\"ssr\":\"" << passenger.ssr
                    << "\",\"old_seat\":\"" << passenger.old_seat
                    << "\",\"fixed_seat\":\"" << passenger.fixed_seat
                    << "\",\"need_cared\":" << (passenger.need_cared ? "true" : "false")
                    << ",\"need_both_empty\":" << (passenger.need_both_empty ? "true" : "false")
                    << ",\"need_single_empty\":" << (passenger.need_single_empty ? "true" : "false")
                    << ",\"same_subrow_no_other_ssr\":"
                    << (passenger.same_subrow_no_other_ssr ? "true" : "false")
                    << ",\"same_row_no_other_ssr\":"
                    << (passenger.same_row_no_other_ssr ? "true" : "false") << '}';
        }
        *output << "],\"groups\":[";
        for (size_t index = 0; index < problem.groups.size(); ++index) {
            if (index) *output << ',';
            const auto& group = problem.groups[index];
            *output << "{\"id\":" << group.id << ",\"passengers\":[";
            for (size_t passenger = 0; passenger < group.passengers.size(); ++passenger) {
                if (passenger) *output << ',';
                *output << group.passengers[passenger];
            }
            *output << "]}";
        }
        *output << "]}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 3;
    }
}
