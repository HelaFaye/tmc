"""The reference scenes every change to the stage is checked against,
and the sheets that show them. Crops are in cells (x0, y0, x1, y1).

  python3 -m vox.scenes DUMPS OUT [--what regions]

Writes OUT/<scene>_<what>.png -- the game's picture beside what the stage
made of it. Renders hold ROM-derived art: OUT must be outside the repo
(or in an ignored directory).
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import math

from . import heights as HT
from . import mesh as MS
from . import regions as RG
from . import room as RM
from .room import HERE  # noqa: F401  (tools/ on the path)
import refcheck as RC  # noqa: E402

SCENES = {
    "gate":        ("02_00", (26, 14, 37, 24)),   # arch on two blocks, bell, steps
    "blue_house":  ("02_00", (0, 29, 7, 38)),     # flat roof, chimney, plank, corner
    "west_ledge":  ("02_00", (4, 27, 11, 35)),    # stairs cut into a ledge
    "mushrooms":   ("02_00", (17, 30, 25, 46)),   # stacked mushroom houses
    "links_house": ("03_01", (36, 16, 48, 27)),   # thatch, square chimney, woodpile
    "garden":      ("07_00", (18, 9, 26, 15)),    # clipped hedges
}
ZOOM = 3


def crop(a, box):
    x0, y0, x1, y1 = box
    return a[y0 * 16:y1 * 16, x0 * 16:x1 * 16]


def sheet(panels, labels, path):
    ims = [Image.fromarray(p).resize((p.shape[1] * ZOOM, p.shape[0] * ZOOM), Image.NEAREST)
           for p in panels]
    W = sum(i.width + 8 for i in ims)
    H = max(i.height for i in ims) + 18
    o = Image.new("RGB", (W, H), (22, 24, 30))
    d = ImageDraw.Draw(o)
    x = 0
    for im, lab in zip(ims, labels):
        o.paste(im, (x, 18))
        d.text((x + 4, 3), lab, fill=(225, 225, 225))
        x += im.width + 8
    o.save(path)


TALL = 64


def render(V, F, UV, pic, yaw, pitch, game):
    """The surface from a camera, textured with the picture."""
    H, W = pic.shape[:2]
    T = np.array([(u / W, 1.0 - v / H) for u, v in UV for _ in range(4)])
    FT = [[4 * i, 4 * i + 1, 4 * i + 2, 4 * i + 3] for i in range(len(F))]
    C = np.full((len(V), 3), 0.6)
    box = (0, W, 0, H)
    if game:
        img = RC.render(V, C, F, RC.camera(0, 45), box, scale=(1.0, math.sqrt(2)),
                        tall=TALL, tex=(pic, T, FT), lit=False)
        return np.asarray(img)[TALL:TALL + H, :W]
    return np.asarray(RC.render(V, C, F, RC.camera(yaw, pitch), box, tall=TALL,
                                tex=(pic, T, FT)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--what", default="regions")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rooms = {}
    for name, (rid, box) in SCENES.items():
        if a.only and name not in a.only.split(","):
            continue
        if rid not in rooms:
            rooms[rid] = RM.load(Path(a.dumps) / f"room_{rid}.tmcr")
        rm = rooms[rid]
        pic = crop(rm.picture, box)
        if a.what == "regions":
            lab, _n, sh = RG.split(pic)
            sheet([pic, RG.overlay(pic, lab, sh)], ["the game", "regions and shading"],
                  out / f"{name}_regions.png")
        elif a.what == "build":
            lab, _n, sh = RG.split(pic)
            yb, sl, gnd = HT.solve(rm, lab, sh, box)
            V, F, UV = MS.build(yb, sl, gnd)
            panels, labels = [pic], ["the game"]
            for yaw, pitch, game in ((0, 45, True), (35, 30, False), (-35, 30, False)):
                panels.append(render(V, F, UV, pic, yaw, pitch, game))
                labels.append("built, game camera" if game else f"yaw {yaw} pitch {pitch}")
            sheet(panels, labels, out / f"{name}_build.png")
        print(name)


if __name__ == "__main__":
    main()
