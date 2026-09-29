#!/usr/bin/env python3
"""tile_catalog.py -- every tile in the game, identified.

For each area's tileset and each metatile its rooms draw on layer 0, what
it is:

  type     Picori's tile type (or special tile) and its name
  surface  Picori's act surface under it (door, ladder, water, ...)
  label    what the tile stage makes of it: a tileid family (tree, pot,
           fence ...) or, failing one, its terrain role (floor, wall ...)
  view     how the camera shows it (docs/vr/06):
             top    something's top: floor, a roof, a board, a cliff top
             front  a south-facing front: drawn height, walkable below it
             back   the far rim of something raised: walkable above it
             side   an east or west flank: walkable beside it only
             wall   an indoor wall face (the box convention)
             void   nothing drawn
             object a family's tile or an object's special tile: a thing
                    standing on the ground, whatever is round it

A tile is its number and its drawing (a room may use another tileset
than the rest of its area), so it should be one thing everywhere. Where the
rooms that use it disagree -- different labels, or different views -- it
is flagged, for a person to settle with tile_verify / tile_labeler.

Usage:
  tile_catalog.py DUMPS [--out DIR] [--areas 34,45] [--sheet]
    DIR/catalogue.tsv   area tile key name surface label view cells rooms flags
    DIR/summary.txt     totals; views and labels; every flagged tile
    DIR/sheet_AA.png    with --sheet: each area's tiles, one sample each,
                        numbered, with its label and view

The .tsv and summary hold tile numbers, names and counts only. The
sheets are your ROM's art: keep them local (DIR defaults to
vr/tiles/catalogue/, which is ignored).
"""
import argparse
import hashlib
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import picori_labels as PL  # noqa: E402
import room_explore as RE  # noqa: E402
import tileid as TI  # noqa: E402
import tilevox as TV  # noqa: E402

OUT = HERE.parent / "vr" / "tiles" / "catalogue"
AGREE = 0.8             # a tile is one thing if this share of its cells agree
WALL_ROLES = ("wall", "wallflat")


def views(r, cls, role):
    """Per cell, how the camera shows it (see the module notes)."""
    h, w = cls.shape
    walk = cls == RE.CLASS_GROUND
    solid = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    loc = PL.location(r.area, r.room)
    inside = loc["kind"] != "outdoors" and not loc["open_air"]
    v = np.full((h, w), "top", dtype=object)
    v[cls == RE.CLASS_VOID] = "void"
    P = np.pad(walk, 1)
    s, n = P[2:, 1:-1], P[:-2, 1:-1]
    ew = P[1:-1, 2:] | P[1:-1, :-2]
    v[solid & s] = "front"
    v[solid & ~s & n] = "back"
    v[solid & ~s & ~n & ew] = "side"
    if inside:
        for y, x in zip(*np.nonzero(solid)):
            if role[y, x] in WALL_ROLES:
                v[y, x] = "wall"
    return v


def room_tiles(job):
    """[(area, tile, key, surface, label, view, room, x, y, drawing hash)]"""
    dumps, f = job
    try:
        r = RE.load_room(Path(f))
        rgb = RE.room_art_rgb(r, 0)
        if rgb is None:
            return [], f"{Path(f).name}: no art (a placeholder room)"
        art = np.ascontiguousarray(rgb[:, :, :3].astype(np.uint8))
        cls, H, bl, _fl, _d = TV.room_heights(r, 0, path=str(f))
        fam, _floor = TI.families(r, cls, H, art)
        role = TV.room_roles(r, cls, H, bl, art)
    except Exception as e:          # a room the tools cannot read: say so
        return [], f"{Path(f).name}: {type(e).__name__}: {e}"
    L = r.layers[0]
    h, w = r.cells_h, r.cells_w
    t = L["tile"][:h, :w].astype(int)
    tt = L["tiletype"][np.clip(t, 0, len(L["tiletype"]) - 1)].astype(int)
    act = L["act"][:h, :w].astype(int)
    vw = views(r, cls, role)
    bpp8 = bool(L.get("bpp8"))      # a picture, not tiles: each cell its own
    rid = f"{r.area:02d}_{r.room:02d}"
    out = []
    for y in range(h):
        for x in range(w):
            ti = int(t[y, x])
            key = f"special=0x{ti:x}" if ti >= 0x4000 else f"type=0x{int(tt[y, x]):x}"
            label = fam[y, x] or f"({role[y, x] or 'none'})"
            px = art[y * 16:y * 16 + 16, x * 16:x * 16 + 16]
            dh = hashlib.blake2b(px.tobytes(), digest_size=8).hexdigest() if px.shape[:2] == (16, 16) else ""
            tile = f"d{dh}" if bpp8 else ti
            view = "object" if (fam[y, x] or ti >= 0x4000) else vw[y, x]
            out.append((r.area, tile, key, int(act[y, x]), label, view, rid, x, y, dh))
    return out, None


def tid(t):
    """A tile's number, or d<hash> for a cell of an 8bpp picture."""
    return t if isinstance(t, str) else f"0x{t:x}"


def picori_name(key):
    kind, v = key.split("=")
    v = int(v, 16)
    if kind == "special":
        return PL.special_tiles().get(v, "")
    return PL.tile_types().get(v, "")


def catalogue(rows):
    """{(area, tile): entry} from every cell's row."""
    # a room of an area may use another tileset: a tile is its number AND
    # its drawing
    by = {}
    for row in rows:
        by.setdefault((row[0], row[1], row[9]), []).append(row)
    cat = {}
    for (area, ti, dh), rs in by.items():
        labels = Counter(r_[4] for r_ in rs)
        vws = Counter(r_[5] for r_ in rs)
        keys = Counter(r_[2] for r_ in rs)
        surf = Counter(r_[3] for r_ in rs)
        lab, nl = labels.most_common(1)[0]
        vw, nv = vws.most_common(1)[0]
        key = keys.most_common(1)[0][0]
        flags = []
        if nl < AGREE * len(rs):
            flags.append("labels:" + ",".join(f"{k}={n}" for k, n in labels.most_common(3)))
        if nv < AGREE * len(rs) and vw != "void":
            flags.append("views:" + ",".join(f"{k}={n}" for k, n in vws.most_common(3)))
        cat[(area, ti, dh)] = dict(
            area=area, tile=ti, key=key, name=picori_name(key),
            surface=PL.act_short(surf.most_common(1)[0][0]) if hasattr(PL, "act_short") else "",
            label=lab, view=vw, cells=len(rs),
            rooms=sorted({r_[6] for r_ in rs}), flags=flags,
            sample=rs[len(rs) // 2][6:9], drawings=len({r_[9] for r_ in rs}))
    return cat


def write(cat, rows, errors, out):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "catalogue.tsv", "w") as f:
        f.write("area\ttile\tkey\tname\tsurface\tlabel\tview\tcells\trooms\tflags\n")
        for (a, ti, _dh), e in sorted(cat.items(), key=lambda kv: (kv[0][0], str(kv[0][1]).zfill(8))):
            rooms = ",".join(e["rooms"][:8]) + ("..." if len(e["rooms"]) > 8 else "")
            f.write(f"{a:02d}\t{tid(ti)}\t{e['key']}\t{e['name']}\t{e['surface']}\t"
                    f"{e['label']}\t{e['view']}\t{e['cells']}\t{rooms}\t{';'.join(e['flags'])}\n")
    drawings = len({r_[9] for r_ in rows if r_[9]})
    vws = Counter()
    labs = Counter()
    for e in cat.values():
        vws[e["view"]] += 1
        labs[e["label"]] += 1
    flagged = [e for e in cat.values() if e["flags"]]
    with open(out / "summary.txt", "w") as f:
        named = sum(1 for e in cat.values() if not e["label"].startswith("("))
        pic = sum(1 for e in cat.values() if e["name"])
        f.write(f"{len(rows)} cells in {len({r_[6] for r_ in rows})} rooms; "
                f"{len(cat)} tiles (area x metatile x drawing; an 8bpp room's cells each one); "
                f"{drawings} distinct drawings\n"
                f"{named} tiles have a family (tileid); {pic} have a Picori name; "
                f"the rest are known by their terrain role only\n\n")
        f.write("tiles by view:\n")
        for k, n in vws.most_common():
            f.write(f"  {k:6s} {n}\n")
        f.write("\ntiles by label:\n")
        for k, n in labs.most_common():
            f.write(f"  {k:28s} {n}\n")
        f.write(f"\n{len(flagged)} tiles flagged (the rooms using them disagree, "
                f"or nothing names them):\n")
        for e in sorted(flagged, key=lambda e: -e["cells"]):
            f.write(f"  {e['area']:02d} {tid(e['tile'])} {e['key']} {e['name'] or '-'} "
                    f"[{e['label']} / {e['view']}] {e['cells']} cells, e.g. "
                    f"{e['sample'][0]} {e['sample'][1]},{e['sample'][2]}: {'; '.join(e['flags'])}\n")
        if errors:
            f.write(f"\n{len(errors)} rooms not read:\n")
            for m in errors:
                f.write(f"  {m}\n")
    return drawings, flagged


def sheet(dumps, cat, area, out, zoom=3):
    """One sample of each of the area's tiles, numbered, labelled."""
    from PIL import Image, ImageDraw
    es = sorted((e for e in cat.values() if e["area"] == area), key=lambda e: str(e["tile"]).zfill(8))
    if not es:
        return None
    cw, ch = 16 * zoom + 120, 16 * zoom + 8
    cols = 6
    img = Image.new("RGB", (cols * cw, ((len(es) + cols - 1) // cols) * ch), (24, 24, 28))
    d = ImageDraw.Draw(img)
    arts = {}
    for i, e in enumerate(es):
        rid, x, y = e["sample"]
        if rid not in arts:
            r = RE.load_room(Path(dumps) / f"room_{rid}.tmcr")
            arts[rid] = RE.room_art_rgb(r, 0)[:, :, :3].astype(np.uint8)
        px = arts[rid][y * 16:y * 16 + 16, x * 16:x * 16 + 16]
        gx, gy = (i % cols) * cw, (i // cols) * ch
        if px.shape[:2] == (16, 16):
            img.paste(Image.fromarray(px).resize((16 * zoom, 16 * zoom), Image.NEAREST), (gx + 4, gy + 4))
        col = (255, 150, 120) if e["flags"] else (220, 220, 220)
        d.text((gx + 16 * zoom + 8, gy + 4), f"{tid(e['tile'])} {e['view']}", fill=col)
        d.text((gx + 16 * zoom + 8, gy + 18), e["label"][:18], fill=col)
        d.text((gx + 16 * zoom + 8, gy + 32), (e["name"] or e["key"])[:18], fill=(150, 150, 160))
    p = out / f"sheet_{area:02d}.png"
    img.save(p)
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--areas", default="", help="comma list of area numbers (default all)")
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=0)
    a = ap.parse_args()
    files = sorted(Path(a.dumps).glob("room_*.tmcr"))
    if a.areas:
        want = {int(s) for s in a.areas.split(",")}
        files = [f for f in files if int(f.stem.split("_")[1]) in want]
    rows, errors = [], []
    with ProcessPoolExecutor(a.jobs or None) as ex:
        for rs, err in ex.map(room_tiles, [(a.dumps, str(f)) for f in files], chunksize=4):
            rows.extend(rs)
            if err:
                errors.append(err)
    cat = catalogue(rows)
    out = Path(a.out)
    drawings, flagged = write(cat, rows, errors, out)
    print(f"{len(files)} rooms, {len(cat)} tiles, {drawings} distinct drawings, "
          f"{len(flagged)} flagged, {len(errors)} rooms unread -> {out}")
    if a.sheet:
        for area in sorted({e["area"] for e in cat.values()}):
            sheet(a.dumps, cat, area, out)


if __name__ == "__main__":
    main()
