#!/usr/bin/env python3
"""find_stairs.py — every staircase and stairway doorway in the game.

Stairs have no single label. Four signals find them, all from the game's own
tables rather than from colour:

  steps         Flights of steps: cells whose action byte is a slope,
                SURFACE_SLOPE_GNDGND_V (0x26, rising north/south) or _H
                (0x27, rising east/west) -- walkable ground that carries Link
                between two floor heights. 251 cells in 57 rooms.
  stair-arch    A doorway in a wall with steps rising into it, like the
                bridge entrance of area 141. Tile types 0x91-0x93, 0x9a,
                0x9b (tiles.h names 0x92 STAIRS_UP and 0x93 STAIRS_DOWN).
  stairwell     Steps going down through a railed opening in the floor.
                Tile types 0x8f, 0x90.
  steps-to-door A short run of steps leading up to a doorway. Tile types
                0x4d6, 0x503, 0x520, 0x99.

The tile-type groups other than 0x92/0x93 were sorted by looking at every
room-transition doorway (SURFACE_DOOR_13, 0x28) in the game; house doors,
tree doors, Minish holes and burrows were left out. Tile types are per
tileset in principle, so a new group may turn up in a room not yet
harvested: check the --sheet output.

Neighbouring cells of one kind are merged into one run. Each output line is
a manifest line: coordinates only, no art.

Usage
-----
  python3 tools/find_stairs.py vrdump                       # print the list
  python3 tools/find_stairs.py vrdump --out objects/stairs.txt
  python3 tools/find_stairs.py vrdump --sheet stairs.png    # labelled crops
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_explore as RE  # noqa: E402

SLOPE_V, SLOPE_H = 0x26, 0x27
BY_TILETYPE = {
    "stair-arch": (0x91, 0x92, 0x93, 0x9a, 0x9b),
    "stairwell": (0x8f, 0x90),
    "steps-to-door": (0x4d6, 0x503, 0x520, 0x99),
}


def runs(mask):
    """4-connected components of a boolean cell mask -> list of cell lists."""
    seen = np.zeros_like(mask, bool)
    out = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, cells = [(y0, x0)], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            cells.append((int(y), int(x)))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if (0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]
                        and mask[ny, nx] and not seen[ny, nx]):
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append(cells)
    return out


def find(room_path):
    r = RE.load_room(room_path)
    found = []
    if not r.cells_w:
        return found
    for li in (0, 1):
        L = r.layers[li]
        if not L["present"]:
            continue
        H, W = r.cells_h, r.cells_w
        act = L["act"][:H, :W]
        tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)][:H, :W]
        kinds = [("steps", act == SLOPE_V, "ns"), ("steps", act == SLOPE_H, "ew")]
        kinds += [(k, np.isin(tt, ts), "") for k, ts in BY_TILETYPE.items()]
        for kind, mask, rise in kinds:
            for cells in runs(mask):
                ys = [c[0] for c in cells]
                xs = [c[1] for c in cells]
                found.append(dict(room=room_path.name, layer=li, kind=kind,
                                  rise=rise, n=len(cells),
                                  rect=(min(xs), min(ys), max(xs), max(ys))))
    return found


def sheet(items, dumps, out, per_row=5):
    from PIL import Image, ImageDraw
    import extract_art as A
    cache, tiles = {}, []
    for it in items:
        name = it["room"]
        if name not in cache:
            r = RE.load_room(Path(dumps) / name)
            img = A.room_art(r, 0).copy()
            top = A.room_art(r, 1)
            if top is not None:
                m = top[:, :, 3] > 0
                img[m] = top[m]
            cache[name] = img
        img = cache[name]
        x0c, y0c, x1c, y1c = it["rect"]
        cx, cy = (x0c + x1c) // 2, (y0c + y1c) // 2
        ox, oy = max(0, (cx - 3) * 16), max(0, (cy - 3) * 16)
        crop = Image.fromarray(img[oy:oy + 112, ox:ox + 112]).convert("RGB")
        d = ImageDraw.Draw(crop)
        d.rectangle([x0c * 16 - ox, y0c * 16 - oy,
                     (x1c + 1) * 16 - ox - 1, (y1c + 1) * 16 - oy - 1],
                    outline=(255, 0, 255))
        crop = crop.resize((224, 224), Image.NEAREST)
        d = ImageDraw.Draw(crop)
        d.rectangle([0, 0, 224, 11], fill=(0, 0, 0))
        d.text((2, 0), f"{it['kind']} {name[5:-5]} {x0c},{y0c}", fill=(255, 255, 0))
        tiles.append(crop)
    rows = (len(tiles) + per_row - 1) // per_row
    W = Image.new("RGB", (224 * per_row, 224 * rows), (20, 20, 20))
    for i, t in enumerate(tiles):
        W.paste(t, ((i % per_row) * 224, (i // per_row) * 224))
    W.save(out)
    print(f"sheet: {len(tiles)} crops -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--out", default="", help="write the list here (a manifest)")
    ap.add_argument("--kind", default="", help="only this kind")
    ap.add_argument("--sheet", default="", help="also draw labelled crops to this PNG")
    ap.add_argument("--sheet-max", type=int, default=60)
    a = ap.parse_args()

    items = []
    for f in sorted(Path(a.dumps).glob("room_*.tmcr")):
        try:
            items += find(f)
        except Exception as e:
            print(f"  {f.name}: {e}", file=sys.stderr)
    if a.kind:
        items = [i for i in items if i["kind"] == a.kind]

    lines = ["# stairs, from tools/find_stairs.py. Coordinates only.",
             "# room              cells (cx0,cy0,cx1,cy1)  kind  options"]
    for it in items:
        x0, y0, x1, y1 = it["rect"]
        opt = f"layer={it['layer']}" + (f" rise={it['rise']}" if it["rise"] else "")
        rect = f"{x0},{y0},{x1},{y1}"
        lines.append(f"{it['room']:<18} {rect:<14} {it['kind']:<13} "
                     f"{opt}   # {it['n']} cells")
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)
    counts = {}
    for it in items:
        k = counts.setdefault(it["kind"], [0, 0, set()])
        k[0] += 1
        k[1] += it["n"]
        k[2].add(it["room"])
    for k, (runs_, cells, rooms) in sorted(counts.items()):
        print(f"  {k:<13} {runs_:4d} runs, {cells:4d} cells, {len(rooms):3d} rooms",
              file=sys.stderr)
    if a.sheet:
        # A spread across rooms rather than the first N of one big room.
        seen, pick = set(), []
        for it in items:
            key = (it["room"], it["kind"])
            if key not in seen:
                seen.add(key)
                pick.append(it)
        sheet(pick[:a.sheet_max], a.dumps, a.sheet)


if __name__ == "__main__":
    main()
