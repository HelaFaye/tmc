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
    hue, sat = RG.hsv(pic)
    leafy = (hue >= 60) & (hue < 170) & (sat >= 0.3)
    l1, n1 = RE._label(mask & leafy)
    l2, n2 = RE._label(mask & ~leafy)
    lab = np.where(l2 > 0, l2 + n1, l1)
    n = n1 + n2
    lum = RG.luminance(pic)
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
ROOF_SMOOTH = 4         # px


def roof(P, pic, base_top, base_obj, voff):
    """A shape resting along its whole width (a roof): each column rises
    from the top of what it rests on by its shading -- lit level, shaded
    upright, mid a slope -- then smoothed over the shape, as the ground
    pass does."""
    from . import mesh as MS
    h, w = pic.shape[:2]
    lab, _n, sh = RG.split(pic)
    m = np.zeros((h, w), bool)
    for R, X in P:
        m[R, X] = True
    rise = np.vectorize(lambda v: RISE.get(int(v), 0.5))(sh).astype(float)
    yb = np.zeros((h, w))
    s = np.zeros((h, w))
    for X in np.unique([x for _r, x in P]):
        col = np.nonzero(m[:, X])[0]
        R0 = col.max()
        below = base_top[R0 + 1, X] if R0 + 1 < h else 0.0
        for R in range(R0, col.min() - 1, -1):
            if not m[R, X]:
                continue
            yb[R, X], s[R, X] = below, rise[R, X]
            below += rise[R, X]
    top = yb + s
    r_ = ROOF_SMOOTH
    Pd = np.pad(np.where(m, top, np.nan), r_, constant_values=np.nan)
    st = [Pd[r_ + dy:r_ + dy + h, r_ + dx:r_ + dx + w]
          for dy in range(-r_, r_ + 1) for dx in range(-r_, r_ + 1)]
    with np.errstate(all="ignore"):
        med = np.nanmedian(np.stack(st), axis=0)
    yb = np.where(m, np.maximum(np.where(np.isnan(med), top, med) - s, 0), 0)
    # mesh only the roof: everything else is set so it makes no faces
    V, F, UV = MS.build(np.where(m, yb, -1e6), np.where(m, s, 0), ~m)
    keep = [i for i, f in enumerate(F) if V[f].min(axis=0)[1] > -1e5]
    F2 = [F[i] for i in keep]
    UV2 = [(UV[i][0], UV[i][1] + voff) for i in keep]
    return [tuple(v) for v in V], F2, UV2
