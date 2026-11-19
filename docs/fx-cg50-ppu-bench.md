# fx-CG50 PPU timing diagnostic

This is an opt-in hardware diagnostic for separating the native Picori frame
cost into PPU rendering, scaling, and LCD presentation. It uses the fxSDK
`libprof` free-running TMU profiler, reports microseconds, and does not require
an emulator, a ROM dump on the host, or a clock modification.

## Build

Install the optional profiler library once (the calculator build already uses
Gint):

```sh
giteapc install Lephenixnoir/libprof
```

Then, from the repository root, enable the diagnostic only for the calculator
build:

```sh
cd port/fxcg
fxsdk build-cg -c -DTMC_FXCG_PPU_BENCH=ON
fxsdk build-cg
```

The CMake option finds `LibProf` 2.1 or newer and links its calculator-specific
`LibProf::LibProf` target. The ordinary build does not require libprof.

The build does not call `clock_set_speed()` or any Ptune/overclock API. The
on-screen `CLK` field is read from `clock_get_speed()`; on an fx-CG50, F1 is
the calculator's normal/default profile. For a stock-clock measurement, make
sure no external utility has changed the profile and confirm that the overlay
reports `CLK F1`.

To return to the normal build:

```sh
fxsdk build-cg -c -DTMC_FXCG_PPU_BENCH=OFF
fxsdk build-cg
```

## Measurements

The diagnostic publishes a new average once per 60 rendered frames. The
on-screen rows mean:

| Label | Measurement |
|---|---|
| `PPU` | Whole `virtuappu_render_frame()` call; min/average/max over the last completed 60-frame window |
| `PREP` | Frame geometry, HDMA/IO scanline preparation, and affine-reference setup |
| `PAL` | Conversion of BG/OBJ palette RAM into the per-frame lookup tables |
| `BG` | Enabled background-layer rendering, including each BG line-buffer clear |
| `OBJ` | OBJ line-buffer clearing, sprite rendering, and OBJ-window/semitransparency mask clearing |
| `MIX` | Per-pixel layer composition, windows, and blend/brightness effects |
| `SCALE` | CPU scaling and RGB565 conversion from 240x160 to 336x224 in the calculator presenter |
| `LCD` | CPU time spent inside `Fxcg_Update()` / `dupdate()`, separate from rasterization and scaling |

The LCD figure measures the time spent in the `dupdate()` API call; it is not
an independent measurement of panel scanout or completion of a background DMA
transfer. Gint's fx-CG display path can overlap DMA transfer with CPU work, so
interpret this as presentation-call overhead, not total physical display time.

`PREP + PAL + BG + OBJ + MIX` should be close to `PPU`; small differences
are expected from profiler bracket overhead and work outside those measured
sections. `SCALE` and `LCD` are outside the PPU total. Game logic, input polling,
VBlank waiting, and other engine work are not included.

## Measurement procedure

1. Confirm the overlay says `CLK F1`.
2. Enter a representative scene and let the game run for at least 60 frames.
3. Record the displayed 60-frame averages and the PPU min/max.
4. Repeat in the same room and scene for each candidate build, with the same
   gameplay conditions. Scene composition matters: sprite count, BG layers,
   affine effects, fades, and windows all change PPU cost.
5. Keep the normal non-diagnostic build for ordinary play; the benchmark overlay
   is intentionally intrusive and adds a small amount of measurement overhead.

The profiler is initialized before the port reserves its periodic VBlank
timer, and released if the game entry returns. The CMake package check verifies
the profiler headers and link target at configure time.
