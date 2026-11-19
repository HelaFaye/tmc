cmake_minimum_required(VERSION 3.15)
project(FxCG50RamCheck C)

include(GenerateG3A)
find_package(Gint 2.11 REQUIRED)

get_filename_component(TMC_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

add_executable(ramcheck src/main.c)
target_compile_options(ramcheck PRIVATE
    -Wall -Wextra -Werror -Os -std=gnu11 -fstrict-volatile-bitfields)
target_link_libraries(ramcheck PRIVATE Gint::Gint)

generate_g3a(TARGET ramcheck OUTPUT "RAMCheck.g3a" NAME "RAMCheck"
    ICONS
        "${TMC_ROOT}/port/fxcg/assets/icon-uns.png"
        "${TMC_ROOT}/port/fxcg/assets/icon-sel.png")
