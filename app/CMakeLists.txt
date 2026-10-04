# DroidForge app - Qt 6 Widgets UI.
#
# Phase 1 ships a minimal bootstrap so the build stays green and we can smoke-
# test that Qt links. The real gaming shell (instance manager window, per-
# instance window with the QEMU video embedded, keymap overlay editor,
# one-click wizard) is built up in Phase 3.
#
# Qt6 is found at the top level; this file only consumes it.

qt6_standard_project_setup()

add_executable(droidforge_app
    main.cpp
)

target_link_libraries(droidforge_app
    PRIVATE
        Qt6::Widgets
        droidforge_core
)

target_include_directories(droidforge_app
    PRIVATE
        ${CMAKE_SOURCE_DIR}/core/include
)

set_target_properties(droidforge_app PROPERTIES
    OUTPUT_NAME "droidforge"
    WIN32_EXECUTABLE OFF
    MACOSX_BUNDLE OFF
)

# Install the .desktop + icon in Phase 4.
install(TARGETS droidforge_app RUNTIME DESTINATION bin)
