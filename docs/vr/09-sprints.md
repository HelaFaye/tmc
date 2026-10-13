# 09 — Sprints to a playable early-game demo

The goal is a playable early-game demo first. A sprint is small enough to
finish, render and review in one sitting. When a fix for an early room also
helps later ones (stairs, doors, houses), do it now and refine it later.

Each sprint ends the same way:
- rebuild the reference scenes;
- render them from the game's own camera and from turned views;
- show the owner;
- commit.

**Reference scenes** (in the early game): Hyrule Town square and gate, the
blue house, the west ledge, Link's house inside and out, Hyrule Field
around it, the castle garden, and the first dungeon room.

## Done

- **Depth model.** Anything drawn h px up stands h px further south. This
  removed the doubled ledge, arch and roof.
- **Hedges:** boxes. **Bushes:** fitted mounds. **Saplings:** a trunk and
  a crown. Cohesive backs on all three.
- **Stairs:** steps sized by their lit tread and shaded riser. Flights
  between the same two levels share one rise.
- **Gate:** two stone blocks, with the arch upright on them. The arch is
  as thick as its lit band and its outline isn't built.
- **Houses:**
  - walls as tall as drawn;
  - rounded roofs as high as drawn, with the light patch as a flat top;
  - chimney and plank;
  - no slot cut over the door;
  - the plan covers only the columns under the roof.

## Sprint 1: the town and the field read right

1. **West stairs cut into the wall.** The steps sit in a notch in the
   upper level, not in front of the cliff.
2. **The gate's bell** hangs inside the arch, not flat on the ground.
3. **The blue house sits into the corner** with the stone wall. Its plan
   meets the wall, with no gap.
4. **Link's house:** check the roof against the drawing from three
   angles. The woodpile and stump stand on the ground.
5. **Holes left by the depth model** at the room's edges and behind
   houses are filled.

## Sprint 2: Link and enemies stand on the world

1. **The level map.** tilevox writes each room's per-cell shift, height
   and level as a small binary file next to its mesh. It holds coordinates
   only, no art.
2. **The VR runtime reads it** (`port/vr/vr_interp.c`): an actor at drawn
   (x, y) stands at the ground height there, moved south by the same
   shift.
3. **The world viewer** uses the same map for its marker and enemies.

## Sprint 3: doors and stairs as parts

1. **Doors.** The leaf is the door's own colours. The frame is the colour
   change round it, a few px proud, with its own grain. A doorway with no
   door is open into a dark recess.
2. **Every flight** keeps each step's own colours. The bottom step gets a
   normal tread rather than becoming a wall.
3. **Stairs beyond a doorway** are the doorway's width.

## Sprint 4: plants and small things with volume

1. **Flower patches.** Each flower stands as a small stalk and head, not
   a 2 px bump.
2. **Grass tufts and tall grass** rise from the ground.
3. **Pots, rocks and stumps:** check them against the review page flags.

## Sprint 5: variable voxel scale

Each object or tile picks the coarsest scale that keeps its detail:
- **Flat ground:** coarse.
- **Faces and props:** 1 px.
- **Fine parts:** 0.5 px. For example, a sign's lettering, the
  Triforce's wings, a door's grain.

The **measure** is the share of a tile's pixels that differ from their
2×2 block's mean. Below a threshold, the tile is built at the coarser
scale.

The **output** is the scale per tile model, recorded in `placements.txt`.
The meshes merge at the boundaries between scales.

## Sprint 6: voxel LoDs

Each tile library and room mesh is written at three LoDs:
- **LoD0:** full scale.
- **LoD1:** 2×. Props as boxes, flat ground merged.
- **LoD2:** 4×. Each cell one box at its height, with its average colour.

The VR runtime picks a LoD by distance from the camera. Rooms beyond the
current one use LoD2.

## Sprint 7: objects split from each other (regions)

Split each room's picture into regions bounded by the outline and colour
changes, then label each region:
- **lit:** a top;
- **mid;**
- **shaded:** a front.

A tile then holds as many objects as are drawn in it. The aim is the
first step of the method in doc 08, where houses are built from parts:
roof faces, walls, door, frame, windows and chimney.

Show the regions on the review page as an overlay, so the split can be
checked first.

## Sprint 8: the rest of the early game

Rebuild and review the remaining early rooms in story order:
- Hyrule Field west and south;
- Minish Woods;
- the Deepwood Shrine;
- Mt. Crenel's foot.

Each room's flags on the review page become its sprint list.
