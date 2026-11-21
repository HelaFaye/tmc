# D1 TLB-miss handler design and validation gates

Status: **design investigation only; no runnable replacement handler exists**.

`Fxcg_RomInit()` must remain fail-closed. The current target is an fx-CG50
running OS 3.20.0202. Do not copy OS 3.60 addresses, syscalls, RAM variables,
or table layouts into the target implementation until they have been checked
against that exact OS build.

## Existing project facts

- The proposed ROM window is `[0x20000000, 0x21000000)`, divided into 4096
  4 KiB pages.
- The project has tested access to the upper DRAM P2 alias
  `0xac4e0000..0xac800000` on the target calculator. The game `.xbss` uses the
  P1 range beginning at `0x8c4e0000`; the remaining capacity is finite and
  must be budgeted for page frames and resident pager metadata.
- `port/fxcg/fxcg.ld` keeps `.gint.tlbh` in the image and exports
  `_gint_tlbh_start` and `_gint_tlbh_size`. The existing 4-byte link probe
  proves that an application-provided section replaces the archive handler
  at link time. It does **not** prove runtime chaining or safe exception entry.
- The `P2-FLAG-ISOLATION-PROBE.g3a` tests synthetic SR flag-setting and
  readback. It does not cause a TLB exception, execute `LDTLB`, or test the
  exception stack/return path.
- The pinned CI emulator `Heath123/casio-emu` at
  `5d34e298b94be65f7ab0505d3c0f9b9ed802ac4b` does not implement real MMU
  translation or `LDTLB`. Its passing P2 test is only an instruction/SR
  regression, not a pager validation.

## Exception constraints

For the SH-4A, a TLB miss enters the TLB-miss vector at `VBR + 0x400`; it is
not the ordinary exception vector at `VBR + 0x100`. Exception entry saves
`SR` to `SSR`, the faulting/restart PC to `SPC`, and the prior `R15` to `SGR`,
then sets `MD`, `RB`, and `BL`. `R0-R7` are banked; `R8-R15` are not. The
SH-4A manual documents restrictions on privileged P4/TLB-array accesses and
requires the prescribed transition after `LDTLB`.

The most important operational constraint is `SR.BL`: a second exception
while `BL=1` can reset the physical calculator. Therefore the miss path must
not depend on pageable code or data, allocate memory, perform file I/O, invoke
the decompressor, wait for the ESP32 link, or call an ordinary function whose
code/data working set has not been proved resident.

## OS research reference — version-limited

The public CG50 Internals investigation describes **OS 3.60**, not the target
OS 3.20.0202. It reports the following OS 3.60 behavior:

- The OS maps add-in code beginning at `0x00300000` on demand using a page
  table at `0x8C04CF0C`.
- Its TLB-miss path is associated with syscall `0x000C` / handler address
  `0x8002C918`.
- The 4 KiB add-in code mappings use UTLB slots 0–54 with a round-robin
  counter; slots 55–63 hold add-in RAM/other permanent mappings.
- The OS exception vectors set `MD|RB|BL` on entry. The documented OS 3.60
  vectors save banked registers before switching to an ordinary handler.

These are useful leads, **not target constants**. In particular, do not hardcode
`0x8C04CF0C`, `0x8002C918`, syscall `0x000C`, the slot counter, or the slot
allocation policy for OS 3.20 until independently verified. The published
reverse-engineering notes also describe a later OS build, with details that
may have changed between versions.

References:

- [CG50 Internals: CPU and exception vectors](https://hexbinoct.github.io/casio-cg50/cpu/)
- [CG50 Internals: MMU and add-in launch](https://hexbinoct.github.io/casio-cg50/os/mmu-and-addins/)
- [Renesas SH-4A Software Manual](https://www.renesas.com/en/document/mah/sh-4a-software-manual-hardware)

## Handler design requirements

A production D1 handler must satisfy all of these requirements before it is
allowed to back `gRomData`:

1. **Resident entry path.** Every instruction and every datum touched before a
   valid translation is installed must be accessible during a TLB miss,
   independently of the faulting 0x20000000 window. The vector/trampoline
   placement and VBR lifecycle must be derived from the exact Gint source and
   verified linked image.
2. **Preserve Gint's existing miss semantics.** Gint's current
   `.gint.tlbh` is required for the add-in's own demand-paged code. Replacing
   the section removes that handler; the link-only probe has already shown
   that the section is a replacement hook, not an additive callback. The
   custom implementation must contain or safely delegate to the exact
   Gint default path for non-ROM faults. No guessed call to an OS syscall or
   OS address is acceptable.
3. **Classify using architectural state.** The handler must distinguish a
   miss in `[0x20000000, 0x21000000)` from an add-in code miss and every other
   fault. Use the documented exception cause and fault-address registers,
   with alignment/range checks before indexing metadata.
4. **Map resident frames only.** A ROM-window miss may map only a physical
   frame already resident in the page cache. Fetching a page, decoding PCPK,
   changing cache ownership, or waiting for the ESP32 must happen outside the
   TLB-miss context.
5. **Share UTLB replacement safely.** The hardware UTLB has 64 entries. Do not
   assume a free slot, or reserve slots 0–54 without proving how that interacts
   with Gint/OS add-in-code refills. Do not touch the permanent RAM mappings.
   A shared replacement policy must remain correct when ordinary Gint code
   misses and ROM-window misses interleave.
6. **Correct refill and return.** PTEH/PTEL/MMUCR.URC programming, `LDTLB`,
   post-`LDTLB` instruction sequencing, and `RTE` state restoration must be
   audited against the SH4AL-DSP manual and tested on the real chip. A faulting
   instruction must be retried only after a valid mapping is installed.
7. **Explicit miss failure.** If a ROM page is absent from the resident cache,
   the handler must not return to the same unmapped instruction and loop. A
   resident, non-recursive failure path must be designed and tested separately.

## Required source audit before coding

Inspect the exact installed Gint 2.11.0 checkout selected by the fxSDK:

- `src/kernel/tlbh.S`: full default TLB-miss handler, including every branch,
  literal and external dependency.
- `src/kernel/syscalls.S`: any OS service used by the handler and the exact
  calling convention/trap sequence.
- Gint kernel initialization: how `.gint.tlbh` is copied into the VBR region,
  when `VBR` is changed, and what memory remains accessible during the copy
  and after it.
- Linker script and built Picori ELF: `_gint_tlbh_start`, size, placement,
  symbols/relocations, and the actual instructions selected by the current
  toolchain.

Record the Gint commit hash and compare it with the expected source commit
`badbd0fd2bd8ac796fd55d49b93691741bd8a139`. Do not modify the user's Gint
checkout; it has independent local changes.

## Staged validation plan

| Gate | Test | What it establishes | What it does not establish |
|---|---|---|---|
| H0 | Read-only source/link audit | Exact Gint hook and VBR-copy semantics | Runtime correctness |
| H1 | Manual 4 KiB UTLB mapping probe, installed before access | PTEH/PTEL/MMUCR.URC, `LDTLB`, a known virtual-to-physical read | TLB-miss exception entry or delegation |
| H2 | Isolated one-page ROM-window miss with preloaded frame | Actual exception entry, resident refill, restart/return | Eviction or absent-page handling |
| H3 | Interleaved add-in code misses and ROM-window misses | Gint/default path coexistence and shared UTLB replacement | Long-run cache performance |
| H4 | Two-page boundary, repeated eviction/refill, cache exhaustion | Replacement invariants and deterministic failure policy | Gameplay integration |
| H5 | PCPK preload integration | Real metadata/cache path while all refill-time work remains resident-only | Full game correctness |

Every new hardware probe must be a separate add-in and must have a documented
recovery path. Never run a link-only `rte` stub or a probe whose failure mode
has not been reviewed. Keep `Fxcg_RomInit()` fail-closed through H4.

## Candidate higher-fidelity emulator

`hexbinoct/casio-cg50` is a separate, newer fx-CG50 emulator/reverse-engineering
project. Its Go/Python cores model UTLB translation, `LDTLB`, and precise
TLB-miss/protection exceptions, unlike the emulator pinned in this project's
current CI. It can boot an owner-supplied flash dump and run `.g3a` add-ins.
However, its OS analysis and end-to-end boot validation are centered on OS
3.60; another OS version is accepted with warnings but does not become verified
for OS 3.20.0202 by that fact alone. Its documented model also omits a real
ITLB and caches. Treat it as a valuable secondary test environment, not a
replacement for the target calculator.

- [hexbinoct/casio-cg50](https://github.com/hexbinoct/casio-cg50)
- [Project status and model limitations](https://github.com/hexbinoct/casio-cg50#status)

No firmware dump is included in or should be committed to this repository.
