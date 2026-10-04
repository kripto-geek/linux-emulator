// Unit tests for the droidforge JSON parser / dumper (shared by the QMP
// client and the keymap engine).

#include "droidforge/qmp_client.hpp"

#include <gtest/gtest.h>

#include <stdexcept>
#include <string>

using droidforge::jsonParse;
using droidforge::jsonDump;
using droidforge::jsonToObj;
using droidforge::QmpValue;

TEST(Json, ParsesNull) {
    auto v = jsonParse("null");
    EXPECT_EQ(v.type, QmpValue::Type::Null);
}

TEST(Json, ParsesBooleans) {
    EXPECT_TRUE(jsonParse("true").asBool());
    EXPECT_FALSE(jsonParse("false").asBool());
}

TEST(Json, ParsesNumbers) {
    EXPECT_DOUBLE_EQ(jsonParse("42").number, 42.0);
    EXPECT_DOUBLE_EQ(jsonParse("-3.14").number, -3.14);
    EXPECT_DOUBLE_EQ(jsonParse("1e3").number, 1000.0);
}

TEST(Json, ParsesStrings) {
    EXPECT_EQ(jsonParse("\"hello\"").string, "hello");
    EXPECT_EQ(jsonParse("\"a\\n b\"").string, "a\n b");
    EXPECT_EQ(jsonParse("\"\\u0041\"").string, "A");
}

TEST(Json, ParsesObject) {
    auto v = jsonParse(R"({"a":1,"b":"two","c":[true,null]})");
    EXPECT_EQ(v.type, QmpValue::Type::Object);
    EXPECT_DOUBLE_EQ(v.find("a")->number, 1.0);
    EXPECT_EQ(v.find("b")->string, "two");
    EXPECT_EQ(v.find("c")->array.size(), 2u);
    EXPECT_TRUE(v.find("c")->array[0].boolean);
    EXPECT_EQ(v.find("c")->array[1].type, QmpValue::Type::Null);
}

TEST(Json, ParsesNestedArray) {
    auto v = jsonParse("[1,2,[3,4]]");
    EXPECT_EQ(v.array.size(), 3u);
    EXPECT_DOUBLE_EQ(v.array[2].array[1].number, 4.0);
}

TEST(Json, RejectsTrailingJunk) {
    EXPECT_THROW(jsonParse("{\"a\":1} junk"), std::runtime_error);
}

TEST(Json, RejectsMalformed) {
    EXPECT_THROW(jsonParse("{"), std::runtime_error);
    EXPECT_THROW(jsonParse("[1,]"), std::runtime_error);
    EXPECT_THROW(jsonParse("\"\\x\""), std::runtime_error);
}

TEST(Json, DumpAndParseRoundtrip) {
    auto original = jsonParse(R"({"name":"pubg1","ram":4096,"paused":true,"tags":["a","b"]})");
    std::string dumped = jsonDump(original);
    auto reparsed = jsonParse(dumped);
    EXPECT_EQ(reparsed.find("name")->string, "pubg1");
    EXPECT_DOUBLE_EQ(reparsed.find("ram")->number, 4096.0);
    EXPECT_TRUE(reparsed.find("paused")->boolean);
    EXPECT_EQ(reparsed.find("tags")->array.size(), 2u);
}

TEST(Json, DumpEscapesSpecialChars) {
    QmpValue v;
    v.type = QmpValue::Type::String;
    v.string = "he said \"hi\"\n\\ line";
    std::string dumped = jsonDump(v);
    EXPECT_EQ(dumped, "\"he said \\\"hi\\\"\\n\\\\ line\"");
}

TEST(Json, DumpIntegersHaveNoTrailingZero) {
    QmpValue v;
    v.type = QmpValue::Type::Number;
    v.number = 4096.0;
    EXPECT_EQ(jsonDump(v), "4096");
}

TEST(Json, jsonToObjThrowsOnNonObject) {
    EXPECT_THROW(jsonToObj(jsonParse("42")), std::runtime_error);
}

// Simulate a full QMP "execute" -> "return" roundtrip through the same
// parse/dump code the client uses, to lock down the wire format.
TEST(Json, QmpExecuteFrameRoundtrip) {
    // Build the frame a client would send for: cont
    QmpValue cmd;
    cmd.type = QmpValue::Type::Object;
    QmpValue exec; exec.type = QmpValue::Type::String; exec.string = "cont";
    cmd.object["execute"] = exec;

    std::string frame = jsonDump(cmd);
    // Must be a single line with no trailing newline (client adds it).
    EXPECT_EQ(frame, "{\"execute\":\"cont\"}");
}
