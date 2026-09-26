#!/usr/bin/env python3
"""refcheck.py — look at the voxel model of known rooms next to the game's art.

Every change to the depth model is judged by eye, one room at a time, and a
fix for one room quietly breaks another. This renders a fixed set of
reference cases so a change can be judged on all of them at once, the same
way every time.

Each case is a rectangle of cells in one room (vr/reference.txt). For each,
the sheet shows three panels side by side:


    art       the room as the game draws it (both layers composited)
    SW, SE    the mesh from the south-west and the south-east, where depth
              is visible. Surfaces are coloured by projecting the art from
              the game's camera (a point at height y over (x, z) takes the
              pixel drawn at (x, z - y)), so a front face shows the band
              drawn above its base and a raised top shows its drawn top.

The mesh is `room_explore.py voxel` output, so this checks what the
pipeline actually builds. With --worldgen it is `worldgen --texture` output
instead, rendered through its own UVs and atlas: that is the mesh the
overworld stage writes, and the only way to check a surface whose texture
is not the plain projection (free blocks, --blocks).

Usage
-----
  python3 tools/refcheck.py vrdump                      # every case
  python3 tools/refcheck.py vrdump --only brazier       # cases whose label matches
  python3 tools/refcheck.py vrdump --out refcheck/ --subdiv 4

Output: one PNG per case plus refcheck/index.png, a contact sheet of all.
Everything written is derived from your ROM; refcheck/ is gitignored.
"""
import argparse
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402

BG = (22, 24, 30)
LIGHT = np.array([-1.0, 1.4, -1.0])        # from the north-west and above
LIGHT /= np.linalg.norm(LIGHT)


def load_cases(path):
    cases = []
    for ln, line in enumerate(Path(path).read_text().splitlines(), 1):
        note = line.split("#", 1)[1].strip() if "#" in line else ""
        f = line.split("#", 1)[0].split()
        if not f:
            continue
        if len(f) < 3:
            sys.exit(f"{path}:{ln}: need <room> <cx0,cy0,cx1,cy1> <label>")
        rect = tuple(int(v) for v in f[1].split(","))
        cases.append(dict(room=f[0], rect=rect, label=f[2], note=note))
    return cases


def load_obj(path, uvs=False):
    V, C, F, T, FT = [], [], [], [], []
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            V.append([float(p[1]), float(p[2]), float(p[3])])
            C.append([float(t) for t in p[4:7]] if len(p) >= 7 else [.6, .6, .6])
        elif p[0] == "vt":
            T.append([float(p[1]), float(p[2])])
        elif p[0] == "f":
            F.append([int(t.split("/")[0]) - 1 for t in p[1:]])
            FT.append([int(t.split("/")[1]) - 1 if "/" in t else -1
                       for t in p[1:]])
    if uvs:
        return np.array(V, float), np.array(C, float), F, np.array(T, float), FT
    return np.array(V, float), np.array(C, float), F


def camera(yaw_deg, pitch_deg):
    """Orthographic camera. Returns (right, up, toward-viewer) unit vectors.

    yaw 0, pitch 45 is the game's camera: it looks north and down, so a point
    one unit up and one unit south lands on the same pixel -- drawn row =
    z - height, which is the 45-degree oblique viewangle.py measures.
    """
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    fwd = np.array([math.sin(y) * math.cos(p), -math.sin(p),
                    -math.cos(y) * math.cos(p)])      # direction of view
    right = np.array([math.cos(y), 0.0, math.sin(y)])
    up = np.cross(right, fwd)
    return right, up, -fwd


def render(V, C, F, view, box, scale=(1.0, 1.0), tall=96, tex=None):
    """Z-buffered flat-shaded raster of quads, cropped to a world-space box.

    tex = (atlas, T, FT): colour each pixel from the mesh's own UVs, T the
    vt array and FT each face's vt indices (worldgen output).

    tex = (art, origin_x, origin_y): colour each pixel by projecting the art
    from the game's camera: a surface point at height y over (x, z) takes
    the pixel drawn at (x, z - y). A top lifted h takes the drawing h rows
    north, where its top is drawn; a south-facing wall takes the band drawn
    above its base, which is its front face. Without tex, mesh colours.

    box = (x0, x1, z0, z1) in world pixels: the case rectangle. Everything is
    projected, then the image is cropped to where that rectangle's floor
    lands, with a margin above for anything standing on it.
    """
    right, up, toward = view
    sx = V @ right * scale[0]
    sy = -(V @ up) * scale[1]
    depth = V @ toward
    x0, x1, z0, z1 = box
    corners = np.array([[x0, 0, z0], [x1, 0, z0], [x0, 0, z1], [x1, 0, z1],
                        [x0, tall, z0], [x1, tall, z0]], float)
    cx = corners @ right * scale[0]
    cy = -(corners @ up) * scale[1]
    ox, oy = cx.min(), cy.min()
    W = int(round(cx.max() - ox))
    H = int(round(cy.max() - oy))
    img = np.zeros((H, W, 3), float)
    img[:] = np.array(BG) / 255.0
    zb = np.full((H, W), -1e18)
    sx = sx - ox
    sy = sy - oy
    for fi, f in enumerate(F):
        if len(f) < 3:
            continue
        a, b, c = V[f[0]], V[f[1]], V[f[2]]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln == 0:
            continue
        n /= ln
        shade = 0.55 + 0.45 * max(0.0, float(n @ LIGHT))
        col = C[f].mean(0) * shade
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])) if len(f) == 4 \
                else ((f[0], f[1], f[2]),):
            px, py, pz = sx[list(tri)], sy[list(tri)], depth[list(tri)]
            bx0, bx1 = int(max(0, math.floor(px.min()))), int(min(W - 1, math.ceil(px.max())))
            by0, by1 = int(max(0, math.floor(py.min()))), int(min(H - 1, math.ceil(py.max())))
            if bx1 < bx0 or by1 < by0:
                continue
            gx, gy = np.meshgrid(np.arange(bx0, bx1 + 1) + 0.5,
                                 np.arange(by0, by1 + 1) + 0.5)
            d = (py[1] - py[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (py[0] - py[2])
            if abs(d) < 1e-12:
                continue
            w0 = ((py[1] - py[2]) * (gx - px[2]) + (px[2] - px[1]) * (gy - py[2])) / d
            w1 = ((py[2] - py[0]) * (gx - px[2]) + (px[0] - px[2]) * (gy - py[2])) / d
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = w0 * pz[0] + w1 * pz[1] + w2 * pz[2]
            sub = zb[by0:by1 + 1, bx0:bx1 + 1]
            win = inside & (z > sub)
            sub[win] = z[win]
            if tex is None:
                img[by0:by1 + 1, bx0:bx1 + 1][win] = col
                continue
            if isinstance(tex[1], np.ndarray):
                atlas, UV, FT = tex
                ft = FT[fi]
                k = [f.index(i) for i in tri]
                if min(ft) < 0:
                    img[by0:by1 + 1, bx0:bx1 + 1][win] = col
                    continue
                t3 = UV[[ft[j] for j in k]]
                u = w0 * t3[0, 0] + w1 * t3[1, 0] + w2 * t3[2, 0]
                v = w0 * t3[0, 1] + w1 * t3[1, 1] + w2 * t3[2, 1]
                ah, aw = atlas.shape[:2]
                ax = np.clip((u * aw).astype(int), 0, aw - 1)
                ay = np.clip(((1.0 - v) * ah).astype(int), 0, ah - 1)
                img[by0:by1 + 1, bx0:bx1 + 1][win] = \
                    atlas[ay[win], ax[win], :3] / 255.0 * shade
                continue
            art, tox, toz = tex
            T = V[list(tri)]
            # World position of each pixel, nudged half a unit inward so a
            # wall samples the cell it belongs to, not its neighbour.
            wx = w0 * T[0, 0] + w1 * T[1, 0] + w2 * T[2, 0] - 0.5 * n[0]
            wz = w0 * T[0, 2] + w1 * T[1, 2] + w2 * T[2, 2] - 0.5 * n[2]
            wy = w0 * T[0, 1] + w1 * T[1, 1] + w2 * T[2, 1] - 0.5 * n[1]
            wz = wz - wy                     # project from the game camera
            ax = np.clip((wx - tox).astype(int), 0, art.shape[1] - 1)
            az = np.clip((wz - toz).astype(int), 0, art.shape[0] - 1)
            img[by0:by1 + 1, bx0:bx1 + 1][win] = \
                art[az[win], ax[win], :3] / 255.0 * shade
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def label_panel(img, text):
    out = Image.new("RGB", (img.width, img.height + 14), BG)
    out.paste(img, (0, 14))
    ImageDraw.Draw(out).text((2, 1), text, fill=(220, 220, 220))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", help="directory of .tmcr room dumps")
    ap.add_argument("--cases", default=str(HERE.parent / "vr" / "reference.txt"))
    ap.add_argument("--only", default="", help="substring of the case label")
    ap.add_argument("--out", default="refcheck")
    ap.add_argument("--canopy", action="store_true",
                    help="build tree crowns with voxel --canopy")
    ap.add_argument("--relief", action="store_true",
                    help="build heights with voxel --relief")
    ap.add_argument("--blocks", action="store_true",
                    help="build free-standing blocks as cubes (--blocks)")
    ap.add_argument("--worldgen", action="store_true",
                    help="render the textured worldgen mesh through its UVs")
    ap.add_argument("--subdiv", type=int, default=None,
                    help="voxel detail (4 = 4px); default 4 for voxel, "
                         "1 for --worldgen as the overworld stage uses")
    ap.add_argument("--zoom", type=int, default=2)
    ap.add_argument("--heights", default=str(HERE.parent / "vr" / "world" / "heights.txt"))
    a = ap.parse_args()

    cases = [c for c in load_cases(a.cases) if a.only in c["label"]]
    if not cases:
        sys.exit("no cases")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="refcheck"))
    meshes = {}
    sheets = []
    for i, c in enumerate(cases, 1):
        room = Path(a.dumps) / c["room"]
        if not room.exists():
            print(f"  [{i}/{len(cases)}] {c['label']}: {room} missing, skipped")
            continue
        r = RE.load_room(room)
        if c["room"] not in meshes:
            sub = a.subdiv or (1 if a.worldgen else 4)
            extra = ((["--canopy"] if a.canopy else [])
                     + (["--relief"] if a.relief else [])
                     + (["--blocks"] if a.blocks else [])
                     + ["--overlay", "--subdiv", str(sub)])
            if a.heights and Path(a.heights).exists():
                extra += ["--heights", a.heights]
            if a.worldgen:
                wd = tmp / room.stem
                obj = wd / f"area_{r.area:02d}.obj"
                cmd = [sys.executable, str(HERE / "room_explore.py"), "worldgen",
                       str(room), "--out", str(wd), "--texture"] + extra
            else:
                obj = tmp / (room.stem + ".obj")
                cmd = [sys.executable, str(HERE / "room_explore.py"), "voxel",
                       str(room), "--out", str(obj)] + extra
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0 or not obj.exists():
                print(f"  [{i}/{len(cases)}] {c['label']}: mesh failed: "
                      f"{(p.stderr or p.stdout).strip().splitlines()[-1:]}")
                continue
            if a.worldgen:
                V_, C_, F_, T_, FT_ = load_obj(obj, uvs=True)
                atlas = np.asarray(Image.open(obj.with_suffix(".png")).convert("RGB"))
                meshes[c["room"]] = (V_, C_, F_, (atlas, T_, FT_))
            else:
                meshes[c["room"]] = load_obj(obj) + (None,)
        V, C, F, uvtex = meshes[c["room"]]
        cx0, cy0, cx1, cy1 = c["rect"]
        # The case rectangle in world pixels.
        box = (r.origin_x + cx0 * 16, r.origin_x + (cx1 + 1) * 16,
               r.origin_y + cy0 * 16, r.origin_y + (cy1 + 1) * 16)
        # Only faces near the box: keeps the raster fast on big rooms.
        # Keep a face if its extent overlaps the box at all: greedy-merged
        # faces run far past a small box, and requiring every corner inside
        # dropped whole floors.
        keep = [k for k, f in enumerate(F)
                if V[f, 0].max() >= box[0] - 48 and V[f, 0].min() <= box[1] + 48
                and V[f, 2].max() >= box[2] - 48 and V[f, 2].min() <= box[3] + 96]
        Fn = [F[k] for k in keep]
        if uvtex is not None:
            uvtex = (uvtex[0], uvtex[1], [uvtex[2][k] for k in keep])
        art = RE.room_art_rgb(r, 0)
        crop = art[cy0 * 16:(cy1 + 1) * 16, cx0 * 16:(cx1 + 1) * 16].astype(np.uint8)
        art_img = Image.fromarray(crop)
        # Game camera: at yaw 0, pitch 45 a point's drawn row is z - y, once
        # the vertical axis is scaled by sqrt(2). Cropped to the floor of the
        # case rectangle only (tall=0), so it lines up pixel for pixel with
        # the art panel: anything standing up must appear where it is drawn.
        # With the art projected from the game's camera, the model seen from
        # that camera always reproduces the art, so it cannot check anything.
        # Two 3/4 views instead: from the south-west and the south-east.
        tex = uvtex if uvtex is not None else (art, r.origin_x, r.origin_y)
        game = render(V, C, Fn, camera(35, 35), box, scale=(1.0, 1.0), tex=tex)
        three = render(V, C, Fn, camera(-35, 35), box, scale=(1.0, 1.0), tex=tex)
        z = a.zoom
        panels = [label_panel(im.resize((im.width * z, im.height * z), Image.NEAREST), t)
                  for im, t in ((art_img, "art"), (game, "from south-west"), (three, "from south-east"))]
        h = max(p.height for p in panels)
        sheet = Image.new("RGB", (sum(p.width for p in panels) + 8 * 2, h + 18), BG)
        x = 0
        for p in panels:
            sheet.paste(p, (x, 18))
            x += p.width + 8
        ImageDraw.Draw(sheet).text(
            (2, 2), f"{c['label']}  {c['room']} {cx0},{cy0}-{cx1},{cy1}  {c['note']}"[:120],
            fill=(255, 220, 120))
        name = f"{i:02d}_{c['label']}.png"
        sheet.save(out / name)
        sheets.append(sheet)
        print(f"  [{i}/{len(cases)}] {c['label']:<14} -> {out / name}")
    if sheets:
        W = max(s.width for s in sheets)
        idx = Image.new("RGB", (W, sum(s.height + 6 for s in sheets)), BG)
        y = 0
        for s in sheets:
            idx.paste(s, (0, y))
            y += s.height + 6
        idx.save(out / "index.png")
        print(f"contact sheet -> {out / 'index.png'}")


if __name__ == "__main__":
    main()
