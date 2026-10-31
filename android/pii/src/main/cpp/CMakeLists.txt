# JNI library of ai.newmean:pii (libnewmean_pii_jni) over the shared native core in native/core (compiled from there, not copied).
# Android: built per ABI by the Android Gradle Plugin (externalNativeBuild). Host (macOS, Linux): built by the Gradle task
# buildHostJni so that JVM unit tests can run the real model without an emulator.
cmake_minimum_required(VERSION 3.22)
project(newmean_pii_jni LANGUAGES C)

set(PII_CORE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../native/core" CACHE PATH "native/core of the newmean-pii repository")
set(PII_BUILD_SHARED OFF)
set(PII_BUILD_TESTS OFF)
add_subdirectory(${PII_CORE_DIR} pii_core)

if(ANDROID_ABI STREQUAL "armeabi-v7a")
  # 32-bit NEON flushes denormals to zero, so the vector kernel could differ from the other builds in the last bits. The
  # scalar kernel uses VFP (IEEE) and LLVM does not vectorize float math for ARMv7 without -ffast-math.
  target_compile_definitions(pii_static PRIVATE PII_SCALAR)
endif()

add_library(newmean_pii_jni SHARED pii_jni.c)
target_link_libraries(newmean_pii_jni PRIVATE newmean::pii)
set_target_properties(newmean_pii_jni PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_VISIBILITY_PRESET hidden)
if(NOT MSVC)
  target_compile_options(newmean_pii_jni PRIVATE -Wall -Wextra -Wshadow -Wstrict-prototypes)
endif()
if(ANDROID OR CMAKE_SYSTEM_NAME STREQUAL "Linux")
  # export only the JNI entry points, not the core's pii_* functions
  target_link_options(newmean_pii_jni PRIVATE -Wl,--exclude-libs,ALL -Wl,--gc-sections)
endif()

if(NOT ANDROID)
  if(PII_JNI_INCLUDE)  # passed by buildHostJni: a JDK include/ folder or one holding the NDK jni.h
    file(GLOB PII_JNI_MD LIST_DIRECTORIES true "${PII_JNI_INCLUDE}/*")
    target_include_directories(newmean_pii_jni PRIVATE ${PII_JNI_INCLUDE} ${PII_JNI_MD})
  else()
    find_package(JNI REQUIRED)
    target_include_directories(newmean_pii_jni PRIVATE ${JNI_INCLUDE_DIRS})
  endif()
endif()
