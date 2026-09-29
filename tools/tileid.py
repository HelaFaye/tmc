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
  signpost                         an upright: the drawing stood on its
                                   foot, the drawing on its front
  fence                            an upright of its wood: a rail of logs
                                   (generic prop 0x70, widest at its foot,
                                   running on into the next cell) or a
                                   post (a narrow wood blob with pale end
                                   grain) in a cell only partly blocked --
                                   the stakes around Link's yard
  foliage                          a leafy mass, its top rounding off
                                   toward the drawn edge: a blocked
                                   cell drawn vivid leaf green -- hedges,
                                   the field's round trees (the
                                   woods' forest mass is teal, muted)
  flowers                          floor with its petals standing up:
                                   bright specks, white or not leaf-
                                   coloured, that are not one of the
                                   room's floor colours (South Hyrule
                                   Field's dry grass is 18% of its floor
                                   and saturated yellow), on a fully
                                   walkable cell
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
import picori_labels as PL  # noqa: E402

# Picori's tile type names (CUT_BUSH, ROCK, PERMA_ROCK, SIGNPOST, ...) and
# special tiles (Pots, Boulder), read from include/tiles.h, then ours
BY_TYPE = dict(PL.seed_by_type())
BY_TYPE.update({0x165: "mushroom", 0x166: "mushroom"})
BY_SPECIAL = dict(PL.seed_by_special())
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
PETALS = 6              # petal pixels on grass: flowers
PETAL_BLOB = 12         # px: a petal cluster is small; a dirt patch is not
GROUND_SHARE = 0.03     # a colour this common on the room's floor is floor

FENCE_WOOD = 0.5        # share of a fence drawing that is wood or outline
POST_W = (3, 9)         # px: a post's drawn width
POST_MIN = 14           # px: smallest post drawing
FENCE_ALL_WOOD = 0.6    # share of the whole drawing (off the floor) in wood
FENCE_COLOUR = 0.1      # bright colour in it at most: a flower box is not

# what each family is built as, for tilevox
SHAPES = {
    "bush": ("dome", 12), "sapling": ("dome", 16), "rock": ("dome", 10),
    "mushroom": ("dome", 12), "stump": ("drum", 8), "planter": ("box", 10),
    "pot": ("dome", 12),
    "prop": ("box", 10),
}
PROP_FAMILIES = tuple(SHAPES)
# drawn standing, with next to no depth: built as uprights (tilevox), the
# drawing on the front, depth north of the foot
UPRIGHT_FAMILIES = {"fence": 4, "signpost": 4}


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


def ground_palette(art, cls):
    """The room's floor colours, packed 0xRRGGBB: every colour covering at
    least GROUND_SHARE of its walkable pixels. South Hyrule Field's dry
    grass (224,200,40) is 18% of its floor -- yellow, saturated, bright,
    and not a flower."""
    h, w = cls.shape
    A = np.asarray(art)[:h * 16, :w * 16, :3].astype(np.int64)
    walk = np.kron(cls == RE.CLASS_GROUND, np.ones((16, 16), bool))[:A.shape[0], :A.shape[1]]
    if not walk.any():
        return np.zeros(0, np.int64)
    key = _pack(A[walk])
    vals, counts = np.unique(key, return_counts=True)
    return vals[counts >= GROUND_SHARE * counts.sum()]


def _pack(px):
    px = np.asarray(px).astype(np.int64)
    return (px[..., 0] << 16) | (px[..., 1] << 8) | px[..., 2]


def petal_mask(px, ground=None):
    """Petal pixels: bright, white or of a colour that is not leaf green
    nor the teal to blue of water sparkle nor the magenta the art uses for
    'not drawn', not one of the room's floor colours (ground_palette), in
    clusters of PETAL_BLOB pixels at most."""
    hue, sat, v = _hsv(px)
    magenta = (px[..., 0] >= 240) & (px[..., 1] <= 16) & (px[..., 2] >= 240)
    white = (sat < 0.35) & (v >= 0.8)
    coloured = ((hue < 60) | (hue >= 260)) & (sat >= 0.5) & (v >= 0.7)
    m = (white | coloured) & ~magenta
    if ground is not None and len(ground):
        m &= ~np.isin(_pack(px[..., :3]), ground)
    lab, n = RE._label(m)
    if n:
        sizes = np.bincount(lab.ravel())
        m &= sizes[lab] <= PETAL_BLOB
    return m


def petal_count(px, ground=None):
    return int(petal_mask(px, ground).sum())


def petal_blobs(px, ground=None):
    return RE._label(petal_mask(px, ground))[1]


def wood_mask(px):
    """Wood and its outline: brown (hue under 50 or red-purple, saturated,
    not bright), the pale end grain of a cut post, and the dark outline."""
    hue, sat, v = _hsv(px)
    brown = ((hue < 50) | (hue >= 300)) & (sat >= 0.2) & (v >= 0.2) & (v < 0.9)
    grain = (hue >= 30) & (hue < 65) & (sat >= 0.2) & (sat < 0.7) & (v >= 0.8)
    dark = v < 0.3
    return brown | grain | dark


def fence_blobs(px, ground=None):
    """The fence in a drawing: its blobs off the room's floor colours
    (ground_palette), POST_MIN pixels or more, that are mostly wood."""
    m = drawn_mask(px) if ground is None or not len(ground) \
        else ~np.isin(_pack(px[..., :3]), ground)
    wood = wood_mask(px)
    lab, n = RE._label(m & wood)
    out = []
    for k in range(1, n + 1):
        b = lab == k
        if b.sum() >= POST_MIN and wood[b].mean() >= FENCE_WOOD:
            out.append(b)
    return out


def all_wood(px, ground):
    """The drawing, off the room's floor colours, is wood: a fence, not a
    flower box or a stall with wood in it."""
    m = ~np.isin(_pack(px[..., :3]), ground) if ground is not None and len(ground) \
        else drawn_mask(px)
    if m.sum() < POST_MIN:
        return False
    wood = wood_mask(px)
    _h, sat, v = _hsv(px)
    colour = (sat >= 0.5) & (v >= 0.7) & ~wood
    return wood[m].mean() >= FENCE_ALL_WOOD and colour[m].mean() < FENCE_COLOUR


def is_rail(b):
    """A rail: a blob running edge to edge (on into the next cell), widest
    at its foot -- posts stand up out of it. A stump is widest at its cut
    top; a roof fills its cell."""
    xs = np.nonzero(b.any(axis=0))[0]
    if not (xs.min() <= 1 and xs.max() >= 14) or b.mean() > 0.95:
        return False
    rows = b.sum(axis=1)
    ys = np.nonzero(rows)[0]
    mid = (ys.min() + ys.max() + 1) // 2
    return rows[mid:ys.max() + 1].mean() >= rows[ys.min():mid].mean() + 0.5


def has_post(px, ground=None):
    """A post: a wood blob POST_W wide, taller than wide, with pale end
    grain in its upper half."""
    _h, sat, v = _hsv(px)
    hue = _h
    grain = (hue >= 30) & (hue < 65) & (sat >= 0.2) & (sat < 0.7) & (v >= 0.8)
    for b in fence_blobs(px, ground):
        ys, xs = np.nonzero(b)
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if not (POST_W[0] <= bw <= POST_W[1]) or bh < bw:
            continue
        upper = b & (np.arange(16)[:, None] <= ys.min() + bh // 2)
        if (grain & upper).any():
            return True
    return False


def drawn_mask(px, floor_px=None):
    """Every pixel that is not the floor's (outline() keeps only the
    largest blob; a fence cell draws two posts)."""
    if floor_px is None:
        edge = np.concatenate([px[0], px[-1], px[:, 0], px[:, -1]])
        vals, counts = np.unique(edge.reshape(-1, 3), axis=0, return_counts=True)
        bg = vals[counts >= max(2, counts.max() // 4)]
    else:
        bg = np.unique(floor_px.reshape(-1, 3), axis=0)
    m = np.ones(px.shape[:2], bool)
    for c in bg:
        m &= ~(px[..., :3] == c).all(axis=-1)
    return m


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


# --- what a person says ----------------------------------------------------
# The rules above are guesses from the drawing; the overrides file is where
# a wrong guess is corrected, cell by cell or tile type by tile type, and
# where a height the drawing cannot tell is written down. It holds only
# room numbers, cell coordinates and tile type numbers -- nothing from the
# ROM -- so it is committed. tile_review.py draws a room with every cell
# labelled, to read the coordinates off.
#
#   # where                what
#   03_01 38,24            family=stump          one cell
#   34_17 6,2-12,2         height=0              a rectangle, inclusive
#   21_00 type=0x3e0       family=fence          every cell of a tile type
#   a3    type=0x70        family=-              a whole area; - = terrain
#   *     type=0x176       family=signpost       the whole game
#   *     special=0x4000   family=pot            an object tile (SpecialTile)
#
# family: any family name, or - for none (the terrain role stands).
# height: the cell's terrain height in px, set after every other stage.
# label:  a name for people (no spaces); it shapes nothing.
OVERRIDES = HERE.parent / "vr" / "tiles" / "overrides.txt"
_overrides_cache = {}


def load_overrides(path=None):
    """The override rules, parsed once per path. TMC_TILE_OVERRIDES names
    another file; an empty value turns them off."""
    import os
    if path is None:
        path = os.environ.get("TMC_TILE_OVERRIDES", str(OVERRIDES))
    if not path:
        return []
    if path in _overrides_cache:
        return _overrides_cache[path]
    rules = []
    p = Path(path)
    if p.is_file():
        for n, line in enumerate(p.read_text().splitlines(), 1):
            f = line.split("#", 1)[0].split()
            if len(f) < 3:
                continue
            try:
                rule = _parse_rule(f)
            except ValueError as e:
                print(f"{p}:{n}: {e}", file=sys.stderr)
                continue
            rule["line"] = n
            rules.append(rule)
    _overrides_cache[path] = rules
    return rules


def _parse_rule(f):
    who, where, what = f[0], f[1], f[2:]
    rule = {}
    if who == "*":
        pass
    elif who.startswith("a"):
        rule["area"] = int(who[1:])
    else:
        a, rm = who.split("_")
        rule["area"], rule["room"] = int(a), int(rm)
    if where.startswith("type="):
        rule["type"] = int(where[5:], 0)
    elif where.startswith("special="):
        rule["special"] = int(where[8:], 0)
    else:
        if "room" not in rule:
            raise ValueError("cell coordinates need a room (AA_RR)")
        ends = [tuple(int(v) for v in e.split(",")) for e in where.split("-")]
        (x0, y0), (x1, y1) = ends[0], ends[-1]
        rule["cells"] = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    for kv in what:
        k, _, v = kv.partition("=")
        if k == "family":
            if v != "-" and v not in PROP_FAMILIES and v not in UPRIGHT_FAMILIES \
                    and v not in ("foliage", "flowers"):
                raise ValueError(f"unknown family {v!r}")
            rule["family"] = v
        elif k == "height":
            rule["height"] = int(v)
        elif k == "label":
            rule["label"] = v              # a name, for people; shapes nothing
        else:
            raise ValueError(f"unknown key {k!r}")
    return rule


def override_mask(r, rule, tt=None):
    """The cells of room r a rule covers."""
    h, w = r.cells_h, r.cells_w
    m = np.zeros((h, w), bool)
    if rule.get("area", r.area) != r.area or rule.get("room", r.room) != r.room:
        return m
    t = r.layers[0]["tile"][:h, :w]
    if "special" in rule:
        return np.asarray(t == rule["special"])
    if "type" in rule:
        if tt is None:
            L = r.layers[0]
            tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)]
        return np.asarray(tt[:h, :w] == rule["type"]) & (t < 0x4000)
    x0, y0, x1, y1 = rule["cells"]
    m[max(0, y0):y1 + 1, max(0, x0):x1 + 1] = True
    return m


def apply_height_overrides(r, H):
    """H with the height rules of the overrides file applied."""
    H = np.array(H, dtype=np.int64)
    for rule in load_overrides():
        if "height" in rule:
            H[override_mask(r, rule)] = rule["height"]
    return H


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
    ground = ground_palette(art, cls)
    partial = (coll >= 1) & (coll <= 14)          # some quadrants blocked
    special = t >= 0x4000                         # object tiles (SpecialTile)
    for cy in range(h):
        for cx in range(w):
            px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            if px.shape[:2] != (16, 16):
                continue
            f = None
            v = int(tt[cy, cx])
            if special[cy, cx]:
                # an object tile: its index is not a tile type
                if blocked[cy, cx] and int(t[cy, cx]) in BY_SPECIAL:
                    f = BY_SPECIAL[int(t[cy, cx])]
            elif blocked[cy, cx]:
                if v in BY_TYPE:
                    f = BY_TYPE[v]
                elif coll[cy, cx] == LIFT_COLL and int(act[cy, cx]) in BY_LIFT_ACT:
                    f = BY_LIFT_ACT[int(act[cy, cx])]
                elif v == PROP_TYPE and sizes[lab[cy, cx]] <= PROP_CLUSTER:
                    f = drawing_kind(px)
                elif v == PROP_TYPE and _open_ns(walk, cy, cx) and all_wood(px, ground) and (
                        any(is_rail(b) for b in fence_blobs(px, ground))
                        or has_post(px, ground)):
                    f = "fence"                 # a rail of logs, posts
                elif (sizes[lab[cy, cx]] >= HEDGE_CLUSTER
                      and green_share(px, *HEDGE_HUE) > GREEN_SHARE and H[cy, cx] <= 16
                      and _hsv(px)[2].mean() >= HEDGE_BRIGHT
                      and _hsv(px)[1].mean() >= HEDGE_SAT):
                    f = "foliage"
            elif walk[cy, cx] and partial[cy, cx]:
                if has_post(px, ground) and all_wood(px, ground):
                    f = "fence"                 # stakes, half the cell blocked
            elif (walk[cy, cx] and coll[cy, cx] == 0 and petal_blobs(px, ground) >= 2
                  and petal_count(px, ground) >= PETALS and green_share(px) > 0.35):
                f = "flowers"
            fam[cy, cx] = f
    # a fence runs on: the rail cells beside a fence drawn mostly in wood
    # (an end post, a gap between posts) are fence too
    grew = True
    while grew:
        grew = False
        for cy, cx in zip(*np.nonzero(blocked & (tt == PROP_TYPE) & ~special & (fam == None))):  # noqa: E711
            if not any(0 <= x < w and fam[cy, x] == "fence" for x in (cx - 1, cx + 1)):
                continue
            px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            if _open_ns(walk, cy, cx) and all_wood(px, ground):
                fam[cy, cx] = "fence"
                grew = True
    # a fence is a run: a lone post-like cell is a table leg, a stand
    lone = [(cy, cx) for cy, cx in zip(*np.nonzero(fam == "fence"))
            if not any(fam[y, x] == "fence"
                       for y in range(max(0, cy - 1), min(h, cy + 2))
                       for x in range(max(0, cx - 1), min(w, cx + 2)) if (y, x) != (cy, cx))]
    for cy, cx in lone:
        fam[cy, cx] = None
    # what a person has said a cell is wins (vr/tiles/overrides.txt)
    for rule in load_overrides():
        if "family" in rule:
            m = override_mask(r, rule, tt)
            fam[m] = None if rule["family"] == "-" else rule["family"]
    for cy in range(h):
        for cx in range(w):
            f = fam[cy, cx]
            if f in PROP_FAMILIES or f in UPRIGHT_FAMILIES:
                nb = [int(H[y, x]) for y, x in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1))
                      if 0 <= y < h and 0 <= x < w and walk[y, x]]
                floor[cy, cx] = min(nb) if nb else 0
    return fam, floor


def _open_ns(walk, cy, cx):
    """Open ground north or south of the cell: a fence stands in the open,
    a roof or a wall's face does not."""
    return any(0 <= y < walk.shape[0] and walk[y, cx] for y in (cy - 1, cy + 1))


def _floor_beside(art, walk, cy, cx):
    """The drawing of a walkable cell next to (cy, cx), or None."""
    h, w = walk.shape
    for y, x in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
        if 0 <= y < h and 0 <= x < w and walk[y, x]:
            return art[y * 16:y * 16 + 16, x * 16:x * 16 + 16]
    return None


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


def flower_hmap(px, ground=None):
    """Grass with its petals standing PETAL_H above it."""
    return (1 + PETAL_H * petal_mask(px, ground)).astype(np.int64)


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
