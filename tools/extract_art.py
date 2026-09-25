#!/usr/bin/env python3
"""
extract_art.py — turn v2 room dumps into actual pixels.

A v2 .tmcr carries everything needed to reconstruct the room's artwork:

    bg_vram      64 KB of 4bpp 8x8 character data, as the room has it loaded
    bg_palette   256 BGR555 entries (16 banks of 16)
    subTile      tileIndex*4 -> TL,TR,BL,BR BG tilemap entries
    tile         the 64x64 grid of metatile indices

This is the missing half of the pipeline. Collision tells you where geometry
is; only the art tells you what it looks like — and PotatoVoxel's procedural
height detector works by *measuring drawn pixels*, so without this there is no
height signal at all, just flat-coloured boxes.

Commands
--------
  atlas    <dumps> --out art/    per-room tileset atlas PNG (2048 metatiles)
  room     <file>  --out x.png   ONE layer of a room (--layer); use composite
                                 to see it as the game draws it
  tiles    <dumps> --out tiles/  one PNG per distinct metatile, deduplicated
                                 across rooms — the input to a voxelizer

Requires pillow + numpy.
"""

import argparse
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "room_explore", Path(__file__).with_name("room_explore.py"))
RE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RE)

TILESET = 2048


# ---------------------------------------------------------------------------
# GBA decode
# ---------------------------------------------------------------------------

def room_palette(room):
    """The palette to render with.

    v4 carries gPaletteBuffer (the fade SOURCE) alongside gBgPltt (PAL_RAM, the
    fade DESTINATION). src/fade.c:146 writes source -> destination through the
    active fade, so a capture taken while gFadeControl.active was set has its
    live palette pulled toward the fade colour — measured as a uniform +2 per
    5-bit channel against a reference. Prefer the source whenever we have it.
    """
    src = getattr(room, "src_palette", None)
    pal = src if src is not None else room.bg_palette
    return palette_rgba(pal, correct=COLOR_CORRECT[0])


# Picori's display transform, reproduced exactly (port/port_ppu.cpp:596).
# GBA art was authored for a dim, high-gamma reflective panel; shown raw on an
# sRGB monitor it reads over-bright. Picori decodes through the panel gamma to
# linear light and re-encodes with the sRGB OETF, and has this ON by default —
# so every screenshot of the game is corrected, while a raw palette read is not.
COLOR_CORRECT = [False]   # set by --gamma
PORT_GBA_LCD_GAMMA = 4.0
_GAMMA_LUT = None


def _gamma_lut():
    global _GAMMA_LUT
    if _GAMMA_LUT is None:
        x = np.arange(256) / 255.0
        lin = np.power(x, PORT_GBA_LCD_GAMMA)
        enc = np.where(lin <= 0.0031308, 12.92 * lin,
                       1.055 * np.power(lin, 1.0 / 2.4) - 0.055)
        _GAMMA_LUT = np.clip(enc * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return _GAMMA_LUT


def palette_rgba(bg_palette, correct=False):
    """256 BGR555 entries -> (256, 4) uint8 RGBA.

    The 5->8 bit expansion is `<< 3`, matching the engine (port_ppu.cpp:559
    calls it "a plain <<3"), NOT v*255/31 — the two differ by up to 7/255 at
    the top of the range, which is enough to miss an exact-match check.

    correct=True additionally applies Picori's colour-correction LUT, which is
    what makes output comparable to a screenshot.
    """
    p = np.asarray(bg_palette, dtype=np.uint16)
    r = ((p >> 0) & 0x1F) << 3
    g = ((p >> 5) & 0x1F) << 3
    b = ((p >> 10) & 0x1F) << 3
    out = np.stack([r, g, b, np.full_like(r, 255)], axis=-1).astype(np.uint8)
    if correct:
        lut = _gamma_lut()
        out[:, :3] = lut[out[:, :3]]
    out[::16, 3] = 0          # entry 0 of every bank
    return out


def decode_chars(bg_vram):
    """64 KB of 4bpp char data -> (2048, 8, 8) uint8 palette indices.

    GBA 4bpp packs two pixels per byte with the LOW nibble on the LEFT — the
    single most commonly got-wrong detail in GBA graphics code.
    """
    n = len(bg_vram) // 32
    d = np.asarray(bg_vram[:n * 32], dtype=np.uint8).reshape(n, 8, 4)
    lo = d & 0x0F
    hi = d >> 4
    out = np.empty((n, 8, 8), dtype=np.uint8)
    out[:, :, 0::2] = lo
    out[:, :, 1::2] = hi
    return out


def render_subtile(ent, chars, pal, char_base=0):
    """One 8x8 BG tilemap entry -> RGBA. bits 0-9 char, 10 hflip, 11 vflip,
    12-15 palette bank."""
    char = (ent & 0x3FF) + char_base
    if char >= len(chars):
        return np.zeros((8, 8, 4), dtype=np.uint8)
    px = chars[char]
    if (ent >> 10) & 1:
        px = px[:, ::-1]
    if (ent >> 11) & 1:
        px = px[::-1, :]
    return pal[((ent >> 12) & 0xF) * 16 + px]


def render_metatile(idx, chars, pal, subtile, char_base=0):
    """Assemble one 16x16 metatile from its four subtiles."""
    out = np.zeros((16, 16, 4), dtype=np.uint8)
    for k, (oy, ox) in enumerate(((0, 0), (0, 8), (8, 0), (8, 8))):
        out[oy:oy + 8, ox:ox + 8] = render_subtile(
            int(subtile[idx * 4 + k]), chars, pal, char_base)
    return out


def subtilemap_pixels(stm, sh, sw, chars, char_base=0):
    """Decode the top-left sh x sw subtiles of a 128x128 subtile map.

    Returns (index, valid), both (sh*8, sw*8): index is bank*16 + colour, the
    256-entry palette index of each pixel; valid is False where the entry
    points past the character data (render_subtile draws those blank). The
    same decode as render_subtile, for every subtile at once instead of in a
    Python loop.
    """
    ent = np.asarray(stm, dtype=np.int32).reshape(0x80, 0x80)[:sh, :sw]
    char = (ent & 0x3FF) + char_base
    valid = char < len(chars)
    char = np.where(valid, char, 0)[:, :, None, None]
    # Flips as index arithmetic: row r of a vflipped subtile is row 7-r.
    k = np.arange(8, dtype=np.int32)
    rows = k[None, None, :, None] ^ (((ent >> 11) & 1)[:, :, None, None] * 7)
    cols = k[None, None, None, :] ^ (((ent >> 10) & 1)[:, :, None, None] * 7)
    px = chars[char, rows, cols]                          # (sh, sw, 8, 8)
    idx = ((ent >> 12) & 0xF)[:, :, None, None] * 16 + px
    valid = np.broadcast_to(valid[:, :, None, None], idx.shape)
    # (sh, sw, 8, 8) -> (sh*8, sw*8): rows of subtiles, then rows within one.
    flat = lambda a: a.transpose(0, 2, 1, 3).reshape(sh * 8, sw * 8)
    return flat(idx), flat(valid)


def room_art(room, layer_index):
    """Render a layer exactly as the engine does.

    v3 carries gMapData{Bottom,Top}Special — the engine's own output from
    RenderMapLayerToSubTileMap. That has already resolved GetSafeTileSetIndex,
    including the >= 0x4000 special-tile path that needs mapDataOriginal, which
    we never captured. Rendering from it removes every reimplementation guess.

    v2 falls back to tileIndex*4 into subTile, which is correct for indices
    under 2048 and wrong for special tiles.
    """
    layer = room.layers[layer_index]
    if not layer["present"] or room.cells_w == 0:
        return None
    chars = decode_chars(room.bg_vram)
    pal = room_palette(room)
    cb = layer.get("char_base", 0) // 32          # as a char-index offset
    img = np.zeros((room.cells_h * 16, room.cells_w * 16, 4), dtype=np.uint8)

    stm = layer.get("subtilemap")
    if stm is not None:
        sh, sw = room.cells_h * 2, room.cells_w * 2
        idx, valid = subtilemap_pixels(stm, sh, sw, chars, cb)
        rgba = pal[idx]
        rgba[~valid] = 0
        img[:sh * 8, :sw * 8] = rgba
        return img

    sub = layer["subtile"]
    cache = {}
    for cy in range(room.cells_h):
        for cx in range(room.cells_w):
            t = int(layer["tile"][cy, cx])
            if t >= TILESET:
                continue
            if t not in cache:
                cache[t] = render_metatile(t, chars, pal, sub, cb)
            img[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16] = cache[t]
    return img


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def need_v2(rooms):
    v2 = [r for r in rooms if getattr(r, "version", 1) >= 2]
    if not v2:
        raise SystemExit(
            "No v2 dumps found. Art needs format v2 — rebuild with the updated\n"
            "vr_world.c and re-run tools/harvest_rooms.py.")
    return v2


def cmd_atlas(args):
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rooms = need_v2(list(RE.iter_rooms(Path(args.path))))
    for r in rooms:
        layer = r.layers[args.layer]
        if not layer["present"]:
            continue
        chars = decode_chars(r.bg_vram)
        pal = room_palette(r)
        cols = 32
        rows = (TILESET + cols - 1) // cols
        sheet = np.zeros((rows * 16, cols * 16, 4), dtype=np.uint8)
        for t in range(TILESET):
            y, x = divmod(t, cols)
            sheet[y * 16:(y + 1) * 16, x * 16:(x + 1) * 16] = \
                render_metatile(t, chars, pal, layer["subtile"],
                            layer.get("char_base", 0) // 32)
        f = out / f"atlas_{r.area:02d}_{r.room:02d}_L{args.layer}.png"
        Image.fromarray(sheet).save(f)
    print(f"{len(rooms)} atlases -> {out}")


def cmd_room(args):
    r = RE.load_room(Path(args.path))
    need_v2([r])
    img = room_art(r, args.layer)
    if img is None:
        raise SystemExit("layer not present, or room is 0x0")
    Image.fromarray(img).save(args.out)
    print(f"area {r.area} room {r.room} layer {args.layer} "
          f"({r.cells_w}x{r.cells_h} cells) -> {args.out}")


def cmd_composite(args):
    """Bottom then top. A single layer is never the whole picture: in Hyrule
    Field the top layer covers 35% of the room, and everything under it looks
    wrong in isolation because it was never meant to be seen."""
    r = RE.load_room(Path(args.path))
    need_v2([r])
    bottom = room_art(r, 0)
    top = room_art(r, 1)
    if bottom is None:
        raise SystemExit("bottom layer absent, or room is 0x0")
    out = bottom.copy()
    if top is not None:
        m = top[:, :, 3] > 0
        out[m] = top[m]
        print(f"top layer covers {100 * m.mean():.0f}% of the room")
    Image.fromarray(out).save(args.out)
    print(f"area {r.area} room {r.room} -> {args.out}")


def cmd_stitch(args):
    """Place every room of an area on one canvas at its world origin.

    A single room is rarely the unit anyone thinks in. Hyrule Field's rooms 8,
    9 and 0 all sit at x=1008 and stack to 640+160+208 = 1008 px — exactly the
    extent of the published map of that region. Rendering one room in isolation
    and comparing it to such a map looks like a bug and is not one.

    origin_x/origin_y are absolute world coordinates, so assembly needs no
    adjacency table: every room already knows where it belongs.
    """
    rooms = [r for r in RE.iter_rooms(Path(args.path))
             if r.area == args.area and r.cells_w]
    if args.rooms:
        want = {int(v, 0) for v in args.rooms.split(",")}
        rooms = [r for r in rooms if r.room in want]
    if not rooms:
        raise SystemExit(f"no usable rooms for area {args.area}")
    need_v2(rooms)

    x0 = min(r.origin_x for r in rooms)
    y0 = min(r.origin_y for r in rooms)
    W = max(r.origin_x + r.width for r in rooms) - x0
    H = max(r.origin_y + r.height for r in rooms) - y0
    if W * H > 120_000_000:
        raise SystemExit(f"area spans {W}x{H} px; narrow it with --rooms")

    canvas = np.zeros((H, W, 4), dtype=np.uint8)
    placed = 0
    for r in sorted(rooms, key=lambda r: (r.origin_y, r.origin_x)):
        bottom = room_art(r, 0)
        if bottom is None:
            continue
        img = bottom.copy()
        top = room_art(r, 1)
        if top is not None:
            m = top[:, :, 3] > 0
            img[m] = top[m]
        oy, ox = r.origin_y - y0, r.origin_x - x0
        canvas[oy:oy + img.shape[0], ox:ox + img.shape[1]] = img
        placed += 1

    Image.fromarray(canvas).save(args.out)
    print(f"area {args.area}: {placed} rooms -> {W}x{H} px -> {args.out}")
    faded = [r for r in rooms if getattr(r, "fade_active", 0)]
    if faded:
        print(f"  note: {len(faded)} room(s) were captured mid-fade; their "
              f"live palette is unreliable. v4 dumps render from the fade "
              f"source instead, so this is only a problem on v3 and older.")


def cmd_tiles(args):
    """Deduplicate every distinct metatile across every room.

    This is the real input to a voxelizer: PotatoVoxel authors height pins per
    tile, not per room, and the same tile recurs across hundreds of rooms. The
    hash is over the rendered pixels, so the same art drawn from different
    tileset slots collapses to one entry.
    """
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rooms = need_v2(list(RE.iter_rooms(Path(args.path))))
    seen, manifest = {}, []
    for r in rooms:
        for li, layer in enumerate(r.layers):
            if not layer["present"]:
                continue
            chars = decode_chars(r.bg_vram)
            pal = room_palette(r)
            used = set(int(t) for t in
                       layer["tile"][:r.cells_h, :r.cells_w].ravel() if t < TILESET)
            for t in used:
                px = render_metatile(t, chars, pal, layer["subtile"],
                                     layer.get("char_base", 0) // 32)
                h = hashlib.sha1(px.tobytes()).hexdigest()[:12]
                if h in seen:
                    seen[h]["rooms"] += 1
                    continue
                if px[:, :, 3].max() == 0:
                    continue          # fully transparent, nothing to voxelize
                Image.fromarray(px).save(out / f"tile_{h}.png")
                seen[h] = {"rooms": 1, "example": (r.area, r.room, li, t)}
    for h, v in sorted(seen.items(), key=lambda kv: -kv[1]["rooms"]):
        a, rm, li, t = v["example"]
        manifest.append(f"{h},{v['rooms']},{a},{rm},{li},{t}")
    (out / "manifest.csv").write_text(
        "hash,room_count,example_area,example_room,example_layer,example_tile\n"
        + "\n".join(manifest) + "\n")
    print(f"{len(seen)} distinct metatiles across {len(rooms)} rooms -> {out}")
    print("manifest.csv is sorted by how many rooms use each tile: author your\n"
          "height pins from the top down and a few hundred entries covers most\n"
          "of the game.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("atlas", help="per-room tileset atlas PNG")
    a.add_argument("path"); a.add_argument("--out", default="art")
    a.add_argument("--layer", type=int, default=0)
    a.set_defaults(func=cmd_atlas)

    r = sub.add_parser("room", help="render one layer of a room (see composite)")
    r.add_argument("path"); r.add_argument("--out", default="room.png")
    r.add_argument("--layer", type=int, default=0)
    r.set_defaults(func=cmd_room)

    c = sub.add_parser("composite", help="both layers, as the game composites them")
    c.add_argument("path"); c.add_argument("--out", default="composite.png")
    c.set_defaults(func=cmd_composite)

    st = sub.add_parser("stitch", help="assemble a whole area from its rooms")
    st.add_argument("path", help="directory of .tmcr dumps")
    st.add_argument("--area", type=int, required=True)
    st.add_argument("--out", default="area.png")
    st.add_argument("--rooms", help="comma-separated room ids (default: all)")
    st.set_defaults(func=cmd_stitch)

    t = sub.add_parser("tiles", help="deduplicated metatile library")
    t.add_argument("path"); t.add_argument("--out", default="tiles")
    t.set_defaults(func=cmd_tiles)

    ap.add_argument("--gamma", action="store_true",
                    help="apply Picori's colour-correction curve (on by default "
                         "in the game, so use this to compare against screenshots)")
    args = ap.parse_args()
    COLOR_CORRECT[0] = getattr(args, "gamma", False)
    args.func(args)


if __name__ == "__main__":
    main()
