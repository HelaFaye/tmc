# fx-CG50 P2 instruction-execution probe

Status: **ordinary P2 instruction execution passed on fx-CG50 OS 3.20.0202; the next synthetic MD/RB/BL test is pending**.

## Why this is the next gate

The upper 3,200 KiB DRAM range has passed the read/write RAMCheck through its
uncached P2 alias on the fx-CG50 running OS 3.20.0202. That result does not
establish instruction fetch from the P2 alias. Before placing any D1 TLB-miss
code there, verify ordinary instruction execution and return independently.

The Renesas SH-4A Software Manual, Rev. 1.50, §7.3.3 / Figure 7.10 (printed
page 135), routes instruction accesses in the P2 area directly to non-cacheable
memory rather than through the ITLB. The same manual, §2.7.1 (printed page 22),
requires `SYNCO` followed by `ICBI @Rn` before executing code modified in a
non-cacheable area, to prevent stale prefetched instructions from running.

References:

- [Renesas SH-4A Software Manual](https://www.renesas.com/en/document/mas/sh4al-dsp-software-manual), Rev. 1.50, §2.7.1 and §7.3.3.
- [CG50 Internals: memory map](https://hexbinoct.github.io/casio-cg50/memory-map/).
- [CG50 Internals: MMU and add-in launch](https://hexbinoct.github.io/casio-cg50/os/mmu-and-addins/), which records that fx-CG50 add-ins run privileged. The target's actual OS remains the hardware authority.

## Probe design

The new optional target is `fxcg_p2_exec_probe`, producing
`P2-EXEC-PROBE.g3a`. Its source is
`tests/fxcg50-p2-exec-probe/p2_exec_probe.c`.

It uses address `0xac7ff000`, the final 4 KiB page in the tested P2 alias
range `0xac4e0000..0xac800000` (physical `0x0c7ff000`). The corresponding P1
page is `0x8c7ff000`, above the previously linked `.xbss` end
`0x8c657eb0`. The test writes only six bytes; it does not allocate a stack or
large buffer in this region.

The six big-endian instruction bytes are:

```text
e0 35    mov #0x35,r0
00 0b    rts
00 09    nop                 ; RTS delay slot
```

The probe verifies the bytes by reading them back through P2, issues the
manual-required `SYNCO; ICBI @Rn` sequence, then calls the P2 address as a
`uint32_t(void)` function. A correct result (`0x00000035`) demonstrates that
instruction fetch reached the P2 routine, its `RTS` returned through the
caller-supplied PR, and execution resumed in the original P0 add-in code.

The test intentionally does **not**:

- access or modify PTEH, PTEL, MMUCR, UTLB, or ITLB;
- execute `LDTLB`, change URB/URC, or replace Gint's TLB-miss handler;
- change VBR, SR, interrupt state, or exception vectors;
- execute in the `SR.BL=1` exception-entry context;
- enable `Fxcg_RomInit()` or touch the ROM window.

This is a prerequisite test, not proof that a P2 TLB-miss handler is safe.
In particular, it does not test exception-entry stack availability, exception
return rules, or any custom TLB refill strategy. A later trampoline test must
still prove the exact P2 code/stack/register/return path with the exception
constraints applied.

## Build and run

From `~/tmc/port/fxcg`:

```bash
fxsdk build-cg -c -DTMC_FXCG_EMULATOR_TEST=OFF -DTMC_FXCG_P2_EXEC_PROBE=ON
fxsdk build-cg
sha256sum P2-EXEC-PROBE.g3a
```

Run **only** `P2-EXEC-PROBE.g3a` on the fx-CG50. Do not run
`TLB-STATE-PROBE.g3a` or `TLBH-LINK-PROBE-DO-NOT-RUN.g3a`. The normal Picori
`Picori.g3a` remains fail-closed and is not the target of this experiment.

Expected final screen:

```text
fx-CG50 P2 execution probe
P2 execute + return: PASS
Returned expected value 0x00000035
```

Press EXE to close. If the display stays on `Then executing from P2...`, the
routine did not return normally. A reset or exception screen is also a failed
probe; record the exact observation and do not proceed to a TLB handler.

The emulator workflow is configured to compile and archive this add-in only;
it must not automatically execute it. The pass criterion is a hardware result
on the target calculator and OS, not merely a successful link.

## Hardware result: ordinary P2 execution passed

On the fx-CG50 running OS 3.20.0202, the user confirmed
`P2 execute + return: PASS` and the expected `0x00000035` result. Tested
artifact SHA-256:

`10e0cc372bfd215e46467ee184ebf542e4f705c84ea2bd39321c67ff212adfb4`

This validates ordinary P2 execution/return only. It does not establish
execution under the `MD|RB|BL` state set by a TLB-miss exception. That is the
next isolated test, documented in `docs/fx-cg50-p2-bl-rb-probe.md`.

## Acceptance and next step

Accept only when all of the following are recorded:

1. The target builds and the linked ELF disassembly confirms the intended
   three instructions and P2 call target.
2. P2 code-byte readback succeeds.
3. The add-in displays `P2 execute + return: PASS` and closes with EXE.
4. The exact `.g3a` SHA-256 and calculator OS version are recorded.

After that, design a separate exception-context trampoline experiment. Keep
D1 blocked until that second experiment demonstrates a safe entry, resident
stack, controlled register preservation, valid refill/return semantics, and
correct delegation to Gint for non-D1 misses.
