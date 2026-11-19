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
