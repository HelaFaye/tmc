# fx-CG50 D1: OS TLB slot-allocation sweep

## Purpose

The initial hardware snapshot shows valid UTLB entries in slots 0-6 and 55-63, with slots 7-54 invalid at that instant. This probe tests whether normal add-in ROM demand paging on the target OS uses the apparent 0-54 code-page pool while preserving the add-in RAM mappings in slots 55-62 and the low-memory mapping in slot 63.

This is a hardware diagnostic for the calculator's installed OS 3.20.0202. The documented OS 3.60 layout is a comparison only; it is not treated as proof of OS 3.20 internals.

## What it does

1. Reads the linker-defined add-in ROM span (`_brom`, `_srom`).
2. Performs one volatile 32-bit read at the beginning of every 4 KiB page in that span. These ordinary reads deliberately cause data-side TLB misses for add-in pages that are not currently mapped.
3. Leaves all page faults to Gint's existing stock `.gint.tlbh` handler and the calculator OS. It does not install a new handler.
4. Uses the existing P2 helper to read the UTLB and ITLB arrays after the sweep.
5. Saves the snapshot and sweep metadata to `\\fls0\\TLBSLOT.TXT`.

The checksum is only a record that the page reads occurred; it is not a ROM integrity check.

## What it does not do

- It does not write MMUCR, PTEH, PTEL, UTLB or ITLB entries.
- It does not change URB, URC, LRUI, VBR or the exception handler.
- It does not map the D1 window or access the page-cache frames.
- It does not call the OS mapping syscalls directly.

The normal OS paging mechanism will naturally update TLB state as the probe reads add-in pages. That is the behavior under investigation.

## Build

Configure the existing `port/fxcg/build-cg` directory with `TMC_FXCG_TLB_SLOT_SWEEP_PROBE=ON`, then build target `fxcg_tlb_slot_sweep_probe`. The output add-in is `port/fxcg/TLB-SLOT-SWEEP-PROBE.g3a`.

This test is intended for the real fx-CG50. The pinned CI emulator does not implement the MMU/TLB behavior being measured.

## Interpretation

A useful result should have:

- `SWEEP_STATUS=OK` and a nonzero `PAGES` count;
- valid 4 KiB add-in page mappings populated among UTLB entries 0-54;
- the eight 64 KiB user-RAM mappings in entries 55-62 unchanged;
- the 64 KiB low-memory mapping in entry 63 unchanged.

If slots 55-63 change, stop: do not attempt D1 mapping on the basis of this test. If add-in mappings appear in 55-63 or the result is incomplete, preserve the log and investigate OS 3.20 behavior before proceeding.

The sweep establishes the observed slot range used by the stock page-fault path. It does not by itself prove that D1 mappings can safely share that pool; that still requires an integrated fault-handler design and targeted tests.
