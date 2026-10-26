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
FOOTPRINT = True        # objects from their collision footprint (else the run walk)
RUN_STEP = 20           # luminance: a front is this much darker than the top above it
SMOOTH_R = 4            # px: the smoothing window's reach
RUN_MIN = 3             # px: a shorter run is texture
GROUND_SHARE = 0.6      # of a region's pixels on ground cells, for it to be ground
# cells that lie flat at their level: ground, and water, holes and the
# undrawn void (each at its class height). Built as objects they stood up
# as walls (a river, the void round a castle hall)
FLAT = (RE.CLASS_GROUND, RE.CLASS_WATER, RE.CLASS_HOLE, RE.CLASS_VOID)


def ground_levels(rm):
    """Per cell, the ground's height (tilevox's measured levels, with the
    upper landing of every flight of steps raised)."""
    import tilevox as TV
    from pathlib import Path
    path = rm.meta.get("path")
    cls, H, _bl, _fl, _d = TV.room_heights(rm.raw, 0, path=Path(path) if path else None)
    return cls, np.asarray(H, float)


def solve(rm, labels, shade, box=None, pic=None, hidden=None):
    """(yb, s, ground): per pixel the height at its lower edge, its rise,
    and whether it is ground. box (x0, y0, x1, y1) in cells limits the
    work to a crop of the room (labels and shade are of that crop).
    Flights of steps (stairs.py) are ground with their own heights."""
    from . import stairs as ST
    cls, H = ground_levels(rm)
    x0c, y0c = (box[0], box[1]) if box else (0, 0)
    h, w = labels.shape
    cy = (np.arange(h) // 16 + y0c)[:, None].repeat(w, 1)
    cx = (np.arange(w) // 16 + x0c)[None, :].repeat(h, 0)
    cy = np.clip(cy, 0, cls.shape[0] - 1)
    cx = np.clip(cx, 0, cls.shape[1] - 1)
    on_ground = np.isin(cls[cy, cx], FLAT)
    lvl = H[cy, cx]
    # a region is ground when it lies mostly on ground cells
    n = labels.max() + 1
    tot = np.bincount(labels.ravel(), minlength=n)
    gnd = np.bincount(labels.ravel(), weights=on_ground.ravel().astype(float), minlength=n)
    region_ground = gnd >= GROUND_SHARE * np.maximum(tot, 1)
    ground = on_ground & region_ground[labels]
    st_m, st_yb, st_s = ST.fixed(rm, labels, box)
    ground = ground | st_m
    # what the overhead layer draws over is never seen: lie it flat and let
    # the overhead shape build the thing (a hedge's shadow under it read as
    # a front and stood up as a wall as long as the hedge)
    if hidden is not None:
        ground = ground | hidden
    rise =np.vectorize(lambda v: RISE.get(int(v), np.nan))(shade).astype(float)
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
            if st_m[y, x]:
                yb[y, x], s[y, x] = st_yb[y, x], st_s[y, x]
                below = st_yb[y, x] + st_s[y, x]
                continue
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
    if FOOTPRINT:
        yb, s = footprint_heights(labels, rise, ground, lvl, cls, cy, cx, yb, s)
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


SPLIT_FRONT = 4         # px: a shaded band this tall behind a top starts a new thing
SPLIT_DEPTH = 112       # px: a footprint deeper than this may hold several things
SPLIT_WIN = 12          # px: a thing's foot is agreed over this many columns each side
TOP_MIN = 8             # px: after at least this much top
FP_SMOOTH = 9           # px: front heights and back edges agree across this many columns


def _colmed(a, valid, k):
    out = a.astype(float).copy()
    idx = np.nonzero(valid)[0]
    for i in idx:
        lo, hi = max(0, i - k // 2), min(len(a), i + k // 2 + 1)
        sel = [j for j in range(lo, hi) if valid[j]]
        out[i] = np.median(a[sel])
    return out


def footprint_heights(labels, rise, ground, lvl, cls, cy, cx, yb, s):
    """An object's every pixel from its footprint. In each column a run of
    drawn object pixels from the foot (row rb, on the ground in front) up
    to its top (rt) is a front -- the shaded rows from the foot, upright,
    hf high -- then a top spread back over its depth to zn. The depth is
    the blocked cells under the column, from the foot to their north edge;
    but where the run, after a stretch of top, meets another shaded front
    of SPLIT_FRONT rows or more, a second thing stands behind the first
    (houses one behind another, the collision one block): the first ends
    at the second's foot. Front heights and back edges are smoothed across
    columns, thing by thing, so each is one shape."""
    h, w = labels.shape
    obj = ~ground & (labels >= 0)
    # pass 1: each column's run (from the foot up) and the rows where a new
    # thing's foot starts behind a top
    cols = {}                   # x -> [(rt0, rb0, zn_all, g, [split feet])]
    for x in range(w):
        y = h - 1
        while y >= 0:
            if not obj[y, x]:
                y -= 1
                continue
            rb0 = y
            while y >= 0 and obj[y, x]:
                y -= 1
            rt0 = y + 1
            cn = cy[rb0, x]
            while cn - 1 >= 0 and cls[cn - 1, cx[rb0, x]] not in FLAT:
                cn -= 1
            zn_all = max((cn - cy[0, 0]) * 16, rt0)
            g = lvl[min(rb0 + 1, h - 1), x]
            deep = (rb0 + 1 - zn_all) > SPLIT_DEPTH
            feet = []
            r = rb0
            while deep and r >= rt0:
                f = r
                while f >= rt0 and rise[f, x] >= 0.75:
                    f -= 1
                t = f
                found = None
                while t >= rt0:
                    if rise[t, x] >= 0.75:
                        k_ = t
                        while k_ >= rt0 and rise[k_, x] >= 0.75:
                            k_ -= 1
                        if t - k_ >= SPLIT_FRONT and f - t >= TOP_MIN:
                            found = t
                            break
                        t = k_
                        continue
                    t -= 1
                if found is None:
                    break
                feet.append(found)
                r = found
            cols.setdefault(x, []).append((rt0, rb0, zn_all, g, feet))
    # pass 2: a thing's foot is one line across it -- each column takes the
    # median of its neighbours' k-th split, when most of them have one
    xs_ = sorted(cols)
    runs = []                   # [x, k, rt, rb, hf, zn, g]
    for x in xs_:
        for (rt0, rb0, zn_all, g, feet) in cols[x]:
            nb = [c for xx in range(x - SPLIT_WIN, x + SPLIT_WIN + 1) if xx in cols
                  for c in cols[xx] if c[0] <= rb0 and c[1] >= rt0]
            agreed = []
            for k in range(max([len(c[4]) for c in nb] + [0])):
                have = [c[4][k] for c in nb if len(c[4]) > k]
                if len(have) * 2 < len(nb):
                    break
                ft = int(np.median(have))
                if not (rt0 + TOP_MIN <= ft <= (agreed[-1] if agreed else rb0) - TOP_MIN):
                    break
                agreed.append(ft)
            bounds = [rb0] + agreed
            for k in range(len(bounds)):
                rb = bounds[k]
                rt = bounds[k + 1] + 1 if k + 1 < len(bounds) else rt0
                f = rb
                while f >= rt and rise[f, x] >= 0.75:
                    f -= 1
                zn = bounds[k + 1] + 1 if k + 1 < len(bounds) else zn_all
                runs.append([x, k, rt, rb, rb - f, max(zn, rt), g])
    if not runs:
        return yb, s
    R = np.array(runs, float)
    hf_s = np.zeros(len(R))
    zn_s = np.zeros(len(R))
    for k in np.unique(R[:, 1]):
        idx = np.nonzero(R[:, 1] == k)[0]
        idx = idx[np.argsort(R[idx, 0])]
        hf_s[idx] = _colmed(R[idx, 4], np.ones(len(idx), bool), FP_SMOOTH)
        zn_s[idx] = _colmed(R[idx, 5], np.ones(len(idx), bool), FP_SMOOTH)
    for (x, k, rt, rb, _hf, _zn, g), hf, zn in zip(R, hf_s, zn_s):
        x, rt, rb = int(x), int(rt), int(rb)
        hf = int(min(hf, rb - rt + 1))
        zs = rb + 1
        zn = min(max(zn, rt), zs - 1)
        rf = rb - hf
        for r in range(rb, rf, -1):
            yb[r, x], s[r, x] = g + (rb - r), 1.0
        n = max(1, rf - rt + 1)
        ztop = zs
        for i, r in enumerate(range(rf, rt - 1, -1)):
            z1 = zs + (zn - zs) * (i + 1) / n
            y_lo = ztop - (r + 1) - g
            y_hi = z1 - r - g
            yb[r, x] = g + max(0.0, y_lo)
            s[r, x] = max(0.0, y_hi) - max(0.0, y_lo)
            ztop = z1
    return yb, s
