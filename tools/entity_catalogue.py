#!/usr/bin/env python3
"""
entity_catalogue.py — turn a world-wide entity harvest into one index.

`harvest_rooms.py --entsheet` visits every room and writes a PNG per live
entity, which is thousands of files and mostly repeats: the same bush appears
in forty rooms, the same pot in a hundred. What is wanted is the DISTINCT set,
each one named by what it is and where it can be found.

Why this exists at all: an entity's sprite never appears in the room art, so
no amount of searching the .tmcr tilemaps will locate an object that is drawn
as an entity. Four objects in a row were misidentified by reasoning from
entity ids and their neighbours -- a stone tablet, a waterfall, a pillar --
before the cheap answer became obvious: dump them all and look.

Identity is the exact pixels. Two drawings that differ by one pixel are two
entries, because at this stage a guess about which differences are meaningful
is exactly the kind of guess that has gone wrong before.

Usage
-----
  python3 tools/entity_catalogue.py entsheets --out objects/entities
  python3 tools/entity_catalogue.py entsheets --out objects/entities --min 3
"""

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decomp_labels import Labels, find_repo  # noqa: E402


def load_trimmed(path):
    """The drawing, cropped to its opaque pixels. Canvas position is an
    artefact of the 128px dump canvas, not a property of the object."""
    im = Image.open(path).convert("RGBA")
    bb = im.getbbox()
    return im.crop(bb) if bb else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="root written by harvest_rooms.py --entsheet")
    ap.add_argument("--out", default="objects/entities")
    ap.add_argument("--min", type=int, default=1,
                    help="only catalogue drawings seen at least this often")
    ap.add_argument("--cols", type=int, default=12)
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--repo", default=None,
                    help="decomp root, for the object/npc/enemy id names")
    a = ap.parse_args()

    repo = Path(a.repo) if a.repo else find_repo()
    L = Labels(repo) if repo else None
    if L and L.ok:
        print(f"  labels from {repo}/include: "
              + ", ".join(f"{len(t)} {k}" for k, t in sorted(L.tables.items())))
    else:
        print("  no decomp headers found -- entries will be numbered, not named")
        L = None

    root = Path(a.dir)
    files = sorted(root.rglob("ent*.png"))
    if not files:
        sys.exit(f"no ent*.png under {root} -- run harvest_rooms.py --entsheet first")

    groups = defaultdict(lambda: {"n": 0, "where": [], "ids": set(),
                                  "sprites": set(), "im": None,
                                  "kinds": set(), "names": set()})
    skipped = 0
    for i, f in enumerate(files):
        im = load_trimmed(f)
        if im is None:
            skipped += 1
            continue
        key = hashlib.sha1(np.array(im).tobytes()).hexdigest()
        g = groups[key]
        g["n"] += 1
        if g["im"] is None:
            g["im"] = im
        # a<area>_r<room>/entNN_idXX_sprNNN.png
        room = f.parent.name
        name = f.stem.split("_")
        if len(g["where"]) < 40:
            g["where"].append(room)
        kind = None
        for part in name:
            if part.startswith("id"):
                g["ids"].add(part[2:])
            elif part.startswith("spr"):
                g["sprites"].add(part[3:])
            elif part.startswith("k") and part[1:].isdigit():
                kind = int(part[1:])
                g["kinds"].add(kind)
        if L and kind is not None:
            for eid in list(g["ids"]):
                try:
                    g["names"].add(L.name(kind, int(eid, 16)))
                except ValueError:
                    pass
        if i % 500 == 0:
            print(f"  [{i}/{len(files)}] {len(groups)} distinct", file=sys.stderr,
                  flush=True)

    items = [g for g in groups.values() if g["n"] >= a.min]
    items.sort(key=lambda g: -g["n"])
    if not items:
        sys.exit(f"nothing seen {a.min}+ times")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    S, cols = a.scale, a.cols
    W = max(g["im"].width for g in items)
    H = max(g["im"].height for g in items)
    cw, ch = W * S + 10, H * S + 26
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, rows * ch), (22, 22, 28))
    d = ImageDraw.Draw(sheet)
    for k, g in enumerate(items):
        im = g["im"].resize((g["im"].width * S, g["im"].height * S),
                            Image.NEAREST)
        x = (k % cols) * cw + 5
        y = (k // cols) * ch + 20
        sheet.paste(im, (x, y), im)
        tag = ('/'.join(sorted(g["names"])) or f"id {','.join(sorted(g['ids']))}")
        d.text((x, y - 15), f"{k} {tag[:22]} x{g['n']}", fill=(235, 235, 240))
    sheet.save(out.with_suffix(".png"))

    with out.with_suffix(".txt").open("w") as fh:
        fh.write("# distinct entity drawings, most common first\n")
        fh.write("# The name is the decomp's own, from object.h / npc.h /\n")
        fh.write("# enemy.h keyed by the entity's kind. An id without its\n")
        fh.write("# kind is meaningless: 0x2b is CASTOR_WILDS_STATUE as an\n")
        fh.write("# NPC and LILYPAD_LARGE_FALLING as an OBJECT.\n")
        fh.write("# index count  w x h  name  ids  sprites  rooms\n")
        for k, g in enumerate(items):
            im = g["im"]
            fh.write(f"{k:4d} {g['n']:5d}  {im.width:3d}x{im.height:<3d}  "
                     f"{'/'.join(sorted(g['names'])) or '?':<28}  "
                     f"id={','.join(sorted(g['ids']))}  "
                     f"spr={','.join(sorted(g['sprites']))}  "
                     f"{' '.join(sorted(set(g['where']))[:6])}\n")

    print(f"  {len(files)} frames from {len(set(f.parent for f in files))} rooms")
    print(f"  {len(groups)} distinct drawings, {len(items)} with count >= {a.min}"
          + (f", {skipped} empty" if skipped else ""))
    print(f"  -> {out.with_suffix('.png')}  and  {out.with_suffix('.txt')}")
    print("  Identity is exact pixels, so a palette change counts as a new entry.")


if __name__ == "__main__":
    main()
