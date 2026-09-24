# Asset pipeline and the no-redistribution rule

## The rule

**The repository holds code that derives assets. It never holds the assets.**

Everything the tools produce — room captures, sprite dumps, textures, voxel
geometry — comes out of the user's own ROM. None of it may be committed or
shipped. A contributor with their own legally obtained ROM runs the pipeline
and gets byte-identical output; that is what makes distributing only the code
sufficient.

`.gitignore` encodes this. When a tool grows a new output directory, it gets
added there **in the same commit that creates it**, not later.

## What is derived, and from what

```
baserom.gba  (never committed)
  │
  ├─ tmc_pc + TMC_ROOMCAP_TMCR  ─────────►  vrdump/room_AA_RR.tmcr   room captures
  │   (via tools/harvest_rooms.py)           (map, collision, entities,
  │                                           palettes, BG VRAM)
  │
  ├─ tmc_pc + TMC_ROOMCAP_SPRITES  ──────►  spritedump/*.png + .json  Link's frames
  │
  ├─ tools/shapefit.py scene  ───────────►  geom/<class>.obj          fitted objects
  │
  ├─ tools/room_explore.py voxel  ───────►  geom/rooms/*.obj          one per room
  │
  └─ tools/room_explore.py worldgen  ────►  area_NN.obj              geometry
                  --texture                  area_NN.png              texture atlas
                                             area_NN.mtl
```

Every arrow is reproducible. Nothing downstream contains information that is
not in the ROM plus this repository.

## Reproducing from scratch

```sh
# 1. capture rooms (needs a TMC_VR build and your own ROM in dist/USA/)
python3 tools/harvest_rooms.py --out vrdump --jobs 8

# 2. generate geometry and textures for one area
python3 tools/room_explore.py worldgen vrdump --area 3 --texture --out geom

# or everything at once
bash tools/voxelate_all.sh
```

Proving the output is reproducible is not automated in the repository yet.
The intended check, `tools/verify_repro.py` (not yet committed), runs the
generator twice and compares every byte. It prints a **pipeline digest**, a
hash over all outputs. Two people with the same ROM should see the same
digest; if they do not, the pipeline has picked up a non-determinism
(unsorted glob, dict ordering, an embedded timestamp) and the promise above
is broken until it is fixed. Its `--break` flag corrupts the second run on
purpose, because a reproducibility check that has never failed has not been
tested.

## Why the texture atlas is the stitched area

`worldgen --texture` writes one PNG per area: the area's rooms composited and
stitched at their world origins. Top-face UVs are then just world position
normalised, which means greedy meshing and texturing do not fight each other —
a 30x12 merged quad maps exactly as a 1x1 one does.

A packed tile atlas would have been the conventional choice and would have
broken this: once two cells' tiles are not adjacent in atlas space, a merged
quad spanning them cannot be mapped, and the mesher would have to stop merging
across tile boundaries.

## What the side faces are

Top-down art has no side art. A wall's visible face in TMC is drawn as part of
the *top-down* tile, not as a separate elevation view, so there is nothing to
look up for a vertical face — the only question is what to invent.

Current choice: a side face samples the footprint it belongs to, so a wall
reads as an extrusion of its own top. It keeps materials consistent and needs
no authoring. It is an invention, and it is worth remembering that it is one:
if sides ever look wrong, that is the assumption to revisit, not a bug.
