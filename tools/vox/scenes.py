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

from . import glrender as GL
from . import heights as HT
from . import mesh as MS
from . import overhead as OV
from . import poster as PO
from . import classify as CL
from . import buildings as BD
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
    "field_bank":  ("03_03", (0, 4, 9, 14)),      # steps up an earth bank, one level
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
RIM_MAX = 8             # px: an awning's front rim is at most this many rows


def hang_awning(m, pic, top_mat, gnd, yb, s, eave=None):
    """An awning's heights, in place (classify.awnings). It hangs from its
    building at the top of the building's wall (eave); without one, its
    front's underside stands over the first ground below it (the
    45-degree rule). Its rim (the rows at its bottom not of its top's
    material) is upright; above that its top is level. Heights agree
    across it, so it is one flat sheet."""
    h, w = m.shape
    cols = {}
    for x in np.nonzero(m.any(axis=0))[0]:
        col = np.nonzero(m[:, x])[0]
        rb = col.max()
        f = rb + 1
        while f < h and not gnd[f, x]:
            f += 1
        if f < h:
            # the underside's front edge, drawn on row rb, over ground at f
            cols[x] = (col.min(), rb, f + (yb[f, x] + s[f, x]) - rb - 1)
    if not cols:
        return
    hu = eave if eave is not None else float(np.median([c[2] for c in cols.values()]))
    for x, (rt, rb, _hu) in cols.items():
        t = 0
        while t < RIM_MAX and rb - t >= rt and \
                CL.family(pic[rb - t:rb - t + 1, x], saturated_first=True) != top_mat:
            t += 1
        for r in range(rb, rt - 1, -1):
            k = rb - r
            if k < t:
                yb[r, x], s[r, x] = hu + k, 1.0
            else:
                yb[r, x], s[r, x] = hu + t, 0.0


def render(V, F, UV, pic, yaw, pitch, game, size=None):
    """The surface from a camera, textured with the picture (size: the
    crop's own height and width, when the texture is taller)."""
    TH, W = pic.shape[:2]
    UV = np.asarray(UV, float)
    T = np.repeat(np.stack([UV[:, 0] / W, 1.0 - UV[:, 1] / TH], axis=1), 4, axis=0)
    F = np.asarray(F).tolist()
    H = size[0] if size else TH
    FT = [[4 * i, 4 * i + 1, 4 * i + 2, 4 * i + 3] for i in range(len(F))]
    C = np.full((len(V), 3), 0.6)
    box = (0, W, 0, H)
    if game:
        img = RC.render(V, C, F, RC.camera(0, 45), box, scale=(1.0, math.sqrt(2)),
                        tall=TALL, tex=(pic, T, FT), lit=False)
        return np.asarray(img)[TALL:TALL + H, :W]
    return np.asarray(RC.render(V, C, F, RC.camera(yaw, pitch), box, tall=TALL,
                                tex=(pic, T, FT)))


def _kept(V, F, UV):
    """Drop the quads of masked pixels (built at -1e6)."""
    keep = V[F].min(axis=1)[:, 1] > -1e5 if len(F) else np.zeros(0, bool)
    return V, F[keep], UV[keep]


def _join(parts):
    """One mesh from several (V, F, UV)."""
    Vs, Fs, UVs, n = [], [], [], 0
    for v, f, uv in parts:
        v = np.asarray(v, float).reshape(-1, 3)
        Vs.append(v)
        Fs.append(np.asarray(f, int).reshape(-1, 4) + n)
        UVs.append(np.asarray(uv, float).reshape(-1, 2))
        n += len(v)
    return np.concatenate(Vs), np.concatenate(Fs), np.concatenate(UVs)


def build_scene(rm, box, pic, poster=False, styles=False):
    """(V, F, UV, tex): the crop built, as quads textured from tex (the
    ground layer's picture over the shown one). box in cells; a whole
    room is (0, 0, cells_w, cells_h)."""
    base, om = OV.under(rm, box)
    post, _idx = PO.posterize(base) if poster else (base, None)
    lab, _n, sh = RG.split(post)
    yb, sl, gnd = HT.solve(rm, lab, sh, box, post, hidden=om)
    top = yb + sl
    Hh = pic.shape[0]
    # buildings by their style, from the full picture
    bmask = np.zeros(pic.shape[:2], bool)
    amask = np.zeros(pic.shape[:2], bool)
    bparts = []
    if styles:
        objs, _dp = CL.objects(rm, box, pic, om)
        objs = CL._merge(objs, pic)
        labs = [CL.label(m, bb, pic, [], isb) for m, bb, isb in objs]
        eave = {}               # building index -> its wall's top
        for j, ((m, bb, isb), L) in enumerate(zip(objs, labs)):
            if isb is not True:
                continue
            if L["kind"] != "building" or L["style"] not in BD.BUILT:
                continue
            gat = lambda r, x: float(top[min(r + 1, Hh - 1), x]) if gnd[min(r + 1, Hh - 1), x] else 0.0
            byb, bs = BD.heights(m, pic, L["style"], L["top"], gat, L.get("band"))
            m = m & ~np.isnan(byb)
            bparts.append((m, byb, bs))
            bmask |= m
            wall = (bs >= 0.75) & m
            if wall.any():
                eave[j] = float(np.median([(byb[:, x][wall[:, x]] + 1).max()
                                           for x in np.nonzero(wall.any(axis=0))[0]]))
        for i in CL.awnings(objs, labs):
            hang_awning(objs[i][0], pic, labs[i]["top"], gnd, yb, sl,
                        eave.get(labs[i]["attached"]))
            amask |= objs[i][0]
    yb_g = np.where(bmask, -1e6, yb)
    parts = [_kept(*MS.build(yb_g, sl, gnd | bmask, open_=amask))]
    for m, byb, bs in bparts:
        yy = np.where(m, byb, -1e6)
        v2, f2, uv2 = _kept(*MS.build(np.nan_to_num(yy, nan=-1e6), np.where(m, bs, 0), ~m))
        parts.append((v2, f2, uv2 + np.array([0.0, Hh])))
    om = om & ~bmask & ~amask
    pic_p = PO.posterize(pic)[0] if poster else pic
    p_shade = p_sat = None
    from .room import RE
    cells = np.kron(rm.cls[box[1]:box[3], box[0]:box[2]] == RE.CLASS_GROUND,
                    np.ones((16, 16), bool))[:pic.shape[0], :pic.shape[1]]
    for P, zp, T in OV.shapes(om, pic_p, top, ~gnd, walk=cells):
        if zp == "roof":
            if p_shade is None:
                p_shade, p_sat = RG.split(pic_p)[2], RG.hsv(pic_p)[1]
            part = OV.roof(P, pic_p, top, ~gnd, Hh, p_shade, p_sat)
        elif zp is not None:
            part = OV.slab(P, zp, T, Hh)
        else:
            part = OV.sheet(P, top, Hh)
        if len(part[1]):
            parts.append(tuple(np.asarray(a) for a in part))
    V, F, UV = _join(parts)
    tex = np.vstack([base, pic])
    return V, F, UV, tex


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--what", default="regions")
    ap.add_argument("--poster", action="store_true", help="read shapes from a posterized picture")
    ap.add_argument("--tag", default="", help="suffix for the sheets")
    ap.add_argument("--styles", action="store_true", help="build buildings by their style")
    ap.add_argument("--slow", action="store_true", help="render with refcheck (Python) instead of WebGL")
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
            V, F, UV, tex = build_scene(rm, box, pic, a.poster, a.styles)
            panels, labels = [pic], ["the game"]
            views = ((0, 45, True), (35, 30, False), (-35, 30, False))
            if a.slow:
                imgs = [render(V, F, UV, tex, y, p, g, pic.shape[:2]) for y, p, g in views]
            else:
                imgs = GL.render_views(V, F, UV, tex, pic.shape[:2],
                                       [("game",) if g else (y, p) for y, p, g in views], tall=TALL)
            for (yaw, pitch, game), im in zip(views, imgs):
                panels.append(np.ascontiguousarray(im[:, :, :3]))
                labels.append("built, game camera" if game else f"yaw {yaw} pitch {pitch}")
            sheet(panels, labels, out / f"{name}_build{a.tag}.png")
        elif a.what == "classes":
            _base, om = OV.under(rm, box)
            L = CL.classify(rm, box, pic, om)
            CL.sheet(pic, L, out / f"{name}_classes.png")
            CL.write(L, out / f"{name}_classes.json")
        print(name)


if __name__ == "__main__":
    main()
