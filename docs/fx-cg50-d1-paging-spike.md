# fx-CG50 D1: ROM-window paging spike

Status: **blocked on a safe TLB-miss refill path**. The current ROM reader is
intentionally fail-closed; this document records the next experiment and the
constraints discovered during the branch audit.

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

## Required spike, before game integration

1. Inspect the exact Gint 2.11.0 archive selected by the installed fxSDK. Verify
   how `.gint.tlbh` is copied into the VBR area and whether an application can
   replace or extend it without linking a second conflicting handler.
2. Build a small isolated diagnostic. Preload two known 4 KiB pages into the
   hardware-tested upper-DRAM range, map adjacent virtual pages, read across
   their boundary, then force TLB eviction and verify refill. Do not involve
   Picori's ROM call sites in this first test.
3. Keep file I/O and raw-DEFLATE decoding outside the TLB-miss context. The
   refill path should map only a frame that is already resident in the cache;
   page loading and decompression must happen during explicit preloading.
4. Test repeated refill, eviction, cache exhaustion, and an unmapped-page
   failure on the target OS. An unserviceable fault must fail safely rather
   than return to the faulting instruction without a valid translation.
5. Only after the diagnostic passes, connect PCPK metadata, cache bookkeeping,
   and per-room preloading. If the Gint hook cannot be replaced safely, use the
   documented accessor fallback rather than shipping an unstable vector.

No custom TLB handler is introduced by this work. `port/fxcg/fxcg_rom.c` must
continue to fail initialization until the isolated refill test passes.
