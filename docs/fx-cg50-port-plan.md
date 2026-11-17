# Project Picori on the Casio fx-CG50: port plan

Status: plan, November 2026. Nothing below is implemented yet. When this plan
was written, the `fx-cg50` branch pointed at the same commit as `master`
(`08f3ada`).

Progress, measurements and decisions as they happen are logged in
[`fx-cg50-progress.md`](fx-cg50-progress.md).

## Goal

Run The Minish Cap, built from this decompilation, as a native fx-CG50 add-in.
It is also the first program to use an **ESP32-S3 SuperMini with a microSD
breakout**, plugged into the calculator's 2.5 mm port. The ESP32 adds:

- storage;
- installing and streaming applications and data from the SD card;
- Wi-Fi file transfer;
- audio output;
- later, a link to Celio and GB-Link netplay.

Picori proves the storage side. Pokémon Emerald comes later and proves online
multiplayer, against mGBA running the US ROM.

As with the PC port, the player supplies the ROM. No ROM data is ever
distributed.

## Base

[zeldaret/tmc](https://github.com/zeldaret/tmc) is the decompilation on its
own: matching C for the GBA. Project Picori (this repository) builds it as a
native PC game and adds the port layer, region support and portability fixes
(64-bit pointers, the N64 work for big-endian 32-bit). The calculator port
starts from Picori's tree, on the `fx-cg50` branch, because the game code here
already runs outside the GBA. Picori's PC-only parts (SDL, ImGui, the GPU
renderer, RetroAchievements and so on) stay out of the calculator build.

## What the code base gives us

| Finding | Where | Consequence |
|---|---|---|
| The PC port loads the whole 16 MB ROM into `gRomData` | `port/port_rom.c` | It cannot fit in fx-CG50 RAM. A paged ROM is required (D1). |
| 121 sites in `src/` and `port/` do pointer arithmetic straight into `gRomData` | `grep gRomData` | Paging must be transparent to that code, or every site must be converted. |
| ROM pages are already 4 KB units, with a per-session "touched" bitmap (`sExtractedPages`) | `port/port_rom.c` | The working set can be measured on PC with no new mechanism. |
| An abandoned N64 target left `TMC_N64` guards: 32-bit types, big-endian ROM reads (`RomLE32`), a 240×160 PPU with a GBA-sized VRAM, no SDL | `port/port_types.h`, `include/gba/types.h`, `port/port_rom.c`, `port/ppu/include/virtuappu.h`, `src/main.c` | The SH-4A is also 32-bit and big-endian. That work is the starting point, generalised from "N64" to "32-bit big-endian embedded". The platform directory `port/n64/` itself is gone. |
| The PPU (`port/ppu/src/mode1.c`) is integer-only | `grep float` | It suits a CPU without an FPU. |
| agbplay (audio) uses floats in 8 of its 16 source files | `libs/agbplay_core` | On the SH-4A every float operation is a library call. Audio runs better on the ESP32-S3, which has an FPU (D2). |
| Game RAM is small: EWRAM 256 KB, IWRAM 32 KB, VRAM 96 KB | `port/port_gba_mem.c` | Fits comfortably. |
| Assets are JSON-indexed files (`assets/*.json`, about 3.4 MB) with a pak loader | `port/port_asset_pak*.cpp` | Bake everything to binary paks at build time; no JSON parsing on the calculator. |
| The port layer is C++17 with SDL3, ImGui, the GPU renderer, RetroAchievements, Discord, TTS and more | `port/` | The calculator gets its own small C platform layer, and those subsystems stay out of the build. |

## Target hardware

Values marked *measure* are not known well enough to design around yet.

**fx-CG50**

- **CPU:** SH-4A (SH7305), 32-bit big-endian, about 118 MHz stock. Overclocking
  is possible with Ptune-style tools; *measure* whether it is stable for long
  play.
- **No FPU.** fxOGL's measurements (HelaFaye/fxOGL) show what that costs.
- **RAM:** 8 MB in total. The amount gint can give an add-in is *measure*. The
  plan assumes at most 3 MB for the ROM cache plus game state.
- **Display:** 396×224, RGB565, sent with gint's `dupdate`. Full-frame transfer
  time: *measure*.
- **Storage memory:** about 16 MB of flash, shared with the user's other files.
  Read throughput through gint/BFile: *measure*.
- **2.5 mm port:** 3-pin serial (TX, RX, ground) on the SH7305 SCIF.
  - Logic levels: *measure with a scope* before connecting the ESP32.
  - The OS serial API tops out at 115,200 baud. The highest reliable baud
    driving the SCIF directly: *measure*.
- **USB:** Mini-B; gint has a USB driver (fxlink).

**ESP32-S3 SuperMini**

- **CPU:** dual-core 240 MHz with an FPU.
- **Radios:** Wi-Fi and BLE.
- **USB:** OTG, which can act as a USB host.
- **Module:** ESP32-S3FH4R2, so 4 MB of in-package flash and 2 MB of
  in-package quad PSRAM. The PSRAM holds the page cache for streaming and the
  audio buffers.
- **SD card:** a breakout on SPI.

### Wiring without a level shifter

There is no level shifter. ESP32-S3 GPIOs are 3.3 V and **not 5 V tolerant**,
so before anything is connected:

1. Measure the calculator's TX line at idle (high) with a multimeter, with
   the calculator running an add-in that has opened the serial port.
2. Connect directly only if it reads 3.3 V or less. Put about 1 kΩ in series
   with each signal line to limit current while the two boards disagree, for
   example during power-up.
3. If it reads above 3.6 V, connect nothing. Use a level shifter after all, or
   at least a resistor divider on the calculator-TX to ESP32-RX line.
4. Connect ground first.

Record the measured voltage here when it is taken.

### Suggested pinout (not decided)

The constraints:

- avoid the strapping pins: GPIO 0, 3, 45 and 46;
- leave the USB pins (GPIO 19 and 20) free for USB OTG (D4's fallback);
- keep GPIO 43 and 44 for the UART0 console;
- GPIO 48 is the board's RGB LED;
- the in-package PSRAM does not use any exposed pins.

| Function | GPIO | Note |
|---|---|---|
| SD CS / MOSI / SCK / MISO | 10 / 11 / 12 / 13 | SPI2 (FSPI) on its native IO_MUX pins, the fastest SPI path |
| Calculator link TX → calculator RX | 5 | UART1, about 1 kΩ in series |
| Calculator link RX ← calculator TX | 6 | UART1, about 1 kΩ in series; check the voltage first |
| I2S BCLK / WS / DOUT (Phase 4) | 7 / 8 / 9 | to an I2S DAC such as a PCM5102A or MAX98357A |
| Spare | 1, 2, 4 | |
| USB D− / D+ | 19 / 20 | reserved for USB host |

## Architecture

One **launcher** add-in is the only program installed on the calculator. It
owns the ESP32 link and runs every application it loads from the SD card.
Applications such as Picori are never installed into storage memory.

```
 fx-CG50                                              ESP32-S3 SuperMini
 ┌──────────────────────────────────────────┐         ┌───────────────────────────┐
 │ Launcher (the one installed .g3a, gint)  │         │ SD card: apps, page paks, │
 │   link client (SLIP+CRC)                 │ 2.5 mm  │   audio data, saves       │
 │   page cache in RAM + preloader          │ serial  │ link service              │
 │   decompressor (deflate)                 │◄───────►│ page server (per pak)     │
 │   app loader: load, relocate, run        │         │ audio: agbplay -> I2S     │
 │   service table for apps                 │         │ Wi-Fi file transfer       │
 │ ┌──────────────────────────────────────┐ │         │ later: GB-Link/Celio      │
 │ │ App in RAM (Picori): src/ + port/fxcg│ │         └───────────────────────────┘
 │ │ reads ROM pages through the launcher │ │
 │ └──────────────────────────────────────┘ │
 └──────────────────────────────────────────┘
```

Internal storage use is kept to a minimum: the launcher's `.g3a` and small
settings files. Game code, game data and saves live on the SD card and reach
the calculator through the launcher. (While bringing the game up, before the
link works, a development build may keep its page pak in storage memory.)

### Decisions

**D1. ROM access: a 16 MB window backed by the launcher's page cache.**

- `gRomData` points at a virtual 16 MB range, so the 121 direct pointer sites
  keep working unchanged.
- A TLB-miss handler maps each 4 KB ROM page to its copy in the RAM page
  cache. The SH-4 UTLB has only 64 entries, so a software refill from a page
  table is needed anyway.
- A page that is not cached is a miss: the game waits while the launcher
  fetches it. Preloading (D4) exists to make misses rare.
- **Fallback:** if gint's exception handling makes this impractical, convert
  the 121 sites to an accessor macro. The PC build can check that the
  converted code is exactly equivalent.

**D2. Audio runs on the ESP32.**

- The calculator sends m4a engine events: song start and stop, sound effects,
  volume and tempo, stamped with the game frame.
- The ESP32 runs agbplay from audio data on its SD card and drives an I2S DAC
  or PWM. Audio data never crosses the link.

**D3. Video: VirtuaPPU at 240×160, scaled to 336×224 (×1.4) or shown 1:1.**

- The ×1.4 scale fills the screen height: two of every five rows and columns
  are duplicated.
- Optionally, widescreen 384×160 at 1:1, using the existing widescreen support.
- Game logic always runs at 60 ticks per second. If it has to, rendering drops
  to every second frame. Frame time is a main performance risk.

**D4. Transport and preloading: the 2.5 mm port, with room-level preloading.**

- **Framing:** SLIP with a CRC, reusing `link_proto.{h,c}` from
  HelaFaye/gbagate, where it is already tested.
- **Link speed** (8N1, ten bits per byte, before protocol overhead): about
  11 KB/s at 115,200 baud, the OS's limit; about 90 KB/s at 921,600 baud, if the
  SH7305 SCIF runs that fast when driven directly. The real limit is a Phase 0
  device measurement.
- **What a room needs** is measured (see Phase 0 results): a small baseline
  that stays resident, plus each room's own pages, compressed.
- **Preloading:**
  - At launch, the baseline and the starting room's pages are loaded before
    the game starts.
  - During play, the preloader fetches the pages of the rooms reachable from
    the current one (room exits come from the ROM's exit lists) in the
    background, nearest first, while the link is otherwise idle.
  - Each room's manifest says which pages to fetch. Rooms visited in the
    measurement runs have exact manifests; for others the pager falls back to
    fetching on a miss, and the manifests improve as more of the game is
    measured.
  - Cache eviction is least recently used, never evicting the baseline or the
    current room.
- At 115,200 baud a median room takes about 1.5 s to fetch. Room-level
  preloading is required, and fast room changes may still stall. Above about
  460,800 baud even fetching a room on arrival takes under a second. The
  ESP32-S3's USB host mode, talking to gint's USB device, is the fallback if
  the serial port can't run fast enough.

**D5. Build: fxSDK and gint, with a C-only platform layer.**

- `port/fxcg/` in this repository holds Picori's calculator platform: main
  loop, video, keyboard, timers and the calls into the launcher's services.
- The `TMC_N64` guards become `TMC_EMBEDDED` (32-bit, no SDL) plus `TMC_BE`
  (big-endian ROM reads). The calculator sets `TMC_FXCG` on top.
- The build follows HelaFaye/fxGLMark's CMake (`fxsdk build-cg`).

**D6. Data: a compressed page pak plus baked asset paks.**

- `scripts/fxcg/build_page_pak.py` stores every measured ROM page once,
  compressed with raw deflate on its own, with an index, the resident baseline
  and a manifest per room. The format carries a codec ID.
- Per-room chunks compressed to: deflate 39%, zstd 37%, LZMA 32%, LZ4 51%.
  Single 4 KB pages compressed independently average 37% with deflate, so
  page granularity costs almost nothing.
- Deflate comes first: its decoder is small and fast without an FPU. LZMA saves
  another sixth of the link time, if the SH-4A has the cycles to spare.
- Pointer tables the PC port resolves at load time are resolved by the build
  tools instead and shipped baked, so the calculator does not need the 8.9 MB
  the PC port touches while loading.
- The calculator never parses JSON.

**D7. The launcher runs applications from RAM.**

- An application is a relocatable executable built with the fxSDK toolchain
  and stored on the SD card. The launcher loads it into RAM, applies its
  relocations and calls its entry point with a table of launcher services:
  page access and preloading, files, saves, audio events, time and input.
- The launcher stays resident, so the link, the cache and the preloader keep
  running under every application.
- Application code then uses RAM, so the RAM budget covers the launcher, the
  application's code, the page cache and the game's own state. Picori's code
  size on SH-4 must be measured early.
- Prior art exists for loading executables into RAM on Casio calculators.
  Whether gint allows running code from RAM is proven by a spike in Phase 0.

## Phases

Each phase ends with something measured or running, not just written. The
order: first the game running on the calculator (Phase 1), then preloading
from the ESP32 (Phase 2), then performance. ESP32 loading is not optimised
until the game runs.

### Phase 0: measure and set up (no calculator code yet)

A USA ROM matching `tmc.sha1` (`b4bd50e4…`) is available to the development
session for these measurements. It is kept outside every repository.

1. **ROM working set (done; see results below).** `port/port_rom_touch.c`
   records every ROM page the game touches, and `scripts/fxcg/` sweeps rooms
   and summarises them.
2. **Endianness and 32-bit.** Build the game core (no SDL, a null platform) as
   32-bit big-endian and run it under `qemu-user` (for example `qemu-mips` or
   `qemu-ppc`). Compare frame hashes with the 64-bit PC build over the same
   input script, as fxGLMark's golden frames do.
3. **Toolchain.** fxSDK, gint and `sh-elf-gcc` in CI; a "hello" g3a booting in
   casio-emu.
4. **Code size.** Build the game core with `sh-elf-gcc` and measure its code
   and data size: this sizes D7.
5. **Device measurements** on a real fx-CG50:
   - the RAM gint can give an add-in;
   - `dupdate` time;
   - 2.5 mm logic levels (before connecting the ESP32), then the highest
     reliable SCIF baud rate.
6. **Spikes:** the D1 TLB-miss window, and the D7 load-relocate-run of a small
   application from RAM.

#### Results: ROM working set

Method: each run boots a fresh game headless, starts in the target room and
plays for 13 to 20 seconds of game time with audio off. A room counts only if
the harness confirmed reaching it.

| Measure | Value |
|---|---|
| Rooms measured | 592 (room 0 of all 74 named areas, rooms 1-7 of each) |
| Pages every room touches (resident baseline) | 13 (52 KB; 21 KB compressed) |
| Pages per room during play | median 21, maximum 80 (Hyrule Town, 320 KB) |
| Per room beyond the baseline, compressed | median 11 KB, 90th percentile 25 KB, maximum 104 KB |
| All rooms together | 478 pages, 1.87 MB raw; the page pak is 0.74 MB (39%) with an 18 KB index |
| Touched while the PC port loads | 2277 pages (8.9 MB): table resolution and asset overlays, not shipped (D6) |

So the game reads well under 2 MB of its 16 MB ROM while playing these rooms.

Preload simulation (`lib/cglink/tools/cglink_sim` in HelaFaye/fxLauncher, using the
real link code; a 16-room route from Hyrule Town to Deepwood Shrine):

| Baud | Prefetch the next room | Fetch on arrival only |
|---|---|---|
| 115,200 | 11.3 s at launch, then no stall over 0.1 s | 10 stalls, worst 11.3 s |
| 921,600 | 1.4 s at launch, then none | 6 stalls, worst 1.4 s |

The same holds at a rushed five seconds per room. The launch stall is the
preload before the game starts: the baseline plus Hyrule Town.

The harness counts any armed warp as reached, so some high room numbers may
not be real rooms; their manifests are still valid page sets, but 592 is an
upper bound on distinct rooms.

Limits of the measurement:
- Rooms are visited briefly, standing at the entrance. Enemies, items and
  cutscenes deeper in a room add pages the manifests don't yet have; misses
  catch those.
- Music and sound data are not counted (audio was off); they stay on the
  ESP32 (D2).
- The load phase (8.9 MB touched by the PC port) is not shipped: the tools
  bake what it computes (D6).

### Phase 1: the compressed game on the calculator

- Write `port/fxcg/`:
  - a gint main loop that calls `AgbMain`;
  - VBlank pacing from a gint timer;
  - the keyboard map: D-pad on the arrows, A/B/L/R/Start/Select on F-keys and
    SHIFT, ALPHA, EXIT and MENU, configurable;
  - PPU output through the D3 scaler to `dupdate`.
- Bring-up build: a stand-alone add-in reading the compressed page pak from
  storage memory through the same page-cache interface the launcher will
  provide, so moving to the launcher changes no game code.
- Exit: boots to the title screen, starts a new file and plays through Hyrule
  Town, with frame hashes matching the PC build for the same inputs.

### Phase 2: the launcher and preloading from the ESP32

- **ESP32 firmware** (ESP-IDF, in HelaFaye/fxLauncher): the SLIP+CRC link service, FAT
  on SD, file commands, a page server that streams compressed pages from a
  pak, and save storage.
- **Launcher** (HelaFaye/fxLauncher): link client, page cache, preloader, deflate
  decoder, the D7 application loader, and a file browser for the SD card.
- **Preload tests, in order:**
  1. Host simulation: the link client against a host build of the page
     server over a throttled pipe at 115,200 and 921,600 baud, replaying a
     route of room changes and logging every stall.
  2. qemu_esp32 with a "SuperMini + SD" board serving the page pak, its serial
     port bridged to the host link client.
  3. Hardware: the launcher on a real fx-CG50 and SuperMini, measuring fetch
     time per room and stalls along the same route.
- Exit: Picori launched from the SD card through the launcher, with the
  calculator's storage memory holding only the launcher, and a stall log for
  a fixed route.

### Phase 3: performance

- Profile on the device. Test whether fxGLMark's host cost model carries
  over to the game core.
- Optimise the PPU for the SH-4A, keeping output byte-exact with
  `tools/ppu_parity_check.sh`.
- Add frame skipping and an optional overclock profile.
- Exit: logic at a steady 60 ticks per second and a published render rate.

### Phase 4: audio offload

- Calculator: emit m4a events with frame stamps over the link.
- ESP32: run agbplay with I2S output and a jitter buffer.
- Exit: music and sound effects stay in sync over a 30-minute session.

### Phase 5: Wi-Fi file transfer

- On the ESP32: an access point or station mode with a small HTTP/WebDAV server
  for the SD card, so applications, paks and saves can be exchanged with a
  phone or PC.

### Phase 6 (later, a separate plan): Emerald and online multiplayer

- Pokémon Emerald (from pokeemerald) as the second application, using the same
  launcher, pager and link service.
- The game's serial I/O maps to the link service. The ESP32 runs the
  GB-Link-compatible firmware from gbagate, which acts as the parent and
  absorbs network latency as gbagate's README describes, and bridges to Celio
  or the netplay bridge.
- Test partner: mGBA running the US ROM. The ROM supplied for testing has
  SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7`, which matches pokeemerald's
  `rom.sha1`. It stays out of every repository.

## Testing at each layer

| Layer | Tool | What it proves |
|---|---|---|
| Game logic, 64-bit | the PC build with `TMC_AUTOPLAY` and the repro harness | the behaviour reference |
| Game logic, 32-bit big-endian | `qemu-user` and frame-hash parity | endian and pointer-size fixes |
| PPU | `tools/ppu_parity_check.sh` | optimisations are byte-exact |
| Calculator build | casio-emu headless | it boots and draws, without device timing |
| Link protocol | host unit tests (gbagate's `link_proto` tests) | framing, CRC, retries |
| ESP32 firmware | qemu_esp32 with an SD model and a serial bridge | the firmware before buying parts |
| Everything | a real fx-CG50 and SuperMini | timing, levels, throughput, battery life |

## Risks

1. **Render speed.** The PPU on a 118 MHz SH-4A without an FPU is untested.
   Phase 0 and Phase 2 measure it before anything else depends on it.
2. **The D1 MMU spike** depends on how much of the exception path gint lets an
   add-in own. The fallback costs a large mechanical edit but is safe.
3. **2.5 mm throughput and levels.** These decide how seamless preloading can
   be. USB host mode on the ESP32-S3 is the escape hatch.
4. **RAM.** The launcher, the application's code (D7), the page cache and the
   game state share the calculator's RAM, none of which is measured yet
   (Phase 0).
5. **Running code from RAM** under gint is unproven (Phase 0 spike).
6. **Licensing.**
   - The decomp tooling never ships ROM data.
   - Paks are built by the player from their own ROM.
   - The PPU is GPL-3.0, and the calculator build must keep that license's
     obligations.

## Where the code lives

| Part | Repository |
|---|---|
| The Picori port (`port/fxcg/`, paks, build) | HelaFaye/tmc, `fx-cg50` branch |
| The launcher (link client, page cache, preloader, application loader), the link library, the ESP32 firmware | HelaFaye/fxLauncher |
| GB-Link compatibility on the fx-CG50 | its own repository, when started |
| An SDL-compatible layer that ports may use | HelaFaye/fxSDL |

## Decided

- **Module:** ESP32-S3FH4R2 (4 MB flash, 2 MB PSRAM).
- **No level shifter:** see "Wiring without a level shifter".
- **Repositories:** as in the table above.
- **One launcher** owns the ESP32 link and runs everything loaded from it (D7).
- **Internal storage is kept to a minimum:** the launcher only; data is
  compressed and preloaded from the ESP32 (D4, D6).

## Still open

1. The pinout. The table above is a suggestion.
2. Which region comes first. USA is assumed.
