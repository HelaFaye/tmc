#!/usr/bin/env python3
"""tile_review.py -- a room with every cell labelled, for correcting tileid.

Each cell shows its coordinates, what the tile stage makes of it (the
tileid family, else the terrain role), its height and its tile type, with
a border coloured by family. Read the coordinates off, and write the fix
into vr/tiles/overrides.txt (the format is in tileid.py):

  03_01 38,24   family=stump
  34_17 7,2     height=0

Rerun and the grid shows the override applied.

Usage:
  python3 tools/tile_review.py states/p0 03_01 --cells 34,18,49,32 --out yard.png
  python3 tools/tile_review.py states/p0 34_16 --text

The PNG is the room's own art (derived from your ROM): keep it local.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402
import tileid as TI  # noqa: E402
import tilevox as TV  # noqa: E402

COLOUR = {
    "bush": (60, 220, 60), "sapling": (120, 200, 40), "rock": (170, 170, 170),
    "mushroom": (230, 60, 60), "stump": (170, 110, 50), "planter": (200, 150, 220),
    "prop": (240, 240, 80), "signpost": (255, 200, 120), "fence": (255, 140, 0),
    "foliage": (0, 160, 90), "flowers": (255, 120, 200),
}


def review(dumps, name):
    """(room, art, cls, H, label grid, family grid, tile type grid)."""
    f = Path(dumps) / f"room_{name}.tmcr"
    r = RE.load_room(f)
    art = np.ascontiguousarray(RE.room_art_rgb(r, 0)[:, :, :3].astype(np.uint8))
    cls, H, bl, _fl, _doors = TV.room_heights(r, 0, path=str(f))
    fam, _floor = TI.families(r, cls, H, art)
    role = TV.room_roles(r, cls, H, bl, art)
    L = r.layers[0]
    tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)][:r.cells_h, :r.cells_w]
    label = np.where(fam != None, fam, role)  # noqa: E711
    return r, art, cls, H, label, fam, tt


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("room", help="AA_RR")
    ap.add_argument("--cells", default="", help="x0,y0,x1,y1 inclusive (default: all)")
    ap.add_argument("--out", default="", help="PNG to write (default review_AA_RR.png)")
    ap.add_argument("--zoom", type=int, default=4)
    ap.add_argument("--text", action="store_true", help="print the grid instead")
    a = ap.parse_args()
    r, art, cls, H, label, fam, tt = review(a.dumps, a.room)
    x0, y0, x1, y1 = ([int(v) for v in a.cells.split(",")] if a.cells
                      else (0, 0, r.cells_w - 1, r.cells_h - 1))
    if a.text:
        for cy in range(y0, y1 + 1):
            print(" ".join(f"{cx:2d},{cy:<2d} {str(label[cy, cx])[:8]:<8} "
                           f"h{int(H[cy, cx]):<3d} {int(tt[cy, cx]):x}"
                           for cx in range(x0, x1 + 1)))
        return
    from PIL import Image, ImageDraw
    Z = a.zoom
    S = 16 * Z
    crop = art[y0 * 16:(y1 + 1) * 16, x0 * 16:(x1 + 1) * 16]
    im = Image.fromarray(crop).resize((crop.shape[1] * Z, crop.shape[0] * Z), Image.NEAREST)
    d = ImageDraw.Draw(im)
    ink = dict(stroke_width=2, stroke_fill=(0, 0, 0))
    for cy in range(y0, y1 + 1):
        for cx in range(x0, x1 + 1):
            X, Y = (cx - x0) * S, (cy - y0) * S
            f = fam[cy, cx]
            d.rectangle([X, Y, X + S - 1, Y + S - 1],
                        outline=COLOUR.get(f, (0, 0, 0)), width=3 if f else 1)
            d.text((X + 3, Y + 2), f"{cx},{cy}", fill=(255, 255, 0), **ink)
            d.text((X + 3, Y + 14), f"h{int(H[cy, cx])} {int(tt[cy, cx]):x}",
                   fill=(255, 255, 255), **ink)
            d.text((X + 3, Y + S - 14), str(label[cy, cx]),
                   fill=COLOUR.get(f, (200, 200, 200)), **ink)
    out = a.out or f"review_{a.room}.png"
    im.save(out)
    print(f"{out}: cells {x0},{y0}-{x1},{y1} of room {a.room} (keep local: ROM art)")


if __name__ == "__main__":
    main()
