# fx-CG50 D1: ROM-window paging spike

Status: **blocked on a safe TLB-miss refill path**. CI proves the `.gint.tlbh`
link override and builds a UTLB snapshot add-in, but the snapshot currently
reads P4 UTLB arrays from P0 and is **not safe to run on hardware**. The ROM
reader remains fail-closed pending a corrected P2 snapshot and isolated refill
test.

## Findings

The planned `gRomData` view is a 16 MiB virtual ROM window at `0x20000000`.
That address is outside the ordinary add-in code mapping. It cannot be made
usable simply by opening the PCPK file and assigning the pointer.

On the SH-4A, a TLB miss takes the separate `VBR + 0x400` vector, while other
exceptions use `VBR + 0x100`. A normal exception callback is therefore not, by
itself, evidence that the data-window page fault can be handled. The calculator
linker script already retains Gint's `.gint.tlbh` section; D1 must integrate
with that refill hook. The handler must execute from memory that remains
addressable during exception entry. An MMU fault while `SR.BL` is set can reset
the calculator, so calling pageable game code from that handler is unsafe.

The published fx-CG50 OS 3.60 reverse-engineering notes describe an add-in
page table with 512 4 KiB entries (2 MiB), used to map code beginning at
`0x00300000`. That table does not describe a 16 MiB data window at
`0x20000000`. This is OS 3.60 research, not proof of the exact OS 3.20.0202
implementation on the target calculator. Re-check the target OS before relying
on any OS-internal address, table size, or syscall behavior.

References:

- [SH-4A exception vectors and TLB-miss behavior](https://hexbinoct.github.io/casio-cg50/cpu/)
- [fx-CG50 OS MMU and add-in page table investigation](https://hexbinoct.github.io/casio-cg50/os/mmu-and-addins/)

## Startup working set and ROM-dump side effects

The host-side working-set tooling records two phases. The corrected sweep
reports 116 distinct `load` pages (464 KiB) and a 506-page union of `play`
pages. The original PCPK v1 builder read only `play` lines, so it could omit
pages needed before the first game frame. Meanwhile the current
`TMC_FXCG` `Port_LoadRom()` path still reads `gRomData` during region detection,
ROM pointer-table resolution and data-stub initialization. Those reads happen
before the play-phase page manifests can be used. The D6 plan's statement that
load-time pointer tables are already replaced by baked data is ahead of the
current C implementation: these direct reads are still present.

The overlay therefore promotes the union of all successful runs' `load`
pages into every room set. PCPK v1's existing common-page baseline then
contains the complete measured startup set, while room manifests contain only
the additional room-specific pages. Rebuild the pak from the corrected 590-room
dataset before sizing the cache or claiming the pak is complete. Once generated
D6 data truly removes each startup read, the load baseline can be reduced only
after a new sweep and a coverage test prove it safe.

The audit also found that `Port_LogRomAccess()` writes every newly observed
page to `rom_data/`, and `Port_LoadRom()` performs a final bulk `rom_data/`
extraction. These are PC-side measurement/cache behaviours, not part of the
calculator's page-pak design. The overlay disables both for `TMC_EMBEDDED`,
so the fx-CG50 path does not try to create thousands of files or write ROM
pages to calculator storage during startup/gameplay.

## Gint source cross-check

The public [Gint 2.11.0 source snapshot](https://github.com/JumpSushi/CGType/tree/a72732fb52f08d9cf04476a55b9bcd7d66288bab)
provides a useful implementation reference:

- [`src/kernel/kernel.c`](https://github.com/JumpSushi/CGType/blob/a72732fb52f08d9cf04476a55b9bcd7d66288bab/src/kernel/kernel.c)
  copies the linked TLB handler to `VBR + 0x400` and then configures VBR.
- [`src/kernel/tlbh.S`](https://github.com/JumpSushi/CGType/blob/a72732fb52f08d9cf04476a55b9bcd7d66288bab/src/kernel/tlbh.S)
  handles the stock add-in code window through the CG OS syscall table and
  falls through to the general exception vector for addresses outside it.
- [`CMakeLists.txt`](https://github.com/JumpSushi/CGType/blob/a72732fb52f08d9cf04476a55b9bcd7d66288bab/CMakeLists.txt)
  declares Gint 2.11.0.

The local Gint checkout is now identified as version 2.11.0, commit
`badbd0fd2bd8ac796fd55d49b93691741bd8a139` (`GINT_HASH 0xbadbd0f`). The
public mirror remains a secondary cross-check rather than proof of identical
source. CI's cached GiteaPC package is still not tied to a recorded source
commit; use the local checkout for the exact implementation audit.

## SH4AL-DSP TLB access execution constraints

The Renesas [SH4AL-DSP Software Manual, Rev. 1.50](https://www.renesas.com/en/document/mas/sh4al-dsp-software-manual), §7.6 (printed page 235), states that memory-mapped ITLB/UTLB reads and writes must be issued by a privileged program executing in **P2**. Operation is not guaranteed when the MOV access is issued from another area. After a memory-mapped TLB access, software must execute an approved transition before accessing or fetching outside P2; the manual recommends an `RTE` branch or `ICBI`. The `IRMCR.MT=0` shortcut is not guaranteed for future SuperH implementations.

Section 7.4.3 (printed pages 226–227) separately specifies that `LDTLB` must execute from **P1 or P2**. It copies `PTEH` and `PTEL` into the UTLB entry selected by `MMUCR.URC`. After `LDTLB`, software must execute `RTE` or `ICBI` before accessing an address translated by the TLB. This gives us two possible privileged trampoline designs:

- a P2 routine for direct memory-mapped UTLB inspection or writes, with all intermediate buffers accessed through P2 aliases and an `ICBI` before returning to ordinary code;
- a P1/P2 routine for `LDTLB`, with deliberate `URC` ownership and an `ICBI` before returning to ordinary code.

The exception catcher itself executes in the normal add-in mapping (P0). It must not directly read or write the memory-mapped UTLB arrays, nor execute `LDTLB` in that P0 callback. It can validate `EXPEVT`/`TEA`, select an already-resident frame, and call a small assembly trampoline in the appropriate privileged area.

**The current read-only state probe is not yet safe to run on hardware.** `tests/fxcg50-tlb-probe/tlb_state_probe.c::snapshot()` reads `0xf6000000` and `0xf7000000` directly from ordinary C code. That code is not placed in P2. Its successful build proves compilation only; it does not validate the hardware reads. Move the 64-entry snapshot into a P2 routine before distributing or running a replacement artifact.

The final linked Picori ELF now resolves this discrepancy: `utlb_translate()` and `mmu_uram()` are both in P0 `.text`, not P2. See **Final ELF placement audit** below. The exact startup call path and behavior on the OS 3.20.0202 calculator still need hardware verification.

The TLB entry layout and initial target UTLB allocation policy remain unproven on OS 3.20.0202. Do not choose `URB`/`URC`, write entries, or enable `Fxcg_RomInit()` until the hardware snapshot has been corrected and captured.

## Final ELF placement audit: Gint's UTLB reader is in P0

The final linked calculator ELF was inspected after LTO, rather than relying
on the intermediate `mmu.c.obj` file:

| Symbol / section | Final ELF address | Placement |
|---|---:|---|
| `utlb_translate` | `0x003c457c` | `.text`, P0 |
| `mmu_uram` | `0x003c45f8` | `.text`, P0 |
| `.text` | `0x00300000`, size `0x000ce5d8` | P0 add-in code |
| `.ilram` | `0xe5200000`, size `0xa0` | Separate on-chip section; neither symbol is here |

The exact local Gint source (`badbd0fd2bd8ac796fd55d49b93691741bd8a139`,
`GINT_VERSION` 2.11.0, `GINT_HASH` `0xbadbd0f`) shows that `mmu_uram()` calls
`mmu_translate()`, which selects `utlb_translate()` on SH-4A/SH4AL hardware.
Gint's `kernel.c` calls `mmu_uram()` while calculating the fx-CG50 VBR during
`kinit()`. Consequently, the linked startup helper path contains UTLB
inspection code in P0, despite the SH4AL-DSP manual's P2-only guarantee for
memory-mapped UTLB array access.

This is a **specification/implementation discrepancy**, not proof that Gint
fails on the fx-CG50. The manual says P0 access is not guaranteed; the ELF
proves where the code is placed, but no target-hardware failure has been
observed from this audit. Do not infer that P0 access is safe merely because
Gint contains it. Before modifying Gint's startup path, verify whether this
path has been exercised successfully on the target OS 3.20.0202 calculator
and inspect the final instructions that dereference the P4 UTLB arrays.

The implementation choice for D1 remains open:

- direct memory-mapped UTLB reads/writes require a privileged P2 routine;
- `LDTLB` is specified for a routine executing in P1 or P2 and uses `MMUCR.URC`;
- either approach needs the required post-operation transition before code
  accesses or fetches outside the permitted area.

The 3,200 KiB upper-DRAM range has been tested through its P2 alias for data
read/write, but that RAMCheck result does **not** establish instruction fetch
or execution from the P2 alias. Any P2 trampoline must first pass a separate
copy/branch/return execution probe. A P1 trampoline is a possible route for
`LDTLB`, but it cannot perform direct memory-mapped UTLB array reads.

Do not run the current P0-based `TLB-STATE-PROBE.g3a` on hardware. Rework the
snapshot into a P2 routine first, and separately validate trampoline execution.
Do not patch Gint startup or enable `Fxcg_RomInit()` until the discrepancy is
understood on the target.

### Instruction-level confirmation from the final ELF (2026-11-20)

The final linked `port/fxcg/build-cg/picori` ELF was disassembled at the exact
symbol addresses reported by `sh-elf-nm`. This closes the remaining question
of whether the P0 placement merely reflected symbol metadata or whether the
actual P0 instructions dereference the memory-mapped UTLB arrays:

- `_utlb_translate` begins at `0x003c457c`. Its literal loads at `0x003c4580`
  and `0x003c4584` resolve to `0xf6000000` (UTLB address array) and
  `0xf7000000` (UTLB data array).
- At `0x003c458a`, the instruction `mov.l @r1,r1` reads an entry from the
  address array. At `0x003c4596`, `mov.l @r2,r2` reads the corresponding data
  array entry. The loop advances the array offset by `0x100` and scans 64
  entries. These are actual P4 array loads, not indirect calls to a P2 helper.
- `_mmu_uram` begins at `0x003c45f8`; it loads the `_utlb_translate` address
  (`0x003c457c`), passes `0x08100000` as the translation query, calls the
  helper, then ORs the returned address with `0x80000000` to form the P1 alias
  used for Gint's URAM/VBR setup.

Both routines are inside the P0 `.text` section beginning at `0x00300000`.
The disassembly therefore confirms that this linked Gint startup path performs
memory-mapped UTLB reads directly from P0. It does not make those accesses
conform to the SH4AL-DSP manual's P2 execution requirement. This remains a
specification/implementation discrepancy; it is not proof that the code fails
on the fx-CG50. No conclusion about runtime success should be drawn from the
ELF or emulator build alone.

The most useful next hardware observation is whether this exact Picori build
reaches its expected fail-closed ROM diagnostic screen on the OS 3.20.0202 calculator. Gint's `kinit()`
uses `mmu_uram()` before the application entry point, so reaching the expected diagnostic screen with this exact image would establish that the P0 reader completed in that
specific hardware/OS configuration. Record the tested `.g3a` SHA-256 and
result. Such an observation would not establish that P0 access is guaranteed
by the architecture, nor would it authorize a custom D1 refill handler to
access UTLB arrays from P0.

The existing `TLB-STATE-PROBE.g3a` remains blocked for hardware use: its
snapshot has the same P0 direct-read problem. A replacement snapshot must put
the memory-mapped reads in a P2 routine and separately pass the P2
instruction-execution test. Keep `Fxcg_RomInit()` fail-closed; do not write UTLB
entries or select `URB`/`URC` based on this disassembly.

## P2 execute-and-return precondition

Before any custom TLB-miss handler or D1 refill experiment, build and test the
separate `P2-EXEC-PROBE.g3a` described in
`docs/fx-cg50-p2-exec-probe.md`. It copies a three-instruction leaf routine to
`0xac7ff000`, verifies the bytes through P2, applies the SH-4A-required
`SYNCO; ICBI` sequence, and verifies a P2 instruction fetch plus `RTS` return
to the P0 caller. It does not touch the TLB or exception vectors. A successful
link is not a hardware pass; record the add-in SHA-256 and the result on the
target OS. Keep `Fxcg_RomInit()` fail-closed until the later exception-context
trampoline has also been independently validated.

## Synthetic TLB-miss SR-state P2 probe

The ordinary `P2-EXEC-PROBE.g3a` hardware gate passed on OS 3.20.0202 with
expected result `0x00000035`; its SHA-256 is recorded in
`docs/fx-cg50-p2-exec-probe.md`. The next independent gate is
`P2-BL-RB-PROBE.g3a`, which sets the supervisor-mode `SR.RB|SR.BL` bits and
calls the same leaf in P2 without generating an exception or touching the
TLB/VBR. This is reset-sensitive: Renesas specifies that an exception other
than user break while `SR.BL=1` causes reset behavior. Do not enable ROM paging
or install a real TLB handler based solely on this synthetic-state result.
See `docs/fx-cg50-p2-bl-rb-probe.md`.

## Required spike, before game integration

| Check | State | Evidence / next action |
|---|---|---|
| Application-provided `.gint.tlbh` wins the link against the CI fxSDK/Gint package | **Build-time PASS** | The link-only probe and ELF verifier pass in CI. The four-byte `rte; nop` probe is nonfunctional and must never be run. |
| UTLB snapshot diagnostic | **Build-time PASS; hardware use BLOCKED** | The existing artifact reads P4 UTLB arrays from P0 C code. Replace with a P2 snapshot routine before running it on the physical fx-CG50. |
| Gint 2.11.0 source-level behavior cross-check | **PARTIAL** | A public source mirror shows the handler copy to `VBR + 0x400`, the normal add-in code-window syscall path, and forwarding to `VBR + 0x100`; its commit is not yet matched to the installed package. |
| Exact installed Gint source/archive and target OS contract | **OPEN** | Match the locally installed archive to its source commit and confirm the OS 3.20.0202 syscall path, handler copy range, VBR transition, UTLB behavior, and memory/stack safety with `SR.BL` set. |
| Isolated resident-page refill | **OPEN** | Preload two known 4 KiB pages into tested upper DRAM, map adjacent virtual pages, read across the boundary, and force eviction/refill. Do not involve Picori's ROM call sites. |
| Failure and replacement behavior | **OPEN** | On target hardware, test repeated misses, UTLB replacement, cache exhaustion and an unmapped page. An unserviceable fault must fail safely; never return to the faulting instruction without a valid translation. |

The application-provided `.gint.tlbh` section replaces the stock handler;
it is not an additive callback. A custom implementation must preserve the
normal add-in code-window miss path (or provide an equivalent) as well as
handle the `0x20000000` ROM window, and must forward unrelated faults to the
normal exception path. Do not assume the game ROM window is the only source
of TLB misses.

The handler must map only a page frame that is already resident. File I/O and
raw-DEFLATE decoding must stay outside the TLB-miss context; loading and
decompression belong in explicit preloading at a safe execution point. The
policy for recovering from a cache miss while game code is running remains
undecided and must be designed separately rather than implemented in the
exception handler.

Only after the isolated diagnostic passes should PCPK metadata, cache
bookkeeping and per-room preloading be connected. If the Gint hook cannot be
replaced or safely chained on the target, use the documented accessor fallback
rather than shipping an unstable vector.

No custom TLB handler is introduced by this work. `port/fxcg/fxcg_rom.c` must
continue to fail initialization until the isolated refill test passes.

## 2026-11-20: stage-8 stall and straight-line P2 follow-up

The staged hardware build reached `8/8: Executing P2 wrapper` but did not
return to the result screen. The calculator was recovered with RESTART and
returned to the normal menu. This is a failed/inconclusive hardware result;
it does not establish whether the stall was at SR transition, protected
instruction fetch, nested P2 call/return, SR restoration, or return to C.

The next revision removes the nested `JSR`/`RTS` to a separate P2 leaf. The
copied wrapper now has a straight-line protected interval: after setting
`SR.RB|SR.BL`, it writes `0x35` to the selected bank's R0, copies that result
to unbanked R9, reads SR, computes a state-check bit in unbanked R11, and
restores the original SR. There is no branch, call, return, stack access,
literal/data load, or store while BL is set. It returns `0x135` only when the
R0 operation executed and both RB and BL read back set; `0x35` means the state
check did not pass. The C harness retains eight visible progress checkpoints.

This remains a reset-sensitive synthetic-state experiment. It must be built
and disassembled before hardware use. Do not infer real TLB exception entry,
VBR behavior, RTE safety, or refill correctness from this test.

### 2026-11-20: preserve banked R0 in synthetic BL/RB probe

A review of the straight-line template found that `mov #53,r0` executed with
`SR.RB=1`, so it modified `R0_BANK1`. Although the normal C register bank was
restored before returning, the alternate bank was not preserved. The corrected
template writes the test value to unbanked `R9` instead; the second instruction
slot is a `nop`, preserving the 60-byte copy size. No banked GPR is modified
while `RB=1`. The protected interval still has no memory loads/stores, calls,
branches, stack operations, or literal loads. Instruction fetches from the copied P2 template necessarily continue; they are not data-memory loads or stores. The C progress
text now says “No data-memory loads/stores while BL=1” to distinguish data-memory
accesses from register/control-register operations and instruction fetches.

This is a preservation correction, not an explanation for the earlier stage-8
stall. The prior straight-line artifact is superseded and must not be run.
Rebuild and review the new linked template before any further hardware test.

## BL=1 data-memory wording clarification

The synthetic P2 BL/RB probe has **no data-memory loads or stores while
`SR.BL=1`**. The protected interval also contains no stack accesses,
literal-pool loads, calls, branches, or returns. Instruction fetches from the
copied P2 template necessarily continue; they are not data-memory loads or
stores. `STC SR,Rn` and `LDC Rn,SR` are CPU-register/status-register transfers,
not data-memory accesses. The exact guarantee is that no data-memory loads or stores occur during the protected BL=1 interval. Instruction fetches from the copied P2 template necessarily continue; they are not data-memory accesses.

## Hardware finding: combined synthetic RB/BL probe required restart

The straight-line combined-state probe did not return from its stage-8 P2 call
and the calculator required a physical restart. Its linked template reveals a
specific defect: it ORed the test mask into R8 and then loaded that modified R8
into SR again at the purported restore point. The original SR was not restored;
RB/BL remained asserted through the epilogue and return. Therefore the hardware
run is invalid as a test of the protected interval and does not show that the
three protected instructions triggered a reset. The next gate separates
`SR.RB` from `SR.BL`: RB-only with BL clear, then BL-only with RB clear and only
three NOPs inside the protected interval. The corrected wrapper retains the
original SR in R8 and builds the temporary SR in R10. It does not attempt
combined state or a real exception. Keep the ROM window disabled until the
isolated hardware result and subsequent exception-entry/RTE behavior are
independently established.
