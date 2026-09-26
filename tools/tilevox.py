#!/usr/bin/env python3
"""tilevox.py -- identify every room's tiles and voxelate them.

The terrain stage (room_explore.py voxel / worldgen) settles HOW HIGH each
cell stands, checked against the game's collision. This stage settles what
each cell LOOKS like up close: every distinct 16x16 drawing becomes one
voxel model at the art's own resolution, one voxel per pixel, and every
cell places the model for its drawing on top of its height.

  identify   A tile is its drawing, both layers composited as the game shows
             them, plus its role (floor, wall, block, water, pit). Keying on
             the pixels rather than the tile index makes a tile the same
             thing wherever it is drawn, whatever tileset slot it came from:
             251,628 cells in 555 rooms come down to about 31,600 drawings,
             at most about 1,600 in one area.

  voxelate   One column per pixel, textured with that pixel, standing one
             voxel plus a relief taken from the drawing's own shading on
             2px blocks -- lit grass tufts and stones stand up to RELIEF
             voxels proud, outlines and shadow stay down. A first model,
             meant to be tuned per role later.

  place      Each cell's model sits on the cell's height (the same
             heightfield the terrain uses: collision classes, measured
             faces, blocks, sprite footprints flattened), rooms stacked into
             levels as worldgen does. Vertical faces where a cell drops to a
             lower neighbour are built here too, one voxel row at a time,
             each row coloured by the art the game draws there (drawn row =
             z - height, the 45-degree rule) -- so a wall's front shows its
             drawn front, not its top stretched down.

Outputs, per area, under --out (default geom/tiles; gitignored -- derived
from your ROM):
  area_NN/tiles.obj      the tile library: one object per drawing, local
                         coordinates, 16x16 base at y=0, +Z south,
                         textured from area_NN/tiles.png (the drawings)
  area_NN/placements.txt one line per cell: key room cx cy x y z role
  area_NN/room_AA_RR.obj (--merge) the room assembled: placed tiles plus
                         the vertical faces, world coordinates, textured
                         from room_AA_RR.png (the room's art), ready to view

Usage:
  python3 tools/tilevox.py states/p0 --area 0 --merge
  python3 tools/tilevox.py states/p0 --merge --jobs 8      # every area
"""
import argparse
import hashlib
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402

# Relief in voxels above the one-voxel base, per role. Water and pits are
# flat: their drawn ripples and shading are not shape.
RELIEF = {"floor": 1, "wall": 2, "block": 1, "water": 0, "pit": 0}
RELIEF_STEP = 2         # px: relief is measured on blocks this size
FLAT_SPREAD = 12.0      # luminance spread below which a drawing is flat


# --------------------------------------------------------------- identify --

def room_roles(r, cls, H, blocks):
    """Per cell role, or None where nothing is built (void)."""
    h, w = r.cells_h, r.cells_w
    role = np.empty((h, w), dtype=object)
    for cy in range(h):
        for cx in range(w):
            c = cls[cy, cx]
            if c == RE.CLASS_VOID:
                role[cy, cx] = None
            elif (cy, cx) in blocks:
                role[cy, cx] = "block"
            elif c == RE.CLASS_WATER:
                role[cy, cx] = "water"
            elif c == RE.CLASS_HOLE:
                role[cy, cx] = "pit"
            elif H[cy, cx] > 0:
                role[cy, cx] = "wall"
            else:
                role[cy, cx] = "floor"
    return role


def room_heights(r, layer=0, blocks=True, relief=False):
    """The terrain's cell heights, built exactly as `voxel` builds them."""
    cls = RE.classify_room(r, layer)
    H = np.array(RE.heightfield(r, cls, layer), dtype=np.int64)
    if relief:
        Hr, _solid = RE.relief_field(r, cls, 16, layer)
        H = np.array(Hr, dtype=np.int64)
    bl = RE.block_cells(r, cls, layer) if blocks else {}
    if bl:
        H = RE.block_heights(H, bl, 16)
    H = RE.flatten_cells(H, RE.sprite_footprints(r, cls, layer), 16)
    return cls, H, bl


def tile_key(pixels, role):
    return hashlib.sha1(pixels.tobytes()).hexdigest()[:12] + role[0]


# -------------------------------------------------------------- voxelate --

def tile_relief(px, R, step=None):
    """Relief 0..R per pixel from the drawing's own shading.

    Measured on step x step blocks: per pixel, dithering and outlines turn
    the relief into noise -- 160 quads a cell in Minish Woods -- where 2px
    blocks keep the grass tufts and stones and cost 40. The texture stays
    at full resolution either way.
    """
    step = step or RELIEF_STEP
    if R <= 0:
        return np.zeros(px.shape[:2], np.int64)
    lum = px[:, :, :3].astype(float).mean(axis=2)
    n = 16 // step
    b = lum.reshape(n, step, n, step).mean(axis=(1, 3))
    lo, hi = np.percentile(b, 10), np.percentile(b, 90)
    if hi - lo < FLAT_SPREAD:
        return np.zeros(lum.shape, np.int64)
    t = np.rint(np.clip((b - lo) / (hi - lo), 0.0, 1.0) * R).astype(np.int64)
    return np.kron(t, np.ones((step, step), np.int64))


def tile_quads(px, R):
    """The voxel model of one drawing: [(4 corners, 4 texels)], y up from 0.

    Columns of 1 + relief voxels, one per pixel. Colour comes from the
    drawing through texture coordinates -- (s, t) in the tile's own pixels
    -- so faces merge by SHAPE alone: a flat floor tile is one top quad
    whatever its dithering, where one colour per face made it ninety.
    Tops are greedy-merged by height. A side is emitted where a column
    stands above its neighbour, or above the floor at the tile's edge,
    merged along its row; its texels run along the pixels it borders, so
    each column's side shows that column's pixel.
    """
    h = 1 + tile_relief(px, R)
    quads = []
    for y in sorted(set(int(v) for v in np.unique(h))):
        for x, z, w, d in RE.greedy_quads(h == y):
            quads.append(([(x, y, z), (x + w, y, z), (x + w, y, z + d), (x, y, z + d)],
                          [(x, z), (x + w, z), (x + w, z + d), (x, z + d)]))

    def nb(z, x):
        return int(h[z, x]) if 0 <= z < 16 and 0 <= x < 16 else 0

    for dz in (1, -1):              # faces along x, merged along x
        for z in range(16):
            run = None
            for x in range(17):
                seg = None
                if x < 16:
                    lo, hi = nb(z + dz, x), int(h[z, x])
                    if hi > lo:
                        seg = (lo, hi)
                if run and seg == run[1]:
                    continue
                if run:
                    x0, (lo, hi) = run
                    zz, t = (z + 1 if dz == 1 else z), z + 0.5
                    if dz == 1:
                        p = [(x0, hi, zz), (x, hi, zz), (x, lo, zz), (x0, lo, zz)]
                        uv = [(x0, t), (x, t), (x, t), (x0, t)]
                    else:
                        p = [(x, hi, zz), (x0, hi, zz), (x0, lo, zz), (x, lo, zz)]
                        uv = [(x, t), (x0, t), (x0, t), (x, t)]
                    quads.append((p, uv))
                run = (x, seg) if seg else None
    for dx in (1, -1):              # faces along z, merged along z
        for x in range(16):
            run = None
            for z in range(17):
                seg = None
                if z < 16:
                    lo, hi = nb(z, x + dx), int(h[z, x])
                    if hi > lo:
                        seg = (lo, hi)
                if run and seg == run[1]:
                    continue
                if run:
                    z0, (lo, hi) = run
                    xx, sx = (x + 1 if dx == 1 else x), x + 0.5
                    if dx == 1:
                        p = [(xx, hi, z0), (xx, hi, z), (xx, lo, z), (xx, lo, z0)]
                        uv = [(sx, z0), (sx, z), (sx, z), (sx, z0)]
                    else:
                        p = [(xx, hi, z), (xx, hi, z0), (xx, lo, z0), (xx, lo, z)]
                        uv = [(sx, z), (sx, z0), (sx, z0), (sx, z)]
                    quads.append((p, uv))
                run = (z, seg) if seg else None
    return quads


# ----------------------------------------------------------------- faces --

def drop_faces(H, solid, ox, oz, lift, outside):
    """Vertical faces where a cell drops to a lower neighbour.

    Texels are in room-art pixels. A south face d voxels tall takes the d
    rows drawn above its foot, bottom row at the foot (drawn row = z -
    height) -- the drawn front of the thing: a door frame, a cliff face --
    one quad, since that mapping is linear. Faces the game never draws
    (north, east, west) stretch the cell's own edge row or column, an
    extrusion of its top.
    """
    quads = []
    rows, cols = H.shape
    for cy in range(rows):
        for cx in range(cols):
            if not solid[cy, cx]:
                continue
            hh = int(H[cy, cx])
            x0, z0 = cx * 16, cy * 16
            X0, X1, Z0, Z1 = ox + x0, ox + x0 + 16, oz + z0, oz + z0 + 16
            for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                ny, nx = cy + dz, cx + dx
                nh = (int(H[ny, nx]) if 0 <= ny < rows and 0 <= nx < cols
                      and solid[ny, nx] else outside)
                if nh >= hh:
                    continue
                a, b = hh + lift, nh + lift
                d = hh - nh
                if dz == 1:
                    foot = z0 + 16
                    quads.append(([(X0, a, Z1), (X1, a, Z1), (X1, b, Z1), (X0, b, Z1)],
                                  [(x0, foot - d), (x0 + 16, foot - d),
                                   (x0 + 16, foot), (x0, foot)]))
                elif dz == -1:
                    t = z0 + 0.5
                    quads.append(([(X1, a, Z0), (X0, a, Z0), (X0, b, Z0), (X1, b, Z0)],
                                  [(x0 + 16, t), (x0, t), (x0, t), (x0 + 16, t)]))
                elif dx == 1:
                    sx = x0 + 15.5
                    quads.append(([(X1, a, Z0), (X1, a, Z1), (X1, b, Z1), (X1, b, Z0)],
                                  [(sx, z0), (sx, z0 + 16), (sx, z0 + 16), (sx, z0)]))
                else:
                    sx = x0 + 0.5
                    quads.append(([(X0, a, Z1), (X0, a, Z0), (X0, b, Z0), (X0, b, Z1)],
                                  [(sx, z0 + 16), (sx, z0), (sx, z0), (sx, z0 + 16)]))
    return quads


# ------------------------------------------------------------------ write --

class Obj:
    """Minimal textured OBJ writer: one texture, UVs from texel coords."""

    def __init__(self, path, header, texture, tw, th):
        self.path = Path(path)
        mtl = self.path.with_suffix(".mtl")
        with open(mtl, "w") as m:
            m.write("# Derived from the user's own ROM. Not redistributable.\n"
                    "newmtl tmc\nKa 1 1 1\nKd 1 1 1\nd 1\nillum 1\n"
                    f"map_Kd {texture}\n")
        self.f = open(self.path, "w")
        self.f.write(header + f"mtllib {mtl.name}\nusemtl tmc\n")
        self.tw, self.th = float(tw), float(th)
        self.n = 0
        self.faces = 0

    def obj(self, name):
        self.f.write(f"o {name}\n")

    def quads(self, quads, off=(0, 0, 0), toff=(0, 0)):
        ox, oy, oz = off
        su, sv = toff
        seen = {}
        lines, fl = [], []
        for pts, uvs in quads:
            idx = []
            for (x, y, z), (u, v) in zip(pts, uvs):
                k = (x + ox, y + oy, z + oz, u + su, v + sv)
                i = seen.get(k)
                if i is None:
                    self.n += 1
                    i = seen[k] = self.n
                    lines.append(f"v {k[0]} {k[1]} {k[2]}\n"
                                 f"vt {k[3] / self.tw:.6f} {1.0 - k[4] / self.th:.6f}\n")
                idx.append(i)
            fl.append("f %d/%d %d/%d %d/%d %d/%d\n"
                      % (idx[0], idx[0], idx[1], idx[1], idx[2], idx[2], idx[3], idx[3]))
        self.f.write("".join(lines))
        self.f.write("".join(fl))
        self.faces += len(fl)

    def close(self):
        self.f.close()


HEADER = ("# Derived from the user's own ROM; regenerate, do not redistribute.\n"
          "# world-pixel units, +X east, +Y up, +Z south\n")


ATLAS_COLS = 64                 # tiles per atlas row


def build_area(job):
    from PIL import Image
    global RELIEF_STEP
    area, paths, a = job
    RELIEF_STEP = a.relief_step
    rooms = []
    for p in paths:
        try:
            r = RE.load_room(Path(p))
        except Exception:
            continue
        if r.cells_w and r.cells_h and r.layers[a.layer]["present"]:
            rooms.append(r)
    if not rooms:
        return area, 0, 0, 0, 0
    out = Path(a.out) / f"area_{area:02d}"
    out.mkdir(parents=True, exist_ok=True)
    levels = RE.assign_levels(rooms)

    # identify: every cell's drawing and role, keyed; heights as the terrain
    lib = {}                      # key -> (index, pixels, role)
    placed = []                   # (r, lift, art, H, solid, [(key, cx, cy, y)])
    for li, lv in enumerate(levels):
        lift = li * a.floor_height
        for r in lv:
            art = RE.room_art_rgb(r, a.layer)
            if art is None:
                continue
            art = np.ascontiguousarray(art[:, :, :3].astype(np.uint8))
            cls, H, bl = room_heights(r, a.layer, blocks=not a.no_blocks,
                                      relief=a.relief)
            role = room_roles(r, cls, H, bl)
            cells = []
            for cy in range(r.cells_h):
                for cx in range(r.cells_w):
                    ro = role[cy, cx]
                    if ro is None:
                        continue
                    pix = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                    if pix.shape[:2] != (16, 16):
                        continue
                    k = tile_key(pix, ro)
                    if k not in lib:
                        lib[k] = (len(lib), pix, ro)
                    cells.append((k, cx, cy, int(H[cy, cx]) + lift, ro))
            placed.append((r, lift, art, H, cls != RE.CLASS_VOID, cells))

    # voxelate: one model per drawing, packed into the area's atlas
    n = len(lib)
    arows = max(1, (n + ATLAS_COLS - 1) // ATLAS_COLS)
    atlas = np.zeros((arows * 16, ATLAS_COLS * 16, 3), np.uint8)
    models = {}
    lib_obj = Obj(out / "tiles.obj", HEADER + f"# area {area}: tile library, "
                  f"{n} drawings; atlas tiles.png, {ATLAS_COLS} per row\n",
                  "tiles.png", ATLAS_COLS * 16, arows * 16)
    for k, (i, pix, ro) in lib.items():
        ty, tx = divmod(i, ATLAS_COLS)
        atlas[ty * 16:ty * 16 + 16, tx * 16:tx * 16 + 16] = pix
        models[k] = tile_quads(pix, RELIEF[ro])
        lib_obj.obj(f"t_{k}")
        lib_obj.quads(models[k], toff=(tx * 16, ty * 16))
    lib_obj.close()
    Image.fromarray(atlas).save(out / "tiles.png")

    # place
    nfaces = ncell = 0
    with open(out / "placements.txt", "w") as place:
        place.write("# key room cx cy x y z role -- tile model origin, world pixels\n")
        for r, lift, art, H, solid, cells in placed:
            name = f"room_{r.area:02d}_{r.room:02d}"
            ox, oz = r.origin_x, r.origin_y
            for k, cx, cy, y, ro in cells:
                place.write(f"{k} {name} {cx} {cy} {ox + cx * 16} {y} "
                            f"{oz + cy * 16} {ro}\n")
            ncell += len(cells)
            if not a.merge:
                continue
            Image.fromarray(art).save(out / f"{name}.png")
            m = Obj(out / f"{name}.obj", HEADER + f"# {name}: tiles placed on the terrain\n",
                    f"{name}.png", art.shape[1], art.shape[0])
            m.obj(name)
            for k, cx, cy, y, ro in cells:
                m.quads(models[k], (ox + cx * 16, y, oz + cy * 16), (cx * 16, cy * 16))
            m.quads(drop_faces(H, solid, ox, oz, lift, a.outside))
            nfaces += m.faces
            m.close()
    return area, len(rooms), ncell, n, nfaces


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", help="directory of .tmcr dumps, or one dump")
    ap.add_argument("--area", default="", help="comma-separated areas; default all")
    ap.add_argument("--out", default="geom/tiles")
    ap.add_argument("--layer", type=int, default=0)
    ap.add_argument("--merge", action="store_true",
                    help="also write each room assembled, for viewing")
    ap.add_argument("--relief", action="store_true",
                    help="cell heights from room_explore --relief")
    ap.add_argument("--no-blocks", action="store_true",
                    help="leave dungeon blocks to the measured heights")
    ap.add_argument("--relief-step", type=int, default=RELIEF_STEP,
                    choices=(1, 2, 4, 8, 16),
                    help="px block the relief is measured on (1 = per pixel)")
    ap.add_argument("--floor-height", type=int, default=64)
    ap.add_argument("--outside", type=int, default=-16)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()

    src = Path(a.dumps)
    files = [src] if src.is_file() else sorted(src.glob("room_*.tmcr"))
    by_area = {}
    for f in files:
        try:
            area = int(f.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        by_area.setdefault(area, []).append(str(f))
    want = ([int(x, 10) for x in a.area.split(",")] if a.area else sorted(by_area))
    jobs = [(ar, by_area[ar], a) for ar in want if ar in by_area]
    if not jobs:
        sys.exit("no rooms")
    tot = [0, 0, 0, 0]
    print(f"{'area':>5} {'rooms':>6} {'cells':>7} {'tiles':>6} {'faces':>9}")
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for area, nr, nc, nt, nf in ex.map(build_area, jobs):
            print(f"{area:5d} {nr:6d} {nc:7d} {nt:6d} {nf:9d}", flush=True)
            for i, v in enumerate((nr, nc, nt, nf)):
                tot[i] += v
    print(f"\n{tot[0]} rooms, {tot[1]} cells placed from {tot[2]} tile models "
          f"(per-area libraries) -> {a.out}")


if __name__ == "__main__":
    main()
