# fx-CG50: host-side ROM data preparation

The fx-CG50 target is currently built for the supported USA 16 MiB ROM. Do
not copy the raw ROM to the calculator. Build a compact, page-addressable
`PCPK` pak on the development computer and transfer only that derived pak
when the calculator-side page reader is ready.

## 1. Generate ROM touch data on the PC

Build the normal PC port and run the ROM-touch sweep. A representative
command is:

```sh
scripts/fxcg/rom_touch_sweep.sh \
  build/pc/tmc_pc baserom.gba build/fxcg-rom-touch 1200 4
```

The default sweep visits room 0 across all 144 area IDs. For good game
coverage, include completed runs for the other rooms too; the script accepts
explicit `area:room` pairs after the optional frame/job arguments. The packer
ignores runs that did not log `warp -> 1`, so failed room warps do not pollute
the manifest.

## 2. Compress and validate on the host

```sh
scripts/fxcg/prepare_calculator_data.sh \
  baserom.gba build-cg/picori_usa_pages.pak \
  build/fxcg-rom-touch
```

The packer checks the exact ROM size and SHA-1 expected by the current USA
linker symbols. It accepts only room runs with a zero process exit status,
post-intro seed evidence, a complete ROM-touch summary matching the touch log,
and a confirmed requested destination. Expected unreachable/redirected rooms
are excluded; missing or inconsistent run evidence aborts packaging. It collects
both `load` and `play` records from accepted runs. The union of load-phase pages is promoted into the common resident
baseline, so the game can resolve its ROM header and pointer tables before
room-specific paging starts. Repeated captures of one room are unioned across
all supplied directories. Each distinct 4 KiB page is stored once and
independently raw-DEFLATE-compressed (or stored raw when compression expands
it). Every page is decompressed again and byte-compared against the source
ROM before the output is accepted.

Three files are produced:

- `picori_usa_pages.pak`: the binary PCPK v1 page pak.
- `picori_usa_pages.pak.sha256`: transfer-integrity checksum.
- `picori_usa_pages.pak.json`: ROM fingerprint, format, size and page counts.

Only the `.pak` is a candidate for calculator transfer. Keep the ROM and
host-side sidecars on the computer. The pak contains copyrighted ROM-derived
data; generate it from your own legally obtained ROM and do not commit or
redistribute it.

## 3. Record the pages the calculator reads, not the PC

The calculator build has no asset loader: texts, area tables, sprite
pointers and graphics groups come from the ROM. The PC build loads them from
its extracted asset tree unless `TMC_ROM_ONLY=1` is set. A pak built from
asset-backed sweeps therefore misses pages the calculator needs. On
2026-11-23 the ROM-only sweep touched 1,140 unique pages, against 582 in
the earlier pak.

- `rom_touch_sweep.sh` now always sets `TMC_ROM_ONLY=1`.
- `rom_touch_boot.sh <tmc_pc> <baserom.gba> boot-touch.txt` records power-on
  through the title screen, file select and a new file, also ROM-only.
- Pass that log to the packer with `--boot=boot-touch.txt`; its pages join
  the pinned baseline:

```sh
scripts/fxcg/prepare_calculator_data.sh baserom.gba Picori.pak \
  --boot=boot-touch.txt build/fxcg-rom-touch
```

Copy `Picori.pak` to the root of storage memory as `/Picori.pak`.

## 4. Runtime

The calculator-side reader is the D1 pager (`docs/fx-cg50-d1-pager.md`). It
is built only with `-DTMC_FXCG_D1_PAGER=ON`. The default build still stops
at the fail-closed ROM message. Run `D1-PAGER-PROBE.g3a` on the calculator
before the pager build.

The game BSS placement is separate from ROM paging. It targets the upper
DRAM range described in `fxcg.ld`, and CI checks the measured origin,
capacity, and BSS placement. RAMCheck v2 passed the entire 3,200 KiB range
through its uncached P2 alias on the target fx-CG50 running OS 3.20.0202
(800 blocks, 12,001 checks, zero failures or restore failures). This confirms
read/write access and restoration during that test session; it does not test
Picori startup, runtime coexistence with the OS, or ROM paging.

## Harness touch capture (2026-11-25)

The PC ROM-touch sweeps under-record what the calculator build reads: on
the 12,000-frame intro script (`tests/fxcg50-bigendian/input-intro.txt`)
the calculator configuration touched 278 ROM pages, 65 of them missing
from the 1,140-page pak (the first, page 2482 at 0x9B2000, is needed by
the first save dialog). Capture the calculator's own touches with the host
harness and add them to the baseline:

```
HOST_TOUCH=host-touch-intro.txt picori-host baserom.gba 12000 "$(cat input-intro.txt)"
python3 scripts/fxcg/build_page_pak.py baserom.gba Picori.pak \
    --boot=boot-touch.txt --boot=host-touch-intro.txt <room-sweep-dir>
```

`HOST_PAK=Picori.pak picori-host ...` then runs the game with the ROM
window served only from the pak, through the pager's own reader and
cache (`fxcg_rom.c`, `FXCG_ROM_HOST`); a page the pak lacks stops the run
with `MISSING_PAGE <page>`. With the rebuilt pak (1,205 pages, 280 in the
baseline, 2.68 MiB) all 12,000 frames match the full-ROM run. The baseline
is now larger than the 264 frames the pager pins; the rest are fetched
on demand.

### Whole-ROM pak (recommended for first hardware runs)

`build_page_pak.py ... --all` stores all 4,096 ROM pages: 7.7 MiB, well
inside the fx-CG50's storage memory, so no room can stop on a missing
page. Room sweeps become optional; the boot captures still decide which
pages are pinned at start:

```
python3 scripts/fxcg/build_page_pak.py baserom.gba Picori.pak --all \
    --boot=boot-touch.txt --boot=host-touch-intro.txt
```

Checked with `HOST_PAK`: all 12,000 intro frames match the full-ROM run.
The per-room pak stays the target for the ESP32 link, where transfer size
matters.
