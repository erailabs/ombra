# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
# The router: placement of clusters on nodes (plain C++, CPU tests) and the
# ombra_router executable (CPU only).
add_library(ombra_router_placement STATIC src/placement.cpp)
target_include_directories(ombra_router_placement PUBLIC include)
target_link_libraries(ombra_router_placement PUBLIC ombra_storage)

add_executable(router_placement_test tests/placement_test.cpp)
target_link_libraries(router_placement_test PRIVATE ombra_router_placement)
add_test(NAME router_placement COMMAND router_placement_test)
# The ombra_router executable is defined in server/grpc/CMakeLists.txt, next to ombra_proto.
