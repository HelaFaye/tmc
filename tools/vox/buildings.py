"""Buildings built by their style (classify.py).

A building is built as one piece from its own pixels. In each column, the
rows from its foot that are not its top's material are its wall: upright,
their height smoothed across the building. The rows above are its roof,
with a profile by style:

  cap, thatch   rounded over the top
  slate         rising to a flat top
  block         flat

Each roof row's height is solved so the game's camera sees exactly the
drawing (a point at height y over depth z is drawn at row z - y).
"""
import numpy as np

from . import classify as CL
from . import regions as RG

BUILT = ("cap", "thatch", "slate")  # styles built here so far; others keep the general pass
WALL_SMOOTH = 15        # px: wall heights agree across this many columns
ROOF_RUN = 4            # rows: the roof starts at this many rows of its material
                        # (a speck of it in a door frame or a window is wall)
SLATE_RISE = 0.35       # of a slate roof's depth to its flat top's run
SLATE_PEAK = 0.25       # a slate roof's height, of its drawn rows (the rest is depth)


def profile(n, style):
    """Heights above the eaves for a roof drawn n rows tall."""
    if n <= 0:
        return np.zeros(1)
    steps = 600
    t = np.linspace(0, 1, steps)
    if style == "block":
        return np.zeros(n + 1)
    D = float(n)
    if style == "slate":
        P = n * SLATE_PEAK
        yy = P * np.minimum(1.0, t / SLATE_RISE)
    else:
        P = n / 2.0
        yy = P * (1 - (1 - t) ** 2)
    rr = t * D / 2 + yy if style != "slate" else t * D * 0.6 + yy
    rr = rr * (n / rr[-1])          # the whole profile shows its n rows
    return np.interp(np.arange(n + 1), rr, yy)


def heights(mask, pic, style, top_mat, ground_at, band_mat=None):
    """(yb, s) for a building's pixels; nan elsewhere, and in columns that
    never reach its roof (a wall the mask took in beside it)."""
    h, w = mask.shape
    roof = {top_mat} | ({band_mat} if style == "slate" and band_mat else set())
    ys, xs = np.nonzero(mask)
    cols = {}
    for x in np.unique(xs):
        col = ys[xs == x]
        cols[x] = (col.min(), col.max())
    # wall: rows from the foot up to the roof's material -- for slate, the
    # first ROOF_RUN rows of it (a cap's pale spots break any run, so a cap
    # or thatch stops at its first pixel, as drawn round its rim)
    need = ROOF_RUN if style == "slate" else 1
    walls = {}
    for x, (rt, rb) in list(cols.items()):
        r, run = rb, 0
        while r >= rt:
            run = run + 1 if CL.family(pic[r:r + 1, x], saturated_first=True) in roof else 0
            if run >= need:
                break
            r -= 1
        if r < rt and style == "slate":
            del cols[x]
            continue
        walls[x] = rb - r - (need - 1)
    if not walls:
        return np.full((h, w), np.nan), np.zeros((h, w))
    xs_ = sorted(walls)
    k = WALL_SMOOTH
    wall_s = {x: int(np.median([walls[xx] for xx in xs_[max(0, i - k // 2):i + k // 2 + 1]]))
              for i, x in enumerate(xs_)}
    yb = np.full((h, w), np.nan)
    s = np.zeros((h, w))
    for x, (rt, rb) in cols.items():
        g = ground_at(rb, x)
        hw = min(wall_s[x], rb - rt + 1)
        for r in range(rb, rb - hw, -1):
            yb[r, x], s[r, x] = g + (rb - r), 1.0
        n = (rb - hw) - rt + 1
        ys_ = profile(n, style)
        for i in range(n):
            r = rb - hw - i
            yb[r, x] = g + hw + ys_[i]
            s[r, x] = ys_[i + 1] - ys_[i]
    return yb, s
