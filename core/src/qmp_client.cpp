// DroidForge core - QMP client + minimal JSON implementation.
//
// The JSON parser is a tiny, hand-rolled recursive-descent parser covering the
// subset QMP uses (objects, arrays, strings, numbers, booleans, null). It is
// deliberately kept in this single translation unit so the rest of the core
// stays dependency-free and the parser is easy to unit-test.

#include "droidforge/qmp_client.hpp"

#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>

#include <array>
#include <cctype>
#include <cmath>
#include <cstring>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>

namespace droidforge {

// ===========================================================================
//  JSON parsing
// ===========================================================================
namespace detail {

struct Parser {
    const char* p;
    const char* end;

    explicit Parser(const std::string& s) : p(s.data()), end(s.data() + s.size()) {}

    [[noreturn]] void fail(const std::string& msg) const {
        throw std::runtime_error("json: " + msg);
    }

    void skipWs() {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) ++p;
    }

    char peek() { skipWs(); if (p >= end) fail("unexpected end of input"); return *p; }

    bool consume(char c) {
        skipWs();
        if (p < end && *p == c) { ++p; return true; }
        return false;
    }

    void expect(char c) {
        if (!consume(c)) fail(std::string("expected '") + c + "'");
    }

    QmpValue parseValue() {
        char c = peek();
        switch (c) {
            case '{': return parseObject();
            case '[': return parseArray();
            case '"': { QmpValue v; v.type = QmpValue::Type::String; v.string = parseString(); return v; }
            case 't': { QmpValue v; v.type = QmpValue::Type::Bool; v.boolean = true;  return literal("true",  v); }
            case 'f': { QmpValue v; v.type = QmpValue::Type::Bool; v.boolean = false; return literal("false", v); }
            case 'n': { QmpValue v; v.type = QmpValue::Type::Null;                    return literal("null",  v); }
            default:  return parseNumber();
        }
    }

    QmpValue literal(const char* word, QmpValue v) {
        size_t n = std::strlen(word);
        if (static_cast<size_t>(end - p) < n || std::memcmp(p, word, n) != 0)
            fail("bad literal");
        p += n;
        return v;
    }

    std::string parseString() {
        expect('"');
        std::string out;
        while (p < end && *p != '"') {
            char c = *p++;
            if (c == '\\') {
                if (p >= end) fail("bad escape");
                char e = *p++;
                switch (e) {
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case 'n': out += '\n'; break;
                    case 'r': out += '\r'; break;
                    case 't': out += '\t'; break;
                    case 'u': {
                        if (end - p < 4) fail("bad \\u escape");
                        unsigned code = 0;
                        for (int i = 0; i < 4; ++i) {
                            char h = *p++;
                            code <<= 4;
                            if (h >= '0' && h <= '9') code |= h - '0';
                            else if (h >= 'a' && h <= 'f') code |= h - 'a' + 10;
                            else if (h >= 'A' && h <= 'F') code |= h - 'A' + 10;
                            else fail("bad \\u escape");
                        }
                        // Encode as UTF-8 (BMP only is sufficient for QMP).
                        if (code < 0x80) out += char(code);
                        else if (code < 0x800) {
                            out += char(0xC0 | (code >> 6));
                            out += char(0x80 | (code & 0x3F));
                        } else {
                            out += char(0xE0 | (code >> 12));
                            out += char(0x80 | ((code >> 6) & 0x3F));
                            out += char(0x80 | (code & 0x3F));
                        }
                        break;
                    }
                    default: fail("unknown escape");
                }
            } else {
                out += c;
            }
        }
        expect('"');
        return out;
    }

    QmpValue parseNumber() {
        const char* start = p;
        while (p < end && (std::isdigit(static_cast<unsigned char>(*p)) || *p == '-' ||
                           *p == '+' || *p == '.' || *p == 'e' || *p == 'E')) ++p;
        if (start == p) fail("bad number");
        QmpValue v;
        v.type = QmpValue::Type::Number;
        v.number = std::strtod(std::string(start, p).c_str(), nullptr);
        return v;
    }

    QmpValue parseObject() {
        expect('{');
        QmpValue v;
        v.type = QmpValue::Type::Object;
        if (consume('}')) return v;
        for (;;) {
            std::string key = parseString();
            expect(':');
            v.object[key] = parseValue();
            if (consume(',')) continue;
            expect('}');
            break;
        }
        return v;
    }

    QmpValue parseArray() {
        expect('[');
        QmpValue v;
        v.type = QmpValue::Type::Array;
        if (consume(']')) return v;
        for (;;) {
            v.array.push_back(parseValue());
            if (consume(',')) continue;
            expect(']');
            break;
        }
        return v;
    }
};

} // namespace detail

QmpValue jsonParse(const std::string& text) {
    detail::Parser parser(text);
    auto v = parser.parseValue();
    parser.skipWs();
    if (parser.p != parser.end)
        throw std::runtime_error("json: trailing characters after value");
    return v;
}

std::string jsonEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default: out += c;
        }
    }
    return out;
}

namespace {
// Forward decl so the recursive Object/Array cases can call the stream helper.
void jsonDumpToStream(std::ostringstream& os, const QmpValue& v);
} // namespace

std::string jsonDump(const QmpValue& v) {
    std::ostringstream os;
    switch (v.type) {
        case QmpValue::Type::Null:   os << "null"; break;
        case QmpValue::Type::Bool:   os << (v.boolean ? "true" : "false"); break;
        case QmpValue::Type::Number: {
            // Emit integers without a trailing ".0" when possible.
            if (v.number == std::floor(v.number) &&
                std::fabs(v.number) < 9.0e15) {
                os << static_cast<long long>(v.number);
            } else {
                os << v.number;
            }
            break;
        }
        case QmpValue::Type::String: os << '"' << jsonEscape(v.string) << '"'; break;
        case QmpValue::Type::Array: {
            os << '[';
            for (size_t i = 0; i < v.array.size(); ++i) {
                if (i) os << ',';
                jsonDumpToStream(os, v.array[i]);
            }
            os << ']';
            break;
        }
        case QmpValue::Type::Object: {
            os << '{';
            bool first = true;
            for (auto& [k, val] : v.object) {
                if (!first) os << ',';
                first = false;
                os << '"' << jsonEscape(k) << "\":";
                jsonDumpToStream(os, val);
            }
            os << '}';
            break;
        }
    }
    return os.str();
}

QmpObject jsonToObj(const QmpValue& v) {
    if (v.type != QmpValue::Type::Object)
        throw std::runtime_error("json: not an object");
    return v.object;
}

// ---------------------------------------------------------------------------
// Stream helper + QMP socket client
// ---------------------------------------------------------------------------
namespace {
void jsonDumpToStream(std::ostringstream& os, const QmpValue& v) {
    switch (v.type) {
        case QmpValue::Type::Null:   os << "null"; break;
        case QmpValue::Type::Bool:   os << (v.boolean ? "true" : "false"); break;
        case QmpValue::Type::Number: {
            if (v.number == std::floor(v.number) &&
                std::fabs(v.number) < 9.0e15)
                os << static_cast<long long>(v.number);
            else
                os << v.number;
            break;
        }
        case QmpValue::Type::String: os << '"' << jsonEscape(v.string) << '"'; break;
        case QmpValue::Type::Array: {
            os << '[';
            for (size_t i = 0; i < v.array.size(); ++i) {
                if (i) os << ',';
                jsonDumpToStream(os, v.array[i]);
            }
            os << ']';
            break;
        }
        case QmpValue::Type::Object: {
            os << '{';
            bool first = true;
            for (auto& [k, val] : v.object) {
                if (!first) os << ',';
                first = false;
                os << '"' << jsonEscape(k) << "\":";
                jsonDumpToStream(os, val);
            }
            os << '}';
            break;
        }
    }
}
} // namespace

// ===========================================================================
//  QmpClient
// ===========================================================================
QmpClient::QmpClient() = default;

QmpClient::~QmpClient() { closeFd(); }

void QmpClient::closeFd() {
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
    capabilities_negotiated_ = false;
}

void QmpClient::connect(const std::string& socket_path) {
    closeFd();

    // If the caller passed an "unix:..." prefix (common in -qmp args), strip it.
    std::string path = socket_path;
    if (path.rfind("unix:", 0) == 0) path.erase(0, 5);

    fd_ = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd_ < 0) throw std::runtime_error("socket() failed: " + std::string(std::strerror(errno)));

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof(addr.sun_path))
        throw std::runtime_error("unix socket path too long: " + path);
    std::memcpy(addr.sun_path, path.c_str(), path.size());

    if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        closeFd();
        throw std::runtime_error("connect(" + path + ") failed: " + std::string(std::strerror(errno)));
    }

    handshake();
}

void QmpClient::disconnect() { closeFd(); }

void QmpClient::handshake() {
    // QMP greeting: 1 or more lines of { "QMP": { ... } }, ending with
    // { "QMP": { "capabilities": [...] } }. We must reply with
    // { "execute": "qmp_capabilities" } before issuing commands.
    std::string line;
    bool saw_capabilities = false;
    while (true) {
        line = readLine();
        auto v = parseLine(line);
        if (auto q = v.find("QMP")) {
            if (q->find("capabilities")) saw_capabilities = true;
            if (saw_capabilities) break;
        }
    }
    sendLine("{\"execute\":\"qmp_capabilities\"}");
    // Expect a success echo.
    auto ack = parseLine(readLine());
    auto* ret = ack.find("return");
    if (!ret)
        throw std::runtime_error("qmp_capabilities did not return 'return'");
    capabilities_negotiated_ = true;
}

std::string QmpClient::readLine() {
    std::string line;
    char c;
    while (true) {
        ssize_t n = ::read(fd_, &c, 1);
        if (n < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("read() failed: " + std::string(std::strerror(errno)));
        }
        if (n == 0) throw std::runtime_error("QMP socket closed by peer");
        if (c == '\n') break;
        line += c;
    }
    // Strip trailing '\r' if present.
    if (!line.empty() && line.back() == '\r') line.pop_back();
    return line;
}

QmpValue QmpClient::parseLine(const std::string& line) {
    if (line.empty()) throw std::runtime_error("empty QMP line");
    try {
        return jsonParse(line);
    } catch (const std::exception& e) {
        throw std::runtime_error(std::string("QMP parse error: ") + e.what() +
                                 " (line: " + line.substr(0, 200) + ")");
    }
}

void QmpClient::sendLine(const std::string& line) {
    std::string payload = line;
    if (payload.empty() || payload.back() != '\n') payload += '\n';
    size_t sent = 0;
    while (sent < payload.size()) {
        ssize_t n = ::write(fd_, payload.data() + sent, payload.size() - sent);
        if (n < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("write() failed: " + std::string(std::strerror(errno)));
        }
        sent += static_cast<size_t>(n);
    }
}

QmpValue QmpClient::execute(const std::string& command, const QmpObject& args) {
    if (fd_ < 0) throw std::runtime_error("QMP not connected");
    QmpValue cmd;
    cmd.type = QmpValue::Type::Object;

    QmpValue execStr;
    execStr.type = QmpValue::Type::String;
    execStr.string = command;
    cmd.object["execute"] = execStr;

    if (!args.empty()) {
        QmpValue argsObj;
        argsObj.type = QmpValue::Type::Object;
        argsObj.object = args;
        cmd.object["arguments"] = argsObj;
    }

    sendLine(jsonDump(cmd));
    auto resp = parseLine(readLine());

    // QMP errors arrive as { "error": { "class": ..., "desc": ... } }.
    if (auto err = resp.find("error")) {
        std::string desc = "unknown";
        if (auto e = err; e && e->find("desc"))
            desc = e->find("desc")->asString();
        throw std::runtime_error("QMP error executing '" + command + "': " + desc);
    }
    auto ret = resp.find("return");
    return ret ? *ret : QmpValue{};
}

void QmpClient::cont()  { execute("cont"); }
void QmpClient::stop()  { execute("stop"); }
void QmpClient::quit()  {
    // "quit" is a QMP command on QEMU >= 4. Older builds use system_powerdown
    // or the HMP "poweroff"; try quit first, fall back to HMP on failure.
    try {
        execute("quit");
    } catch (const std::exception&) {
        hmp("poweroff");
    }
}

QmpValue QmpClient::queryStatus() { return execute("query-status"); }
QmpValue QmpClient::queryCpu()    { return execute("query-cpus"); }

QmpValue QmpClient::systemReset(bool hard) {
    QmpValue hardV;
    hardV.type = QmpValue::Type::Bool;
    hardV.boolean = hard;
    QmpObject args;
    args["hard"] = hardV;
    return execute("system_reset", args);
}

QmpValue QmpClient::hmp(const std::string& hmp_command) {
    QmpValue cmdV;
    cmdV.type = QmpValue::Type::String;
    cmdV.string = hmp_command;
    QmpObject args;
    args["command-line"] = cmdV;
    return execute("human-monitor-command", args);
}

} // namespace droidforge
