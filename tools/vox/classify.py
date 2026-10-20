"""Every object in a scene, labelled from its drawing.

An object is a connected run of what is not ground -- solid cells by
collision, and what the overhead layer draws over them -- split along
the drawn dark outline (overhead.py splits the same way). Its labels:

  kind       building (its footprint holds a door, or it is large) or object
  style      buildings: cap (a domed, smooth-edged top), thatch (yellow,
             streaked), slate (a roof with a flat light patch), block (stone)
  material   colour family of its top and of its front
  height     its drawn front (shaded rows from the foot) and its drawn
             height over that, in px
  shape      mound (round, low), stand (narrow and tall), box (fills its
             box), slab (thin and wide), arch (open underneath)

Labels and coordinates only: classify writes no pixels.
"""
import json
import numpy as np

from . import regions as RG
from .room import RE

FAMILIES = [  # (name, hue lo, hue hi) for saturated colours
    ("red", 345, 15), ("orange", 15, 40), ("yellow", 40, 70), ("green", 70, 170),
    ("cyan", 170, 200), ("blue", 200, 250), ("purple", 250, 300), ("pink", 300, 345)]
BUILDING_PX = 48 * 48   # px: a footprint drawn this large is a building
SAT_SHARE = 0.3         # of a top's pixels coloured: its colour, not its grey
STRAW_STEP = 10         # luminance: a straw is this much darker than either side
STRAW_SHARE = 0.04      # of a top's pixels on straws: thatch
GROW_SIDE = 8           # px: a piece joins a building only within its columns, this much over
FRONT_ROWS = 6          # px: a front's material from its bottom rows
GROW_MIN = 20           # px: a piece a building overlaps this much may join it
MIN_PX = 40             # px: smaller is detail
STEP_SCRAP = 96         # px: a piece this small touching a flight is part of it


def family(px, saturated_first=False):
    """The colour family most of these pixels are in."""
    if not len(px):
        return "none"
    hue, sat = RG.hsv(px[None])
    hue, sat = hue[0], sat[0]
    lum = RG.luminance(px)
    names = []
    for h, s_, l in zip(hue, sat, lum):
        if l < RG.OUTLINE:
            continue
        if s_ < 0.18:
            names.append("white" if l > 200 else "grey")
            continue
        if 15 <= h < 45 and l < 140 and s_ < 0.6:
            names.append("brown")
            continue
        for n, lo, hi in FAMILIES:
            if (lo <= h < hi) if lo < hi else (h >= lo or h < hi):
                names.append(n)
                break
    if not names:
        return "dark"
    if saturated_first:
        sat_n = [n for n in names if n not in ("white", "grey")]
        if len(sat_n) >= SAT_SHARE * len(names):
            names = sat_n
    v, c = np.unique(names, return_counts=True)
    return str(v[c.argmax()])


def objects(rm, box, pic, overhead_mask):
    """[(pixels mask, bbox)] for the scene's objects."""
    import tilevox as TV
    cls, _H, _bl, _fl, doors = TV.room_heights(rm.raw, 0)
    x0, y0, x1, y1 = box
    h, w = pic.shape[:2]
    cells = np.isin(cls[y0:y1, x0:x1], [RE.CLASS_WALL, RE.CLASS_LEDGE])
    solid = np.kron(cells, np.ones((16, 16), bool))[:h, :w] | overhead_mask
    body = solid & (RG.luminance(pic) >= RG.OUTLINE)
    # flights of steps are one thing each, as wide as they are drawn
    from . import stairs as ST
    steps = ST.drawn(rm, pic.shape, box)
    for sm, _fl in steps:
        body &= ~sm
    out = []
    lab0, n0 = RE._label(body)
    fam0 = {}
    claimed = np.zeros((h, w), bool)
    # buildings first, as the old stage finds them (anchored on their
    # doors, which it does reliably): their roof cells and their front
    # down to the foot, in their own columns
    for b in buildings(rm, cls, doors):
        m = np.zeros((h, w), bool)
        for (cy_, cx_) in b["roof"]:
            m[(cy_ - y0) * 16:(cy_ - y0 + 1) * 16, (cx_ - x0) * 16:(cx_ - x0 + 1) * 16] = True
        top = min(cy_ for cy_, _c in b["roof"])
        m[(top - y0) * 16:(b["foot"] - y0) * 16, (b["c0"] - x0) * 16:(b["c1"] + 1 - x0) * 16] = True
        m = m & solid & (RG.luminance(pic) >= RG.OUTLINE)
        if m.sum() < MIN_PX:
            continue
        # the rest of the building where the finder took only part of it:
        # outline-bounded pieces it overlaps with the same top material
        bys, bxs = np.nonzero(m)
        bmid = np.percentile(bys, 60)
        btop = family(pic[bys[bys < bmid], bxs[bys < bmid]], saturated_first=True)
        ids, cnt = np.unique(lab0[m], return_counts=True)
        for j, c in zip(ids, cnt):
            if j == 0 or c < GROW_MIN:
                continue
            if j not in fam0:
                jy, jx = np.nonzero(lab0 == j)
                jmid = np.percentile(jy, 60)
                fam0[j] = family(pic[jy[jy < jmid], jx[jy < jmid]], saturated_first=True)
            jx = np.nonzero((lab0 == j).any(axis=0))[0]
            if fam0[j] == btop and jx.min() >= bxs.min() - GROW_SIDE and jx.max() <= bxs.max() + GROW_SIDE:
                m = m | (lab0 == j)
        m = m & ~claimed
        claimed |= m
        ys, xs = np.nonzero(m)
        out.append((m, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), True))
        body = body & ~m
    lab, n = RE._label(body)
    for j in range(1, n + 1):
        m = lab == j
        # a scrap left beside a flight (its drawn edge rows) is the flight's
        near = np.pad(m, 1)
        near = near[:-2, 1:-1] | near[2:, 1:-1] | near[1:-1, :-2] | near[1:-1, 2:]
        k = next((i for i, (sm, _f) in enumerate(steps) if (near & sm).any()), None)
        if k is not None and m.sum() < STEP_SCRAP:
            steps[k] = (steps[k][0] | m, steps[k][1])
            continue
        if m.sum() < MIN_PX:
            continue
        ys, xs = np.nonzero(m)
        out.append((m, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), False))
    for sm, _fl in steps:
        ys, xs = np.nonzero(sm)
        out.append((sm, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), "steps"))
    door_px = [((d["cx"] - x0) * 16 + 8, (d["cy"] - y0) * 16 + 8) for d in doors]
    return out, door_px


def buildings(rm, cls, doors):
    import tilevox as TV
    r = rm.raw
    if len(r.layers) < 2 or not r.layers[1]["present"]:
        return []
    _c, H, _b, _f, _d = TV.room_heights(r, 0)
    ov = TV.room_overlay(r, H, canopy=True)
    if ov is None or ov[2] is None:
        return []
    return TV.find_buildings(r, cls, np.array(H), doors, ov[1], ov[5])[1]


def label(m, bbox, pic, door_px, is_building=False):
    """is_building: True, False, or "steps" (a flight, stairs.drawn)."""
    x0, y0, x1, y1 = bbox
    if is_building == "steps":
        ys, xs = np.nonzero(m)
        return dict(bbox=[int(v) for v in bbox], kind="object", style=None, shape="steps",
                    streak=None, top=family(pic[ys, xs]), front=family(pic[ys, xs]),
                    band=None, front_h=0, over_h=0, px=int(m.sum()), door=False)
    w, h = x1 - x0, y1 - y0
    px = pic[m]
    lum = RG.luminance(pic)
    # the front: shaded rows up from the foot, per column, median
    fronts, tops = [], []
    for x in range(x0, x1):
        col = np.nonzero(m[:, x])[0]
        if not len(col):
            continue
        rb, rt = col.max(), col.min()
        run = lum[rt:rb + 1, x]
        med = np.median(run)
        f = 0
        while f < len(run) and run[len(run) - 1 - f] < med:
            f += 1
        fronts.append(f)
        tops.append(rb - rt + 1 - f)
    hf = int(np.median(fronts)) if fronts else 0
    ht = int(np.median(tops)) if tops else 0
    # top and front material
    ys, xs = np.nonzero(m)
    mid = np.percentile(ys, 60)
    top_mat = family(pic[ys[ys < mid], xs[ys < mid]], saturated_first=True)
    # the front: each column's bottom FRONT_ROWS rows (a cap's pale rim)
    fy, fx = [], []
    for x in np.unique(xs):
        col = ys[xs == x]
        sel = col[col > col.max() - FRONT_ROWS]
        fy.extend(sel.tolist())
        fx.extend([x] * len(sel))
    front_mat = family(pic[np.array(fy, int), np.array(fx, int)])
    # the band between top and front (a thatch's fringe, over its stone)
    bsel = (ys >= mid) & ~np.isin(np.arange(len(ys)), [])
    band_px = []
    for x in np.unique(xs):
        col = ys[xs == x]
        lo = col.max() - FRONT_ROWS
        sel = col[(col >= mid) & (col <= lo)]
        band_px.extend([(y_, x) for y_ in sel])
    band_mat = family(pic[tuple(np.array(band_px).T)]) if band_px else front_mat
    lower_mat = family(pic[ys[ys >= mid], xs[ys >= mid]])
    fill = m.sum() / float(max(1, w * h))
    has_door = any(x0 <= dx < x1 and y0 <= dy < y1 for dx, dy in door_px)
    big = m.sum() >= BUILDING_PX
    # open underneath: a gap in the bottom quarter's middle
    bottom = m[y1 - max(2, h // 4):y1, x0:x1]
    mid_cols = bottom[:, w // 3:2 * w // 3]
    open_under = mid_cols.size and mid_cols.mean() < 0.2 and bottom.mean() > 0.2
    if open_under:
        shape = "arch"
    elif h > 2 * w:
        shape = "stand"
    elif w > 3 * h:
        shape = "slab"
    elif fill > 0.85:
        shape = "box"
    else:
        shape = "mound"
    # leaf top over leaf front is a tree; a green cap stands on a pale rim
    kind = "building" if is_building and not (top_mat == "green" and front_mat == "green") else "object"
    if is_building and shape == "arch":
        shape = "box"
    style = None
    if kind == "building":
        # a light flat patch on top: slate; yellow streaked: thatch;
        # smooth round top: cap; grey: block
        # vertical streaks (a thatch's straw) change brightness far more
        # along a row than down a column
        tm = m.copy()
        tm[int(mid):] = False
        dx = np.abs(np.diff(lum, axis=1))[tm[:, 1:] & tm[:, :-1]].mean() if tm.any() else 0
        dy = np.abs(np.diff(lum, axis=0))[tm[1:] & tm[:-1]].mean() if tm.any() else 0
        # straw: a pixel darker than both side neighbours, the pixel above
        # it too -- thin dark lines running down
        L_ = lum
        dark = np.zeros_like(tm)
        dark[:, 1:-1] = (L_[:, 1:-1] < L_[:, :-2] - STRAW_STEP) & (L_[:, 1:-1] < L_[:, 2:] - STRAW_STEP)
        straw = dark & np.vstack([np.zeros((1, dark.shape[1]), bool), dark[:-1]])
        streak = round(float(straw[tm].mean()) if tm.any() else 0.0, 3)
        streaky = streak > STRAW_SHARE
        # thatch is yellow over a fringe of another colour; a yellow
        # mushroom cap is yellow over yellow (texture did not separate
        # them: see straw, recorded for review)
        if top_mat == "yellow" and ({band_mat, lower_mat} & {"orange", "brown", "red"}):
            style = "thatch"
        elif top_mat in ("blue", "purple", "cyan"):
            style = "slate"
        elif top_mat in ("grey", "white", "dark"):
            style = "block"
        else:
            style = "cap"
    if kind != "building":
        streak = None
    return dict(bbox=[int(v) for v in bbox], kind=kind, style=style, shape=shape, streak=locals().get("streak"),
                top=top_mat, front=front_mat, band=band_mat, front_h=hf, over_h=ht,
                px=int(m.sum()), door=bool(has_door))


def _merge(objs, pic):
    """One building where the finder reported several over one house:
    buildings with the same top material whose boxes overlap."""
    blds = [o for o in objs if o[2] is True]
    rest = [o for o in objs if o[2] is not True]
    tops = []
    for m, _bb, _ in blds:
        ys, xs = np.nonzero(m)
        mid = np.percentile(ys, 60)
        tops.append(family(pic[ys[ys < mid], xs[ys < mid]], saturated_first=True))
    merged = True
    while merged:
        merged = False
        for i in range(len(blds)):
            for j in range(i + 1, len(blds)):
                a, b = blds[i][1], blds[j][1]
                over = min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
                if over and tops[i] == tops[j]:
                    m = blds[i][0] | blds[j][0]
                    ys, xs = np.nonzero(m)
                    blds[i] = (m, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), True)
                    del blds[j], tops[j]
                    merged = True
                    break
            if merged:
                break
    return blds + rest


AWNING_SIDE = 8         # px: an awning's box ends this near its building's
AWNING_MIN = 12         # px: an awning is at least this wide and deep


def awnings(objs, labels):
    """Mark awnings: an object beside a building, of its roof's material,
    whose box overlaps the building's rows -- a canopy hung off its wall
    (the mushroom shop's, over its stall). Sets shape "awning" and
    "attached" (the building's index); returns their indices."""
    out = []
    for i, L in enumerate(labels):
        if L["kind"] != "object" or L["shape"] == "steps":
            continue
        a = L["bbox"]
        if a[2] - a[0] < AWNING_MIN or a[3] - a[1] < AWNING_MIN:
            continue
        for j, B in enumerate(labels):
            if B["kind"] != "building" or B["top"] != L["top"]:
                continue
            b = B["bbox"]
            beside = abs(a[2] - b[0]) <= AWNING_SIDE or abs(a[0] - b[2]) <= AWNING_SIDE
            rows = min(a[3], b[3]) > max(a[1], b[1])
            if beside and rows and a[3] < b[3]:
                L["shape"], L["attached"] = "awning", j
                out.append(i)
                break
    return out


def classify(rm, box, pic, overhead_mask):
    objs, door_px = objects(rm, box, pic, overhead_mask)
    objs = _merge(objs, pic)
    labels = [label(m, bb, pic, door_px, isb) for m, bb, isb in objs]
    awnings(objs, labels)
    return labels


def sheet(pic, labels, path, zoom=3):
    from PIL import Image, ImageDraw
    im = Image.fromarray(pic).resize((pic.shape[1] * zoom, pic.shape[0] * zoom), Image.NEAREST)
    d = ImageDraw.Draw(im)
    col = {"building": (255, 80, 200), "object": (80, 220, 255)}
    for L in labels:
        x0, y0, x1, y1 = [v * zoom for v in L["bbox"]]
        c = col[L["kind"]]
        d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=c, width=2)
        t = f"{L['style'] or L['shape']} {L['top']}/{L['front']} {L['front_h']}+{L['over_h']}"
        d.rectangle([x0, y0, x0 + 7 * len(t), y0 + 12], fill=(0, 0, 0))
        d.text((x0 + 2, y0 + 1), t, fill=c)
    im.save(path)


def write(labels, path):
    with open(path, "w") as f:
        json.dump(labels, f, indent=1)
