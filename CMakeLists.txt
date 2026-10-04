cmake_minimum_required(VERSION 3.20)

project(DroidForge
    VERSION 0.1.0
    DESCRIPTION "Open-source hardware-accelerated Android x86_64 gaming emulator for Linux (QEMU+KVM)"
    LANGUAGES CXX)

# ---------------------------------------------------------------------------
# Rename in one place: the AUR package name, .desktop id, and all user-facing
# strings read PROJECT_NAME / DROIDFORGE_DISPLAY_NAME.
# ---------------------------------------------------------------------------
set(DROIDFORGE_DISPLAY_NAME "DroidForge")
add_compile_definitions(DROIDFORGE_DISPLAY_NAME="${DROIDFORGE_DISPLAY_NAME}")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()

add_compile_options(-Wall -Wextra)

# ---------------------------------------------------------------------------
# Core: UI-independent library (instance manager, QEMU cmd builder,
# QMP client, keymap engine, ADB wrapper). Unit tested. No Qt dependency.
# ---------------------------------------------------------------------------
find_package(nlohmann_json 3.10 QUIET)
if(NOT nlohmann_json_FOUND)
    message(FATAL_ERROR "nlohmann-json not found (sudo pacman -S nlohmann-json)")
endif()

add_subdirectory(core)

# ---------------------------------------------------------------------------
# Tests (gtest + gmock, bundled in Arch's 'gtest' package)
# ---------------------------------------------------------------------------
enable_testing()
add_subdirectory(tests)

# ---------------------------------------------------------------------------
# App: Qt 6 UI (only built if Qt is available; core + tests always build)
# ---------------------------------------------------------------------------
find_package(Qt6 COMPONENTS Widgets QUIET)
if(TARGET Qt6::Widgets)
    add_subdirectory(app)
    message(STATUS "DroidForge: Qt6 Widgets found -> building app/")
else()
    message(STATUS "DroidForge: Qt6 Widgets not found -> skipping app/ (core + tests still build)")
endif()
