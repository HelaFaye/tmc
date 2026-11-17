# Project Picori on the Casio fx-CG50: port plan

Status: plan, November 2026. Nothing below is implemented yet. When this plan
was written, the `fx-cg50` branch pointed at the same commit as `master`
(`08f3ada`).

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
- **No FPU.** fxOGL's measurements in CasioMini3D show what that costs.
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

```
 fx-CG50 add-in (gint)                       ESP32-S3 SuperMini
 ┌───────────────────────────────┐           ┌──────────────────────────────┐
 │ src/  decompiled game logic   │           │ SD card: FAT, paks, saves    │
 │ port/fxcg/  C platform layer  │  2.5 mm   │ link service (same protocol) │
 │   video: PPU → scale → dupdate│  serial   │ storage: list/read/write     │
 │   input: keyboard map         │◄─────────►│ installer: add-ins and paks  │
 │   saves: storage memory       │ SLIP+CRC  │ audio: agbplay → I2S         │
 │   ROM pager (D1)              │           │ Wi-Fi: HTTP/WebDAV file share│
 │   link client (optional)      │           │ later: GB-Link/Celio bridge  │
 └───────────────────────────────┘           └──────────────────────────────┘
```

The game must also run **without** the ESP32: from storage memory, silently.
The ESP32 adds to the game; it never stands between the game and a frame.

### Decisions

**D1. ROM access: an MMU-backed 16 MB window, if the spike works.**

- `gRomData` points at a virtual 16 MB range.
- A TLB-miss handler fills entries from a software page table. The table
  points at 4 KB pages held in a RAM cache, which is loaded from the paks.
- The 121 pointer sites keep working unchanged.
- The SH-4 UTLB has only 64 entries, so a refill handler is required anyway.
  When it can do I/O, misses can also stream pages.
- **Fallback:** if gint's exception handling makes this impractical, convert
  the 121 sites to an accessor macro and keep only the measured working set.
  The PC build can check that the converted code is exactly equivalent.
- Either way, phase 0 measures the real working set first. Only pages the game
  touches ship. Graphics and sound already come from the asset paks, not
  `gRomData`.

**D2. Audio runs on the ESP32.**

- The calculator sends m4a engine events: song start and stop, sound effects,
  volume and tempo, stamped with the game frame.
- The ESP32 runs agbplay and drives an I2S DAC or PWM.
- Without the ESP32, the game is silent.

**D3. Video: VirtuaPPU at 240×160, scaled to 336×224 (×1.4) or shown 1:1.**

- The ×1.4 scale fills the screen height: two of every five rows and columns
  are duplicated.
- Optionally, widescreen 384×160 at 1:1, using the existing widescreen support.
- Game logic always runs at 60 ticks per second. If it has to, rendering drops
  to every second frame. Frame time is the main performance risk.

**D4. Transport: the 2.5 mm port first, USB as the fallback.**

- At 115,200 baud the link carries about 11 KB/s. That is enough for audio
  events, saves and link play, but loading a 4 KB page takes about 0.35 s.
- So streaming means **bulk loads** at boot, at room transitions, and when
  installing. It does not mean demand paging inside a frame.
- If the SCIF runs reliably at about 1–2 Mbaud, room-level prefetch becomes
  practical.
- The ESP32-S3 can be a USB host for gint's USB device. Measure that path too
  before committing to an ESP32 board revision.
- **Framing:** SLIP with a CRC, reusing `link_proto.{h,c}` from
  HelaFaye/gbagate, where it is already tested.

**D5. Build: fxSDK and gint, with a C-only platform layer.**

- A new `port/fxcg/` directory holds the calculator platform: main loop,
  video, keyboard, timers, saves, ROM pager and link client.
- The `TMC_N64` guards become `TMC_EMBEDDED` (32-bit, no SDL) plus `TMC_BE`
  (big-endian ROM reads). The calculator sets `TMC_FXCG` on top.
- The build follows CasioMini3D's fxGLMark CMake (`fxsdk build-cg`,
  `GenerateG3A`).

**D6. Assets: baked binary paks.**

- A PC-side tool turns the extracted assets and the measured ROM pages into
  region-specific binary paks.
- Paks are installed to storage memory or kept on SD.
- The calculator never parses JSON.

## Phases

Each phase ends with something measured or running, not just written.

### Phase 0: measure and set up (no calculator code yet)

A USA ROM matching `tmc.sha1` (`b4bd50e4…`) is available to the development
session for these measurements. It is kept outside every repository.

1. **ROM working set.** Run PC builds headless with `TMC_AUTOPLAY` and the repro
   harness through as much of the game as the scripted inputs cover.
   - Dump the touched-page bitmap.
   - Report pages touched, total and per area. This sizes D1.
2. **Endianness and 32-bit.** Build the game core (no SDL, a null platform) as
   32-bit big-endian and run it under `qemu-user` (for example `qemu-mips` or
   `qemu-ppc`).
   - Compare frame hashes with the 64-bit PC build over the same input script.
     This uses the golden-frame method from CasioMini3D's
     `apps/fxGLMark/tests/profile`.
   - Fix divergences before touching the calculator.
3. **Toolchain.** Get fxSDK, gint and `sh-elf-gcc` into CI, and boot a "hello"
   g3a in casio-emu, which CasioMini3D's regression harness already uses.
4. **Device measurements.** On a real fx-CG50, measure:
   - the RAM gint can give an add-in;
   - `dupdate` time;
   - storage-memory read speed;
   - 2.5 mm logic levels and the highest reliable SCIF baud rate.
5. **D1 spike.** Install a TLB-miss handler under gint that maps a 16 MB window
   from a RAM page table. Decide between D1 and its fallback.

Exit: working-set size, big-endian frame parity, a g3a in CI, the device
numbers, and the D1 decision, all written into this file.

### Phase 1: the game core on the calculator, without the ESP32

- Write `port/fxcg/`:
  - a gint main loop that calls `AgbMain`;
  - VBlank pacing from a gint timer;
  - the keyboard map: D-pad on the arrows, A/B/L/R/Start/Select on F-keys and
    SHIFT, ALPHA, EXIT and MENU, configurable;
  - PPU output through the D3 scaler to `dupdate`;
  - saves to storage memory.
- Load the ROM pager from paks in storage memory.
- Exit: boots to the title screen, starts a new file and plays through Hyrule
  Town without corruption, with frame hashes matching the PC build for the same
  inputs.

### Phase 2: performance

- Profile on the device. Test whether the CasioMini3D host cost model (32-bit
  soft-float build plus callgrind) carries over to the game core.
- Optimise the PPU for the SH-4A, keeping output byte-exact with
  `tools/ppu_parity_check.sh`:
  - a tile decode cache;
  - background layers that are skipped when unchanged;
  - scanlines rendered straight into the LCD format.
- Add frame skipping and an optional overclock profile.
- Exit: logic at a steady 60 ticks per second, a published render rate
  (at least 30 fps), and the measured cost of each subsystem.

### Phase 3: the ESP32 storage interface

- **Firmware**, a new ESP-IDF project in HelaFaye/CasioMini3D, next to the
  launcher, since every fx-CG50 port shares it. It is modelled on gbagate's
  layout and provides:
  - the SLIP+CRC link service;
  - FAT on the SD card;
  - commands to list, read, write and checksum files;
  - an installer that copies add-ins and paks into calculator storage;
  - save backup and restore.
- **Calculator side:**
  - a link client library and the launcher/file-browser add-in, both in
    CasioMini3D;
  - Picori's own use of the client in `port/fxcg/`, in this repository.
- **Emulation before hardware:** a "SuperMini + SD" board in qemu_esp32, its
  serial port bridged to the calculator side. First that is the host build of
  the link client; later casio-emu, if it gains a serial model (its serial
  support is *unknown*).
- Exit: on hardware, install Picori's paks from SD and boot them with the
  calculator's storage memory empty beforehand.

### Phase 4: audio offload

- Calculator: emit m4a events with frame stamps over the link.
- ESP32: run agbplay with I2S output and a jitter buffer.
- Exit: music and sound effects stay in sync over a 30-minute session, and the
  game is unchanged with the ESP32 unplugged.

### Phase 5: Wi-Fi file transfer

- On the ESP32: an access point or station mode with a small HTTP/WebDAV server
  for the SD card, so add-ins, paks and saves can be exchanged with a phone or PC.
- Exit: upload a pak over Wi-Fi and install it from the calculator.

### Phase 6: streaming

- Only if the Phase 0 baud rate allows it: room-level prefetch over the link,
  so the full game runs with only a resident core in storage memory.
- Exit: a room transition costs less than the GBA's own fade.

### Phase 7 (later, a separate plan): Emerald and online multiplayer

- Pokémon Emerald (from pokeemerald) as the second port, reusing the platform
  layer, the pager and the link service.
- The calculator's serial I/O registers map to the link service. The ESP32 runs
  the GB-Link-compatible firmware from gbagate, which acts as the parent and
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
3. **2.5 mm throughput and levels.** These decide whether streaming (Phase 6)
   is possible at all. USB OTG is the escape hatch.
4. **Add-in size and RAM limits** are not yet measured (Phase 0).
5. **Licensing.**
   - The decomp tooling never ships ROM data.
   - Paks are built by the player from their own ROM.
   - The PPU is GPL-3.0, and the calculator build must keep that license's
     obligations.

## Where the code lives

| Part | Repository |
|---|---|
| The Picori port (`port/fxcg/`, paks, build) | HelaFaye/tmc, `fx-cg50` branch |
| The launcher add-in, the calculator link client, the ESP32 firmware | HelaFaye/CasioMini3D |
| GB-Link compatibility on the fx-CG50 | HelaFaye/CasioMini3D for now, moved to its own repository later |

## Decided

- **Module:** ESP32-S3FH4R2 (4 MB flash, 2 MB PSRAM).
- **No level shifter:** see "Wiring without a level shifter".
- **Repositories:** as in the table above.

## Still open

1. The pinout. The table above is a suggestion.
2. Which region comes first. USA is assumed.
