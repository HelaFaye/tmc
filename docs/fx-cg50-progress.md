# fx-CG50 port: progress log

What has been done, measured and decided, newest first. The plan is in
[`fx-cg50-port-plan.md`](fx-cg50-port-plan.md). Update this file with every
step that changes what is known.

## Status

| Phase | State |
|---|---|
| 0. Measure and set up | ROM working set done; SH-4 toolchain built and checked; code size measured (about 1.0 MB of game code); device measurements and spikes not started |
| 1. The game on the calculator | **Current focus.** All 623 game files compile for the SH-4; linking against `port/fxcg/` next |
| 2. Launcher and ESP32 preloading | Link library and preload simulation done (fxLauncher `lib/cglink`); firmware and launcher not started, and deliberately wait for Phase 1 |
| 3-6 | Not started |

## Phase 1: what the game code needs from the port layer

Linking the PC build's `src/` objects alone leaves 1,357 undefined symbols,
all provided by `port/`. Grouped by what the calculator build does with them:

| Group | Files | Symbols | Calculator build |
|---|---|---|---|
| ROM data tables (generated) | `data_stubs_autogen.c`, `data_const_stubs.c`, `port_linked_stubs.c`, `stubs_autogen.c` | about 1,140 | compile unchanged |
| ROM, memory and game helpers | `port_rom.c`, `port_gba_mem.c`, `port_gameplay_stubs.c`, `port_draw.c`, `port_animation.c`, `port_text_render.c`, `port_math.c`, `port_hdma.c`, offset/region/script/room tables, `port_stubs.c` | about 120 | compile, with `port_rom.c` reading pages through the pager instead of a 16 MB buffer |
| GBA BIOS | `port_bios.c`: `VBlankIntrWait`, `CpuSet`, `LZ77UnComp*`, `Div`, affine set-up, `SoftReset` | 12 | reimplement in `port/fxcg/`: VBlank waits on a gint timer, the rest is plain C |
| Saves | `port_save.c`: `EEPROM*` and save-profile calls | 9 | reimplement: EEPROM image in RAM, written back through the launcher (storage memory during bring-up) |
| Sound engine entry points | `port_m4a_stubs.c`: `m4aSongNumStart`, `m4aSoundMain` and friends | 12 | the D2 hook: send audio events to the ESP32; no-ops without it |
| Assets | `port_asset_loader.cpp`: gfx and palette groups, map data | 6 | reimplement in C over the baked paks (D6) |
| Presentation and settings | `port_ppu.cpp`, `port_runtime_config.cpp` | 11 | reimplement: the D3 scaler to `dupdate`; fixed settings |
| PC-only features | randomizer, Reborn, TTS, level editor, debug actions, soft slots, `port_m4a_backend.cpp` | about 50 | stub (randomizer and soft slots could return later) |

So the calculator platform layer is small: BIOS, saves, audio hooks, assets,
presentation and settings, plus stubs for the PC-only features.

## Log

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
