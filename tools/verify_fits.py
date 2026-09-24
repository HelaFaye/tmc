#!/usr/bin/env python3
"""Check that fitted objects are the right SIZE, not merely that the fitter
exited cleanly.

A scene run reporting "306 placed, 0 failed" once included chests 3 pixels
tall: the command succeeded and the object was wrong. Exit status says
nothing about geometry, so this rebuilds each entry and measures it.
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

BUILDERS = {
    "chest": lambda m, mat, art: SF.build_chest(m)[0],
    "torch": lambda m, mat, art: SF.build_torch(m, mat, art)[0],
    "rock":  lambda m, mat, art: SF.build_boulder(m, mat, art, 0)[0],
}
# class -> (min width, min height, min voxels)
FLOORS = {"chest": (10, 10, 200), "torch": (8, 6, 120), "rock": (8, 2, 100)}


def check(manifest, rooms):
    cache = {}
    dims, bad = [], []
    lines = [l.split("#")[0].strip() for l in open(manifest)]
    lines = [l for l in lines if l]
    for l in lines:
        f = l.split()
        room, cells, mode = f[0], f[1], f[2]
        o = dict(x.split("=", 1) for x in f[3:] if "=" in x)
        li = int(o.get("layer", 0))
        want = [int(v) for v in o["materials"].split(",")]
        key = (room, li)
        if key not in cache:
            try:
                r = RE.load_room(Path(rooms) / room)
                cache[key] = (SH.decompose_room(r, li)[0], RE.room_art_rgb(r, li))
            except Exception:
                cache[key] = None
        if cache[key] is None:
            bad.append((room, cells, "unreadable"))
            continue
        mat, art = cache[key]
        cx0, cy0, cx1, cy1 = (int(v) for v in cells.split(","))
        cell = mat[cy0 * 16:cy1 * 16, cx0 * 16:cx1 * 16]
        ca = art[cy0 * 16:cy1 * 16, cx0 * 16:cx1 * 16]
        mask = SF.largest_blob(np.isin(cell, want))
        try:
            vox = BUILDERS[mode](mask, cell, ca)
        except Exception as e:
            bad.append((room, cells, f"build error: {e}"))
            continue
        if not vox:
            bad.append((room, cells, "empty"))
            continue
        xs = [v[0] for v in vox]; es = [v[1] for v in vox]
        w, h, n = max(xs) - min(xs) + 1, max(es) - min(es) + 1, len(vox)
        dims.append((w, h, n))
        mw, mh, mn = FLOORS.get(mode, (1, 1, 1))
        if w < mw or h < mh or n < mn:
            bad.append((room, cells, f"{w}x{h} {n}vox"))
    return dims, bad, len(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("manifests", nargs="+")
    ap.add_argument("--rooms", default="vrdump")
    a = ap.parse_args()
    worst = 0
    for m in a.manifests:
        dims, bad, total = check(m, a.rooms)
        name = Path(m).stem
        if not dims:
            print(f"{name:10s} NOTHING BUILT of {total}")
            worst = 1
            continue
        ws = sorted(d[0] for d in dims); hs = sorted(d[1] for d in dims)
        vs = sorted(d[2] for d in dims)
        print(f"{name:10s} {len(dims):4d}/{total} built   "
              f"w {ws[0]}..{ws[-1]} (med {ws[len(ws)//2]})   "
              f"h {hs[0]}..{hs[-1]} (med {hs[len(hs)//2]})   "
              f"vox {vs[0]}..{vs[-1]}   undersized {len(bad)}")
        for b in bad[:5]:
            print(f"             {b[0]} {b[1]}: {b[2]}")
        if len(bad) > len(dims) * 0.05:
            worst = 1
    sys.exit(worst)
