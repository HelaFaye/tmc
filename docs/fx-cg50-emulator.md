# fx-CG50 emulator smoke test

`.github/workflows/fxcg50-emulator.yml` builds the actual `port/fxcg` add-in
with the pinned fxSDK/SH-4A toolchain, then runs the resulting `Picori.g3a` in
the pinned headless `Heath123/casio-emu` interpreter. The workflow follows the
same toolchain-container, emulator-source, framebuffer-capture and artifact
pattern used by `HelaFaye/fxOGL`.

The test currently validates the earliest safe boot milestone, not gameplay:

1. Gint starts the real Picori add-in and initializes the fx-CG50 platform.
2. Picori clears its game BSS in the measured upper-DRAM range.
3. `Fxcg_RomInit()` rejects initialization because the D1 ROM window is still
   deliberately unimplemented.
4. The platform renders the expected fail-closed error message. A one-pixel
   RGB565 marker is added only in the emulator-test build so the headless
   framebuffer oracle can distinguish the intended boot path from a blank
   display or an unrelated crash.

`TMC_FXCG_EMULATOR_TEST` only changes the message wait in this test build: it
holds on the rendered screen instead of waiting for a physical key. Normal
calculator builds leave the key wait and screen output unchanged.

The workflow uploads `emulated-screen.ppm` even when the test fails, when a
framebuffer capture exists.

## Scope and limitations

This is a smoke test of the real add-in binary, Gint startup, the current BSS
placement, display output and the intentionally safe ROM-init failure path.
It does **not** validate the D1 TLB refill handler, the page-pak reader, the
hardware OS 3.20.0202 MMU behavior, or game execution. The emulator is not a
replacement for physical-calculator validation. No copyrighted ROM or page
pak is checked out or embedded by this workflow.

## D1 pager test (`--d1-pager`)

The pinned emulator has no MMU, no TLB-miss exceptions and no OS, so the
plain smoke test cannot reach the D1 pager. The harness therefore copies the
emulator sources into its build directory and splices in a **test-only MMU
extension** (`tests/fxcg50-emulator/casio-emu-headless/mmu_window.c`). It is
inactive unless `--d1-pager` is given, so the two tests above run the
emulator as before. For the ROM window `[0x20000000, 0x21000000)` only, it
adds:

- translation through the emulator's 64-entry UTLB model;
- precise TLB-miss exceptions: the faulting access is aborted, SPC holds the
  faulting instruction (or its branch, for a delay slot), SSR, SGR, EXPEVT
  and TEA are set, SR.MD/RB/BL are set, and the CPU jumps to `VBR+0x400`. A
  miss while BL is already set stops the run, since hardware would reset;
- OS syscall 0x09 into round-robin UTLB slots 0-54, the behaviour
  D1-MAP-PROBE recorded on OS 3.20.0202;
- writes to the memory-mapped UTLB arrays, for the P2 eviction routine;
- interrupts held while BL = 1 or IMASK = 15.

`D1-PAGER-PROBE.g3a`, built with `TMC_FXCG_EMULATOR_TEST`, prints its report
through the emulator's debug port instead of storage memory and writes a
done port when finished. CI requires `RESULT=PASS`, all eight stages
passing and `REGS_MISMATCH_MASK=0000`, and uploads the report.

First local run, 2026-11-23: all eight stages passed in 26 emulated frames,
with 6,184 window TLB misses, 3,093 syscall 0x09 mappings and 3,084
evictions with invalidation. Two deliberate bugs were planted to check that
the test can fail. Without UTLB invalidation on eviction, `5_EVICTION` fails.
Without restoring r5 in the upcall, stages 2-4 fail with
`REGS_MISMATCH_MASK=0020`.

This checks the handler's control flow, the upcall's register state and
the cache bookkeeping on an interpreter. It does not model the SH7305's
caches, ITLB, the real OS mapper, P2-only access rules or timing. CI then
builds the hardware variants of the probe and of `Picori.g3a` with
`TMC_FXCG_D1_PAGER=ON`, and uploads them as `fxcg50-d1-pager-hardware`;
CI does not run them.

## D1 pager against a real pak (`--fs`)

`D1-PAK-PROBE.g3a` runs the D1 pager probe's eight stages with pages read
from a PCPK pak instead of generated ones. CI writes a synthetic pak
(`tests/fxcg50-d1-pager-probe/make_test_pak.py`: 512 pages, every seventh
stored raw, two baseline pages; no ROM involved) and runs

    casio-emu-headless D1-PAK-PROBE.g3a --d1-pager --fs <dir with D1TEST.pak>

`--fs DIR` makes the test-only extension answer the OS file syscalls gint's
`fopen`/`fread`/`fseek` use (BFile FindFirst/FindClose, Open, Close, Size,
Seek, GetPos, Read), read-only from `DIR`. This covers the pager's real
page source: the pak index, baseline pinning, DEFLATE and raw pages, and
page reads from inside the fault upcall. First run (2026-11-26): all stages
PASS, 3,124 page fetches, 3,116 evictions, 14,201 file reads (7.3 MB).
A second CI step checks the probe fails closed when the pak is absent.
