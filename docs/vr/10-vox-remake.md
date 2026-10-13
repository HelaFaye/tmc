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
