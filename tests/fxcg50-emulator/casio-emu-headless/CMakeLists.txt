cmake_minimum_required(VERSION 3.15)
project(casio_emu_headless LANGUAGES C)

set(CASIO_EMU_SOURCE "" CACHE PATH "Path to the Heath123/casio-emu checkout")
if(NOT EXISTS "${CASIO_EMU_SOURCE}/src/interpreter.c")
    message(FATAL_ERROR "CASIO_EMU_SOURCE must point to a casio-emu source checkout")
endif()

# Copy the emulator sources and splice in the test-only MMU hooks
# (mmu_window.c; active only with --d1-pager). The pinned checkout itself is
# never modified. Each anchor must match exactly or configuration fails.
set(EMU_SRC "${CMAKE_BINARY_DIR}/emu/src")
file(REMOVE_RECURSE "${CMAKE_BINARY_DIR}/emu")
file(COPY "${CASIO_EMU_SOURCE}/src" DESTINATION "${CMAKE_BINARY_DIR}/emu")
file(COPY "${CMAKE_CURRENT_SOURCE_DIR}/mmu_window.h" DESTINATION "${CMAKE_BINARY_DIR}")

function(splice file anchor replacement)
    file(READ "${EMU_SRC}/${file}" text)
    string(FIND "${text}" "${anchor}" at)
    if(at EQUAL -1)
        message(FATAL_ERROR "casio-emu changed: anchor not found in ${file}: ${anchor}")
    endif()
    string(REPLACE "${anchor}" "${replacement}" text "${text}")
    file(WRITE "${EMU_SRC}/${file}" "${text}")
endfunction()

set(UNALIGNED_READ "    printf(\"Unaligned memory read at %08x, PC = %08x\\n\", address, cpu.reg.PC);\n    // *((volatile u32* ) 1);\n    exit(1);\n  }\n")
splice(memory/memory.c "${UNALIGNED_READ}" "${UNALIGNED_READ}  MMU_WINDOW_HOOK(address, false);\n")
set(UNALIGNED_WRITE "    printf(\"Unaligned memory write at %08x\\n\", address);\n    exit(1);\n  }\n")
splice(memory/memory.c "${UNALIGNED_WRITE}" "${UNALIGNED_WRITE}  MMU_WINDOW_HOOK(address, true);\n")
splice(memory/memory.c "    if ((address & 0xff000000) == 0xb4000000) {"
    "    if (mmuWindowActive && mmuSpecialWrite(address, value)) return;\n    if ((address & 0xff000000) == 0xb4000000) {")
splice(memory/memory.c "#include \"memory.h\"" "#include \"memory.h\"\n#include \"../../../mmu_window.h\"")
splice(interpreter.c "#include \"interrupts.h\"" "#include \"interrupts.h\"\n#include \"../../mmu_window.h\"")
splice(interpreter.c "    if (cpu.interruptPending && !cpu.isBranchDelaySlot) {"
    "    if (cpu.interruptPending && !cpu.isBranchDelaySlot && !mmuInterruptsHeld()) {")
splice(interpreter.c "    // TODO: Check alignment\n    u16 instr = readMemory2Quick(cpu.reg.PC);"
    "    if (mmuWindowActive) {\n      if (cpu.reg.PC == 0x80020070 && mmuSyscall()) continue;\n      mmuInstrPC = cpu.reg.PC;\n      mmuInSlot = cpu.branchDelayDone;\n      if (setjmp(mmuFaultJmp)) { mmuRaiseMiss(); continue; }\n    }\n    // TODO: Check alignment\n    u16 instr = readMemory2Quick(cpu.reg.PC);")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/tlb_append.c.in" TLB_APPEND)
file(APPEND "${EMU_SRC}/hardware/tlb/tlb.c" "${TLB_APPEND}")
set(CASIO_EMU_SOURCE "${CMAKE_BINARY_DIR}/emu")

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_executable(casio-emu-headless
    "${CASIO_EMU_SOURCE}/src/interpreter.c"
    "${CASIO_EMU_SOURCE}/src/instructions.c"
    "${CASIO_EMU_SOURCE}/src/interrupts.c"
    "${CASIO_EMU_SOURCE}/src/hardware/cpuRegisters/cpuRegisters.c"
    "${CASIO_EMU_SOURCE}/src/hardware/display/display.c"
    "${CASIO_EMU_SOURCE}/src/hardware/dma/dma.c"
    "${CASIO_EMU_SOURCE}/src/hardware/intc/intc.c"
    "${CASIO_EMU_SOURCE}/src/hardware/keyboard/keyboard.c"
    "${CASIO_EMU_SOURCE}/src/hardware/power/power.c"
    "${CASIO_EMU_SOURCE}/src/hardware/timers/timers.c"
    "${CASIO_EMU_SOURCE}/src/hardware/tlb/tlb.c"
    "${CASIO_EMU_SOURCE}/src/hardware/rtc/rtc.c"
    "${CASIO_EMU_SOURCE}/src/hardware/ports/ports.c"
    "${CASIO_EMU_SOURCE}/src/hardware/adc/adc.c"
    "${CASIO_EMU_SOURCE}/src/hardware/bcd/bcd.c"
    "${CASIO_EMU_SOURCE}/src/hardware/debug/debug.c"
    "${CASIO_EMU_SOURCE}/src/memory/memory.c"
    "${CASIO_EMU_SOURCE}/src/memory/specialAddrs.c"
    headless_gui.c
    headless_main.c
    mmu_window.c
)

target_include_directories(casio-emu-headless PRIVATE "${CASIO_EMU_SOURCE}/src" "${CMAKE_BINARY_DIR}")
target_compile_options(casio-emu-headless PRIVATE -O3 -Wall -Wextra)
target_link_libraries(casio-emu-headless PRIVATE m)
