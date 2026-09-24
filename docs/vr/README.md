# TMC VR — a voxel port of The Legend of Zelda: The Minish Cap

A fork of [Project Picori](https://github.com/999sian/tmc), which is a native
PC port of *The Legend of Zelda: The Minish Cap*. This fork adds a voxel/VR
layer: it reads the game's own room data, reconstructs three-dimensional
shapes from the two-dimensional art, and renders the result as a world you
can stand in.

Forked from Picori **v0.9.2** (`3ebfebe07`). Upstream stays a remote, and
everything here lives on top of it.

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

## Pipeline

```
roomcap hook  ──▶  room dumps (.tmcr)  ──▶  manifests (.scene)  ──▶  meshes (.obj)
 port/                tools/room_explore     tools/mk_manifests     tools/shapefit
 port_repro_roomcap   tools/shading                                 tools/rebuild_all.sh
```

Run the whole thing with `tools/rebuild_all.sh` (set `TMC_ROOT` if the kit
and the game tree are not nested).

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

### A note on what is *not* finished

Kept here because a README that only lists successes is misleading:

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
