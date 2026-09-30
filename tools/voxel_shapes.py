#!/usr/bin/env python3
"""voxel_shapes.py -- write the game's 3D view overrides from what the tile
stage knows.

Picori's 3D room view (port/port_voxel.cpp) guesses each cell's shape from
collision alone: a fully solid cell (collision 0x0F, not water or a hole)
stands up, the rest lies flat; a solid run stands its southmost rows up as a
wall `wall` tiles tall. What it reads wrong is patched per area in
voxel_shapes.json, by hand, from F8, a tile at a time:

  { "<area>": { "wall": 2, "tiles": { "<tileType>": "floor" | "block" | "prop" } } }

The tile stage (tileid.py, tilevox.py, tile_catalog.py) already says what
every cell is. This writes the file from it: for each area's tile types,
the shape the tile stage gives them --

  prop    a thing standing on the ground: bush, sapling, rock, pot, stump,
          mushroom, stone, boulder, spiky rock, planter; a fence or a
          signpost (drawn upright)
  block   foliage (hedges, field trees); the terrain's walls
  floor   floor, grass, flowers

-- where that differs from the view's own guess, in AGREE of the type's
cells. Water, stairs and whatever the tile stage has no word for stay the
view's own. `wall` is not written: the tile stage's heights give a wall
cell its class height (16 px), not the drawn height of its front, so an
area keeps the view's default (or a hand edit) until they do.

An existing file is merged, not replaced: what is in it already -- a hand
edit from F8 -- wins, tile by tile and wall by wall (--replace to start
over).

The file holds tile type numbers and shape names only, no art.

Usage:
  voxel_shapes.py DUMPS [--out voxel_shapes.json] [--areas 3,34] [--replace]
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402
import tileid as TI  # noqa: E402
import tilevox as TV  # noqa: E402

AGREE = 0.8             # of a tile type's cells that must want the same shape
SOLID_COLLISION = 0x0F  # port_voxel.cpp SolidTile()
NOT_WALL_ACT = {0x0F, 0x10, 0x11, 0x19, 0xF0}  # water, shallows, holes

PROP = set(TI.PROP_FAMILIES) | set(TI.UPRIGHT_FAMILIES)
BLOCK = {"foliage", "(wall)", "(block)"}
FLOOR = {"flowers", "(floor)", "(grass)", "(indoor)"}


def shape_of(label):
    if label in PROP:
        return "prop"
    if label in BLOCK:
        return "block"
    if label in FLOOR:
        return "floor"
    return None


def room_cells(path):
    """(area, [(tileType, ours, theirs)]) for a room."""
    try:
        r = RE.load_room(Path(path))
        rgb = RE.room_art_rgb(r, 0)
        if rgb is None:
            return None
        art = np.ascontiguousarray(rgb[:, :, :3].astype(np.uint8))
        cls, H, bl, _fl, _d = TV.room_heights(r, 0, path=str(path))
        fam, _floor = TI.families(r, cls, H, art)
        role = TV.room_roles(r, cls, H, bl, art)
    except Exception as e:          # a room the tools cannot read: say so
        print(f"  {Path(path).name}: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    L = r.layers[0]
    if L.get("bpp8"):               # a picture, not tiles: no tile types
        return None
    h, w = r.cells_h, r.cells_w
    t = L["tile"][:h, :w].astype(int)
    tt = L["tiletype"][np.clip(t, 0, len(L["tiletype"]) - 1)].astype(int)
    coll = L["collision"][:h, :w].astype(int)
    act = L["act"][:h, :w].astype(int)
    cells = []
    for y in range(h):
        for x in range(w):
            if t[y, x] >= 0x4000:   # special tiles: keyed otherwise in the view
                continue
            label = fam[y, x] or f"({role[y, x] or 'none'})"
            wet = act[y, x] in NOT_WALL_ACT   # water, holes: the view's own
            ours = None if wet else shape_of(label)
            solid = coll[y, x] == SOLID_COLLISION and not wet
            cells.append((int(tt[y, x]), ours, "block" if solid else "floor"))
    return r.area, cells


def shapes(files, jobs=0):
    per = defaultdict(lambda: defaultdict(list))   # area -> type -> [(ours, theirs)]
    with ProcessPoolExecutor(jobs or None) as ex:
        for got in ex.map(room_cells, [str(f) for f in files], chunksize=4):
            if got is None:
                continue
            area, cells = got
            for ty, ours, theirs in cells:
                per[area][ty].append((ours, theirs))
    out = {}
    for area in sorted(per):
        tiles = {}
        for ty, cs in sorted(per[area].items()):
            said = [o for o, _ in cs if o]
            if not said:
                continue
            best, n = Counter(said).most_common(1)[0]
            if n < AGREE * len(cs):
                continue            # the type's cells disagree: leave it
            differ = sum(1 for o, th in cs if o == best and th != best)
            # a prop is never the view's own guess for a run of solid tiles
            if best == "prop" or differ >= len(cs) / 2:
                tiles[str(ty)] = best
        if tiles:
            out[str(area)] = {"tiles": tiles}
    return out


def merge(old, new):
    """new under old: what the file has already (a hand edit) wins."""
    out = {k: dict(v, tiles=dict(v.get("tiles", {}))) for k, v in new.items()}
    for area, v in old.items():
        o = out.setdefault(area, {"tiles": {}})
        if "wall" in v:
            o["wall"] = v["wall"]
        o["tiles"].update(v.get("tiles", {}))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--out", default="voxel_shapes.json")
    ap.add_argument("--areas", default="", help="comma list of area numbers (default all)")
    ap.add_argument("--replace", action="store_true", help="ignore an existing file")
    ap.add_argument("-j", "--jobs", type=int, default=0)
    a = ap.parse_args()
    files = sorted(Path(a.dumps).glob("room_*.tmcr"))
    if a.areas:
        want = {int(s) for s in a.areas.split(",")}
        files = [f for f in files if int(f.stem.split("_")[1]) in want]
    new = shapes(files, a.jobs)
    out = Path(a.out)
    old = {}
    if out.is_file() and not a.replace:
        old = json.loads(out.read_text())
    res = merge(old, new)
    out.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    nt = sum(len(v["tiles"]) for v in res.values())
    kinds = Counter(s for v in res.values() for s in v["tiles"].values())
    print(f"{len(files)} rooms -> {out}: {len(res)} areas, {nt} tile overrides "
          f"({', '.join(f'{k} {n}' for k, n in kinds.most_common())})"
          + (f"; kept {sum(len(v.get('tiles', {})) for v in old.values())} from the file" if old else ""))


if __name__ == "__main__":
    main()
