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
option(TMC_FXCG_TLBH_LINK_PROBE
    "Build a non-runnable link-only probe for overriding Gint's TLB-miss handler" OFF)
option(TMC_FXCG_TLB_STATE_PROBE
    "Build a read-only fx-CG50 UTLB state probe for hardware diagnostics" OFF)
option(TMC_FXCG_TLB_SLOT_SWEEP_PROBE
    "Build a hardware probe that walks add-in ROM pages to observe OS TLB slot allocation" OFF)
option(TMC_FXCG_P2_EXEC_PROBE
    "Build an isolated SH-4A P2 instruction-fetch/return probe" OFF)
option(TMC_FXCG_P2_BL_RB_PROBE
    "Build an isolated P2 probe with synthetic TLB-miss MD/RB/BL state" OFF)
option(TMC_FXCG_D1_MAP_PROBE
    "Build hardware-only D1 alias proof using the OS 4 KiB mapper" OFF)
option(TMC_FXCG_COLD_DATA_TLB_MISS_PROBE
    "Build a hardware-only controlled cold data-TLB miss/refill probe" OFF)
option(TMC_FXCG_D1_PAGER
    "Build Picori with the D1 ROM window (TLB-miss pager reading Picori.pak); default stays fail-closed" OFF)
option(TMC_FXCG_D1_PAGER_PROBE
    "Build the hardware-only D1-PAGER-PROBE add-in (synthetic pages, staged log)" OFF)
if(TMC_FXCG_D1_PAGER OR TMC_FXCG_D1_PAGER_PROBE OR TMC_FXCG_TLBH_LINK_PROBE OR TMC_FXCG_P2_BL_RB_PROBE OR TMC_FXCG_TLB_STATE_PROBE OR TMC_FXCG_TLB_SLOT_SWEEP_PROBE OR TMC_FXCG_D1_MAP_PROBE OR TMC_FXCG_COLD_DATA_TLB_MISS_PROBE)
    enable_language(ASM)
endif()
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

# D1 pager: the TLB-miss handler (replaces gint's .gint.tlbh), the P2 UTLB
# eviction helper and the DEFLATE decoder from fxLauncher's link library.
set(CGLINK_DIR ${TMC_ROOT}/external/fxLauncher/lib/cglink)
set(PICORI_D1_SOURCES fxcg_tlbh.S fxcg_p2_tlb.S ${CGLINK_DIR}/src/inflate.c)
# SYNCO/ICBI/OCBP are SH-4A instructions (the probes build the same way).
set_source_files_properties(fxcg_rom.c fxcg_p2_tlb.S fxcg_tlbh.S
    PROPERTIES COMPILE_OPTIONS "-m4a-nofpu")
if(TMC_FXCG_D1_PAGER)
    list(APPEND PICORI_PORT_SOURCES ${PICORI_D1_SOURCES})
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
if(TMC_FXCG_D1_PAGER)
    target_compile_definitions(picori PRIVATE TMC_FXCG_D1_PAGER=1)
    target_include_directories(picori PRIVATE ${CGLINK_DIR}/include)
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

# A separate tiny target verifies that an application object can provide
# Gint's .gint.tlbh entry point under this exact toolchain and linker script.
# Its handler is a deliberately nonfunctional `rte; nop` stub: build and
# inspect its ELF only; NEVER execute the generated add-in.
if(TMC_FXCG_TLBH_LINK_PROBE)
    # CMake otherwise ignores the .S input when only C is enabled.
    # Since C is already enabled, ASM uses the same SH cross-compiler.
    enable_language(ASM)
    add_executable(fxcg_tlbh_link_probe
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlbh_link_probe.c
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlbh_link_probe.S)
    target_compile_options(fxcg_tlbh_link_probe PRIVATE
        -Os -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_tlbh_link_probe PRIVATE Gint::Gint)
    # Keep the fxcg.ld _errno placement canary active in this minimal target.
    # The probe does not otherwise use malloc(), so force libc's _errno object
    # into the link instead of letting the linker-script assertion see it as
    # an unresolved symbol. This does not change the TLB-handler test itself.
    target_link_options(fxcg_tlbh_link_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_tlbh_link_probe
        OUTPUT "TLBH-LINK-PROBE-DO-NOT-RUN.g3a"
        NAME "TLBH Link Probe - DO NOT RUN"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Read-only target-hardware snapshot of MMUCR/PTEH/PTEL and all UTLB entries.
# It uses Gint's default TLB handler and never modifies MMU state.
if(TMC_FXCG_TLB_STATE_PROBE)
    add_executable(fxcg_tlb_state_probe
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlb_state_probe.c
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlb_state_probe.S)
    target_compile_options(fxcg_tlb_state_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_tlb_state_probe PRIVATE Gint::Gint)
    # Preserve the linker-script libc _errno placement assertion.
    target_link_options(fxcg_tlb_state_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_tlb_state_probe
        OUTPUT "TLB-STATE-PROBE.g3a"
        NAME "TLB State Probe"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Hardware-only test of instruction fetch and RTS return from the uncached P2
# alias of the already-tested upper-DRAM region. Never run this in the emulator.
if(TMC_FXCG_P2_EXEC_PROBE)
    add_executable(fxcg_p2_exec_probe
        ${TMC_ROOT}/tests/fxcg50-p2-exec-probe/p2_exec_probe.c)
    # SYNCO and ICBI are SH-4A instructions; no FPU instructions are used.
    target_compile_options(fxcg_p2_exec_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_p2_exec_probe PRIVATE Gint::Gint)
    # Keep the fxcg.ld libc _errno placement assertion active.
    target_link_options(fxcg_p2_exec_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_p2_exec_probe
        OUTPUT "P2-EXEC-PROBE.g3a"
        NAME "P2 Execution Probe"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Synthetic P2 execution test with the MD/RB/BL state used on TLB-miss
# entry. It runs on hardware and in the headless emulator, but does not trigger
# an exception or access VBR/MMU/TLB registers.
if(TMC_FXCG_P2_BL_RB_PROBE)
    add_executable(fxcg_p2_bl_rb_probe
        ${TMC_ROOT}/tests/fxcg50-p2-bl-rb-probe/p2_bl_rb_probe.c
        ${TMC_ROOT}/tests/fxcg50-p2-bl-rb-probe/p2_bl_rb_trampoline.S)
    target_compile_options(fxcg_p2_bl_rb_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_p2_bl_rb_probe PRIVATE Gint::Gint)
    # CI has no keyboard input: auto-dismiss result screens and draw a
    # framebuffer marker after the final successful SR readback.
    if(TMC_FXCG_EMULATOR_TEST)
        target_compile_definitions(fxcg_p2_bl_rb_probe PRIVATE
            TMC_FXCG_P2_EMULATOR_TEST=1)
    endif()
    target_link_options(fxcg_p2_bl_rb_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_p2_bl_rb_probe
        OUTPUT "P2-FLAG-ISOLATION-PROBE.g3a"
        NAME "P2 Flag Isolation"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Read-only hardware diagnostic for the OS-managed add-in page-mapping pool.
# It touches one word per mapped add-in ROM page using normal loads, allowing
# Gint's stock handler and the OS to resolve the resulting data-side TLB misses.
# The final UTLB/ITLB snapshot is read through the existing P2 helper.
if(TMC_FXCG_TLB_SLOT_SWEEP_PROBE)
    add_executable(fxcg_tlb_slot_sweep_probe
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlb_state_probe.c
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlb_state_probe.S)
    target_compile_options(fxcg_tlb_slot_sweep_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
    target_compile_definitions(fxcg_tlb_slot_sweep_probe PRIVATE
        TMC_FXCG_TLB_SLOT_SWEEP=1)
    target_link_libraries(fxcg_tlb_slot_sweep_probe PRIVATE Gint::Gint)
    target_link_options(fxcg_tlb_slot_sweep_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_tlb_slot_sweep_probe
        OUTPUT "TLB-SLOT-SWEEP-PROBE.g3a"
        NAME "TLB Slot Sweep Probe"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Read-only capture of the OS add-in page table and paging allocator state.
# Candidate OS RAM offsets are version-sensitive and are labelled unverified
# in the output; this target does not modify MMU or OS paging state.
option(TMC_FXCG_OS_PAGING_PROBE
    "Build read-only fx-CG50 OS paging-state diagnostic" OFF)
if(TMC_FXCG_OS_PAGING_PROBE)
    add_executable(fxcg_os_paging_probe
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/os_paging_state_probe.c)
    target_compile_options(fxcg_os_paging_probe PRIVATE
        -Os -std=gnu11 -fno-freestanding -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_os_paging_probe PRIVATE Gint::Gint)
    target_link_options(fxcg_os_paging_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_os_paging_probe
        OUTPUT "OS-PAGING-PROBE.g3a"
        NAME "OS Paging State Probe"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Hardware-only proof that the running OS can map add-in flash pages at D1
# virtual addresses using its own 4 KiB round-robin allocator. This does not
# enable the Picori D1 runtime or install a custom TLB-miss handler.
if(TMC_FXCG_D1_MAP_PROBE)
    add_executable(fxcg_d1_map_probe
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/d1_map_proof_probe.c
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/os_map_page_4k.S
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlb_state_probe.S)
    target_compile_options(fxcg_d1_map_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding
        -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_d1_map_probe PRIVATE Gint::Gint)
    target_link_options(fxcg_d1_map_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_d1_map_probe
        OUTPUT "D1-MAP-PROBE.g3a"
        NAME "D1 OS Mapper Proof"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()


# One controlled data-side miss in a valid add-in page. The existing Gint/OS
# handler must refill it; this target does not install or replace .gint.tlbh.
if(TMC_FXCG_COLD_DATA_TLB_MISS_PROBE)
    add_executable(fxcg_cold_data_tlb_miss_probe
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/cold_data_tlb_miss_probe.c
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/cold_data_tlb_miss_probe.S
        ${TMC_ROOT}/tests/fxcg50-tlb-probe/tlb_state_probe.S)
    target_compile_options(fxcg_cold_data_tlb_miss_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding
        -ffunction-sections -fdata-sections -w)
    target_link_libraries(fxcg_cold_data_tlb_miss_probe PRIVATE Gint::Gint)
    target_link_options(fxcg_cold_data_tlb_miss_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_cold_data_tlb_miss_probe
        OUTPUT "COLD-DATA-TLB-MISS-PROBE.g3a"
        NAME "Cold Data TLB Miss Probe"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()

# Staged hardware test of the D1 pager with generated pages (no pak): the
# same fxcg_tlbh.S, fxcg_p2_tlb.S and fxcg_rom.c as Picori. See
# docs/fx-cg50-d1-pager.md for the stages and how to read D1-PAGER.TXT.
if(TMC_FXCG_D1_PAGER_PROBE)
    add_executable(fxcg_d1_pager_probe
        ${TMC_ROOT}/tests/fxcg50-d1-pager-probe/d1_pager_probe.c
        ${TMC_ROOT}/tests/fxcg50-d1-pager-probe/d1_pager_regs.S
        fxcg_rom.c fxcg_tlbh.S fxcg_p2_tlb.S)
    target_compile_options(fxcg_d1_pager_probe PRIVATE
        -Os -m4a-nofpu -std=gnu11 -fno-freestanding
        -ffunction-sections -fdata-sections -w)
    target_compile_definitions(fxcg_d1_pager_probe PRIVATE
        TMC_FXCG_D1_PAGER=1 FXCG_ROM_SYNTHETIC=1 FXCG_ROM_FRAMES=64)
    if(TMC_FXCG_EMULATOR_TEST)
        # Report through the headless emulator's debug port (--d1-pager).
        target_compile_definitions(fxcg_d1_pager_probe PRIVATE TMC_FXCG_EMULATOR_TEST=1)
    endif()
    target_include_directories(fxcg_d1_pager_probe PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR} ${CGLINK_DIR}/include)
    target_link_libraries(fxcg_d1_pager_probe PRIVATE Gint::Gint)
    target_link_options(fxcg_d1_pager_probe PRIVATE
        -Wl,--gc-sections -Wl,-u,_errno)
    generate_g3a(TARGET fxcg_d1_pager_probe
        OUTPUT "D1-PAGER-PROBE.g3a"
        NAME "D1 Pager Probe"
        ICONS assets/icon-uns.png assets/icon-sel.png)
endif()
