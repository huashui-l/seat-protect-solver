#pragma once

#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace native_json {

struct Value {
    enum class Type { Null, Boolean, Number, String, Array, Object };
    Type type = Type::Null;
    bool boolean = false;
    double number = 0.0;
    std::string string;
    std::vector<Value> array;
    std::map<std::string, Value> object;

    bool is_null() const { return type == Type::Null; }
    bool is_boolean() const { return type == Type::Boolean; }
    bool is_number() const { return type == Type::Number; }
    bool is_string() const { return type == Type::String; }
    bool is_array() const { return type == Type::Array; }
    bool is_object() const { return type == Type::Object; }

    const Value& at(const std::string& key) const {
        if (!is_object()) throw std::runtime_error("JSON value is not an object");
        const auto found = object.find(key);
        if (found == object.end()) throw std::runtime_error("missing JSON key: " + key);
        return found->second;
    }
    const Value* find(const std::string& key) const {
        if (!is_object()) return nullptr;
        const auto found = object.find(key);
        return found == object.end() ? nullptr : &found->second;
    }
    std::string string_or(const std::string& fallback = "") const {
        return is_string() ? string : fallback;
    }
    bool bool_or(bool fallback = false) const {
        return is_boolean() ? boolean : fallback;
    }
    double number_or(double fallback = 0.0) const {
        return is_number() ? number : fallback;
    }
};

class Parser {
public:
    explicit Parser(std::string text) : text_(std::move(text)) {}

    Value parse() {
        skip_space();
        Value result = parse_value();
        skip_space();
        if (position_ != text_.size()) fail("trailing characters");
        return result;
    }

private:
    std::string text_;
    size_t position_ = 0;

    [[noreturn]] void fail(const std::string& message) const {
        throw std::runtime_error(
            "JSON parse error at byte " + std::to_string(position_) + ": " + message
        );
    }
    void skip_space() {
        while (position_ < text_.size()
               && std::isspace(static_cast<unsigned char>(text_[position_]))) {
            ++position_;
        }
    }
    bool consume(char expected) {
        skip_space();
        if (position_ < text_.size() && text_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }
    void expect(char expected) {
        if (!consume(expected)) fail(std::string("expected '") + expected + "'");
    }
    Value parse_value() {
        skip_space();
        if (position_ >= text_.size()) fail("unexpected end of input");
        const char current = text_[position_];
        if (current == 'n') return parse_literal("null", Value{});
        if (current == 't') {
            Value value; value.type = Value::Type::Boolean; value.boolean = true;
            return parse_literal("true", std::move(value));
        }
        if (current == 'f') {
            Value value; value.type = Value::Type::Boolean; value.boolean = false;
            return parse_literal("false", std::move(value));
        }
        if (current == '"') {
            Value value; value.type = Value::Type::String; value.string = parse_string();
            return value;
        }
        if (current == '[') return parse_array();
        if (current == '{') return parse_object();
        if (current == '-' || std::isdigit(static_cast<unsigned char>(current))) {
            return parse_number();
        }
        fail("unexpected token");
    }
    Value parse_literal(const char* literal, Value value) {
        const std::string expected(literal);
        if (text_.compare(position_, expected.size(), expected) != 0) fail("invalid literal");
        position_ += expected.size();
        return value;
    }
    static void append_utf8(std::string& output, unsigned codepoint) {
        if (codepoint <= 0x7f) output.push_back(static_cast<char>(codepoint));
        else if (codepoint <= 0x7ff) {
            output.push_back(static_cast<char>(0xc0 | (codepoint >> 6)));
            output.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        } else {
            output.push_back(static_cast<char>(0xe0 | (codepoint >> 12)));
            output.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        }
    }
    unsigned parse_hex4() {
        unsigned value = 0;
        for (int index = 0; index < 4; ++index) {
            if (position_ >= text_.size()) fail("incomplete unicode escape");
            const char ch = text_[position_++];
            value <<= 4;
            if (ch >= '0' && ch <= '9') value += ch - '0';
            else if (ch >= 'a' && ch <= 'f') value += ch - 'a' + 10;
            else if (ch >= 'A' && ch <= 'F') value += ch - 'A' + 10;
            else fail("invalid unicode escape");
        }
        return value;
    }
    std::string parse_string() {
        expect('"');
        std::string result;
        while (position_ < text_.size()) {
            const char ch = text_[position_++];
            if (ch == '"') return result;
            if (static_cast<unsigned char>(ch) < 0x20) fail("control character in string");
            if (ch != '\\') { result.push_back(ch); continue; }
            if (position_ >= text_.size()) fail("incomplete escape");
            const char escaped = text_[position_++];
            switch (escaped) {
                case '"': result.push_back('"'); break;
                case '\\': result.push_back('\\'); break;
                case '/': result.push_back('/'); break;
                case 'b': result.push_back('\b'); break;
                case 'f': result.push_back('\f'); break;
                case 'n': result.push_back('\n'); break;
                case 'r': result.push_back('\r'); break;
                case 't': result.push_back('\t'); break;
                case 'u': append_utf8(result, parse_hex4()); break;
                default: fail("invalid escape");
            }
        }
        fail("unterminated string");
    }
    Value parse_number() {
        const char* begin = text_.c_str() + position_;
        char* end = nullptr;
        errno = 0;
        const double number = std::strtod(begin, &end);
        if (end == begin || errno == ERANGE) fail("invalid number");
        position_ += static_cast<size_t>(end - begin);
        Value value; value.type = Value::Type::Number; value.number = number;
        return value;
    }
    Value parse_array() {
        expect('[');
        Value value; value.type = Value::Type::Array;
        if (consume(']')) return value;
        do { value.array.push_back(parse_value()); } while (consume(','));
        expect(']');
        return value;
    }
    Value parse_object() {
        expect('{');
        Value value; value.type = Value::Type::Object;
        if (consume('}')) return value;
        do {
            skip_space();
            if (position_ >= text_.size() || text_[position_] != '"') fail("expected object key");
            std::string key = parse_string();
            expect(':');
            if (!value.object.emplace(std::move(key), parse_value()).second) {
                fail("duplicate object key");
            }
        } while (consume(','));
        expect('}');
        return value;
    }
};

inline Value parse_file(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open JSON file: " + path);
    std::string text((std::istreambuf_iterator<char>(input)), {});
    if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xef
        && static_cast<unsigned char>(text[1]) == 0xbb
        && static_cast<unsigned char>(text[2]) == 0xbf) text.erase(0, 3);
    return Parser(std::move(text)).parse();
}

}  // namespace native_json
