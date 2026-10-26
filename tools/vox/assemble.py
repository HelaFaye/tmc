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
    prop      a big group is a roof (overhead.roof); a small one stands
              upright on its foot
    top       flat, on the face drawn below it
    deck      flat at the level of the cells under it (an upper floor
              reached by steps, a courtyard)
    void      nothing
  flights of steps and the cliffs beside them come from stairs.py.

check() measures what can go wrong: anything floating over what is under
it, tops not resting on faces, and crowns or hedges out of proportion.
"""
import json
from pathlib import Path

import numpy as np

from . import heights as HT
from . import mesh as MS
from . import overhead as OV
from . import stairs as ST
from . import tiles as TL
from .room import RE

FLAT = ("floor", "water", "void", "deck")
UPRIGHT = ("face", "trunk", "prop")
HEDGE_MAX_W = 40        # px: foliage this narrow across ...
HEDGE_RATIO = 2.5       # ... and this many times as long is a hedge
HEDGE_FRONT_MAX = 16    # px: a hedge's front is at most this tall
CROWN_SHARE = 0.5       # a crown rises at most this share of its group's width
CROWN_MAX = 48          # px: and no higher than a tree
FACE_MAX = 48           # px: a face rises from a floor at most this far (3 cells)
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


def ground(rm, K0, K1, overhead_alpha):
    """(yb, s, flat): the ground layer's pixels."""
    h, w = rm.picture.shape[:2]
    cls, H = HT.ground_levels(rm)
    cy = np.minimum(np.arange(h) // 16, cls.shape[0] - 1)
    cx = np.minimum(np.arange(w) // 16, cls.shape[1] - 1)
    lvl = H[cy][:, cx]
    k0 = K0[cy][:, cx]
    k1 = K1[cy][:, cx]
    hidden = overhead_alpha & np.isin(k1, ["foliage", "prop", "top", "deck"])
    flat = np.isin(k0, FLAT) | hidden | (k0 == "foliage") | (k0 == None)  # noqa: E711
    upright = np.isin(k0, UPRIGHT) & ~flat
    st_m, st_yb, st_s = ST.fixed(rm, rm.picture)
    yb = np.zeros((h, w))
    s = np.zeros((h, w))
    for x in range(w):
        below = None
        run = 0
        for y in range(h - 1, -1, -1):
            if st_m[y, x]:
                yb[y, x], s[y, x] = st_yb[y, x], st_s[y, x]
                below = yb[y, x] + s[y, x]
                continue
            if flat[y, x]:
                yb[y, x], s[y, x] = lvl[y, x], 0.0
                below = lvl[y, x]
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


def hedge(m, top, lum):
    """A hedge: a box. Its front is the darker band at the bottom of each
    column (at most HEDGE_FRONT_MAX rows), upright; its top is level."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    med = float(np.median(lum[m]))
    cols = _columns(m)
    fronts = []
    for x, (rt, rb) in cols.items():
        f = 0
        while f < HEDGE_FRONT_MAX and rb - f >= rt and lum[rb - f, x] < med:
            f += 1
        fronts.append(f)
    hf = int(np.median(fronts)) if fronts else 0
    for x, (rt, rb) in cols.items():
        g = float(top[min(rb + 1, h - 1), x])
        for i, r in enumerate(range(rb, rt - 1, -1)):
            if i < hf:
                yb[r, x], s[r, x] = g + i, 1.0
            else:
                yb[r, x], s[r, x] = g + hf, 0.0
    return yb, s


def flat_on(m, top, level=None):
    """Level at the height under its lowest row (a wall's top on its face),
    or at the given per-pixel level (a deck)."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    for x, (rt, rb) in _columns(m).items():
        g = float(top[min(rb + 1, h - 1), x]) if level is None else float(level[rb, x])
        yb[rt:rb + 1, x] = np.where(m[rt:rb + 1, x], g, -1e6)
    return yb, s


def upright_on(m, top):
    """Standing on its foot: each drawn row a pixel of height."""
    h, w = m.shape
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    for x, (rt, rb) in _columns(m).items():
        g = float(top[min(rb + 1, h - 1), x])
        for i, r in enumerate(range(rb, rt - 1, -1)):
            if m[r, x]:
                yb[r, x], s[r, x] = g + i, 1.0
    return yb, s


def build(rm, kinds):
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
    yb, s, flat = ground(rm, K0, K1, alpha)
    top = yb + s
    parts = [_kept(*MS.build(yb, s, flat))]
    lum = RG.luminance(pic)
    cls, H = HT.ground_levels(rm)
    lvl = H[np.minimum(np.arange(h) // 16, H.shape[0] - 1)][:, np.minimum(np.arange(w) // 16, H.shape[1] - 1)]
    groups = {"crown": [], "hedge": [], "roof": [], "prop": [], "top": [], "deck": []}
    # foliage: the overhead's leaves and the ground layer's together
    fol = (_cells(K1 == "foliage", (h, w)) & alpha) | _cells(K0 == "foliage", (h, w))
    for m in _outlined(fol, lum):
        ys, xs = np.nonzero(m)
        ext = sorted([xs.max() - xs.min() + 1, ys.max() - ys.min() + 1])
        if ext[0] <= HEDGE_MAX_W and ext[1] >= HEDGE_RATIO * ext[0]:
            groups["hedge"].append((m, hedge(m, top, lum)))
        else:
            groups["crown"].append((m, crown(m, top)))
    busy = _cells(K1 == "prop", (h, w)) & alpha
    for m in _groups(busy):
        if m.sum() >= ROOF_MIN_CELLS * 256:
            P = set(zip(*[a.tolist() for a in np.nonzero(m)]))
            v, f, uv = OV.roof(P, pic, top, ~flat, 0)
            if len(f):
                parts.append((np.asarray(v), np.asarray(f), np.asarray(uv)))
                groups["roof"].append((m, None))
        else:
            groups["prop"].append((m, upright_on(m, top)))
    for m in _groups(_cells(K1 == "top", (h, w)) & alpha):
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
    report = check(groups, top, flat, K0, rm)
    return V, F, UV, pic, report


def check(groups, top, flat, K0, rm):
    """What can go wrong, counted: these numbers should all be 0 or near it."""
    h, w = top.shape
    rep = {}
    # nothing floats: each group's lowest row rests on what is under it
    gap = 0
    for kind in ("crown", "hedge", "prop", "top"):
        for m, hs in groups[kind]:
            if hs is None:
                continue
            gyb, _ = hs
            for x, (rt, rb) in _columns(m).items():
                under = top[min(rb + 1, h - 1), x]
                if gyb[rb, x] > under + 1.5:
                    gap += 1
    rep["columns floating over what is under them"] = gap
    # proportion: a crown no higher than its group is wide, a hedge's
    # front no taller than HEDGE_FRONT_MAX
    bad = 0
    for m, (gyb, gs_) in groups["crown"]:
        xs = np.nonzero(m.any(axis=0))[0]
        hgt = np.max(np.where(m, gyb + gs_, -1e6)) - np.min(np.where(m, gyb, 1e6))
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
