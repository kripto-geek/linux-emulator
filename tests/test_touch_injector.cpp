// Unit tests for TouchInjector – protocol encoding only.
//
// These tests do NOT connect to a real device; they verify that the binary
// messages we encode match the scrcpy 4.x wire format exactly.
// Reference: https://github.com/Genymobile/scrcpy/blob/master/app/src/control_msg.h

#include "droidforge/touch_injector.hpp"

#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

using droidforge::ScrcpyMsgType;
using droidforge::TouchAction;
using droidforge::KeyAction;
using droidforge::TouchInjector;

namespace {

// Read big-endian values from a byte buffer (same as the scrcpy wire format).
uint16_t readU16(const std::vector<uint8_t>& buf, size_t offset) {
    return static_cast<uint16_t>((buf[offset] << 8) | buf[offset + 1]);
}
uint32_t readU32(const std::vector<uint8_t>& buf, size_t offset) {
    return (static_cast<uint32_t>(buf[offset    ]) << 24) |
           (static_cast<uint32_t>(buf[offset + 1]) << 16) |
           (static_cast<uint32_t>(buf[offset + 2]) <<  8) |
            static_cast<uint32_t>(buf[offset + 3]);
}
uint64_t readU64(const std::vector<uint8_t>& buf, size_t offset) {
    return (static_cast<uint64_t>(readU32(buf, offset)) << 32) |
            static_cast<uint64_t>(readU32(buf, offset + 4));
}

// Use reflection trick: expose buildTouchMessage / buildKeyMessage via a
// subclass so we can test them without opening a socket.
class TestableInjector : public TouchInjector {
public:
    using TouchInjector::buildTouchMessage;
    using TouchInjector::buildKeyMessage;
};

} // namespace

// ---------------------------------------------------------------------------
// Touch message layout tests
// ---------------------------------------------------------------------------

TEST(TouchInjector, TouchDownMessageSize) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Down, 0, 100, 200, 1280, 720, 1.0f);
    // scrcpy INJECT_TOUCH_EVENT: 1 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4 + 4 = 32 bytes
    EXPECT_EQ(msg.size(), 32u);
}

TEST(TouchInjector, TouchDownMessageTypeByte) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Down, 0, 100, 200, 1280, 720, 1.0f);
    EXPECT_EQ(msg[0], static_cast<uint8_t>(ScrcpyMsgType::InjectTouch));
}

TEST(TouchInjector, TouchDownActionByte) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Down, 0, 100, 200, 1280, 720, 1.0f);
    EXPECT_EQ(msg[1], static_cast<uint8_t>(TouchAction::Down));
}

TEST(TouchInjector, TouchUpActionByte) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Up, 0, 100, 200, 1280, 720, 0.0f);
    EXPECT_EQ(msg[1], static_cast<uint8_t>(TouchAction::Up));
}

TEST(TouchInjector, TouchMoveActionByte) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Move, 0, 100, 200, 1280, 720, 0.5f);
    EXPECT_EQ(msg[1], static_cast<uint8_t>(TouchAction::Move));
}

TEST(TouchInjector, TouchPointerIdEncoding) {
    TestableInjector inj;
    // pointerId = 7 (a multi-touch finger slot)
    auto msg = inj.buildTouchMessage(TouchAction::Down, 7, 0, 0, 1280, 720, 1.0f);
    uint64_t pid = readU64(msg, 2);
    EXPECT_EQ(pid, 7u);
}

TEST(TouchInjector, TouchCoordinateEncoding) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Move, 0, 640, 360, 1280, 720, 0.8f);
    // x at offset 10, y at offset 14
    EXPECT_EQ(readU32(msg, 10), 640u);
    EXPECT_EQ(readU32(msg, 14), 360u);
}

TEST(TouchInjector, TouchScreenDimensionsEncoding) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Down, 0, 0, 0, 1920, 1080, 1.0f);
    EXPECT_EQ(readU16(msg, 18), 1920u);
    EXPECT_EQ(readU16(msg, 20), 1080u);
}

TEST(TouchInjector, TouchFullPressureIsMaxUint16) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Down, 0, 0, 0, 1280, 720, 1.0f);
    EXPECT_EQ(readU16(msg, 22), 0xFFFFu);
}

TEST(TouchInjector, TouchZeroPressureIsZero) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Up, 0, 0, 0, 1280, 720, 0.0f);
    EXPECT_EQ(readU16(msg, 22), 0u);
}

TEST(TouchInjector, TouchActionButtonsAreZero) {
    TestableInjector inj;
    auto msg = inj.buildTouchMessage(TouchAction::Down, 0, 0, 0, 1280, 720, 1.0f);
    EXPECT_EQ(readU32(msg, 24), 0u);  // actionButton
    EXPECT_EQ(readU32(msg, 28), 0u);  // buttons
}

// ---------------------------------------------------------------------------
// Keyboard message layout tests
// ---------------------------------------------------------------------------

TEST(TouchInjector, KeyMessageSize) {
    TestableInjector inj;
    auto msg = inj.buildKeyMessage(KeyAction::Down, 0x001D /*KEYCODE_A*/, 0, 0);
    // 1 + 1 + 4 + 4 + 4 = 14 bytes
    EXPECT_EQ(msg.size(), 14u);
}

TEST(TouchInjector, KeyMessageTypeByte) {
    TestableInjector inj;
    auto msg = inj.buildKeyMessage(KeyAction::Down, 0, 0, 0);
    EXPECT_EQ(msg[0], static_cast<uint8_t>(ScrcpyMsgType::InjectKeycode));
}

TEST(TouchInjector, KeyDownActionByte) {
    TestableInjector inj;
    auto msg = inj.buildKeyMessage(KeyAction::Down, 0, 0, 0);
    EXPECT_EQ(msg[1], static_cast<uint8_t>(KeyAction::Down));
}

TEST(TouchInjector, KeyUpActionByte) {
    TestableInjector inj;
    auto msg = inj.buildKeyMessage(KeyAction::Up, 0, 0, 0);
    EXPECT_EQ(msg[1], static_cast<uint8_t>(KeyAction::Up));
}

TEST(TouchInjector, KeycodeEncoding) {
    TestableInjector inj;
    // KEYCODE_BACK = 4
    auto msg = inj.buildKeyMessage(KeyAction::Down, 4, 0, 0);
    EXPECT_EQ(readU32(msg, 2), 4u);
}

TEST(TouchInjector, KeyRepeatEncoding) {
    TestableInjector inj;
    auto msg = inj.buildKeyMessage(KeyAction::Down, 0, 3, 0);
    EXPECT_EQ(readU32(msg, 6), 3u);
}

TEST(TouchInjector, KeyMetaStateEncoding) {
    TestableInjector inj;
    // META_SHIFT_ON = 0x00000001
    auto msg = inj.buildKeyMessage(KeyAction::Down, 0, 0, 0x00000001);
    EXPECT_EQ(readU32(msg, 10), 0x00000001u);
}

// ---------------------------------------------------------------------------
// Max pointer ID boundary
// ---------------------------------------------------------------------------

TEST(TouchInjector, MaxPointerIdBoundary) {
    TestableInjector inj;
    // Android supports up to 10 simultaneous touch points (IDs 0–9).
    // Encoding must handle all IDs without overflow.
    for (int64_t id = 0; id < droidforge::kMaxTouches; ++id) {
        auto msg = inj.buildTouchMessage(TouchAction::Down, id, 0, 0, 1280, 720, 1.0f);
        EXPECT_EQ(readU64(msg, 2), static_cast<uint64_t>(id));
    }
}
