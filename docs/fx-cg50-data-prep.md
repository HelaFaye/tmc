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

## 3. Runtime status — important

This host-side conversion is implemented, but the calculator-side D1 ROM
window is not. `port/fxcg/fxcg_rom.c` still deliberately returns failure and
`Fxcg_GameMain()` displays that failure message. The `.pak` is therefore a
prepared input, not yet a loadable runtime resource. Do not remove that
fail-closed behaviour or claim the game boots until the TLB-backed page
reader (or the documented accessor fallback) has been implemented and tested.

The game BSS placement is separate from ROM paging. It targets the upper
DRAM range described in `fxcg.ld`, and CI checks the measured origin,
capacity, and BSS placement. RAMCheck v2 passed the entire 3,200 KiB range
through its uncached P2 alias on the target fx-CG50 running OS 3.20.0202
(800 blocks, 12,001 checks, zero failures or restore failures). This confirms
read/write access and restoration during that test session; it does not test
Picori startup, runtime coexistence with the OS, or ROM paging.
