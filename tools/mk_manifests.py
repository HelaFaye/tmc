#!/usr/bin/env python3
"""Rebuild every object manifest from the current room dumps.

One generator for all object classes, because they all need the same three
things and each was learned the hard way:

  * per-CELL entries, not clump rects. Padded bounding boxes often pointed
    a whole cell away from the object.
  * per-cell material resolution that ignores neighbours of the SAME class.
    Objects come in packed grids, so without that an object's own colours
    get classified as background.
  * the LAYER recorded per cell. Some objects sit on layer 1, and fitting
    one from layer 0 builds the floor instead.

Tile type is behavioural, not visual, so each class also carries an art
test: TORCH and TORCH_LIT are the same type family but 0x77 is a floor
button, told apart by whether the tile has fire in it.
"""
import argparse
import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_explore as RE
import shading as SH
import shapefit as SF
import surfaces as SU

# name -> (tile types, fit mode, minimum blob px, art test)
PLURAL = {"chest": "chests", "torch": "torches", "rock": "rocks"}

CLASSES = {
    "chest": ((0x73, 0x74), "chest", 90, None),
    "torch": ((0x76, 0x77), "torch", 60, "fire"),
    "rock":  ((0x55,),      "rock",  60, None),
}


def find_trees(mat, art, li):
    """Trees have no tile type, so find them in the ART.

    Every other class here is located by tile type, but no type in tiles.h
    marks a tree -- they are plain scenery in the tilemap. What a tree does
    have is a big compact blob of one non-ground material with warm bark
    pixels tucked under its lower edge, which nothing else in these rooms
    looks like.

    Yields (cx, cy, materials, w, h) for each canopy found.
    """
    import collections as _c
    H, W = mat.shape
    rgbf = art[:, :, :3].astype(float)
    lumf = rgbf.mean(axis=2)
    room_med = float(np.median(lumf))
    warm = rgbf[:, :, 0] > rgbf[:, :, 2] + 8
    counts = _c.Counter(mat.ravel().tolist())
    ground = {m for m, c in counts.most_common(3)}
    out = []
    for m, c in counts.items():
        if m < 0 or m in ground or c < 600:
            continue
        for area, ys, xs in SF._blobs(mat == m):
            if not (700 <= area <= 6000):
                continue
            w = xs.max() - xs.min() + 1
            h = ys.max() - ys.min() + 1
            if w < 24 or h < 24:
                continue
            if not (0.55 <= h / float(w) <= 1.9):
                continue
            if area / float(w * h) < 0.45:          # compact, not a sprawl
                continue
            # Foliage is DARK as well as green. Green alone still accepted
            # bright dungeon floors, grass and pond water -- green is the
            # commonest colour in this game. A canopy is in shadow under its
            # own leaves, so it sits well below the room's median brightness.
            selm = (mat == m)
            selm[:ys.min(), :] = False
            selm[ys.max() + 1:, :] = False
            if selm.any() and lumf[selm].mean() > room_med * 0.85:
                continue
            # Foliage is GREEN. Without this the compact-blob-with-warm-
            # pixels-beneath test also accepted stone arches, a cave wall,
            # a fire and a stone tablet -- anything round-ish with a warm
            # edge. Green dominance costs nothing and removes all of them.
            sel = (mat == m)
            sel[:ys.min(), :] = False
            sel[ys.max() + 1:, :] = False
            px = rgbf[sel]
            if not len(px):
                continue
            g = px[:, 1].mean()
            if not (g > px[:, 0].mean() + 6 and g > px[:, 2].mean() + 6):
                continue
            band = slice(int(ys.max()) + 1, min(H, int(ys.max()) + 8))
            cols = slice(int(xs.min()), int(xs.max()) + 1)
            if warm[band, cols].sum() < 12:          # no bark under it
                continue
            cx = int(round((xs.min() + xs.max()) / 2.0 / 16))
            cy = int(round((ys.min() + ys.max()) / 2.0 / 16))
            out.append((cx, cy, [int(m)], int(w), int(h)))
    return out


def resolve_materials(mat, cx, cy, same_cells):
    """Materials of this cell, minus whatever surrounds it."""
    Hm, Wm = mat.shape
    y0, x0 = cy * 16, cx * 16
    cell = mat[y0:y0 + 16, x0:x0 + 16]
    ry0, ry1 = max(0, y0 - 16), min(Hm, y0 + 32)
    rx0, rx1 = max(0, x0 - 16), min(Wm, x0 + 32)
    ring = mat[ry0:ry1, rx0:rx1].copy().astype(int)
    ring[y0 - ry0:y0 - ry0 + 16, x0 - rx0:x0 - rx0 + 16] = -1
    for (ncx, ncy) in same_cells:
        if (ncx, ncy) == (cx, cy):
            continue
        ay, ax = ncy * 16 - ry0, ncx * 16 - rx0
        if -16 < ay < ring.shape[0] and -16 < ax < ring.shape[1]:
            ring[max(0, ay):ay + 16, max(0, ax):ax + 16] = -1
    rc = collections.Counter(ring[ring >= 0].ravel().tolist())
    rn = max(1, sum(rc.values()))
    bg = {m for m, c in rc.items() if c / rn >= 0.15}
    return sorted(int(m) for m, c in
                  collections.Counter(cell.ravel().tolist()).items()
                  if int(m) not in bg and c >= 6 and int(m) >= 0)


def build(dump, outdir, only=None):
    rooms = sorted(Path(dump).glob("room_*.tmcr"))
    made = {}
    # Trees are OPT-IN (--only tree) because the finder is not trustworthy.
    # They have no tile type, and three art-based attempts -- compact blob,
    # then green, then green-and-dark -- returned 936, 316 and 63 candidates
    # that were mostly dungeon floors, lily pads, lotus flowers, stone
    # arches and a bed. Finding them needs tile IDENTITY per area, not
    # colour. Until then they stay out of the batch rather than poisoning it.
    if only and "tree" in only:
        lines, seen = [], set()
        bar = RE.Progress(len(rooms), "tree  ")
        for p in rooms:
            bar.update(1)
            try:
                r = RE.load_room(p)
            except Exception:
                continue
            for li, L in enumerate(r.layers):
                if not L.get("present") or L.get("tile") is None:
                    continue
                d = SH.decompose_room(r, li)
                if d is None:
                    continue
                art = RE.room_art_rgb(r, li)
                for cx, cy, mats, w, h in find_trees(d[0], art, li):
                    key = (p.name, li, cx, cy)
                    if key in seen:
                        continue
                    seen.add(key)
                    n = max(2, int(np.ceil(max(w, h) / 32.0)) + 1)
                    lines.append(
                        f"{p.name}  {max(0,cx-n)},{max(0,cy-n)},{cx+n},{cy+n}  tree  "
                        f"materials={','.join(map(str, mats))} layer={li}"
                        f"   # TREE canopy {w}x{h}px")
        hdr = ["# tree manifest. Trees have NO tile type -- located in the art",
               "# by a compact non-ground blob with warm bark pixels beneath.",
               f"# {len(lines)} found.",
               "# room              cells        mode     options"]
        (Path(outdir) / "trees.scene").write_text("\n".join(hdr + lines) + "\n")
        made["tree"] = (len(lines), 0, {})
        print(f"  {'tree':6s} {len(lines):5d} found (art-based, no tile type)")

    for name, (types, mode, minpx, art_test) in CLASSES.items():
        if only and name not in only:
            continue
        lines, skipped, rejected = [], collections.Counter(), 0
        bar = RE.Progress(len(rooms), f"{name:6s}")
        for p in rooms:
            bar.update(1)
            try:
                r = RE.load_room(p)
            except Exception:
                skipped["unreadable room"] += 1
                continue
            for li, L in enumerate(r.layers):
                if not L.get("present") or L.get("tile") is None:
                    continue
                tile, tt = L["tile"], L["tiletype"]
                tmap = tt[np.clip(tile, 0, len(tt) - 1)]
                ys, xs = np.where(np.isin(tmap, list(types)))
                if not len(ys):
                    continue
                d = SH.decompose_room(r, li)
                if d is None:
                    skipped["no palette"] += 1
                    continue
                mat = d[0]
                art = RE.room_art_rgb(r, li)
                same = [(int(a), int(b)) for a, b in zip(xs, ys)]
                # Cells that must never be drawn into another object's rect.
                # A torch's rect runs one cell past its own, and in
                # room_72_09 that reached down onto a floor button and would
                # have built the button into the torch's base. The game says
                # which cells those are; see tools/surfaces.py.
                # BUTTONS ONLY. Trimming on pits too cost 44 torches in
                # one pass: there are 82 button cells in the whole game but
                # 24,058 pit cells, and dungeon torches stand right at the
                # edge of pits, so those rects collapsed to a single cell
                # and the fit found nothing in them. A pit beside a torch
                # is scenery; a button under one gets built into its base.
                try:
                    avoid = SU.is_button(r, li)
                except Exception:
                    avoid = None
                for cy, cx in zip(ys, xs):
                    cy, cx = int(cy), int(cx)
                    if (cy + 1) * 16 > mat.shape[0] or (cx + 1) * 16 > mat.shape[1]:
                        skipped["outside room"] += 1
                        continue
                    ca = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                    if art_test == "fire" and SF.fire_fraction(ca) <= 0.0:
                        rejected += 1          # a button, not a torch
                        continue
                    want = resolve_materials(mat, cx, cy, same)
                    if not want:
                        skipped["no materials"] += 1
                        continue
                    cell = mat[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                    m = SF.largest_blob(np.isin(cell, want))
                    if m.sum() < minpx:
                        skipped["blob too small"] += 1
                        continue
                    yy, xx = np.where(m)
                    if (yy.max() - yy.min() + 1) < 8 or (xx.max() - xx.min() + 1) < 8:
                        skipped["wrong shape"] += 1
                        continue
                    ex, ey, note = cx + 1, cy + 1, ""
                    if avoid is not None:
                        if ey < avoid.shape[0] and avoid[ey, cx:ex + 1].any():
                            ey, note = cy, "  # rect trimmed off a button"
                        if ex < avoid.shape[1] and avoid[cy:ey + 1, ex].any():
                            ex, note = cx, "  # rect trimmed off a button"
                    lines.append(
                        f"{p.name}  {cx},{cy},{ex},{ey}  {mode}  "
                        f"materials={','.join(map(str, want))} layer={li}"
                        f"   # {name.upper()} cell {cx},{cy}{note}")
        hdr = [f"# {name} manifest, rebuilt from {dump}.",
               "# One line per CELL. Materials resolved per cell, ignoring",
               "# neighbours of the same class. Layer recorded per cell.",
               f"# {len(lines)} fittable"
               + (f", {rejected} rejected by the art test" if rejected else "")
               + (f", {sum(skipped.values())} skipped" if skipped else "") + ".",
               "# room              cells        mode     options"]
        out = Path(outdir) / f"{PLURAL.get(name, name + 's')}.scene"
        out.write_text("\n".join(hdr + lines) + "\n")
        made[name] = (len(lines), rejected, dict(skipped))
        print(f"  {name:6s} {len(lines):5d} fittable   rejected {rejected:4d}   "
              f"skipped {dict(skipped)}")
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rooms", default="vrdump")
    ap.add_argument("--out", default="objects")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    build(a.rooms, a.out, a.only)
