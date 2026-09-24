#!/usr/bin/env python3
"""
shapefit.py — fit a voxel SOLID to one drawn object, instead of extruding it.

Why this exists
---------------
Extruding a tile's silhouette gives a prism: correct from directly above,
obviously wrong from anywhere else. A tree stump is the clean test case. Its
drawing is a rounded mass of cut rings seen from straight down; the sides are
never drawn at all. Extruded, it is a brown box with a picture of rings on the
lid. What it should be is a short cylinder with a slightly flared base and a
cut face on top -- which requires constructing the sides the artist omitted.

That construction is an invention, and the honest position is to say which
part is measured and which is invented:

    measured   the plan cross-section (the mask), and the object's footprint
    invented   the height, and the side profile

So the modes below differ only in what they invent, and each names its
assumption. Nothing here guesses a height from shading -- that was measured
and found absent (direction concentration R = 0.019 across 691k samples).

Modes
-----
  slab      extrude the mask. Truthful and flat; the current behaviour.
  taper     extrude with the cross-section shrinking toward the top. Gives the
            oblique sides a stump, a pot or a bollard reads with, and stays a
            single measured mask plus one number.
  revolve   treat each mask ROW's span as a circle's diameter and give each
            pixel a depth chord. Correct for objects drawn from the SIDE, and
            wrong for anything drawn from above -- a top-down disc revolved
            about the wrong axis becomes a sphere.

Usage
-----
  python3 shapefit.py fit room_04_00.tmcr --cells 33,56,36,59 \\
      --mode taper --height 10 --out stump.obj
  python3 shapefit.py fit ... --mode slab --out flat.obj     # for comparison
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import re

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_explore as RE
import shading as SH
import viewangle as VA


def object_mask(mat, art, x0, y0, x1, y1, ground_frac=0.02, margin=3):
    """Mask of the OBJECT inside a cell rect, with the surrounding ground cut.

    The object is whatever is not the background, and the background is
    identified the way DramaticShape identifies it: not by colour, but by
    reaching the border. A material that touches the rect's edge over a large
    share of it is the ground the object stands on, not the object.
    """
    sub = mat[y0:y1, x0:x1]
    h, w = sub.shape
    # A one-pixel border with a 30% threshold cannot find grass, because grass
    # is dithered from three or four palette entries and no single one of them
    # holds 30% of an edge. On the Minish Woods stump that left every grass
    # entry counted as object and cut the stump down to 13 pixels. Read a band
    # instead, and take any entry that shows up in it as ground: something
    # reaching the edge of the rect is what the object is standing on.
    band = np.zeros((h, w), bool)
    m_ = max(1, int(margin))
    band[:m_] = band[-m_:] = True
    band[:, :m_] = band[:, -m_:] = True
    counts = Counter(int(v) for v in sub[band] if v >= 0)
    if not counts:
        return np.zeros((h, w), bool), set()
    edge_total = max(1, sum(counts.values()))
    bg = {m for m, n in counts.items() if n / edge_total >= ground_frac}
    if not bg:
        bg = {counts.most_common(1)[0][0]}
    mask = np.ones((h, w), bool)
    for m in bg:
        mask &= (sub != m)
    mask &= (sub >= 0)
    return mask, bg


def drop_border_touching(mask):
    """Remove every component that reaches the rect edge.

    The object being fitted sits inside the rect; anything running off the
    edge is the scenery it stands in. Without this the grass tufts scattered
    around a stump survive the background cut -- they are a different material
    from the lawn, so they are not "background" -- get welded to the stump by
    the connectivity pass, and extrude into blocks that dwarf it. That is
    exactly what the first fit produced.
    """
    h, w = mask.shape
    seen = np.zeros_like(mask)
    stack = []
    for x in range(w):
        for y in (0, h - 1):
            if mask[y, x] and not seen[y, x]:
                seen[y, x] = True
                stack.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if mask[y, x] and not seen[y, x]:
                seen[y, x] = True
                stack.append((y, x))
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return mask & ~seen


def largest_blob(mask):
    """Keep the biggest 4-connected component; drop scattered speckle.

    Grass tufts around a stump touch its outline and would otherwise be welded
    into the solid, giving it a ring of spikes.
    """
    h, w = mask.shape
    seen = np.zeros_like(mask)
    best = None
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            comp = []
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if best is None or len(comp) > len(best):
                best = comp
    out = np.zeros_like(mask)
    for y, x in best or []:
        out[y, x] = True
    return out


def erode(mask, n=1):
    m = mask.copy()
    for _ in range(n):
        p = np.zeros_like(m)
        p[1:-1, 1:-1] = (m[1:-1, 1:-1] & m[:-2, 1:-1] & m[2:, 1:-1]
                         & m[1:-1, :-2] & m[1:-1, 2:])
        m = p
    return m


def build_slab(mask, height):
    return {(x, y, z) for y in range(height)
            for z, x in zip(*np.where(mask))}


def build_taper(mask, height, inset_every=3):
    """Extrude, shrinking the cross-section every `inset_every` voxels of rise.

    One number buys the oblique sides. The mask is eroded rather than scaled so
    the silhouette keeps its drawn shape while pulling in -- scaling a
    pixel-art outline resamples it into mush.
    """
    vox = set()
    cur = mask.copy()
    for y in range(height):
        if y and y % max(1, inset_every) == 0:
            e = erode(cur, 1)
            if e.any():
                cur = e
        for z, x in zip(*np.where(cur)):
            vox.add((int(x), y, int(z)))
    return vox


def build_revolve(mask, squash=100, cap_rows=0, base_rows=0):
    """Revolve a SIDE-drawn mask: each row's span is a circle's diameter."""
    h, w = mask.shape
    vox = set()
    rows = range(cap_rows, h - base_rows)
    for y in rows:
        xs = np.where(mask[y])[0]
        if not len(xs):
            continue
        lo, hi = int(xs.min()), int(xs.max())
        c = (lo + hi + 1) / 2.0
        hw = (hi - lo + 1) / 2.0
        for x in range(lo, hi + 1):
            dx = x + 0.5 - c
            n = 1
            if hw * hw > dx * dx:
                n = max(1, int(2 * np.sqrt(hw * hw - dx * dx) + 0.5))
            n = max(1, int(n * squash / 100.0 + 0.5))
            z0 = int(w / 2 - n / 2 + 0.5)
            for z in range(z0, z0 + n):
                vox.add((x, h - 1 - y, z))
    return vox


def prune_floaters(vox, keep_frac=0.004, keep_min=6, report=True):
    """Drop voxels that are not attached to anything.

    Nothing in this file ever checked that what it built was connected.
    Every builder decides voxel by voxel -- this pixel is bright so push
    it out, this one is dark so cut it away -- and a rule applied per
    pixel makes per-pixel debris: a speck of stone hanging in the air off
    the corner of a wall, a crumb of leaf beside a canopy.

    They cluster where the drawing is brightest, which under a north-west
    light (see viewangle) is the upper left. That is not a coincidence
    and it is not the room's fault: brightness-driven rules fire hardest
    where the light is, so the top left of anything collects the debris.

    So: 6-connected components, keep the big ones, drop the crumbs. A
    threshold rather than "largest only", because some objects are
    honestly in pieces -- a flame above a pedestal, a lid above a chest.
    """
    if not vox:
        return vox, 0
    todo = set(vox)
    comps = []
    while todo:
        seed = todo.pop()
        comp = {seed}
        stack = [seed]
        while stack:
            x, y, z = stack.pop()
            for n in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z),
                      (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)):
                if n in todo:
                    todo.discard(n)
                    comp.add(n)
                    stack.append(n)
        comps.append(comp)
    if len(comps) == 1:
        return vox, 0
    floor = max(keep_min, int(len(vox) * keep_frac))
    biggest = max(len(c) for c in comps)
    kept, dropped = set(), 0
    for c in comps:
        if len(c) >= floor or len(c) == biggest:
            kept |= c
        else:
            dropped += len(c)
    if report and dropped:
        print("  pruned %d floating voxels in %d detached pieces"
              % (dropped, len(comps) - 1))
    return kept, dropped


def write_obj_h(path, vox, colour_of):
    """write_obj, but the colour function is also told the voxel's height, so a
    top face can be shaded from the drawn top and a side from the drawn side."""
    vox, _ = prune_floaters(vox)
    return write_obj(path, vox, lambda x, z, _c=colour_of: _c(x, z, None),
                     colour3=colour_of)


def write_obj(path, vox, colour_of, colour3=None):
    """The voxels' outer surface, with interior faces culled.

    Neighbouring faces that lie in one plane and carry the same colour, as
    written (3 decimals), are merged into one rectangle, and a corner shared
    by faces of the same colour is written once. The surface and its colours
    are exactly those of one quad per exposed voxel face; the file is several
    times smaller, which is what a headset's triangle budget needs.
    """
    vox, _ = prune_floaters(vox)
    S = set(vox)
    faces = (((1, 0, 0), [(1,0,0),(1,1,0),(1,1,1),(1,0,1)]),
             ((-1, 0, 0), [(0,0,1),(0,1,1),(0,1,0),(0,0,0)]),
             ((0, 1, 0), [(0,1,0),(0,1,1),(1,1,1),(1,1,0)]),
             ((0, -1, 0), [(0,0,0),(1,0,0),(1,0,1),(0,0,1)]),
             ((0, 0, 1), [(0,0,1),(1,0,1),(1,1,1),(0,1,1)]),
             ((0, 0, -1), [(0,0,0),(0,1,0),(1,1,0),(1,0,0)]))

    # Exposed faces, grouped by plane: (face, position along its normal) ->
    # {(u, v): colour}, where u and v are the two in-plane axes.
    planes = {}
    for (x, y, z) in sorted(S):
        col = colour3(x, z, y) if colour3 is not None else colour_of(x, z)
        col = f"{col[0]:.3f} {col[1]:.3f} {col[2]:.3f}"
        p = (x, y, z)
        for fi, (d, _) in enumerate(faces):
            if (x + d[0], y + d[1], z + d[2]) in S:
                continue
            n = fi // 2
            u, v = (n + 1) % 3, (n + 2) % 3
            planes.setdefault((fi, p[n]), {})[(p[u], p[v])] = col

    V, F, index = [], [], {}

    def vertex(pos, col):
        key = (pos, col)
        if key not in index:
            V.append(key)
            index[key] = len(V)
        return index[key]

    for (fi, depth) in sorted(planes):
        cells = planes[(fi, depth)]
        n = fi // 2
        u, v = (n + 1) % 3, (n + 2) % 3
        quad = faces[fi][1]
        for (u0, v0) in sorted(cells, key=lambda c: (c[1], c[0])):
            col = cells.get((u0, v0))
            if col is None:
                continue                      # already inside a rectangle
            w = 1
            while cells.get((u0 + w, v0)) == col:
                w += 1
            h = 1
            while all(cells.get((u0 + i, v0 + h)) == col for i in range(w)):
                h += 1
            for j in range(h):
                for i in range(w):
                    del cells[(u0 + i, v0 + j)]
            ids = []
            for q in quad:
                pos = [0, 0, 0]
                pos[n] = depth + q[n]
                pos[u] = u0 + q[u] * w
                pos[v] = v0 + q[v] * h
                ids.append(vertex(tuple(pos), col))
            F.append(tuple(ids))

    with open(path, "w") as f:
        f.write("# fitted object; world-pixel units, +X east +Y up +Z south\n")
        for (x, y, z), c in V:
            f.write(f"v {x} {y} {z} {c}\n")
        for a, b, c_, d in F:
            f.write(f"f {a} {b} {c_} {d}\n")
    return len(V), len(F)


# ---------------------------------------------------------------- stairs ---
# Roles a top-down drawing gives a pixel.
R_GROUND, R_TOP, R_UPRIGHT, R_VOID, R_UNDEC, R_OUTLINE = 0, 1, 2, 3, 4, 5
ROLE_NAME = {R_GROUND: "ground", R_TOP: "top", R_UPRIGHT: "upright",
             R_VOID: "void", R_UNDEC: "undecided", R_OUTLINE: "outline"}


def _runlen(mask, along_rows):
    """Length of the solid run each pixel belongs to, along one axis."""
    m = mask if along_rows else mask.T
    out = np.zeros(m.shape, int)
    for j in range(m.shape[0]):
        row = m[j]
        i = 0
        while i < len(row):
            if not row[i]:
                i += 1
                continue
            k = i
            while k < len(row) and row[k]:
                k += 1
            out[j, i:k] = k - i
            i = k
    return out if along_rows else out.T


def linewidth(mask):
    """Per-pixel min(row-run, column-run): how thin the stroke is here.

    A drawn outline is thin in one axis whichever way you measure it; a filled
    region is not. This is what separates the dark ring around an object from
    the dark hole inside it, which share a palette entry and so cannot be told
    apart by colour.
    """
    return np.minimum(_runlen(mask, True), _runlen(mask, False))


def classify_faces(sub_mat, sub_shd, sub_art, void_lum=40.0, ring_w=3,
                   ground_tol=40.0, margin=4):
    """Split an object's pixels into ground / top / upright / void.

    GBA art shades a raised object out of one palette ramp, and it spends the
    ramp in a fixed way: the flat surface that catches light gets the top
    entries, and every vertical face -- outer wall, riser, the lip of a hole --
    gets the darker ones. So within a single material, the top two ramp entries
    are horizontal and the rest are upright. That is the whole rule, and it is
    what makes a height field readable off a top-down drawing at all.

    Darkness alone does not mean 'hole': the outline stroke around the object
    uses the same near-black entry as the void inside it. They are separated by
    stroke width, not by colour.
    """
    role = np.full(sub_mat.shape, R_GROUND, np.uint8)
    grey = sub_art.mean(axis=2)
    # Ground is whatever reaches the edge of the rect. A single outer row is
    # not enough to find it: a grass field is drawn from three or four palette
    # entries dithered together, and one row may happen to catch only one of
    # them, leaving the rest of the lawn to be read as object surface.
    band = np.zeros(sub_mat.shape, bool)
    band[:margin] = band[-margin:] = True
    band[:, :margin] = band[:, -margin:] = True
    border = set(sub_mat[band].ravel().tolist())
    # Ground is whatever the object is standing on, and the artist dithers the
    # edge of that ground a shade or two into the object's footprint using
    # separate palette entries. Matching on colour rather than on id catches
    # those, which otherwise read as bright flat tops ringing the object.
    bg_rgb = [sub_art[sub_mat == b].mean(0) for b in border if (sub_mat == b).any()]

    def is_ground(mm):
        if not len(bg_rgb):
            return False
        c = sub_art[mm].mean(0)
        return min(float(np.linalg.norm(c - b)) for b in bg_rgb) <= ground_tol

    for m in sorted(set(sub_mat.ravel().tolist())):
        mm = sub_mat == m
        dark = grey[mm].mean() <= void_lum
        if dark:
            # Near-black: a hole where it is a broad field, an outline where
            # it is a thin stroke.
            lw = linewidth(mm)
            role[mm & (lw > ring_w)] = R_VOID
            role[mm & (lw <= ring_w)] = R_OUTLINE
            continue
        if m in border or is_ground(mm):
            continue                                   # surrounding ground
        vals = sorted(set(sub_shd[mm].ravel().tolist()))
        n = len(vals)
        if n < 3:
            # Too few entries to be a shaded ramp, so this colour says nothing
            # about which way its surface faces; stair_field resolves it from
            # what it sits between.
            role[mm] = R_UNDEC
            continue
        for i, v in enumerate(vals):
            k = mm & (sub_shd == v)
            role[k] = R_TOP if i >= n - 2 else R_UPRIGHT
    return role


def _blobs(mask):
    """4-connected components as (size, ys, xs), largest first, border kept."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    out = []
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            stack, comp = [(sy, sx)], []
            seen[sy, sx] = True
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            ys = np.array([p[0] for p in comp])
            xs = np.array([p[1] for p in comp])
            out.append((len(comp), ys, xs))
    out.sort(key=lambda c: -c[0])
    return out


def deoutline(sub_art, outline, rounds=6):
    """Replace the outline stroke with the colour it encloses.

    The near-black stroke around every object is a drawing convention for a
    flat screen, not a material. Extruded literally it becomes a black wall
    standing in front of whatever it was meant to describe, so each outline
    pixel takes the colour of the nearest surface it borders instead.
    """
    art = sub_art.astype(float).copy()
    todo = outline.copy()
    for _ in range(rounds):
        if not todo.any():
            break
        src = ~todo
        acc = np.zeros_like(art)
        cnt = np.zeros(todo.shape, float)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(np.roll(src, dy, 0), dx, 1)
            sa = np.roll(np.roll(art, dy, 0), dx, 1)
            take = todo & sh
            acc[take] += sa[take]
            cnt[take] += 1
        fill = todo & (cnt > 0)
        art[fill] = acc[fill] / cnt[fill][:, None]
        todo &= ~fill
    return art.astype(np.uint8)


def enclosed(blob):
    """The hole inside a ring: pixels the outside cannot reach without
    crossing the ring. A raised platform with an opening in it is drawn as
    exactly that ring, so this is how the opening is found."""
    h, w = blob.shape
    free = ~blob
    seen = np.zeros_like(free)
    stack = [(y, x) for y in range(h) for x in (0, w - 1) if free[y, x]]
    stack += [(y, x) for x in range(w) for y in (0, h - 1) if free[y, x]]
    for y, x in stack:
        seen[y, x] = True
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and free[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return free & ~seen


def stair_field(role, sub_shd, sub_mat, sub_art, riser=3, min_tread=6):
    """Read a raised platform with an opening in it as a height field.

    Height is NOT brightness. On this cave mouth the outer steps are the
    *brightest* stone in the object and also the *lowest* part of it: they sit
    below the ledge they climb to. What the palette gives you is the grouping --
    one tread is one connected field of horizontal-facing entries, and the
    riser between two treads is a darker entry that breaks the connection.
    Height then comes from order along the run, and the direction comes from
    which end of the run meets open ground and which meets the hole.

    So: the ledge is the largest tread field, and it is drawn as a ring; the
    hole in that ring is the opening. Treads outside the ring climb to it, one
    riser each, nearest lowest. Treads inside it descend from it, one riser
    each, nearest highest.
    """
    h, w = role.shape
    grey = sub_art.mean(axis=2)
    role = role.copy()

    def top_blobs():
        return [b for b in _blobs(role == R_TOP) if b[0] >= min_tread]

    blobs = top_blobs()
    if not blobs:
        return None

    # Keep only the structure the ledge belongs to. Connectivity is traced
    # through surfaces, never through outline strokes: one near-black palette
    # entry outlines every object in the room, so following it would weld the
    # staircase to every bush around it.
    ring_n, ring_ys, ring_xs = blobs[0]
    core = (role != R_GROUND) & (role != R_OUTLINE)
    obj = np.zeros((h, w), bool)
    for size, ys, xs in _blobs(core):
        m = np.zeros((h, w), bool)
        m[ys, xs] = True
        if m[ring_ys, ring_xs].any():
            obj = m
            break
    for _ in range(2):                    # take back this object's own outline
        grown = obj.copy()
        grown[1:] |= obj[:-1]
        grown[:-1] |= obj[1:]
        grown[:, 1:] |= obj[:, :-1]
        grown[:, :-1] |= obj[:, 1:]
        obj |= grown & (role == R_OUTLINE)
    role[~obj] = R_GROUND
    role[role == R_OUTLINE] = R_UPRIGHT

    ledge_mask = np.zeros((h, w), bool)
    ledge_mask[ring_ys, ring_xs] = True
    hole = enclosed(ledge_mask)
    if not hole.any():
        return None

    # Palette entries with no ramp to read carry no elevation of their own, so
    # they are resolved against their neighbours instead. Inside the opening
    # the entries run tread, riser, void in descending luminance -- that is the
    # convention the drawing uses, and it is the only way to tell the flat of a
    # step from the face of it when both are a single flat colour.
    und = role == R_UNDEC
    role[und & ~hole] = R_TOP
    inside = und & hole
    if inside.any():
        mats = sorted(set(sub_mat[inside].ravel().tolist()),
                      key=lambda m: -grey[inside & (sub_mat == m)].mean())
        for i, m in enumerate(mats):
            role[inside & (sub_mat == m)] = R_TOP if i == 0 else R_UPRIGHT

    blobs = top_blobs()
    if not blobs:
        return None
    steps = []
    for size, ys, xs in blobs:
        steps.append({"n": size, "ys": ys, "xs": xs,
                      "y0": int(ys.min()), "y1": int(ys.max()),
                      "x0": int(xs.min()), "x1": int(xs.max()),
                      "cy": float(ys.mean())})
    ledge = max(steps, key=lambda p: p["n"])

    def in_hole(p):
        return bool(hole[p["ys"], p["xs"]].mean() > 0.5)

    inner = sorted((p for p in steps if p is not ledge and in_hole(p)),
                   key=lambda p: -p["cy"])          # nearest (south) first
    outer = sorted((p for p in steps if p is not ledge and not in_hole(p)),
                   key=lambda p: -p["cy"])

    top_h = riser * (len(outer) + 1)
    height = np.zeros((h, w), int)
    assigned = np.zeros((h, w), bool)
    plan = []

    def place(p, e, label):
        height[p["ys"], p["xs"]] = e
        assigned[p["ys"], p["xs"]] = True
        plan.append((label, p["y0"], p["y1"], p["x1"] - p["x0"] + 1, e, p["n"]))

    for i, p in enumerate(outer):
        place(p, riser * (i + 1), f"up {i + 1}")
    place(ledge, top_h, "ledge")
    for i, p in enumerate(inner):
        place(p, top_h - riser * (i + 1), f"down {i + 1}")

    # Risers drawn inside the opening with no tread visible behind them are
    # steps the drawing shows going further down than it shows a surface for.
    hidden = 0
    for size, ys, xs in _blobs((role == R_UPRIGHT) & hole):
        if size >= min_tread and ys.min() < min((p["y0"] for p in inner), default=h):
            hidden += 1

    shaft_floor = top_h - riser * (len(inner) + hidden + 1)
    # Only what was actually drawn as darkness is a hole. A riser inside the
    # opening is structure you stand on the edge of, not open air.
    role[~hole & (role == R_VOID)] = R_UPRIGHT

    # An upright pixel is the face between two levels; it takes the higher of
    # them so the wall is closed rather than a gap you can see through.
    # Which way the run travels: the axis the treads are stacked along.
    if len(inner) >= 2:
        cys = [p["cy"] for p in inner]
        cxs = [float(p["xs"].mean()) for p in inner]
        run_y = (max(cys) - min(cys)) >= (max(cxs) - min(cxs))
    else:
        run_y = True
    run_dirs = ((1, 0), (-1, 0)) if run_y else ((0, 1), (0, -1))

    ys, xs = np.where((role == R_UPRIGHT) & ~assigned)
    for y, x in zip(ys, xs):
        best = None
        # Inside the opening every upright is a riser, and a riser belongs to
        # the tread behind it -- not to the side wall it happens to touch.
        # Looking sideways there is what fills the whole stairwell with rock.
        dirs = run_dirs if hole[y, x] else ((1, 0), (-1, 0), (0, 1), (0, -1))
        for dy, dx in dirs:
            for step in range(1, 8):
                ny, nx = y + dy * step, x + dx * step
                if not (0 <= ny < h and 0 <= nx < w):
                    break
                if assigned[ny, nx]:
                    v = int(height[ny, nx])
                    best = v if best is None else max(best, v)
                    break
        if best is None and dirs is run_dirs:
            # Nothing up or down the run: this is a side wall of the stairwell
            # rather than a riser, so let it match whatever it flanks.
            for dy, dx in ((0, 1), (0, -1)):
                for step in range(1, 8):
                    ny, nx = y + dy * step, x + dx * step
                    if not (0 <= ny < h and 0 <= nx < w):
                        break
                    if assigned[ny, nx]:
                        v = int(height[ny, nx])
                        best = v if best is None else max(best, v)
                        break
        # An upright with no level anywhere near it is the outline running
        # along the object's skirt, which sits on the ground, not on the roof.
        height[y, x] = 0 if best is None else best
    return height, role, shaft_floor, top_h, plan, hidden


def build_stairs(height, role, shaft_floor, base=0):
    """Extrude the height field into a solid.

    Shaft columns stay open above their floor so the player can actually walk
    down into the dark. Only the rock immediately around the shaft is carried
    below ground level -- everything else stops at the surface it stands on,
    which is both what the world looks like and a great many fewer voxels.
    """
    h, w = height.shape
    deep = role == R_VOID
    for _ in range(2):
        grown = deep.copy()
        grown[1:] |= deep[:-1]
        grown[:-1] |= deep[1:]
        grown[:, 1:] |= deep[:, :-1]
        grown[:, :-1] |= deep[:, 1:]
        deep = grown
    vox = set()
    for y in range(h):
        for x in range(w):
            r = role[y, x]
            if r == R_VOID:
                vox.add((x, shaft_floor, y))
                continue
            if r == R_GROUND:
                vox.add((x, base, y))
                continue
            bottom = shaft_floor if deep[y, x] else base
            for e in range(bottom, int(height[y, x]) + 1):
                vox.add((x, e, y))
    return vox


def cmd_steps(args):
    """Fit a stair run / cave mouth as a height field read off the drawing."""
    r = RE.load_room(Path(args.path))
    d = SH.decompose_room(r, args.layer)
    if d is None:
        sys.exit("needs a v3+ dump with palettes")
    mat, shd = d[0], d[1]
    art = RE.room_art_rgb(r, args.layer)
    cx0, cy0, cx1, cy1 = (int(v) for v in args.cells.split(","))
    x0, y0, x1, y1 = cx0 * 16, cy0 * 16, cx1 * 16, cy1 * 16
    sub_mat, sub_shd, sub_art = mat[y0:y1, x0:x1], shd[y0:y1, x0:x1], art[y0:y1, x0:x1]

    role = classify_faces(sub_mat, sub_shd, sub_art, args.void_lum,
                          margin=args.margin)
    sub_art = deoutline(sub_art, role == R_OUTLINE)
    got = stair_field(role, sub_shd, sub_mat, sub_art, args.riser, args.min_tread)
    if got is None:
        sys.exit("no opening or no treads in that rect -- widen --cells")
    height, role, shaft_floor, top_h, plan, hidden = got

    vox = build_stairs(height, role, shaft_floor)

    def colour_of(x, z):
        if 0 <= z < sub_art.shape[0] and 0 <= x < sub_art.shape[1]:
            return tuple(float(v) / 255.0 for v in sub_art[z, x])
        return (0.5, 0.5, 0.5)

    nv, nf = write_obj(args.out, vox, colour_of)
    counts = {ROLE_NAME[k]: int((role == k).sum())
              for k in (R_GROUND, R_TOP, R_UPRIGHT, R_VOID)}
    print(f"  {Path(args.path).name} cells {args.cells}  riser {args.riser}px")
    print("  faces: " + "  ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"  {len(plan)} level(s), ledge at +{top_h}, shaft floor at {shaft_floor:+d}:")
    for label, r0, r1, wid, e, n in plan:
        print(f"    {label:<8} rows {r0:3d}..{r1:<3d} width {wid:3d}  elev {e:+3d}  ({n} px)")
    if hidden:
        print(f"  {hidden} further riser(s) drawn with no tread behind them: the")
        print(f"  run keeps descending past what the drawing shows a surface for.")
    print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
    print("  Tread grouping comes from the palette ramp; elevation comes from")
    print("  order along the run, not from brightness.")


def _scan_one(path):
    """Find every ring-and-hole in one room. Module level so it can be pooled."""
    try:
        r = RE.load_room(Path(path))
        d = SH.decompose_room(r, 0)
        if d is None:
            return []
        mat, shd = d[0], d[1]
        art = RE.room_art_rgb(r, 0)
    except Exception:
        return []
    role = classify_faces(mat, shd, art, margin=4)
    out = []
    for size, ys, xs in _blobs(role == R_TOP):
        if not (60 <= size <= 20000):
            continue
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        if (y1 - y0) < 8 or (x1 - x0) < 8:
            continue
        sub = np.zeros((y1 - y0 + 3, x1 - x0 + 3), bool)
        sub[ys - y0 + 1, xs - x0 + 1] = True
        a = int(enclosed(sub).sum())
        if a < 24:
            continue
        out.append((Path(path).name, a, int(size),
                    int(x0) // 16, int(y0) // 16, int(x1) // 16 + 1, int(y1) // 16 + 1))
    return out


def cmd_openings(args):
    """Find every opening in the world: a raised surface with a hole in it.

    A cave mouth, a doorway, an archway and a walled room are the same drawing
    to this test -- a ring of horizontal surface enclosing something that is
    not that surface. They separate by size, which is why the report is sorted
    by hole area rather than filtered: a doorway is tens of pixels, an archway
    a hundred or two, and a room's floor is thousands. Rather than guess a
    threshold, look at the bands and name them.
    """
    from multiprocessing import Pool
    files = sorted(Path(args.dir).glob("room_*.tmcr"))
    if not files:
        sys.exit(f"no room_*.tmcr under {args.dir}")
    lo, hi = (int(v) for v in args.holes.split(","))
    res = []
    bar = RE.Progress(len(files), "openings")
    with Pool(args.jobs) as pool:
        for hits in pool.imap_unordered(_scan_one, [str(f) for f in files]):
            res += [h for h in hits if lo <= h[1] <= hi]
            bar.update(1, f"{len(res)} found")
    bar.done()
    res.sort(key=lambda t: -t[1])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write("# room  hole_px  ring_px  cells\n")
        for f, a, size, cx0, cy0, cx1, cy1 in res:
            fh.write(f"{f} hole={a} ring={size} cells {cx0},{cy0},{cx1},{cy1}\n")
    print(f"  {len(res)} opening(s) in {len(files)} room(s) -> {out}")
    if res:
        bands = [("doorway-ish", 24, 120), ("archway-ish", 121, 400),
                 ("mouth/alcove", 401, 1500), ("room floor", 1501, 10 ** 9)]
        for name, a, b in bands:
            n = sum(1 for t in res if a <= t[1] <= b)
            print(f"    {name:<14} {n:5d}   hole {a}..{b} px")
        print("  Fit one with: shapefit.py steps <room> --cells <cells>")


def dark_outline(rgb, mask, lum=48.0, ring_w=3):
    """The near-black stroke around a drawn object, as a mask."""
    grey = rgb[..., :3].mean(axis=2)
    dark = mask & (grey <= lum)
    if not dark.any():
        return dark
    return dark & (linewidth(dark) <= ring_w)


def split_top_side(rgb, mask):
    """Split a top-down sprite into its lit top face and its shaded side.

    Same convention the staircase relies on: a drawn object spends its ramp
    with the horizontal surface on the light entries and every vertical face on
    the dark ones. On a stump that separates the cut face from the bark, and
    those two measurements -- how wide the cut is, how tall the bark band is --
    are the whole cylinder.
    """
    lum = rgb[..., :3].mean(axis=2)
    vals = np.sort(lum[mask])
    if not len(vals):
        return mask & False, mask & False
    # Split at the largest gap in the middle of the luminance range rather than
    # a fixed threshold, so it follows the object's own palette.
    lo, hi = int(len(vals) * 0.15), int(len(vals) * 0.85)
    seg = vals[lo:hi] if hi > lo + 2 else vals
    gaps = np.diff(seg)
    cut = float(seg[int(np.argmax(gaps))]) if len(gaps) else float(vals.mean())
    bright = mask & (lum > cut)
    # Tone alone is not enough: bark catches the same highlights as the cut
    # face, so thresholding paints half the trunk as "top". The cut face is the
    # bright region that reaches the TOP of the drawing and is continuous --
    # one blob, not a stack of lit ridges. Take that blob and call everything
    # below it side.
    blobs = _blobs(bright)
    if not blobs:
        return bright, mask & ~bright
    face = min(blobs, key=lambda b: (b[1].min(), -b[0]))
    top = np.zeros_like(mask)
    top[face[1], face[2]] = True
    side = mask & ~top
    return top, side


def build_cylinder(top, side, squash=100, height_override=0):
    """A stump: a circular cut face over a bark band as tall as the drawn side.

    Measured: the radius, from the widest span of the cut face; the height,
    from the number of rows the side band occupies. Invented: that the plan is
    a circle at all. The drawing gives an ellipse because of the camera lean,
    and nothing in it says what the hidden half looks like -- so the ellipse's
    WIDTH is used and its height discarded, which is the one reading that does
    not bake the viewing angle into the model.
    """
    ys, xs = np.where(top)
    if not len(ys):
        return set(), 0, 0
    x0, x1 = int(xs.min()), int(xs.max())
    r = (x1 - x0 + 1) / 2.0
    cx = (x0 + x1 + 1) / 2.0
    # Only rows BELOW the cut face count as bark. The dark outline wraps the
    # whole sprite, so counting every row that contains a dark pixel makes the
    # stump as tall as the drawing instead of as tall as its visible side.
    below = side.any(axis=1) & ~top.any(axis=1)
    rows = np.where(below)[0]
    top_rows = np.where(top.any(axis=1))[0]
    if len(rows) and len(top_rows):
        rows = rows[rows > top_rows.max()]
    height = int(height_override) if height_override else max(1, int(len(rows)))
    rz = max(1.0, r * squash / 100.0)
    vox = set()
    for e in range(height + 1):
        for x in range(x0, x1 + 1):
            dx = (x + 0.5 - cx) / r
            if dx * dx > 1.0:
                continue
            span = rz * np.sqrt(1.0 - dx * dx)
            for z in range(int(round(-span)), int(round(span)) + 1):
                vox.add((x, e, z))
    return vox, height, r


def radial_radii(mask, cy, cx, bins=72):
    """The object's outline as a radius per direction.

    Tracing the silhouette this way keeps the bumps. A stump's roots are not
    noise around a circle, they ARE the shape near the ground -- so the outline
    is recorded direction by direction rather than collapsed to one number,
    and a root is simply a direction where the outline reaches further.
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return np.zeros(bins)
    ang = np.arctan2(ys - cy, xs - cx)
    rad = np.hypot(ys - cy, xs - cx)
    idx = (((ang + np.pi) / (2 * np.pi)) * bins).astype(int) % bins
    out = np.zeros(bins)
    for b in range(bins):
        m = idx == b
        if m.any():
            out[b] = rad[m].max()
    # A direction with no pixels is a gap in the trace, not a radius of zero.
    miss = out <= 0
    if miss.any() and (~miss).any():
        good = np.where(~miss)[0]
        for b in np.where(miss)[0]:
            j = good[np.argmin(np.abs(((good - b + bins // 2) % bins) - bins // 2))]
            out[b] = out[j]
    return out


def build_flared(full, top, height, bins=72, top_frac=0.25):
    """A stump: a raised circular cut face over a trunk that widens to a
    rooted base.

    The name says what the shape is. A stump is not a cylinder -- it is a cut
    face sitting above a trunk that flares out into roots, and a top-down
    drawing shows both: the bright disc is the cut, and the ragged silhouette
    around it is the base. Reading only the bounding box throws that away and
    returns a drum as wide as the widest root.

    So the outline is traced twice, once for the cut face and once for the
    whole object, and the cross-section is interpolated between them with
    height. The top of the run holds the cut face flat -- a stump has a lip,
    not a dome -- and everything below widens back out to the roots.
    """
    ys, xs = np.where(top if top.any() else full)
    cy, cx = float(ys.mean()), float(xs.mean())
    R_base = radial_radii(full, cy, cx, bins)
    R_top = radial_radii(top, cy, cx, bins) if top.any() else R_base * 0.6
    h_, w_ = full.shape
    yy, xx = np.mgrid[0:h_, 0:w_]
    dy, dx = yy - cy, xx - cx
    rr = np.hypot(dy, dx)
    bb = (((np.arctan2(dy, dx) + np.pi) / (2 * np.pi)) * bins).astype(int) % bins
    vox = set()
    flat = max(1, int(round(height * top_frac)))
    for e in range(height + 1):
        # The last `flat` rows are the cut face itself, held at one radius.
        t = 1.0 if e > height - flat else (e / max(1, height - flat))
        R = R_base * (1.0 - t) + R_top * t
        keep = rr <= R[bb]
        for (y, x) in zip(*np.where(keep)):
            vox.add((int(x), int(e), int(y)))
    return vox, (cy, cx), R_base, R_top


def trace_png(path, art, full, top, centre, R_base, R_top, bins=72, scale=6):
    """Draw what was traced, so the outline can be argued with."""
    from PIL import Image, ImageDraw
    h_, w_ = full.shape
    base = np.array(art[..., :3], dtype=np.uint8).copy()
    base[full & ~top] = (base[full & ~top] * 0.55 + np.array([255, 120, 0]) * 0.45)
    base[top] = (base[top] * 0.55 + np.array([0, 200, 255]) * 0.45)
    im = Image.fromarray(base).resize((w_ * scale, h_ * scale), Image.NEAREST)
    d = ImageDraw.Draw(im)
    cy, cx = centre
    for R, col in ((R_base, (255, 60, 60)), (R_top, (60, 160, 255))):
        pts = []
        for b in range(bins + 1):
            a = (b % bins) / bins * 2 * np.pi - np.pi
            pts.append(((cx + R[b % bins] * np.cos(a)) * scale,
                        (cy + R[b % bins] * np.sin(a)) * scale))
        d.line(pts, fill=col, width=2)
    im.save(path)


def cmd_sprite(args):
    """Fit a solid to a harvested sprite PNG instead of to a tilemap rect."""
    from PIL import Image
    im = Image.open(args.path).convert("RGBA")
    a = np.array(im)
    mask = a[..., 3] > 0
    if not mask.any():
        sys.exit(f"{args.path} is fully transparent")
    ys, xs = np.where(mask)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    a = a[y0:y1 + 1, x0:x1 + 1]
    mask = mask[y0:y1 + 1, x0:x1 + 1]
    rgb = a[..., :3].astype(float)

    top, side = split_top_side(rgb, mask)
    if args.mode == "cylinder":
        vox, height, radius = build_cylinder(top, side, args.squash)
        r = radius
        detail = f"radius {r:.1f}px from the cut face, height {height}px from the bark"
    elif args.mode == "slab":
        vox = build_slab(mask, args.height)
        height, radius = args.height, max(1.0, mask.shape[1] / 2.0)
        detail = f"slab {args.height}px"
    else:
        vox = build_taper(mask, args.height, args.inset_every)
        height, radius = args.height, max(1.0, mask.shape[1] / 2.0)
        detail = f"taper {args.height}px"
    if not vox:
        sys.exit("nothing fitted")

    # Wrap the drawing back onto the solid instead of sampling one row per
    # column. The cut face is drawn as an ellipse because of the camera lean,
    # so a point at depth z on the circular top came from a row offset by
    # z/radius times the ellipse's half-height -- undo that and the rings land
    # where they belong instead of smearing into stripes. The bark band is
    # drawn once and wraps, so it is sampled by how far down the side the voxel
    # sits.
    h_, w_ = mask.shape
    trows = np.where(top.any(axis=1))[0]
    srows = np.where(side.any(axis=1) & ~top.any(axis=1))[0]
    if len(trows):
        t0, t1 = int(trows.min()), int(trows.max())
    else:
        t0 = t1 = 0
    tc = (t0 + t1) / 2.0
    th = max(1.0, (t1 - t0 + 1) / 2.0)
    srows = srows[srows > t1] if len(srows) and len(trows) else srows
    b0 = int(srows.min()) if len(srows) else t1
    b1 = int(srows.max()) if len(srows) else min(h_ - 1, t1 + 1)
    fallback = tuple(float(v) / 255.0 for v in rgb[mask].mean(axis=0)[:3])

    def sample(yy, xx):
        yy = min(max(int(round(yy)), 0), h_ - 1)
        xx = min(max(int(round(xx)), 0), w_ - 1)
        if not mask[yy, xx]:
            return None
        return tuple(float(v) / 255.0 for v in rgb[yy, xx][:3])

    def colour_of(x, z, e=None):
        if e is not None and e >= height:
            yy = tc + (z / max(1.0, radius)) * th
            c = sample(yy, x)
            if c:
                return c
        frac = 0.0 if height <= 0 else (height - (e or 0)) / float(height)
        c = sample(b0 + frac * max(0, b1 - b0), x)
        return c or fallback

    def colour_at(x, z):
        return colour_of(x, z, None)

    nv, nf = write_obj_h(args.out, vox, colour_of)
    print(f"  {Path(args.path).name}  mode {args.mode}")
    print(f"  mask {int(mask.sum())}px -> top {int(top.sum())}px, side {int(side.sum())}px")
    print(f"  {detail}")
    print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")


def build_dome(full, top, height, bins=72):
    """Foliage: a mass that is widest low down and rounds off at the top.

    A bush or a tree crown is the opposite profile to a stump. The stump has a
    flat cut and a flared base; foliage swells outward and closes over. Both
    are read from the same two traces, and only the curve between them differs
    -- which is the point: the name says which curve, the drawing says how big.
    """
    ys, xs = np.where(full)
    cy, cx = float(ys.mean()), float(xs.mean())
    R_base = radial_radii(full, cy, cx, bins)
    R_top = radial_radii(top, cy, cx, bins) if top.any() else R_base * 0.35
    h_, w_ = full.shape
    yy, xx = np.mgrid[0:h_, 0:w_]
    dy, dx = yy - cy, xx - cx
    rr = np.hypot(dy, dx)
    bb = (((np.arctan2(dy, dx) + np.pi) / (2 * np.pi)) * bins).astype(int) % bins
    vox = set()
    for e in range(height + 1):
        t = e / max(1, height)
        # Round, not conical: the silhouette holds most of the way up and then
        # falls away quickly, the way a drawn crown does.
        k = float(np.cos(t * np.pi / 2.0))
        R = R_top + (R_base - R_top) * k
        for (y, x) in zip(*np.where(rr <= R[bb])):
            vox.add((int(x), int(e), int(y)))
    return vox, (cy, cx), R_base, R_top


def build_upright(mask, squash=100, dome=1.0, cap_frac=0.0,
                  relief=None, relief_depth=0.0, foot_frac=0.0, vscale=1.0,
                  section="round"):
    """An object that stands up, drawn from above AND from the south.

    The whole game is drawn from one camera: high up and to the south. A tall
    thing is therefore drawn very nearly as its FRONT ELEVATION -- a mushroom
    house is a cap above a wall, a tree is a crown above a trunk -- with only
    its top surface showing over the shoulder. Reading that drawing as a plan
    is what makes a house come out as a pancake 90px wide and 10px tall: it
    puts the building's HEIGHT into the ground.

    But the drawing is not ONE view, it is two joined at the shoulder. Below
    the widest row the object is drawn side-on, and those rows fold up, each
    becoming a horizontal slice as wide as it was drawn. Above it the object
    is drawn from above, and those rows are a PLAN of the cap: folding them up
    too is what leaves a flat lid where a dome should be. They are lifted
    instead, by how far in from the cap's rim they sit, which is the only
    reading that closes the top.

    Returns (voxels, base_row, shoulder_row, rowmap) where rowmap[(x, e)] is
    the drawn row that coloured that height, so the door stays where it was
    drawn.
    """
    h, w = mask.shape
    ys, xs = np.where(mask)
    if not len(ys):
        return set(), 0, 0, {}
    base = int(ys.max())
    top = int(ys.min())
    # Where does the drawing stop being an elevation and start being a view
    # of the top? Measure it rather than guess a fraction.
    #
    # Coming down from the crown the silhouette WIDENS, because each row is a
    # ring further round the top of the object -- that is the far side seen
    # from above. Once it stops widening you are looking at the side, and
    # every row below is elevation. So the shoulder is the first row that
    # reaches full width, and on the Minish Woods mushroom that is row 37 of
    # 16..87: measured, not a fraction I picked.
    span = np.array([
        (int(np.where(mask[y])[0].max()) - int(np.where(mask[y])[0].min()) + 1)
        if mask[y].any() else 0 for y in range(h)])
    if cap_frac and cap_frac > 0:
        y_wide = top + max(1, int(round((base - top + 1) * cap_frac)))
    else:
        band = span[top:base + 1]
        thresh = band.max() * 0.98
        hit = np.where(band >= thresh)[0]
        y_wide = top + int(hit[0]) if len(hit) else top + max(1, (base - top) // 3)
    y_wide = min(max(y_wide, top + 1), base)

    vox = set()
    rowmap = {}
    ring = {}                      # height -> (centre x, half width, drawn row)

    # The drawing has a foot as well as a cap. The bottom band is the ground
    # CONTACT seen from above -- a mushroom's stem below its door is drawn
    # there, and it is in the ground, not standing on it. Folding those rows
    # up lifts the whole object onto a plinth of its own base. They lie flat
    # at height zero instead, which is the same rule as the cap, mirrored.
    y_foot = base - int(round((base - top + 1) * foot_frac)) if foot_frac else base

    # ---- body: rows between the foot and the shoulder fold up ----
    prev_e = -1
    for y in range(y_wide, y_foot + 1):
        row = np.where(mask[y])[0]
        if not len(row):
            continue
        lo, hi = int(row.min()), int(row.max())
        c = (lo + hi + 1) / 2.0
        hw = (hi - lo + 1) / 2.0
        # The elevation is foreshortened: the camera looks DOWN as well as
        # south, so drawn height is shorter than real height. Link is the
        # ruler -- he is drawn 24px tall and has to walk through these doors,
        # so a doorway drawn 20px must stand at least 24. vscale is that
        # correction, and it is the one number here taken from the game rather
        # than from this drawing.
        e = int(round((y_foot - y) * vscale))
        for x in range(lo, hi + 1):
            if not mask[y, x]:
                continue
            dx = x + 0.5 - c
            if section == "box":
                # A chest is a box. Revolving its profile turns it into a
                # barrel: the drawing says nothing about depth either way, but
                # "as deep as it is wide, with corners" is the right reading
                # for furniture and the wrong one for a mushroom.
                d = hw * squash / 100.0
            else:
                d = 0.5 if hw * hw <= dx * dx else \
                    np.sqrt(hw * hw - dx * dx) * squash / 100.0
            # Shallow relief from the drawn shading. The artist already
            # modelled this surface with light and dark -- a window sunk into
            # a wall, a spot raised on a cap -- so the ramp position is a
            # depth cue for THIS pixel even though it says nothing about the
            # scene's lighting. Kept to a voxel or two: it is texture, not
            # geometry, and any more would be inventing a shape.
            if relief is not None and relief_depth:
                d = max(0.5, d + relief[y, x] * relief_depth)
            for z in range(int(round(-d)), int(round(d)) + 1):
                vox.add((x, e, z))
            rowmap[(x, e)] = y
            ring[e] = (c, hw, y)
            # vscale leaves gaps between rows; fill them so the surface is
            # continuous and every height knows which row drew it.
            if vscale > 1.0 and prev_e >= 0:
                for e2 in range(e + 1, prev_e):
                    for z in range(int(round(-d)), int(round(d)) + 1):
                        vox.add((x, e2, z))
                    rowmap.setdefault((x, e2), y)
                    ring.setdefault(e2, (c, hw, y))
        prev_e = e

    # ---- cap: the far rim, domed over the body it sits on ----
    # The dome is built on the SHOULDER's circle, not on the cap band's own
    # drawn width. Using the band's width makes the cap narrower than the
    # body and it perches on top like a separate object; starting from the
    # shoulder makes one continuous surface. The cap's rows then map onto it
    # radially: the top row is the crown, the shoulder row is the rim, and
    # everything between compresses the way a dome does when seen from the
    # south.
    if section == "box" and y_wide > top:
        # A chest is flat-faced with a BARREL lid: a half cylinder lying on
        # its side, its axis running left to right, which is why its planks
        # run left to right too. Neither of the other two readings fits it --
        # a dome rounds it in both axes, and a flat plan gives it a lid like
        # a table top.
        #
        # The drawn lid band is that half cylinder seen from the front and
        # above, so its rows run from the FAR edge to the near one over the
        # arc. Mapping row to arc angle rather than to depth keeps the planks
        # evenly spaced instead of bunching them at the top.
        capm = mask.copy()
        capm[y_wide:] = False
        if capm.any():
            cys, cxs = np.where(capm)
            band_ = max(1, int(cys.max() - cys.min() + 1))
            shoulder = y_foot - y_wide
            # Depth of the lid: as deep as the body is wide, halved.
            brow = np.where(mask[y_wide])[0]
            D = max(1.0, (brow.max() - brow.min() + 1) / 2.0
                    * squash / 100.0) if len(brow) else float(band_)
            lid_h = D * dome if dome else D
            for yy_ in range(int(cys.min()), int(cys.max()) + 1):
                xs_ = np.where(capm[yy_])[0]
                if not len(xs_):
                    continue
                t = (yy_ - cys.min()) / float(band_ - 1) if band_ > 1 else 1.0
                ang = np.pi * t
                z = -D * np.cos(ang)
                hgt = shoulder + lid_h * np.sin(ang)
                for xx_ in xs_:
                    e_top = int(round(hgt))
                    zi = int(round(z))
                    for e in range(shoulder, e_top + 1):
                        vox.add((int(xx_), e, zi))
                        rowmap.setdefault((int(xx_), e), int(yy_))
                    # Close the lid's shell so it is solid, not a vault.
                    for zf in range(min(0, zi), max(0, zi) + 1):
                        vox.add((int(xx_), shoulder, zf))
        return vox, y_foot, y_wide, rowmap, ring

    row = np.where(mask[y_wide])[0]
    if len(row) and y_wide > top:
        lo, hi = int(row.min()), int(row.max())
        ccx = (lo + hi + 1) / 2.0
        R = max(1.0, (hi - lo + 1) / 2.0)
        shoulder = int(round((y_foot - y_wide) * vscale))
        band = float(y_wide - top)
        ri = int(np.ceil(R))
        for dxi in range(-ri, ri + 1):
            for dzi in range(-ri, ri + 1):
                rr = float(np.hypot(dxi, dzi))
                if rr > R:
                    continue
                # Two stretches lived here. Vertically, the dome rose R
                # voxels while the art that clothes it is only `band` rows
                # tall, so every drawn row was smeared over R/band voxels.
                # Radially, those same rows were spread from rim to crown.
                # The cap now rises exactly as many voxels as it has drawn
                # rows -- one row, one voxel -- and the band CLONES inward
                # rather than stretching, the same rule the bark follows.
                lift = np.sqrt(max(0.0, R * R - rr * rr)) * (band / R) * dome
                x = int(round(ccx + dxi))
                if not (0 <= x < w):
                    continue
                inward = int(round(R - rr))
                art_row_pre = int(y_wide - (inward % max(1, int(band))))
                if relief is not None and relief_depth and 0 <= art_row_pre < h:
                    lift += relief[art_row_pre, x] * relief_depth
                e_top = int(round(shoulder + lift))
                z = int(round(dzi * squash / 100.0))
                art_row = art_row_pre
                for e in range(shoulder, e_top + 1):
                    vox.add((x, e, z))
                    if (x, e) not in rowmap:
                        rowmap[(x, e)] = art_row
    if foot_frac and y_foot < base:
        for y in range(y_foot + 1, base + 1):
            row = np.where(mask[y])[0]
            if not len(row):
                continue
            lo, hi = int(row.min()), int(row.max())
            c = (lo + hi + 1) / 2.0
            hw = max(1.0, (hi - lo + 1) / 2.0)
            for x in range(lo, hi + 1):
                if not mask[y, x]:
                    continue
                dx = x + 0.5 - c
                d = 0.5 if hw * hw <= dx * dx else \
                    np.sqrt(hw * hw - dx * dx) * squash / 100.0
                for z in range(int(round(-d)), int(round(d)) + 1):
                    vox.add((x, 0, z))
                rowmap.setdefault((x, 0), y)
    return vox, y_foot, y_wide, rowmap, ring


_PLINTH_LAST = [0]
PLINTH_OUT = 1      # voxels a base course stands proud of the wall above it
PLINTH_DARK = 0.85  # a plinth is at most this fraction of the wall's brightness
PLINTH_SPAN = 0.60  # ...and runs across at least this much of the width
PLINTH_MAX = 6      # a base course is a course, not a storey


def _plinth(vox, wall_rows, med, art, matsub=None):
    """Put a projecting base course back -- IF the drawing has one.

    A stone building often stands on a plinth: a course that carries the
    wall and juts out past it. Where that exists it is worth building,
    because a step at the foot is what makes a building sit on the ground
    instead of floating on it.

    Two ways of finding it were tried and both found one where there was
    none. Brightness fails because by the time the roof is split off, the
    wall band left behind is already the dark part of the drawing, so
    "darker than the wall" asks a dark band to be darker than itself.
    Material identity fails worse: on the stone block in room_05_01 it
    reported 62% of the foot as a different material, which was true and
    meant nothing -- those were the grey posts running the full height at
    both edges, not a course along the bottom.

    So this looks for an actual STEP: a sustained drop in brightness across
    most of the front, starting at the foot. On that same block there is no
    step -- the wall is mottled at the same level all the way up, and its
    dark foot is simply the base-course material, which belongs in the mask
    and shows up once it is named. This returns 0 there, and it should.
    Abstaining is the point: an invented plinth is worse than none.
    """
    if art is None or not wall_rows:
        return 0
    lum = art[:, :, :3].astype(float).mean(axis=2)
    cols = sorted(wall_rows)
    upper = []
    for x in cols:
        r0, r1 = wall_rows[x]
        mid = r1 - max(1, (r1 - r0) // 2)
        upper.extend(lum[r0:mid + 1, x].tolist())
    if not upper:
        return 0
    ref = float(np.median(upper))
    depth = 0
    for k in range(0, min(med, PLINTH_MAX) + 1):
        dark = seen = 0
        for x in cols:
            r0, r1 = wall_rows[x]
            row = r1 - k
            if row < r0:
                continue
            seen += 1
            if float(lum[row, x]) < ref * PLINTH_DARK:
                dark += 1
        if seen and dark / float(seen) >= PLINTH_SPAN:
            depth = k + 1
        else:
            break
    if depth < 2:
        return 0          # one dark row is an outline, not a course
    face = {}
    for (x, e, z) in list(vox):
        if e > med:
            continue
        if x not in face or z > face[x]:
            face[x] = z
    for x, zf in face.items():
        if x not in wall_rows:
            continue
        for e in range(0, depth):
            for d in range(1, PLINTH_OUT + 1):
                vox.add((int(x), int(e), int(zf + d)))
    return depth


WALL_RELIEF = 1     # voxels a drawn stone stands proud of its mortar


def _wall_relief(vox, wall, wall_rows, med, art):
    """Give a stone wall its courses back.

    A building leaves this file as a box: the plan, extruded to the storey
    height the wall measured. Every face is then dead flat, and a stone
    house reads as a painted crate -- which is the one thing the drawing
    never looks like, because the artist shaded individual blocks.

    That shading is a measurement like any other. On this wall the light is
    the same north-west light as everywhere else (see viewangle), so a
    block face catching it is drawn bright and the mortar line between
    courses is drawn dark. Pushing the bright pixels proud by a voxel and
    sinking the dark ones turns the painted courses into real ones, and it
    needs no guess about how the masonry is laid -- the artist already
    decided that.

    The fold is the same one the wall itself uses: one drawn row per voxel
    of elevation, counting up from the bottom of the drawn band.
    """
    if art is None or not wall_rows:
        return
    lum = art[:, :, :3].astype(float).mean(axis=2)
    face = {}
    for (z, x) in zip(*np.where(wall)):
        if x not in face or z > face[x]:
            face[int(x)] = int(z)
    vals = [lum[r0:r1 + 1, x] for x, (r0, r1) in wall_rows.items()]
    flat = np.concatenate([v.ravel() for v in vals]) if vals else None
    if flat is None or not flat.size:
        return
    lo, hi = float(np.percentile(flat, 8)), float(np.percentile(flat, 92))
    rng = max(hi - lo, 1e-6)
    # Decide the whole face first, THEN edit the voxels. Doing both at
    # once -- push this pixel out, cut that one away -- is what left
    # specks hanging off the corners: a lone bright pixel became a lone
    # voxel with nothing beside it. A stone is several pixels across, so
    # a proud patch has to be too.
    hi_m, lo_m = {}, {}
    for x in sorted(face):
        rr = wall_rows.get(x)
        if rr is None:
            continue
        r0, r1 = rr
        for e in range(0, med + 1):
            row = r1 - e
            if row < r0:
                break
            t = (float(lum[row, x]) - lo) / rng
            hi_m[(x, e)] = t > 0.66
            lo_m[(x, e)] = t < 0.33

    def opened(m):
        # erode then dilate: a cell survives only if its neighbours agree
        er = {k for k in m
              if m.get(k) and m.get((k[0] + 1, k[1]), False)
              and m.get((k[0] - 1, k[1]), False)
              and m.get((k[0], k[1] + 1), False)
              and m.get((k[0], k[1] - 1), False)}
        out = set(er)
        for (x, e) in er:
            out.update({(x + 1, e), (x - 1, e), (x, e + 1), (x, e - 1)})
        return {k for k in out if k in m}

    for (x, e) in opened(hi_m):
        zf = face[x]
        for d in range(1, WALL_RELIEF + 1):
            vox.add((int(x), int(e), int(zf + d)))
    # Cut from the top down, and never cut a voxel still holding one up --
    # a hole punched under a surviving course is how a wall ends up with
    # its top left hanging in the air.
    for (x, e) in sorted(opened(lo_m), key=lambda k: -k[1]):
        zf = face[x]
        if (int(x), int(e + 1), int(zf)) in vox:
            continue
        vox.discard((int(x), int(e), int(zf)))


def build_building(roof, wall, height_hint=0, art=None, matsub=None):
    """A structure that projects from the ground, with its front wall drawn.

    This is the case where the height is MEASURED rather than authored. A
    stump is drawn from straight above and says nothing about how tall it is,
    so a number has to be supplied. A building is not: the band of wall drawn
    below the roof is a true elevation of that wall, seen straight on, and the
    number of rows it occupies IS the building's height in pixels.

    So the wall does not get cloned the way bark does -- cloning is what you
    do when the drawing is shorter than the surface. Here it is exactly as
    tall as the surface, and the band folds up into the vertical plane one row
    per voxel, which is the only mapping that keeps a door a door.

    Returns (voxels, H, wall_rows) where H[x] is the height at each column and
    wall_rows[x] is (top_row, bottom_row) of the drawn wall there.
    """
    h_, w_ = roof.shape
    wall_rows = {}
    for x in range(w_):
        col = np.where(wall[:, x])[0]
        if not len(col):
            continue
        # The front elevation is the BOTTOM-MOST contiguous run of wall in the
        # column, not everything between the first and last wall pixel. The
        # same near-black entry that draws the wall also outlines the roof, so
        # min-to-max makes a one-storey hut as tall as the whole drawing.
        end = int(col[-1])
        start = end
        i = len(col) - 1
        while i > 0 and col[i - 1] == col[i] - 1:
            i -= 1
            start = int(col[i])
        wall_rows[x] = (start, end)
    heights = {x: (r1 - r0 + 1) for x, (r0, r1) in wall_rows.items()}
    if not heights:
        return set(), {}, {}
    med = int(np.median(list(heights.values())))
    if height_hint:
        med = int(height_hint)

    # One height for the building, not one per column. Per-column heights
    # sound better -- gables, porches -- but the measurement is noisy at the
    # edges of the drawing, where a column may catch two pixels of shadow and
    # claim to be a spire. The median across every column that has a wall is
    # the building's storey height, and the roof sits on it flat.
    vox = set()
    for (z, x) in zip(*np.where(roof)):
        for e in range(0, med + 1):
            vox.add((int(x), int(e), int(z)))
    for (z, x) in zip(*np.where(wall)):
        for e in range(0, med + 1):
            vox.add((int(x), int(e), int(z)))

    # ---- the roof's own rise -------------------------------------------
    #
    # Up to here a building is a box: its plan extruded to the storey
    # height the wall measured. That is right for the walls and wrong for
    # the roof, which is the part the player looks down on. A stone house
    # with a flat lid reads as a crate.
    #
    # The rise is MEASURED, the same way the wall height was. Under the
    # camera (see viewangle: drawn = height + depth) the roof's drawn band
    # covers its depth plus whatever it rises, and a roof's depth is about
    # its width. So rows_drawn - width is the rise. Where that comes out at
    # or below zero the roof really is flat -- a terrace, a wall top -- and
    # it stays flat rather than being given an invented pitch.
    rz, rx = np.where(roof)
    if len(rz):
        rows = int(rz.max() - rz.min() + 1)
        wide = int(rx.max() - rx.min() + 1)
        rise = VA.split_drawn(rows, depth=wide)[0]
        if rise > 0:
            rise = min(rise, wide)  # no spires
            # Hip the roof: every course steps in from the eaves, so the
            # ridge lands wherever the plan is furthest from its edge. That
            # needs no guess about which way the building faces -- an
            # L-shaped house gets an L-shaped ridge for free.
            inset = np.full(roof.shape, -1, dtype=int)
            inset[roof] = 0
            cur = roof.copy()
            step = 0
            while cur.any():
                nxt = cur.copy()
                nxt[:-1, :] &= cur[1:, :]
                nxt[1:, :] &= cur[:-1, :]
                nxt[:, :-1] &= cur[:, 1:]
                nxt[:, 1:] &= cur[:, :-1]
                nxt[0, :] = False
                nxt[-1, :] = False
                nxt[:, 0] = False
                nxt[:, -1] = False
                if not nxt.any():
                    break
                step += 1
                inset[nxt] = step
                cur = nxt
            deep = max(1, int(inset.max()))
            for (z, x) in zip(rz, rx):
                lift = int(round(rise * inset[z, x] / float(deep)))
                for e in range(med + 1, med + lift + 1):
                    vox.add((int(x), int(e), int(z)))
            _wall_relief(vox, wall, wall_rows, med, art)
            _PLINTH_LAST[0] = _plinth(vox, wall_rows, med, art, matsub)
            return (vox, {x: med for x in wall_rows}, wall_rows,
                    {(int(x), int(z)): med + int(round(rise * inset[z, x]
                                                       / float(deep)))
                     for z, x in zip(rz, rx)}, rise)
    _wall_relief(vox, wall, wall_rows, med, art)
    _PLINTH_LAST[0] = _plinth(vox, wall_rows, med, art, matsub)
    return vox, {x: med for x in wall_rows}, wall_rows, {}, 0


# --------------------------------------------------------------- recipes ---
# What a thing is called says what shape it is. These are the priors, one
# line each, matched against the decomp's own name for the entity or the name
# given on the command line. Nothing here is measured -- the sizes all come
# from the drawing -- but WHICH profile to trace toward is a judgement, and
# this is where those judgements live so they can be argued with.
# Things that are not world geometry at all. An enemy, a projectile, a puff of
# smoke or an invisible spawner has no business being frozen into the voxel
# world: they move, they die, they are drawn by the engine every frame. Listing
# them keeps the coverage number honest -- "164 objects have no recipe" is
# alarming until you notice most of them are skulls and sparks.
ACTOR_PATTERNS = (
    r"PROJECTILE|_FX\b|SPECIAL_FX|FLAME|FIRE|SPARK|SMOKE|DUST|EXPLOSION",
    r"CLOUD|WHIRLWIND|WIND|VORTEX|SPLASH|BUBBLE|RIPPLE",
    r"SPAWNER|CAMERA_TARGET|MANAGER|CUTSCENE|ORCHESTRATOR|WARP_POINT",
    r"MINISH\b|TOWN_MINISH|FOREST_MINISH|TPWNSPERSON|TOWNSPERSON",
    r"SKULL|CHUCHU|OCTOROK|MOBLIN|DARK_NUT|ARMOS|GHINI|EYEGORE|BOBOMB",
    r"GROUND_ITEM|SHOP_ITEM|LINK_|PLAYER_|EZLO|FAIRY|BIRD|CAT|DOG|COW",
)


def is_actor(name):
    return bool(name) and any(re.search(p, name, re.I) for p in ACTOR_PATTERNS)


SHAPE_RECIPES = [
    (r"STUMP|LOG\b",               "stump",    dict(height=14, top_frac=0.25)),
    (r"TREE|BUSH|FOLIAGE|LEAF|CROWN|HEDGE",
                                    "dome",     dict(height=20)),
    (r"POT|JAR|BARREL|VASE|BUCKET", "cylinder", dict(height=14)),
    (r"ROCK|BOULDER|STATUE|GRAVE|PILLAR|CREST|TABLET",
                                    "taper",    dict(height=16)),
    (r"SIGN|BOARD|DOOR|GATE|FENCE", "slab",     dict(height=20)),
    (r"HOUSE|SHOP|HUT|CABIN|TOWER|WALL|ROOF|BUILDING",
                                    "building", dict()),
    (r"ARCHWAY",                    "slab",     dict(height=28)),
    (r"STAIR|STEPS",                "steps",    dict()),
    (r"MUSHROOM",                   "dome",     dict(height=16)),
    (r"FURNITURE|CHEST|BOOKSHELF|TABLE|BED|OVEN|CABIN",
                                    "taper",    dict(height=16)),
    (r"BOLLARD|LEVER|SWITCH|BELL|BEANSTALK|STALK",
                                    "cylinder", dict(height=18)),
    (r"BLOCK|PLATFORM|LEDGE|RAILTRACK|LADDER|MINECART",
                                    "slab",     dict(height=16)),
    (r"DECORATION|ORNAMENT|BANNER|CURTAIN|STATUE",
                                    "taper",    dict(height=24)),
    (r"ENTRANCE|OPENING|PORTAL_STONE",
                                    "slab",     dict(height=20)),
]


def recipe_for(name):
    """(mode, kwargs, why) for a decomp name, or None if nothing fits."""
    if not name:
        return None
    for pat, mode, kw in SHAPE_RECIPES:
        if re.search(pat, name, re.I):
            return mode, kw, pat
    return None


def cmd_recipes(args):
    """Show which profile each catalogued name would be fitted with."""
    names = []
    if args.catalogue and Path(args.catalogue).exists():
        for line in Path(args.catalogue).read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            f = line.split()
            if len(f) >= 4:
                names.append(f[3])
    names = sorted(set(n for n in names if n and n != "?"))
    if not names:
        sys.exit("pass --catalogue objects/entities.txt")
    hit = miss = actors = 0
    for n in names:
        if is_actor(n):
            actors += 1
            if args.actors:
                print(f"  {n:<34} -- actor, not scenery")
            continue
        r = recipe_for(n)
        if r:
            hit += 1
            if args.verbose:
                print(f"  {n:<34} {r[0]:<9} (matched /{r[2]}/)")
        else:
            miss += 1
            if args.unmatched:
                print(f"  {n:<34} -- no recipe")
    total = hit + miss
    pct = 100.0 * hit / max(1, total)
    print(f"\n  {len(names)} distinct names: {actors} are actors (drawn live, "
          f"never voxelised)")
    print(f"  of the {total} scenery names, {hit} have a shape recipe "
          f"({pct:.0f}%), {miss} do not")
    print("  A scenery name with no recipe is not a failure: it means nobody")
    print("  has said what shape that name implies yet, and guessing one is")
    print("  how a pillar became a tree stump.")


def write_placement(out, r, x0, y0, x1, y1, mode, centred):
    """Record where a fitted object belongs, so a room can be assembled.

    A fit is solved in the little coordinate system of its own rect, and for
    the upright modes depth is centred on zero because the drawing never says
    where the back of the object is. Neither fact survives into the OBJ, so a
    scene built from several fits would stack them all at the origin. This is
    the missing half: rect, room origin, and whether depth was centred.
    """
    import json
    meta = {"room": getattr(r, "room", 0), "area": getattr(r, "area", 0),
            "origin_x": int(r.origin_x), "origin_y": int(r.origin_y),
            "x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1),
            "mode": mode, "depth_centred": bool(centred)}
    Path(str(out) + ".place.json").write_text(json.dumps(meta, indent=1))


def _fit_in_process(argv):
    """Run `shapefit.py fit <argv>` inside this process.

    Returns (returncode, stdout, stderr), as subprocess.run would, so
    cmd_scene reports a failure exactly as it did when every fit was its own
    process. sys.argv is set for the duration because cmd_fit reads it to
    tell an explicit --height from the default.
    """
    import contextlib
    import io
    import traceback
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv
    sys.argv = [str(Path(__file__).resolve()), "fit"] + list(argv)
    rc = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            a = build_parser().parse_args(sys.argv[1:])
            a.func(a)
    except SystemExit as e:
        if isinstance(e.code, int):
            rc = e.code
        elif e.code is not None:
            err.write(f"{e.code}\n")
            rc = 1
    except Exception:
        err.write(traceback.format_exc())
        rc = 1
    finally:
        sys.argv = saved
    return rc, out.getvalue(), err.getvalue()


def cmd_scene(args):
    """Assemble a room: terrain plus every object fitted in its own right.

    The world generator extrudes the tile grid, which is correct for ground
    and hopeless for anything standing on it -- a house becomes a block, a
    tree becomes a prism. The object fitters solve those properly but each
    one lands at its own origin. This puts them back where they came from.

    The manifest is authored on purpose. Naming which rect is a house and
    which is a tree is the one thing the dump cannot tell us, and guessing it
    is what turned a pillar into a tree stump three times over.

      # room                cells          mode     options
      room_00_00.tmcr  40,0,49,10   upright  materials=113,114,112,193 cap=0.28
    """
    import json
    import subprocess
    tmp = Path(args.out).parent / "_scene_parts"
    tmp.mkdir(parents=True, exist_ok=True)
    entries = []
    for ln, line in enumerate(Path(args.manifest).read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        f = line.split()
        if len(f) < 3:
            print(f"  line {ln}: need at least <room> <cells> <mode>")
            continue
        entries.append((ln, f[0], f[1], f[2], f[3:]))
    if not entries:
        sys.exit(f"no entries in {args.manifest}")

    KEYMAP = {"materials": "--materials", "wall": "--wall-materials",
              "cap": "--cap-frac", "foot": "--foot-frac", "relief": "--relief",
              "features": "--features", "fdepth": "--feature-depth",
              "height": "--height", "squash": "--squash", "dome": "--dome",
              "vscale": "--vscale", "outline": "--keep-outline",
              "section": "--section", "lid": "--lid-frac",
              "ground": "--ground-frac", "riser": "--riser",
              "layer": "--layer"}
    verts, faces, ok, bad = [], [], 0, 0
    jobs = []
    for ln, room, cells, mode, opts in entries:
        part = tmp / f"part{ln:03d}.obj"
        cmd = [str(Path(args.rooms) / room), "--cells", cells,
               "--mode", mode, "--out", str(part), "--place"]
        for o in opts:
            if "=" not in o:
                continue
            k, v = o.split("=", 1)
            if k in KEYMAP:
                cmd += [KEYMAP[k], v]
        jobs.append(cmd)

    # Fit in worker processes, each reused for many objects, rather than one
    # fresh interpreter per object. Results come back in manifest order, so
    # the merged scene is the same whatever --jobs is.
    from multiprocessing import Pool
    bar = RE.Progress(len(entries), "scene")
    if args.jobs > 1 and len(jobs) > 1:
        pool = Pool(min(args.jobs, len(jobs)))
        results = pool.imap(_fit_in_process, jobs)
    else:
        pool, results = None, map(_fit_in_process, jobs)
    for (ln, room, cells, mode, opts), (rc, so, se) in zip(entries, results):
        part = tmp / f"part{ln:03d}.obj"
        meta_p = Path(str(part) + ".place.json")
        if rc != 0 or not part.exists() or not meta_p.exists():
            bad += 1
            bar.update(1, f"line {ln} failed")
            print(f"  line {ln} ({room} {cells} {mode}) failed: "
                  f"{(se or so).strip().splitlines()[-1:] or ['?']}")
            continue
        meta = json.loads(meta_p.read_text())
        # Back into room pixels: the rect's own origin, plus the room's.
        ox = meta["origin_x"] + meta["x0"]
        oz = meta["origin_y"] + meta["y0"]
        if meta["depth_centred"]:
            oz += (meta["y1"] - meta["y0"]) // 2
        n0 = len(verts)
        for l in part.read_text().splitlines():
            q = l.split()
            if not q:
                continue
            if q[0] == "v":
                verts.append((float(q[1]) + ox, float(q[2]), float(q[3]) + oz,
                              q[4:]))
            elif q[0] == "f":
                faces.append([int(t.split("/")[0]) + n0 for t in q[1:]])
        ok += 1
        bar.update(1, f"{ok} placed")
    bar.done()
    if pool is not None:
        pool.close()
        pool.join()

    # Terrain last, and unshifted: room_explore writes it in room pixels
    # already, origin included, so adding an offset here would slide the
    # ground out from under everything standing on it.
    if args.terrain:
        tpath = tmp / "terrain.obj"
        tc = [sys.executable, str(Path(__file__).resolve().parent / "room_explore.py"),
              "voxel", str(Path(args.rooms) / args.terrain), "--out", str(tpath)]
        if args.overlay:
            tc.append("--overlay")
        if args.subdiv > 1:
            tc += ["--subdiv", str(args.subdiv)]
        tp = subprocess.run(tc, capture_output=True, text=True)
        if tpath.exists():
            n0 = len(verts)
            nt = 0
            for l in tpath.read_text().splitlines():
                q = l.split()
                if not q:
                    continue
                if q[0] == "v":
                    verts.append((float(q[1]), float(q[2]), float(q[3]), q[4:]))
                elif q[0] == "f":
                    faces.append([int(t.split("/")[0]) + n0 for t in q[1:]])
                    nt += 1
            print(f"  terrain: {nt} faces from {args.terrain}")
        else:
            print(f"  terrain FAILED: "
                  f"{(tp.stderr or tp.stdout).strip().splitlines()[-1:] or ['?']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write("# room scene: terrain plus objects, each fitted in its own "
                 "right and returned to its place\n")
        fh.write("# Derived from the user's own ROM; regenerate, do not "
                 "redistribute.\n")
        for v in verts:
            fh.write("v %g %g %g%s\n" % (v[0], v[1], v[2],
                                          ("" if not v[3] else " " + " ".join(v[3]))))
        for f_ in faces:
            fh.write("f " + " ".join(str(i) for i in f_) + "\n")
    print(f"  {ok} object(s) placed, {bad} failed -> {out}")
    print(f"  {len(verts)} vertices, {len(faces)} faces")
    if bad:
        print("  A failed line is usually the wrong --cells or the wrong "
              "materials; fit it on its own to see the error in full.")


def find_fittings(mask, mat_sub, art_sub, feat_ids, base, min_frac=0.15):
    """Sort things drawn on a surface into projections and flush detail.

    A tube standing out of a surface is seen END ON, so it draws about as tall
    as it is wide. Anything flush sits on a face the camera foreshortens and
    draws much wider than tall. That ratio is the test.

    Two corrections the drawing forces. What a fitting is ringed by says which
    surface it is on -- but every fitting has its own dark outline, so the
    nearest ring is always that stroke and the question has to be asked three
    pixels out. And the size floor is relative: a fleck of trim read as a
    fourth chimney next to stacks four times its area.
    """
    fm = mask & np.isin(mat_sub, list(feat_ids))
    rest = mask & ~fm
    if not fm.any() or not rest.any():
        return [], 0
    vals, cnts = np.unique(mat_sub[rest], return_counts=True)
    skin = int(vals[int(np.argmax(cnts))])
    outline_m = dark_outline(art_sub, mask)
    out = []
    for size, bys, bxs in _blobs(fm):
        if size < 20:
            continue
        hgt = int(bys.max() - bys.min() + 1)
        wid = int(bxs.max() - bxs.min() + 1)
        asp = wid / max(1.0, hgt)
        blob = np.zeros_like(mask)
        blob[bys, bxs] = True
        grown = blob.copy()
        for _ in range(3):
            g2 = grown.copy()
            g2[1:] |= grown[:-1]; g2[:-1] |= grown[1:]
            g2[:, 1:] |= grown[:, :-1]; g2[:, :-1] |= grown[:, 1:]
            grown = g2
        ring_ = grown & ~blob & mask & ~outline_m
        host = 0
        if ring_.any():
            hv, hc = np.unique(mat_sub[ring_], return_counts=True)
            host = int(hv[int(np.argmax(hc))])
        kind = ("ground" if bys.max() >= base - 1 else
                "project" if (asp <= 1.45 and host == skin) else
                "fitting" if asp <= 1.45 else "flush")
        out.append({"kind": kind, "y0": int(bys.min()), "y1": int(bys.max()),
                    "x0": int(bxs.min()), "x1": int(bxs.max()),
                    "w": wid, "h": hgt, "asp": asp, "host": host,
                    "area": wid * hgt, "ys": bys, "xs": bxs})
    areas = [f["area"] for f in out if f["kind"] == "project"]
    if areas:
        floor_ = max(areas) * min_frac
        out = [f for f in out
               if f["kind"] != "project" or f["area"] >= floor_]
    return out, skin


def object_blobs(mat_sub, art_sub, min_px=600, max_px=20000, ground_take=3):
    """Every sizeable thing in a room that is not the ground it stands on.

    Ground is the handful of materials that cover the most of the room --
    grass, dirt, floor, water. Everything else is something placed on it. That
    is crude, and deliberately so: the point is to find candidates to MEASURE,
    not to decide what they are. Deciding from a guess is what produced a
    pillar called a tree stump.
    """
    vals, cnts = np.unique(mat_sub, return_counts=True)
    order = np.argsort(-cnts)
    ground = {int(vals[i]) for i in order[:ground_take]}
    fg = ~np.isin(mat_sub, list(ground))
    out = []
    for size, bys, bxs in _blobs(fg):
        if not (min_px <= size <= max_px):
            continue
        h = int(bys.max() - bys.min() + 1)
        w = int(bxs.max() - bxs.min() + 1)
        if h < 24 or w < 16:
            continue
        out.append((size, int(bys.min()), int(bys.max()),
                    int(bxs.min()), int(bxs.max()), w, h))
    return out, ground


def measure_blob(mask):
    """Silhouette readings for one object: width per row, and what it implies."""
    ys = np.where(mask.any(axis=1))[0]
    if not len(ys):
        return None
    top, base = int(ys.min()), int(ys.max())
    span = np.array([
        (int(np.where(mask[y])[0].max()) - int(np.where(mask[y])[0].min()) + 1)
        if mask[y].any() else 0 for y in range(top, base + 1)])
    wmax = int(span.max()) if len(span) else 0
    if wmax <= 0:
        return None
    widest = top + int(np.argmax(span))
    below = span[widest - top:]
    narrow = int(below.min()) if len(below) > 2 else wmax
    thresh = wmax * 0.98
    hit = np.where(span >= thresh)[0]
    shoulder = top + int(hit[0]) if len(hit) else top
    return {"top": top, "base": base, "h": base - top + 1, "w": wmax,
            "widest": widest, "narrow": narrow,
            "overhang": narrow / float(wmax), "shoulder": shoulder,
            "shoulder_frac": (shoulder - top) / max(1.0, base - top)}


def _survey_room(path):
    try:
        r = RE.load_room(Path(path))
        d = SH.decompose_room(r, 0)
        if d is None:
            return []
        mat = d[0]
        art = RE.room_art_rgb(r, 0)
    except Exception:
        return []
    blobs, ground = object_blobs(mat, art)
    rows = []
    for size, y0, y1, x0, x1, w, h in blobs:
        sub = mat[y0:y1 + 1, x0:x1 + 1]
        m = ~np.isin(sub, list(ground))
        m = largest_blob(m)
        g = measure_blob(m)
        if g is None:
            continue
        mats = sorted(set(int(v) for v in sub[m].ravel()))
        if len(mats) > 8:
            # Too many palettes for one object: this is a patch of scenery,
            # not a built thing.
            continue
        kind = "overhang" if g["overhang"] < 0.55 else "solid"
        rows.append({"room": Path(path).name, "size": int(size),
                     "cells": f"{x0 // 16},{y0 // 16},{x1 // 16 + 1},{y1 // 16 + 1}",
                     "w": g["w"], "h": g["h"], "overhang": g["overhang"],
                     "shoulder_frac": g["shoulder_frac"], "kind": kind,
                     "materials": ",".join(str(v) for v in mats)})
    return rows


def cmd_survey(args):
    """Find every built thing in the world and measure its silhouette.

    The mushroom house was worked out one object at a time. This finds the
    others: anything sizeable that is not ground, measured the same way, so a
    fit can be attempted on all of them at once instead of hand-writing a rect
    per building.
    """
    from multiprocessing import Pool
    files = sorted(Path(args.dir).glob("room_*.tmcr"))
    if not files:
        sys.exit(f"no room_*.tmcr under {args.dir}")
    rows = []
    bar = RE.Progress(len(files), "survey")
    with Pool(args.jobs) as pool:
        for got in pool.imap_unordered(_survey_room, [str(f) for f in files]):
            rows += got
            bar.update(1, f"{len(rows)} objects")
    bar.done()
    if args.kind:
        rows = [r for r in rows if r["kind"] == args.kind]
    rows.sort(key=lambda r: -r["size"])
    if args.limit:
        rows = rows[:args.limit]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write("# Built things found across the world, largest first.\n")
        fh.write("# overhang = narrowest width below the widest row, as a\n")
        fh.write("# fraction. Under 0.55 the top is much wider than what\n")
        fh.write("# holds it up -- a mushroom or a parasol. At or above it\n")
        fh.write("# the body carries its own width down -- a cottage, a rock.\n")
        fh.write("#\n")
        fh.write("# This file IS a scene manifest: feed it to `scene`.\n")
        fh.write("# room              cells        mode     options\n")
        for r in rows:
            fh.write(f"{r['room']}  {r['cells']}  upright  "
                     f"materials={r['materials']}"
                     f"   # {r['w']}x{r['h']}px {r['kind']} "
                     f"overhang={r['overhang']:.2f}\n")
    n_over = sum(1 for r in rows if r["kind"] == "overhang")
    print(f"  {len(rows)} object(s) in {len(files)} room(s) -> {out}")
    print(f"    {n_over} with an overhang (mushroom-like), "
          f"{len(rows) - n_over} solid (cottage-like)")
    print("  Every line is a CANDIDATE, not a building. Look at the sheet")
    print("  before fitting: a boulder and a hut measure much the same.")


def _tile_pattern(r, cx0, cy0, cx1, cy1, layer=0):
    t = r.layers[layer]["tile"]
    return np.array(t[cy0:cy1, cx0:cx1], dtype=np.int32)


def _find_like(argstuple):
    path, pat, minfrac = argstuple
    try:
        r = RE.load_room(Path(path))
    except Exception:
        return []
    out = []
    for layer in (0, 1):
        if layer >= len(r.layers) or not r.layers[layer]["present"]:
            continue
        t = np.array(r.layers[layer]["tile"][:r.cells_h, :r.cells_w],
                     dtype=np.int32)
        ph, pw = pat.shape
        if t.shape[0] < ph or t.shape[1] < pw:
            continue
        nz = pat != 0
        need = int(nz.sum())
        if need == 0:
            continue
        for y in range(t.shape[0] - ph + 1):
            for x in range(t.shape[1] - pw + 1):
                win = t[y:y + ph, x:x + pw]
                same = int(((win == pat) & nz).sum())
                if same >= need * minfrac:
                    out.append((Path(path).name, layer, x, y,
                                same / float(need)))
    return out


def cmd_findlike(args):
    """Find every other instance of ONE building, by its tiles.

    Blob detection does not work for this. In a tilemap everything that is
    not ground touches everything else -- a house runs into the cliff behind
    it, which runs into the path, which runs into the next house -- and the
    Minish Woods room came back as a single object of 229,900 pixels.

    A building is not a blob, it is an ARRANGEMENT OF TILES, and that is
    exact. Take the tile indices of one you understand and look for the same
    arrangement everywhere else. Zero tiles are treated as "don't care", so a
    rect drawn loosely around a house still matches.
    """
    from multiprocessing import Pool
    r = RE.load_room(Path(args.path))
    cx0, cy0, cx1, cy1 = (int(v) for v in args.cells.split(","))
    pat = _tile_pattern(r, cx0, cy0, cx1, cy1, args.layer)
    if args.materials:
        # Only the object's own tiles are the pattern. A rect drawn round a
        # building also contains the ground it stands on, and that ground is
        # different everywhere else -- so matching on the whole rect finds the
        # building only in the exact spot it was copied from. Two identical
        # stumps in one room came back as one.
        d = SH.decompose_room(r, args.layer)
        if d is not None:
            want = {int(v) for v in args.materials.split(",")}
            mt = d[0]
            for cy in range(cy1 - cy0):
                for cx in range(cx1 - cx0):
                    px = mt[(cy0 + cy) * 16:(cy0 + cy + 1) * 16,
                            (cx0 + cx) * 16:(cx0 + cx + 1) * 16]
                    if not np.isin(px, list(want)).any():
                        pat[cy, cx] = 0
    nz = int((pat != 0).sum())
    print(f"  pattern from {Path(args.path).name} cells {args.cells}: "
          f"{pat.shape[1]}x{pat.shape[0]} cells, {nz} non-empty tiles")
    if nz < 4:
        sys.exit("that rect has almost no tiles in it -- check --cells/--layer")
    files = sorted(Path(args.dir).glob("room_*.tmcr"))
    hits = []
    bar = RE.Progress(len(files), "findlike")
    with Pool(args.jobs) as pool:
        for got in pool.imap_unordered(
                _find_like, [(str(f), pat, args.min_match) for f in files]):
            hits += got
            bar.update(1, f"{len(hits)} hits")
    bar.done()
    hits.sort(key=lambda h: (-h[4], h[0]))

    # Material ids are PER AREA. The same stump is 209/145/208 in Minish Woods
    # and something else entirely two areas over, so copying the source room's
    # ids onto every hit produced eight manifest lines that fitted nothing.
    # Each hit gets the ids actually present where it was found.
    mats_for = {}
    for name in sorted({h[0] for h in hits}):
        try:
            rr = RE.load_room(Path(args.dir) / name)
            dd = SH.decompose_room(rr, 0)
            if dd is None:
                continue
            mt = dd[0]
            vals, cnts = np.unique(mt[:rr.cells_h * 16, :rr.cells_w * 16],
                                   return_counts=True)
            order = np.argsort(-cnts)
            mats_for[name] = (mt, {int(vals[i]) for i in order[:3]})
        except Exception:
            pass
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ph, pw = pat.shape
    with out.open("w") as fh:
        fh.write(f"# every instance of the building at "
                 f"{Path(args.path).name} {args.cells}\n")
        fh.write("# matched on TILE INDICES, not pixels: a building is an\n")
        fh.write("# arrangement of tiles, and that is exact. Blobs are not --\n")
        fh.write("# in a tilemap everything not-ground touches everything.\n")
        fh.write("#\n# This file IS a scene manifest: feed it to `scene`.\n")
        fh.write("# room              cells        mode     options\n")
        for name, layer, x, y, frac in hits:
            mats = args.materials
            got = mats_for.get(name)
            if got is not None:
                mt, ground = got
                px = mt[y * 16:(y + ph) * 16, x * 16:(x + pw) * 16]
                here = sorted(set(int(v) for v in px.ravel()) - ground)
                if here:
                    mats = ",".join(str(v) for v in here)
            fh.write(f"{name}  {x},{y},{x + pw},{y + ph}  {args.mode}  "
                     f"materials={mats}{args.extra and ' ' + args.extra or ''}"
                     f"   # match {frac:.0%} layer {layer}\n")
    exact = sum(1 for h in hits if h[4] >= 0.999)
    print(f"  {len(hits)} instance(s) in {len(files)} room(s) -> {out}")
    print(f"    {exact} exact, {len(hits) - exact} partial "
          f"(>= {args.min_match:.0%} of the pattern's tiles)")
    rooms = sorted({h[0] for h in hits})
    print(f"    across {len(rooms)} room(s): "
          + ", ".join(rooms[:8]) + (" ..." if len(rooms) > 8 else ""))


def _tiles_room(argstuple):
    path, names = argstuple
    try:
        r = RE.load_room(Path(path))
    except Exception:
        return []
    out = []
    for layer in (0, 1):
        if layer >= len(r.layers) or not r.layers[layer]["present"]:
            continue
        L = r.layers[layer]
        tt = L["tiletype"]
        t = L["tile"][:r.cells_h, :r.cells_w]
        idx = np.clip(t.astype(np.int64), 0, len(tt) - 1)
        types = tt[idx]
        for v in np.unique(types):
            v = int(v)
            if v not in names:
                continue
            ys, xs = np.where(types == v)
            # Group the cells of one type into contiguous clumps: two chests
            # in a room are two objects, not one entry.
            m = np.zeros(types.shape, bool)
            m[ys, xs] = True
            for size, bys, bxs in _blobs(m):
                out.append((Path(path).name, layer, v, names[v], int(size),
                            int(bxs.min()), int(bys.min()),
                            int(bxs.max()) + 1, int(bys.max()) + 1))
    return out


def cmd_tiles(args):
    """Find everything the decomp NAMES, across every room.

    This is the starting point that should have come first. The project
    already knows what a great deal of the world is: include/tiles.h labels
    219 tile types -- ROCK, CHEST, STAIRS_UP, SIGNPOST, Pots, Boulder,
    Beanstalk/Ladder -- and every cell carries a tile type. That is a named
    object list for the whole game, sitting in the repo.

    Everything I built before this inferred objects from palette ids instead,
    which are per-area and meant nothing two areas over.
    """
    from multiprocessing import Pool
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from decomp_labels import Labels, find_repo
    repo = Path(args.repo) if args.repo else find_repo()
    if repo is None:
        sys.exit("could not find the decomp root (no include/tiles.h above here)")
    L = Labels(repo)
    if not L.tiles:
        sys.exit(f"no tile names parsed from {repo}/include/tiles.h")
    names = dict(L.tiles)
    if not args.raw:
        # Not every comment in tiles.h is an object name. Many describe
        # BEHAVIOUR -- the handler that runs, the surface flag it sets, the
        # enemy that walks on it -- and those say nothing about what is drawn
        # there. Keep the ones that name a thing.
        def is_thing(n):
            if re.search(r"sub_0[0-9A-Fa-f]{7}|->|TILE_ACT_|SURFACE_|_Main|"
                         r"Init\(|Update|Action[0-9]", n):
                return False
            return bool(re.match(r"^[A-Z][A-Za-z0-9_/ ()'-]*$", n))
        names = {v: n for v, n in names.items() if is_thing(n)}
    if args.grep:
        rx = re.compile(args.grep, re.I)
        names = {v: n for v, n in names.items() if rx.search(n)}
        if not names:
            sys.exit(f"no tile name matches /{args.grep}/")
    print(f"  {len(names)} named tile type(s) from {repo}/include/tiles.h")

    files = sorted(Path(args.dir).glob("room_*.tmcr"))
    rows = []
    bar = RE.Progress(len(files), "tiles")
    with Pool(args.jobs) as pool:
        for got in pool.imap_unordered(
                _tiles_room, [(str(f), names) for f in files]):
            rows += got
            bar.update(1, f"{len(rows)} found")
    bar.done()
    rows = [r for r in rows if r[4] >= args.min_cells]
    if args.manifest:
        # Resolve each find's own materials. Ids are per-area, so the set that
        # describes a chest in Minish Woods describes nothing in the Temple of
        # Droplets -- copying one room's ids across the world is what made
        # eight stump lines fit almost nothing.
        man = Path(args.manifest)
        man.parent.mkdir(parents=True, exist_ok=True)
        cache = {}
        kept = 0
        with man.open("w") as fh:
            fh.write("# Fit manifest for everything tiles.h names here.\n")
            fh.write("# Materials are resolved PER FIND: ids are per-area.\n")
            fh.write("# room              cells        mode     options\n")
            for name, layer, v, nm, size, x0, y0, x1, y1 in sorted(rows):
                if name not in cache:
                    cache.clear()
                    try:
                        rr = RE.load_room(Path(args.dir) / name)
                        dd = SH.decompose_room(rr, layer)
                        if dd is None:
                            continue
                        mt = dd[0]
                        vals, cnts = np.unique(
                            mt[:rr.cells_h * 16, :rr.cells_w * 16],
                            return_counts=True)
                        order = np.argsort(-cnts)
                        cache[name] = (mt, {int(vals[i]) for i in order[:4]})
                    except Exception:
                        continue
                if name not in cache:
                    continue
                mt, ground = cache[name]
                px = mt[y0 * 16:y1 * 16, x0 * 16:x1 * 16]
                here = sorted(set(int(q) for q in px.ravel()) - ground)
                if not here:
                    continue
                pad = args.pad
                fh.write(f"{name}  {max(0, x0 - pad)},{max(0, y0 - pad)},"
                         f"{x1 + pad},{y1 + pad}  {args.mode}  "
                         f"materials={','.join(str(q) for q in here)}"
                         f"{args.extra and ' ' + args.extra or ''}"
                         f"   # {nm}\n")
                kept += 1
        print(f"  {kept} fit line(s) -> {man}")
    from collections import Counter
    tally = Counter(r[3] for r in rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write("# Everything include/tiles.h gives a name to, found in the\n")
        fh.write("# world. The name is the decomp's own comment on that tile\n")
        fh.write("# type -- not a guess from colour, which is per-area.\n")
        fh.write("#\n# room  cells  layer  type  name  cells_in_clump\n")
        for name, layer, v, nm, size, x0, y0, x1, y1 in sorted(rows):
            fh.write(f"{name}  {x0},{y0},{x1},{y1}  L{layer}  "
                     f"0x{v:04x}  {nm}  ({size} cells)\n")
    print(f"  {len(rows)} clump(s) in {len(files)} room(s) -> {out}")
    print()
    print("  most common named things in the world:")
    for nm, n in tally.most_common(args.top):
        print(f"    {n:5d}  {nm}")


def build_chest(mask, lid_frac=0.42, squash=100):
    """A chest: flat faces, a barrel lid, and no arguing with the silhouette.

    At 16x16 the sprite is too small to carry its own lid curvature -- the
    measured shoulder put two rows in the lid, which collapses the arc to
    nothing, and forcing a bigger band just domed the whole box. But a chest
    is not an unknown shape. It is a box with a half cylinder lying on top,
    axis left to right, which is why its planks run left to right. There are
    197 identical ones in the game.

    The lid is built in VOXEL space and the sprite sampled back from it. The
    first attempt walked the seven drawn lid rows and placed one voxel column
    per row, which is the stretching mistake in another costume: seven samples
    cannot describe a sixteen-voxel arc, and it rendered as a ziggurat. Here
    every voxel of the arc asks which row it came from, so the planks land at
    their own spacing and the curve is a curve.

    Returns (voxels, lid_row, rowof, depth) where rowof maps (x, e, z) to the
    source row that voxel's colour comes from.
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return set(), 0, {}, 0
    top, base = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    W = x1 - x0 + 1
    D = max(2, int(round(W * squash / 100.0)))
    half = D // 2
    lid_row = top + max(1, int(round((base - top + 1) * lid_frac)))
    body_h = base - lid_row + 1
    vox, rowof = set(), {}

    # Body: flat faces, square in plan. Each drawn row is one course, and
    # every voxel in that course carries that row's colour.
    for y in range(lid_row, base + 1):
        e = base - y
        for x in range(x0, x1 + 1):
            if not mask[y, x]:
                continue
            for z in range(-half, D - half):
                vox.add((x, e, z))
                rowof[(x, e, z)] = y

    # Lid: a circular SEGMENT lying left-to-right -- an arch of chord D and
    # rise taken from the drawn lid band, not a half cylinder. Forcing the
    # radius to half the depth makes the lid as tall as the body and leaves
    # a huge upward-facing quadrant that only the last two drawn rows can
    # colour, which is the smearing again. The rise is measured, so the lid
    # is as shallow as the sprite says it is.
    band = max(1, lid_row - top)
    rise = max(1, band)
    R = (rise * rise + half * half) / (2.0 * rise)   # chord 2*half, rise
    cz = 0.0
    ce = rise - R                                    # centre sits below the deck
    lid_cols = {x for x in range(x0, x1 + 1) if mask[top:lid_row, x].any()}
    e0 = body_h - 1                       # the lid sits on the top course
    # Row comes from where the voxel PROJECTS in the drawing, not from its
    # angle: the sprite is a projection, so equal angles are not equal rows.
    # Along a shallow arch that projection is dominated by depth, and +Z is
    # SOUTH (see write_obj's header) -- the first lid row drawn is the BACK
    # edge at -z, the last is the near edge at +z above the front face.
    for x in sorted(lid_cols):
        for dz in range(-half, half + 1):
            for de in range(0, rise + 1):
                if dz * dz + (de - ce) ** 2 > R * R:
                    continue
                vox.add((x, e0 + de, dz))
                t = (dz + half) / float(2 * half) if half else 0.0
                row = top + int(round(t * (band - 1)))
                rowof[(x, e0 + de, dz)] = min(max(row, top), lid_row - 1)
    return vox, lid_row, rowof, D


def split_cap_body(mask, sub_mat):
    """Where does 'seen from above' stop and 'front elevation' begin?

    A torch, a chest and a brazier are all the same animal: a band drawn
    from above sitting over a band drawn as a front face. The chest needed
    a --lid-frac knob to find that seam, which is a guess. Here the drawing
    says it outright -- whatever reaches the BOTTOM of the tile is the thing
    standing on the ground (the stone, the wood), and whatever does not is
    what sits in or on it (the fire, the orb, the lid's deck). The seam is
    the last row of that content.

    Returns (split_row, content_mask) or (None, None) when the tile is all
    structure, which is the honest answer for an unlit slab.
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return None, None
    top, base = int(ys.min()), int(ys.max())
    floor = mask[base]
    if not floor.any():
        return None, None
    grounded = {int(m) for m in np.unique(sub_mat[base][floor])}
    content = mask & ~np.isin(sub_mat, list(grounded))
    if content.sum() < 12:
        return None, None
    # What sits in the bowl is ONE thing. A brazier draws the gaps between
    # its legs in the same ramp as its fire, and those stray pixels sit
    # near the tile floor -- taking the bbox of every non-stone pixel put
    # the seam two rows off the ground and left the pedestal 2px tall.
    content = largest_blob(content)
    if content.sum() < 12:
        return None, None
    cys = np.where(content.any(axis=1))[0]
    return int(cys.max()), content


def fire_fraction(sub_art):
    """How much of this tile is on fire?

    Tile type cannot tell a torch from a button: tiles.h names 0x76 TORCH
    and 0x77 TORCH_LIT, but 0x77 is a floor BUTTON -- the names describe
    what a tile DOES, not what it looks like. The art separates them
    cleanly. Fire is saturated and bright; stone, metal and the pale disc
    of a button are neither. Measured over the 31 distinct variants in the
    game: torches 0.17..0.57, buttons exactly 0.00, no overlap.

    Saturation rather than hue, because some torches burn green.
    """
    a = sub_art[:, :, :3].astype(float)
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    return float(((sat > 0.45) & (mx > 110)).mean())


def flame_material(content, sub_mat, sub_art):
    """Fire is drawn in two ramps: a bright one that fills the bowl and a
    darker, more saturated one that draws the tongues. The tips belong to
    the second -- counting peaks on the whole fire finds the glow's flat
    top edge, not the flames."""
    ids = [int(m) for m in np.unique(sub_mat[content])]
    if len(ids) < 2:
        return content
    lum = {m: float(sub_art[content & (sub_mat == m)][:, :3].mean())
           for m in ids}
    return content & (sub_mat == min(lum, key=lum.get))


def flame_tips(fm):
    """How many teardrops? As many as the flame has tips.

    A tip is a high point on the top edge of the flame ramp. The ends count
    too -- a flame's outer tongues sit at the edge of the silhouette and
    never have a neighbour on one side, so requiring neighbours on both
    sides throws them away and leaves one fat teardrop.
    """
    cols = np.where(fm.any(axis=0))[0]
    if not len(cols):
        return []
    prof = {int(x): int(np.where(fm[:, x])[0].min()) for x in cols}
    o = sorted(prof)
    cand = []
    for i, x in enumerate(o):
        lo = prof[o[i - 1]] if i else None
        hi = prof[o[i + 1]] if i + 1 < len(o) else None
        up_l = (lo is None) or prof[x] < lo
        up_r = (hi is None) or prof[x] < hi
        eq_l = (lo is not None) and prof[x] == lo
        eq_r = (hi is not None) and prof[x] == hi
        if (up_l and (up_r or eq_r)) or ((up_l or eq_l) and up_r):
            cand.append(x)
    keep = []
    for x in sorted(cand, key=lambda v: prof[v]):
        if all(abs(x - k) >= 2 for k in keep):
            keep.append(x)
    return [(float(x), prof[x]) for x in sorted(keep)]


def build_torch(mask, sub_mat, sub_art, squash=100):
    """A torch: a raised bowl-like fireplace with flames standing in it.

    The block is a box of stone or brick; its top is a raised RIM with a
    bowl inside, and the fire is one or more TEARDROPS rising out of that
    bowl. Teardrops are unioned -- a voxel set is a boolean OR, so two
    tongues that overlap merge instead of leaving a seam inside the solid.

    Everything is measured. The stone is whatever reaches the bottom of the
    tile; the fire is what does not. Rows below the fire are the box's front
    elevation at 1:1, rows of fire are the flame's height, and the number of
    tongues is the number of peaks along the top edge of the fire.

    Returns (voxels, seam, rowof, depth).
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return set(), 0, {}, 0
    top, base = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    W = x1 - x0 + 1
    D = max(2, int(round(W * squash / 100.0)))
    half = D // 2
    cxp = (x0 + x1) / 2.0
    seam, content = split_cap_body(mask, sub_mat)
    stone = mask.copy()
    if content is not None:
        stone &= ~content
    vox, rowof = set(), {}

    def put(x, e, z, row):
        vox.add((int(x), int(e), int(z)))
        rowof[(int(x), int(e), int(z))] = (
            (int(row[0]), int(row[1])) if isinstance(row, tuple) else int(row))

    # Colour must come from STONE where stone is meant. Sampling a row that
    # happens to contain flame pixels paints the flame across the whole top
    # face; that is exactly what made the pitched views show an orange
    # stripe, and it was a lid fallback that caused it.
    def stone_row_near(x, y):
        col = np.where(stone[:, int(x)])[0]
        if not len(col):
            col = np.where(stone.any(axis=1))[0]
            if not len(col):
                return int(y)
        return int(col[np.argmin(np.abs(col - y))])

    if content is None or not content.any():
        for y in range(top, base + 1):
            for x in range(x0, x1 + 1):
                if not mask[y, x]:
                    continue
                for z in range(-half, D - half):
                    put(x, base - y, z, y)
        return vox, base, rowof, D

    cy0 = int(np.where(content.any(axis=1))[0].min())
    cy1 = int(np.where(content.any(axis=1))[0].max())
    # The rim is the solid band of stone drawn immediately below the fire --
    # the near lip of the bowl. Using the whole cap band instead made the
    # rim as tall as the flame, so the bowl swallowed it.
    full = 0.7 * W
    rim_h = 0
    for y in range(cy1 + 1, base + 1):
        if stone[y].sum() >= full:
            rim_h += 1
        else:
            break
    rim_h = max(1, rim_h)

    # 1. The box below the fire: its own front elevation, 1:1.
    body_rows = [y for y in range(cy1 + 1, base + 1) if stone[y].any()]
    H_body = len(body_rows)

    # 1. The block: every stone row below the fire is front elevation, 1:1,
    #    so the object keeps the height the sprite gives it. The rim is the
    #    TOP of this block, not a ring stacked on it -- adding it on top ate
    #    the body of any torch with a thick band under its fire and left a
    #    14x6 slab instead of a 14x15 block.
    for i, y in enumerate(body_rows):
        e = H_body - 1 - i
        for x in range(x0, x1 + 1):
            if not mask[y, x]:
                continue
            for z in range(-half, D - half):
                put(x, e, z, y if stone[y, x] else stone_row_near(x, y))

    # 2. Carve the bowl into the top of the block. Square: these are blocks
    #    of stone and brick, not turned bowls, so only the fire is round.
    fx = np.where(content.any(axis=0))[0]
    mx0, mx1 = int(fx.min()), int(fx.max())
    mouth_w = mx1 - mx0 + 1
    mz0 = -(mouth_w // 2)
    mz1 = mz0 + mouth_w - 1
    depth_bowl = max(1, min(rim_h, H_body - 1))
    for k in range(depth_bowl):
        e = H_body - 1 - k
        for x in range(mx0, mx1 + 1):
            for z in range(mz0, mz1 + 1):
                vox.discard((int(x), int(e), int(z)))
                rowof.pop((int(x), int(e), int(z)), None)

    # No glow dome. Laying the bowl's contents out in depth gives every
    # depth slice a different source row, which reads as horizontal stripes
    # from the front -- the fire is the tongues, and nothing else.
    e_floor = max(0, H_body - max(1, min(rim_h, H_body - 1)))

    # The tongues: one teardrop per tip, unioned. A voxel set IS a boolean
    # OR, so tongues that touch merge into one solid with no seam inside.
    fm = flame_material(content, sub_mat, sub_art)
    # The fire is only what the flame ramp outlines and encloses. The bright
    # ramp is LIGHT the fire casts on what is around it -- in area 47 it
    # washes over the whole 16x16 tile (123px of 256) while the flame itself
    # is 22px. Taking that light for substance made the fire as wide as the
    # block and split it into a row of thin tongues.
    # Fill the outline by draping: every column runs from the flame's top
    # edge down to the base of the fire. enclosed() cannot do it here --
    # the outline is open at the bottom where it meets the bowl, so the
    # flood escapes and the fire comes out hollow, a ring with no volume.
    lit = np.zeros_like(fm)
    fcols = [x for x in range(fm.shape[1]) if fm[:, x].any()]
    if fcols:
        tops = {x: int(np.where(fm[:, x])[0].min()) for x in fcols}
        # Columns BETWEEN outline strokes carry no flame pixel of their own.
        # Filling only the columns that do left vertical slots through the
        # fire -- a comb instead of a body -- so the top edge is carried
        # across the gaps from its neighbours.
        for x in range(min(fcols), max(fcols) + 1):
            if x in tops:
                t = tops[x]
            else:
                lo = max([c for c in fcols if c < x], default=None)
                hi = min([c for c in fcols if c > x], default=None)
                if lo is None or hi is None:
                    continue
                f = (x - lo) / float(hi - lo)
                t = int(round(tops[lo] * (1 - f) + tops[hi] * f))
            lit[t:cy1 + 1, x] = True
    if lit.sum() >= 8:
        content = content & lit
        fx = np.where(content.any(axis=0))[0]
        if len(fx):
            mx0, mx1 = int(fx.min()), int(fx.max())
            mouth_w = mx1 - mx0 + 1
    tips = flame_tips(fm)
    if not tips:
        tips = [((mx0 + mx1) / 2.0, cy0)]
    n = len(tips)
    # Each tongue gets its own share of the mouth, so they stay legible as
    # separate flames instead of fusing into a slab.
    # Each tongue takes its share of the mouth. Do not shrink it: tongues
    # that overlap simply union, and narrow ones read as candles.
    Rmax = max(1.5, (mouth_w / float(n)) / 2.0)
    # One fire, not a row of tongues. Keep its volume, height, colour and
    # silhouette: revolve the drawn silhouette row by row about its own
    # centre. Splitting the fire between tips cut a single mass into slices
    # that were each too narrow to be anything but a stalk.
    #
    # Where a row holds two separate runs of fire, each is revolved on its
    # own -- which is how a forked tip appears, without being asked for.
    fire = content
    fys = np.where(fire.any(axis=1))[0]
    if not len(fys):
        return vox, cy1, rowof, D
    fy0, fy1 = int(fys.min()), int(fys.max())
    for y in range(fy0, fy1 + 1):
        xs_f = np.where(fire[y])[0]
        if not len(xs_f):
            continue
        runs, run = [], [int(xs_f[0])]
        for x in xs_f[1:]:
            if int(x) == run[-1] + 1:
                run.append(int(x))
            else:
                runs.append(run)
                run = [int(x)]
        runs.append(run)
        e = e_floor + (fy1 - y)
        for run in runs:
            lo, hi = run[0], run[-1]
            cxr = (lo + hi) / 2.0
            r = max(0.5, (hi - lo + 1) / 2.0)
            # Walk the integer columns of the run and solve for depth. The
            # previous form stepped dx and rounded cxr + dx: with a run of
            # even width cxr is a half integer, so two neighbouring dx round
            # onto the SAME column and the one between them is never written.
            # That dropped every other column and is what turned the fire
            # into a comb of vertical bars.
            for x in range(lo, hi + 1):
                if x < x0 or x > x1:
                    continue
                dxn = (x + 0.5) - (cxr + 0.5)
                half_d = np.sqrt(max(0.0, r * r - dxn * dxn))
                hz = int(round(half_d))
                for dz in range(-hz, hz + 1):
                    put(x, e, dz, (y, x))
    return vox, cy1, rowof, D


ROCK_FOOT = 2       # rows at the base that tuck in, so a rock reads as movable
ROCK_WEST = 0.08    # how much the lit west flank eases out


def build_boulder(mask, sub_mat, sub_art, height=0):
    """A rock: a half ellipsoid whose PLAN is the drawing.

    A boulder seen from this game's camera shows almost nothing but its top,
    so the drawn tile is very nearly its plan view. That makes the mapping
    trivial and removes the usual guesswork: a voxel at plan position
    (x, z) takes the pixel drawn at (x, z), because depth projects 1:1 the
    same way height does. Nothing is stretched and nothing is invented.

    build_dome was the obvious thing to reach for and it is wrong here: it
    makes a cylinder with a capped top and an authored height, which renders
    as a banded drum.

    Height is measured, not authored. The drawing shows the rock lit from
    the upper left with a shaded band on the lower right; that band is the
    side of the rock turning away, and its thickness is how much side the
    camera can see. A tall rock shows a lot of side, a flat one almost none.

    Returns (voxels, height, rowof).
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return set(), 0, {}
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    cy, cx = (y0 + y1) / 2.0, (x0 + x1) / 2.0
    W = x1 - x0 + 1

    measured = False
    if not height:
        # The shaded band on the lit-away side is the rock's visible side,
        # and its thickness is how much side the camera sees.
        #
        # But the black OUTLINE is dark too, and it rings the whole rock. A
        # plain luminance split therefore returns the outline's own width
        # (~1.5px) with an arc span of 2*pi, which a floor of 3 then dressed
        # up as a measurement. Exclude the outline, and refuse to answer when
        # the band still wraps the whole silhouette -- that means the split
        # found a rim light, not a face.
        lum = sub_art[:, :, :3].astype(float).mean(axis=2)
        outline = mask & (lum < 40.0)
        body = mask & ~outline
        v = lum[body]
        if len(v):
            thr = (v.max() + v.min()) / 2.0
            side = body & (lum < thr)
            if side.sum() >= 4:
                sy, sx = np.where(side)
                ang = np.arctan2(sy - cy, sx - cx)
                span = float(ang.max() - ang.min())
                if span < 4.0:                    # a band, not a ring
                    arc = max(1.0, span) * (W / 2.0)
                    h = side.sum() / max(arc, 1.0)
                    if h >= 2.0:
                        height = int(round(h)); measured = True
        if not measured:
            # Say so rather than inventing precision. A boulder this size
            # reads about half as tall as it is wide.
            height = max(3, int(round(W * 0.45)))

    # The light and dark ON the rock are not a rim light: they are the
    # rock's own faces catching the light at different angles. Bright means
    # a face turned up, dark means turned away -- so luminance IS a height
    # field over the plan, and the rock is that field, not a smooth blob.
    lum = sub_art[:, :, :3].astype(float).mean(axis=2)
    outline = mask & (lum < 40.0)
    body = mask & ~outline
    vals = lum[body] if body.any() else lum[mask]
    lo, hi = (float(vals.min()), float(vals.max())) if len(vals) else (0.0, 1.0)
    rng = max(hi - lo, 1e-6)

    vox, rowof = set(), {}
    H = height
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if not mask[y, x]:
                continue
            if outline[y, x]:
                t = 0.15                      # the outline is the rim, lowest
            else:
                t = (float(lum[y, x]) - lo) / rng
            # Flat lighting means a FLAT FACE, so the facets ARE the rock
            # and nothing should inflate them. Doming the whole thing was
            # worse than the plain facets. The only rounding a pushable rock
            # needs is at its BASE -- a rounded foot says it can roll, while
            # the faces above stay exactly as drawn.
            h = max(1, int(round(H * t)))
            zi = int(round(y - cy))
            for e in range(h):
                vox.add((int(x), e, zi))
                rowof[(int(x), e, zi)] = (int(y), int(x))

    # The WEST flank faces the light (see viewangle: the light is north
    # west, from above), so it is the side the player reads as rounded.
    # Ease it out by 8% -- enough to catch the eye, not enough to lose the
    # facets. The east flank stays as drawn.
    west = {}
    for (vx, ve, vz) in list(vox):
        dxn = (vx - cx) / max(W / 2.0, 1e-6)
        b = VA.west_bias(dxn)
        if b <= 0.0:
            continue
        grow = 1.0 + ROCK_WEST * b
        nx = int(round(cx + (vx - cx) * grow))
        if nx != vx:
            west[(nx, ve, vz)] = rowof.get((vx, ve, vz))
    for k, v in west.items():
        vox.add(k)
        if v is not None:
            rowof.setdefault(k, v)

    # Tuck the lowest rows in, so the rock sits on a rounded foot rather
    # than a flat-cut slab. Scenery is planted; a boulder rests.
    if ROCK_FOOT > 0:
        hw = max(W / 2.0, 1e-6)
        hd = max((y1 - y0 + 1) / 2.0, 1e-6)
        for e in range(ROCK_FOOT):
            k = 0.82 + 0.18 * (e / float(ROCK_FOOT))
            for (vx, ve, vz) in [v for v in vox if v[1] == e]:
                dx = (vx - cx) / hw
                dz = vz / hd
                if dx * dx + dz * dz > k * k:
                    vox.discard((vx, ve, vz))
                    rowof.pop((vx, ve, vz), None)
    return vox, H, rowof, measured


def span_at(centre, radius, i):
    """Half-extent of a circle of `radius` about `centre` at integer index i.

    Always walk the INTEGER cells and solve for the extent. Never step an
    offset and round `centre + offset`: when the centre falls on a half
    integer -- which it does whenever the object spans an even number of
    cells -- two neighbouring offsets round to the same cell and the one
    between them is never written. That drops every other column and has
    now produced striped fire, a striped tree canopy and a striped trunk.
    """
    d = (i + 0.5) - (centre + 0.5)
    r2 = radius * radius - d * d
    if r2 <= 0.0:
        return None
    return np.sqrt(r2)


def analyse_object(mask, sub_art):
    """The stages between naming an object and voxelating it.

    Order matters: classification, then ANGULAR ANALYSIS, then PROFILE
    INSPECTION, then extrapolation, and only then voxels. Skipping the
    middle two is what made me guess heights and invent proportions.

    Angular analysis
        Bisect the silhouette vertically and horizontally and score each
        half against its mirror. A symmetrical object can be built from one
        half, and its axis of symmetry is where the form turns.
        The BRIGHTEST pixels say which way is up: the surface most squarely
        facing the light is the object's high point, and this game lights
        from above and to the upper left.

    Profile inspection
        Width per row down the silhouette, plus the two half profiles, so a
        taper, a shoulder or a bulge is measured rather than assumed.

    Returns a dict the builders can read.
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return {}
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    W, H = x1 - x0 + 1, y1 - y0 + 1
    sub = mask[y0:y1 + 1, x0:x1 + 1]

    # --- angular: symmetry about each bisector
    lr = float((sub == sub[:, ::-1]).mean())
    tb = float((sub == sub[::-1, :]).mean())

    # --- angular: where the light lands hardest = the high point
    lum = sub_art[:, :, :3].astype(float).mean(axis=2)
    inside = np.where(mask, lum, -1.0)
    thr = float(np.percentile(lum[mask], 92))
    bright = mask & (lum >= thr)
    if bright.any():
        by, bx = np.where(bright)
        apex = (int(np.median(by)), int(np.median(bx)))
        apex_row_frac = (apex[0] - y0) / float(max(1, H - 1))
    else:
        apex = (int(ys.mean()), int(xs.mean()))
        apex_row_frac = 0.5

    # --- profile: width per row, and each half's own width
    widths, lhw, rhw = [], [], []
    cxp = (x0 + x1) / 2.0
    for y in range(y0, y1 + 1):
        row = np.where(mask[y])[0]
        if not len(row):
            widths.append(0); lhw.append(0); rhw.append(0); continue
        widths.append(int(len(row)))
        lhw.append(int((row < cxp).sum()))
        rhw.append(int((row >= cxp).sum()))
    widths = np.array(widths)
    wmax = int(widths.max()) if len(widths) else 0
    widest = int(np.argmax(widths)) if len(widths) else 0

    return {
        "bbox": (x0, y0, x1, y1), "w": W, "h": H,
        "sym_lr": lr, "sym_tb": tb,
        "symmetric_lr": lr >= 0.88, "symmetric_tb": tb >= 0.88,
        "apex": apex, "apex_row_frac": apex_row_frac,
        "widths": widths, "widest_row": widest, "wmax": wmax,
        "widest_frac": widest / float(max(1, H - 1)),
        "half_left": np.array(lhw), "half_right": np.array(rhw),
        # A tall object is drawn with its top at the top of its tile: the
        # apex sits at the drawn top and the camera tilt is what spreads the
        # rest downward. A flat one shows mostly plan.
        "tall": (H > W * 1.05) or (widest / float(max(1, H - 1)) > 0.6),
    }


LOTUS_SOUTH = 0.72  # how far the south face reaches, vs the north


def build_lotus(mask, sub_mat, sub_art, height=0):
    """A lotus flower: a bulbous head whose petals come to a point on top.

    The shape is a bulb -- widest low down, tapering upward to a point --
    not a sphere and not a cone. What makes it read as a flower rather than
    an egg is the petals, and those are already in the drawing: each petal
    is a pale lobe with a slightly darker, feathered seam beside it. So the
    seams are grooves and the petal backs are ridges, and the shading is
    what carves them. Same principle as the rock's facets, but curved --
    a petal is a rounded lobe, not a plane.

    Returns (voxels, height, rowof).
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return set(), 0, {}
    x0b, x1b = int(xs.min()), int(xs.max())
    y0b, y1b = int(ys.min()), int(ys.max())
    W = x1b - x0b + 1
    cxp = (x0b + x1b) / 2.0
    R = max(2.0, W / 2.0)
    if not height:
        height = max(4, int(round(W * 1.15)))
    H = height

    lum = sub_art[:, :, :3].astype(float).mean(axis=2)
    inside = lum[mask]
    lo, hi = float(inside.min()), float(inside.max())
    rng = max(hi - lo, 1e-6)

    # Petals are RADIAL lobes, so their relief belongs on the radius, not
    # on the height. Displacing the surface vertically gouged trenches down
    # the bulb's near-vertical sides instead of sculpting petals.
    # A lotus head is TALL, so its drawing is nearer an elevation than a
    # plan: the drawn row corresponds to HEIGHT, not depth. Sampling it as
    # a plan mapped the bulb's lower surface onto the dark water between
    # the flowers, which is what striped it.
    drawn_h = max(1, y1b - y0b)

    def drawn_row(k):
        return y1b - int(round(k * drawn_h / float(max(1, H - 1))))

    def sample(x, k):
        ry = min(max(drawn_row(k), y0b), y1b)
        if mask[ry, x]:
            return (ry, x)
        row = np.where(mask[ry])[0]
        if len(row):
            return (ry, int(row[np.argmin(np.abs(row - x))]))
        col = np.where(mask[:, x])[0]
        if len(col):
            return (int(col[np.argmin(np.abs(col - ry))]), x)
        return None

    # Petal relief, radial and per DIRECTION. Averaging each column into a
    # single number smoothed the base into a plain egg; the petals wrap all
    # the way round, so the relief has to vary with the direction you look
    # from, not just with x. The base gets the most of it because that is
    # where the lobes are widest and the seams deepest.
    cyc = (y0b + y1b) / 2.0
    petal = {}
    for x in range(x0b, x1b + 1):
        for ry in range(y0b, y1b + 1):
            if not mask[ry, x]:
                continue
            t = (float(lum[ry, x]) - lo) / rng
            petal[(x, int(round(ry - cyc)))] = 0.88 + 0.22 * t

    def petal_at(x, dz, k):
        base = petal.get((x, dz))
        if base is None:
            near = [v for (px, pz), v in petal.items() if px == x]
            base = float(np.mean(near)) if near else 1.0
        # deepen the seams low down, ease them off toward the tips
        w = 1.0 - 0.45 * (k / float(max(1, H - 1)))
        return 1.0 + (base - 1.0) * w

    # The head is a rounded bulb, and EACH PETAL comes to its own point on
    # top -- not one apex. The drawing says where they are: every pale lobe
    # is a petal back, and the feathered seams are the gaps between them.
    dome_h = int(round(H * 0.72))
    vox, rowof = set(), {}
    for k in range(dome_h):
        f = k / float(max(1, dome_h - 1))
        rk = R * (1.0 - f ** 2.4) ** 0.5
        if rk < 0.4:
            continue
        for x in range(x0b, x1b + 1):
            src = sample(x, k)
            if src is None:
                continue
            for dz in range(-int(np.ceil(rk)), int(np.ceil(rk)) + 1):
                hx = span_at(cxp, rk * petal_at(x, dz, k), x)
                if hx is None or abs(dz) > hx:
                    continue
                # A lotus stands upright, so it is not as DEEP as it is
                # wide. Under the camera (drawn = height + depth) a bloom
                # drawn this tall is mostly height; giving it full width in
                # depth pushed its south face out past the drawing.
                if dz > 0 and dz > hx * LOTUS_SOUTH:
                    continue
                vox.add((x, k, dz))
                rowof[(x, k, dz)] = src

    # Petal tips: local high points of the drawn light, kept apart so each
    # is a separate lobe rather than a ridge.
    tips = []
    lm = np.where(mask, lum, -1.0)
    for y in range(y0b, y1b + 1):
        for x in range(x0b, x1b + 1):
            if not mask[y, x]:
                continue
            v = lm[y, x]
            win = lm[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
            if v < win.max() - 1e-6 or v < lo + 0.45 * rng:
                continue
            if all((x - tx) ** 2 + (y - ty) ** 2 >= 9 for tx, ty in tips):
                tips.append((x, y))
    if not tips:
        tips = [(int(round(cxp)), (y0b + y1b) // 2)]

    # Each tip becomes a small pointed lobe standing on the dome.
    cyp = (y0b + y1b) / 2.0
    tip_h = max(2, int(round(H * 0.30)))
    for (tx, ty) in tips:
        dz0 = int(round(ty - cyp))
        # where the dome surface sits under this petal
        base_e = max([e for (vx, e, vz) in vox
                      if vx == tx and vz == dz0] or [dome_h - 1])
        # 0.24 was tried and was worse: fat petals merge into one
        # overhanging crown instead of separate points.
        pr = max(1.0, R * 0.16)
        for i in range(tip_h):
            g = i / float(tip_h)
            ri = pr * (1.0 - g) ** 0.6
            if ri < 0.4:
                break
            e = base_e + i
            for x in range(int(np.floor(tx - ri)), int(np.ceil(tx + ri)) + 1):
                hx = span_at(float(tx), ri, x)
                if hx is None:
                    continue
                src = sample(min(max(x, x0b), x1b), min(H - 1, e))
                for dz in range(dz0 - int(round(hx)), dz0 + int(round(hx)) + 1):
                    vox.add((x, e, dz))
                    if src is not None:
                        rowof[(x, e, dz)] = src

    return vox, H, rowof


def apex_centre(mask, tol=2):
    """Where the crown actually peaks, and how wide that crown is.

    The bounding box lies when foliage from a neighbouring tree joins the
    blob: the box then spans the clump and its midpoint sits between two
    trees rather than on either trunk. The apex does not lie. A conical
    tree's topmost rows form a short plateau directly over its axis, so the
    columns that reach within `tol` of the highest drawn row ARE the crown,
    and their midpoint is the tree's centre.

    Returns (centre_x, x_lo, x_hi) over the crown, or None.
    """
    top = []
    for x in range(mask.shape[1]):
        c = np.where(mask[:, x])[0]
        top.append(int(c.min()) if len(c) else None)
    have = [x for x, t in enumerate(top) if t is not None]
    if not have:
        return None
    hi = min(top[x] for x in have)
    plate = [x for x in have if top[x] <= hi + tol]
    if not plate:
        return None
    # Only the run of plateau columns containing the highest point -- a
    # second tree of the same height further along is a second tree.
    seed = min(plate, key=lambda x: (top[x], x))
    lo = hi_x = seed
    ps = set(plate)
    while lo - 1 in ps:
        lo -= 1
    while hi_x + 1 in ps:
        hi_x += 1
    return (lo + hi_x) / 2.0, lo, hi_x


def build_tree(mask, sub_mat, sub_art, trunk_h=0, canopy_h=0):
    """A tree: a canopy of foliage standing on a trunk.

    The canopy is the same problem as a rock and gets the same treatment --
    its plan is the drawing, and its light and dark are faces at different
    angles, so the surface is a facetted height field rather than a smooth
    dome. build_boulder already does exactly that, so the canopy IS a
    boulder made of leaves; only the lift and the trunk are new.

    What the tile CANNOT say is how tall the trunk is. From this camera the
    canopy hides it: all that shows below the leaves is a band a few pixels
    deep, and that band runs the full width of the canopy, so it is the
    shaded underside rather than a drop shadow whose offset could be
    measured. The trunk height is therefore declared, not derived.

    Returns (voxels, trunk_h, canopy_h, rowof).
    """
    ys, xs = np.where(mask)
    if not len(ys):
        return set(), 0, 0, {}
    W = int(xs.max() - xs.min() + 1)
    a = analyse_object(mask, sub_art)
    drawnH = int(a.get("h", 0)) or (int(ys.max() - ys.min()) + 1)
    if not canopy_h:
        # The tree's TOPMOST POINT is the top of its tile. So the drawing
        # spans the whole tree, apex to foot, and the model's total height
        # is the drawn height -- not some multiple of its width. Split that
        # between canopy and trunk instead of inventing both.
        canopy_h = max(6, int(round(drawnH / 1.45)))
    if not trunk_h:
        # The trunk is proportionate to the TREE, not to its own diameter:
        # a tall tree stands on a tall trunk. Its width is still the
        # measured 16px from the felled logs.
        trunk_h = max(4, int(round(canopy_h * 0.55)))

    # The canopy is a BALL of leaves, not an extruded outline.
    #
    # build_boulder treats the drawing as the object's plan, which is right
    # for a rock: a rock is low, so the camera sees only its top. A tree is
    # tall, so its drawn height mixes depth AND elevation -- this canopy is
    # drawn 48 wide by 56 tall, and extruding that as a plan gives a 56-deep
    # slab. The width is the honest measurement of how big the ball is.
    ys, xs = np.where(mask)
    x0b, x1b = int(xs.min()), int(xs.max())
    y0b, y1b = int(ys.min()), int(ys.max())
    # The centre is the CROWN's, not the blob's. Neighbouring foliage joins
    # the blob and drags the bounding box's midpoint off the trunk; the
    # apex plateau stays over it.
    ap = apex_centre(mask)
    cxp = (x0b + x1b) / 2.0
    if ap is not None:
        cxp = ap[0]
        # Reach is measured from the crown outward, and stops where the
        # canopy stops falling -- not at the far edge of the clump.
        R = max(2.0, min(cxp - x0b, x1b - cxp) + (ap[2] - ap[1]) / 2.0)
        R = min(R, W / 2.0)
    else:
        R = max(2.0, W / 2.0)
    lum = sub_art[:, :, :3].astype(float).mean(axis=2)
    inside = lum[mask]
    lo, hi = float(inside.min()), float(inside.max())
    rng = max(hi - lo, 1e-6)

    vox, rowof = set(), {}
    # A cone with a softened tip. Putting the widest ring at the very
    # bottom made an umbrella: a full-width disc with a hard rim and the
    # trunk hidden under it. Real foliage gathers a little way up, so the
    # mass swells from a narrower shoulder to its widest at CANOPY_PEAK of
    # the way up, then tapers to the tip. The trunk shows below it, which
    # is what makes the tree read as tall rather than as a mushroom.
    for k in range(canopy_h):
        f = k / float(max(1, canopy_h - 1))
        # Tried putting the widest ring a fifth of the way up so the trunk
        # would show. It left the top of that ring exposed as a flat shelf
        # and the tree read as a mushroom with a brim. A continuous taper
        # from the base has no shelf, so that is what stays.
        rk = R * (1.0 - f) ** 0.65
        if rk < 0.5:
            continue
        e = trunk_h + k
        for x in range(x0b, x1b + 1):
            hx = span_at(cxp, rk, x)
            if hx is None:
                continue
            for dz in range(-int(np.ceil(hx)), int(np.ceil(hx)) + 1):
                if abs(dz) > hx:
                    continue
                ry = int(round((y0b + y1b) / 2.0 + dz))
                ry = min(max(ry, y0b), y1b)
                if not mask[ry, x]:
                    continue
                vox.add((x, e, dz))
                rowof[(x, e, dz)] = (ry, x)

    # Push the top surface in and out by the drawn shading, so the facets
    # the artist painted survive on the ball.
    top = {}
    for (x, e, z) in list(vox):
        if (x, z) not in top or e > top[(x, z)]:
            top[(x, z)] = e
    for (x, z), e in top.items():
        key = rowof.get((x, e, z))
        if key is None:
            continue
        t = (float(lum[key[0], key[1]]) - lo) / rng
        bump = int(round((t - 0.5) * 0.18 * R))
        if bump > 0:
            for k in range(1, bump + 1):
                vox.add((x, e + k, z))
                rowof[(x, e + k, z)] = key
        elif bump < 0:
            for k in range(0, -bump):
                vox.discard((x, e - k, z))

    # The trunk: a column under the middle of the canopy, taking its colour
    # from the bark drawn just below the leaves. It uses the SAME crown
    # centre -- a trunk under the bounding box's midpoint stands beside the
    # tree it is meant to hold up.
    czp = 0.0
    # Trunk diameter is MEASURED, from the felled logs lying in the same
    # woods: a downed tree shows its trunk side on, and those logs are 16px
    # thick (one cell). An upright tree's trunk is the same trunk, so there
    # is no need to guess it from the canopy.
    r = max(1.0, 16.0 / 2.0)
    lum = sub_art[:, :, :3].astype(float).mean(axis=2)
    # Bark is WARM (red above blue); the shadow pooled under the canopy is
    # cool purple and darker. Picking "the darkest pixels below the leaves"
    # painted the trunk with that shadow instead of bark.
    bark = None
    band = np.zeros_like(mask)
    band[int(ys.max()) + 1:min(mask.shape[0], int(ys.max()) + 10), :] = True
    rgbf = sub_art[:, :, :3].astype(float)
    warm = band & (rgbf[:, :, 0] > rgbf[:, :, 2] + 8)
    pick = warm if warm.any() else band
    if pick.any():
        by, bx = np.where(pick)
        bark = (int(np.median(by)), int(np.median(bx)))
    for x in range(int(round(cxp - r)), int(round(cxp + r)) + 1):
        hx = span_at(cxp, r, x)
        if hx is None:
            continue
        for dz in range(-int(round(hx)), int(round(hx)) + 1):
            for e in range(trunk_h):
                vox.add((x, e, dz))
                if bark is not None:
                    rowof[(x, e, dz)] = bark
    return vox, trunk_h, canopy_h, rowof


def cmd_profile(args):
    """Print an object's silhouette as width per row, and what that implies.

    Before fitting anything it is worth seeing the shape the drawing actually
    describes. A mushroom and a cottage are both "a thing with a roof", and
    they are completely different profiles: the mushroom's widest point is a
    cap that overhangs a narrow stem, the cottage's widest point is its wall
    and its roof tapers in above it. Reading that off the drawing is cheaper
    and far more reliable than guessing a mode from a name.
    """
    r = RE.load_room(Path(args.path))
    d = SH.decompose_room(r, args.layer)
    if d is None:
        sys.exit("needs a v3+ dump with palettes")
    mat = d[0]
    art = RE.room_art_rgb(r, args.layer)
    cx0, cy0, cx1, cy1 = (int(v) for v in args.cells.split(","))
    x0, y0, x1, y1 = cx0 * 16, cy0 * 16, cx1 * 16, cy1 * 16
    if args.materials:
        want = {int(v) for v in args.materials.split(",")}
        mask = np.isin(mat[y0:y1, x0:x1], list(want))
    else:
        mask, _bg = object_mask(mat, art, x0, y0, x1, y1, args.ground_frac)
    mask = largest_blob(mask)
    if not mask.any():
        sys.exit("nothing found -- check --cells / --materials")

    ys = np.where(mask.any(axis=1))[0]
    top, base = int(ys.min()), int(ys.max())
    W = np.array([int(mask[y].sum()) for y in range(top, base + 1)])
    span = np.array([
        (int(np.where(mask[y])[0].max()) - int(np.where(mask[y])[0].min()) + 1)
        if mask[y].any() else 0 for y in range(top, base + 1)])
    wmax = max(1, int(span.max()))
    print(f"  {Path(args.path).name} cells {args.cells}")
    print(f"  {int(mask.sum())}px, rows {top}..{base} ({base - top + 1} tall), "
          f"widest {wmax}px")
    print()
    print("  row   width  profile (| = silhouette span, # = filled)")
    for i, y in enumerate(range(top, base + 1)):
        if args.step > 1 and i % args.step:
            continue
        bar = int(round(span[i] * 46.0 / wmax))
        fill = int(round(W[i] * 46.0 / wmax))
        pad = (46 - bar) // 2
        line = " " * pad + "#" * fill + "-" * max(0, bar - fill)
        print(f"  {y:4d} {span[i]:5d}  |{line:<46}|")
    print()

    # Where does the silhouette change character? A step in width is a joint:
    # cap to stem on a mushroom, roof to wall on a cottage.
    dw = np.diff(span.astype(float))
    joints = []
    for i, v in enumerate(dw):
        if abs(v) >= max(2.0, wmax * 0.12):
            joints.append((top + i + 1, int(span[i]), int(span[i + 1])))
    widest = top + int(np.argmax(span))
    print(f"  widest row {widest} ({wmax}px), "
          f"{100.0 * (widest - top) / max(1, base - top):.0f}% down from the top")
    if joints:
        print("  joints (a step in the silhouette, where one part meets another):")
        for row, a, b in joints[:12]:
            print(f"    row {row:4d}  {a:3d}px -> {b:3d}px  "
                  f"{'widens' if b > a else 'narrows'}")
    else:
        print("  no sharp joints: one continuous body")
    below = span[widest - top:]
    if len(below) > 2:
        narrow = int(below.min())
        print(f"  narrowest below the widest row: {narrow}px "
              f"({100.0 * narrow / wmax:.0f}% of the width)")
        if narrow < wmax * 0.55:
            print("  -> OVERHANG: the top is much wider than what holds it up, "
                  "which is a mushroom or a parasol, not a cottage.")
        else:
            print("  -> no overhang: the body carries its own width down to "
                  "the ground, which is a cottage or a tower.")

    if args.features:
        want = {int(v) for v in args.features.split(",")}
        fm = mask & np.isin(mat[y0:y1, x0:x1], list(want))
        print()
        print("  things drawn ON the surface, in material(s) "
              f"{sorted(want)}:")
        print("    A tube standing out of a surface is seen END ON and draws")
        print("    about as tall as it is wide. Anything flush sits on a face")
        print("    that the camera foreshortens, so it draws much wider than")
        print("    tall. That ratio is the test -- not where it sits, which")
        print("    is how a chimney got called a window.")
        print()
        print("    rows        size    aspect  reading")
        found = []
        # The skin is the material that covers the most of the object: the
        # cap on a mushroom, the roof or wall on a house.
        skin_vals, skin_cnts = np.unique(mat[y0:y1, x0:x1][mask & ~fm],
                                         return_counts=True)
        skin = int(skin_vals[int(np.argmax(skin_cnts))]) if len(skin_vals) else 0
        sub_art_p = art[y0:y1, x0:x1]
        outline_m = dark_outline(sub_art_p, mask)
        print(f"    the object's skin is material {skin}")
        print()
        for size, bys, bxs in _blobs(fm):
            if size < 20:
                continue
            hgt = int(bys.max() - bys.min() + 1)
            wid = int(bxs.max() - bxs.min() + 1)
            asp = wid / max(1.0, hgt)
            # What a fitting sits IN says which surface it is on. A stack
            # stands in the cap and is ringed by cap colour; a door panel sits
            # inside the porch and is ringed by the porch's dark. Without this
            # every fitting on the building reads as a chimney, because a door
            # panel is also about as tall as it is wide.
            blob = np.zeros_like(mask)
            blob[bys, bxs] = True
            grown = blob.copy()
            for _ in range(3):
                g2 = grown.copy()
                g2[1:] |= grown[:-1]; g2[:-1] |= grown[1:]
                g2[:, 1:] |= grown[:, :-1]; g2[:, :-1] |= grown[:, 1:]
                grown = g2
            # Look PAST the outline. Every fitting is drawn with its own dark
            # stroke around it, so the nearest ring of pixels is always that
            # stroke -- asking it what surface a stack sits in returns "the
            # outline", and three chimneys came back as panels.
            ring_ = grown & ~blob & mask & ~outline_m
            host = 0
            if ring_.any():
                vals, cnts = np.unique(mat[y0:y1, x0:x1][ring_], return_counts=True)
                host = int(vals[int(np.argmax(cnts))])
            if bys.max() >= base - 1:
                what = "meets the ground: structure, not a fitting"
            elif asp <= 1.45 and host == skin:
                what = f"PROJECTS from the {skin} surface (a stack, seen end on)"
            elif asp <= 1.45:
                what = f"set into the {host} surface (a panel, a fitting)"
            else:
                what = "flush on the face (a window, a sign)"
            found.append((int(bys.min()), int(bys.max()), wid, hgt, asp,
                          host, what))
        # A fitting far smaller than the others is a fleck of trim, not a
        # chimney. Scale the floor to what is actually there rather than fix
        # a pixel count that would be wrong on the next building.
        areas = [w_ * h_ for _a, _b, w_, h_, _s, _o, wt in found
                 if "PROJECTS" in wt]
        floor_ = max(areas) * 0.15 if areas else 0
        found = [f_ for f_ in found
                 if "PROJECTS" not in f_[6] or f_[2] * f_[3] >= floor_]
        for a, b, wid, hgt, asp, host, what in sorted(found):
            print(f"    {a:3d}..{b:<3d}  {wid:3d}x{hgt:<3d}  {asp:5.2f}  "
                  f"in {host:3d}  {what}")
        npro = sum(1 for f_ in found if "PROJECTS" in f_[6])
        nflu = sum(1 for f_ in found if "flush" in f_[6])
        print(f"    {npro} projecting, {nflu} flush")


def cmd_fit(args):
    r = RE.load_room(Path(args.path))
    d = SH.decompose_room(r, args.layer)
    if d is None:
        sys.exit("needs a v3+ dump with palettes")
    mat, shd = d[0], d[1]
    art = RE.room_art_rgb(r, args.layer)
    cx0, cy0, cx1, cy1 = (int(v) for v in args.cells.split(","))
    x0, y0, x1, y1 = cx0 * 16, cy0 * 16, cx1 * 16, cy1 * 16

    if getattr(args, "materials", ""):
        # Naming the ramp beats inferring it when an object is drawn with an
        # effect around it: the Minish portal's sparkle halo reaches the same
        # cells as the stump and is not part of its solid.
        want = {int(v) for v in args.materials.split(",")}
        mask = np.isin(mat[y0:y1, x0:x1], list(want))
        bg = set()
        named = True
    else:
        mask, bg = object_mask(mat, art, x0, y0, x1, y1, args.ground_frac)
        named = False
    # Dropping components that run off the rect is for the INFERRED case,
    # where touching the edge means "this is the background continuing".
    # When the caller names the ramp it has already said what the object is,
    # and the rect is often the object's own cell -- a chest fills its 16x16
    # tile, so every one of its components touches the border and this threw
    # the whole chest away, leaving a 28px crumb. That is why the one chest
    # that worked had a 3x3 rect around it.
    if not args.keep_border and not named:
        inner = drop_border_touching(mask)
        if inner.any():
            mask = inner
    mask = largest_blob(mask)
    if args.clean:
        mask = erode(mask, args.clean)
    if not mask.any():
        sys.exit("no object found in that rect -- widen it or lower --ground-frac")

    sub_art = art[y0:y1, x0:x1]
    if args.mode not in ("upright",) and not getattr(args, "keep_outline", False):
        # De-outlining is right when the stroke would become a black WALL, as
        # it does on an extruded stump. On an upright object the outline is
        # part of the drawing -- the frame around a door, the rim of a window
        # -- and removing it erases the very detail that makes the feature
        # readable.
        sub_art = deoutline(sub_art, dark_outline(sub_art, mask))

    def colour_of(x, z):
        if 0 <= z < sub_art.shape[0] and 0 <= x < sub_art.shape[1]:
            return tuple(float(v) / 255.0 for v in sub_art[z, x])
        return (0.6, 0.6, 0.6)

    if args.mode == "upright":
        mask = largest_blob(mask)
        relief = None
        if args.relief:
            # Ramp position per pixel, -1 (darkest) .. +1 (lightest), within
            # each material so one object's shading is not measured against
            # another's palette.
            sub_shd2 = shd[y0:y1, x0:x1]
            sub_mat2 = mat[y0:y1, x0:x1]
            relief = np.zeros(mask.shape, float)
            for m_ in sorted(set(int(v) for v in sub_mat2[mask].ravel())):
                mm = mask & (sub_mat2 == m_)
                vals = sorted(set(sub_shd2[mm].ravel().tolist()))
                if len(vals) < 2:
                    continue
                rank = {v: i / (len(vals) - 1.0) for i, v in enumerate(vals)}
                for v, r_ in rank.items():
                    relief[mm & (sub_shd2 == v)] = 2.0 * r_ - 1.0
        # Chimneys, windows, doors and the stem are all drawn in the SAME
        # material on this house, so colour cannot tell them apart. Position
        # can, and the rule is about what the thing IS rather than how it is
        # drawn: above the shoulder a feature stands proud of the roof (a
        # smoke stack sticks up out of it), below the shoulder a feature is an
        # opening (a window or a door is a hole in a wall). The piece standing
        # on the ground is the stem holding the object up, and it is neither.
        depth = args.relief
        if args.features and args.feature_depth:
            want = {int(v) for v in args.features.split(",")}
            fm = mask & np.isin(mat[y0:y1, x0:x1], list(want))
            if relief is None:
                relief = np.zeros(mask.shape, float)
            ys_m = np.where(mask.any(axis=1))[0]
            top_r, base_r = int(ys_m.min()), int(ys_m.max())
            sh_row = top_r + max(1, int(round((base_r - top_r + 1) * args.cap_frac)))
            scale = args.feature_depth / max(0.001, args.relief or 1.0)
            n_proud = n_sunk = n_stem = 0
            for size, bys, bxs in _blobs(fm):
                if size < 8:
                    continue
                if bys.max() >= base_r - 1:
                    n_stem += 1
                    continue
                if bys.mean() < sh_row:
                    relief[bys, bxs] = relief[bys, bxs] + scale
                    n_proud += 1
                else:
                    relief[bys, bxs] = relief[bys, bxs] - scale
                    n_sunk += 1
            depth = args.relief or 1.0
            print(f"  features in material(s) {sorted(want)}: {n_proud} proud "
                  f"of the roof, {n_sunk} sunk into the wall, {n_stem} "
                  f"standing on the ground (left as structure)")
        vox, base, y_wide, rowmap, ring = build_upright(
            mask, args.squash, args.dome, args.cap_frac, relief, depth,
            args.foot_frac, args.vscale)
        if not vox:
            sys.exit("nothing in that rect")

        # Projections are DETECTED but not built. `profile --features`
        # reads them correctly -- three stacks and one window on the Minish
        # Woods house, matching what the art actually shows -- but placing
        # them in 3D needs the cap's plan mapping, and that mapping is only
        # trustworthy near the crown. Near the rim, where two of these three
        # sit, the drawing is showing their side rather than their top, and
        # the tubes landed off the surface and out of line.
        #
        # Building them wrong is worse than leaving them as surface colour,
        # so they stay as surface colour until the placement is right.
        stacks = []

        h_, w_ = mask.shape
        ys_, xs_ = np.where(mask)
        how = "measured" if not args.cap_frac else f"--cap-frac {args.cap_frac}"
        print(f"  upright: base row {base}, shoulder row {y_wide} ({how}) -- "
              f"rows below fold up, rows above lift into the cap")
        print(f"  {xs_.max() - xs_.min() + 1}px wide, "
              f"{base - ys_.min() + 1}px tall -- the height is MEASURED, "
              f"because a front elevation states it")
        fbu = tuple(float(v) / 255.0 for v in sub_art[mask].mean(axis=0)[:3])

        def colour_of(x, z, y=None):
            # Unwrap the drawing around the object by ARC LENGTH, not by x.
            #
            # The body is a stack of circles and the elevation is drawn flat.
            # Sampling each voxel at its own x looks right and is not: the
            # chord across a circle is shorter than the arc over it, and the
            # gap grows toward the silhouette. So anything drawn off-centre --
            # a window, a smoke stack -- was smeared wider the further from
            # the middle it sat, which is the horizontal stretch. Walking the
            # surface by angle gives every drawn pixel the same amount of
            # surface. The back half mirrors the front, because the drawing
            # only ever shows one side.
            e = int(y or 0)
            ix = min(max(int(round(x)), 0), w_ - 1)
            g = ring.get(e)
            if g is not None and g[1] > 0.5:
                cx_, hw_, _r0 = g
                phi = np.arctan2(x + 0.5 - cx_, float(z) if z else 1e-6)
                if abs(phi) > np.pi / 2:
                    phi = np.sign(phi) * (np.pi - abs(phi))
                ix = int(round(cx_ + hw_ * (phi / (np.pi / 2)) - 0.5))
                ix = min(max(ix, 0), w_ - 1)
            # Ask the builder which drawn row made this height, so the door,
            # the windows and the sign stay on the row they were drawn on --
            # a fold-up that guesses the row puts the door underground.
            row = rowmap.get((ix, e))
            if row is None and g is not None:
                row = g[2]
            if row is None:
                for k in (1, -1):
                    row = rowmap.get((ix, e + k))
                    if row is not None:
                        break
            if row is None:
                row = base - e
            if 0 <= row < h_ and mask[row, ix]:
                return tuple(float(v) / 255.0 for v in sub_art[row, ix][:3])
            return fbu

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode,
                            args.mode in ("upright", "stump", "dome", "cylinder"))
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "lotus":
        mask = largest_blob(mask)
        sub_mat = mat[y0:y1, x0:x1]
        gave_h = any(a == "--height" or a.startswith("--height=") for a in sys.argv)
        vox, H, rowof = build_lotus(mask, sub_mat, sub_art,
                                    args.height if gave_h else 0)
        if not vox:
            sys.exit("nothing in that rect")
        h_, w_ = mask.shape
        ys_, xs_ = np.where(mask)
        print(f"  lotus: {int(xs_.max()-xs_.min()+1)}px across, head {H}px tall "
              f"{'(given)' if gave_h else '(1.15 x width: bulbous, pointed on top)'}")
        fbc = tuple(float(v) / 255.0 for v in sub_art[mask].mean(axis=0)[:3])

        def colour_of(x, z, y=None):
            e = int(y or 0)
            key = rowof.get((int(round(x)), e, int(round(z))))
            if key is None:
                return fbc
            ry, rx = key
            ry = min(max(ry, 0), h_ - 1); rx = min(max(rx, 0), w_ - 1)
            return tuple(float(v) / 255.0 for v in sub_art[ry, rx][:3])

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode, True)
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "tree":
        mask = largest_blob(mask)
        sub_mat = mat[y0:y1, x0:x1]
        gave_h = any(a == "--height" or a.startswith("--height=") for a in sys.argv)
        vox, th, ch, rowof = build_tree(mask, sub_mat, sub_art,
                                        args.height if gave_h else 0)
        if not vox:
            sys.exit("nothing in that rect")
        h_, w_ = mask.shape
        ys_, xs_ = np.where(mask)
        print(f"  tree: canopy {int(xs_.max()-xs_.min()+1)}px across, "
              f"{int(ys_.max()-ys_.min()+1)}px deep in plan")
        print(f"  canopy {ch}px thick, trunk {th}px "
              f"{'(given)' if gave_h else '(DECLARED, not measured: the canopy hides the trunk)'}")
        fbc = tuple(float(v) / 255.0 for v in sub_art[mask].mean(axis=0)[:3])

        def colour_of(x, z, y=None):
            e = int(y or 0)
            key = rowof.get((int(round(x)), e, int(round(z))))
            if key is None:
                return fbc
            ry, rx = key
            ry = min(max(ry, 0), h_ - 1); rx = min(max(rx, 0), w_ - 1)
            return tuple(float(v) / 255.0 for v in sub_art[ry, rx][:3])

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode, True)
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "rock":
        mask = largest_blob(mask)
        sub_mat = mat[y0:y1, x0:x1]
        # --height has a non-zero default, so a sentinel comparison cannot
        # tell "not given" from "given that value". Ask the command line.
        gave_h = any(a == "--height" or a.startswith("--height=")
                     for a in sys.argv)
        vox, H, rowof, meas = build_boulder(mask, sub_mat, sub_art,
                                            args.height if gave_h else 0)
        if not vox:
            sys.exit("nothing in that rect")
        h_, w_ = mask.shape
        ys_, xs_ = np.where(mask)
        print(f"  rock: {int(xs_.max()-xs_.min()+1)}px wide, "
              f"{int(ys_.max()-ys_.min()+1)}px deep in plan, height {H}px "
              f"{'(given)' if gave_h else ('(measured from the shaded side)' if meas else '(NOT measurable from this tile: the shaded band rings the whole rock, so it is a rim light, not a face. Using 0.45 x width.)')}")
        fbc = tuple(float(v) / 255.0 for v in sub_art[mask].mean(axis=0)[:3])

        def colour_of(x, z, y=None):
            e = int(y or 0)
            key = rowof.get((int(round(x)), e, int(round(z))))
            if key is None:
                return fbc
            ry, rx = key
            ry = min(max(ry, 0), h_ - 1); rx = min(max(rx, 0), w_ - 1)
            if mask[ry, rx]:
                return tuple(float(v) / 255.0 for v in sub_art[ry, rx][:3])
            return fbc

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode, True)
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "torch":
        mask = largest_blob(mask)
        sub_mat = mat[y0:y1, x0:x1]
        ff = fire_fraction(sub_art)
        if ff <= 0.0 and not getattr(args, "force", False):
            sys.exit(f"no fire in this tile (fire fraction {ff:.3f}) -- this "
                     f"is a button, not a torch. Tile type says TORCH_LIT for "
                     f"both; the art does not. Pass --force to build anyway.")
        vox, split, rowof, D = build_torch(mask, sub_mat, sub_art, args.squash)
        if not vox:
            sys.exit("nothing in that rect")
        h_, w_ = mask.shape
        ys_, xs_ = np.where(mask)
        base_r = int(ys_.max())
        print(f"  torch: {int(xs_.max() - xs_.min() + 1)}px wide, "
              f"{int(base_r - ys_.min() + 1)}px tall, depth {D}px")
        print(f"  seam MEASURED at row {split}: below it is stone that "
              f"reaches the tile floor, above it is what sits in the bowl")
        fbc = tuple(float(v) / 255.0 for v in sub_art[mask].mean(axis=0)[:3])

        def colour_of(x, z, y=None):
            e = int(y or 0)
            ix = min(max(int(round(x)), 0), w_ - 1)
            row = rowof.get((ix, e, int(round(z))))
            if isinstance(row, tuple):
                row, ix = int(row[0]), min(max(int(row[1]), 0), w_ - 1)
            if row is None:
                row = base_r - e
            if 0 <= row < h_ and mask[row, ix]:
                return tuple(float(v) / 255.0 for v in sub_art[row, ix][:3])
            return fbc

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode, True)
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "chest":
        mask = largest_blob(mask)
        vox, lid_row, rowof, D = build_chest(mask, args.lid_frac, args.squash)
        if not vox:
            sys.exit("nothing in that rect")
        h_, w_ = mask.shape
        ys_, xs_ = np.where(mask)
        base_r = int(ys_.max())
        print(f"  chest: {int(xs_.max() - xs_.min() + 1)}px wide, "
              f"{int(base_r - ys_.min() + 1)}px tall, depth {D}px")
        print(f"  lid starts at row {lid_row} (--lid-frac {args.lid_frac}); "
              f"box body below, barrel lid above")
        fbc = tuple(float(v) / 255.0 for v in sub_art[mask].mean(axis=0)[:3])

        def colour_of(x, z, y=None):
            e = int(y or 0)
            ix = min(max(int(round(x)), 0), w_ - 1)
            row = rowof.get((ix, e, int(round(z))))
            if isinstance(row, tuple):
                row, ix = int(row[0]), min(max(int(row[1]), 0), w_ - 1)
            if row is None:
                row = base_r - e
            if 0 <= row < h_ and mask[row, ix]:
                return tuple(float(v) / 255.0 for v in sub_art[row, ix][:3])
            return fbc

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode, True)
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "building":
        # Roof is the lit horizontal surface, wall is everything below it.
        if args.wall_materials:
            # Naming the wall beats inferring it. A drawn roof often has a
            # shaded underside in its own ramp, and that underside sits lower
            # than the wall it overhangs -- so "below the roof" quietly
            # deletes the front elevation, which is the one measurement a
            # building actually gives you.
            want = {int(v) for v in args.wall_materials.split(",")}
            wall = mask & np.isin(mat[y0:y1, x0:x1], list(want))
            roof = mask & ~wall
        else:
            bright, _dark = split_top_side(sub_art.astype(float), mask)
            blobs = _blobs(bright)
            roof = np.zeros_like(mask)
            if blobs:
                big = max(blobs, key=lambda b: b[0])
                roof[big[1], big[2]] = True
            else:
                roof = bright
            wall = mask & ~roof
        # Only wall BELOW the roof is the front elevation; anything above it
        # is roof shading, not a wall, and folding it up builds a phantom
        # storey on the far side of the building.
        if not args.wall_materials:
            for x in range(mask.shape[1]):
                rc = np.where(roof[:, x])[0]
                if len(rc):
                    wall[:rc.max() + 1, x] = False
        _PLINTH_LAST[0] = 0
        vox, heights, wall_rows, roof_top, roof_rise = build_building(
            roof, wall, args.height if args.height != 8 else 0, art=sub_art,
            matsub=mat[y0:y1, x0:x1])
        if not vox:
            sys.exit("no wall found below the roof -- widen --cells")
        hs = sorted(heights.values())
        print(f"  roof {int(roof.sum())}px, wall {int(wall.sum())}px")
        plinth_d = _PLINTH_LAST[0]
        if plinth_d > 0:
            print(f"  plinth MEASURED at {plinth_d}px: a base course of a "
                  f"different stone, standing {PLINTH_OUT}px proud")
        if roof_rise > 0:
            print(f"  roof rise MEASURED at {roof_rise}px "
                  f"(drawn band less its own depth)")
        else:
            print("  roof is flat (drawn band no deeper than the plan)")
        print(f"  height MEASURED from the drawn wall: "
              f"{hs[0]}..{hs[-1]}px, median {int(np.median(hs))}px "
              f"across {len(heights)} columns")
        h_, w_ = mask.shape

        def colour_of(x, z, y=None):
            ix = min(max(int(round(x)), 0), w_ - 1)
            iz = min(max(int(round(z)), 0), h_ - 1)
            if y is not None and roof[iz, ix] and y >= heights.get(ix, 0):
                # the pitched courses keep the roof's own colour
                return tuple(float(v) / 255.0 for v in sub_art[iz, ix][:3])
            # Only the SOUTH face is drawn. The east, west and north faces
            # have no source at all, so they clone the front elevation from
            # the nearest column that has one -- rather than falling through
            # to the plan view, which at the edges of the object is grass.
            rr_ = wall_rows.get(ix)
            if rr_ is None:
                for k in range(1, w_):
                    if ix - k in wall_rows:
                        rr_ = wall_rows[ix - k]
                        ix = ix - k
                        break
                    if ix + k in wall_rows:
                        rr_ = wall_rows[ix + k]
                        ix = ix + k
                        break
            if rr_:
                r0, r1 = rr_
                row = r1 - int(y or 0)
                row = min(max(row, r0), r1)
                return tuple(float(v) / 255.0 for v in sub_art[row, ix][:3])
            return tuple(float(v) / 255.0 for v in sub_art[iz, ix][:3])

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode,
                            args.mode in ("upright", "stump", "dome", "cylinder"))
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        print("  The wall is folded up one drawn row per voxel, not cloned:")
        print("  it was drawn at the height it actually is.")
        return
    if args.mode in ("stump", "dome"):
        # The cut face is the bright blob at the middle of the object; the
        # roots are what the silhouette does near the ground. Both come from
        # the same drawing, read at two different radii.
        # The cut face is the top of the object's own palette ramp, not
        # whatever is brightest on screen. Luminance fails here because the
        # cut and the bark are both brown in this area's palette; the ramp
        # position separates them, the same rule the staircase uses.
        sub_shd = shd[y0:y1, x0:x1]
        top_m = np.zeros_like(mask)
        for m_ in sorted(set(sub_mat_ids := set(
                int(v) for v in mat[y0:y1, x0:x1][mask].ravel()))):
            mm = mask & (mat[y0:y1, x0:x1] == m_)
            vals = sorted(set(sub_shd[mm].ravel().tolist()))
            if len(vals) < 2:
                continue
            top_m |= mm & (sub_shd >= vals[-2])
        bright_all = top_m.copy()
        blobs = _blobs(top_m)
        if blobs:
            # Keep the cut face, which is the blob covering the middle.
            cyc, cxc = np.array(np.where(mask)).mean(axis=1)
            best = min(blobs, key=lambda b: (abs(b[1].mean() - cyc)
                                             + abs(b[2].mean() - cxc)) / max(1, b[0]))
            top_m = np.zeros_like(mask)
            top_m[best[1], best[2]] = True
        frac = int(top_m.sum()) / max(1, int(mask.sum()))
        if frac < 0.10 or frac > 0.85:
            top_m = erode(mask, max(1, args.clean or 3))
            bright_all = top_m.copy()
        # Filling the bright blob's holes does not recover the cut face: the
        # split runs OUT to the rim, so the bright ring is broken and there is
        # nothing enclosed to fill. The traced radius is the honest boundary --
        # the cut face is every pixel of the object inside its own outline,
        # and that carries the rings, the cracks and the split with it.
        top_face = None   # set below, once the trace exists
        if args.mode == "dome":
            vox, centre, R_base, R_top = build_dome(mask, top_m, args.height)
        else:
            vox, centre, R_base, R_top = build_flared(mask, top_m, args.height,
                                                      top_frac=args.top_frac)
        if args.trace:
            trace_png(args.trace, sub_art, mask, top_m, centre, R_base, R_top)
            print(f"  traced outline -> {args.trace}  "
                  f"(cyan = cut face, orange = trunk, lines = fitted radii)")
        crown = "crown" if args.mode == "dome" else "cut face"
        where = "outward at the base" if args.mode == "stump" else "at the widest ring"
        print(f"  {crown} radius {R_top.mean():.1f}px (measured), "
              f"widest radius {R_base.mean():.1f}px, "
              f"flare {R_base.mean() - R_top.mean():+.1f}px {where}")
        print(f"  height {args.height}px (authored), top {args.top_frac:.0%} "
              f"held flat as the cut face")

        cy, cx = centre
        h_, w_ = mask.shape
        bins = len(R_base)
        # Bark is every DARK entry of the ramp, not merely "outside the cut
        # face blob". The cut is drawn with a lit rim whose pixels sit outside
        # that blob, and letting them into the trunk paints pale wood down the
        # sides -- on a stump the only exposed wood is the cut itself.
        h0_, w0_ = mask.shape
        _yy, _xx = np.mgrid[0:h0_, 0:w0_]
        _cy, _cx = centre
        _rr = np.hypot(_yy - _cy, _xx - _cx)
        _bb = (((np.arctan2(_yy - _cy, _xx - _cx) + np.pi)
                / (2 * np.pi)) * len(R_top)).astype(int) % len(R_top)
        top_face = mask & (_rr <= R_top[_bb])

        bark = mask & ~bright_all
        # Foliage overlapping the base is not bark. Anything coloured like the
        # ground around the object belongs to the ground.
        ring = np.zeros_like(mask)
        ring[1:] |= mask[:-1]; ring[:-1] |= mask[1:]
        ring[:, 1:] |= mask[:, :-1]; ring[:, :-1] |= mask[:, 1:]
        ring &= ~mask
        if ring.any():
            gcol = sub_art[ring][..., :3].astype(float).mean(axis=0)
            dist = np.abs(sub_art[..., :3].astype(float) - gcol).sum(axis=2)
            bark &= dist > args.foliage_tol
        if not bark.any():
            bark = mask & ~top_m
        fb = tuple(float(v) / 255.0 for v in (sub_art[bark] if bark.any()
                                              else sub_art[mask]).mean(axis=0)[:3])
        flat_from = args.height - max(1, int(round(args.height * args.top_frac)))

        # The band of bark drawn BELOW the cut face is a picture of the
        # trunk's front surface, seen straight on. That strip is the texture,
        # and it is cloned up the column at its own drawn height -- keeping
        # its horizontal structure, so a root or a split stays where it was
        # drawn instead of scattering.
        #
        # The two things this is not: stretching one pixel up a column, which
        # makes vertical stripes, and interpolating between pixels by height,
        # which invents shades. Cloning repeats real pixels at their own
        # spacing, which is what a tiling texture does.
        rows_with_bark = np.where(bark.any(axis=1))[0]
        face_rows = np.where(top_face.any(axis=1))[0]
        if len(face_rows) and len(rows_with_bark):
            strip = rows_with_bark[rows_with_bark > face_rows.max()]
        else:
            strip = rows_with_bark
        if len(strip) < 2:
            strip = rows_with_bark
        strip = np.asarray(strip, int)

        col_fallback = {}
        for ix_ in range(w_):
            col = np.where(bark[:, ix_])[0]
            if len(col):
                col_fallback[ix_] = int(col[-1])

        def colour_of(x, z, y=None):
            iy = min(max(int(round(z)), 0), h_ - 1)
            ix = min(max(int(round(x)), 0), w_ - 1)
            # Only the very top layer is cut wood: the side of a cut is bark.
            if y is not None and y >= args.height and top_face[iy, ix]:
                return tuple(float(v) / 255.0 for v in sub_art[iy, ix][:3])
            if len(strip):
                # Ground takes the bottom of the strip, then clone upward.
                k = (len(strip) - 1) - (int(y or 0) % len(strip))
                sy = int(strip[k])
                if bark[sy, ix]:
                    return tuple(float(v) / 255.0 for v in sub_art[sy, ix][:3])
            if ix in col_fallback:
                sy = col_fallback[ix]
                return tuple(float(v) / 255.0 for v in sub_art[sy, ix][:3])
            return fb

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode,
                            args.mode in ("upright", "stump", "dome", "cylinder"))
        print(f"  mask {int(mask.sum())} px traced in {bins} directions")
        print(f"  {len(vox)} voxels, {nf} faces -> {args.out}")
        return
    if args.mode == "cylinder":
        # A stump drawn from straight above shows its cut face and almost no
        # bark, so the radius is measured and the height is not. Saying which
        # is which matters more than the number: a top-down tileset simply
        # does not record how tall anything is.
        top_m, side_m = split_top_side(sub_art.astype(float), mask)
        if int(top_m.sum()) < 0.3 * int(mask.sum()):
            # Drawn from straight above with no bark showing, so there is no
            # top/side split to find and the whole footprint IS the cut face.
            # Forcing a split here invents a side out of whatever pixel
            # happened to be brightest, which is how a stump became one voxel.
            top_m, side_m = mask, np.zeros_like(mask)
        vox, cyl_h, cyl_r = build_cylinder(top_m, side_m, args.squash,
                                           args.height)
        print(f"  cut face {int(top_m.sum())}px -> radius {cyl_r:.1f}px "
              f"(measured); height {cyl_h}px (authored via --height)")
        # build_cylinder centres depth on zero; the art is indexed from zero.
        # Without putting those back in the same frame every side voxel falls
        # off the image and comes back grey.
        _ys, _xs = np.where(top_m)
        _cz = float(_ys.mean())
        _h, _w = mask.shape
        _fb = sub_art[mask].mean(axis=0) / 255.0 if mask.any() else np.array([.6, .6, .6])

        def _rim(x, z):
            """Walk out along the radius to the last drawn pixel: the side of a
            top-down object is only ever described by its own edge."""
            import math
            n = math.hypot(x - (_xs.mean()), z)
            if n < 1e-6:
                return None
            ux, uz = (x - _xs.mean()) / n, z / n
            last = None
            for t in range(0, int(cyl_r) + 2):
                px_, pz_ = _xs.mean() + ux * t, _cz + uz * t
                iy, ix = int(round(pz_)), int(round(px_))
                if 0 <= iy < _h and 0 <= ix < _w and mask[iy, ix]:
                    last = (iy, ix)
            return last

        def colour_of(x, z, y=None):
            if y is not None and y >= cyl_h:
                iy, ix = int(round(_cz + z)), int(round(x))
                if 0 <= iy < _h and 0 <= ix < _w and mask[iy, ix]:
                    return tuple(float(v) / 255.0 for v in sub_art[iy, ix])
            hit = _rim(x, z)
            if hit:
                return tuple(float(v) / 255.0 for v in sub_art[hit])
            return tuple(float(v) for v in _fb)

        nv, nf = write_obj_h(args.out, vox, colour_of)
        if getattr(args, "place", False):
            write_placement(args.out, r, x0, y0, x1, y1, args.mode,
                            args.mode in ("upright", "stump", "dome", "cylinder"))
        ys_ = sorted({v[1] for v in vox})
        print(f"  mask {int(mask.sum())} px (background materials {sorted(bg)})")
        print(f"  {len(vox)} voxels, {nf} faces after interior culling -> {args.out}")
        print(f"  heights present: {ys_[:1]}..{ys_[-1:]}  footprint "
              f"{int(mask.any(0).sum())}x{int(mask.any(1).sum())} px")
        return
    elif args.mode == "slab":
        vox = build_slab(mask, args.height)
    elif args.mode == "taper":
        vox = build_taper(mask, args.height, args.inset_every)
    else:
        vox = build_revolve(mask, args.squash, args.cap_rows, args.base_rows)

    nv, nf = write_obj(args.out, vox, colour_of)
    ys = sorted({v[1] for v in vox})
    print(f"  {Path(args.path).name} cells {args.cells}  mode {args.mode}")
    print(f"  mask {int(mask.sum())} px (background materials {sorted(bg)})")
    print(f"  {len(vox)} voxels, {nf} faces after interior culling -> {args.out}")
    print(f"  heights present: {ys[:1]}..{ys[-1:]}  footprint "
          f"{int(mask.any(0).sum())}x{int(mask.any(1).sum())} px")



# ---------------------------------------------------------------------------
# catalogue: find the distinct drawn objects, so shapes can be assigned to a
# LIST rather than hunted for by eye
# ---------------------------------------------------------------------------

def components(mask, min_px, max_px, drop_border=True):
    h, w = mask.shape
    seen = np.zeros_like(mask)
    out = []
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            comp = []
            touch = False
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                if y in (0, h - 1) or x in (0, w - 1):
                    touch = True
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if drop_border and touch:
                continue
            if min_px <= len(comp) <= max_px:
                out.append(comp)
    return out


def signature(comp, mat):
    """Identity for a drawn object: its normalised silhouette plus materials.

    Two archways of the same design in different rooms must land in one group,
    while an archway and a doorway of similar size must not. Silhouette alone
    confuses them; materials alone confuse everything sharing a palette. Both
    together separate them, and normalising the silhouette to a fixed grid
    makes the comparison independent of where the thing sits.
    """
    ys = [y for y, _ in comp]
    xs = [x for _, x in comp]
    y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
    h, w = y1 - y0 + 1, x1 - x0 + 1
    G = 12
    grid = np.zeros((G, G), np.uint8)
    mats = set()
    for y, x in comp:
        gy = min(G - 1, (y - y0) * G // h)
        gx = min(G - 1, (x - x0) * G // w)
        grid[gy, gx] = 1
        v = int(mat[y, x])
        if v >= 0:
            mats.add(v)
    return (grid.tobytes(), tuple(sorted(mats)), w, h)


def objectness(comp, mat, mask_shape, ring=3):
    """How much a component stands APART from what surrounds it.

    Ranking by frequency alone puts terrain at the top: a room has far more
    grass variants and puddles than archways, and they are all "not the
    dominant material". What separates an object from a patch of ground is
    that its materials do not continue into the ring around it -- a puddle
    shades into the lawn, a fountain does not.

    Returns 0..1: the share of the surrounding ring whose material never
    appears inside the component.
    """
    h, w = mask_shape
    inside = set()
    cells = set()
    for y, x in comp:
        cells.add((y, x))
        v = int(mat[y, x])
        if v >= 0:
            inside.add(v)
    ys = [y for y, _ in comp]; xs = [x for _, x in comp]
    y0, y1 = max(0, min(ys) - ring), min(h - 1, max(ys) + ring)
    x0, x1 = max(0, min(xs) - ring), min(w - 1, max(xs) + ring)
    out_n = diff = 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if (y, x) in cells:
                continue
            v = int(mat[y, x])
            if v < 0:
                continue
            out_n += 1
            diff += (v not in inside)
    return diff / float(out_n) if out_n else 0.0


def suggest_mode(w, h, fill):
    """A first guess at the right primitive, from proportions alone.

    Deliberately crude: it is a starting column in a table a human edits, not
    a classifier. Proportions cannot tell a fountain from a well -- but they
    can tell a long thin run (a bridge) from a compact blob (a plant), and
    that is enough to sort the list.
    """
    ar = w / max(1, h)
    if ar > 2.2 or ar < 0.45:
        return "slab", "long thin run -- bridge / wall band"
    if fill > 0.82:
        return "taper", "solid compact -- stump / pot / block"
    if fill < 0.45:
        return "slab", "open silhouette -- archway / frame / plant"
    return "taper", "compact -- prop"


def cmd_catalogue(args):
    """Catalogue the distinct drawn objects across a set of rooms.

    This exists because assigning 3D shapes by hand is only tractable against
    a LIST. DramaticShape generated one catalogue entry per distinct building
    drawing before authoring a single height, and that is what made their band
    tables possible. The same applies here to archways, doors, fountains,
    bridges and plants: find them once, group identical drawings together, and
    then decide shapes per GROUP rather than per placement.
    """
    from PIL import Image
    paths = sorted(Path(args.dumps).glob("room_*.tmcr")) \
        if Path(args.dumps).is_dir() else [Path(args.dumps)]
    if args.limit:
        paths = paths[:args.limit]

    groups = {}
    prog = RE.Progress(len(paths), label="catalogue", step=10)
    for p in paths:
        prog.update(1, p.stem)
        try:
            r = RE.load_room(p)
        except Exception:
            continue
        d = SH.decompose_room(r, args.layer)
        if d is None:
            continue
        mat = d[0]
        art = RE.room_art_rgb(r, args.layer)
        if art is None:
            continue
        H = min(r.cells_h * 16, mat.shape[0])
        W = min(r.cells_w * 16, mat.shape[1])
        m = mat[:H, :W]
        vals, counts = np.unique(m[m >= 0], return_counts=True)
        if not len(vals):
            continue
        # Ground = the materials covering most of the room. Objects are what
        # is left once the field they stand on is removed.
        order = np.argsort(-counts)
        keep, acc = set(), 0
        for i in order:
            keep.add(int(vals[i]))
            acc += counts[i]
            if acc / counts.sum() >= args.ground_cover:
                break
        mask = (m >= 0)
        for g in keep:
            mask &= (m != g)
        for comp in components(mask, args.min_px, args.max_px):
            sig = signature(comp, m)
            g = groups.setdefault(sig, {"n": 0, "where": [], "comp": comp,
                                        "room": r, "art": art,
                                        "obj": objectness(comp, m, m.shape)})
            g["n"] += 1
            if len(g["where"]) < 6:
                ys = [y for y, _ in comp]; xs = [x for _, x in comp]
                g["where"].append((r.area, r.room,
                                   min(xs) // 16, min(ys) // 16,
                                   max(xs) // 16 + 1, max(ys) // 16 + 1))
    prog.done(f"{len(groups)} distinct drawings")

    # Rank by distinctness first, then by how often it occurs: a thing that
    # stands apart AND recurs is a designed object, not a patch of scenery.
    rows = sorted(groups.items(),
                  key=lambda kv: (-round(kv[1]["obj"], 2), -kv[1]["n"]))
    rows = [kv for kv in rows if kv[1]["obj"] >= args.min_objectness]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    thumbs = []
    with (out / "objects.txt").open("w") as f:
        f.write("# distinct drawn objects, most common first\n")
        f.write("# group  count  w x h  fill  apart  suggested  "
                "where (area,room,cx0,cy0,cx1,cy1)\n")
        for i, (sig, g) in enumerate(rows[:args.show]):
            comp = g["comp"]
            ys = [y for y, _ in comp]; xs = [x for _, x in comp]
            y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
            w_, h_ = x1 - x0 + 1, y1 - y0 + 1
            fill = len(comp) / float(w_ * h_)
            mode, why = suggest_mode(w_, h_, fill)
            a, rm, cx0, cy0, cx1, cy1 = g["where"][0]
            f.write(f"{i:3d}  {g['n']:4d}  {w_:3d}x{h_:<3d}  {fill:.2f}  "
                    f"{g['obj']:.2f}  {mode:6s}  "
                    f"{a},{rm},{cx0},{cy0},{cx1},{cy1}   # {why}\n")
            pad = 2
            sub = g["art"][max(0, y0 - pad):y1 + 1 + pad,
                           max(0, x0 - pad):x1 + 1 + pad].astype(np.uint8)
            if sub.size:
                thumbs.append((i, g["n"], Image.fromarray(sub)))

    if thumbs:
        CELL = args.thumb
        cols = 8
        rowsn = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * CELL, rowsn * CELL), (20, 20, 26))
        for k, (i, n, im) in enumerate(thumbs):
            s = max(1, min(CELL // max(im.width, im.height), 8))
            im2 = im.resize((im.width * s, im.height * s), Image.NEAREST).convert("RGB")
            gx, gy = (k % cols) * CELL, (k // cols) * CELL
            sheet.paste(im2, (gx + (CELL - im2.width) // 2,
                              gy + (CELL - im2.height) // 2))
        sheet.save(out / "objects.png")

    print(f"\n  {len(groups)} distinct drawings across {len(paths)} room(s)")
    print(f"  catalogue -> {out/'objects.txt'}   contact sheet -> {out/'objects.png'}")
    print("  The 'suggested' column is a starting guess from proportions only.")
    print("  Name them from the sheet, then fit with `shapefit.py fit --cells ...`.")


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit", help="fit one object")
    f.add_argument("path")
    f.add_argument("--cells", required=True, help="cx0,cy0,cx1,cy1")
    f.add_argument("--layer", type=int, default=0)
    f.add_argument("--mode",
                   choices=("slab", "taper", "revolve", "cylinder", "stump",
                            "dome", "building", "upright", "chest",
                            "torch", "rock", "tree", "lotus"),
                   default="taper")
    f.add_argument("--top-frac", type=float, default=0.25,
                   help="fraction of the height held flat as the cut face")
    f.add_argument("--relief", type=float, default=0.0,
                   help="voxels of shallow relief taken from the drawn "
                        "shading; 1.5 is a good start, 0 disables it")
    f.add_argument("--place", action="store_true",
                   help="also write <out>.place.json, so `scene` can put this "
                        "object back where it came from")
    f.add_argument("--lid-frac", type=float, default=0.42,
                   help="chest mode: fraction of the height that is the lid")
    f.add_argument("--section", choices=("round", "box"), default="round",
                   help="cross-section of an upright object: round revolves "
                        "the profile (mushrooms, trees), box keeps corners "
                        "(chests, crates, buildings)")
    f.add_argument("--vscale", type=float, default=1.0,
                   help="stretch drawn height into real height; the elevation "
                        "is foreshortened by the camera tilt and Link (24px "
                        "tall) is the ruler. 1.25 makes a 20px doorway 25px")
    f.add_argument("--keep-outline", action="store_true",
                   help="keep the near-black outline stroke; on by default "
                        "for --mode upright, where it is part of the drawing")
    f.add_argument("--foot-frac", type=float, default=0.0,
                   help="fraction of the height at the BOTTOM that is ground "
                        "contact seen from above; it lies flat instead of "
                        "folding up (a mushroom stem below its door)")
    f.add_argument("--features", default="",
                   help="material ids drawn as chimneys, windows and doors: "
                        "pushed out above the shoulder, sunk in below it")
    f.add_argument("--feature-depth", type=float, default=0.0,
                   help="extra voxels of displacement for --features")
    f.add_argument("--cap-frac", type=float, default=0.0,
                   help="fraction of the height that is drawn from above. "
                        "0 (the default) MEASURES it: the shoulder is the "
                        "first row that reaches full width, because the "
                        "silhouette only widens while you are seeing the top")
    f.add_argument("--dome", type=float, default=1.0,
                   help="how far the cap lifts; 1.0 is a hemisphere")
    f.add_argument("--wall-materials", default="",
                   help="building mode: material ids that are the front wall; "
                        "everything else in the mask is roof")
    f.add_argument("--foliage-tol", type=float, default=90.0,
                   help="colour distance from the surrounding ground below "
                        "which a pixel is foliage, not part of the object")
    f.add_argument("--trace", default="",
                   help="write a PNG of the traced outline for inspection")
    f.add_argument("--height", type=int, default=10)
    f.add_argument("--inset-every", type=int, default=3)
    f.add_argument("--squash", type=int, default=100)
    f.add_argument("--cap-rows", type=int, default=0)
    f.add_argument("--base-rows", type=int, default=0)
    f.add_argument("--ground-frac", type=float, default=0.02)
    f.add_argument("--materials", default="",
                   help="comma-separated material ids that ARE the object; "
                        "use when an effect or halo shares its cells")
    f.add_argument("--clean", type=int, default=0)
    f.add_argument("--force", action="store_true",
                   help="build even when the tile fails its own sanity check (e.g. a torch with no fire)")
    f.add_argument("--keep-border", action="store_true",
                   help="keep components that run off the rect edge")
    f.add_argument("--out", default="object.obj")
    f.set_defaults(func=cmd_fit)
    st = sub.add_parser("steps", help="fit a staircase from riser/tread bands")
    st.add_argument("path")
    st.add_argument("--cells", required=True)
    st.add_argument("--layer", type=int, default=0)
    st.add_argument("--riser", type=int, default=3,
                    help="voxel height of one step; the only authored number")
    st.add_argument("--min-tread", type=int, default=6,
                    help="smallest patch accepted as a tread, in pixels")
    st.add_argument("--margin", type=int, default=4,
                    help="width of the rect border band taken to be ground")
    st.add_argument("--void-lum", type=float, default=40.0,
                    help="luminance at or below which a broad field is a hole")
    st.add_argument("--out", default="steps.obj")
    st.set_defaults(func=cmd_steps)

    c = sub.add_parser("catalogue", help="find the distinct drawn objects")
    c.add_argument("dumps")
    c.add_argument("--layer", type=int, default=0)
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--min-px", type=int, default=40)
    c.add_argument("--max-px", type=int, default=4000)
    c.add_argument("--ground-cover", type=float, default=0.55)
    c.add_argument("--show", type=int, default=48)
    c.add_argument("--min-objectness", type=float, default=0.45,
                   help="0 keeps terrain patches; 0.45 keeps things that stand apart")
    c.add_argument("--thumb", type=int, default=112)
    c.add_argument("--out", default="objects")
    c.set_defaults(func=cmd_catalogue)

    tl = sub.add_parser("tiles",
                        help="find everything include/tiles.h names, "
                             "across every room")
    tl.add_argument("dir")
    tl.add_argument("--repo", default=None)
    tl.add_argument("--grep", default="")
    tl.add_argument("--raw", action="store_true",
                    help="keep every comment, including behaviour notes that "
                         "name a handler rather than a thing")
    tl.add_argument("--min-cells", type=int, default=1)
    tl.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    tl.add_argument("--top", type=int, default=25)
    tl.add_argument("--manifest", default="",
                    help="also write a scene manifest, materials resolved "
                         "per find")
    tl.add_argument("--mode", default="upright")
    tl.add_argument("--extra", default="")
    tl.add_argument("--pad", type=int, default=1,
                    help="cells of margin around each find")
    tl.add_argument("--out", default="objects/named_tiles.txt")
    tl.set_defaults(func=cmd_tiles)


    fl = sub.add_parser("findlike",
                        help="find every other instance of one building, "
                             "matched on its tile indices")
    fl.add_argument("path")
    fl.add_argument("dir")
    fl.add_argument("--cells", required=True)
    fl.add_argument("--layer", type=int, default=0)
    fl.add_argument("--materials", default="")
    fl.add_argument("--min-match", type=float, default=0.75)
    fl.add_argument("--mode", default="upright",
                    help="fit mode to write into the manifest")
    fl.add_argument("--extra", default="",
                    help="extra options to write on every manifest line")
    fl.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    fl.add_argument("--out", default="objects/like.txt")
    fl.set_defaults(func=cmd_findlike)


    sv = sub.add_parser("survey",
                        help="find and measure built things across every room")
    sv.add_argument("dir")
    sv.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    sv.add_argument("--kind", choices=("overhang", "solid"), default="")
    sv.add_argument("--limit", type=int, default=0)
    sv.add_argument("--out", default="objects/buildings.txt")
    sv.set_defaults(func=cmd_survey)


    pr = sub.add_parser("profile",
                        help="print an object's width-per-row silhouette")
    pr.add_argument("path")
    pr.add_argument("--cells", required=True)
    pr.add_argument("--layer", type=int, default=0)
    pr.add_argument("--materials", default="")
    pr.add_argument("--ground-frac", type=float, default=0.02)
    pr.add_argument("--step", type=int, default=2)
    pr.add_argument("--features", default="",
                    help="material ids of things drawn on the surface, to be "
                         "sorted into projecting and flush")
    pr.set_defaults(func=cmd_profile)


    sc = sub.add_parser("scene",
                        help="assemble many fitted objects into one room")
    sc.add_argument("manifest")
    sc.add_argument("--rooms", default="vrdump",
                    help="directory holding the .tmcr files")
    sc.add_argument("--terrain", default="",
                    help="also extrude this room's ground under the objects")
    sc.add_argument("--overlay", action="store_true",
                    help="include layer 1 (canopies, bridge decks) in terrain")
    sc.add_argument("--subdiv", type=int, default=1)
    sc.add_argument("--out", default="objects/scene.obj")
    sc.add_argument("--jobs", type=int, default=os.cpu_count() or 2,
                    help="fit this many objects at once")
    sc.set_defaults(func=cmd_scene)


    rc = sub.add_parser("recipes",
                        help="which shape profile each catalogued name gets")
    rc.add_argument("--catalogue", default="objects/entities.txt")
    rc.add_argument("--verbose", action="store_true")
    rc.add_argument("--unmatched", action="store_true")
    rc.add_argument("--actors", action="store_true")
    rc.set_defaults(func=cmd_recipes)


    sp = sub.add_parser("sprite", help="fit a solid to a harvested sprite PNG")
    sp.add_argument("path")
    sp.add_argument("--mode", choices=("cylinder", "slab", "taper"),
                    default="cylinder")
    sp.add_argument("--squash", type=int, default=100,
                    help="depth as a percent of width; 100 keeps it circular")
    sp.add_argument("--height", type=int, default=8)
    sp.add_argument("--inset-every", type=int, default=3)
    sp.add_argument("--out", default="sprite.obj")
    sp.set_defaults(func=cmd_sprite)


    o = sub.add_parser("openings",
                       help="scan a dump directory for raised surfaces with holes")
    o.add_argument("dir")
    o.add_argument("--holes", default="24,1000000",
                   help="keep holes in this pixel-area range, lo,hi")
    o.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    o.add_argument("--out", default="objects/openings.txt")
    o.set_defaults(func=cmd_openings)
    return ap


def main():
    a = build_parser().parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
