#!/usr/bin/env python3
"""tileid.py -- say what every cell of a room is.

The tile stage (tilevox.py) knew roles: floor, wall, block, water, pit. A
bush, a stump, a hedge, a signpost and a cliff were all "wall", a 16px
box with its drawing on top. This names them, from the game's own data
where it says, and from the drawing where it does not, so each can be
given its own shape.

Evidence, strongest first:

  tile type   the few include/tiles.h names: CHEST, TORCH, ROCK,
              PERMA_ROCK, SIGNPOST, the dungeon blocks, mushrooms
  collision   0x1D is the game's "cut or lift this": its surface action
  + action    says which -- 0x14 a bush, 0x15 a liftable rock, 0x38 a
              sapling (a small tree, its trunk drawn)
  class       the terrain's (room_explore): water, pit, tall grass
  drawing     for the rest -- the generic prop tile type 0x70, hedges,
              floors -- the drawing's colours and shape

Families, and what the tile stage builds for each:

  bush, sapling, rock, mushroom    a dome on the drawn outline
  stump                            a flat-topped drum on the outline
  planter, prop                    a box on the outline
  signpost                         a board: the outline, stood up
  foliage                          a leafy mass, its top rounding off
                                   toward the drawn edge: a blocked
                                   cell drawn vivid leaf green -- hedges,
                                   the field's round trees (the
                                   woods' forest mass is teal, muted)
  flowers                          floor with its petals standing up
  (anything else keeps its terrain role)

The outline is the drawing less the floor it stands on: the colours of
the floor cell beside it.

Usage:
  python3 tools/tileid.py states/p0 --rooms 00_00,03_01,07_00
  python3 tools/tileid.py states/p0 --area 3 --sheet tileid.png

Output is counts and, with --sheet, a contact sheet of every family's
drawings (derived from your ROM; keep it local).
"""
import argparse
import collections
import hashlib
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402

# --- the game's data -------------------------------------------------------
BY_TYPE = {
    0x55: "rock", 0x1d3: "rock", 0x1d4: "rock", 0x1d5: "rock", 0x1d6: "rock",
    0x176: "signpost",
    0x165: "mushroom", 0x166: "mushroom",
}
LIFT_COLL = 0x1D                    # cut or lift
BY_LIFT_ACT = {0x14: "bush", 0x15: "rock", 0x38: "sapling"}
PROP_TYPE = 0x70                    # the generic prop tile type

# --- the drawing -----------------------------------------------------------
PROP_CLUSTER = 4        # cells: a blocked cluster this small is a prop
HEDGE_CLUSTER = 1       # cells: foliage may stand alone (a field tree)
GREEN_SHARE = 0.55      # share of green pixels (hue 60-170, sat 0.3)
HEDGE_HUE = (70, 150)   # a hedge is leaf green; the woods are teal (160+)
HEDGE_SAT = 0.45        # mean saturation: clipped hedges are vivid
HEDGE_BRIGHT = 0.5      # mean brightness: hedges are clipped and lit; the
                        # forest mass under the woods' canopy is not
PETALS = 10             # saturated non-green pixels on grass: flowers

# what each family is built as, for tilevox
SHAPES = {
    "bush": ("dome", 12), "sapling": ("dome", 16), "rock": ("dome", 10),
    "mushroom": ("dome", 12), "stump": ("drum", 8), "planter": ("box", 10),
    "prop": ("box", 10), "signpost": ("board", 14),
}
PROP_FAMILIES = tuple(SHAPES)


def _hsv(px):
    rgb = px[..., :3].astype(float) / 255.0
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    r_, g_, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.where(mx == r_, ((g_ - b_) / d) % 6,
                   np.where(mx == g_, (b_ - r_) / d + 2, (r_ - g_) / d + 4)) * 60.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    return hue, sat, mx


def green_share(px, lo=60, hi=170):
    hue, sat, _v = _hsv(px)
    return float(((hue >= lo) & (hue < hi) & (sat >= 0.3)).mean())


def petal_mask(px):
    """Petal pixels: saturated, bright, neither leaf green nor the teal to
    blue of water sparkle, nor the magenta the art uses for 'not drawn'."""
    hue, sat, v = _hsv(px)
    magenta = (px[..., 0] >= 240) & (px[..., 1] <= 16) & (px[..., 2] >= 240)
    return (((hue < 60) | (hue >= 260)) & (sat >= 0.5) & (v >= 0.7) & ~magenta)


def petal_count(px):
    return int(petal_mask(px).sum())


def petal_blobs(px):
    return RE._label(petal_mask(px))[1]


def drawing_kind(px):
    """What a generic prop (tile type 0x70) is, from its drawing -- the
    outline only, not the floor or water ring it stands in."""
    m = outline(px)
    hue, sat, v = (a[m] for a in _hsv(px))
    green = (hue >= 60) & (hue < 170) & (sat >= 0.3)
    red = ((hue < 12) | (hue >= 345)) & (sat >= 0.6) & (v >= 0.5)
    white = (sat < 0.2) & (v > 0.85)
    brown = (hue >= 12) & (hue < 50) & (sat >= 0.3) & (v < 0.9)
    grey = sat < 0.2
    colour = (sat >= 0.45) & (v >= 0.5) & ~((hue >= 180) & (hue < 260))   # not water
    if red.mean() > 0.15 and white.mean() > 0.05:
        return "mushroom"               # red cap, white spots
    if green.mean() > GREEN_SHARE:
        return "bush"
    if grey.mean() > 0.25 and colour.mean() > 0.08 and brown.mean() < 0.25:
        return "planter"                # a grey box with plants in it
    if brown.mean() > 0.25:
        return "stump"
    if grey.mean() > 0.3:
        # a square grey outline is a box (the plants of a planter are drawn
        # in the cell above); a round one is a stone
        ys, xs = np.nonzero(m)
        square = m.sum() / float((np.ptp(ys) + 1) * (np.ptp(xs) + 1))
        return "planter" if square >= 0.85 else "rock"
    return "prop"


def families(r, cls, H, art, layer=0):
    """Family per cell (None where the terrain role stands), and the floor
    height each prop stands on."""
    import extract_art
    top = extract_art.room_art(r, 1) if len(r.layers) > 1 and r.layers[1]["present"] else None
    covered = np.zeros((r.cells_h, r.cells_w), bool)
    if top is not None and RE.overlay_overhead(r):
        a1 = np.asarray(top)[:, :, 3] > 0
        for cy in range(r.cells_h):
            for cx in range(r.cells_w):
                covered[cy, cx] = a1[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16].mean() > 0.5
    L = r.layers[layer]
    h, w = r.cells_h, r.cells_w
    t = L["tile"][:h, :w]
    tt = L["tiletype"][np.clip(t, 0, len(L["tiletype"]) - 1)]
    coll = L["collision"][:h, :w]
    act = L["act"][:h, :w]
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    walk = cls == RE.CLASS_GROUND
    lab, _n = RE._label(blocked)
    sizes = np.bincount(lab.ravel())
    fam = np.empty((h, w), dtype=object)
    floor = np.zeros((h, w), np.int64)
    for cy in range(h):
        for cx in range(w):
            px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            if px.shape[:2] != (16, 16):
                continue
            f = None
            v = int(tt[cy, cx])
            if blocked[cy, cx]:
                if v in BY_TYPE:
                    f = BY_TYPE[v]
                elif coll[cy, cx] == LIFT_COLL and int(act[cy, cx]) in BY_LIFT_ACT:
                    f = BY_LIFT_ACT[int(act[cy, cx])]
                elif v == PROP_TYPE and sizes[lab[cy, cx]] <= PROP_CLUSTER:
                    f = drawing_kind(px)
                elif (sizes[lab[cy, cx]] >= HEDGE_CLUSTER
                      and green_share(px, *HEDGE_HUE) > GREEN_SHARE and H[cy, cx] <= 16
                      and _hsv(px)[2].mean() >= HEDGE_BRIGHT
                      and _hsv(px)[1].mean() >= HEDGE_SAT):
                    f = "foliage"
            elif (walk[cy, cx] and petal_count(px) >= PETALS and petal_blobs(px) >= 2
                  and green_share(px) > 0.35):
                f = "flowers"
            fam[cy, cx] = f
            if f in PROP_FAMILIES:
                nb = [int(H[y, x]) for y, x in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1))
                      if 0 <= y < h and 0 <= x < w and walk[y, x]]
                floor[cy, cx] = min(nb) if nb else 0
    return fam, floor


def outline(px, floor_px=None):
    """The drawn thing, less the floor it stands on: pixels whose colour
    the floor beside it does not use, largest blob, holes filled."""
    if floor_px is None:
        edge = np.concatenate([px[0], px[-1], px[:, 0], px[:, -1]])
        vals, counts = np.unique(edge.reshape(-1, 3), axis=0, return_counts=True)
        bg = vals[counts >= max(2, counts.max() // 4)]
    else:
        bg = np.unique(floor_px.reshape(-1, 3), axis=0)
    m = np.ones(px.shape[:2], bool)
    for c in bg:
        m &= ~(px[..., :3] == c).all(axis=-1)
    lab, n = RE._label(m)
    if n == 0:
        return np.ones(px.shape[:2], bool)
    best = max(range(1, n + 1), key=lambda k: (lab == k).sum())
    m = lab == best
    # fill holes: background not reachable from the border
    out = ~m
    lab2, n2 = RE._label(out)
    border = set(lab2[0].tolist()) | set(lab2[-1].tolist()) | \
        set(lab2[:, 0].tolist()) | set(lab2[:, -1].tolist())
    for k in range(1, n2 + 1):
        if k not in border:
            m |= lab2 == k
    if m.sum() < 12:
        return np.ones(px.shape[:2], bool)
    return m


def shape_heights(px, family, floor_px=None):
    """16x16 column heights (0 = nothing) for a prop family's model."""
    kind, hmax = SHAPES[family]
    m = outline(px, floor_px)
    if kind in ("box", "board"):
        return np.where(m, hmax, 0).astype(np.int64)
    # distance from the outline's edge, for domes and drums
    dist = np.where(m, 99, 0).astype(np.int64)
    P = np.pad(m, 1)
    edge = m & ~(P[:-2, 1:-1] & P[2:, 1:-1] & P[1:-1, :-2] & P[1:-1, 2:])
    dist[edge] = 1
    for _ in range(8):
        P = np.pad(dist, 1, constant_values=0)
        nb = np.minimum.reduce([P[:-2, 1:-1], P[2:, 1:-1], P[1:-1, :-2], P[1:-1, 2:]])
        dist = np.where(m, np.minimum(dist, nb + 1), 0)
    if kind == "drum":
        return np.where(m, np.where(dist >= 2, hmax, hmax - 2), 0).astype(np.int64)
    R = max(1.0, float(dist.max()))
    t = np.clip(dist / R, 0, 1)
    dome = 1 + np.rint((hmax - 1) * np.sqrt(1.0 - (1.0 - t) ** 2))
    if family in ("bush", "sapling"):
        # leaf clumps: lit pixels up a voxel, shadow down
        lum = px[..., :3].astype(float).mean(axis=-1)
        med = np.median(lum[m]) if m.any() else 0
        dome = dome + np.where(lum > med + 20, 1, np.where(lum < med - 30, -1, 0))
    return np.where(m, np.maximum(dome, 1), 0).astype(np.int64)


FOLIAGE_ROUND = 8       # px: how far a leafy top falls off at its edge
FOLIAGE_R = 8           # px: how far in from the edge it reaches full height
PETAL_H = 2             # voxels: a flower's petals above the grass


def foliage_hmaps(art, fam):
    """{(cy, cx): 16x16 heights} for foliage cells, from the whole room.

    The leaf-green pixels of every foliage cell form one mask; a pixel's
    distance from its edge (up to FOLIAGE_R) lifts it along a quarter
    circle from the edge, FOLIAGE_ROUND down, to full height -- a long
    hedge gets rounded shoulders, a field tree a dome. Leaf shading adds
    a voxel on lit clumps. Heights are above (cell height - FOLIAGE_ROUND);
    pixels that are not leaf (outline, trunk) sit at the edge height.
    """
    h, w = fam.shape
    Hpx, Wpx = h * 16, w * 16
    A = art[:Hpx, :Wpx]
    hue, sat, v = _hsv(A)
    leaf = (hue >= 60) & (hue < 170) & (sat >= 0.3)
    cellmask = np.kron(fam == "foliage", np.ones((16, 16), bool))[:A.shape[0], :A.shape[1]]
    m = leaf & cellmask
    dist = np.where(m, FOLIAGE_R, 0).astype(np.int64)
    P = np.pad(m, 1)
    edge = m & ~(P[:-2, 1:-1] & P[2:, 1:-1] & P[1:-1, :-2] & P[1:-1, 2:])
    dist[edge] = 1
    for _ in range(FOLIAGE_R):
        Q = np.pad(dist, 1)
        nb = np.minimum.reduce([Q[:-2, 1:-1], Q[2:, 1:-1], Q[1:-1, :-2], Q[1:-1, 2:]])
        dist = np.where(m, np.minimum(dist, nb + 1), 0)
    t = np.clip(dist / float(FOLIAGE_R), 0, 1)
    prof = np.rint(FOLIAGE_ROUND * np.sqrt(1.0 - (1.0 - t) ** 2))
    lum = A[..., :3].astype(float).mean(axis=-1)
    bump = np.where(lum > np.median(lum[m]) + 20, 1, 0) if m.any() else 0
    hm = np.where(m, 1 + prof + bump, 1).astype(np.int64)
    out = {}
    for cy, cx in zip(*np.nonzero(fam == "foliage")):
        out[(int(cy), int(cx))] = hm[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
    return out


def flower_hmap(px):
    """Grass with its petals standing PETAL_H above it."""
    return (1 + PETAL_H * petal_mask(px)).astype(np.int64)


# --------------------------------------------------------------------- CLI --
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--rooms", default="", help="AA_RR,... (default: all, or --area)")
    ap.add_argument("--area", default="", help="comma-separated areas")
    ap.add_argument("--sheet", default="", help="write a contact sheet here")
    ap.add_argument("--per", type=int, default=16, help="drawings per family on the sheet")
    a = ap.parse_args()
    import tilevox as TV
    files = sorted(Path(a.dumps).glob("room_*.tmcr"))
    if a.rooms:
        want = {"room_%s" % x for x in a.rooms.split(",")}
        files = [f for f in files if f.stem in want]
    elif a.area:
        areas = {int(x) for x in a.area.split(",")}
        files = [f for f in files if int(f.stem.split("_")[1]) in areas]
    count = collections.Counter()
    per_room = collections.defaultdict(collections.Counter)
    samples = collections.defaultdict(dict)
    for f in files:
        try:
            r = RE.load_room(f)
        except Exception:
            continue
        if not r.cells_w or not r.layers[0]["present"]:
            continue
        art = RE.room_art_rgb(r, 0)
        if art is None:
            continue
        art = np.ascontiguousarray(art[:, :, :3].astype(np.uint8))
        cls, H, bl, _fl, _doors = TV.room_heights(r, 0, path=str(f))
        fam, _floor = families(r, cls, H, art)
        role = TV.room_roles(r, cls, H, bl, art)
        for cy in range(r.cells_h):
            for cx in range(r.cells_w):
                k = fam[cy, cx] or role[cy, cx]
                if k is None:
                    continue
                count[k] += 1
                per_room[f.stem][k] += 1
                px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                if px.shape[:2] == (16, 16) and len(samples[k]) < a.per:
                    samples[k].setdefault(hashlib.sha1(px.tobytes()).hexdigest(), px)
    total = sum(count.values())
    print(f"{len(files)} rooms, {total} cells")
    for k, n in count.most_common():
        print(f"  {k:<10} {n:7d}  {100.0 * n / max(1, total):5.1f}%")
    if a.sheet:
        from PIL import Image, ImageDraw
        fams = [k for k, _ in count.most_common()]
        W = a.per * 36 + 90
        img = Image.new("RGB", (W, len(fams) * 36 + 4), (30, 30, 30))
        d = ImageDraw.Draw(img)
        for i, k in enumerate(fams):
            d.text((4, i * 36 + 12), k, fill=(255, 220, 120))
            for j, px in enumerate(list(samples[k].values())[:a.per]):
                img.paste(Image.fromarray(px).resize((32, 32), Image.NEAREST),
                          (90 + j * 36, i * 36 + 2))
        img.save(a.sheet)
        print(f"sheet -> {a.sheet}")


if __name__ == "__main__":
    main()
