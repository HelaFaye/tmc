"""The tile catalogue: every distinct tile the rooms are built from.

A room is a grid of 16 px cells. Each cell holds one metatile per layer:
the ground layer (0) and the overhead layer (1, drawn over actors). A
metatile index means something only within its tileset, so a tile's key
is

    <tileset>:<layer>:<metatile>

where <tileset> is a short fingerprint of the area's metatile tables (the
subtile table and the tile-type table, as the room dump records them).
Areas that share a tileset share tiles. The key holds indices only, no
art: it may be committed, and so may anything keyed by it (doc 10).

  python3 -m vox.tiles DUMPS [--rooms AA_RR,...] [--out catalogue.json]

For each tile: how many cells use it, in which rooms and where, its tile
type, and the collision classes under it.
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .room import RE

EMPTY = 0               # metatile 0 on the overhead layer draws nothing


def tileset_key(layer):
    """A short fingerprint of a layer's metatile tables."""
    h = hashlib.sha1()
    h.update(np.ascontiguousarray(layer["subtile"]).tobytes())
    h.update(np.ascontiguousarray(layer["tiletype"]).tobytes())
    return h.hexdigest()[:8]


def room_tiles(r, cls=None):
    """{(layer, cy, cx): key} for a loaded room (room_explore), and the
    tile type of each. Empty overhead cells are left out."""
    if cls is None:
        cls = RE.classify_room(r, 0)
    out = {}
    for li in (0, 1):
        L = r.layers[li]
        if not L["present"]:
            continue
        ts = tileset_key(L)
        tile = L["tile"][:r.cells_h, :r.cells_w]
        for cy in range(r.cells_h):
            for cx in range(r.cells_w):
                t = int(tile[cy, cx])
                if li == 1 and t == EMPTY:
                    continue
                tt = int(L["tiletype"][min(t, len(L["tiletype"]) - 1)])
                out[(li, cy, cx)] = (f"{ts}:{li}:{t}", tt, cls[cy, cx])
    return out


def catalogue(dumps, rooms=None):
    """{key: {count, layer, tiletype, classes: {class: n}, where: {rid: [[cx, cy], ...]}}}."""
    cat = defaultdict(lambda: {"count": 0, "classes": defaultdict(int), "where": defaultdict(list)})
    for p in sorted(Path(dumps).glob("room_*.tmcr")):
        rid = p.stem[5:]
        if rooms and rid not in rooms:
            continue
        r = RE.load_room(p)
        if not r.cells_w:
            continue
        for (li, cy, cx), (key, tt, c) in room_tiles(r).items():
            e = cat[key]
            e["count"] += 1
            e["layer"] = li
            e["tiletype"] = tt
            e["classes"][c] += 1
            e["where"][rid].append([cx, cy])
    return {k: {"count": v["count"], "layer": v["layer"], "tiletype": v["tiletype"],
                "classes": dict(v["classes"]), "where": dict(v["where"])}
            for k, v in sorted(cat.items(), key=lambda kv: -kv[1]["count"])}


# ------------------------------------------------------------- kinds --
# A tile's kind says how it is built. The first guess comes from the
# tile's own art, the collision under it, and what lies round it wherever
# it is used; review corrects it, and a correction applies everywhere.
KINDS = ("floor", "water", "face", "top", "foliage", "trunk", "prop", "deck", "void")
LEAF_HUE = (60, 170)    # degrees: leaf green
LEAF_SHARE = 0.4        # of a tile's opaque pixels leaf green: foliage
LEAF_STD = 18           # luminance: foliage is clumpy; an even green is a lawn or floor
BARK_HUE = (10, 45)     # degrees: bark brown
BARK_SHARE = 0.4        # of its pixels bark brown, with vertical grain: a trunk
GRAIN_RATIO = 1.5       # column-to-row variance: grain runs up the tile
FLAT_STD = 4.0          # luminance: a plain fill
BAND_RATIO = 2.0        # row-to-column variance: banded like a wall face
OPAQUE_MIN = 0.15       # of an overhead tile's pixels drawn: else nothing to build


def _art(r):
    import extract_art
    a0 = RE.room_art_rgb(r, 0)[:, :, :3].astype(np.uint8)
    a1 = None
    if len(r.layers) > 1 and r.layers[1]["present"]:
        o = extract_art.room_art(r, 1)
        if o is not None:
            a1 = np.asarray(o).astype(np.uint8)
    return a0, a1


def features(dumps, cat):
    """Per tile: its art's measures (from its first use) and its
    surroundings (over all its uses). Adds e['f'] to each entry."""
    from . import regions as RG
    by_room = defaultdict(list)
    for k, e in cat.items():
        for rid in e["where"]:
            by_room[rid].append(k)
    stats = {k: {"below_floor": 0, "above_wall": 0, "n": 0} for k in cat}
    done = set()
    for rid, keys in by_room.items():
        r = RE.load_room(Path(dumps) / f"room_{rid}.tmcr")
        cls = RE.classify_room(r, 0)
        a0, a1 = _art(r)
        h, w = cls.shape
        for k in keys:
            e = cat[k]
            for cx, cy in e["where"][rid]:
                s = stats[k]
                s["n"] += 1
                if cy + 1 < h and cls[cy + 1, cx] == RE.CLASS_GROUND:
                    s["below_floor"] += 1
                if cy - 1 >= 0 and cls[cy - 1, cx] in (RE.CLASS_WALL, RE.CLASS_LEDGE):
                    s["above_wall"] += 1
            if k in done:
                continue
            cx, cy = e["where"][rid][0]
            src = a0 if e["layer"] == 0 else a1
            if src is None:
                continue
            t = src[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            alpha = t[:, :, 3] > 0 if t.shape[2] == 4 else np.ones(t.shape[:2], bool)
            px = t[:, :, :3][alpha]
            if not len(px):
                e["f"] = {"opaque": 0.0}
                done.add(k)
                continue
            lum = RG.luminance(px)
            hue, sat = RG.hsv(px[None])
            hue, sat = hue[0], sat[0]
            leaf = float(((hue >= LEAF_HUE[0]) & (hue < LEAF_HUE[1]) & (sat > 0.25)).mean())
            bark = float(((hue >= BARK_HUE[0]) & (hue < BARK_HUE[1]) & (sat > 0.3)
                          & (lum < 150)).mean())
            L = RG.luminance(t[:, :, :3]).astype(float)
            rowv = float(np.var(L.mean(axis=1)))
            colv = float(np.var(L.mean(axis=0)))
            e["f"] = {"opaque": float(alpha.mean()), "lum": float(lum.mean()),
                      "std": float(lum.std()), "leaf": leaf, "bark": bark, "sat": float(sat.mean()),
                      "band": rowv / max(colv, 1.0)}
            done.add(k)
    for k, s in stats.items():
        f = cat[k].setdefault("f", {"opaque": 0.0})
        n = max(s["n"], 1)
        f["below_floor"] = s["below_floor"] / n
        f["above_wall"] = s["above_wall"] / n
    return cat


def guess_kind(e):
    """The first guess at a tile's kind, from its features. Whether a
    group of foliage tiles is a tree, a bush or a hedge is its shape's to
    say (templates), not one tile's."""
    f = e.get("f", {})
    cls = max(e["classes"], key=e["classes"].get)
    leaf = f.get("leaf", 0) >= LEAF_SHARE
    even = f.get("std", 99) < LEAF_STD
    if e["layer"] == 1:
        if f.get("opaque", 0) < OPAQUE_MIN:
            return "void"
        if f.get("std", 99) < FLAT_STD and f.get("lum", 0) < 40:
            return "void"           # the black fill round a room
        if cls == RE.CLASS_GROUND:
            # over walkable cells: an even fill is an overhead floor (a
            # courtyard's lawn, a hall's upper floor, a bridge); clumpy
            # leaves overhang it (a crown's edge); anything else busy is
            # a thing drawn over the floor (a pot, a brazier, flowers)
            if even:
                return "deck"
            return "foliage" if leaf else "prop"
        if leaf:
            return "foliage"        # a crown's middle is smooth, still foliage
        return "top"                # the top of a wall drawn over actors
    if cls == RE.CLASS_WATER:
        return "water"
    if cls in (RE.CLASS_GROUND, RE.CLASS_HOLE, RE.CLASS_VOID):
        return "floor"
    # blocked: leaves here are a crown or a hedge, however smooth
    if leaf:
        return "foliage"
    if f.get("bark", 0) >= BARK_SHARE and 1.0 / max(f.get("band", 1.0), 1e-6) >= GRAIN_RATIO:
        return "trunk"
    if f.get("band", 0) >= BAND_RATIO or f.get("below_floor", 0) >= 0.5:
        return "face"
    if e["count"] <= 4:
        return "prop"
    return "top"


ATLAS_COLS = 64        # tiles per atlas row
CTX = 3                 # cells: a tile is shown in a CTX x CTX context


def export(cat, dumps, out):
    """The review page's tile data: OUT/tiles.js (each tile's kind,
    counts and rooms), OUT/tiles.png (every tile, 16 px, ATLAS_COLS a
    row) and OUT/tiles_ctx.png (each in its context, 48 px, the tile
    outlined). The atlases are the player's ROM art: OUT stays local."""
    from PIL import Image, ImageDraw
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    keys = list(cat)
    rows = (len(keys) + ATLAS_COLS - 1) // ATLAS_COLS
    atlas = Image.new("RGBA", (ATLAS_COLS * 16, rows * 16), (0, 0, 0, 0))
    cs = CTX * 16
    ctx = Image.new("RGB", (ATLAS_COLS * cs, rows * cs), (30, 32, 36))
    by_room = defaultdict(list)
    for i, k in enumerate(keys):
        rid = next(iter(cat[k]["where"]))
        by_room[rid].append(i)
    for rid, idx in by_room.items():
        r = RE.load_room(Path(dumps) / f"room_{rid}.tmcr")
        a0, a1 = _art(r)
        h, w = a0.shape[:2]
        for i in idx:
            e = cat[keys[i]]
            cx, cy = e["where"][rid][0]
            src = a0 if e["layer"] == 0 else a1
            t = src[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            im = Image.fromarray(np.ascontiguousarray(t)).convert("RGBA")
            atlas.paste(im, ((i % ATLAS_COLS) * 16, (i // ATLAS_COLS) * 16))
            # the context: the ground layer with the overhead drawn over it
            y0 = min(max(0, cy * 16 - 16), max(0, h - cs))
            x0 = min(max(0, cx * 16 - 16), max(0, w - cs))
            c = a0[y0:y0 + cs, x0:x0 + cs].copy()
            if a1 is not None:
                o = a1[y0:y0 + cs, x0:x0 + cs]
                m = o[:, :, 3] > 0
                c[m] = o[:, :, :3][m]
            ci = Image.fromarray(np.ascontiguousarray(c))
            d = ImageDraw.Draw(ci)
            d.rectangle((cx * 16 - x0, cy * 16 - y0, cx * 16 - x0 + 15, cy * 16 - y0 + 15),
                        outline=(255, 0, 255))
            ctx.paste(ci, ((i % ATLAS_COLS) * cs, (i // ATLAS_COLS) * cs))
    atlas.save(out / "tiles.png")
    ctx.save(out / "tiles_ctx.png")
    recs = []
    for i, k in enumerate(keys):
        e = cat[k]
        recs.append({"key": k, "i": i, "layer": e["layer"], "kind": e.get("kind"),
                     "count": e["count"],
                     "rooms": {rid: len(v) for rid, v in e["where"].items()},
                     "cls": max(e["classes"], key=e["classes"].get)})
    (out / "tiles.js").write_text("window.TILES=" + json.dumps(
        {"cols": ATLAS_COLS, "ctx": cs, "tiles": recs}) + ";\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--rooms", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--export", default="", help="write the review page's tile data here")
    a = ap.parse_args()
    cat = catalogue(a.dumps, set(v for v in a.rooms.split(",") if v) or None)
    features(a.dumps, cat)
    for e in cat.values():
        e["kind"] = guess_kind(e)
    cells = sum(e["count"] for e in cat.values())
    counts = sorted((e["count"] for e in cat.values()), reverse=True)
    print(f"{len(cat)} tiles over {cells} cell layers")
    for n in (100, 500, 1000):
        print(f"  top {n}: {100 * sum(counts[:n]) / max(cells, 1):.0f}% of cell layers")
    for li in (0, 1):
        k = [e for e in cat.values() if e["layer"] == li]
        print(f"  layer {li}: {len(k)} tiles, {sum(e['count'] for e in k)} cells")
    kc = defaultdict(lambda: [0, 0])
    for e in cat.values():
        kc[e["kind"]][0] += 1
        kc[e["kind"]][1] += e["count"]
    print("  kinds (tiles, cells): " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(kc.items(), key=lambda kv: -kv[1][1])))
    if a.out:
        Path(a.out).write_text(json.dumps(cat))
    if a.export:
        export(cat, a.dumps, a.export)


if __name__ == "__main__":
    main()
