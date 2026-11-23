# fx-CG50 D1 pager: the ROM window

Status: **implemented, built, host-tested; not yet run on hardware.** The
default build stays fail-closed. The pager is built only with
`-DTMC_FXCG_D1_PAGER=ON`; its hardware test add-in only with
`-DTMC_FXCG_D1_PAGER_PROBE=ON`.

## What the hardware results decided

| Result (OS 3.20.0202) | Consequence for the design |
|---|---|
| `TLBSNAP`/`TLBSLOT`: UTLB 0-54 hold 4 KiB add-in pages; 55-62 are permanent 64 KiB add-in RAM mappings (`0x08100000`-`0x0817FFFF`); 63 maps VA 0. `URB=63` | A raw `LDTLB` at `MMUCR.URC` can land on 55-63 and unmap the stack. The pager never executes `LDTLB`. |
| `D1-MAP-PROBE`: PASS. Syscall `0x09` (r4 VA, r5 physical, r6 `0x6C`) mapped 8 window pages at `0x20000000` into OS slots 9-16; CRCs matched | Window pages are mapped with the OS's own mapper, in the OS's own round-robin pool. |
| `COLD-DATA-TLB-MISS`: PASS. A data read of a cold add-in page went through gint's stock handler (syscall `0x0C`) and returned the right word | The stock path is kept, instruction for instruction, for add-in misses. |
| `RAMCHECK`: PASS over `0x8c4e0000-0x8c800000` | Frames, the page table and the save area live in sysram (P1). |
| `PCPKLOG`: PASS. A PCPK v1 pak read and every page decoded from storage memory | The page source is `/Picori.pak` read with stdio. |
| `D1TLBH.TXT`: 650 zero bytes | A run that died before its report was written. Every probe stage below saves its report before the next starts. |

## How it works

`gRomData = 0x20000000`, a 16 MiB window with nothing mapped.

`port/fxcg/fxcg_tlbh.S` replaces gint's `.gint.tlbh`. gint's `kinit()` copies
it to `VBR+0x400`, so it runs from P1; `fxcg.ld` asserts that it fits in the
`0x200` bytes before the interrupt entry at `VBR+0x600` (it is `0x120`). On a
TLB miss:

1. **Add-in code/data** (`0x00300000` to `0x00300000 + _srom`): gint 2.11.0's
   path, syscall `0x0C`.
2. **Window page whose frame is resident** (`fxcg_rom_phys[page] != 0`):
   syscall `0x09` maps the page to its frame with flags `0x6C`, as the mapper
   probe did, then `RTE`.
3. **Window page not resident:** no file I/O in exception context. The handler
   stores SPC, SSR and the page in `fxcg_rom_save` (P1) and returns into the
   upcall stub, part of the same copied section, with `IMASK = 15` and
   `BL = 0`. The stub saves r0-r7, PR, MACH and MACL, restores the game's SR,
   and calls `Fxcg_RomServiceFault()` in normal context. That reads the page
   from `/Picori.pak`, inflates it into a frame and records the frame. The
   stub then masks interrupts, restores every register, loads SPC and SSR,
   and `RTE`s to the faulting instruction. That instruction misses again and
   takes path 2. Between masking and `RTE`, the stub touches only P1 (its own
   code and `fxcg_rom_save`), so nothing can fault.
4. **Anything else:** gint's panic path (`VBR+0x100`).

`port/fxcg/fxcg_rom.c` keeps the page cache:

- 352 frames (1.38 MiB). The game's sysram use with them is 3.01 MiB of the
  3.2 MiB RAMCheck tested.
- Frames are split by page parity. The SH-4A operand cache is virtually
  indexed, and a frame is written through P1 but read through the window, so
  both aliases need the same index bit 12.
- The pak's baseline pages (load phase plus boot capture) are loaded at start
  and pinned, up to three quarters of each parity's frames. The rest rotate
  first in, first out.
- Evicting a frame first drops its old window page from the UTLB.
  `fxcg_p2_tlb.S` is copied to a P2 buffer and run there (SH4AL-DSP
  manual 7.6). It clears V in the address-array entry with that VPN, then
  runs `SYNCO` and `ICBI` before returning. This is a write version of the P2
  snapshot routine that ran on hardware.

## Hardware test order

1. **`D1-PAGER-PROBE.g3a`** (`-DTMC_FXCG_D1_PAGER_PROBE=ON`). The same
   handler, P2 helper and cache code, with generated pages (word at offset
   `o` of page `p` = `(p << 16) ^ o ^ 0x5A000000`) and 8 frames. It writes
   `/D1-PAGER.TXT` after every stage:

   | Stage | Tests |
   |---|---|
   | `TLBH_INSTALLED` | `VBR+0x400` holds this handler |
   | `1_RESIDENT_MAP` | Path 2: syscall `0x09` from the miss handler |
   | `2_UPCALL` | Path 3: upcall, fetch, retry |
   | `3_REGISTERS` | r0-r7, r14, r15, MACH, MACL, GBR and T unchanged across an upcall (`REGS_MISMATCH_MASK=0000`) |
   | `4_BOUNDARY` | Last word of one page, first of the next |
   | `5_EVICTION` | 64 pages through 8 frames, forward and back: a stale UTLB entry would return another page's words |
   | `6_INTERLEAVE` | Window misses between add-in data misses (stock path) over 48 add-in pages |
   | `7_STRESS` | 3,000 pseudo-random reads over 256 pages |
   | `8_PINNED` | The pinned page survived |

   On a crash, the last `STAGE` line in the file is the last one that
   completed.
2. **`Picori.g3a` built with `-DTMC_FXCG_D1_PAGER=ON`**, only after the probe
   passes, with `/Picori.pak` in storage memory (see
   `fx-cg50-data-prep.md`). A ROM page missing from the pak stops the game
   with `ROM page N (0x...) is not in Picori.pak.`

## Known limits

- Window pages are mapped writable (flags `0x6C`, as validated). A game write
  to ROM, which a GBA ignores, would change the cached copy of that page.
- The fault service reads storage memory through gint's world switch from
  whatever point the game was at when it missed. The `PCPKLOG` reader did the
  same reads, but not from inside a fault.
- The emulator in CI does not model the MMU, so CI only builds these
  targets.
