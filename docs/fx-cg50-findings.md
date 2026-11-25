# fx-CG50 port: findings and audit

Running record of what we learned, for the port and for improving fx-CG50
emulation. Update with every finding.

## Hardware facts (OS 3.20.0202)

- UTLB 0-54: OS round-robin 4 KiB add-in pages; 55-62 permanent 64 KiB
  mappings of add-in RAM 0x08100000-0x0817FFFF; 63 maps VA 0; URB = 63.
- Syscall 0x09 (r4 VA, r5 PA, r6 0x6C) maps a 4 KiB page into slots 0-54.
- Syscall 0x0C serves add-in page misses (gint's stock TLB path).
- sysram 0x8c4e0000-0x8c800000 passes RAMCheck; 0x8c200000 hard-crashes.
- Memory-mapped UTLB array access must run from P2, then SYNCO + ICBI.

## What emulators need to model (casio-emu gaps found)

- No MMU/UTLB: add-in code at 0x00300000 and any VA window need TLB-miss
  exceptions, TEA/PTEH, the memory-mapped UTLB arrays (0xF6000000) and
  LDTLB. Our test-only extension (`tests/fxcg50-emulator/.../mmu_window.c`)
  covers only the D1 window.
- No OS syscalls: 0x09 and 0x0C (dispatcher 0x80020070) are needed for
  any add-in that pages; the extension fakes them.
- Permanent UTLB 55-62 and URB = 63 should be reproduced, so a raw LDTLB
  that clobbers the add-in RAM mapping fails like on hardware.
- Virtually indexed operand cache (index bit 12 aliasing) is not modelled;
  parity bugs in frame placement would pass in emulation.
- sysram ranges the OS uses are not modelled: writes to 0x8c200000 work in
  emulation but crash hardware.
- Fault-context file I/O through gint world switches is not exercised.

## Port pitfalls found (big-endian / 32-bit)

- Add-in RAM and stack (0x0810xxxx) overlap the GBA ROM range; any
  "is this a GBA address" test must exclude it on TMC_FXCG.
- 64-bit assumptions: MapLayer +4 offset, Font GBA-blob heuristic.
- ROM bytes read natively: text tables, glyphs, scripts, sprite frames,
  room records, pointer tables. Fix with Port_ReadU16/32 or GBA_LE_STRUCT.
- Tilemaps stay GBA byte order (TILE_LE16); map/tile-type arrays and
  palettes are host order (swapped at load).
- Byte-built HDMA tables and u32-stored/s16-read buffers change meaning.
- Double swaps: LoadPalettes on data already in gPaletteBuffer.
- be_bitfields.py skips enum-typed bit-fields (Dialog fixed by hand).

## Audit of our 38 commits (2026-11-25)

- CI: PC builds (Linux x86_64/arm64, Windows x86_64/arm64, Android) and the
  fx-CG50 emulator job pass. macOS x86_64 fails because xmake segfaults
  (exit -11) before compiling: runner issue, not our code.
- CI has no ROM (ASSETS_TOKEN / vars.ASSETS_REPO unset on this fork), so
  ROM-based checks (PPU parity, BE harness) do not run there.
- Pager runtime (fxcg_rom.c, fxcg_tlbh.S, fxcg_p2_tlb.S): no bugs found.
  Frame parity matches window parity; all handler data is in P1 sysram;
  no interrupt path touches the window.
- Risk: gint_osmenu() lets the user run other apps; nothing guarantees
  they leave sysram (game BSS, ROM frames) intact.
- Risk: window pages are mapped writable; a stray ROM write sticks.
- Bit-field reversal is used only for host-order hardware structs (BgCnt,
  PlttData, OamData, SIO); ROM structs use the storage-order wrapper.
  Reversed blocks in definitions.h are in the non-PC_PORT branch (unused).
- port_resolve_addr exclusion: no GBA ROM literals in 0x0810-0x0817xxxx.
- Open: BE intro frames 8609-8671 differ (VRAM tilemap byte order).
- Remaining audit scope: tools, probes, CI workflow details.
