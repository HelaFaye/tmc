"""Flights of steps, built step by step from their drawing.

A flight is found by the game's collision (find_stairs: cells whose
action byte is a slope) and its two landings by tilevox.stair_levels,
which raises the upper one. Then, from the drawing:

  width   the drawn steps are often wider than their slope cells (the
          town's west ledge: one cell of slope, steps nearly two wide).
          A column belongs to the flight when its rows go light and dark
          as the flight's do (correlation >= COL_MATCH), contiguous with
          the slope cells.
  steps   find_stairs.measure_steps reads each step's band: its lit rows
          (tread) and dark rows (riser). Built from the bottom up: the
          riser upright, then the tread level on top of it.

From the game's camera any split of a band's rows into tread and riser
draws the same (the 45-degree rule: drawn rows = height + depth), so the
risers are scaled together to climb exactly from the lower landing to the
upper one, each step keeping at least one row of tread.

Only flights whose two sides are different levels are built; a flight
between one level (reachable round it) stays as drawn relief.
"""
from pathlib import Path

import numpy as np

COL_MATCH = 0.6         # a column's profile against the flight's, to be in it
COL_REACH = 24          # px: how far past the slope cells a flight may be drawn
TREAD_MIN = 2           # rows: every step keeps at least this much tread
LEAN_MAX = 1.5          # a riser row's rise at most (over 1, it leans forward)


def flights(rm):
    """The room's flights of steps: dicts of the slope cells' rect, the
    direction it climbs (up: n, e or w), base and top levels, the measured
    step edges and bands (absolute picture rows, or columns for e/w), and
    the cliff cells beside it.

    A flight between two levels climbs from one to the other. A north-south
    flight between one level -- the ground on its two sides meets round
    the end of a bank, as in Hyrule Field -- climbs a ridge: as high as its
    own risers are drawn (the 45-degree rule), its top running back flat
    over the bank, which drops to the same level behind."""
    import tilevox as TV
    path = rm.meta.get("path")
    if path is None:
        return []
    cls, H, _bl, fl, _d = TV.room_heights(rm.raw, 0, path=Path(path))
    from . import regions as RG
    from .room import RE
    lum = RG.luminance(rm.picture)
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    import find_stairs as FS
    meas = {it["rect"]: it["measure"] for it in FS.find(Path(path))
            if it["kind"] == "steps" and it["layer"] == 0 and it.get("measure")}
    rows_, cols_ = H.shape
    out = []
    for f in fl:
        m = meas.get(tuple(f["rect"]))
        if not m or len(m["edges"]) < 2:
            continue
        x0, y0, x1, y1 = f["rect"]
        up, base, top = f.get("up"), f.get("base"), f.get("top")
        ridge = False
        cliff = list(f.get("cliff") or [])
        if up is None:
            if f["rise"] != "ns" or y1 + 1 >= rows_:
                continue
            # the bank beside the flight, in its rows: blocked cells running
            # east and west from it
            cliff = []
            for y in range(y0, y1 + 1):
                for step, x in ((-1, x0 - 1), (1, x1 + 1)):
                    while 0 <= x < cols_ and blocked[y, x]:
                        cliff.append((y, x))
                        x += step
            if not cliff:
                continue
            # its height: the face rows -- darker than halfway from the lip
            # to the face, across the bank's own columns (the 45-degree rule)
            xs = sorted({x for _y, x in cliff})
            pcols = [X for x in xs for X in range(x * 16, x * 16 + 16)]
            prof = np.median(lum[y0 * 16:(y1 + 1) * 16][:, pcols], axis=1)
            face = int((prof < (prof.max() + prof.min()) / 2).sum())
            if face < TV.STAIR_RISE[0]:
                continue
            base = int(np.median(H[y1 + 1, x0:x1 + 1]))
            top = base + min(face, TV.STAIR_RISE[1])
            up, ridge = "n", True
        if top is None or top <= base:
            continue
        out.append(dict(rect=(x0, y0, x1, y1), up=up, base=int(base), top=int(top),
                        ridge=ridge, edges=list(m["edges"]), bands=list(m["bands"]),
                        cliff=cliff))
    return out


def ew_levels(fl):
    """Per column of an east-west flight (west first): its level. Each
    drawn band is one step, flat; the flight climbs from the base on its
    low side to the top on its up side in len(bands) + 1 equal risers."""
    bands = fl["bands"]
    n = len(bands)
    out = []
    for i, (a, b, _t, _f) in enumerate(bands):
        k = i if fl["up"] == "e" else n - 1 - i
        lvl = fl["base"] + (fl["top"] - fl["base"]) * (k + 1) / (n + 1)
        out += [(X, lvl) for X in range(a, b)]
    return out


def _columns(lum, fl):
    """The picture columns the flight is drawn across."""
    x0, _y0, x1, _y1 = fl["rect"]
    r0, r1 = fl["edges"][0], fl["edges"][-1]
    h, w = lum.shape
    r0, r1 = max(0, r0), min(h, r1)
    if r1 - r0 < 3:
        return []
    core = range(x0 * 16, min(w, (x1 + 1) * 16))
    prof = np.median(lum[r0:r1, list(core)], axis=1)
    prof = prof - prof.mean()
    pn = np.linalg.norm(prof) or 1.0

    def match(x):
        c = lum[r0:r1, x] - lum[r0:r1, x].mean()
        return float(c @ prof) / (pn * (np.linalg.norm(c) or 1.0))

    ok = {x for x in range(max(0, x0 * 16 - COL_REACH), min(w, (x1 + 1) * 16 + COL_REACH))
          if match(x) >= COL_MATCH}
    mid = (x0 * 16 + min(w, (x1 + 1) * 16)) // 2
    if mid not in ok:
        near = sorted(ok, key=lambda x: abs(x - mid))
        if not near or abs(near[0] - mid) > 8:
            return []
        mid = near[0]
    a = b = mid
    while a - 1 in ok:
        a -= 1
    while b + 1 in ok:
        b += 1
    return list(range(a, b + 1))


def profile(fl):
    """Per row of the flight (top first): (yb, s) -- the height at the
    row's lower edge and its rise (1 riser, 0 tread)."""
    bands = fl["bands"]
    rise = fl["top"] - fl["base"]
    n = [b[1] - b[0] for b in bands]
    dark = np.array([max(b[3], 0) for b in bands], float)
    w = dark if dark.sum() > 0 else np.array(n, float)
    # each band's riser rows, proportional to its drawn riser, summing to
    # the rise (largest remainder), keeping a row of tread where it can
    want = w / w.sum() * rise
    r = np.floor(want).astype(int)
    for i in np.argsort(-(want - r))[:rise - r.sum()]:
        r[i] += 1
    # a step keeps TREAD_MIN rows of tread; where the drawing is too short
    # for that (the art cheats: the town's west steps climb 30 px in 29
    # rows), the risers lean forward to make up the rise, up to LEAN_MAX
    cap = np.array([max(k - TREAD_MIN, 1) for k in n])
    r = np.minimum(r, cap)
    over = rise - int(r.sum())
    for i in np.argsort(-(cap - r)):        # first into steps with room
        if over <= 0:
            break
        add = min(over, int(cap[i] - r[i]))
        r[i] += add
        over -= add
    lean = min(LEAN_MAX, rise / max(int(r.sum()), 1)) if over > 0 else 1.0
    rows = []                               # bottom up
    hgt = float(fl["base"])
    for (a, b, _t, _f), k in zip(reversed(bands), reversed(r)):
        for i in range(b - a):
            if i < k:
                rows.append((hgt, lean))
                hgt += lean
            else:
                rows.append((hgt, 0.0))
    return list(reversed(rows)), bands[0][0]


def fixed(rm, pic, box=None):
    """(mask, yb, s) over the crop: the pixels the flights fix, and their
    heights. box (x0, y0, x1, y1) in cells, as heights.solve takes it."""
    from . import regions as RG
    h, w = pic.shape[:2]
    mask = np.zeros((h, w), bool)
    yb = np.zeros((h, w))
    s = np.zeros((h, w))
    ox, oy = (box[0] * 16, box[1] * 16) if box else (0, 0)
    lum = RG.luminance(rm.picture)
    fls = flights(rm)
    steps_at = np.zeros(rm.picture.shape[:2], bool)
    for fl in fls:
        if fl["up"] != "n":
            # east-west: each band's columns, level, across the slope rows
            _x0, y0, _x1, y1 = fl["rect"]
            fl["_cols"] = []
            for X, lvl in ew_levels(fl):
                for R in range(y0 * 16, (y1 + 1) * 16):
                    Rc, Xc = R - oy, X - ox
                    if 0 <= Rc < h and 0 <= Xc < w:
                        mask[Rc, Xc] = True
                        yb[Rc, Xc], s[Rc, Xc] = lvl, 0.0
            continue
        fl["_cols"] = _columns(lum, fl)
        _x0, y0, _x1, y1 = fl["rect"]
        if fl["_cols"]:
            steps_at[y0 * 16:(y1 + 1) * 16, fl["_cols"][0]:fl["_cols"][-1] + 1] = True
    # the cliff the flights climb (tilevox lists its cells): upright from
    # the lower level at its drawn foot, a pixel a row, to the upper level,
    # then level -- exact by the 45-degree rule, whatever its shading says
    done = set()
    for fl in fls:
        bycol = {}
        for cy_, cx_ in fl["cliff"]:
            bycol.setdefault(cx_, []).append(cy_)
        for cx_, cys in bycol.items():
            if (cx_, min(cys)) in done:
                continue
            done.add((cx_, min(cys)))
            foot, lip = (max(cys) + 1) * 16 - 1, min(cys) * 16
            for X in range(cx_ * 16, (cx_ + 1) * 16):
                Xc = X - ox
                if not 0 <= Xc < w:
                    continue
                for R in range(foot, lip - 1, -1):
                    Rc = R - oy
                    if not 0 <= Rc < h or steps_at[R, X]:
                        continue
                    hgt = fl["base"] + (foot - R)
                    mask[Rc, Xc] = True
                    yb[Rc, Xc], s[Rc, Xc] = (hgt, 1.0) if hgt < fl["top"] else (fl["top"], 0.0)
    for fl in fls:
        cols = fl["_cols"]
        if not cols:
            continue
        rows, r0 = profile(fl)
        # the slope cells' rows beyond the drawn steps are the landings
        _x0, y0, _x1, y1 = fl["rect"]
        rows = ([(float(fl["top"]), 0.0)] * max(0, r0 - y0 * 16) + rows +
                [(float(fl["base"]), 0.0)] * max(0, (y1 + 1) * 16 - (r0 + len(rows))))
        r0 = min(r0, y0 * 16)
        for i, (b, ri) in enumerate(rows):
            R = r0 + i - oy
            if not 0 <= R < h:
                continue
            for X in cols:
                X -= ox
                if 0 <= X < w:
                    mask[R, X] = True
                    yb[R, X], s[R, X] = b, ri
        fl["cols"] = (cols[0], cols[-1])
    return mask, yb, s
