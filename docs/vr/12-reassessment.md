# 12 — Reassessment: build from tiles, not pixels

## What was done

`tools/vox` solves one height per drawn pixel from its shading and the
collision under it, then adds rules per failure: steps, cliffs, awnings,
slate walls, overhead arches, roofs, slabs and decks. It was checked by:
- the match from the game's camera;
- turned views of seven reference crops, judged by eye.

## What it produced

On whole rooms (the first voxel review round, 18 early-game rooms):
- the river stood up as walls;
- the castle garden's hedges stood up as sheets as long as the hedges;
- tree lines at room edges became vertical walls of canopy, and canopies
  became fins and blades;
- a castle hall's upper floor became a hump.

Each fix moved the fault somewhere else (hedges against trees). The crops
had looked right because their edges cut the shapes the rules depend on.

## Why

1. **The main check could not fail.** Any heights reproduce the drawing
   from the game's camera; the method is built that way. The match
   measures nothing about shape.
2. **Shape was solved per pixel column, not per thing.** Nothing knows
   what a tree or a hedge is, or how tall one stands. Columns are solved
   apart, which is what makes fins, blades and sheets.
3. **Rules depend on context that should not matter:** the size of a
   connected shape, the share of its columns resting on something, where
   a crop ends.
4. **The game's structure was ignored.** Rooms are 16 px tiles, reused:
   the 18 early-game rooms have 25,759 cells and 4,154 distinct drawn
   cells (both layers); 500 of them cover 69% of the cells.
5. **Reviews did not carry.** They were on per-room boxes whose ids change
   with every run; a correction fixed one box once.

## The method from here

1. **Tiles are the unit.** Each distinct tile (keyed by the game's
   tileset and tile indices, both layers, with its collision) gets one
   voxel model: a 16 x 16 height map of a known kind (flat, wall face,
   step, hedge box, tree crown part, roof part, water), with parameters.
   An automatic first guess, then review.
2. **Templates for things of several tiles.** A tree, each house style
   and the gate are defined once from their tiles; every copy uses them.
3. **Rooms are assembled** from their tiles and the collision levels
   (`tilevox.stair_levels`, ledges). No per-room rules.
4. **The review page reviews tiles and templates,** most frequent first,
   each shown everywhere it occurs. Right or wrong, then notes; a fix
   applies everywhere.
5. **Checks that can fail:** nothing floats, no face stands taller than it
   is drawn, water and floors are flat, templates agree across copies, and
   a small hand-checked set of tile heights is the ground truth. The game
   camera's match stays a constraint, not a measure of quality.

Models and labels keyed by tile index are structure (doc 10's asset
rule); the art stays local.

## Kept from the pixel pass

These hold whatever the unit is, and are in the code now:
- water, holes and the undrawn void lie flat at their class height;
- what the overhead layer draws over is never built by the ground pass;
- an arch is gate-sized (192 x 128 px at most);
- a large overhead shape over walkable cells (an upper floor) is a deck;
- a rounded top rises at most as high as it is wide.

## Progress

**1. The tile catalogue** (`tools/vox/tiles.py`). A tile's key is
`<tileset>:<layer>:<metatile>`; `<tileset>` fingerprints the area's
metatile tables, so areas sharing a tileset share tiles. The 18
early-game rooms use 4,094 tiles (2,919 ground, 1,175 overhead) over
34,084 cell layers; the top 500 cover 71% and the top 1,000 82%.

Each tile gets a first-guess kind: floor, water, face, top, foliage,
trunk, prop, deck or void. The guess uses the tile's art (leaf green and
how even it is, bark with vertical grain, banding, a plain fill), the
collision under it, and what lies below it wherever it is used. Whether
foliage is a tree, a bush or a hedge, and whether a group of busy
overhead tiles is a roof or a prop, is for the group's shape to say
(templates), not one tile.

The review page's Tiles mode shows every tile in its context, most used
first, with its kind, how often it is used and where. Right or wrong,
then notes; the review is keyed by the tile, so it applies everywhere.

**2. Things, one label each** (`tools/vox/objects.py`). A room's trees,
houses, hedges and pillars are found from two sources, each strong where
the other is weak:
- *Binding:* two neighbouring tiles belong to one thing when, across every
  dumped room, each is found beside the other there at least half the time.
  A house's tiles travel together; two houses in a stack do not, so they
  part. The statistics are tile indices only (`<dumps>/binding.json`).
- *The art:* trees overlap and share edge tiles, so binding cannot part
  them. A watershed of the picture can. Its seeds are the deepest points
  away from the long dark outlines, grown over what is not floor.

Pieces mostly in one bound group join, unless the group is mostly leaf
green; there they join only across boundaries that are not drawn outlines.

Checked by eye on the 18 early rooms:
- every house in Hyrule Town is one thing, and stacked mushroom houses
  are separate;
- each tree in the fields and along the room edges is its own thing;
- the garden's hedges are one thing per run;
- shallow water, carpets and paths are left out.

Castle rooms split into pillars, windows and rim pieces rather than whole
walls: their tiles are mostly used once or mirrored, so binding has little
to go on. Those pieces get their heights from collision levels anyway.

What was tried and dropped:
- merging by how dark a boundary is: tree-to-tree and roof-piece
  boundaries overlap in darkness;
- grouping by collision value or tile type: whole tree rows and stacked
  houses share both;
- mining fixed multi-tile templates: forests are drawn as overlapping
  crowns, so the templates come out as fragments.

**3. Building things whole** (`tools/vox/assemble.py`). Each thing from
step 2 is built as one shape at one height. Before, each pixel column was
solved apart, which is what made fins and sheets.
- *Crowns and bushes* are one ellipsoid each. It is as wide as drawn, and
  its drawn rows are shared evenly between height and depth. It rests at
  one level: the median of what is under the centre third of its foot.
  Leaf pixels in the floor's colours join the crown they lie in. A crown
  claims all its leaves whatever their tiles' kinds say; nothing else
  (steps, faces) builds them.
- *Hedges* are things thin everywhere (most pixels in a run no wider than
  40 px across or down) and at least 64 px long, whatever turns they take.
  Each run of drawn rows gets a front of one height along the whole hedge
  and a level top behind it, so they lie along the ground.
- *The overhead layer is never stood upright.* It is drawn over the
  actors (roof tops, wall tops, canopy) and is seen from above. A thing's
  overhead part is built one connected patch at a time. A big patch is a
  roof, one rounded cap like a crown; a small one is level at one height.
  A stack of houses is one thing to the statistics (the game never draws
  them apart); the wall between their roofs parts them.
- *Ground hidden under a thing* carries on from the last real floor in
  front of it. It is not raised to its cell's blocked level, and it never
  carries a trunk's or another thing's top. Under each thing it is one
  level, set front to back so a thing behind rests on settled ground.
- *Binding statistics* count a tile at a place once across versions of
  one map (the town on festival day). Copies made the town's stacks look
  bound.

check() now measures in the world, not from the game's camera, so it can
fail:
- things resting on nothing;
- things buried over a quarter of their width;
- buried edge columns;
- overhead pixels stood upright (camera-facing vertical quads);
- crowns taller than wide;
- uneven floors.

| room | resting on nothing | buried > 1/4 | overhead upright px |
|---|---|---|---|
| 03_01 field | 3 | 14 | 646 |
| 07_00 garden | 0 | 0 | 2 |
| 02_00 town | 2 | 23 | 447 |
| 128_00 castle hall | 0 | 2 | 56 |

Most of the upright overhead pixels are the one-pixel fringe along the
bottom of each crown, where an ellipsoid's underside turns vertical. The
buried things are mostly crowns whose sides overhang a raised bank.
