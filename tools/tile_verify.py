#!/usr/bin/env python3
"""tile_verify.py -- have a person verify every tile, a batch at a time.

The rooms' cells are grouped by what they are drawn from (tile type, or
special tile for object tiles) and what the tile stage makes of them (the
tileid family, else the terrain role). Each group not yet verified is a
numbered card on a contact sheet: two cells of it shown in their
surroundings, its label, Picori's name for the tile, how many cells, which
rooms. A person answers "all good", or says which are wrong; the answer is
recorded:

  confirmed   vr/tiles/verified.txt, so it is not asked again
  corrected   vr/tiles/overrides.txt: a rule for the tile type where the
              group is all of that type in those rooms, else one per cell

Both files hold room numbers, coordinates and tile numbers only.

Usage:
  tile_verify.py batch DUMPS --rooms 00_00,03_01 --out sheet.png [--size 24]
      writes sheet.png and sheet.json (the batch)
  tile_verify.py answer sheet.json --ok all --set 5=rock --set 7:label=log_pile
      --ok all|1,2,3  confirm these (all = every card not set or skipped)
      --set N=FAMILY  (or N=- for none) correct card N's family
      --set N:height=PX / N:label=NAME   add a height or a label
      --skip N        leave card N for later
  tile_verify.py status DUMPS --rooms ...
      how much of those rooms is verified

The sheet is the rooms' own art (derived from your ROM): keep it local.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import picori_labels as PL  # noqa: E402
import room_explore as RE  # noqa: E402
import tileid as TI  # noqa: E402
import tilevox as TV  # noqa: E402

VERIFIED = HERE.parent / "vr" / "tiles" / "verified.txt"
ZOOM = 3
CONTEXT = 1             # cells of surroundings each side of a sample


def load_verified(path=VERIFIED):
    """{(room id, key, label)} already verified."""
    out = set()
    p = Path(path)
    if p.is_file():
        for line in p.read_text().splitlines():
            f = line.split("#", 1)[0].split()
            if len(f) >= 3:
                out.add((f[0], f[1], f[2]))
    return out


def room_cells(dumps, rid):
    """Per-cell (key, label, height) for a room, with its art."""
    f = Path(dumps) / f"room_{rid}.tmcr"
    r = RE.load_room(f)
    art = np.ascontiguousarray(RE.room_art_rgb(r, 0)[:, :, :3].astype(np.uint8))
    cls, H, bl, _fl, _d = TV.room_heights(r, 0, path=str(f))
    fam, _floor = TI.families(r, cls, H, art)
    role = TV.room_roles(r, cls, H, bl, art)
    L = r.layers[0]
    h, w = r.cells_h, r.cells_w
    t = L["tile"][:h, :w].astype(int)
    tt = L["tiletype"][np.clip(t, 0, len(L["tiletype"]) - 1)].astype(int)
    key = np.where(t >= 0x4000, np.vectorize(lambda v: f"special=0x{v:x}")(t),
                   np.vectorize(lambda v: f"type=0x{v:x}")(tt))
    label = np.empty((h, w), dtype=object)
    for y in range(h):
        for x in range(w):
            label[y, x] = fam[y, x] or f"({role[y, x] or 'none'})"
    return r, art, key, label, np.asarray(H)[:h, :w]


def picori_name(key):
    kind, v = key.split("=")
    v = int(v, 16)
    if kind == "special":
        return PL.special_tiles().get(v, "")
    return PL.tile_types().get(v, "")


def groups(dumps, rooms):
    """[{key, label, cells: [(room, x, y)], rooms}] over the rooms, and the
    rooms' data."""
    data = {}
    by = {}
    for rid in rooms:
        r, art, key, label, H = room_cells(dumps, rid)
        data[rid] = (r, art, key, label, H)
        for y in range(r.cells_h):
            for x in range(r.cells_w):
                by.setdefault((key[y, x], label[y, x]), []).append((rid, x, y))
    out = []
    for (k, lb), cells in by.items():
        out.append(dict(key=k, label=lb, cells=cells,
                        rooms=sorted({c[0] for c in cells})))
    # families first, then terrain; most cells first
    out.sort(key=lambda g: (g["label"].startswith("("), -len(g["cells"]), g["key"]))
    return out, data


def is_verified(g, verified):
    return all((rid, g["key"], g["label"]) in verified for rid in g["rooms"])


def sample_cells(g, n=2):
    """Up to n cells, from different rooms where it can, spread out."""
    cells = g["cells"]
    picks = []
    for rid in g["rooms"]:
        mine = [c for c in cells if c[0] == rid]
        picks.append(mine[len(mine) // 2])
        if len(picks) == n:
            return picks
    step = max(1, len(cells) // n)
    for c in cells[::step]:
        if c not in picks:
            picks.append(c)
        if len(picks) == n:
            break
    return picks


def crop(art, r, x, y):
    """The cell with CONTEXT cells round it, zoomed, the cell outlined."""
    from PIL import Image, ImageDraw
    c = CONTEXT
    size = (2 * c + 1) * 16
    out = np.full((size, size, 3), 24, np.uint8)
    x0, y0 = (x - c) * 16, (y - c) * 16
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(r.cells_w * 16, x0 + size), min(r.cells_h * 16, y0 + size)
    out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = art[sy0:sy1, sx0:sx1]
    im = Image.fromarray(out).resize((size * ZOOM, size * ZOOM), Image.NEAREST)
    d = ImageDraw.Draw(im)
    a, b = c * 16 * ZOOM, (c + 1) * 16 * ZOOM - 1
    d.rectangle([a - 1, a - 1, b + 1, b + 1], outline=(0, 0, 0), width=1)
    d.rectangle([a, a, b, b], outline=(255, 0, 255), width=2)
    return im


def cmd_batch(a):
    rooms = [x for x in a.rooms.split(",") if x]
    gs, data = groups(a.dumps, rooms)
    verified = load_verified()
    todo = [g for g in gs if not is_verified(g, verified)]
    total = sum(len(g["cells"]) for g in gs)
    done = total - sum(len(g["cells"]) for g in todo)
    batch = todo[:a.size]
    from PIL import Image, ImageDraw
    cw = (2 * CONTEXT + 1) * 16 * ZOOM
    card_w, card_h = cw * 2 + 30, cw + 58
    cols = 4
    rows = max(1, (len(batch) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * card_w, rows * card_h + 30), (22, 24, 30))
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), f"{len(batch)} of {len(todo)} groups left to verify; "
                   f"{done} of {total} cells verified ({100.0 * done / max(1, total):.0f}%)",
           fill=(255, 220, 120))
    items = []
    for i, g in enumerate(batch, 1):
        X, Y = ((i - 1) % cols) * card_w, 30 + ((i - 1) // cols) * card_h
        for j, (rid, x, y) in enumerate(sample_cells(g)):
            r, art = data[rid][0], data[rid][1]
            sheet.paste(crop(art, r, x, y), (X + 6 + j * (cw + 8), Y + 4))
        name = picori_name(g["key"])
        hs = sorted({int(data[rid][4][y, x]) for rid, x, y in g["cells"]})
        d.text((X + 6, Y + cw + 8), f"{i}.  {g['label']}", fill=(120, 230, 255))
        d.text((X + 6, Y + cw + 22), f"{g['key']}  {name[:34]}", fill=(230, 230, 230))
        d.text((X + 6, Y + cw + 36),
               f"{len(g['cells'])} cells, h {hs[0]}{'-%d' % hs[-1] if len(hs) > 1 else ''}"
               f"  in {', '.join(g['rooms'][:4])}{' +' if len(g['rooms']) > 4 else ''}",
               fill=(160, 160, 160))
        items.append(dict(n=i, key=g["key"], label=g["label"], cells=g["cells"],
                          rooms=g["rooms"], picori=name))
    sheet.save(a.out)
    # is each item's key all one label in those rooms? then rules go by type
    for it in items:
        other = [g for g in gs if g["key"] == it["key"] and g["label"] != it["label"]
                 and set(g["rooms"]) & set(it["rooms"])]
        it["whole_type"] = not other
    Path(a.out).with_suffix(".json").write_text(json.dumps(
        dict(rooms=rooms, dumps=str(a.dumps), items=items), indent=1))
    print(f"{a.out}: {len(batch)} cards; {len(todo)} groups left; "
          f"{done}/{total} cells verified")
    for it in items:
        print(f"  {it['n']:2d}. {it['label']:<12} {it['key']:<16} {it['picori'][:30]:<30} "
              f"{len(it['cells']):5d} cells  {','.join(it['rooms'][:3])}")


def _runs(cells):
    """Cells of one room as horizontal runs: [(x0, y, x1)]."""
    out = []
    for x, y in sorted(cells, key=lambda c: (c[1], c[0])):
        if out and out[-1][1] == y and out[-1][2] == x - 1:
            out[-1] = (out[-1][0], y, x)
        else:
            out.append((x, y, x))
    return out


def cmd_answer(a):
    b = json.loads(Path(a.batch).read_text())
    items = {it["n"]: it for it in b["items"]}
    sets = {}
    for s in a.set:
        if ":" in s:
            n, kv = s.split(":", 1)
        else:
            n, v = s.split("=", 1)
            kv = f"family={v}"
        k, _, v = kv.partition("=")
        sets.setdefault(int(n), {})[k] = v
    skip = {int(x) for x in ",".join(a.skip).split(",") if x}
    if a.ok == "all":
        ok = set(items) - set(sets) - skip
    else:
        ok = {int(x) for x in a.ok.split(",") if x}
    ver_lines, rule_lines = [], []
    for n in sorted(ok | set(sets)):
        it = items[n]
        if n in sets:
            what = " ".join(f"{k}={v}" for k, v in sets[n].items())
            rule_lines.append(f"# verify: card {n}, {it['label']} {it['key']}"
                              f"{' (' + it['picori'] + ')' if it['picori'] else ''}")
            if it["whole_type"]:
                for rid in it["rooms"]:
                    rule_lines.append(f"{rid:<6} {it['key']:<22} {what}")
            else:
                for rid in it["rooms"]:
                    mine = [(x, y) for r_, x, y in it["cells"] if r_ == rid]
                    for x0, y, x1 in _runs(mine):
                        where = f"{x0},{y}" if x0 == x1 else f"{x0},{y}-{x1},{y}"
                        rule_lines.append(f"{rid:<6} {where:<22} {what}")
            # the corrected group is verified as what it now is
            fam = sets[n].get("family")
            lb = (fam if fam and fam != "-" else None) or it["label"]
            if fam == "-":
                lb = "(corrected)"
        else:
            lb = it["label"]
        for rid in it["rooms"]:
            ver_lines.append(f"{rid} {it['key']} {lb}")
    if rule_lines:
        p = TI.OVERRIDES
        text = p.read_text() if p.is_file() else ""
        p.write_text(text.rstrip("\n") + "\n\n" + "\n".join(rule_lines) + "\n")
    if ver_lines:
        head = "" if VERIFIED.is_file() else (
            "# Tile groups a person has looked at and agreed with (tools/tile_verify.py).\n"
            "# room  tile  what the tile stage makes of it -- ids only, nothing from the ROM\n")
        text = VERIFIED.read_text() if VERIFIED.is_file() else head
        VERIFIED.write_text(text + "\n".join(ver_lines) + "\n")
    print(f"confirmed {len(ok)}, corrected {len(sets)}, skipped {len(skip)}; "
          f"{len(rule_lines)} override lines, {len(ver_lines)} verified lines")


def cmd_status(a):
    rooms = [x for x in a.rooms.split(",") if x]
    gs, _ = groups(a.dumps, rooms)
    verified = load_verified()
    total = sum(len(g["cells"]) for g in gs)
    done = sum(len(g["cells"]) for g in gs if is_verified(g, verified))
    print(f"{done}/{total} cells verified ({100.0 * done / max(1, total):.1f}%), "
          f"{sum(1 for g in gs if not is_verified(g, verified))} groups left")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("batch")
    p.add_argument("dumps")
    p.add_argument("--rooms", required=True)
    p.add_argument("--out", default="verify.png")
    p.add_argument("--size", type=int, default=24)
    p = sub.add_parser("answer")
    p.add_argument("batch")
    p.add_argument("--ok", default="")
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--skip", action="append", default=[])
    p = sub.add_parser("status")
    p.add_argument("dumps")
    p.add_argument("--rooms", required=True)
    a = ap.parse_args()
    {"batch": cmd_batch, "answer": cmd_answer, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
