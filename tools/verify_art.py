#!/usr/bin/env python3
"""
verify_art.py — prove the art decoder is right, rather than assuming it.

"It looks correct" is not verification. Two independent checks:

  selfcheck   Offline, no game needed. A v3 dump carries the same picture
              twice over: the engine's own expanded subtile map
              (gMapData*Special) and the raw ingredients to rebuild it
              (tile -> subTile[t*4+k]). Rendering both and diffing localises
              exactly which cells the naive path gets wrong — which is how the
              special-tile case (tileIndex >= 0x4000, resolved through
              mapDataOriginal) shows itself as a coordinate list instead of a
              vague "the image isn't quite right".

  groundtruth Runs the game. roomcap captures a real PPU framebuffer PNG of the
              same room, then this aligns it against the decoded room by
              brute-force offset search and reports the pixel match. The
              framebuffer contains sprites and HUD that the BG render does not,
              so a perfect score is not expected — but a sharp correlation peak
              at a plausible offset, with high agreement there, is proof the
              tiles, palettes, flips and char base are all correct.

Usage
-----
  python3 tools/verify_art.py selfcheck vrdump/
  python3 tools/verify_art.py selfcheck vrdump/room_03_08.tmcr --save-diff diff.png
  python3 tools/verify_art.py groundtruth --area 3 --room 8
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import importlib.util

REPO = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location(
    "extract_art", Path(__file__).with_name("extract_art.py"))
EA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(EA)
RE = EA.RE


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------

def render_naive(room, li):
    """Render via tileIndex*4 -> subTile, i.e. the reimplementation. Correct
    only for tileIndex < 2048; GetSafeTileSetIndex (beanstalkSubtask.c:1195)
    routes >= 0x4000 through mapDataOriginal, which we never captured."""
    layer = room.layers[li]
    if not layer["present"] or room.cells_w == 0 or layer.get("subtile") is None:
        return None
    chars = EA.decode_chars(room.bg_vram)
    pal = EA.palette_rgba(room.bg_palette)
    cb = layer.get("char_base", 0) // 32
    img = np.zeros((room.cells_h * 16, room.cells_w * 16, 4), dtype=np.uint8)
    cache = {}
    for cy in range(room.cells_h):
        for cx in range(room.cells_w):
            t = int(layer["tile"][cy, cx])
            if t >= EA.TILESET:
                continue
            if t not in cache:
                cache[t] = EA.render_metatile(t, chars, pal, layer["subtile"], cb)
            img[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16] = cache[t]
    return img


def render_engine(room, li):
    """Render via gMapData*Special — the engine's own output."""
    layer = room.layers[li]
    if layer.get("subtilemap") is None:
        return None
    chars = EA.decode_chars(room.bg_vram)
    pal = EA.palette_rgba(room.bg_palette)
    cb = layer.get("char_base", 0) // 32
    stm = layer["subtilemap"]
    img = np.zeros((room.cells_h * 16, room.cells_w * 16, 4), dtype=np.uint8)
    for sy in range(room.cells_h * 2):
        for sx in range(room.cells_w * 2):
            img[sy * 8:(sy + 1) * 8, sx * 8:(sx + 1) * 8] = \
                EA.render_subtile(int(stm[sy * 0x80 + sx]), chars, pal, cb)
    return img


def cmd_selfcheck(args):
    rooms = list(RE.iter_rooms(Path(args.path)))
    v3 = [r for r in rooms if getattr(r, "version", 1) >= 3]
    if not v3:
        sys.exit("No v3 dumps found. selfcheck needs gMapData*Special, added in\n"
                 "format v3 — rebuild vr_world.c and re-harvest.")

    total_cells = agree = 0
    worst = []
    for r in v3:
        for li in (0, 1):
            eng = render_engine(r, li)
            nai = render_naive(r, li)
            if eng is None or nai is None:
                continue
            # compare per 16x16 cell so disagreement is reported as tiles
            h, w = r.cells_h, r.cells_w
            bad = []
            for cy in range(h):
                for cx in range(w):
                    a = eng[cy*16:(cy+1)*16, cx*16:(cx+1)*16]
                    b = nai[cy*16:(cy+1)*16, cx*16:(cx+1)*16]
                    total_cells += 1
                    if np.array_equal(a, b):
                        agree += 1
                    else:
                        bad.append((cx, cy, int(r.layers[li]["tile"][cy, cx])))
            if bad:
                worst.append((r.area, r.room, li, len(bad), h * w, bad))

    print(f"{len(v3)} v3 rooms, {total_cells} cells compared")
    print(f"engine map and naive rebuild agree on {agree} "
          f"({100 * agree / max(total_cells, 1):.2f}%)\n")

    if not worst:
        print("Perfect agreement. Both paths produce identical pixels, so the\n"
              "naive lookup happens to be sufficient for this data set.")
        return

    worst.sort(key=lambda t: -t[3])
    print("rooms where the two paths differ (worst first):")
    for a, rm, li, n, tot, bad in worst[:12]:
        idx = sorted({t for _, _, t in bad})
        special = [t for t in idx if t >= 0x4000]
        print(f"  area {a:3d} room {rm:2d} L{li}: {n}/{tot} cells differ, "
              f"{len(idx)} distinct tiles, {len(special)} of them >= 0x4000")
    print("\nCells whose tileIndex is >= 0x4000 are the special-tile path.\n"
          "If the differing cells are all of that kind, the engine map is\n"
          "authoritative and the naive path should never be used.")

    if args.save_diff and worst:
        a, rm, li, n, tot, bad = worst[0]
        r = next(x for x in v3 if x.area == a and x.room == rm)
        eng, nai = render_engine(r, li), render_naive(r, li)
        strip = np.concatenate([eng, nai], axis=1)
        Image.fromarray(strip).save(args.save_diff)
        print(f"\nengine | naive written to {args.save_diff}")


# ---------------------------------------------------------------------------
# groundtruth
# ---------------------------------------------------------------------------

def best_offset(big_rgb, small_rgb, step=1):
    """Brute-force the offset of `small` within `big`. Returns
    (x, y, agreement_fraction). Uses mean absolute error on RGB."""
    H, W, _ = big_rgb.shape
    h, w, _ = small_rgb.shape
    if h > H or w > W:
        return None
    best = (None, None, 1e18)
    small = small_rgb.astype(np.int16)
    for y in range(0, H - h + 1, step):
        for x in range(0, W - w + 1, step):
            win = big_rgb[y:y + h, x:x + w].astype(np.int16)
            err = np.abs(win - small).mean()
            if err < best[2]:
                best = (x, y, err)
    x, y, err = best
    win = big_rgb[y:y + h, x:x + w]
    exact = float((np.abs(win.astype(np.int16) -
                          small_rgb.astype(np.int16)).max(axis=2) <= 2).mean())
    return x, y, exact, err


def cmd_groundtruth(args):
    binary = (REPO / args.binary).resolve()
    if not binary.exists():
        sys.exit(f"binary not found: {binary}")
    workdir = binary.parent
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update({
        "TMC_AUTOPLAY": "1", "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy", "TMC_ROOMCAP": "1",
        "TMC_ROOMCAP_WARP": f"{args.area},{args.room},{args.x},{args.y},1",
        "TMC_ROOMCAP_SETTLE": str(args.settle),
    })

    png = out / f"truth_{args.area:02d}_{args.room:02d}.png"
    tmcr = out

    # 1. real PPU framebuffer
    e1 = dict(env); e1["TMC_ROOMCAP_OUT"] = str(png)
    subprocess.run([str(binary)], cwd=workdir, env=e1,
                   capture_output=True, timeout=args.timeout)
    # 2. world snapshot, same warp and settle
    e2 = dict(env); e2["TMC_ROOMCAP_TMCR"] = str(tmcr)
    subprocess.run([str(binary)], cwd=workdir, env=e2,
                   capture_output=True, timeout=args.timeout)

    dump = tmcr / f"room_{args.area:02d}_{args.room:02d}.tmcr"
    if not png.exists() or not dump.exists():
        sys.exit(f"capture failed (png={png.exists()} tmcr={dump.exists()})")

    truth = np.array(Image.open(png).convert("RGB"))
    room = RE.load_room(dump)
    bottom = EA.room_art(room, 0)
    top = EA.room_art(room, 1)
    comp = bottom.copy()
    if top is not None:
        m = top[:, :, 3] > 0
        comp[m] = top[m]
    mine = comp[:, :, :3]

    print(f"framebuffer {truth.shape[1]}x{truth.shape[0]}, "
          f"decoded room {mine.shape[1]}x{mine.shape[0]}")
    res = best_offset(mine, truth, step=args.step)
    if res is None:
        sys.exit("framebuffer is larger than the decoded room; nothing to align")
    x, y, exact, err = res
    print(f"best alignment at ({x}, {y}): {100 * exact:.1f}% of pixels match "
          f"within tolerance, mean abs error {err:.1f}")
    print("\nThe framebuffer also contains sprites (Link, NPCs) and the HUD,\n"
          "which the BG-only decode cannot contain, so 100% is not the target.\n"
          "A sharp peak at a plausible offset with high agreement means the\n"
          "tiles, palettes, flips and char base are all correct.")

    side = np.concatenate(
        [truth, mine[y:y + truth.shape[0], x:x + truth.shape[1]]], axis=0)
    cmp_png = out / f"compare_{args.area:02d}_{args.room:02d}.png"
    Image.fromarray(side).save(cmp_png)
    print(f"\ngame above, decode below: {cmp_png}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("selfcheck", help="engine map vs naive rebuild, offline")
    s.add_argument("path")
    s.add_argument("--save-diff", help="write a side-by-side of the worst room")
    s.set_defaults(func=cmd_selfcheck)

    g = sub.add_parser("groundtruth", help="compare against a real PPU frame")
    g.add_argument("--binary", default="dist/USA/tmc_pc")
    g.add_argument("--out", default="verify")
    g.add_argument("--area", type=int, default=3)
    g.add_argument("--room", type=int, default=8)
    g.add_argument("--x", default="0x1d8")
    g.add_argument("--y", default="0x138")
    g.add_argument("--settle", type=int, default=300)
    g.add_argument("--timeout", type=int, default=180)
    g.add_argument("--step", type=int, default=1,
                   help="alignment search stride; 2 is ~4x faster")
    g.set_defaults(func=cmd_groundtruth)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
