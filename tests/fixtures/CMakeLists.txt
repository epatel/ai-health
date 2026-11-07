cmake_minimum_required(VERSION 3.25)
project(Sample VERSION 1.2.3 LANGUAGES C CXX)

# Options and platform switches
option(SAMPLE_TESTS "Build tests" ON)
set(SOURCES src/main.c src/util.c)

if(WIN32 AND NOT MINGW)
  add_compile_definitions(SAMPLE_WINDOWS)
elseif(APPLE)
  set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0")
else()
  find_package(Threads REQUIRED)
endif()

#[[ A bracket comment
    spanning lines ]]
function(sample_add_library name)
  cmake_parse_arguments(ARG "SHARED;STATIC" "OUTPUT" "SOURCES" ${ARGN})
  if(ARG_SHARED)
    add_library(${name} SHARED ${ARG_SOURCES})
  elseif(ARG_STATIC OR NOT BUILD_SHARED_LIBS)
    add_library(${name} STATIC ${ARG_SOURCES})
  else()
    message(FATAL_ERROR "sample_add_library: pick SHARED or STATIC")
  endif()
  foreach(src IN LISTS ARG_SOURCES)
    if(src MATCHES "\\.cu$")
      set_source_files_properties(${src} PROPERTIES LANGUAGE CUDA)
    endif()
  endforeach()
  set(${name}_FOUND TRUE PARENT_SCOPE)
endfunction()

macro(sample_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra)
  endif()
endmacro()

sample_add_library(core SHARED SOURCES ${SOURCES})
sample_warnings(core)

foreach(tool IN ITEMS cli gui)
  add_executable(${tool} tools/${tool}.c)
  target_link_libraries(${tool} PRIVATE core)
endforeach()

set(i 0)
while(i LESS 3)
  math(EXPR i "${i} + 1")
endwhile()

block(SCOPE_FOR VARIABLES)
  set(LOCAL_ONLY yes)
endblock()

if(SAMPLE_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
