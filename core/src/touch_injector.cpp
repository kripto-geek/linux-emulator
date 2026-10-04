// DroidForge core – scrcpy-protocol touch injector implementation.
//
// Implements the scrcpy 4.x binary control protocol. All multi-byte integers
// are big-endian (network byte order), as required by the scrcpy server.
//
// Protocol reference:
//   https://github.com/Genymobile/scrcpy/blob/master/DEVELOP.md
//   https://github.com/Genymobile/scrcpy/blob/master/app/src/control_msg.h

#include "droidforge/touch_injector.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <thread>

namespace droidforge {

// ---------------------------------------------------------------------------
// Big-endian write helpers
// ---------------------------------------------------------------------------
/*static*/ void TouchInjector::putU16(std::vector<uint8_t>& buf, uint16_t v) {
    buf.push_back(static_cast<uint8_t>(v >> 8));
    buf.push_back(static_cast<uint8_t>(v));
}
/*static*/ void TouchInjector::putU32(std::vector<uint8_t>& buf, uint32_t v) {
    buf.push_back(static_cast<uint8_t>(v >> 24));
    buf.push_back(static_cast<uint8_t>(v >> 16));
    buf.push_back(static_cast<uint8_t>(v >>  8));
    buf.push_back(static_cast<uint8_t>(v));
}
/*static*/ void TouchInjector::putU64(std::vector<uint8_t>& buf, uint64_t v) {
    putU32(buf, static_cast<uint32_t>(v >> 32));
    putU32(buf, static_cast<uint32_t>(v));
}

// ---------------------------------------------------------------------------
// Connect / disconnect
// ---------------------------------------------------------------------------
TouchInjector::TouchInjector() = default;

TouchInjector::~TouchInjector() {
    disconnect();
}

void TouchInjector::connect(const std::string& host, uint16_t port) {
    disconnect();

    fd_ = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd_ < 0)
        throw std::runtime_error(std::string("socket(): ") + ::strerror(errno));

    // Disable Nagle: touch events must be delivered immediately, not batched.
    int one = 1;
    ::setsockopt(fd_, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(port);
    if (::inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
        ::close(fd_); fd_ = -1;
        throw std::runtime_error("invalid host address: " + host);
    }
    if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        int e = errno;
        ::close(fd_); fd_ = -1;
        throw std::runtime_error(std::string("connect(): ") + ::strerror(e));
    }
}

void TouchInjector::disconnect() {
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
}

// ---------------------------------------------------------------------------
// Raw send
// ---------------------------------------------------------------------------
void TouchInjector::writeAll(const uint8_t* buf, size_t len) {
    if (fd_ < 0) throw std::runtime_error("TouchInjector: not connected");
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = ::write(fd_, buf + sent, len - sent);
        if (n < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error(std::string("write(): ") + ::strerror(errno));
        }
        sent += static_cast<size_t>(n);
    }
}

void TouchInjector::sendRaw(const std::vector<uint8_t>& msg) {
    writeAll(msg.data(), msg.size());
}

// ---------------------------------------------------------------------------
// Touch event encoding (scrcpy 4.x INJECT_TOUCH_EVENT = 0x02, 28 bytes)
//
// Byte layout:
//   [0]      type           = 0x02
//   [1]      action         = 0 DOWN | 1 UP | 2 MOVE
//   [2..9]   pointerId      int64 big-endian
//   [10..13] x              int32 big-endian
//   [14..17] y              int32 big-endian
//   [18..19] screenWidth    uint16 big-endian
//   [20..21] screenHeight   uint16 big-endian
//   [22..23] pressure       uint16 big-endian (0–0xFFFF, full=0xFFFF)
//   [24..27] actionButton   uint32 (0 for touch)
//   [28..31] buttons        uint32 (0 for touch)
// ---------------------------------------------------------------------------
std::vector<uint8_t> TouchInjector::buildTouchMessage(
        TouchAction action,
        int64_t pointer_id,
        int32_t x, int32_t y,
        int32_t screen_w, int32_t screen_h,
        float pressure) const
{
    std::vector<uint8_t> msg;
    msg.reserve(32);
    msg.push_back(static_cast<uint8_t>(ScrcpyMsgType::InjectTouch));
    msg.push_back(static_cast<uint8_t>(action));
    putU64(msg, static_cast<uint64_t>(pointer_id));
    putU32(msg, static_cast<uint32_t>(x));
    putU32(msg, static_cast<uint32_t>(y));
    putU16(msg, static_cast<uint16_t>(screen_w));
    putU16(msg, static_cast<uint16_t>(screen_h));
    // pressure in [0, 0xFFFF]
    uint16_t pres = static_cast<uint16_t>(pressure * 0xFFFFu);
    putU16(msg, pres);
    putU32(msg, 0);  // actionButton (0 = touch)
    putU32(msg, 0);  // buttons      (0 = touch)
    return msg;
}

// ---------------------------------------------------------------------------
// Keyboard event encoding (scrcpy 4.x INJECT_KEYCODE = 0x00, 14 bytes)
//
//   [0]     type        = 0x00
//   [1]     action      = 0 DOWN | 1 UP
//   [2..5]  keycode     int32 big-endian
//   [6..9]  repeat      int32 big-endian
//   [10..13] metaState  int32 big-endian
// ---------------------------------------------------------------------------
std::vector<uint8_t> TouchInjector::buildKeyMessage(
        KeyAction action,
        int32_t keycode,
        int32_t repeat,
        int32_t meta_state) const
{
    std::vector<uint8_t> msg;
    msg.reserve(14);
    msg.push_back(static_cast<uint8_t>(ScrcpyMsgType::InjectKeycode));
    msg.push_back(static_cast<uint8_t>(action));
    putU32(msg, static_cast<uint32_t>(keycode));
    putU32(msg, static_cast<uint32_t>(repeat));
    putU32(msg, static_cast<uint32_t>(meta_state));
    return msg;
}

// ---------------------------------------------------------------------------
// Public touch API
// ---------------------------------------------------------------------------
void TouchInjector::touchDown(int64_t pointer_id,
                               int32_t x, int32_t y,
                               int32_t screen_w, int32_t screen_h,
                               float pressure)
{
    sendRaw(buildTouchMessage(TouchAction::Down, pointer_id,
                              x, y, screen_w, screen_h, pressure));
}

void TouchInjector::touchMove(int64_t pointer_id,
                               int32_t x, int32_t y,
                               int32_t screen_w, int32_t screen_h,
                               float pressure)
{
    sendRaw(buildTouchMessage(TouchAction::Move, pointer_id,
                              x, y, screen_w, screen_h, pressure));
}

void TouchInjector::touchUp(int64_t pointer_id,
                             int32_t x, int32_t y,
                             int32_t screen_w, int32_t screen_h)
{
    sendRaw(buildTouchMessage(TouchAction::Up, pointer_id,
                              x, y, screen_w, screen_h, 0.0f));
}

void TouchInjector::tap(int32_t x, int32_t y,
                         int32_t screen_w, int32_t screen_h,
                         int duration_ms)
{
    touchDown(0, x, y, screen_w, screen_h);
    if (duration_ms > 0)
        std::this_thread::sleep_for(std::chrono::milliseconds(duration_ms));
    touchUp(0, x, y, screen_w, screen_h);
}

void TouchInjector::swipe(int32_t x0, int32_t y0,
                           int32_t x1, int32_t y1,
                           int32_t screen_w, int32_t screen_h,
                           int duration_ms, int steps)
{
    if (steps < 1) steps = 1;
    int delay_per_step = duration_ms / steps;

    touchDown(0, x0, y0, screen_w, screen_h);
    for (int i = 1; i < steps; ++i) {
        int x = x0 + (x1 - x0) * i / steps;
        int y = y0 + (y1 - y0) * i / steps;
        if (delay_per_step > 0)
            std::this_thread::sleep_for(std::chrono::milliseconds(delay_per_step));
        touchMove(0, x, y, screen_w, screen_h);
    }
    if (delay_per_step > 0)
        std::this_thread::sleep_for(std::chrono::milliseconds(delay_per_step));
    touchUp(0, x1, y1, screen_w, screen_h);
}

// ---------------------------------------------------------------------------
// Public keyboard API
// ---------------------------------------------------------------------------
void TouchInjector::keyDown(int32_t keycode, int32_t meta_state) {
    sendRaw(buildKeyMessage(KeyAction::Down, keycode, 0, meta_state));
}

void TouchInjector::keyUp(int32_t keycode, int32_t meta_state) {
    sendRaw(buildKeyMessage(KeyAction::Up, keycode, 0, meta_state));
}

void TouchInjector::keyTap(int32_t keycode, int32_t meta_state) {
    keyDown(keycode, meta_state);
    keyUp(keycode, meta_state);
}

// ---------------------------------------------------------------------------
// Text injection (scrcpy INJECT_TEXT = 0x01)
// Format:
//   [0]      type    = 0x01
//   [1..4]   length  uint32 big-endian (byte count of UTF-8 payload)
//   [5..]    UTF-8 text
// ---------------------------------------------------------------------------
void TouchInjector::injectText(const std::string& text) {
    std::vector<uint8_t> msg;
    msg.reserve(5 + text.size());
    msg.push_back(static_cast<uint8_t>(ScrcpyMsgType::InjectText));
    putU32(msg, static_cast<uint32_t>(text.size()));
    for (unsigned char c : text) msg.push_back(c);
    sendRaw(msg);
}

} // namespace droidforge
