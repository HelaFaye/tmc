"""A room built from its tiles' kinds (docs/vr/12-reassessment.md).

Every cell's tiles have a kind (tiles.py: a first guess, corrected by
review). The room is built from those kinds, the collision levels and the
45-degree rule (a point at height y over depth z is drawn on row z - y),
with no per-pixel guessing from shading:

  ground layer, walking each pixel column from the camera's side north:
    floor, water, void   flat at the cell's level
    face, trunk, prop    upright: each drawn row is a pixel of height, so a
                         wall stands exactly as tall as it is drawn
    top                  flat, on whatever stands below it
    foliage              left to its group (below)
    a cell the overhead layer draws over is never seen: flat
  overhead layer, by connected groups of one kind:
    foliage   long and thin: a hedge, a box as high as its front is drawn;
              otherwise a crown, rounded, rising no more than the group
              is wide (the tree's width, not one row's)
    prop, top one shape per thing: a big one is a roof, one rounded cap
              (as a crown); a small one level at one height
    top       flat, on the face drawn below it
    deck      flat at the level of the cells under it (an upper floor
              reached by steps, a courtyard)
    void      nothing
  flights of steps and the cliffs beside them come from stairs.py.

check() measures what can go wrong in the world, not from the game's
camera: things resting on nothing or buried, crowns out of proportion,
uneven floors.
"""
import json
from pathlib import Path

import numpy as np

from . import heights as HT
from . import mesh as MS
from . import objects as OB
from . import overhead as OV
from . import stairs as ST
from . import tiles as TL
from .room import RE

FLAT = ("floor", "water", "void", "deck")
KEEP_LEVEL = ("water", "void", "deck")   # flat at their own level even under a thing
UPRIGHT = ("face", "trunk", "prop")
HEDGE_MAX_W = 40        # px: foliage this thin (across or down) ...
HEDGE_MIN_LEN = 64      # px: ... and at least this long is a hedge
HEDGE_FRONT_MAX = 16    # px: a hedge's front is at most this tall
CROWN_SHARE = 0.5       # a crown rises at most this share of its group's width
CROWN_MAX = 48          # px: and no higher than a tree
FACE_MAX = 48           # px: a face rises from a floor at most this far (3 cells)
FOLIAGE_MIN_PX = 64     # px: a thing with less leaf than this is not a crown
LEAF_REACH = 6          # px: leaves this near a crown are part of it
FOLIAGE_SHARE = 0.5     # share of a thing in leaf for it to be one crown
ROOF_MIN_CELLS = 4      # a group of busy overhead cells this big is a roof
KINDS_FILE = Path(__file__).resolve().parents[2] / "vr" / "tiles" / "kinds.json"


def load_kinds(catalogue):
    """{tile key: kind}: the catalogue's first guesses, overridden by the
    reviewed kinds in vr/tiles/kinds.json (committed: keys and kinds only)."""
    kinds = {k: e.get("kind") for k, e in catalogue.items()}
    if KINDS_FILE.exists():
        kinds.update(json.loads(KINDS_FILE.read_text()))
    return kinds


def kind_grids(rm, kinds):
    """(K0, K1): each cell's ground and overhead kind (None: no tile)."""
    r = rm.raw
    K0 = np.full((r.cells_h, r.cells_w), None, object)
    K1 = np.full((r.cells_h, r.cells_w), None, object)
    for (li, cy, cx), (key, _tt, _c) in TL.room_tiles(r, rm.cls).items():
        (K0 if li == 0 else K1)[cy, cx] = kinds.get(key) or "floor"
    return K0, K1


def _cells(mask_cells, shape):
    return np.kron(mask_cells, np.ones((16, 16), bool))[:shape[0], :shape[1]]


def ground(rm, K0, K1, overhead_alpha, covered=None):
    """(yb, s, flat): the ground layer's pixels. covered: pixels another
    shape builds (a crown); the ground lies flat under them."""
    h, w = rm.picture.shape[:2]
    cls, H = HT.ground_levels(rm)
    cy = np.minimum(np.arange(h) // 16, cls.shape[0] - 1)
    cx = np.minimum(np.arange(w) // 16, cls.shape[1] - 1)
    lvl = H[cy][:, cx]
    k0 = K0[cy][:, cx]
    k1 = K1[cy][:, cx]
    hidden = overhead_alpha & np.isin(k1, ["foliage", "prop", "top", "deck"])
    if covered is None:
        covered = np.zeros((h, w), bool)
    flat = np.isin(k0, FLAT) | hidden | (k0 == "foliage") | (k0 == None) | covered  # noqa: E711
    upright = np.isin(k0, UPRIGHT) & ~flat
    under = (hidden | (k0 == "foliage") | covered) & ~np.isin(k0, KEEP_LEVEL)
    st_m, st_yb, st_s = ST.fixed(rm, rm.picture)
    st_m = st_m & ~covered
    yb = np.zeros((h, w))
    s = np.zeros((h, w))
    for x in range(w):
        below = None
        floor = None    # the last ground south of here (not a thing's top)
        run = 0
        for y in range(h - 1, -1, -1):
            if st_m[y, x]:
                yb[y, x], s[y, x] = st_yb[y, x], st_s[y, x]
                below = floor = yb[y, x] + s[y, x]
                continue
            if flat[y, x]:
                # under a thing (leaves, a roof), the ground goes on at the
                # height of the ground in front of it: the cell is blocked
                # by the thing standing there, not raised
                g = floor if under[y, x] and floor is not None else lvl[y, x]
                yb[y, x], s[y, x] = g, 0.0
                below = floor = g
                run = 0
                continue
            base = below if below is not None else lvl[y, x]
            # a face rises from a floor, and only as far as a wall is drawn
            # tall: past FACE_MAX rows the strip is the wall's top seen
            # from above (a side wall), not more face
            run = run + 1 if upright[y, x] else 0
            r_ = 1.0 if upright[y, x] and run <= FACE_MAX else 0.0
            yb[y, x], s[y, x] = base, r_
            below = base + r_
    return yb, s, flat | st_m


def _groups(mask):
    lab, n = RE._label(mask)
    return [lab == j for j in range(1, n + 1)]


def _outlined(mask, lum):
    """mask's things, split along the dark outline the game draws between
    them (two crowns that touch), the outline joining the thing it bounds."""
    from . import regions as RG
    body = mask & (lum >= RG.OUTLINE)
    lab, n = RE._label(body)
    for _ in range(3):
        P_ = np.pad(lab, 1)
        h_, w_ = lab.shape
        grow = np.zeros_like(lab)
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = P_[1 + dy:h_ + 1 + dy, 1 + dx:w_ + 1 + dx]
            grow = np.where((grow == 0) & (nb > 0), nb, grow)
        lab = np.where(mask & (lab == 0), grow, lab)
    return [lab == j for j in range(1, n + 1) if (lab == j).sum() >= 16]


def _columns(m):
    """{x: (rt, rb)}: each column's drawn extent in mask m."""
    out = {}
    for x in np.nonzero(m.any(axis=0))[0]:
        ys = np.nonzero(m[:, x])[0]
        out[int(x)] = (int(ys.min()), int(ys.max()))
    return out


def crown(m, top):
    """A crown's heights: in each column, rounded up from the ground (or
    trunk) under its lowest drawn row, rising no more than CROWN_SHARE of
    the group's width."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    xs = np.nonzero(m.any(axis=0))[0]
    peak = min(CROWN_SHARE * (xs.max() - xs.min() + 1), CROWN_MAX)
    for x, (rt, rb) in _columns(m).items():
        g = float(top[min(rb + 1, h - 1), x])
        n = rb - rt + 1
        ys = OV._cap_rows(n, peak=peak)
        for i in range(n):
            r = rb - i
            yb[r, x], s[r, x] = g + ys[i], ys[i + 1] - ys[i]
    return yb, s


def foot_level(m, top):
    """The one height a thing rests at: the median of what is under the
    centre third of its foot."""
    h = m.shape[0]
    cols = _columns(m)
    xs = np.array(sorted(cols))
    x0, x1 = xs.min(), xs.max() + 1
    mid, third = (x0 + x1) / 2.0, (x1 - x0) / 6.0
    xs = [x for x in xs if abs(x + 0.5 - mid) <= third] or list(xs)
    return float(np.median([top[min(cols[x][1] + 1, h - 1), x] for x in xs]))


def dome(m, top):
    """A crown as one ellipsoid: as wide as the thing is drawn, rising
    CROWN_SHARE of that (at most CROWN_MAX), as deep as the drawn rows then
    allow (a point at height y over depth z is drawn on row z - y, so the
    ellipsoid's drawn extent is 2 sqrt(az^2 + c^2)). It rests at one height,
    the median of what is under the centre third of its foot, so it is one
    shape and not columns on different footings. Each drawn
    pixel takes the front-most point of the ellipsoid on its row."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    cols = _columns(m)
    xs = np.array(sorted(cols))
    x0, x1 = xs.min(), xs.max() + 1
    ax = (x1 - x0) / 2.0
    cxm = (x0 + x1) / 2.0
    rt = min(v[0] for v in cols.values())
    rb = max(v[1] for v in cols.values()) + 1
    half = (rb - rt) / 2.0
    # the drawn rows are shared evenly between height and depth (a crown
    # drawn as tall as it is wide is then as deep as it is high)
    c = min(CROWN_SHARE * (x1 - x0), CROWN_MAX, half / np.sqrt(2))
    az = np.sqrt(max(half * half - c * c, 1.0))
    g = foot_level(m, top)
    # its front-most lowest point (drawn on its foot row) rests at g; the
    # rest of its underside is hidden behind it
    yc = g + c * c / half
    zc = (rt + rb) / 2.0 + yc

    def height(x, r):
        # on row r: z = r + y; ((y-yc)/c)^2 + ((r+y-zc)/az)^2 = 1 - u^2
        u = min(((x + 0.5 - cxm) / ax) ** 2, 1.0)
        A = 1 / c ** 2 + 1 / az ** 2
        B = -2 * yc / c ** 2 + 2 * (r - zc) / az ** 2
        C = yc ** 2 / c ** 2 + (r - zc) ** 2 / az ** 2 - (1 - u)
        d = B * B - 4 * A * C
        return (-B + np.sqrt(max(d, 0.0))) / (2 * A)

    for x, (a, b) in cols.items():
        for r in range(a, b + 1):
            if m[r, x]:
                lo, hi = height(x, r + 1), height(x, r)
                yb[r, x], s[r, x] = lo, hi - lo
    return yb, s


def is_hedge(m):
    """Thin everywhere (most pixels lie in a run, across or down, no wider
    than HEDGE_MAX_W) and long: a hedge, whatever turns it takes. A crown
    is wide both ways; a bush is short."""
    ys, xs = np.nonzero(m)
    if max(xs.max() - xs.min(), ys.max() - ys.min()) + 1 < HEDGE_MIN_LEN:
        return False
    thin = np.minimum(OV._row_widths(m), OV._row_widths(m.T).T)[m]
    return float(np.median(thin)) <= HEDGE_MAX_W


def _runs(col):
    """[(top, bottom)]: the runs of True rows in a column."""
    d = np.diff(np.concatenate([[0], col.astype(np.int8), [0]]))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1))


def hedge(m, top, lum):
    """A hedge: a box at one level. Each run of drawn rows in a column has
    a front -- the darker band at its bottom, the same height all along
    the hedge (the median; at most HEDGE_FRONT_MAX rows), upright -- and
    over it a level top running back for the rest of the run."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    med = float(np.median(lum[m]))
    g = foot_level(m, top)
    runs = [(x, a, b) for x in np.nonzero(m.any(axis=0))[0] for a, b in _runs(m[:, x])]
    fronts = []
    for x, a, b in runs:
        f = 0
        while f < HEDGE_FRONT_MAX and b - f >= a and lum[b - f, x] < med:
            f += 1
        fronts.append(f)
    hf = int(np.median(fronts)) if fronts else 0
    for x, a, b in runs:
        for i, r in enumerate(range(b, a - 1, -1)):
            if i < hf:
                yb[r, x], s[r, x] = g + i, 1.0
            else:
                yb[r, x], s[r, x] = g + hf, 0.0
    return yb, s


def flat_on(m, top, level=None):
    """Level at one height, the one it rests at (a wall's top on its face),
    or at the given per-pixel level (a deck)."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    g0 = foot_level(m, top) if level is None else None
    for x, (rt, rb) in _columns(m).items():
        g = g0 if level is None else float(level[rb, x])
        yb[rt:rb + 1, x] = np.where(m[rt:rb + 1, x], g, -1e6)
    return yb, s


def build(rm, kinds, things=None):
    """(V, F, UV, tex, report): the room's mesh, its texture (ground
    layer's picture over the shown one) and check()'s report."""
    import extract_art
    from .scenes import _kept, _join
    from . import regions as RG
    pic = rm.picture
    h, w = pic.shape[:2]
    K0, K1 = kind_grids(rm, kinds)
    o = extract_art.room_art(rm.raw, 1) if len(rm.raw.layers) > 1 and rm.raw.layers[1]["present"] else None
    alpha = (np.asarray(o)[:h, :w, 3] > 0) if o is not None else np.zeros((h, w), bool)
    lum = RG.luminance(pic)
    # foliage: the overhead's leaves and the ground layer's together, one
    # shape per thing (objects.py); leaves outside any thing are split
    # along their outlines
    fol = (_cells(K1 == "foliage", (h, w)) & alpha) | _cells(K0 == "foliage", (h, w))
    leafy = OB.leafy(pic)
    if things is None:
        things = OB.objects(rm, *OB.statistics(Path(rm.meta["path"]).parent,
                                               Path(rm.meta["path"]).parent / "binding.json"))
    crowns = np.zeros((h, w), np.int32)
    for j in range(1, things.max() + 1):
        t = things == j
        tf = (t & fol).sum()
        if tf >= FOLIAGE_MIN_PX and tf >= FOLIAGE_SHARE * t.sum():
            # a crown (its tiles say so): all its leaves, whatever the
            # kinds of the tiles they lie in say (a green roof is no crown)
            crowns[t & (fol | leafy)] = j
    # leaves in the floor's colours (a highlight) were left out of the
    # things: they join the crown they lie in
    for _ in range(LEAF_REACH):
        P_ = np.pad(crowns, 1)
        grow = np.zeros_like(crowns)
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = P_[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx]
            grow = np.where((grow == 0) & (nb > 0), nb, grow)
        crowns = np.where(fol & (crowns == 0), grow, crowns)
    shapes = [crowns == j for j in np.unique(crowns) if j]
    rest = _outlined(fol & (crowns == 0), lum)
    covered = crowns > 0
    for m in rest:
        covered |= m
    shapes += rest
    yb, s, flat = ground(rm, K0, K1, alpha, covered=covered)
    # the ground under a thing is one level, the one it rests at (columns
    # carried on from different things south of it would stand as walls)
    carried = ~_cells(np.isin(K0, KEEP_LEVEL), (h, w))
    # front to back: a thing behind rests on the ground under the things
    # in front once theirs is set
    shapes.sort(key=lambda m: -np.nonzero(m.any(axis=1))[0].max())
    for m in shapes:
        u = m & carried
        if u.any():
            yb[u], s[u] = foot_level(m, yb + s), 0.0
    top = yb + s
    parts = [_kept(*MS.build(yb, s, flat))]
    cls, H = HT.ground_levels(rm)
    lvl = H[np.minimum(np.arange(h) // 16, H.shape[0] - 1)][:, np.minimum(np.arange(w) // 16, H.shape[1] - 1)]
    groups = {"crown": [], "hedge": [], "roof": [], "prop": [], "top": [], "deck": []}
    for m in shapes:
        if is_hedge(m):
            groups["hedge"].append((m, hedge(m, top, lum)))
        else:
            groups["crown"].append((m, dome(m, top)))
    # the overhead layer's other pieces (roofs, wall tops), one shape per
    # thing: a big one is a roof, a small one level at one height. Pieces
    # in no thing are taken by their connected groups.
    ov = _cells(np.isin(K1, ["prop", "top"]), (h, w)) & alpha & ~covered
    pieces = []
    for j in np.unique(things[ov]):
        if j:
            # a thing's separate patches of overhead art are separate
            # roofs (a stack of houses always drawn together is one thing
            # to the tile statistics; the wall between their roofs parts them)
            pieces += _groups(ov & (things == j))
    pieces += _groups(ov & (things == 0))
    for m in pieces:
        if m.sum() >= ROOF_MIN_CELLS * 256:
            # a roof is one rounded cap over the whole thing, like a
            # crown (columns shaped apart stand up as fins)
            groups["roof"].append((m, dome(m, top)))
        else:
            # seen from above: the top of what stands under it, never
            # stood upright, and one level (not a level per column)
            groups["top"].append((m, flat_on(m, top)))
    for m in _groups(_cells(K1 == "deck", (h, w)) & alpha):
        groups["deck"].append((m, flat_on(m, top, level=lvl)))
    for kind, gs in groups.items():
        for m, hs in gs:
            if hs is None:
                continue
            gyb, gs_ = hs
            ys, xs = np.nonzero(m)
            y0, y1 = max(0, ys.min() - 1), min(h, ys.max() + 2)
            x0, x1 = max(0, xs.min() - 1), min(w, xs.max() + 2)
            part = MS.build(gyb[y0:y1, x0:x1], gs_[y0:y1, x0:x1], ~m[y0:y1, x0:x1],
                            ox=x0, oz=y0, px0=x0, py0=y0)
            parts.append(_kept(*part))
    V, F, UV = _join(parts)
    report = check(groups, top, flat & ~covered, K0, rm)
    report["overhead pixels stood upright"] = upright_overhead(V, F, UV, alpha)
    return V, F, UV, pic, report


UPRIGHT_NZ = 0.95       # a quad facing the camera this squarely stands upright


def upright_overhead(V, F, UV, alpha):
    """How many overhead-layer pixels the mesh stands upright (a quad
    facing the camera, vertical). The overhead layer is drawn over the
    actors -- roof tops, wall tops, canopy -- and is seen from above."""
    if not len(F):
        return 0
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    u = np.clip(UV[:, 0].astype(int), 0, alpha.shape[1] - 1)
    v = np.clip(UV[:, 1].astype(int), 0, alpha.shape[0] - 1)
    front = n[:, 2] >= UPRIGHT_NZ      # (a back, facing away, closes a raised pixel)
    return int(len({(int(x), int(y)) for x, y in zip(u[front & alpha[v, u]], v[front & alpha[v, u]])}))


def surface(top, x, z):
    """The ground pass's height at column x, depth z: the row drawn there
    is the one whose depth (row + height) is nearest z."""
    zs = np.arange(top.shape[0]) + top[:, x]
    return float(top[int(np.argmin(np.abs(zs - z))), x])


def feet(gyb, m):
    """[(x, depth, height)]: the lowest drawn edge of each of m's columns,
    in the world."""
    out = []
    for x, (rt, rb) in _columns(m).items():
        y = float(gyb[rb, x])
        out.append((x, rb + 1 + y, y))
    return out


TOUCH = 2.0             # px: a thing within this of the ground rests on it
SUNK = 2.0              # px: an edge this far below the ground is buried
BURIED_SHARE = 0.25     # a thing with more of its columns buried is sunk


def check(groups, top, flat, K0, rm):
    """What can go wrong, counted in the world (not from the game's camera,
    which any heights reproduce): these should all be 0 or near it."""
    rep = {}
    # each thing rests on the ground somewhere, and no edge is buried
    floating = sunk = edges = 0
    for kind in ("crown", "hedge", "prop", "top"):
        for m, hs in groups[kind]:
            if hs is None:
                continue
            gap = np.array([y - surface(top, x, z) for x, z, y in feet(hs[0], m)])
            if len(gap) and gap.min() > TOUCH:
                floating += 1
            if len(gap) and (gap < -SUNK).mean() > BURIED_SHARE:
                sunk += 1
            edges += int((gap < -SUNK).sum())
    rep["things resting on nothing"] = floating
    rep["things buried over a quarter of their width"] = sunk
    rep["columns buried (all things)"] = edges
    # proportion: a crown no higher than it is wide
    bad = 0
    for m, (gyb, gs_) in groups["crown"]:
        xs = np.nonzero(m.any(axis=0))[0]
        hgt = np.max(np.where(m, np.maximum(gyb, gyb + gs_), -1e6)) - np.min(np.where(m, gyb, 1e6))
        if hgt > xs.max() - xs.min() + 1:
            bad += 1
    rep["crowns taller than wide"] = bad
    rep["crowns"] = len(groups["crown"])
    rep["hedges"] = len(groups["hedge"])
    rep["roofs"] = len(groups["roof"])
    rep["props"] = len(groups["prop"])
    # floors are flat: every floor cell at one height
    uneven = 0
    for cy_ in range(K0.shape[0]):
        for cx_ in range(K0.shape[1]):
            if K0[cy_, cx_] in ("floor", "water"):
                c = top[cy_ * 16:cy_ * 16 + 16, cx_ * 16:cx_ * 16 + 16]
                f = flat[cy_ * 16:cy_ * 16 + 16, cx_ * 16:cx_ * 16 + 16]
                if c[f].size and np.ptp(c[f]) > 0.5:
                    uneven += 1
    rep["floor cells not flat"] = uneven
    return rep
