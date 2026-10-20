# 10 — The tile stage remade (`tools/vox/`)

`tilevox.py` grew a fix at a time: tiles as the unit, two depth rules,
hidden faces guessed from the screen, 132 constants each tuned on one
room, and patches over patches (doc 08). `tools/vox/` starts again from
what held up. `tilevox.py` stays as the working build until `vox` covers
the reference scenes.

## The one rule

The game's camera draws a point at height `y` over depth `z` on row
`z − y`. So every drawn pixel needs one number, its height. Its depth
follows: `z = row + y`.

Whatever heights are chosen, the game's own camera sees exactly the
drawing. That rules out doubling by construction. What is left is to
choose heights that are right from every other angle. The drawing's
shading says how they run:

| Shading | Surface | Height |
|---|---|---|
| lit | a top (ground, a roof's flat, a tread) | level: one `y` across the region |
| shaded | a front (a wall, a riser) | `z` level: `y` climbs one per row up from its foot |
| mid | a slope | between the two |

Where regions meet:
- a front stands on the ground in front of it;
- a top starts at the top of the front below it.

That covers ledges, hedges, steps, house walls and roofs, blocks and
arches with one solver.

## Stages

1. **Room** (`room.py`): the picture the game shows, actors removed (the
   entity stage builds those); the ground and overhead layers; collision
   classes.
2. **Regions** (`regions.py`): connected runs of one material (colour
   family), with the drawn outline as their border. Each pixel is lit,
   mid or shaded relative to its region. A tile can hold several
   regions, which keeps objects in one tile apart.
3. **Heights** (next): solve each region's heights from its shading and
   its neighbours, with the ground's levels from collision and stairs.
4. **Mesh** (next): the solved surface as quads, in drawn space. Each
   region closes its volume behind it, and the faces the game never draws
   wear the region's own pixels (mirrored), never its neighbours'.
5. **Parts and kits** (later): repeated objects (houses, the gate, a
   flight of steps) recognised once, their parts stored as coordinates,
   reviewed, and reused.

## Rules for working on it

- Every change is checked on the reference scenes (`vox/scenes.py`)
  before it is committed: from the game's camera, and from turned views.
- A constant is relative to the room (its palette, its regions), not
  tuned to one room. Each one says what it measures.
- No stage patches another's output. If a later stage needs something,
  the earlier stage produces it.
- Renders hold ROM-derived art and stay out of the repository.

## Posterizing first (tried)

`vox/poster.py` reduces a room's picture to `POSTER_COLOURS` colours
(median cut) before shapes and shading are read from it
(`scenes.py --poster`). At 24 colours it does not help yet:
- merging similar colours also merges the tones that tell a top from a
  front, so more fronts read as tops;
- the mushroom stack gains fins, the blue house a stray slab;
- Link's house and the gate barely change.

It stays an option, off by default. Worth trying again per material
(posterize within a region, not across the room), so tones are kept and
only texture folds.

## Scope: the early game first

Playable through the story scenes before Link leaves the castle:
- Link's house, inside and out;
- Hyrule Field around it;
- Hyrule Town on festival day (square, gate, houses, shops);
- the road to the castle;
- Hyrule Castle's garden, gate and the rooms of the opening scenes.

Every room on that route joins the reference scenes before it is called
done.

## Classes and labels

Buildings are grouped by:
- **style:** a domed cap (mushroom), a thatched roof, a slate roof with
  a flat top, a stone block;
- **material:** the roof's and walls' colour family;
- **height:** drawn wall height and roof rise, measured.

Objects take the same three, plus a **shape cue**:
- mound (bush, rock, pot);
- stand (sapling, post);
- box (crate, table);
- slab (sign, fence);
- arch;
- step.

A group shares one way of being built, and a correction to one member
is a correction to the group.

## What may be committed (assets)

Nothing that reproduces the game's art:
- no colours, textures, palettes or atlases taken from the ROM;
- no rendered pictures;
- no voxels carrying colour.

The rule is on the safe side of derivative-work concerns: a coloured
model that "looks like the tile from the game's camera" is a copy of the
art by design.

What may be committed is structure:
- shapes as heights or occupancy;
- labels (class, style, material class, shape cue);
- tile and metatile indices, room and cell coordinates;
- UVs into a texture the player's own build extracts from their own ROM.

The coloured result is made on the player's machine, from their ROM, by
the same code. The repository already treats the ROM this way (AGENTS.md).

Geometry that closely traces drawn silhouettes is still a grey area, far
weaker than shipping art. Labels and indices are the safest.

This is a project policy, not legal advice.
