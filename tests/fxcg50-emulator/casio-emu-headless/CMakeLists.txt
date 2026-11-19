cmake_minimum_required(VERSION 3.15)
project(casio_emu_headless LANGUAGES C)

set(CASIO_EMU_SOURCE "" CACHE PATH "Path to the Heath123/casio-emu checkout")
if(NOT EXISTS "${CASIO_EMU_SOURCE}/src/interpreter.c")
    message(FATAL_ERROR "CASIO_EMU_SOURCE must point to a casio-emu source checkout")
endif()

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
)

target_include_directories(casio-emu-headless PRIVATE "${CASIO_EMU_SOURCE}/src")
target_compile_options(casio-emu-headless PRIVATE -O3 -Wall -Wextra)
target_link_libraries(casio-emu-headless PRIVATE m)
