#pragma once

// DroidForge core – scrcpy-protocol touch injector.
//
// Encodes and sends touch/keyboard/mouse events to a running scrcpy-server
// instance over an ADB-forwarded TCP socket.
//
// Protocol: scrcpy 4.x binary control protocol (big-endian).
// Reference: https://github.com/Genymobile/scrcpy/blob/master/DEVELOP.md
//
// Usage:
//   TouchInjector inj;
//   inj.connect("127.0.0.1", 27183);          // ADB-forwarded port
//   inj.touchDown(0, 400, 600, 1280, 720);    // finger 0 down at (400,600)
//   inj.touchMove(0, 410, 605, 1280, 720);
//   inj.touchUp(0, 410, 605, 1280, 720);

#include <cstdint>
#include <string>
#include <vector>

namespace droidforge {

// Maximum simultaneous touch points (Android hard limit).
inline constexpr int kMaxTouches = 10;

// scrcpy 4.x control message types (type byte).
enum class ScrcpyMsgType : uint8_t {
    InjectKeycode   = 0x00,
    InjectText      = 0x01,
    InjectTouch     = 0x02,
    InjectScroll    = 0x03,
    BackOrScreen    = 0x04,
    ExpandNotif     = 0x05,
    CollapseNotif   = 0x06,
    GetClipboard    = 0x07,
    SetClipboard    = 0x08,
    SetScreenPower  = 0x0A,
    RotateDevice    = 0x0B,
};

// Android MotionEvent action constants.
enum class TouchAction : uint8_t {
    Down = 0,
    Up   = 1,
    Move = 2,
};

// Android KeyEvent action constants.
enum class KeyAction : uint8_t {
    Down = 0,
    Up   = 1,
};

// ---------------------------------------------------------------------------
// TouchInjector – connects to the scrcpy-server control socket and sends
// touch/keyboard events using the scrcpy 4.x binary protocol.
// ---------------------------------------------------------------------------
class TouchInjector {
public:
    TouchInjector();
    ~TouchInjector();

    // Connect to the scrcpy-server control socket (TCP, ADB-forwarded).
    // host: "127.0.0.1"  port: typically 27183.
    // Throws std::runtime_error on connection failure.
    void connect(const std::string& host, uint16_t port);
    void disconnect();
    bool connected() const { return fd_ >= 0; }

    // ---------------------------------------------------------------------------
    // Touch injection.
    // pointer_id: 0–9 (stable finger identifier for the gesture lifetime).
    // x, y:       pixel coordinates in the guest's framebuffer space.
    // w, h:       guest screen dimensions (needed by the protocol for scaling).
    // pressure:   0.0–1.0 (1.0 = full pressure).
    // ---------------------------------------------------------------------------
    void touchDown(int64_t pointer_id,
                   int32_t x, int32_t y,
                   int32_t screen_w, int32_t screen_h,
                   float pressure = 1.0f);
    void touchMove(int64_t pointer_id,
                   int32_t x, int32_t y,
                   int32_t screen_w, int32_t screen_h,
                   float pressure = 1.0f);
    void touchUp(int64_t pointer_id,
                 int32_t x, int32_t y,
                 int32_t screen_w, int32_t screen_h);

    // Convenience: tap at (x,y) with optional hold duration.
    // This is a synchronous blocking call if duration_ms > 0.
    void tap(int32_t x, int32_t y,
             int32_t screen_w, int32_t screen_h,
             int duration_ms = 50);

    // Swipe from (x0,y0) to (x1,y1) over duration_ms, steps intermediate points.
    void swipe(int32_t x0, int32_t y0,
               int32_t x1, int32_t y1,
               int32_t screen_w, int32_t screen_h,
               int duration_ms = 300,
               int steps = 15);

    // ---------------------------------------------------------------------------
    // Keyboard injection (Android KeyEvent.KEYCODE_* constants).
    // ---------------------------------------------------------------------------
    void keyDown(int32_t keycode, int32_t meta_state = 0);
    void keyUp(int32_t keycode, int32_t meta_state = 0);
    void keyTap(int32_t keycode, int32_t meta_state = 0);

    // Inject UTF-8 text (scrcpy InjectText message).
    void injectText(const std::string& text);

protected:
    // Message encoding helpers. Protected so unit tests can subclass and
    // verify the wire format without opening a real TCP connection.
    std::vector<uint8_t> buildTouchMessage(TouchAction action,
                                           int64_t pointer_id,
                                           int32_t x, int32_t y,
                                           int32_t screen_w, int32_t screen_h,
                                           float pressure) const;

    std::vector<uint8_t> buildKeyMessage(KeyAction action,
                                         int32_t keycode,
                                         int32_t repeat,
                                         int32_t meta_state) const;

private:
    void sendRaw(const std::vector<uint8_t>& msg);
    void writeAll(const uint8_t* buf, size_t len);

    // Big-endian helpers.
    static void putU16(std::vector<uint8_t>& buf, uint16_t v);
    static void putU32(std::vector<uint8_t>& buf, uint32_t v);
    static void putU64(std::vector<uint8_t>& buf, uint64_t v);

    int fd_{-1};
};

} // namespace droidforge
