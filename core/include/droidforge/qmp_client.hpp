#pragma once

// DroidForge core - minimal QMP client.
//
// QMP (QEMU Machine Protocol) is a JSON-lines protocol spoken over a unix
// socket. We only implement the subset DroidForge needs:
//   * connect to an existing socket (server=on,wait=off)
//   * read the greeting (3 capability lines) and reply with "qmp_capabilities"
//   * send a command and read its return / error
//   * high-level helpers: cont/stop, quit (with a clean QMP shutdown),
//     and a "human-monitor-command" escape hatch for debugging.
//
// This is intentionally small: it is a line-oriented JSON parser, not a
// full QMP event loop. The Phase 2/3 UI will extend it with an async event
// reader on a background thread.

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace droidforge {

// One parsed JSON value. We only need: null, bool, number, string, array, object.
struct QmpValue;
using QmpObject = std::map<std::string, QmpValue>;
using QmpArray  = std::vector<QmpValue>;

struct QmpValue {
    enum class Type { Null, Bool, Number, String, Array, Object } type = Type::Null;
    bool   boolean{false};
    double number{0.0};
    std::string  string;
    QmpArray   array;
    QmpObject  object;

    const QmpValue* find(const std::string& key) const {
        auto it = object.find(key);
        return it == object.end() ? nullptr : &it->second;
    }
    std::string asString(const std::string& def = "") const {
        return type == Type::String ? string : def;
    }
    bool asBool(bool def = false) const {
        return type == Type::Bool ? boolean : def;
    }
};

// ---------------------------------------------------------------------------
// QmpClient - synchronous, line-oriented QMP client.
// ---------------------------------------------------------------------------
class QmpClient {
public:
    QmpClient();
    ~QmpClient();

    // Connect to `socket_path` (a unix path). Throws std::runtime_error on
    // failure to connect or on the QMP handshake.
    void connect(const std::string& socket_path);
    void disconnect();

    bool connected() const { return fd_ >= 0; }

    // Send a raw QMP command. `command` is the QMP command name, `args` the
    // command arguments. Returns the "return" object on success; throws
    // std::runtime_error on a QMP error response.
    QmpValue execute(const std::string& command,
                     const QmpObject& args = {});

    // High-level helpers.
    void cont();                 // resume a paused VM
    void stop();                 // pause a running VM
    void quit();                 // graceful QMP shutdown (equivalent to -S + ACPI)
    QmpValue queryStatus();      // { running: bool, status: string }
    QmpValue queryCpu();
    QmpValue systemReset(bool hard = false);

    // "human-monitor-command" escape hatch (e.g. "info pci").
    QmpValue hmp(const std::string& hmp_command);

    int fileDescriptor() const { return fd_; }

private:
    void handshake();
    std::string readLine();                 // one QMP line (or throw on EOF)
    QmpValue  parseLine(const std::string& line);
    void      sendLine(const std::string& line);
    void      closeFd();

    int  fd_{-1};
    bool capabilities_negotiated_{false};
};

// ---------------------------------------------------------------------------
// Tiny JSON helpers. Kept in core so the QMP client and keymap engine share
// them, and so tests can exercise parsing directly.
// ---------------------------------------------------------------------------
QmpValue jsonParse(const std::string& text);      // throws on malformed input
std::string jsonDump(const QmpValue& v);          // compact, no whitespace
QmpObject jsonToObj(const QmpValue& v);           // throws if not an object
std::string jsonEscape(const std::string& s);     // escape a JSON string body

} // namespace droidforge
