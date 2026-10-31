# newmean-pii native core: static and shared library (used by the Python, Android, iOS and Flutter packages), plus the test
# CLI when built on its own.
#
#   cmake -S native/core -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build
#   options: PII_FMA (fused multiply-add kernel), PII_BUILD_SHARED, PII_BUILD_TESTS, PII_SANITIZE (ASan + UBSan)
#
# Other projects: add_subdirectory(native/core) and link newmean::pii (static) or newmean::pii_shared.
cmake_minimum_required(VERSION 3.16)
project(newmean_pii VERSION 1.0.0 LANGUAGES C)

option(PII_FMA "Fused multiply-add in the matrix kernel (like the relaxed-SIMD wasm build; x86-64 gets -mfma)" OFF)
option(PII_BUILD_SHARED "Build the shared library too" ON)
option(PII_BUILD_TESTS "Build the test CLI (pii_cli)" ${PROJECT_IS_TOP_LEVEL})
option(PII_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(PII_SOURCES src/api.c src/engine.c src/onnx.c src/tokenizer.c src/util.c src/sha256.c src/models.c)

# Results must not depend on the compiler's contraction of a * b + c: -ffp-contract=off (Clang, GCC), /fp:precise (MSVC,
# which has no vector extensions and uses the scalar path of src/simd.h). -fno-math-errno: sqrt without a libm call.
if(MSVC)
  set(PII_FLAGS /W4 /fp:precise /O2)
else()
  set(PII_FLAGS -Wall -Wextra -Wshadow -Wstrict-prototypes -ffp-contract=off -fno-math-errno $<$<CONFIG:Release>:-O3>)
endif()
set(PII_DEFS)
if(PII_FMA)
  list(APPEND PII_DEFS PII_FMA)
  if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64|amd64" AND NOT MSVC)
    list(APPEND PII_FLAGS -mfma)
  endif()
endif()
if(PII_SANITIZE AND NOT MSVC)
  list(APPEND PII_FLAGS -fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer -g)
  add_link_options(-fsanitize=address,undefined)
endif()

function(pii_setup target)
  set_target_properties(${target} PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF
                                             C_VISIBILITY_PRESET hidden POSITION_INDEPENDENT_CODE ON OUTPUT_NAME pii)
  target_compile_options(${target} PRIVATE ${PII_FLAGS})
  target_compile_definitions(${target} PRIVATE ${PII_DEFS})
  target_include_directories(${target} PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}> $<INSTALL_INTERFACE:include>)
endfunction()

add_library(pii_static STATIC ${PII_SOURCES})
pii_setup(pii_static)
add_library(newmean::pii ALIAS pii_static)
set(PII_INSTALL pii_static)

if(PII_BUILD_SHARED)
  add_library(pii_shared SHARED ${PII_SOURCES})
  pii_setup(pii_shared)
  target_compile_definitions(pii_shared PRIVATE PII_BUILD_SHARED INTERFACE PII_USE_SHARED)
  set_target_properties(pii_shared PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION ${PROJECT_VERSION_MAJOR})
  if(MSVC)  # pii.lib (import library) must not clash with the static pii.lib
    set_target_properties(pii_shared PROPERTIES ARCHIVE_OUTPUT_NAME pii_import)
  endif()
  add_library(newmean::pii_shared ALIAS pii_shared)
  list(APPEND PII_INSTALL pii_shared)
endif()

if(PII_BUILD_TESTS AND NOT MSVC)
  find_package(Threads REQUIRED)
  add_executable(pii_cli tests/pii_cli.c)
  set_target_properties(pii_cli PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
  target_compile_options(pii_cli PRIVATE ${PII_FLAGS})
  target_compile_definitions(pii_cli PRIVATE _POSIX_C_SOURCE=200809L)
  target_link_libraries(pii_cli PRIVATE pii_static Threads::Threads)
endif()

include(GNUInstallDirs)
install(TARGETS ${PII_INSTALL} ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR} LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES pii.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
