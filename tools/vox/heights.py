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
RUN_STEP = 20           # luminance: a front is this much darker than the top above it
SMOOTH_R = 4            # px: the smoothing window's reach
RUN_MIN = 3             # px: a shorter run is texture
GROUND_SHARE = 0.6      # of a region's pixels on ground cells, for it to be ground


def ground_levels(rm):
    """Per cell, the ground's height (tilevox's measured levels)."""
    import tilevox as TV
    cls, H, _bl, _fl, _d = TV.room_heights(rm.raw, 0)
    return cls, np.asarray(H, float)


def solve(rm, labels, shade, box=None, pic=None):
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
    # each region is one surface: a front when it is darker than what is
    # drawn just above it (across its upper edge, outline skipped), else a
    # top. Decided per region, so neighbouring columns agree.
    lum = RG.luminance(pic if pic is not None else rm_pic(rm, box, labels.shape))
    n = labels.max() + 1
    acc = np.zeros(n)
    cnt = np.zeros(n)
    for x in range(w):
        for y in range(1, h):
            j = labels[y, x]
            if j == 0 or ground[y, x]:
                continue
            k = y - 1
            while k >= 0 and labels[k, x] == 0 and y - k <= 3:
                k -= 1
            if k < 0 or labels[k, x] in (0, j):
                continue
            acc[j] += lum[k, x] - lum[y, x]
            cnt[j] += 1
    mean_lum = np.bincount(labels.ravel(), weights=lum.ravel(), minlength=n) / np.maximum(tot, 1)
    darker = acc / np.maximum(cnt, 1)
    front = (cnt >= 2) & (darker > RUN_STEP)
    obj = ~ground & (labels > 0)
    rise = np.where(obj, np.where(front[labels], 1.0, 0.0), rise)
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
    # any heights draw the same from the game's camera, so an object's are
    # free to be smoothed: a median over its own pixels near each one takes
    # the texture's spikes out and keeps its steps
    obj = ~ground
    top = yb + s
    R_ = SMOOTH_R
    P = np.pad(np.where(obj, top, np.nan), R_, constant_values=np.nan)
    stack = [P[R_ + dy:R_ + dy + h, R_ + dx:R_ + dx + w]
             for dy in range(-R_, R_ + 1) for dx in range(-R_, R_ + 1)]
    with np.errstate(all="ignore"):
        med = np.nanmedian(np.stack(stack), axis=0)
    top2 = np.where(obj & ~np.isnan(med), med, top)
    # keep each pixel's rise, move it to its new top; never below its ground
    yb = np.where(obj, np.maximum(top2 - s, 0), yb)
    return yb, s, ground


def rm_pic(rm, box, shape):
    if box is None:
        return rm.picture
    x0, y0, _x1, _y1 = box
    return rm.picture[y0 * 16:y0 * 16 + shape[0], x0 * 16:x0 * 16 + shape[1]]
