# fx-CG50 port: progress log

What has been done, measured and decided, newest first. The plan is in
[`fx-cg50-port-plan.md`](fx-cg50-port-plan.md). Update this file with every
step that changes what is known.

## Status

| Phase | State |
|---|---|
| 0. Measure and set up | ROM working set done; SH-4 toolchain being built; device measurements, code size and spikes not started |
| 1. The game on the calculator | **Current focus.** Not started: waits for the toolchain |
| 2. Launcher and ESP32 preloading | Link library and preload simulation done (CasioMini3D `lib/cglink`); firmware and launcher not started, and deliberately wait for Phase 1 |
| 3-6 | Not started |

## Log

### 2026-11-17: toolchain, final working-set numbers

- The SH-4 toolchain (fxSDK, `sh-elf-gcc` 14.1.0, binutils 2.42, OpenLibm,
  fxlibc, gint) is built from the GiteaPC recipes. The development environment
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
- CasioMini3D `lib/cglink`: framing, pak reader, deflate decoder, page cache
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
