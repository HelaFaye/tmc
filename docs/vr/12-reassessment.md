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
