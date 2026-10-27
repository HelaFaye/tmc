"""The review page's data: whole rooms, area by area, in story order.

  python3 -u -m vox.review DUMPS OUT [--areas 3,21,2,7,128] [--rooms 03_01,...]
                                     [--no-vox] [--force] [--tiles CATALOGUE]

For each room it writes, under OUT:
  img/<rid>.png         the room as the game shows it
  data/<rid>.json       its labels (classify.py)
  vox/<rid>_game.png    the room built, seen from the game's camera
  vox/<rid>_turned.png  the build from yaw 35 and -35 (pitch 30), side by side
and then OUT/rooms.js, the index of every room done so far.

The build is assemble.py's (doc 12): tile kinds (the catalogue's first
guesses, --tiles, under the reviewed vr/tiles/kinds.json) and one shape
per thing. Each room's record carries the build's date and check()'s
numbers, so the page says what was built and how it measured.

A room already written is skipped (--force redoes it). The pictures are
the player's own ROM art: OUT must be outside the repository (or
ignored), and the page stays private to the people it is shared with.

Ids on the page: an object is <rid>-<x0>-<y0>-<x1>-<y1>, a tile note
<rid>-t-<cx>-<cy>, a voxel tile vox-<rid>-t-<cx>-<cy>, a room's build
vox-<rid>-room. A review stays with an object while its box is unchanged.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import assemble as AS
from . import classify as CL
from . import glrender as GL
from . import overhead as OV
from . import room as RM
from . import scenes as SC

STORY = [3, 21, 2, 7, 128, 0, 1, 72, 5, 6]   # areas, the early game first
KEEP = ("kind", "style", "shape", "top", "front", "front_h", "over_h", "attached")
TURN_SCALE = 1.0        # turned views are stored at this scale


def area_title(rm):
    import picori_labels as PL
    a, r = (int(v) for v in rm.rid.split("_"))
    n = PL.location(a, r).get("area_name") or f"AREA_{a}"
    return n.replace("AREA_", "").replace("_", " ").title()


BUILD = "tiles and things (doc 12)"


def do_room(dump, out, vox=True, kinds=None):
    rm = RM.load(dump)
    r = rm.raw
    box = (0, 0, r.cells_w, r.cells_h)
    pic = SC.crop(rm.picture, box)
    Image.fromarray(pic).save(out / "img" / f"{rm.rid}.png")
    _base, om = OV.under(rm, box)
    labels = CL.classify(rm, box, pic, om)
    objs = []
    for L in labels:
        o = {"bbox": L["bbox"]}
        o.update({k: L.get(k) for k in KEEP if k in L})
        objs.append(o)
    rec = {"rid": rm.rid, "area": int(rm.rid.split("_")[0]), "area_title": area_title(rm),
           "title": rm.rid.replace("_", "/"), "w": int(pic.shape[1]), "h": int(pic.shape[0]),
           "img": f"img/{rm.rid}.png", "objects": objs}
    if vox:
        V, F, UV, tex, report = AS.build(rm, kinds)
        g, *turned = GL.render_views(np.asarray(V, float), np.asarray(F), np.asarray(UV, float),
                                     tex, pic.shape[:2], [("game",), (35, 30), (-35, 30)])
        Image.fromarray(g).save(out / "vox" / f"{rm.rid}_game.png")
        views = [Image.fromarray(v) for v in turned]
        views = [v.resize((max(1, int(v.width * TURN_SCALE)), max(1, int(v.height * TURN_SCALE))), Image.NEAREST)
                 for v in views]
        sheet = Image.new("RGB", (sum(v.width for v in views) + 8, max(v.height for v in views)), (22, 24, 30))
        x = 0
        for v in views:
            sheet.paste(v, (x, 0))
            x += v.width + 8
        sheet.save(out / "vox" / f"{rm.rid}_turned.png")
        rec.update(vox_game=f"vox/{rm.rid}_game.png", vox_turned=f"vox/{rm.rid}_turned.png",
                   turned_w=sheet.width, turned_h=sheet.height, vox_build=BUILD,
                   vox_date=time.strftime("%Y-%m-%d"), vox_check=report)
    (out / "data" / f"{rm.rid}.json").write_text(json.dumps(rec))
    return rec


def write_index(out):
    recs = [json.loads(p.read_text()) for p in (out / "data").glob("*.json")]
    order = {a: i for i, a in enumerate(STORY)}
    recs.sort(key=lambda r: (order.get(r["area"], 999), r["area"], r["rid"]))
    (out / "rooms.js").write_text("window.ROOMS=" + json.dumps(recs) + ";\n")
    return len(recs)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("out")
    ap.add_argument("--areas", default="", help="area numbers, comma separated")
    ap.add_argument("--rooms", default="", help="AA_RR, comma separated")
    ap.add_argument("--no-vox", action="store_true", help="labels only")
    ap.add_argument("--force", action="store_true", help="redo rooms already written")
    ap.add_argument("--revox", action="store_true", help="redo the voxel builds only")
    ap.add_argument("--tiles", default="", help="the tile catalogue with first-guess kinds (tiles.py --out)")
    a = ap.parse_args()
    out = Path(a.out)
    for d in ("img", "data", "vox"):
        (out / d).mkdir(parents=True, exist_ok=True)
    dumps = sorted(Path(a.dumps).glob("room_*.tmcr"),
                   key=lambda p: [int(v) for v in p.stem[5:].split("_")])
    kinds = None
    if not a.no_vox:
        if a.tiles:
            cat = json.loads(Path(a.tiles).read_text())
        else:
            from . import tiles as TL
            cat = TL.catalogue(a.dumps)
            TL.features(a.dumps, cat)
            for e in cat.values():
                e["kind"] = TL.guess_kind(e)
        kinds = AS.load_kinds(cat)
    want_a = {int(v) for v in a.areas.split(",") if v}
    want_r = {v for v in a.rooms.split(",") if v}
    for p in dumps:
        rid = p.stem[5:]
        if (want_a or want_r) and int(rid.split("_")[0]) not in want_a and rid not in want_r:
            continue
        done = out / "data" / f"{rid}.json"
        if done.exists() and not a.force:
            rec = json.loads(done.read_text())
            if a.no_vox or (rec.get("vox_build") == BUILD and not a.revox):
                continue
        t = time.time()
        try:
            rec = do_room(p, out, vox=not a.no_vox, kinds=kinds)
            print(f"{rid}: {len(rec['objects'])} labels, {time.time() - t:.0f}s", flush=True)
        except Exception as e:      # one room's failure does not stop the batch
            print(f"{rid}: failed: {e!r}", flush=True)
        write_index(out)
    print(f"index: {write_index(out)} rooms", flush=True)


if __name__ == "__main__":
    main()
