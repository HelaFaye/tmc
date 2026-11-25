# fx-CG50 port: progress log

What has been done, measured and decided, newest first. The plan is in
[`fx-cg50-port-plan.md`](fx-cg50-port-plan.md). The D1 refill constraints,
startup working-set finding and test sequence are in
[`fx-cg50-d1-paging-spike.md`](fx-cg50-d1-paging-spike.md). Update this file
with every step that changes what is known.

## Status

| Phase | State |
|---|---|
| 0. Measure and set up | ROM working set measured (590 validated rooms); SH-4 toolchain and 623/623 source compile verified; code size measured (about 1.0 MB); upper 3,200 KiB DRAM passed RAMCheck v2 on OS 3.20.0202. Device I/O/baud measurements and the D7 RAM-loader spike remain open; corrected PCPK rebuild remains pending |
| 1. The game on the calculator | **Current focus.** D1 pager implemented (`TMC_FXCG_D1_PAGER=ON`; see `fx-cg50-d1-pager.md`), host-tested, passing `D1-PAGER-PROBE` in the emulator MMU extension (CI); **not yet run on hardware**. Default build stays fail-closed. ROM-only page pak (1,140 pages, 2.63 MiB) built. Next: run the probe, then the pager build. Big-endian validation under way (`tests/fxcg50-bigendian`, see the 2026-11-25 entry); room `0x8a:0x02` hangs in ROM-only mode on the PC |
| 2. Launcher and ESP32 preloading | Link library and preload simulation done (fxLauncher `lib/cglink`); firmware and launcher not started, deliberately deferred until Phase 1 is playable |
| 3-6 | Not started |

## Phase 1: what the game code needs from the port layer

Linking the PC build's `src/` objects alone leaves 1,357 undefined symbols,
all provided by `port/`. Grouped by what the calculator build does with them:

| Group | Files | Symbols | Calculator build |
|---|---|---|---|
| ROM data tables (generated) | `data_stubs_autogen.c`, `data_const_stubs.c`, `port_linked_stubs.c`, `stubs_autogen.c` | about 1,140 | compile, except `data_const_stubs.c`: its symbols point into the ROM window |
| ROM, memory and game helpers | `port_rom.c`, `port_gba_mem.c`, `port_gameplay_stubs.c`, `port_draw.c`, `port_animation.c`, `port_text_render.c`, `port_math.c`, `port_hdma.c`, offset/region/script/room tables, `port_stubs.c` | about 120 | compile; `port_rom.c` reads the ROM through the D1 window (`TMC_ROM_WINDOW`) |
| GBA BIOS | `port_bios.c`: `VBlankIntrWait`, `CpuSet`, `LZ77UnComp*`, `Div`, affine set-up, `SoftReset` | 12 | **done**: `VBlankIntrWait` in `port/fxcg/fxcg_main.c` on a gint timer; the rest compiles from `port_bios.c` |
| Saves | `port_save.c`: `EEPROM*` and save-profile calls | 9 | **compiles** from `port_save.c` (file in storage memory); later through the launcher |
| Sound engine entry points | `port_m4a_stubs.c`: `m4aSongNumStart`, `m4aSoundMain` and friends | 12 | **silent** for now (`fxcg_stubs.c`); the D2 hook to the ESP32 later |
| Assets | `port_asset_loader.cpp`: gfx and palette groups, map data | 6 | **stubbed**: every call site falls back to the ROM, so no asset paks are needed |
| Presentation and settings | `port_ppu.cpp`, `port_runtime_config.cpp` | 11 | **done**: the D3 scaler in `fxcg_main.c`; fixed settings in `fxcg_stubs.c` |
| PC-only features | randomizer, Reborn, TTS, level editor, debug actions, soft slots, `port_m4a_backend.cpp` | about 50 | **stubbed** in `fxcg_stubs.c` (randomizer and soft slots could return later) |

So the calculator platform layer is small: BIOS, saves, audio hooks, assets,
presentation and settings, plus stubs for the PC-only features.

### 2026-11-20: SH4AL-DSP memory-mapped TLB execution constraint

The Renesas [SH4AL-DSP Software Manual, Rev. 1.50](https://www.renesas.com/en/document/mas/sh4al-dsp-software-manual) resolves the execution-context question:

- §7.6 (printed page 235): software reads/writes the memory-mapped ITLB/UTLB only from a privileged program executing in P2. Access issued from another area is explicitly not guaranteed. After the access, execute `RTE` or `ICBI` before returning to an area outside P2; the `IRMCR.MT=0` shortcut is not the portable choice.
- §7.4.3 (printed pages 226–227): `LDTLB` must execute from P1 or P2, selects the destination entry through `MMUCR.URC`, and requires `RTE` or `ICBI` before accessing a TLB-translated area again.

Consequences for D1:

- The exception catcher runs in the normal add-in mapping (P0). It may classify the exception and select an already-resident frame, but it cannot directly access the memory-mapped UTLB arrays or execute `LDTLB` from that callback.
- A small assembly trampoline is required. Use P2 for memory-mapped UTLB array access, or P1/P2 for an `LDTLB` implementation. The trampoline must obey the post-access `ICBI`/`RTE` rule and avoid P0 stack/buffer accesses after the TLB operation until that transition has occurred.
- The existing `tlb_state_probe.c::snapshot()` reads UTLB arrays from ordinary C/P0 code. Its artifact is build-verified only and must not be run as the hardware snapshot until those reads are moved into a P2 trampoline.
- Gint's exact local `src/mmu/mmu.c` also reads UTLB arrays through ordinary C. `GINLINE` is `always_inline inline`, not a P2 placement annotation. Its object is LTO IR, so the empty `.text` section and unhelpful `objdump -dr` output do not reveal the final instruction address. Inspect the final Picori ELF and section map; reconcile this source path with the manual before relying on it.

No UTLB entries have been written, no replacement policy has been selected, and `Fxcg_RomInit()` remains fail-closed. Hardware snapshot and refill experiments remain outstanding.

### 2026-11-20: SH4AL-DSP UTLB access rule versus final ELF placement

The target-specific [Renesas SH4AL-DSP Software Manual, Rev. 1.50](https://www.renesas.com/en/document/mas/sh4al-dsp-software-manual)
is now the primary reference. It matches the SH4AL-DSP MMU family named in
Gint's `include/gint/mpu/mmu.h`:

- §7.6 (printed page 235): memory-mapped ITLB/UTLB arrays may be read or
  written only by a privileged program executing in P2; access from another
  area is not guaranteed. After the access, software must perform the
  prescribed transition before accessing/fetching outside P2.
- §7.4.3 (printed pages 226–227): `LDTLB` must execute from P1 or P2, selects
  an entry using `MMUCR.URC`, and requires a post-operation transition before
  returning to code using TLB-translated addresses.

The local Gint checkout is identified as version `2.11.0`, commit
`badbd0fd2bd8ac796fd55d49b93691741bd8a139` (`GINT_HASH 0xbadbd0f`). The final
Picori ELF, built with LTO, places `utlb_translate` at `0x003c457c` and
`mmu_uram` at `0x003c45f8`; both are in `.text` at `0x00300000` (P0), not in
P2. Gint's `mmu_uram()` calls `mmu_translate()`/`utlb_translate()` for the
SH4AL path, and `kinit()` uses `mmu_uram()` to determine the VBR location.

This exposes a specification/implementation discrepancy in the existing
Gint startup path. It is not by itself evidence of a hardware failure: the
manual says the behavior is not guaranteed, while the ELF establishes only
placement. Verify the actual call path on the OS 3.20.0202 calculator before
deciding whether Gint startup needs a P2 reader. The old LTO object was
insufficient for this audit; final linked ELF placement is now established.

The existing `TLB-STATE-PROBE.g3a` reads the P4 UTLB arrays from P0 C code and
must not be run on hardware. Replace that snapshot with a P2 routine. The
upper-DRAM P2 alias has only been data-tested; instruction fetch/execute from
that alias needs a separate probe. For D1, keep both installation options open:
direct UTLB array access in P2, or `LDTLB` from P1/P2. No entries have been
written, no `URB`/`URC` policy has been selected, and `Fxcg_RomInit()` remains
fail-closed.

### 2026-11-20: final-ELF instructions confirm P0 UTLB reads

The final linked `port/fxcg/build-cg/picori` ELF was disassembled around
`_utlb_translate` (`0x003c457c`) and `_mmu_uram` (`0x003c45f8`):

- `_utlb_translate` loads the UTLB array bases `0xf6000000` and `0xf7000000`.
- Its instructions at `0x003c458a` (`mov.l @r1,r1`) and `0x003c4596`
  (`mov.l @r2,r2`) directly read those P4 arrays while executing in the P0
  `.text` section. The code scans 64 entries with a `0x100`-byte stride.
- `_mmu_uram` calls `_utlb_translate(0x08100000, NULL)` and ORs the result
  with `0x80000000` to produce the P1 alias used by Gint's URAM/VBR setup.

This is instruction-level confirmation of the P0/P2 specification discrepancy,
not evidence of a runtime failure. The ELF establishes the call and placement;
it cannot establish whether the access succeeds on OS 3.20.0202 hardware. Since
Gint calls `mmu_uram()` during `kinit()`, a successful launch of this exact
Picori image to its application UI on the physical calculator would be useful
empirical evidence for this particular target configuration. Record the
`.g3a` SHA-256 and result. It would not change the architectural constraint for
a new TLB handler or make P0 access portable.

`TLB-STATE-PROBE.g3a` is still unsafe to run: its snapshot also performs P0
P4-array reads. A corrected snapshot must move those reads to P2 and pass a
separate P2 instruction-execution probe. No UTLB entries have been written,
no `URB`/`URC` policy has been selected, and `Fxcg_RomInit()` remains fail-closed.

## Log

### 2026-11-25: big-endian validation of the calculator configuration

`tests/fxcg50-bigendian` builds the calculator configuration of the game
(`TMC_EMBEDDED`, `TMC_ROM_WINDOW`, the fx-CG50 frontend with a host stand-in
for gint) twice: big-endian 32-bit PowerPC run under `qemu-ppc`, and
little-endian 32-bit x86. Only byte order differs, so a frame hash that
differs points at a byte-order bug in code the calculator runs.
`run_compare.sh` builds both, runs an input script and prints the first
differing frame; `HOST_DUMP_FRAME` dumps palettes, OAM, I/O and VRAM for
a frame.

- **Input scripts:** `input-title.txt` (3,000 frames: title, file select,
  name entry) matches on every frame. `input-intro.txt` (12,000 frames:
  new file, save, file view, the intro cutscene and the first rooms)
  differs on 63 of 12,000 frames (8609-8671) and matches before and after
  them. Being worked on.
- **Fixed so far:** the PPU was never bound to the GBA memories on the
  calculator (black screen); bit-fields get the GBA layout on big-endian
  (`scripts/fxcg/be_bitfields.py`, 47 runs; `Dialog` by hand); I/O, EWRAM
  and IWRAM hold host-order values; text tables, glyph words, sprite frame
  tables, script operands and several ROM pointer tables are read
  little-endian; tilemap entries keep GBA byte order (`TILE_LE16`);
  ROM records read in place (`RoomHeader`, `EntityData`, `TileEntity`,
  `Transition`) are declared with GCC's little-endian scalar storage order
  (`GBA_LE_STRUCT`); room map, tile-type and tile-index arrays are swapped
  to host order as they load, while `subTiles` (copied into tilemaps)
  keeps GBA order; window HDMA tables are built as host halfwords.
- **32-bit (not byte-order) bugs found on the way:** a native `Font` misread
  as a GBA blob, and room map data landing 4 bytes off; both are now
  fixed, so the 32-bit little-endian build renders Link's house like the
  PC build.
- **Calculator address clash:** the add-in's RAM and stack
  (`0x08100000-0x0817FFFF`) lie inside the GBA ROM range, so
  `port_resolve_addr` would have turned a stack buffer handed to
  `DmaCopy` into a ROM address. That range is now treated as native on
  `TMC_FXCG`. The host harness cannot reproduce this one (its program is
  linked at `0x40000000`).

### 2026-11-23: D1 pager passes in the emulator; CI runs it

- **Local smoke test:** the pinned `Heath123/casio-emu` was built here, and
  the existing Picori smoke test passed: `PICORI_CASIO_EMU_RESULT=PASS`.
- **Probe on the stock emulator:** `D1-PAGER-PROBE` stopped at the OS
  syscall dispatcher. The emulator has no OS and no MMU.
- **Test-only MMU extension:** added to the harness (`--d1-pager`,
  `mmu_window.c`). It is spliced into a copy of the emulator sources at
  configure time, so the pinned checkout and the existing tests are
  unchanged.
- **Probe result with the extension:** all eight stages PASS, with 6,184
  window misses, 3,093 syscall 0x09 mappings, 3,084 evictions and
  `REGS_MISMATCH_MASK=0000`.
- **Planted bugs:** with UTLB invalidation removed, the eviction stage
  fails. With r5 not restored in the upcall, stages 2-4 fail
  (`REGS_MISMATCH_MASK=0020`).
- **CI workflow:** `fxcg50-emulator.yml` now checks out `external/fxLauncher`
  and runs the probe in the emulator. It requires `RESULT=PASS`, all eight
  stages and a zero register mask, and uploads the report. It then builds
  the hardware variants (`D1-PAGER-PROBE.g3a`, and `Picori-D1.g3a` with the
  pager) and uploads them as `fxcg50-d1-pager-hardware`, without running
  them.

### 2026-11-23: D1 pager built; hardware results folded in

Hardware files received (OS 3.20.0202): `TLBSNAP`, `TLBSLOT`, `RAMCHECK`,
`PCPKLOG`, `OS-PAGING`, `D1-MAP-PROBE` (PASS), `COLD-DATA-TLB-MISS` (PASS),
and `D1TLBH.TXT`, which is 650 zero bytes: a run that died before writing.

Design decided by those results (details in `fx-cg50-d1-pager.md`):
- No `LDTLB` anywhere. UTLB 55-62 are the permanent add-in RAM mappings and
  `URB=63`, so a refill at `URC` could unmap the stack.
- **Resident window pages:** mapped from the TLB-miss handler with OS syscall
  `0x09` and flags `0x6C`, the call the mapper probe validated, in the OS's
  own slot pool.
- **Pages not yet resident:** fetched by an upcall in normal context, never
  inside the exception.
- **Eviction:** clears the UTLB entry from P2.
- **Add-in misses:** gint's stock `0x0C` path, unchanged.

Added:
- **Pager sources:** `port/fxcg/fxcg_tlbh.S` (replaces `.gint.tlbh`; 0x120
  bytes, linker-asserted at most 0x200), `fxcg_p2_tlb.S`, and the pager in
  `fxcg_rom.c`.
- **Probe:** `tests/fxcg50-d1-pager-probe/` for `D1-PAGER-PROBE.g3a`, eight
  stages, with the report saved after each one.
- **Host test:** `port/fxcg/tests/test_rom_cache.c`. It passed on the new pak:
  1,140 pages byte-identical, 3,416 evictions, pinned pages kept.
- **Inflate dependency:** `external/fxLauncher` submodule, for `cglink_inflate`.
- **Build options:** `TMC_FXCG_D1_PAGER` and `TMC_FXCG_D1_PAGER_PROBE`, both
  off by default. The default `Picori.g3a` still uses gint's handler
  (`_gint_tlbh_size` 0x70) and fails closed.

**The earlier pak would have failed on the calculator.**
- The calculator has no asset loader. The PC loaded texts, area tables and
  sprite pointers from extracted assets, so its sweeps never touched those
  ROM pages.
- `TMC_ROM_ONLY=1` in `port_asset_loader.cpp` makes the PC take the same ROM
  fallbacks. A boot run then touched 54 play pages instead of 15.
- The ROM-only sweep (`rom_touch_sweep.sh` now sets it) and the new boot
  capture (`rom_touch_boot.sh`, passed with `build_page_pak.py --boot=`)
  give:

| | Asset-backed pak (earlier) | ROM-only pak |
|---|---:|---:|
| Validated rooms | 590 | 590 |
| Unique pages | 582 | 1,140 (4.45 MiB raw) |
| Pak size | 0.89 MiB | 2.63 MiB |
| Pinned baseline | 128 | 165 (234 KiB compressed) |
| Per room beyond baseline | - | median 36 KiB, p90 66 KiB, max 132 KiB |

Room `0x8a:0x02` runs to completion with assets, but under `TMC_ROM_ONLY`
it hangs until the 300 s timeout (exit 137), after
`ResolveRomPtr: address 0x00880000 is outside ROM`. It is left out of this
pak. It may be a bug in the ROM fallback path the calculator uses, and is
not yet investigated.

**Still open, and probably the next blocker after paging:** byte order. The
game reads ROM structures in place (room headers, scripts, entity lists).
Only a few paths have `TMC_BE` swaps, so on the big-endian SH-4 other
multi-byte ROM fields may read wrong. No big-endian run of the game has been
done yet (plan Phase 0 item 2).

### 2026-11-20: exact Picori artifact for the Gint startup check

Candidate hardware-test image: `port/fxcg/Picori.g3a`. The local `sha256sum` output on 2026-11-20 reported SHA-256 `ee0b7692fc77e39f168aa026a77f9c82bd51c4e29c7dd3847a70197c2f086d0d`. This identifies the image to test; it is **not** a recorded hardware result.

Because `port/fxcg/fxcg_rom.c::Fxcg_RomInit()` intentionally returns `false`, the expected screen after successful Gint startup and entry into `Fxcg_GameMain()` is:

- `Picori: cannot open the ROM pages.`
- `ROM window (plan D1) not built yet.`

Reaching this diagnostic establishes that execution passed Gint startup and reached Picori's fail-closed ROM-initialization path. It does **not** mean ROM paging works or that the game booted. Record the observed result against this exact hash. A hang, reset, blank screen, or different message is a different result. **Do not run `TLB-STATE-PROBE.g3a`.**

### 2026-11-20: Picori startup diagnostic confirmed on hardware

On the fx-CG50 running OS 3.20.0202, the user confirmed that Picori rendered
its expected fail-closed diagnostic (`Picori: cannot open the ROM pages.` /
`ROM window (plan D1) not built yet.`). Pressing EXE dismissed the screen and
returned from the add-in. The earlier report that no key closed it was not
reproduced on the subsequent run.

This confirms Gint startup, entry into `Fxcg_GameMain()`, the intentional
`Fxcg_RomInit()` failure path, and the normal key-wait/return path. It does not
validate ROM paging or game startup. The candidate SHA-256 in the preceding
startup-artifact record was not re-read alongside this observation, so the
hardware result is recorded without claiming a newly verified hash binding.

### 2026-11-20: ordinary P2 execute/return hardware gate passed

On the fx-CG50 running OS 3.20.0202, the user ran the exact
`P2-EXEC-PROBE.g3a` artifact and confirmed `P2 execute + return: PASS` with
`0x00000035`. The artifact SHA-256 was
`10e0cc372bfd215e46467ee184ebf542e4f705c84ea2bd39321c67ff212adfb4`.
The result validates the tested six-byte P2 instruction fetch, `RTS` return,
and resume in P0 on this calculator. It does not validate the TLB-miss
exception vector or `SR.BL=1` execution. The next separate diagnostic is the
synthetic MD/RB/BL P2 probe; see `docs/fx-cg50-p2-bl-rb-probe.md`.

### 2026-11-20: D1 gate audit and CI state

The current `fx-cg50` HEAD is `c2357c913eb180abdcc37946c848d759ffdc916d`
(`fx-cg50: add read-only UTLB state probe`).

- The fx-CG50 emulator workflow run
  [44501057061](https://github.com/HelaFaye/tmc/actions/runs/44501057061)
  passed. It built and linked `Picori.g3a`, built the Gint TLB-hook link probe
  and checked its ELF, built/uploaded `TLB-STATE-PROBE.g3a`, and booted Picori
  in the headless emulator. The read-only probe artifact is
  [14517202532](https://github.com/HelaFaye/tmc/actions/runs/44501057061).
  This is a build/emulator result, not evidence that the UTLB snapshot has
  been read on hardware or that a custom handler can refill a ROM page.
- The general CI run
  [44501057235](https://github.com/HelaFaye/tmc/actions/runs/44501057235)
  passed the fx-CG50 memory-layout validator and host validation tests. Its
  Android arm64/x86_64 job failed after `libmain.so` compiled and linked:
  the subsequent `xmake build -y tmc_pc` process exited with SIGSEGV (139).
  This is an unresolved CI/tooling failure, not a reported SH-4 compile or
  emulator failure. A job retry was rejected by GitHub with HTTP 403, so it
  has not been rerun.
- CI's CMake configure log found Gint 2.11.0 (minimum 2.11). The
  `.gint.tlbh` link probe verifies that the application-provided `_gint_tlbh`
  is the first byte of a four-byte handler section, replacing the archive
  member at link time. The workflow pins the `casio-cg50` recipe commit, but
  its Dockerfile installs Gint through GiteaPC without pinning a source
  commit. In the recorded run, both GiteaPC install layers were Docker-cache
  hits, so the exact Gint source commit used by CI is not recorded. The
  `rte; nop` stub is nonfunctional and must never be executed.
- A public Gint source mirror whose CMake project declares version 2.11.0
  shows `kinit()` copying the `.gint.tlbh` bytes to `VBR + 0x400` before
  switching VBR. Its stock CG miss handler checks the fault address and
  services the normal add-in code window (`0x00300000` through
  `0x00300000 + _srom`) through syscall 12 at `0x80020070`; faults outside
  that range are forwarded to `VBR + 0x100`. This is a source-level
  cross-check, not proof that the mirror is the exact commit in the local
  or CI-installed archive. See the source links in the D1 spike document.
- The original `TLB-STATE-PROBE.g3a` was built to record MMUCR, PTEH, PTEL,
  VBR and all 64 UTLB address/data entries, but its current implementation
  reads the P4 arrays from P0. **Do not run this artifact on hardware.**
  First validate instruction execution from the upper-DRAM P2 alias; then
  move the snapshot reads into a P2 routine, rebuild, and only then capture
  all four display pages and the register summary on OS 3.20.0202.
- D1 remains blocked until the exact Gint 2.11.0 hook/exception behavior and
  the target UTLB allocation/replacement policy are understood. Next order:
  hardware snapshot; isolated two-resident-page mapping/boundary-read test;
  repeated refill and eviction; cache exhaustion and unmapped-page failure.
  The exception path may map only an already-resident frame: file I/O and
  DEFLATE stay outside TLB-miss context. PCPK/game integration follows only
  after those tests pass. `Fxcg_RomInit()` remains fail-closed.

### 2026-11-18: corrected post-intro ROM working-set sweep

The full corrected sweep attempted 592 area/room pairs from 74 named areas.
The harness validated 590 destinations after settling; two attempts were
rejected (`0x49/0x00` did not arm its warp, and `0x89/0x06` redirected to
`0x88/0x1a`). The 590 validated runs touched 506 unique play pages
(2,072,576 bytes; 1.98 MiB), with 15 minimum, 21 median and 79 maximum
pages per room (Hyrule Town `0x02/0x01`, 316 KiB). The clean load-phase
union remains 116 pages (464 KiB). The earlier 478-page / 0.74 MB pak
figures came from the prior sweep and are superseded. The page pak, common
baseline and compressed-size figures have not yet been rebuilt from this
corrected dataset; rerun the host packer before using those figures for
calculator storage or preload budgeting.

### 2026-11-18: upper DRAM validated on fx-CG50 OS 3.20.0202

`tools/fxcg50-ramcheck/RAMCheck.g3a` v2 ran on the target calculator and
passed the complete candidate region through the uncached P2 alias. The
saved report recorded:

| Measure | Result |
|---|---:|
| P1 range used by Picori `.xbss` | `0x8c4e0000..0x8c800000` |
| P2 alias used by RAMCheck | `0xac4e0000..0xac800000` |
| Physical range / capacity | `0x0c4e0000..0x0c800000` / 3,200 KiB |
| 4 KiB blocks | 800 / 800 |
| Checks passed | 12,001 |
| Failed checks | 0 |
| Restore failures | 0 |
| Result | **PASS** |

The probe tested byte, halfword and word patterns, within-block address
patterns, walking data bits and distinct cross-block page signatures. It
saved and verified the original contents. The linked `.xbss` is 1,539,760
bytes, ending at `0x8c657eb0`, leaving 1,737,040 bytes in the configured
region. This validates read/write access and restoration on this calculator
and OS version during the test session; it is not a guarantee about every
OS version or future lifecycle. The physical DRAM backing Picori's P1
linker region is hardware-tested on the target OS 3.20.0202 through P2.


### 2026-11-18: Picori.g3a links

`cd port/fxcg && fxsdk build-cg` builds `Picori.g3a` with fxSDK and gint:
the 619 game files, 31 files of the PC port's portable layer compiled
unchanged, and the new calculator platform in `port/fxcg/`.

| Section | Size | Where it goes |
|---|---|---|
| Code (`.text`) | 845 KB | add-in ROM mapping (2 MB limit) |
| Read-only data | 303 KB | add-in ROM mapping |
| So the add-in file | 1.15 MB | storage memory (later: the launcher loads it from SD, D7) |
| Game BSS | 1.54 MB | `sysram`, see below |
| gint's static RAM (491 KB) | almost all free | page cache and heap |

`port/fxcg/`:
- `fxcg_sys.c` is the gint side: keyboard (D-pad on the arrows, A = SHIFT,
  B = ALPHA, L = F1, R = F2, Select = F5, Start = F6 or EXE; MENU visits the
  main menu), a 60 Hz timer for VBlank, `dupdate`, and `main()`.
  `fxcg_main.c` is the game side: `VBlankIntrWait` (buttons into KEYINPUT,
  render, the D3 scaler 240x160 -> 336x224 into RGB565 VRAM, wait for the
  tick, `VBlankIntr`) and the start-up. They are split because gint's `u32`
  is `unsigned long` and the game's is `unsigned int`; no file includes
  both, and `fxcg_sys.h` uses only standard types.
- `fxcg_stubs.c` switches off the PC-only features with the values the PC
  port uses when they are off: asset loader (every call site falls back to
  the ROM), randomizer, Reborn, TTS, soft slots, level editor, bug reporter,
  window settings, analog stick, debug menu. Sound (the m4a backend) is
  silent until the ESP32 plays it (D2).
- `fxcg_libc.c`: `getenv` (no environment: every `TMC_*` switch is off) and
  `readlink`, the only C library functions fxlibc lacks.
- `fxcg_rom.c` is a placeholder: the add-in shows "ROM window (plan D1) not
  built yet" and waits for a key. The D1 window is the next step.
- `fxcg.ld`, `rom_symbols.ld`, `CMakeLists.txt`, `sources.cmake` (from
  `scripts/fxcg/gen_sources.sh`), and two plain icons.

Shared files, all guarded so the PC build is unchanged (three objects
byte-identical, `port_bios.o` the same functions in a different order):
- `port_bios.c`: its SDL frontend half is under `#ifndef TMC_EMBEDDED`; the
  BIOS half (`CpuSet`, `LZ77UnComp*`, `Div`, `Sqrt`, affine set-up,
  `SoftReset`, `RegisterRamReset`) compiles for the calculator as it is.
- `port_save.c`: no `fsync` on embedded targets. On big-endian targets the
  save-image checks read the game's structs natively, as `src/save.c` does,
  so calculator saves are big-endian and not interchangeable with PC saves.
- `include/port_target.h`: `TMC_FXCG` also implies `TMC_ROM_WINDOW`: the
  ROM sits at a fixed address and the linker places ROM-resident symbols in
  it. `port_rom.c` and `port_linked_stubs.c` then neither copy `gMapData`
  into RAM nor fall back to compiled-in ROM tables.

**ROM tables stay out of the add-in.** The PC build compiles
`port/data_const_stubs.c`, 400 tables copied from the ROM (0.99 MB). The
calculator build leaves it out; `scripts/fxcg/gen_rom_symbols.py` writes
`rom_symbols.ld`, which defines each of those symbols, and `gMapData`, at
`FXCG_ROM_WINDOW` (0x20000000) plus its ROM offset. The tables are read
through the same paged window as the rest of the ROM, the add-in shrinks by
1 MB, and it contains no ROM data: only names and offsets. The two remaining
compiled-in fallbacks (`kFrameObjListsData`, `kExtraFrameOffsetsData`) are
unreferenced on the calculator and dropped by the linker.

**sysram placement corrected from the hardware memory map.** The game's own BSS
(GBA EWRAM 256 KB, VRAM 96 KB, `gFrameObjLists` 200 KB, PPU frame/line
snapshots and map buffers) is about 1.54 MB. The old `0x8c200000` placement
is unsafe on newer OS versions: that DRAM area is cleared/used by the OS.
`fxcg.ld` now places the BSS at `0x8c4e0000`, the P1 alias of physical
`0x0c4e0000`, in the upper 3,200 KiB that the fx-CG50 memory investigation
found untouched by the OS. The linker asserts that `.xbss` fits. RAMCheck v2 passed the complete
3,200 KiB range through the uncached P2 alias on the target fx-CG50 running
OS 3.20.0202: 800/800 4 KiB blocks, 12,001 checks, zero failed checks and
zero restore failures. The linked `.xbss` ends at `0x8c657eb0`, leaving
1,737,040 bytes in the configured region. This validates read/write access
and restoration on this calculator and OS during the test session; it does
not guarantee the same behavior on other OS versions or future lifecycles.
`fxcg_main.c` clears this region at startup.

Open risks found while linking:
- **Byte order of ROM structures read in place.** Code that reads ROM data
  through typed pointers (for example `gFrameObjLists[i]` as a `u32` in
  `port_draw.c`) gets little-endian values on a big-endian CPU unless a
  `TMC_BE` swap exists on that path. Phase 0 item 2 (a big-endian build
  under qemu, frame hashes against the PC) finds these.
- **Frame time.** VirtuaPPU renders 32-bit pixels that are then converted
  to RGB565; rendering straight to RGB565 is the first optimisation once the
  game runs.

Next: the D1 window in `fxcg_rom.c`: a TLB-miss handler chained in front of
gint's, a page cache in gint's static RAM, and pages read from the page pak
in storage memory.

### 2026-11-18: the whole game code compiles for the fx-CG50

`scripts/fxcg/sh_compile_check.sh` compiles every file of the game build
(the PC build's own `src/` list, 619 files, plus the 4 generated data-stub
files) with `sh-elf-gcc -m4-nofpu -mb -Os -DTMC_FXCG`: **623 of 623 compile**.

| | Size (per-file objects, not linked) |
|---|---|
| Code and read-only data | 2.03 MB |
| of which `data_const_stubs.c` (constant ROM tables) | 0.99 MB |
| So game code and its own read-only data | about 1.0 MB |
| Zero-initialised RAM (bss) | 1.63 MB, mostly `gMapData` (1 MB) and `gFrameObjLists` (200 KB) |

What changed to get there:
- `PORT_PTR64` in `include/global.h`: 88 padding blocks across 83 files that
  added 4 or 8 bytes under `PC_PORT` to make room for 64-bit pointers now
  depend on pointer size. On 32-bit targets the structs keep the GBA layout,
  and the existing layout asserts check exactly that.
- `include/port_target.h`: `TMC_FXCG` implies `TMC_EMBEDDED` (no SDL,
  threads or OpenMP; 32-bit types) and `TMC_BE` (big-endian data swaps). The
  `TMC_N64` guards were split between them; the N64 boot markers in
  `src/main.c` stay N64-only. `fade.c`'s halfword reads go under `TMC_BE`,
  since they also fix byte order.
- `port/port_ppu.h` no longer needs SDL on embedded targets.

The 64-bit PC build is unchanged: all 736 of its object files compile to
identical code before and after (disassembly compared), and Hyrule Town
touches the same 80 ROM pages during play.

Correction: the load-phase figure from the room sweep (2277 pages, 8.9 MB)
came from the `rom_data/` page cache the sweep's run directories reused. A
clean run touches 116 pages (464 KB) while loading.

Next: link. The undefined symbols are the port layer `port/fxcg/` provides
(BIOS, saves, m4a hooks, assets, presentation, settings) plus stubs for the
PC-only features; see "Phase 1: what the game code needs" above.

### 2026-11-17: first SH-4 compile of the game code

`sh-elf-gcc -m4-nofpu -mb -Os` over `src/` and the generated data stubs,
with the PC defines plus `TMC_N64` (its 32-bit big-endian guards): 537 of 625
files compile. Per-file objects, not linked, so sizes are upper bounds.

| | Size |
|---|---|
| Code and read-only data, all compiled files | 1.81 MB |
| of which `data_const_stubs.c` (constant ROM tables) | 0.99 MB |
| So game code and its own read-only data | about 0.8 MB (from 86% of the files) |
| Zero-initialised RAM (bss) | 1.62 MB, almost all in `port_linked_stubs.c`: `gMapData` 1 MB, `gFrameObjLists` 200 KB, `gCollisionMtx` 70 KB, `gMapTop` 48 KB |

What this means:
- Game code fits comfortably in RAM next to a page cache (D7).
- The constant ROM tables (about 1 MB) are data, not code: they can come
  from the page pak instead of the executable.
- `gMapData` and `gFrameObjLists` are copies the PC port makes of ROM data;
  on the calculator they should read through the page cache instead of
  occupying 1.2 MB of fixed RAM.

The 88 failures are Phase 1's first list:
- generated headers the PC build creates (`assets/gfx_offsets.h`,
  `assets/map_offsets.h`), to generate for the calculator build too;
- static assertions on entity layouts that encode the PC's 64-bit pointer
  padding ("Enemy::child +4 pad"); they need a 32-bit variant;
- the rest are to be triaged.

### 2026-11-17: repositories split, toolchain working

- The graphics and launcher work is now split into repositories:
  HelaFaye/fxOGL (renamed from CasioMini3D), fxGLMark, fxglxgears, fxSDL and
  fxLauncher (launcher, `lib/cglink` link library, ESP32 firmware). The
  Picori port stays here.
- The SH-4 toolchain is verified: fxGLMark builds for the fx-CG50 (536 KB
  `.g3a`). That surfaced two fxOGL bugs, now fixed: fxGLMark's add-in was
  missing a source file and did not link, and fxOGL's CMake leaked the
  calculator's C headers into host builds on machines with fxSDK installed.
- libprof's GiteaPC recipe also builds for the ClassPad (`fxCP`), which gint
  was not built for; install it with `fxsdk build-cg install` from its
  GiteaPC directory instead.

### 2026-11-17: toolchain, final working-set numbers

- The SH-4 toolchain (fxSDK, `sh-elf-gcc` 14.1.0, binutils 2.42, OpenLibm,
  fxlibc, gint, libprof) is built from the GiteaPC recipes. The development environment
  needed `gitea.planet-casio.com`, `git.planet-casio.com` and `ftp.gnu.org`
  allowed. The recipes download from `ftpmirror.gnu.org`, which redirects to
  arbitrary mirrors, so the archives are fetched from `ftp.gnu.org` first and
  placed where the recipes look for them.
- Room sweep finished: 592 rooms. The game reads 478 ROM pages (1.87 MB)
  across them; the page pak is 0.74 MB. Details in the plan's Phase 0
  results.
- Decided: get the game running on the calculator (Phase 1) before any more
  ESP32 loading work.

### 2026-11-17: launcher architecture, preload simulation

- Decided: one launcher add-in owns the ESP32 link and runs everything loaded
  from the SD card (plan D7); internal storage holds only the launcher.
- `scripts/fxcg/build_page_pak.py` builds the page pak: each touched page
  deflated on its own, a resident baseline, a manifest per room. Compressors
  compared on per-room chunks: LZMA 32%, zstd 37%, deflate 39%, LZ4 51%.
  Deflate chosen for its small, fast decoder.
- `lib/cglink` (now in HelaFaye/fxLauncher): framing, pak reader, deflate decoder, page cache
  and page streaming, host-tested; `cglink_sim` showed that prefetching the
  next room leaves only the launch preload as a stall, even at 115,200 baud.

### 2026-11-17: ROM working-set measurement

- `port/port_rom_touch.c` (`TMC_ROM_TOUCH_LOG`): protects the ROM buffer and
  records the first access to each 4 KB page, for loading and for play. It
  sees the direct pointer arithmetic into `gRomData` too.
- `scripts/fxcg/rom_touch_sweep.sh` and `rom_touch_report.py`: boot a fresh
  game per room headless through the `TMC_ROOMCAP` harness and summarise.
- Building the PC port headless here needed xmake 3.1 built from source (the
  Ubuntu package is too old for the package repository), SDL 3.4.12 built
  from source as a console-only library (`SDL_UNIX_CONSOLE_BUILD`), and
  `XMAKE_USE_SYSTEM_SDL3=1`.

### 2026-11-16: plan

- The `fx-cg50` branch started identical to `master`.
- Module: ESP32-S3FH4R2 (4 MB flash, 2 MB PSRAM); no level shifter, so the
  calculator's TX level must be measured before wiring.
- A USA ROM matching `tmc.sha1` is available to the development session and
  kept out of every repository.

### 2026-11-20: synthetic BL/RB probe stalled; simplified follow-up

The staged `P2-BL-RB-PROBE.g3a` reached its final `8/8: Executing P2 wrapper` screen but did not return. The calculator was recovered with RESTART. The result is failed/inconclusive; the original nested-call test must not be run again. A replacement removes the nested P2 call/return and performs only register operations during the synthetic `SR.RB|SR.BL` interval. It remains unapproved for hardware until the new linked assembly is reviewed.

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

### 2026-11-20: combined synthetic RB/BL hardware probe required restart

The latest straight-line `P2-BL-RB-PROBE.g3a` reached its final stage-8 screen
and required a physical calculator restart; it did not return to the result
screen. Inspection of the linked template exposed a concrete bug: the code
modified R8 with the test mask, then loaded that same modified R8 into SR at
the purported restore point. The wrapper never restored the original SR and
left RB/BL asserted through its epilogue and return. The run is therefore
invalid as a test of the protected interval. A distinctly named
`P2-FLAG-ISOLATION-PROBE.g3a` now separates RB-only from BL-only and preserves
the original SR in R8 while constructing the temporary SR in R10. The new
binary must be built and disassembled before any hardware run. Do not rerun the
old combined probe; D1 remains fail-closed.
