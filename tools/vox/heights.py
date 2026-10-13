"""Each drawn pixel's height.

The game draws height y over depth z on row z - y, so a pixel's height
fixes where it is. Walking each column from the south (the camera's
side) north:

  ground   a pixel of the ground (its cell is ground and its region
           lies on ground) is at its level, measured from collision and
           the stairs (tilevox.room_heights)
  object   anything else rises from the pixel below it by its shading:
           a lit pixel is a top (rises 0 per row), a shaded one a front
           (rises 1 per row: upright), a mid one a slope (1/2)

Each pixel gets its height at its lower edge (yb) and its rise (s); its
upper edge is at yb + s. An object begins on the ground under it and
ends where the walk comes back onto ground: its back.
"""
import numpy as np

from . import regions as RG
from .room import RE

RISE = {RG.LIT: 0.0, RG.MID: 0.5, RG.SHADED: 1.0}
GROUND_SHARE = 0.6      # of a region's pixels on ground cells, for it to be ground


def ground_levels(rm):
    """Per cell, the ground's height (tilevox's measured levels)."""
    import tilevox as TV
    cls, H, _bl, _fl, _d = TV.room_heights(rm.raw, 0)
    return cls, np.asarray(H, float)


def solve(rm, labels, shade, box=None):
    """(yb, s, ground): per pixel the height at its lower edge, its rise,
    and whether it is ground. box (x0, y0, x1, y1) in cells limits the
    work to a crop of the room (labels and shade are of that crop)."""
    cls, H = ground_levels(rm)
    x0c, y0c = (box[0], box[1]) if box else (0, 0)
    h, w = labels.shape
    cy = (np.arange(h) // 16 + y0c)[:, None].repeat(w, 1)
    cx = (np.arange(w) // 16 + x0c)[None, :].repeat(h, 0)
    cy = np.clip(cy, 0, cls.shape[0] - 1)
    cx = np.clip(cx, 0, cls.shape[1] - 1)
    on_ground = cls[cy, cx] == RE.CLASS_GROUND
    lvl = H[cy, cx]
    # a region is ground when it lies mostly on ground cells
    n = labels.max() + 1
    tot = np.bincount(labels.ravel(), minlength=n)
    gnd = np.bincount(labels.ravel(), weights=on_ground.ravel().astype(float), minlength=n)
    region_ground = gnd >= GROUND_SHARE * np.maximum(tot, 1)
    ground = on_ground & region_ground[labels]
    rise = np.vectorize(lambda v: RISE.get(int(v), np.nan))(shade).astype(float)
    # a front or a top runs level across its width: each row of a region
    # rises as most of its pixels in that row do
    for j in range(1, n):
        ys, xs = np.nonzero(labels == j)
        if not len(ys):
            continue
        for R in np.unique(ys):
            m = ys == R
            v = rise[R, xs[m]]
            v = v[~np.isnan(v)]
            if len(v):
                rise[R, xs[m]] = np.median(v)
    yb = np.zeros((h, w))
    s = np.zeros((h, w))
    for x in range(w):
        below = None            # height at the upper edge of the pixel below
        for y in range(h - 1, -1, -1):
            if ground[y, x]:
                yb[y, x], s[y, x] = lvl[y, x], 0.0
                below = lvl[y, x]
                continue
            base = below if below is not None else lvl[y, x]
            r = rise[y, x]
            if np.isnan(r):     # the outline: continues what is below it
                r = s[y + 1, x] if y + 1 < h else 0.0
            yb[y, x], s[y, x] = base, r
            below = base + r
    return yb, s, ground
