# fx-CG50 controlled cold data-TLB miss/refill probe

Status: **hardware-only diagnostic; build and upload do not constitute a
hardware pass**.

This test follows the OS paging-state capture and the D1 syscall-`0x09` mapper
proof. It asks a narrower question: when a valid add-in code page is present in
the OS page table but absent from the UTLB, does one ordinary **data read**
trigger the stock Gint/OS miss path and return with the expected 4 KiB mapping?

## Scope and prerequisites

Run only on the fx-CG50 running OS **3.20.0202**. Confirm the version in the
calculator's System Information screen before running the artifact. The probe
records `TARGET_OS_EXPECTED=3.20.0202`; it does not independently identify the
OS version.

The previous OS paging-state capture on this calculator established the
candidate add-in page table at `0x8C04CF0C`, mapping counter at `0x8C04D70C`,
and add-in-mapped flag at `0x8C04CEE1`. The D1 mapper proof separately tested
the OS 4 KiB syscall-`0x09` mapping behavior on this target. Those observations
are prerequisites for this diagnostic, not constants generalized to other OS
versions.

Gint's fx-CG50 TLB-miss handler uses the syscall dispatcher at `0x80020070`
and syscall index `0x0C` for add-in code-page misses. Its range check is for
add-in code beginning at `0x00300000`; the handler delegates the actual page
mapping to the OS. The public CG50 Internals write-up describes the OS-side
`0x0C`/`0x09` path for **OS 3.60**, so its target address and internal routine
layout must not be copied to OS 3.20.0202. This probe resolves the live syscall
table from the dispatcher and records the current `0x0C` and `0x09` targets
without calling either target directly.

References:

- [Gint 2.11.0 TLB-miss handler source mirror](https://github.com/JumpSushi/CGType/blob/a72732fb52f08d9cf04476a55b9bcd7d66288bab/src/kernel/tlbh.S)
  (cross-check only; the local Gint checkout commit is recorded in
  `docs/fx-cg50-d1-paging-spike.md`).
- [CG50 Internals: MMU and add-in launch](https://hexbinoct.github.io/casio-cg50/os/mmu-and-addins/)
  (OS 3.60 reverse-engineering reference, not an OS 3.20 memory map).
- [Renesas SH-4A Software Manual](https://www.renesas.com/en/document/mah/sh-4a-software-manual),
  for TLB-miss exception behavior and event reporting.

## What the probe does

1. Checks MMU translation, supervisor mode, the expected UTLB split, and the
   add-in-mapped flag.
2. Snapshots the UTLB through the existing copied P2 helper. It does not read
   the memory-mapped P4 TLB arrays from ordinary P0 C code.
3. Reads the live syscall dispatcher and resolves its PC-relative syscall
   table pointer. It records the current syscall-`0x0C` handler pointer, the
   syscall-`0x09` mapper pointer, and the first four instruction halfwords at
   the `0x0C` target. These are observations only; neither syscall is called
   directly by the diagnostic.
4. Scans up to the first 256 active add-in page-table entries. It selects a
   nonzero, 4 KiB-aligned flash page whose add-in virtual address is **not
   covered by any currently valid UTLB translation**. It reads one expected
   word through the physical page's P1 alias, which does not populate the
   candidate add-in virtual translation.
5. Bounds candidate selection by the linked add-in image size, then disables
   maskable interrupts without setting `SR.BL`, takes a fresh UTLB snapshot,
   and verifies that the candidate is still cold and the OS mapping counter
   did not change during that baseline snapshot.
6. Executes one dedicated assembly `MOV.L @r4,r0` from the candidate add-in
   virtual address. The assembly records the mapping counter immediately
   before and after the load, along with `EXPEVT`, `TEA`, and `PTEH`.
7. Takes a second UTLB snapshot through the P2 helper and verifies that the
   mapping counter advanced exactly once, the exception was a read TLB miss
   (`EXPEVT=0x040`), `TEA` is the requested data address, the returned word
   matches the P1 alias, and the expected 4 KiB translation is present in the
   expected round-robin slot.
8. Writes `/COLD-DATA-TLB-MISS.TXT` through `gint_world_switch()` after leaving
   the atomic test section.

## Safety boundary

- **Hardware only.** The lightweight `casio-emu` CI target does not model the
  SH-4A MMU translation and real TLB-miss refill path. CI builds and uploads
  this add-in but does not execute it.
- The stock Gint `.gint.tlbh` handler remains linked and installed. The probe
  does not provide or replace `.gint.tlbh`, change `VBR`, or install an
  exception handler.
- The probe does not call syscall `0x0C` or syscall `0x09` directly. A normal
  data read is the only intentional fault trigger; the existing Gint/OS path
  must handle it.
- It does not write the OS page table, add-in-mapped flag, mapping counter,
  MMUCR, PTEH, PTEL, UTLB, or ITLB. It does not call `LDTLB`.
- The P4 UTLB/ITLB arrays are read only by `P2TlbSnapshotTemplate` in the P2
  execution region. Its output is read only after the helper's `SYNCO; ICBI`
  transition. Maskable interrupts are disabled around each snapshot and the
  controlled fault sequence; `SR.BL` is not set by this probe.
- No page is force-evicted. If the first 256 page-table entries contain no
  valid cold candidate, the probe reports a preflight failure and performs no
  deliberate fault.
- The diagnostic uses add-in code pages at `0x00300000` onward. It does not
  access the planned D1 window at `0x20000000`.

## Build

```sh
cd port/fxcg
fxsdk build-cg -c \
  -DTMC_FXCG_EMULATOR_TEST=ON \
  -DTMC_FXCG_TLBH_LINK_PROBE=ON \
  -DTMC_FXCG_TLB_STATE_PROBE=ON \
  -DTMC_FXCG_OS_PAGING_PROBE=ON \
  -DTMC_FXCG_D1_MAP_PROBE=ON \
  -DTMC_FXCG_COLD_DATA_TLB_MISS_PROBE=ON
fxsdk build-cg
```

The target produces `COLD-DATA-TLB-MISS-PROBE.g3a`. CI uploads it as
`fxcg50-cold-data-tlb-miss-probe`; the artifact is not run in the emulator.

## Success criteria

The report must show all of the following:

- `PREFLIGHT=PASS` and `COLD_BEFORE_READ=1`;
- `EXPEVT=00000040` and `TEA` equal to `ACCESS_VA`;
- `COUNTER_CAPTURE_AFTER` equals (`COUNTER_BEFORE` + 1) modulo 55;
- the returned word matches the same physical flash word read through P1;
- the post-read P2 snapshot contains a valid 4 KiB translation for the
  candidate virtual page, with the expected physical page and round-robin slot;
- `COUNTER_AFTER_SNAPSHOT` still equals `COUNTER_CAPTURE_AFTER`, proving that
  the verification snapshot did not consume another mapping slot;
- `RESULT=PASS`.

A pass demonstrates one stock-handler cold **data** miss/refill for one
already-present add-in page on this exact OS/device. It does not prove D1
window refill, repeated misses, eviction policy under load, absent-page
handling, handler replacement/chaining, or gameplay integration. Keep
`Fxcg_RomInit()` fail-closed. The next stage is still a separately reviewed
exception-context design; do not install a custom handler based on this result
alone.
