"""The class review page's data: every reference scene's labels and its
picture, for the shared review page (right or wrong, then notes).

  python3 -m vox.review DUMPS OUT

Writes OUT/scenes.js and OUT/img/<scene>.png. The pictures are the
player's own ROM art: OUT must be outside the repository (or ignored),
and the page stays private to the people it is shared with.

Each object's id is <scene>-<x0>-<y0>-<x1>-<y1>, so a review stays with
an object while its box does not change.
"""
import argparse
import json
from pathlib import Path

from PIL import Image

from . import classify as CL
from . import overhead as OV
from . import room as RM
from . import scenes as SC

TITLES = {
    "gate": "Town gate", "blue_house": "Blue house", "west_ledge": "West ledge",
    "mushrooms": "Mushroom houses", "links_house": "Link's house",
    "garden": "Castle garden", "field_bank": "Hyrule Field bank",
}
KEEP = ("kind", "style", "shape", "top", "front", "front_h", "over_h", "attached")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("out")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "img").mkdir(parents=True, exist_ok=True)
    rooms, scenes = {}, []
    for name, (rid, box) in SC.SCENES.items():
        if rid not in rooms:
            rooms[rid] = RM.load(Path(a.dumps) / f"room_{rid}.tmcr")
        rm = rooms[rid]
        pic = SC.crop(rm.picture, box)
        _base, om = OV.under(rm, box)
        labels = CL.classify(rm, box, pic, om)
        Image.fromarray(pic).save(out / "img" / f"{name}.png")
        objs = []
        for L in labels:
            o = {"id": f"{name}-" + "-".join(str(v) for v in L["bbox"]), "bbox": L["bbox"]}
            o.update({k: L.get(k) for k in KEEP if k in L})
            objs.append(o)
        scenes.append({"scene": name, "title": TITLES.get(name, name), "room": rid,
                       "w": int(pic.shape[1]), "h": int(pic.shape[0]),
                       "img": f"img/{name}.png", "objects": objs})
        print(name, len(objs))
    (out / "scenes.js").write_text("window.SCENES=" + json.dumps(scenes) + ";\n")


if __name__ == "__main__":
    main()
