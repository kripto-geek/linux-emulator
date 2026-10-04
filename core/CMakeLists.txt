# DroidForge core - UI-independent library.
add_library(droidforge_core STATIC
    src/qemu_command_builder.cpp
    src/qmp_client.cpp
    src/touch_injector.cpp
    src/adb_client.cpp
)

target_include_directories(droidforge_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(droidforge_core PUBLIC cxx_std_20)
target_link_libraries(droidforge_core PUBLIC nlohmann_json::nlohmann_json)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_link_libraries(droidforge_core PRIVATE rt)
endif()
