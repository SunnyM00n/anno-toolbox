# The game look's Direct3D 12 module: NVRHI (static, Direct3D 12 only) under a nanobind extension built for the
# stable ABI, so one .pyd serves Python 3.12 and later. scripts/build_native.py fetches the pinned sources and runs
# this; README.md has the details.
cmake_minimum_required(VERSION 3.24)
project(nativegpu LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")  # the C++ runtime inside the .pyd: nothing to install beside it
set(CMAKE_POLICY_DEFAULT_CMP0091 NEW)  # and inside NVRHI's libraries, whose CMake file predates the setting

set(NVRHI_DIR "" CACHE PATH "NVRHI's source at its pinned commit")
set(DIRECTX_HEADERS_DIR "" CACHE PATH "the DirectX headers' source at their pinned commit")
set(MODULE_VERSION "0" CACHE STRING "the module's version, from deps.json")
set(NVRHI_REVISION "unknown" CACHE STRING "the NVRHI commit it was built from")
if(NOT NVRHI_DIR OR NOT DIRECTX_HEADERS_DIR)
  message(FATAL_ERROR "NVRHI_DIR and DIRECTX_HEADERS_DIR are not set: run scripts/build_native.py")
endif()

find_package(Python 3.12 COMPONENTS Interpreter Development.Module Development.SABIModule REQUIRED)
execute_process(COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
                OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)

add_subdirectory("${DIRECTX_HEADERS_DIR}" directx-headers EXCLUDE_FROM_ALL)
set(NVRHI_FETCH_DIRECTX_HEADERS OFF CACHE BOOL "" FORCE)
set(NVRHI_WITH_VULKAN OFF CACHE BOOL "" FORCE)
set(NVRHI_WITH_DX11 OFF CACHE BOOL "" FORCE)
set(NVRHI_WITH_DX12 ON CACHE BOOL "" FORCE)
set(NVRHI_WITH_VALIDATION ON CACHE BOOL "" FORCE)
set(NVRHI_WITH_NVAPI OFF CACHE BOOL "" FORCE)
set(NVRHI_WITH_AFTERMATH OFF CACHE BOOL "" FORCE)
set(NVRHI_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(NVRHI_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory("${NVRHI_DIR}" nvrhi EXCLUDE_FROM_ALL)

nanobind_add_module(_nativegpu STABLE_ABI NB_STATIC LTO
  src/module.cpp src/device.cpp src/resources.cpp src/pipelines.cpp src/commands.cpp src/interop.cpp)
target_include_directories(_nativegpu PRIVATE "${NVRHI_DIR}/src")  # NVRHI's own D3D12 types (README)
target_link_libraries(_nativegpu PRIVATE nvrhi nvrhi_d3d12 DirectX-Headers DirectX-Guids)
target_compile_definitions(_nativegpu PRIVATE MODULE_VERSION="${MODULE_VERSION}" NVRHI_REVISION="${NVRHI_REVISION}"
                                              WIN32_LEAN_AND_MEAN)
if(MSVC)
  target_compile_options(_nativegpu PRIVATE /W3 /permissive- /utf-8)
endif()

# the tests of the layer that need no GPU (formats, names, the patch of the input layout's semantics)
add_executable(nativegpu_test EXCLUDE_FROM_ALL tests/layer_test.cpp src/device.cpp src/resources.cpp src/pipelines.cpp src/interop.cpp
  src/commands.cpp)
target_include_directories(nativegpu_test PRIVATE src "${NVRHI_DIR}/src")
target_link_libraries(nativegpu_test PRIVATE nvrhi nvrhi_d3d12 DirectX-Headers DirectX-Guids)
target_compile_definitions(nativegpu_test PRIVATE WIN32_LEAN_AND_MEAN)
