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
# Zip extraction for cores that can't open archives themselves (roms/validate.prepare_content).
pkg_check_modules(LIBARCHIVE REQUIRED IMPORTED_TARGET libarchive)

add_library(libretro_api INTERFACE)
target_include_directories(libretro_api INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/libretro)

add_library(multitap_common INTERFACE)
target_include_directories(multitap_common INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/common)
target_link_libraries(multitap_common INTERFACE Threads::Threads)

add_executable(multitap-worker
  worker/main.cpp
  worker/core.cpp
  worker/content.cpp
  worker/control.cpp)
target_compile_options(multitap-worker PRIVATE -Wall -Wextra)
target_link_libraries(multitap-worker PRIVATE
  libretro_api multitap_common nlohmann_json::nlohmann_json PkgConfig::LIBARCHIVE ${CMAKE_DL_LIBS})
install(TARGETS multitap-worker RUNTIME DESTINATION bin)

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
  add_test(NAME shm COMMAND multitap-native-tests)
endif()
