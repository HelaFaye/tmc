"""What the overhead layer draws, as its own surfaces.

The ground pass is solved on what lies under the overhead pixels (the
ground layer). Each overhead shape is then one of two things:

  standing   its feet (its lowest drawn rows) rest on something solid
             that the ground pass built: it is upright in one plane at
             the depth its feet give (the town gate's arch, with its
             Triforce on top), as thick as its lit top band
  canopy     anything else: a level sheet DECK_H over the ground (a tree's
             crown, a bridge Link walks under)

Every pixel at row R in a plane at depth zp is at height zp - R.
"""
import numpy as np

from . import regions as RG
from .room import RE

DECK_H = 24             # px: a canopy's height over the ground under it
FOOT_LEVEL = 12         # px: a resting edge lies this near the shape's lowest row
FOOT_ROWS = 3           # px: an overhead shape's feet are its lowest rows
T_MIN, T_MAX = 3, 20    # px: a standing shape's thickness, from its lit band
ARCH_GAP = 0.2          # of its width open underneath between its feet: an arch
ROOF_FEET = 0.6         # of its columns resting on something: a roof, not an arch
LIT_STEP = 40           # luminance: its lit band is this much over its median


def under(rm, box):
    """The crop's picture with the overhead layer's pixels replaced by the
    ground layer under them, and the overhead mask."""
    x0, y0, x1, y1 = box
    sl = (slice(y0 * 16, y1 * 16), slice(x0 * 16, x1 * 16))
    pic = rm.picture[sl].copy()
    if rm.overhead is None:
        return pic, np.zeros(pic.shape[:2], bool)
    m = rm.overhead[sl][..., 3] > 0
    pic[m] = rm.ground[sl][m]
    return pic, m


def shapes(mask, pic, base_top, base_obj):
    """[(pixels {(R, X)}, zp or None, thickness)] for the overhead shapes;
    zp None is a canopy. base_top is the ground pass's top height per
    pixel, base_obj where it built an object (not ground)."""
    # leaf and the rest are separate things even where they touch (a tree
    # over a roof): split before labelling
    # things are drawn apart by their dark outline (a tree over a roof);
    # shapes are what the outline encloses, the outline joining the shape
    # it borders
    lum = RG.luminance(pic)
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
    out = []
    h = mask.shape[0]
    for j in range(1, n + 1):
        ys, xs = np.nonzero(lab == j)
        if len(ys) < 8:
            continue
        P = set(zip(ys.tolist(), xs.tolist()))
        feet = []
        low = int(ys.max())
        gap = run = 0
        for X in np.unique(xs):
            col = ys[xs == X]
            R = int(col.max())
            if R < low - FOOT_LEVEL:
                run += 1        # an underside high above its feet (an arch's)
                gap = max(gap, run)
                continue
            run = 0
            if R + 1 < h and base_obj[R + 1, X]:
                feet.append(R + 1 + base_top[R + 1, X])
        width = len(np.unique(xs))
        arch = gap >= ARCH_GAP * width
        if not arch and len(feet) >= FOOT_ROWS and len(feet) >= ROOF_FEET * width:
            out.append((P, "roof", 0))
            continue
        if len(feet) >= FOOT_ROWS:
            zp = int(round(np.median(feet)))
            # its lit band along the top: rows per column much lighter
            # than the shape's median
            med = np.median(lum[ys, xs])
            widths = []
            for X in np.unique(xs):
                col = np.sort(ys[xs == X])
                k = 0
                while k < len(col) and lum[col[k], X] > med + LIT_STEP:
                    k += 1
                if k:
                    widths.append(k)
            T = int(np.clip(np.percentile(widths, 75) if widths else T_MIN, T_MIN, T_MAX))
            out.append((P, zp, T))
        else:
            out.append((P, None, 0))
    return out


def slab(P, zp, T, voff):
    """Quads for pixels P standing in the plane at depth zp, T thick behind
    it; every face wears its own pixel (row offset voff in the texture)."""
    V, F, UV = [], [], []

    def quad(pts, R, X):
        F.append(list(range(len(V), len(V) + 4)))
        V.extend(pts)
        UV.append((X + 0.5, voff + R + 0.5))
    za, zb = zp - T, zp
    for (R, X) in P:
        y0, y1 = zp - R - 1, zp - R
        x0, x1 = X, X + 1
        quad([(x0, y1, zb), (x1, y1, zb), (x1, y0, zb), (x0, y0, zb)], R, X)
        quad([(x1, y1, za), (x0, y1, za), (x0, y0, za), (x1, y0, za)], R, X)
        if (R - 1, X) not in P:
            quad([(x0, y1, za), (x1, y1, za), (x1, y1, zb), (x0, y1, zb)], R, X)
        if (R + 1, X) not in P:
            quad([(x0, y0, zb), (x1, y0, zb), (x1, y0, za), (x0, y0, za)], R, X)
        if (R, X + 1) not in P:
            quad([(x1, y1, zb), (x1, y1, za), (x1, y0, za), (x1, y0, zb)], R, X)
        if (R, X - 1) not in P:
            quad([(x0, y1, za), (x0, y1, zb), (x0, y0, zb), (x0, y0, za)], R, X)
    return V, F, UV


def sheet(P, base_top, voff):
    """A canopy: each pixel a level quad DECK_H over the ground under it."""
    V, F, UV = [], [], []
    for (R, X) in P:
        y = base_top[R, X] + DECK_H
        z0, z1 = R + y, R + 1 + y
        F.append(list(range(len(V), len(V) + 4)))
        V.extend([(X, y, z0), (X + 1, y, z0), (X + 1, y, z1), (X, y, z1)])
        UV.append((X + 0.5, voff + R + 0.5))
    return V, F, UV


RISE = {RG.LIT: 0.0, RG.MID: 0.5, RG.SHADED: 1.0}
RIM_SAT = 0.2           # saturation: below, a rim's pale stone or plaster
RIM_SHARE = 0.6         # of a row's width pale: a rim row
RIM_AFTER = 12          # px: a rim ends a cap only after this much of it
ROOF_SMOOTH = 4         # px


def _cap_rows(n, steps=400):
    """A rounded cap's front half, as seen: for a top drawn n rows tall,
    depth D = n and peak P = n / 2 (a cap as high as half its depth shows
    exactly its front half), each drawn row i's height above the rim.
    A point t of the way to the ridge is at depth t D / 2 and height
    P (1 - (1 - t)^2), drawn t D / 2 + height rows up."""
    D, P = float(n), n / 2.0
    t = np.linspace(0, 1, steps)
    yy = P * (1 - (1 - t) ** 2)
    rr = t * D / 2 + yy
    return np.interp(np.arange(n + 1), rr, yy)


def roof(P, pic, base_top, base_obj, voff):
    """A shape resting along its width (a roof, a mushroom's cap). In each
    column, each run of its drawn rows is a rim -- its shaded rows from the
    bottom, upright -- then a rounded top over it, rising to a ridge and
    rounding back out of sight (_cap_rows). Rim heights are smoothed
    across the shape, so it is one shape."""
    from . import mesh as MS
    h, w = pic.shape[:2]
    lab, _n, sh = RG.split(pic)
    m = np.zeros((h, w), bool)
    for R, X in P:
        m[R, X] = True
    shaded = sh == RG.SHADED
    # rim rows: low-colour pixels across most of the shape's width there
    # (the white band under each cap of a stack); a cap's spots do not
    _hue, sat = RG.hsv(pic)
    pale = (sat < RIM_SAT) & m
    width = m.sum(axis=1)
    rim = (width > 0) & (pale.sum(axis=1) >= RIM_SHARE * np.maximum(width, 1))
    runs = []
    for X in np.unique([x for _r, x in P]):
        R = h - 1
        while R >= 0:
            if not m[R, X]:
                R -= 1
                continue
            rb = R
            top_rows = 0
            while R >= 0 and m[R, X]:
                if rim[R]:
                    if top_rows >= RIM_AFTER:
                        break           # the next cap's rim: this cap ends
                else:
                    top_rows += 1
                R -= 1
            rt = R + 1
            f = rb
            while f >= rt and shaded[f, X]:
                f -= 1
            g = base_top[rb + 1, X] if rb + 1 < h else 0.0
            runs.append([X, rt, rb, rb - f, g])
    if not runs:
        return [], [], []
    Rn = np.array(runs, float)
    Rn = Rn[np.argsort(Rn[:, 0])]
    k = ROOF_SMOOTH * 2 + 1
    hf_s = np.array([np.median(Rn[max(0, i - k // 2):i + k // 2 + 1, 3]) for i in range(len(Rn))])
    yb = np.full((h, w), -1e6)
    s = np.zeros((h, w))
    for (X, rt, rb, _hf, g), hf in zip(Rn, hf_s):
        X, rt, rb = int(X), int(rt), int(rb)
        hf = int(min(hf, rb - rt + 1))
        for r in range(rb, rb - hf, -1):
            yb[r, X], s[r, X] = g + (rb - r), 1.0
        n = (rb - hf) - rt + 1
        if n <= 0:
            continue
        ys = _cap_rows(n)
        top0 = g + hf
        for i in range(n):
            r = rb - hf - i
            yb[r, X] = top0 + ys[i]
            s[r, X] = ys[i + 1] - ys[i]
    V, F, UV = MS.build(yb, s, ~m)
    keep = V[F].min(axis=1)[:, 1] > -1e5
    return V, F[keep], UV[keep] + np.array([0.0, voff])
