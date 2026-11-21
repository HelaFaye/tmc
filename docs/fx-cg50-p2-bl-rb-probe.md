# fx-CG50 P2 SR-flag isolation probe

Status: **replacement source prepared; build, linked disassembly review, and hardware run pending**.

## Hardware result that prompted this probe

The preceding straight-line `P2-BL-RB-PROBE.g3a` displayed its final
`8/8: Executing straight-line P2` stage, did not return to the result screen,
and required a physical calculator restart. This is a failed hardware result.
The old combined-state artifact must not be run again.

That test set `SR.RB` and `SR.BL` together and executed multiple register
operations while both bits were set. The result does not identify whether the
failure came from the RB transition, BL transition, instruction fetch after
`LDC SR`, one of the protected instructions, or the SR restoration path.

Renesas documents that `SR.BL=1` masks interrupts and that a general exception
other than user break while BL is set causes reset behavior. The reset is
therefore consistent with an exception in the protected interval, but it does
not identify the exact exception or instruction. See the SH-4A Software Manual
Rev. 1.50, §5.5.3 and §5.7.

## Replacement: isolate the SR bits

The optional target keeps the existing CMake option
`TMC_FXCG_P2_BL_RB_PROBE`, but now emits the distinctly named
`P2-FLAG-ISOLATION-PROBE.g3a` so it cannot be confused with the failed binary.
It copies one 36-byte, position-independent wrapper to `0xac7ff100`. The C
harness calls that wrapper twice with one flag mask at a time:

1. **RB-only:** sets `SR.RB=1` while `SR.BL=0`, executes three NOPs in P2,
   restores SR, and returns `0x55`.
2. **BL-only:** sets `SR.BL=1` while `SR.RB=0`, executes three NOPs in P2,
   restores SR, and returns `0x55`.

The wrapper copies its argument from ABI register R4 into unbanked R10 before
changing SR. It preserves R8, R10, and PR on the stack outside the protected
interval. Between setting and restoring SR, there are only three NOPs, the
restoring `LDC R8,SR`, and that instruction's delay NOP. There are no
data-memory loads/stores, branches, calls, returns, or literal loads while
BL=1. The wrapper's normal result path executes only after the original SR is
restored.

The two tests are deliberately separate. A pass does **not** prove that the
combined `RB|BL` state works, that an actual TLB-miss vector can be entered, or
that Gint's exception stack, handler chaining, `LDTLB`, or `RTE` path is safe.
It also does not enable the ROM window.

## Build and review

From `~/tmc/port/fxcg`:

```sh
fxsdk build-cg -c -DTMC_FXCG_EMULATOR_TEST=OFF -DTMC_FXCG_P2_EXEC_PROBE=ON -DTMC_FXCG_P2_BL_RB_PROBE=ON
fxsdk build-cg
sha256sum P2-FLAG-ISOLATION-PROBE.g3a
sh-elf-objdump -d --disassemble=_P2ExecInTlbMissSrStateTemplate build-cg/fxcg_p2_bl_rb_probe
sh-elf-objdump -r build-cg/fxcg_p2_bl_rb_probe
sh-elf-nm -an build-cg/fxcg_p2_bl_rb_probe | grep P2ExecInTlbMissSrStateTemplate
```

Before hardware use, verify the linked template is exactly 36 bytes, has no
relocations, and contains this control flow:

- save PR, R8, and R10 before changing SR;
- copy the flag mask from R4 to R10 before changing SR;
- read and retain the original SR in R8;
- build the temporary SR in R10 using `OR R8,R10`, then execute `LDC R10,SR`;
- execute three NOPs, then restore the original SR with `LDC R8,SR` and one NOP;
- restore R10, R8, and PR; set return value `0x55`; execute `RTS`.

The earlier combined template was defective: it ORed the mask into R8 and then
executed `LDC R8,SR` again at the purported restore point. That re-applied the
modified SR instead of restoring the original value. The corrected template
must keep original SR in R8 and temporary SR in R10; do not accept a disassembly
that reverses those roles.

Do not proceed if the disassembly differs, the template size is not 36 bytes,
or relocations remain in the copied template. In particular, reject any
sequence that executes `LDC R8,SR` to enter the test state and then reuses R8
as the temporary SR; R8 must still contain the original SR at the restore.

## Hardware run

Run only `P2-FLAG-ISOLATION-PROBE.g3a` on the fx-CG50 running OS 3.20.0202.
Do not rerun `P2-BL-RB-PROBE.g3a`. Do not run
`TLB-STATE-PROBE.g3a` or `TLBH-LINK-PROBE-DO-NOT-RUN.g3a`.

Expected screens are `RB-only P2: PASS`, then `BL-only P2: PASS`, then
`Flag isolation: PASS`. The first result screen waits for EXE before proceeding
to the second test. A reset, hang, exception screen, or unexpected result is a
failed test; record which stage was visible and do not repeat the same binary.

## Scope and next gate

The immediate goal is only to distinguish whether a lone RB transition or a
minimal BL=1 P2 interval causes the restart. If both tests pass, a separate
reviewed test may combine the bits with the same NOP-only interval. Do not jump
directly to a real exception handler. Any later handler must preserve Gint's
normal add-in-code TLB-miss path and must be tested independently. `Fxcg_RomInit()`
remains fail-closed throughout this investigation.