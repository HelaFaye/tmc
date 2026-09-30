#!/usr/bin/env python3
"""capture_check.py -- where a built room disagrees with what the game shows.

Renders each room's merged mesh (tilevox.py --merge) from the game's own
camera -- 1:1 oblique, drawn row = depth - height, each surface its own
texture, unlit -- and compares it, 16 px cell by 16 px cell, with the room's
capture (room_capture.py). A cell whose render differs from the picture is
one where the build puts something in the wrong place, at the wrong height,
or leaves it out.

Usage
-----
  python3 tools/capture_check.py DUMPS GEOM [--area 34,2] [--out DIR]

  GEOM  tilevox.py --merge output (area_AA/room_AA_RR.obj)

Writes, to --out (default vr/captures/check/, ignored like the captures):
  room_AA_RR_check.png   the capture, the render, the cells that disagree
  check.json             per room: share of cells that disagree, worst first

Only cells a view saw (and no sprite covers) are judged: elsewhere the
"capture" is the tile art itself. Wall cells are not: the build stands them
up as vertical faces, which the game draws as slanted bands.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refcheck as RC  # noqa: E402
import room_explore as RE  # noqa: E402
import room_capture as CAP  # noqa: E402
import tilevox as TV  # noqa: E402

CELL = 16
BLOCK = 4           # px: compared as block means, so a texture resampled a
                    # pixel off (the atlas) is not a difference
DIFF = 48           # mean |render - capture| over a cell's blocks (sum of RGB)
SEEN_MIN = 0.5      # of a cell's pixels a view saw, for it to be judged
RING = 2            # cells: an enclosed room's shell, from its edge
TALL = 160          # px above the room's north edge a render keeps


def seen_mask(rid, shape, out):
    """Pixels some view saw: from capture.json's placed views."""
    d = json.loads((Path(out) / f"room_{rid}_capture.json").read_text())
    have = np.zeros(shape, bool)
    for v in d["views"]:
        if "dropped" in v:
            continue
        x, y = v.get("placed", v["camera"])
        w = 384 if "strip" not in v else 384 - CAP.GBA_W
        if "strip" in v:
            x += CAP.GBA_W
        have[max(y, 0):y + 160, max(x, 0):x + w] = True
    return have


def shell_ring(cls):
    """Wall cells within RING cells of the room's edge: where an enclosed
    room's shell stands (furniture against it stands further in)."""
    h, w = cls.shape
    yy, xx = np.mgrid[0:h, 0:w]
    edge = np.minimum(np.minimum(yy, h - 1 - yy), np.minimum(xx, w - 1 - xx))
    return (cls == RE.CLASS_WALL) & (edge < RING)


def render_room(obj, r, cls=None):
    """The mesh from the game's camera, cut away above the wall cells: a
    wall stood up hides the floor in front of it, which the game (drawing
    it as a band) does not."""
    V, C, F, T, FT = RC.load_obj(str(obj), uvs=True)
    V = np.asarray(V, float)
    if cls is not None:
        ring = shell_ring(cls)
        keep = []
        for i, f in enumerate(F):
            P = V[list(f)]
            m = P.mean(axis=0)
            cx = int((m[0] - r.origin_x) // CELL)
            cy = int((m[2] - r.origin_y) // CELL)
            raised = P[:, 1].max() > 0.5
            # the front wall stands on its drawn foot line, a cell in
            south = raised and P[:, 2].max() > r.origin_y + (cls.shape[0] - RING - 1) * CELL
            if not (south or (raised and 0 <= cy < ring.shape[0]
                              and 0 <= cx < ring.shape[1] and ring[cy, cx])):
                keep.append(i)
        F = [F[i] for i in keep]
        FT = [FT[i] for i in keep]
    atlas = np.asarray(Image.open(Path(obj).with_suffix(".png")).convert("RGB"))
    W, H = r.cells_w * CELL, r.cells_h * CELL
    box = (r.origin_x, r.origin_x + W, r.origin_y, r.origin_y + H)
    img = RC.render(V, np.asarray(C, float), F, RC.camera(0, 45), box,
                    scale=(1.0, math.sqrt(2)), tall=TALL,
                    tex=(atlas, np.asarray(T, float), FT), lit=False)
    a = np.asarray(img).astype(np.int16)
    return a[TALL:TALL + H, :W]


def check(r, rid, obj, caps, cls=None):
    got = CAP.load_shown(rid, caps)
    if got is None:
        return None
    shown, sprite = got
    shown = shown.astype(np.int16)
    H, W = r.cells_h * CELL, r.cells_w * CELL
    shown, sprite = shown[:H, :W], sprite[:H, :W]
    ren = render_room(obj, r, cls)
    h, w = min(H, ren.shape[0]), min(W, ren.shape[1])
    judge = seen_mask(rid, (H, W), caps)[:h, :w] & ~sprite[:h, :w]
    ch, cw = h // CELL, w // CELL
    h, w = ch * CELL, cw * CELL
    ring = shell_ring(cls) if cls is not None else None
    judge = judge[:h, :w]
    # where not judged (unseen, a sprite), the render stands in for the picture
    pic = np.where(judge[..., None], shown[:h, :w], ren[:h, :w])
    k = BLOCK
    bm = lambda a: a.reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))
    diff = np.abs(bm(ren[:h, :w].astype(float)) - bm(pic.astype(float))).sum(axis=2)
    diff = np.kron(diff, np.ones((k, k)))
    bad = np.zeros((ch, cw), bool)
    judged = np.zeros((ch, cw), bool)
    for cy in range(ch):
        for cx in range(cw):
            sl = (slice(cy * CELL, cy * CELL + CELL), slice(cx * CELL, cx * CELL + CELL))
            m = judge[sl]
            if m.mean() < SEEN_MIN:
                continue
            if ring is not None and ring[cy, cx]:
                continue    # stood up as a wall: the game draws it a band
            judged[cy, cx] = True
            bad[cy, cx] = diff[sl].mean() > DIFF
    return shown, ren, bad, judged


def sheet(rid, shown, ren, bad, path):
    h, w = ren.shape[:2]
    over = ren[:h, :w].copy()
    im = Image.fromarray(np.clip(over, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for cy, cx in zip(*np.nonzero(bad)):
        d.rectangle([cx * CELL, cy * CELL, cx * CELL + CELL - 1, cy * CELL + CELL - 1],
                    outline=(255, 40, 200))
    cap = Image.fromarray(np.clip(shown[:h, :w], 0, 255).astype(np.uint8))
    o = Image.new("RGB", (w * 2 + 8, h + 18), (22, 24, 30))
    o.paste(cap, (0, 18))
    o.paste(im, (w + 8, 18))
    dd = ImageDraw.Draw(o)
    dd.text((4, 3), f"{rid}  as the game shows it", fill=(220, 220, 220))
    dd.text((w + 12, 3), f"built, from the game's camera ({int(bad.sum())} cells differ)",
            fill=(220, 220, 220))
    o.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("geom")
    ap.add_argument("--area", default="")
    ap.add_argument("--captures", default=str(CAP.OUT))
    ap.add_argument("--out", default=str(Path(CAP.OUT) / "check"))
    a = ap.parse_args()
    areas = {int(x) for x in a.area.split(",") if x}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = out / "check.json"
    res = json.loads(rep.read_text()) if rep.is_file() else {}
    for obj in sorted(Path(a.geom).glob("area_*/room_*.obj")):
        rid = obj.stem[5:]
        if areas and int(rid.split("_")[0]) not in areas:
            continue
        f = Path(a.dumps) / f"room_{rid}.tmcr"
        r = RE.load_room(f)
        cls = TV.room_heights(r, 0, path=str(f))[0]
        got = check(r, rid, obj, a.captures, cls)
        if got is None:
            continue
        shown, ren, bad, judged = got
        share = float(bad.sum() / max(judged.sum(), 1))
        res[rid] = dict(differ=round(share, 3), cells=int(bad.sum()), judged=int(judged.sum()))
        sheet(rid, shown, ren, bad, out / f"room_{rid}_check.png")
        print(f"{rid}: {bad.sum()} of {judged.sum()} cells differ ({share:.0%})")
    rep.write_text(json.dumps(dict(sorted(res.items(), key=lambda kv: -kv[1]["differ"])),
                              indent=1))


if __name__ == "__main__":
    main()
