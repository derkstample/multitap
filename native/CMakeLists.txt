cmake_minimum_required(VERSION 3.24)
project(multitap_native LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(MULTITAP_BUILD_TESTS "Build the doctest unit tests (run with ctest)" ON)

include(FetchContent)
# Prefer installed packages; download only what the system doesn't have.
FetchContent_Declare(nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
  URL_HASH SHA256=d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d
  FIND_PACKAGE_ARGS 3.10)
FetchContent_MakeAvailable(nlohmann_json)

find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
# Zip extraction for cores that can't open archives themselves.
pkg_check_modules(LIBARCHIVE REQUIRED IMPORTED_TARGET libarchive)

add_library(libretro_api INTERFACE)
target_include_directories(libretro_api INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/libretro)

add_library(multitap_common STATIC common/control.cpp)
target_include_directories(multitap_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/common)
target_link_libraries(multitap_common PUBLIC Threads::Threads nlohmann_json::nlohmann_json)
# shm_open/shm_unlink live in librt on older glibc.
find_library(RT_LIBRARY rt)
if(RT_LIBRARY)
  target_link_libraries(multitap_common PUBLIC ${RT_LIBRARY})
endif()

add_executable(multitap-worker
  worker/main.cpp
  worker/core.cpp
  worker/content.cpp
  worker/hwrender.cpp)
target_compile_options(multitap-worker PRIVATE -Wall -Wextra)
target_link_libraries(multitap-worker PRIVATE
  libretro_api multitap_common PkgConfig::LIBARCHIVE ${CMAKE_DL_LIBS})
# OpenGL cores render into an offscreen EGL context. Without EGL they are refused.
pkg_check_modules(EGL IMPORTED_TARGET egl)
find_path(GLCOREARB_DIR GL/glcorearb.h)
if(EGL_FOUND AND GLCOREARB_DIR)
  target_compile_definitions(multitap-worker PRIVATE MULTITAP_HAVE_EGL)
  target_include_directories(multitap-worker PRIVATE ${GLCOREARB_DIR})
  target_link_libraries(multitap-worker PRIVATE PkgConfig::EGL)
else()
  message(WARNING "EGL development files not found: multitap-worker can't run OpenGL cores")
endif()
install(TARGETS multitap-worker RUNTIME DESTINATION bin)

# ---- multitap-session: the in-game window (SDL2 + OpenGL 3.3, Dear ImGui OSD) ------------
pkg_check_modules(SDL2 IMPORTED_TARGET sdl2>=2.0.18)
set(OpenGL_GL_PREFERENCE GLVND)
find_package(OpenGL)
if(SDL2_FOUND AND OPENGL_FOUND)
  FetchContent_Declare(imgui
    URL https://github.com/ocornut/imgui/archive/refs/tags/v1.91.9b.tar.gz
    URL_HASH SHA256=8e1bbc76c71d74fef2fb85db7e7ca8eba13d6a86623c54992b60162db554ffdb)
  FetchContent_MakeAvailable(imgui)
  add_library(imgui STATIC
    ${imgui_SOURCE_DIR}/imgui.cpp
    ${imgui_SOURCE_DIR}/imgui_draw.cpp
    ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_sdl2.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp)
  target_include_directories(imgui SYSTEM PUBLIC ${imgui_SOURCE_DIR} ${imgui_SOURCE_DIR}/backends)
  target_compile_definitions(imgui PUBLIC IMGUI_DISABLE_OBSOLETE_FUNCTIONS)
  target_link_libraries(imgui PUBLIC PkgConfig::SDL2)

  # librashader is optional at runtime: only its (MIT) headers are needed to build.
  add_library(librashader_api INTERFACE)
  target_include_directories(librashader_api SYSTEM INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/librashader)

  # Everything but the window, GL and audio: shared with the unit tests.
  add_library(session_core STATIC session/config.cpp session/players.cpp session/input.cpp)
  target_link_libraries(session_core PUBLIC multitap_common PkgConfig::SDL2)
  target_include_directories(session_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/session)

  add_executable(multitap-session
    session/main.cpp
    session/audio.cpp
    session/renderer.cpp
    session/shaders.cpp
    session/osd.cpp)
  target_compile_options(multitap-session PRIVATE -Wall -Wextra)
  target_link_libraries(multitap-session PRIVATE
    session_core imgui librashader_api PkgConfig::SDL2 OpenGL::GL ${CMAKE_DL_LIBS})
  install(TARGETS multitap-session RUNTIME DESTINATION bin)
else()
  message(WARNING "SDL2 or OpenGL development files not found: multitap-session is not built")
endif()

if(MULTITAP_BUILD_TESTS)
  FetchContent_Declare(doctest
    URL https://github.com/doctest/doctest/archive/refs/tags/v2.4.11.tar.gz
    URL_HASH SHA256=632ed2c05a7f53fa961381497bf8069093f0d6628c5f26286161fbd32a560186
    # Header-only: point at a folder without a CMakeLists.txt so the (outdated) project
    # file is never configured.
    SOURCE_SUBDIR doctest
    FIND_PACKAGE_ARGS 2.4)
  FetchContent_MakeAvailable(doctest)
  if(NOT TARGET doctest::doctest)
    add_library(doctest_headers INTERFACE)
    target_include_directories(doctest_headers INTERFACE ${doctest_SOURCE_DIR})
    add_library(doctest::doctest ALIAS doctest_headers)
  endif()
  enable_testing()
  add_executable(multitap-native-tests tests/test_shm.cpp)
  target_compile_options(multitap-native-tests PRIVATE -Wall -Wextra)
  target_link_libraries(multitap-native-tests PRIVATE multitap_common doctest::doctest)
  if(TARGET session_core)
    target_sources(multitap-native-tests PRIVATE tests/test_session_config.cpp)
    target_link_libraries(multitap-native-tests PRIVATE session_core)
  endif()
  add_test(NAME shm COMMAND multitap-native-tests)
endif()
