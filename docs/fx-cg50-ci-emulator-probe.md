# fx-CG50 P2 SR flag probe in CI emulator

The workflow uses `Heath123/casio-emu` commit
`5d34e298b94be65f7ab0505d3c0f9b9ed802ac4b`.

The existing workflow continues to build and upload the hardware P2 probes.
It additionally runs `P2-FLAG-ISOLATION-PROBE.g3a` in the headless emulator.
For that CI build only, the probe skips the EXE wait, holds its final screen,
and redraws the terminal all-stages PASS line in RGB565 green. The headless
GUI checks that green pixels are present in the captured framebuffer. Ordinary
hardware builds do not define `TMC_FXCG_P2_EMULATOR_TEST` and retain the EXE
wait behavior.

The headless executable is invoked at
`build/casio-emu-headless/casio-emu-headless`, matching the existing Picori
smoke-test step and the CMake binary output location.

## What the emulator run checks

- The P2 probe add-in starts under the same fxSDK/Gint toolchain as the port.
- The probe can copy to and execute from the emulator's mapped P2 DRAM alias.
- Its implemented `LDC`/`STC` SR paths, synthetic control flow, raw SR flag
  readback comparisons, restoration, and final result screen execute without
  emulator failure. The emulator test harness seeds raw `SR.MD=1` only for
  `--p2-flags`, because this emulator starts its CPU state zeroed and does
  not emulate the OS add-in launch state.
- This run does not validate the emulator's RB bank-switch bookkeeping; it
  validates the probe's raw SR field readback and straight-line execution.
- The normal Picori fail-closed boot smoke test remains a separate CI step.

## What it does not check

This pinned emulator is not a TLB-refill oracle:

- `src/memory/memory.c` marks proper MMU emulation as TODO and uses direct
  address aliases in its memory map.
- `src/instructions.c::ldtlb()` reports that `LDTLB` is not implemented and
  exits.
- `src/hardware/tlb/tlb.c` contains simplified fake UTLB arrays, not a genuine
  SH-4A UTLB/ITLB translation and refill path.
- `src/interrupts.c` uses simplified exception/interrupt behavior.

A passing CI run validates this synthetic SR/P2 instruction sequence only. It
does not validate an actual TLB miss, `LDTLB`, exception entry/return timing,
hardware UTLB replacement behavior, cache coherency on the physical CPU, or
coexistence with the installed OS/Gint handler. Those remain separate hardware
gates.

`Fxcg_RomInit()` must remain fail-closed until the real exception-handler path
has been independently designed and validated on the fx-CG50.
