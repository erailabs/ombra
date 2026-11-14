# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
# libFuzzer targets for the parsers of untrusted bytes that build on the CPU. Added from the root
# CMakeLists.txt only with -DOMBRA_FUZZ=ON (clang). Run with `make fuzz`; see fuzz/README.md.
# Every target links ombra_storage / ombra_server_admin, which the root builds with the same
# sanitizer flags (-fsanitize=fuzzer-no-link,address,undefined).

set(OMBRA_FUZZ_TARGETS envelope seeded segment blob wal recovery api_keys http auth_header)

add_custom_target(ombra_fuzzers)
foreach(t ${OMBRA_FUZZ_TARGETS})
  add_executable(ombra_fuzz_${t} fuzz_${t}.cpp)
  target_include_directories(ombra_fuzz_${t} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  target_link_libraries(ombra_fuzz_${t} PRIVATE ombra_storage ombra_server_admin)
  target_link_options(ombra_fuzz_${t} PRIVATE -fsanitize=fuzzer)
  add_dependencies(ombra_fuzzers ombra_fuzz_${t})
endforeach()

# Writes the seed corpora (the committed fuzz/corpus is its output).
add_executable(ombra_fuzz_seedgen seedgen.cpp)
target_include_directories(ombra_fuzz_seedgen PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(ombra_fuzz_seedgen PRIVATE ombra_storage ombra_server_admin)
add_dependencies(ombra_fuzzers ombra_fuzz_seedgen)

# gRPC request messages: needs the generated protobuf code of server/grpc.
if(TARGET ombra_proto AND TARGET ombra_server_policy)
  add_executable(ombra_fuzz_proto fuzz_proto.cpp)
  target_include_directories(ombra_fuzz_proto PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  target_link_libraries(ombra_fuzz_proto PRIVATE ombra_proto ombra_server_policy)
  target_link_options(ombra_fuzz_proto PRIVATE -fsanitize=fuzzer)
  add_dependencies(ombra_fuzzers ombra_fuzz_proto)
endif()

# OpenFHE context and key deserialization behind the preflight (CPU-only engine of server/search).
# openfhe_raw is a diagnostic without the preflight; it is built but not run by `make fuzz`.
if(TARGET ombra_search AND NOT OMBRA_ENABLE_CUDA)
  find_package(OpenFHE 1.6.1 EXACT QUIET)
  string(REPLACE "-Werror" "" _ofhe_flags "${OpenFHE_CXX_FLAGS}")
  separate_arguments(_ofhe_flags UNIX_COMMAND "${_ofhe_flags}")
  # OpenFHE is built with GCC's OpenMP; clang's OpenMP runtime (libomp) is not ABI compatible with
  # it and may not be installed, so the inline OpenFHE templates run serially here.
  list(FILTER _ofhe_flags EXCLUDE REGEX "^-fopenmp$")
  foreach(t keys openfhe_raw)
    add_executable(ombra_fuzz_${t} fuzz_${t}.cpp)
    target_include_directories(ombra_fuzz_${t} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}
                               ${CMAKE_SOURCE_DIR}/server/search/src)
    target_compile_options(ombra_fuzz_${t} PRIVATE ${_ofhe_flags})
    target_link_libraries(ombra_fuzz_${t} PRIVATE ombra_search)
    # The link line of OpenFHE carries -fopenmp; name GCC's runtime so clang does not ask for libomp.
    target_link_options(ombra_fuzz_${t} PRIVATE -fsanitize=fuzzer -fopenmp=libgomp)
    add_dependencies(ombra_fuzzers ombra_fuzz_${t})
  endforeach()
endif()
