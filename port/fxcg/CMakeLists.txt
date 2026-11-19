# Project Picori for the Casio fx-CG50.
#
#   cd port/fxcg && fxsdk build-cg
#
# Needs the generated headers under build/USA (from the PC build's asset
# step; see docs/fx-cg50-progress.md) and the page pak the add-in reads
# (scripts/fxcg/build_page_pak.py). The pak holds ROM data: make it from your
# own ROM and do not distribute it; the add-in itself contains no ROM data.

cmake_minimum_required(VERSION 3.15)
project(Picori C)

include(GenerateG3A)
find_package(Gint 2.11 REQUIRED)

option(TMC_FXCG_EMULATOR_TEST
    "Build the fail-closed boot screen for the headless fx-CG50 emulator" OFF)
option(TMC_FXCG_PPU_BENCH
    "Build the on-device PPU timing overlay (no clock changes)" OFF)
if(TMC_FXCG_PPU_BENCH)
    find_package(LibProf 2.1 REQUIRED)
endif()

get_filename_component(TMC_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(TMC_GENERATED "${TMC_ROOT}/build/USA" CACHE PATH "Generated headers (gfx_offsets.h, map_offsets.h)")
if(NOT EXISTS "${TMC_GENERATED}/assets/gfx_offsets.h")
    message(FATAL_ERROR "Missing ${TMC_GENERATED}/assets/gfx_offsets.h: run the PC build's asset step first")
endif()

include(sources.cmake)

set(PICORI_PORT_SOURCES
    # The PC port's portable layer, compiled unchanged (TMC_FXCG guards).
    ${TMC_ROOT}/port/flag_remap_generated.c
    ${TMC_ROOT}/port/port_animation.c
    ${TMC_ROOT}/port/port_bios.c
    ${TMC_ROOT}/port/port_debug_verbose.c
    ${TMC_ROOT}/port/port_draw.c
    ${TMC_ROOT}/port/port_figurines.c
    ${TMC_ROOT}/port/port_gameplay_stubs.c
    ${TMC_ROOT}/port/port_gba_mem.c
    ${TMC_ROOT}/port/port_gba_shadow.c
    ${TMC_ROOT}/port/port_hdma.c
    ${TMC_ROOT}/port/port_inline_ptrs.c
    ${TMC_ROOT}/port/port_m4a_stubs.c
    ${TMC_ROOT}/port/port_math.c
    ${TMC_ROOT}/port/port_offset_remap.c
    ${TMC_ROOT}/port/port_practice.c
    ${TMC_ROOT}/port/port_region_data.c
    ${TMC_ROOT}/port/port_roll_attack_macro.c
    ${TMC_ROOT}/port/port_rom.c
    ${TMC_ROOT}/port/port_rom_tables.c
    ${TMC_ROOT}/port/port_rom_touch.c
    ${TMC_ROOT}/port/port_room_funcs.c
    ${TMC_ROOT}/port/port_save.c
    ${TMC_ROOT}/port/port_script_addrs.c
    ${TMC_ROOT}/port/port_script_funcs.c
    ${TMC_ROOT}/port/port_stubs.c
    ${TMC_ROOT}/port/port_text_render.c
    ${TMC_ROOT}/port/data_stubs_autogen.c
    ${TMC_ROOT}/port/port_linked_stubs.c
    ${TMC_ROOT}/port/stubs_autogen.c
    ${TMC_ROOT}/port/ppu/src/mode1.c
    ${TMC_ROOT}/port/ppu/src/virtuappu.c
    # The calculator platform.
    fxcg_libc.c
    fxcg_main.c
    fxcg_rom.c
    fxcg_stubs.c
    fxcg_sys.c
)
# port/data_const_stubs.c (copies of ROM tables) is left out on purpose:
# fxcg.ld places those symbols in the ROM window (rom_symbols.ld).

if(TMC_FXCG_PPU_BENCH)
    list(APPEND PICORI_PORT_SOURCES fxcg_ppu_bench.c)
endif()

add_executable(picori ${PICORI_GAME_SOURCES} ${PICORI_PORT_SOURCES})
target_compile_options(picori PRIVATE -Os -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
target_compile_definitions(picori PRIVATE
    PC_PORT NON_MATCHING USA ENGLISH REVISION=0 MULTI_REGION TMC_FXCG)
if(TMC_FXCG_EMULATOR_TEST)
    target_compile_definitions(picori PRIVATE TMC_FXCG_EMULATOR_TEST=1)
endif()
if(TMC_FXCG_PPU_BENCH)
    target_compile_definitions(picori PRIVATE TMC_FXCG_PPU_BENCH=1)
endif()
target_include_directories(picori PRIVATE
    ${TMC_ROOT}/include ${TMC_GENERATED} ${TMC_ROOT}/port ${TMC_ROOT}
    ${TMC_ROOT}/port/ppu/include ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(picori PRIVATE Gint::Gint)
if(TMC_FXCG_PPU_BENCH)
    target_link_libraries(picori PRIVATE LibProf::LibProf)
endif()
# Replace gint's linker script with ours (gint's plus the ROM window and
# sysram); -L lets it INCLUDE rom_symbols.ld.
set_target_properties(Gint::Gint PROPERTIES
    INTERFACE_LINK_OPTIONS "-T;${CMAKE_CURRENT_SOURCE_DIR}/fxcg.ld;-L;${CMAKE_CURRENT_SOURCE_DIR}"
    INTERFACE_LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/fxcg.ld;${CMAKE_CURRENT_SOURCE_DIR}/rom_symbols.ld")
target_link_options(picori PRIVATE -Wl,--gc-sections)

generate_g3a(TARGET picori OUTPUT "Picori.g3a" NAME "Picori"
    ICONS assets/icon-uns.png assets/icon-sel.png)
