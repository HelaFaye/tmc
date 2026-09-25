# TMC VR — a voxel port of The Legend of Zelda: The Minish Cap

A fork of [Project Picori](https://github.com/999sian/tmc), which is a native
PC port of *The Legend of Zelda: The Minish Cap*. This fork adds a voxel/VR
layer: it reads the game's own room data, reconstructs three-dimensional
shapes from the two-dimensional art, and renders the result as a world you
can stand in.

Based on Picori **v0.9.3** plus five later upstream commits (`64ab5d2`).
Upstream stays a remote, and everything here lives on top of it.

**Where it stands:** the capture and fitting pipeline works and produces
`.obj` meshes for rooms, whole areas and object classes (chests, torches,
rocks). There is **no 3D or VR renderer in the game yet**; view the meshes
in any OBJ viewer. The runtime modules in `port/vr/` are the groundwork for
that renderer. See [What is not finished](#what-is-not-finished).

## The rule that shapes everything

**No ROM asset is ever distributed.** Not a tile, not a palette, not a
fitted mesh made from one. Every model this project renders is generated on
the user's own machine from the user's own legally obtained ROM, and the
pipeline must stay replicable against a fresh one.

That is why this repository contains *fitters* rather than models. A room
dump, a decoded tile, a fitted `.obj` and a harvested sprite frame are all
derived works; they live in a local cache and are excluded in `.gitignore`.
What is committed is the code that regenerates them, plus manifests that
record only coordinates and material indices.

## How a shape gets made

The art gives one view of each object, so depth has to be recovered rather
than read. Two signals do most of the work, and both come from the original
artists:

- **Black outlines** mark oblique angle transitions — the artist drew a line
  where one surface turns into another — so the outline is a map of an
  object's edges in three dimensions.
- **Shading** tracks orientation under a fixed light from the north-west and
  above. Verified against collision data, which is stored separately from any
  palette so the test is not circular: blocked cells average 0.490 brightness
  against 0.770 for open floor, and blocked is darker in 98.7% of 78 rooms.

The camera is measured, not assumed. A floor tile is 16 world units deep and
drawn 16 rows tall at zero height; the chest is drawn 16 rows, being 9 of
body and 7 of lid deck. Both give `drawn = height + depth` — a 45° oblique.
`tools/viewangle.py` is the single source for this.

Where the art cannot answer a question, the *game* often can.
`tools/surfaces.py` reads each cell's collision and action bytes and looks up
what the decompilation calls them, so a pit is a pit because the game says
`FX_FALL_DOWN`. That matters: a pit built as a solid is a floor over a hole.

## What this fork adds

| Area | Files | What it does |
|---|---|---|
| Design | `docs/vr/01-blueprint.md` … `04-asset-pipeline.md`, `ERRATA-brainstorm.md` | Where to hook the engine, the phased plan, the stabilised camera and scale presets, the asset rule, and the defects found in the earlier design drafts. |
| Room capture | `port/port_repro_roomcap.c` (`TMC_VR` blocks), `tools/apply_roomcap_hook.py`, `tools/check_hook.py` | Picori's headless room-capture harness, extended to write `.tmcr` room dumps, step Link's sprite frames, and boot at a chosen story state. |
| Runtime modules | `port/vr/` | `vr_world` snapshots `gMapTop`/`gMapBottom`, entities, palettes and BG VRAM, and writes `.tmcr` v4. `vr_anchor` is the camera anchor with tabletop, diorama and life scales. `vr_interp` interpolates entities (Link included) between game ticks. `vr_greedy_mesh` builds meshes from voxel grids. `vr_spritedump` rasterises sprite frames. `vr_debug_panel` is an ImGui panel that drives them all. |
| Harvest | `tools/harvest_rooms.py`, `harvest_states.sh`, `harvest_night.sh` | Warp to each of the game's 842 rooms in turn and dump it, optionally at each story state (0–6 dungeons cleared). |
| Decode and analysis | `tools/room_explore.py`, `extract_art.py`, `verify_art.py`, `shading.py`, `tilehints.py`, `survey_tiles.py`, `surfaces.py` (+ `gen_surfaces.py`, `surfaces_table.py`), `viewangle.py`, `decomp_labels.py`, `entity_catalogue.py` | Read dumps, rebuild the art, separate shading from colour, classify cells from the game's own collision and action tables, and name entities from the decomp headers. |
| Fitting | `tools/shapefit.py`, `mk_manifests.py`, `verify_fits.py`, `hull_carve.py`, `fit_link.py` | Turn drawn objects into solids (chests, torches, boulders, stumps, trees, lotus, buildings, stairs), check their sizes, and carve sprite hulls. |
| Manifests | `vr/objects/*.scene`, `vr/world/*.txt` | Coordinates, material indices and authored heights only. No art. |
| Drivers | `tools/rebuild_all.sh`, `tools/voxelate_all.sh` | Run objects, rooms and whole areas end to end. |
| Checks | `tools/verify_repro.py`, `tools/verify_fits.py`, `tools/src/vr_meshtest/main.c`, `tools/test_viewer.js` | Output is byte-for-byte reproducible from a ROM; fitted objects are the right size; the greedy mesher's winding; the viewer's parser and camera. |
| Viewers | `tools/flat-viewer.html`, `tools/world-viewer.html` | Open in a browser and drop in `.tmcr` dumps or generated `.obj` meshes to inspect them in 3D. |
| Build | `xmake-vr.lua`, `requirements.txt` | The `vr` option and the Python dependencies. |

## Setup

```sh
git clone -b vr https://github.com/HelaFaye/tmc.git
cd tmc
git submodule update --init libs/VirtuaAPU
```

Do not clone with `--recurse-submodules`: `libs/tmc-Android-Experimental`
and `libs/tmc-Modern-Launcher` are private upstream repositories that git
cannot fetch, and the build treats both as optional. `libs/VirtuaAPU` is
the only one the build needs (`build.py` also initialises it for you).

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # numpy, Pillow
```

The scripts use `.venv/bin/python` automatically when it exists.

Capture needs a `tmc_pc` built with `TMC_VR` defined. **That wiring is not
committed yet** (see below). Until it is, add `includes("xmake-vr.lua")` near
the top of `xmake.lua` and this flat-PC block inside `target("tmc_pc")`:

```lua
if has_config("vr") then
    add_defines("TMC_VR=1")
    add_includedirs("port/vr")
    add_files("port/vr/vr_world.c", "port/vr/vr_anchor.c",
              "port/vr/vr_greedy_mesh.c", "port/vr/vr_interp.c",
              "port/vr/vr_spritedump.c", "port/vr/vr_debug_panel.cpp")
end
```

(The `TMC_VR BLOCK` inside `xmake-vr.lua` is the later OpenXR/Vulkan build
and needs files that do not exist yet.) Then expose the sprite size table
from `port/port_draw.c`, which `vr_spritedump.c` links against:

```c
#ifdef TMC_VR
const u8* Port_GetSizeTable(void) {
    return sSizeTableLoaded ? sSizeTable : NULL;
}
#endif
```

and build:

```sh
xmake f -y --game_version=USA --vr=y
xmake build -y tmc_pc
python3 tools/apply_roomcap_hook.py --check    # the TMC_VR hook is present
```

That is enough for capture. The live ImGui panel (collision grid, entity
table, anchor controls) also needs `VrDebug_Tick()` called once per tick and
`VrDebug_DrawPanel()` called from the ImGui overlay; the exact call sites are
listed at the top of `port/vr/vr_debug_panel.cpp`.

## Pipeline

```
roomcap hook ──▶ room dumps (.tmcr) ──▶ manifests (.scene) ──▶ meshes (.obj)
 port_repro_       harvest_rooms.py      mk_manifests.py        shapefit.py scene
 roomcap.c         harvest_states.sh                            room_explore.py voxel / worldgen
```

```sh
# 1. Capture every room (needs your own ROM in dist/USA/).
python3 tools/harvest_rooms.py --out vrdump --jobs 8
#    or every story state, into states/p0 .. states/p6:
bash tools/harvest_states.sh

# 2. Fit and mesh everything. Resumable; FORCE=1 rebuilds; JOBS sets how
#    many rooms build at once (default: all CPUs).
bash tools/voxelate_all.sh

# 3. Prove the output is reproducible (runs the generator twice, compares
#    every byte; --break must make it fail).
python3 tools/verify_repro.py vrdump --area 3
```

`voxelate_all.sh` takes its dumps from the first non-empty of `states/p0`,
`night/vrdump` and `vrdump` (override with `TMC_ROOMS`), and writes:

| Output | From |
|---|---|
| `objects/*.scene`, `geom/<class>.obj` | `rebuild_all.sh`: manifests, a size check, then `shapefit.py scene` |
| `geom/rooms/room_AA_RR.obj` | `room_explore.py voxel`, one per room |
| `geom/world/area_N/area_NN.{obj,mtl,png}` | `room_explore.py worldgen --texture --albedo`, one per area |

Every output is derived from your ROM and is gitignored.

### Capture variables

The roomcap harness is driven by environment variables; `harvest_rooms.py`
sets the first four for you.

| Variable | Meaning |
|---|---|
| `TMC_ROOMCAP=1` | enable the harness |
| `TMC_ROOMCAP_WARP=area,room,x,y,layer` | where to warp after boot |
| `TMC_ROOMCAP_SETTLE=n` | frames to wait after the warp |
| `TMC_ROOMCAP_TMCR=<dir>` | write `room_AA_RR.tmcr` there and exit (exit 6 if the warp landed in another room) |
| `TMC_ROOMCAP_PROGRESS=0..6` | dungeons cleared before boot; any value sets `TABIDACHI`, so Hyrule Town loads instead of Festival Town |
| `TMC_ROOMCAP_SPRITES=first,last[,dirmask]`, `TMC_ROOMCAP_SPRITEDIR=<dir>` | step Link through a frame range and dump each frame as PNG + JSON |

The `.tmcr` format is documented at the top of `port/vr/vr_world.c`.

## Credits

### Upstream and prerequisites

| Project | What it provides | Licence |
|---|---|---|
| [Project Picori](https://github.com/999sian/tmc) (999sian) | The PC port this forks: SDL3 platform layer, the vendored software PPU in `port/ppu`, save handling, build system | GPL-3.0 |
| [zeldaret/tmc](https://github.com/zeldaret/tmc) | The decompilation everything rests on. This fork also reads its **labels** directly — `include/tiles.h` and `include/player.h` give the surface and collision tables that `tools/gen_surfaces.py` regenerates, and `src/playerHitbox.c` gives Link's 6×6 footprint | see project |
| [SDL3](https://www.libsdl.org/) | Windowing, input, audio output | Zlib |
| [agbplay](https://github.com/ipatix/agbplay) | GBA audio engine used by the port | see project |

See `THIRD-PARTY-LICENSES.md` for the full upstream list.

### Tools used to build the voxel layer

- **NumPy** and **Pillow** — all tile analysis, silhouette measurement and
  image work.
- **xmake** — build system, inherited from Picori (`tmc_pc` is the target;
  running `xmake run` with no target builds `agb2mid` instead).

### Authors

- **[HelaFaye](https://github.com/HelaFaye)** — project direction, reference
  material, and the observations that most of this is built on: that chest
  lids are horizontal planks over a flat body, that flames are teardrops with
  single or forked tips, that rocks need rounding only at the base to read as
  movable, that trees are tall and conical, that lotus petals each come to
  their own point, and that anything outlined in black is showing an oblique
  angle transition. Also every correction that sent a wrong approach back to
  the drawing board.

## What is not finished

Kept here because a README that only lists successes is misleading.

**Pipeline and build**

- **The `vr` build option is not wired in.** `xmake.lua` does not include
  `xmake-vr.lua`, and the three engine hooks (`Port_GetSizeTable` in
  `port_draw.c`, `VrDebug_Tick` in `port_bios.c`, `VrDebug_DrawPanel` in
  `port_imgui_menu.cpp`) are not committed. Without them `TMC_VR` is never
  defined, the capture code is compiled out, and a harvest writes no dumps.
- **Two capture modes were lost in a hook revision.** `harvest_night.sh`
  sets `TMC_ROOMCAP_SPRITERANGE` (every sprite's animation frames) and
  `harvest_rooms.py --entsheet` sets `TMC_ROOMCAP_ENTSHEET` (per-entity
  sheets for `entity_catalogue.py`). The current hook reads neither, so both
  produce nothing.
- **About a third of rooms fail to capture.** The last full state-0 sweep
  captured 562 of 842 rooms; 278 failed.
- **No renderer.** No Vulkan device, OpenXR session or in-game 3D view
  exists yet; `01-blueprint.md` and `02-implementation-plan.md` lay out
  that work.

**Shapes**


- **Link is not built.** `tools/fit_link.py` is written and its carve is
  unit-tested, but it has never seen his sprites — they come from the ROM
  extraction step and were not available. It refuses to build rather than
  invent him.
- **Trees are not auto-detected.** They have no tile type, and three
  colour-based attempts returned 936, 316 and 63 candidates that were mostly
  dungeon floors, lily pads and a bed. Tree fitting is opt-in until identity
  can be resolved per area.
- **Building detection is unreliable.** A "bright top, dark bottom" test
  returns desert dunes and pits; it should be rebuilt on surface data.
- **5,511 cells disagree** between the two pit bytes across 561 rooms. Ledges
  and bridge decks explain some of it; that has not been verified to explain
  all of it.

## Licence

GPL-3.0, inherited from Project Picori. See `LICENSE`.
