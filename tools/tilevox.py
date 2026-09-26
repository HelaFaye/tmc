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
             lower neighbour are built here too, textured with the rows
             the game draws above the cell's foot (drawn row = z - height,
             the 45-degree rule) -- so a wall's front shows its drawn front,
             not its top stretched down, and its undrawn flanks wear the
             same band.

  stairs     A flight of steps joins two floors the flat terrain put at
             one height. Where its landings are not otherwise connected,
             the upper one is raised by the height of the cliff beside the
             flight (or its drawn risers), and the flight is built as the
             steps find_stairs.py counts, spaced as drawn.

  overlay    Where the top layer is drawn above the bottom one (outdoors),
             its cells are tiles too, cut out along the layer's
             transparency -- roofs, bridge decks, fence and planter tops
             -- lifted OVERLAY_LIFT like the terrain overlay, with aprons
             down to the ground where it comes close. Tree crowns keep the
             terrain's crown shape (room_explore.canopy_field), textured
             from where their leaves are drawn.

Outputs, per area, under --out (default geom/tiles; gitignored -- derived
from your ROM):
  area_NN/tiles.obj      the tile library: one object per drawing, local
                         coordinates, 16x16 base at y=0, +Z south,
                         textured from area_NN/tiles.png (the drawings)
  area_NN/placements.txt one line per cell: key room cx cy x y z role
  area_NN/stairs.txt     every flight of steps: its cells, step count, and
                         the landings it joins
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
RELIEF = {"floor": 1, "wall": 2, "block": 1, "water": 0, "pit": 0, "deck": 1}
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


def room_heights(r, layer=0, blocks=True, relief=False, path=None):
    """The terrain's cell heights, built exactly as `voxel` builds them,
    plus the upper landings of flights of steps when path is given
    (stair_levels). Returns (cls, H, blocks, flights)."""
    cls = RE.classify_room(r, layer)
    H = np.array(RE.heightfield(r, cls, layer), dtype=np.int64)
    if relief:
        Hr, _solid = RE.relief_field(r, cls, 16, layer)
        H = np.array(Hr, dtype=np.int64)
    bl = RE.block_cells(r, cls, layer) if blocks else {}
    if bl:
        H = RE.block_heights(H, bl, 16)
    H = RE.flatten_cells(H, RE.sprite_footprints(r, cls, layer), 16)
    flights = []
    if path is not None and layer == 0:
        H, flights = stair_levels(path, r, cls, H)
    return cls, H, bl, flights


# ----------------------------------------------------------------- stairs --
# A flight of steps (find_stairs.py: cells whose action byte is a slope)
# joins two floors. The flat terrain puts every walkable cell at 0, so both
# landings of 76 of the game's 90 layer-0 flights came out level and the
# steps had nothing to climb. stair_levels restores the upper landing, then
# stair_quads builds the steps the drawing counts.
STAIR_RISE = (8, 48)            # px: plausible total rise of one flight
STAIR_MAX_SHARE = 0.4           # never raise more of a room's floor than this


def stair_levels(path, r, cls, H):
    """Raise the upper landing of every flight; return (H, flights).

    For each flight, the floor on either side of it (stair cells excluded)
    is split into connected regions. If the two landings are one region --
    reachable from each other some other way -- they are one level and the
    flight is left as drawn relief. Otherwise the landing on the up side
    is raised: north for a north-south flight (its risers face the camera,
    so it climbs away from it), the smaller region for an east-west one.

    The rise is the drawn height of the faces beside the flight -- the
    cliff the stairs climb, measured by the terrain -- or, without one, the
    flight's own risers summed (dark rows = height, the 45-degree rule).
    Blocked cells standing mostly on the raised floor (a tree, a pillar on
    the plateau) go up with it.
    """
    import find_stairs as FS
    try:
        found = FS.find(Path(path))
    except Exception:
        return H, []
    flights = [it for it in found if it["kind"] == "steps" and it["layer"] == 0
               and (it.get("measure") or {}).get("steps")]
    if not flights:
        return H, []
    H = np.array(H, dtype=np.int64)
    rows, cols = H.shape
    stair = np.zeros((rows, cols), bool)
    for it in flights:
        x0, y0, x1, y1 = it["rect"]
        stair[y0:y1 + 1, x0:x1 + 1] = True
    floor = (cls == RE.CLASS_GROUND) & ~stair
    total = max(1, int(floor.sum()))
    out = []
    for it in flights:
        x0, y0, x1, y1 = it["rect"]
        m = it["measure"]
        ns = it["rise"] == "ns"
        lab, _n = RE._label(floor)
        if ns:
            sa = [(y0 - 1, x) for x in range(x0, x1 + 1)]       # north: up
            sb = [(y1 + 1, x) for x in range(x0, x1 + 1)]
        else:
            sa = [(y, x0 - 1) for y in range(y0, y1 + 1)]       # west
            sb = [(y, x1 + 1) for y in range(y0, y1 + 1)]       # east
        inside = lambda c: 0 <= c[0] < rows and 0 <= c[1] < cols
        la = {int(lab[c]) for c in sa if inside(c) and lab[c]}
        lb = {int(lab[c]) for c in sb if inside(c) and lab[c]}
        rec = dict(rect=it["rect"], rise=it["rise"], steps=m["steps"],
                   bands=m["bands"], up=None, base=None, top=None)
        if not la or not lb or la & lb:
            out.append(rec)                     # one level: leave as drawn
            continue
        area = lambda ids: int(np.isin(lab, list(ids)).sum())
        if ns:
            up, dn, up_side = la, lb, "n"
        else:
            up, dn, up_side = ((la, lb, "w") if area(la) <= area(lb)
                               else (lb, la, "e"))
        region = np.isin(lab, list(up))
        if region.sum() > STAIR_MAX_SHARE * total:
            out.append(rec)
            continue
        base = int(max(H[c] for c in (sb if up is la else sa) if inside(c)))
        # faces beside the flight: blocked cells level with it, on either side
        faces = []
        if ns:
            for y in range(y0, y1 + 1):
                for x in (x0 - 1, x1 + 1):
                    if inside((y, x)) and cls[y, x] in (RE.CLASS_WALL, RE.CLASS_LEDGE):
                        faces.append(int(H[y, x]) - base)
        risers = int(sum(b[3] for b in m["bands"]))
        rise = int(np.median(faces)) if faces else risers
        if not (STAIR_RISE[0] <= rise <= STAIR_RISE[1]):
            rise = risers
        rise = int(min(max(rise, STAIR_RISE[0]), STAIR_RISE[1]))
        rise = 2 * ((rise + 1) // 2)
        top = base + rise
        H[region & (H < top)] = top
        blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & ~stair
        P = np.pad(region, 1)
        nb = (P[:-2, 1:-1].astype(int) + P[2:, 1:-1] + P[1:-1, :-2] + P[1:-1, 2:])
        H[blocked & (nb >= 3)] += rise
        rec.update(up=up_side, base=base, top=top)
        out.append(rec)
    return H, out


def stair_quads(fl, ox, oz, lift):
    """The steps of one raised flight, textured from the game's camera.

    Steps as the drawing counts them, spaced by the drawn pitches and split
    in height by the drawn risers (falling back to even steps), rising from
    the lower landing to the upper across the flight's cells. Each is a
    solid block down to the base: its tread, its riser facing down the
    flight, its two flanks. Texels project the room art from the game's
    camera, (x, z - height), so each tread takes the rows where it is drawn.
    """
    if fl.get("up") is None:
        return []
    x0, y0, x1, y1 = fl["rect"]
    n = int(fl["steps"])
    bands = fl["bands"] or [(0, 1, 1, 1)] * n
    pitch = np.array([max(1, b - a) for a, b, _t, _r in bands], float)
    riser = np.array([max(0, rr) for _a, _b, _t, rr in bands], float)
    if riser.sum() <= 0:
        riser = np.ones(n)
    # bands run from the top of the drawing down: the first is the top step
    pitch, riser = pitch[::-1], riser[::-1]
    base, top = fl["base"] + lift, fl["top"] + lift
    hs = base + np.rint(np.cumsum(riser) / riser.sum() * (top - base)).astype(int)
    X0, X1 = ox + x0 * 16, ox + (x1 + 1) * 16
    Z0, Z1 = oz + y0 * 16, oz + (y1 + 1) * 16
    up = fl["up"]
    L = (Z1 - Z0) if up == "n" else (X1 - X0)
    cuts = np.rint(np.concatenate([[0], np.cumsum(pitch)]) / pitch.sum() * L).astype(int)

    def uv(pts):
        return [(x - ox, (z - oz) - (y - lift)) for x, y, z in pts]

    quads = []
    prev = base
    for i in range(n):
        h = int(hs[i])
        a_, b_ = int(cuts[i]), int(cuts[i + 1])      # from the low end
        if b_ <= a_ or h <= base:
            prev = max(prev, h)
            continue
        if up == "n":          # low end south, rising north
            za, zb = Z1 - b_, Z1 - a_
            boxes = [
                [(X0, h, za), (X1, h, za), (X1, h, zb), (X0, h, zb)],          # tread
                [(X0, h, zb), (X1, h, zb), (X1, prev, zb), (X0, prev, zb)],    # riser
                [(X1, h, za), (X1, h, zb), (X1, base, zb), (X1, base, za)],    # east
                [(X0, h, zb), (X0, h, za), (X0, base, za), (X0, base, zb)],    # west
            ]
        elif up == "w":        # low end east, rising west
            xa, xb = X1 - b_, X1 - a_
            boxes = [
                [(xa, h, Z0), (xb, h, Z0), (xb, h, Z1), (xa, h, Z1)],
                [(xb, h, Z0), (xb, h, Z1), (xb, prev, Z1), (xb, prev, Z0)],
                [(xa, h, Z1), (xb, h, Z1), (xb, base, Z1), (xa, base, Z1)],
                [(xb, h, Z0), (xa, h, Z0), (xa, base, Z0), (xb, base, Z0)],
            ]
        else:                  # low end west, rising east
            xa, xb = X0 + a_, X0 + b_
            boxes = [
                [(xa, h, Z0), (xb, h, Z0), (xb, h, Z1), (xa, h, Z1)],
                [(xa, h, Z1), (xa, h, Z0), (xa, prev, Z0), (xa, prev, Z1)],
                [(xa, h, Z1), (xb, h, Z1), (xb, base, Z1), (xa, base, Z1)],
                [(xb, h, Z0), (xa, h, Z0), (xa, base, Z0), (xb, base, Z0)],
            ]
        for pts in boxes:
            ys = {p[1] for p in pts}
            if len(ys) == 1 or min(ys) < max(ys):
                quads.append((pts, uv(pts)))
        prev = h
    return quads


def tile_key(pixels, role):
    return hashlib.sha1(pixels.tobytes()).hexdigest()[:12] + role[0]


# -------------------------------------------------------------- voxelate --

def tile_relief(px, R, step=None, mask=None):
    """Relief 0..R per pixel from the drawing's own shading.

    Measured on step x step blocks: per pixel, dithering and outlines turn
    the relief into noise -- 160 quads a cell in Minish Woods -- where 2px
    blocks keep the grass tufts and stones and cost 40. The texture stays
    at full resolution either way. With a mask, only drawn pixels count:
    a cut-out tile's transparent pixels are not dark ones.
    """
    step = step or RELIEF_STEP
    if R <= 0:
        return np.zeros(px.shape[:2], np.int64)
    lum = px[:, :, :3].astype(float).mean(axis=2)
    n = 16 // step
    if mask is None:
        b = lum.reshape(n, step, n, step).mean(axis=(1, 3))
        seen = np.ones((n, n), bool)
    else:
        wsum = mask.reshape(n, step, n, step).sum(axis=(1, 3))
        b = (np.where(mask, lum, 0.0).reshape(n, step, n, step).sum(axis=(1, 3))
             / np.maximum(wsum, 1))
        seen = wsum > 0
        if not seen.any():
            return np.zeros(lum.shape, np.int64)
    lo, hi = np.percentile(b[seen], 10), np.percentile(b[seen], 90)
    if hi - lo < FLAT_SPREAD:
        return np.zeros(lum.shape, np.int64)
    t = np.rint(np.clip((b - lo) / (hi - lo), 0.0, 1.0) * R).astype(np.int64)
    return np.kron(t, np.ones((step, step), np.int64))


def tile_quads(px, R, mask=None):
    """The voxel model of one drawing: [(4 corners, 4 texels)], y up from 0.

    Columns of 1 + relief voxels, one per pixel. Colour comes from the
    drawing through texture coordinates -- (s, t) in the tile's own pixels
    -- so faces merge by SHAPE alone: a flat floor tile is one top quad
    whatever its dithering, where one colour per face made it ninety.
    Tops are greedy-merged by height. A side is emitted where a column
    stands above its neighbour, or above the floor at the tile's edge,
    merged along its row; its texels run along the pixels it borders, so
    each column's side shows that column's pixel.

    mask (16x16 bool) keeps only the pixels a layer actually draws: a
    top-layer tile is cut out along its transparency, so a fence or a roof
    edge has the drawn silhouette instead of a square plate.
    """
    h = 1 + tile_relief(px, R, mask=mask)
    if mask is not None:
        h = np.where(mask, h, 0)
    quads = []
    for y in sorted(set(int(v) for v in np.unique(h)) - {0}):
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

def side_face(cx, cy, dx, dz, top, bottom, ox, oz):
    """One vertical face of cell (cx, cy) from height top down to bottom.

    Texels are in room-art pixels. Every face takes the d rows drawn above
    the cell's foot, bottom row at the foot (drawn row = z - height), one
    quad, since that mapping is linear. For a south face that IS the drawn
    front of the thing: a door frame, a cliff face. The game draws no
    north, east or west faces, so those wear the same front band, running
    left to right as seen from outside -- bark on a trunk's flank, rock on
    a cliff's end. Stretching the cell's edge pixel instead, as a first
    version did, streaked every flank.
    """
    x0, z0 = cx * 16, cy * 16
    X0, X1, Z0, Z1 = ox + x0, ox + x0 + 16, oz + z0, oz + z0 + 16
    a, b, d = top, bottom, top - bottom
    foot = z0 + 16
    tt, tb = foot - d, foot
    # Windings are room_explore's, outward. The band runs left to right as
    # seen from outside: on the east and west faces the first corner is the
    # right-hand one, so their texels run the other way.
    ltr = [(x0, tt), (x0 + 16, tt), (x0 + 16, tb), (x0, tb)]
    rtl = [(x0 + 16, tt), (x0, tt), (x0, tb), (x0 + 16, tb)]
    if dz == 1:
        return [(X0, a, Z1), (X1, a, Z1), (X1, b, Z1), (X0, b, Z1)], ltr
    if dz == -1:
        return [(X1, a, Z0), (X0, a, Z0), (X0, b, Z0), (X1, b, Z0)], ltr
    if dx == 1:
        return [(X1, a, Z0), (X1, a, Z1), (X1, b, Z1), (X1, b, Z0)], rtl
    return [(X0, a, Z1), (X0, a, Z0), (X0, b, Z0), (X0, b, Z1)], rtl


def drop_faces(H, solid, ox, oz, lift, outside):
    """Vertical faces wherever a cell drops to a lower neighbour."""
    quads = []
    rows, cols = H.shape
    for cy in range(rows):
        for cx in range(cols):
            if not solid[cy, cx]:
                continue
            hh = int(H[cy, cx])
            for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                ny, nx = cy + dz, cx + dx
                nh = (int(H[ny, nx]) if 0 <= ny < rows and 0 <= nx < cols
                      and solid[ny, nx] else outside)
                if nh < hh:
                    quads.append(side_face(cx, cy, dx, dz, hh + lift, nh + lift,
                                           ox, oz))
    return quads


# ---------------------------------------------------------------- overlay --
# Layer 1 is the world's second level: canopies, bridge decks, roofs, the
# tops of fences drawn over Link. Only where it is drawn ABOVE layer 0
# (room_explore.overlay_overhead); indoors it is floor detail and is already
# in the composited floor tiles.
OVERLAY_LIFT = 24       # px above the cell's class height, as the terrain
OVERLAY_REACH = 24      # skirt reaches the ground when it is this close
OVERLAY_SKIRT = 12      # otherwise it hangs this far


def room_overlay(r, H, canopy=True):
    """(deck cells, crown field or None, top RGBA art, occupancy, c1).

    Deck cells are layer-1 cells that draw something and are not tree
    crown: [(cx, cy, height)]. Crowns keep the terrain's shape
    (canopy_field), which a flat tile cannot carry.
    """
    import extract_art
    if not RE.overlay_overhead(r):
        return [], None, None, None, None
    top = extract_art.room_art(r, 1)
    if top is None:
        return [], None, None, None, None
    top = np.asarray(top)
    L1 = r.layers[1]
    h, w = r.cells_h, r.cells_w
    c1 = RE.classify_room(r, 1)
    occ = (L1["tile"][:h, :w] != 0) | (L1["collision"][:h, :w] != 0)
    crown = np.zeros((h, w), bool)
    cf = None
    if canopy:
        cmask, _top = RE.canopy_mask(r)
        if cmask is not None and cmask.any():
            crown = RE.crown_cells(r, cmask, np.asarray(_top))
            cf = RE.canopy_field(r, step=2,
                                 lift=RE.CLASS_HEIGHT[RE.CLASS_GROUND] + OVERLAY_LIFT)
    decks = []
    for cy in range(h):
        for cx in range(w):
            if not occ[cy, cx] or crown[cy, cx]:
                continue
            a = top[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            if a.shape[:2] != (16, 16) or not (a[:, :, 3] > 0).any():
                continue
            decks.append((cx, cy, RE.CLASS_HEIGHT.get(c1[cy, cx], 0) + OVERLAY_LIFT))
    return decks, cf, top, occ, c1


def deck_skirts(decks, occ, H, ox, oz, lift):
    """Aprons under the edges of lifted decks, down to the ground where it
    comes close (a roof on its wall, a bridge on its bank), otherwise
    hanging OVERLAY_SKIRT -- room_explore.overlay_skirt_bottom decides."""
    quads = []
    rows, cols = H.shape
    Hi = np.asarray(H, dtype=np.int64)
    for cx, cy, hh in decks:
        for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny, nx = cy + dz, cx + dx
            if 0 <= ny < rows and 0 <= nx < cols and occ[ny, nx]:
                continue
            bottom, _sup = RE.overlay_skirt_bottom(
                hh, Hi, occ, cy, cx, dz, dx, rows, cols,
                OVERLAY_REACH, OVERLAY_SKIRT)
            if bottom < hh:
                quads.append(side_face(cx, cy, dx, dz, hh + lift, bottom + lift,
                                       ox, oz))
    return quads


def crown_quads(cf, ox, oz, lift):
    """The terrain's crown surface, textured by projecting the room art from
    the game's camera: a crown point at height y over (x, z) takes the pixel
    drawn at (x, z - y), where its leaves are drawn."""
    out = []
    base = RE.CLASS_HEIGHT[RE.CLASS_GROUND] + OVERLAY_LIFT

    def uvf(pts):
        return [(x - ox, (z - oz) - (y - lift)) for x, y, z in pts]

    def quad(pts, c, uv=None):
        out.append((pts, uv))
    RE.emit_canopy(quad, cf, ox, oz, lift + base, step=2, uvf=uvf, merge=True)
    return out


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
    dump_path = {}
    for p in paths:
        try:
            r = RE.load_room(Path(p))
        except Exception:
            continue
        if r.cells_w and r.cells_h and r.layers[a.layer]["present"]:
            rooms.append(r)
            dump_path[(r.area, r.room)] = p
    if not rooms:
        return area, 0, 0, 0, 0
    out = Path(a.out) / f"area_{area:02d}"
    out.mkdir(parents=True, exist_ok=True)
    levels = RE.assign_levels(rooms)

    # identify: every cell's drawing and role, keyed; heights as the terrain
    lib = {}                      # key -> (index, pixels, role)
    placed = []                   # (r, lift, art, H, solid, cells, overlay, flights)
    for li, lv in enumerate(levels):
        lift = li * a.floor_height
        for r in lv:
            art = RE.room_art_rgb(r, a.layer)
            if art is None:
                continue
            art = np.ascontiguousarray(art[:, :, :3].astype(np.uint8))
            cls, H, bl, flights = room_heights(
                r, a.layer, blocks=not a.no_blocks, relief=a.relief,
                path=None if a.no_stairs else dump_path.get((r.area, r.room)))
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
            ov = None
            if not a.no_overlay and len(r.layers) > 1 and r.layers[1]["present"]:
                decks, cf, top, occ, _c1 = room_overlay(r, H, canopy=not a.no_canopy)
                for cx, cy, hh in decks:
                    pix = np.ascontiguousarray(
                        top[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16].astype(np.uint8))
                    pix[pix[:, :, 3] == 0] = 0
                    k = tile_key(pix, "deck")
                    if k not in lib:
                        lib[k] = (len(lib), pix, "deck")
                    cells.append((k, cx, cy, hh + lift, "deck"))
                ov = (decks, cf, occ)
            placed.append((r, lift, art, H, cls != RE.CLASS_VOID, cells, ov, flights))

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
        atlas[ty * 16:ty * 16 + 16, tx * 16:tx * 16 + 16] = pix[:, :, :3]
        models[k] = tile_quads(pix, RELIEF[ro],
                               pix[:, :, 3] > 0 if pix.shape[2] == 4 else None)
        lib_obj.obj(f"t_{k}")
        lib_obj.quads(models[k], toff=(tx * 16, ty * 16))
    lib_obj.close()
    Image.fromarray(atlas).save(out / "tiles.png")

    # place
    nfaces = ncell = 0
    with open(out / "placements.txt", "w") as place:
        place.write("# key room cx cy x y z role -- tile model origin, world pixels\n")
        for r, lift, art, H, solid, cells, ov, flights in placed:
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
            for fl in flights:
                m.quads(stair_quads(fl, ox, oz, lift))
            if ov is not None:
                decks, cf, occ = ov
                m.quads(deck_skirts(decks, occ, H, ox, oz, lift))
                if cf is not None:
                    m.quads(crown_quads(cf, ox, oz, lift))
            nfaces += m.faces
            m.close()
    with open(out / "stairs.txt", "w") as st:
        st.write("# room cx0,cy0,cx1,cy1 rise steps up base top -- flights of "
                 "steps; up/base/top are '-' where the landings are one level\n")
        for r, lift, _art, _H, _solid, _cells, _ov, flights in placed:
            name = f"room_{r.area:02d}_{r.room:02d}"
            for fl in flights:
                raised = fl.get("up") is not None
                st.write(f"{name} {','.join(str(v) for v in fl['rect'])} "
                         f"{fl['rise']} {fl['steps']} "
                         f"{fl['up'] if raised else '-'} "
                         f"{fl['base'] + lift if raised else '-'} "
                         f"{fl['top'] + lift if raised else '-'}\n")
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
    ap.add_argument("--no-overlay", action="store_true",
                    help="leave out the top layer (canopies, decks, roofs)")
    ap.add_argument("--no-canopy", action="store_true",
                    help="tree crowns as flat deck tiles instead of shaped crowns")
    ap.add_argument("--no-stairs", action="store_true",
                    help="leave flights of steps flat (no raised landings, "
                         "no steps)")
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
