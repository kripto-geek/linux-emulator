# DroidForge core - unit tests.
#
# Uses gtest + gmock (Arch's 'gtest' package bundles gmock).

find_package(GTest REQUIRED)

add_executable(core_tests
    test_qemu_command_builder.cpp
    test_json.cpp
)

target_link_libraries(core_tests
    PRIVATE
        droidforge_core
        GTest::gtest
        GTest::gmock
        GTest::gtest_main
)

target_include_directories(core_tests
    PRIVATE
        ${CMAKE_SOURCE_DIR}/core/include
)

add_test(NAME core_tests COMMAND core_tests)
set_tests_properties(core_tests PROPERTIES
    TIMEOUT 60
)
