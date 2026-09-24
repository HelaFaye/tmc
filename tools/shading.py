#!/usr/bin/env python3
"""
shading.py — separate SHADING from COLORATION in TMC's tile art.

Why this exists
---------------
TMC fakes 3D. A cliff has a lit top and a dark face; a house has a bright roof
and a shadowed wall. That shading encodes depth the collision data does not:
collision says "solid", the art says "solid, about this tall, lit from there".

To use it, the two kinds of darkness have to be told apart:

    shading      the same material rendered lighter or darker
    coloration   a genuinely different material

Getting that backwards makes every dark green bush read as a shadowed lawn.

The separation is possible because GBA palettes are built as RAMPS. Measured on
a real room's palette, bank 2 holds a green ramp at indices 1-4 (hue 120 -> 107,
luminance 0.28 -> 0.52) and a tan ramp at 6-9 (hue 20 -> 46, luminance 0.27 ->
0.61). Same hue, rising luminance, laid out contiguously. So:

    material = which ramp a palette index belongs to
    shade    = where in that ramp it sits

Commands
--------
  ramps    dump the ramps detected in a room's palette
  analyse  decompose a room into material/shade and test whether the shading
           actually predicts elevation, against the collision classifier

Dependencies: pillow, numpy.
"""

import argparse
import colorsys
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_explore as RE

# A ramp continues while the hue stays put and the luminance climbs. Both
# tolerances are loose because real ramps drift: TMC's green ramp swings 13
# degrees of hue across four entries, and some ramps dip a little before
# rising again.
HUE_TOL = 42.0        # degrees
LUM_DIP = 0.06        # luminance may fall this much and still be one ramp
NEUTRAL_S = 0.18      # below this saturation, hue is meaningless


def bgr555_to_rgb(v):
    v = int(v)
    return ((v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3)


def hls_of(rgb):
    h, l, s = colorsys.rgb_to_hls(*[c / 255.0 for c in rgb])
    return h * 360.0, l, s


def hue_gap(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def detect_ramps(bank_entries):
    """Split one 16-colour bank into ramps.

    Returns (ramp_id_per_index, shade_per_index). Index 0 is the transparent
    slot and 15 is usually pure black; both are given ramp -1 so they never
    count as a material.
    """
    ramp = [-1] * 16
    shade = [0.0] * 16
    cur = -1
    prev = None
    for i in range(16):
        rgb = bank_entries[i]
        h, l, s = hls_of(rgb)
        if i == 0 or (i == 15 and l < 0.02):
            prev = None
            continue
        start_new = True
        if prev is not None:
            ph, pl, ps = prev
            both_neutral = (s < NEUTRAL_S and ps < NEUTRAL_S)
            hue_ok = both_neutral or hue_gap(h, ph) <= HUE_TOL
            lum_ok = (l >= pl - LUM_DIP)
            start_new = not (hue_ok and lum_ok)
        if start_new:
            cur += 1
        ramp[i] = cur
        shade[i] = l
        prev = (h, l, s)

    # Normalise shade to 0..1 within each ramp, so "how dark for this material"
    # is comparable across materials of different intrinsic brightness.
    for rid in set(ramp):
        if rid < 0:
            continue
        idxs = [i for i in range(16) if ramp[i] == rid]
        ls = [shade[i] for i in idxs]
        lo, hi = min(ls), max(ls)
        for i in idxs:
            shade[i] = 0.5 if hi - lo < 1e-6 else (shade[i] - lo) / (hi - lo)
    return ramp, shade


def room_palette_banks(r):
    pal = r.src_palette if r.src_palette is not None else r.bg_palette
    if pal is None:
        return None
    pal = np.asarray(pal)
    return [[bgr555_to_rgb(pal[b * 16 + i]) for i in range(16)] for b in range(16)]


def cmd_ramps(args):
    r = RE.load_room(Path(args.path))
    banks = room_palette_banks(r)
    if banks is None:
        sys.exit("room has no palette (needs a v2+ dump)")
    print(f"{r}\n")
    for b in args.banks:
        ramp, shade = detect_ramps(banks[b])
        groups = {}
        for i in range(16):
            if ramp[i] >= 0:
                groups.setdefault(ramp[i], []).append(i)
        print(f"  bank {b}: {len(groups)} ramp(s)")
        for rid, idxs in sorted(groups.items()):
            hs = [hls_of(banks[b][i]) for i in idxs]
            hue = np.mean([h for h, _, _ in hs])
            sat = np.mean([s for _, _, s in hs])
            kind = "neutral" if sat < NEUTRAL_S else f"hue {hue:5.1f}"
            swatch = " ".join(f"#{banks[b][i][0]:02x}{banks[b][i][1]:02x}"
                              f"{banks[b][i][2]:02x}" for i in idxs)
            print(f"    ramp {rid}: idx {str(idxs):<18} {kind:>10}  {swatch}")
        print()


def decompose_room(r, layer_index=0, composite=True):
    """Return (material, shade) per PIXEL for a room.

    material is a global id (bank * 16 + ramp), shade is 0..1 within its ramp.

    The TOP layer is composited over the bottom by default. Layer 0 alone is
    not what the game shows: tree canopies live on layer 1, and the tiles
    beneath them are filler that is never drawn. Decomposing layer 0 in
    isolation assigns every under-tree pixel the material of a hidden tile,
    which silently corrupts any statistic built on it.
    """
    import extract_art as A
    layer = r.layers[layer_index]
    if not layer["present"] or not r.cells_w:
        return None
    banks = room_palette_banks(r)
    if banks is None:
        return None

    ramps = [detect_ramps(banks[b]) for b in range(16)]
    chars = A.decode_chars(r.bg_vram)

    W = r.cells_w * 2
    H = r.cells_h * 2
    mat = np.full((H * 8, W * 8), -1, np.int32)
    shd = np.zeros((H * 8, W * 8), np.float32)

    # Bottom first, then the top layer over it, so an opaque top-layer pixel
    # wins -- the same order extract_art's `composite` uses.
    order = [layer_index]
    if composite and layer_index == 0 and len(r.layers) > 1 \
            and r.layers[1]["present"]:
        order.append(1)

    # Per bank, palette index -> material id and shade, as lookup tables.
    mat_lut = np.array([pal * 16 + ramps[pal][0][i]
                        for pal in range(16) for i in range(16)], np.int32)
    shd_lut = np.array([ramps[pal][1][i]
                        for pal in range(16) for i in range(16)], np.float32)

    for li in order:
        layer = r.layers[li]
        sub = layer.get("subtilemap")
        if sub is None:
            continue
        idx, valid = A.subtilemap_pixels(sub, H, W, chars,
                                         layer["char_base"] // 32)
        # Colour 0 of each bank is transparent: it leaves what is beneath.
        draw = valid & (idx % 16 != 0)
        mat[draw] = mat_lut[idx[draw]]
        shd[draw] = shd_lut[idx[draw]]
    return mat, shd


def cell_shade_gradient(mat, shd, cy, cx):
    """Vertical shade gradient inside one 16x16 cell, within one material.

    A lit top over a shaded face of the SAME material is the signature of a
    raised surface -- that is the thing worth measuring. Comparing raw
    luminance instead would just rediscover that grass is brighter than stone,
    which says nothing about elevation.

    Returns (gradient, coverage): gradient > 0 means the top of the cell is
    lighter than the bottom within its dominant material.
    """
    m = mat[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
    s = shd[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
    vals, counts = np.unique(m[m >= 0], return_counts=True)
    if not len(vals):
        return 0.0, 0.0
    dom = vals[counts.argmax()]
    sel = (m == dom)
    cover = sel.sum() / 256.0
    top = s[:8][sel[:8]]
    bot = s[8:][sel[8:]]
    if len(top) < 8 or len(bot) < 8:
        return 0.0, cover
    return float(top.mean() - bot.mean()), cover


def cmd_analyse(args):
    """Decompose rooms and test what the decomposition is actually good for.

    Two questions, and the answers point in opposite directions:

      Does SHADING predict elevation?  No. Measured two ways on real rooms --
      the vertical shade gradient inside a cell (d' = 0.07) and same-material
      darkening between vertically adjacent cells (lift 0.99x) -- and neither
      finds a signal. TMC is not ALTTP: its shading is decorative texture, not
      consistent directional lighting, so depth cannot be extrapolated from it.

      Does MATERIAL predict terrain?  Yes, strongly. 97% of cells share their
      material's dominant class against a 59% base rate, and many materials are
      100% pure. That is a better key than the 88 collision values, and it is
      available per PIXEL rather than per 16px cell -- which is what makes
      sub-cell voxel detail possible.
    """
    from collections import Counter, defaultdict
    paths = sorted(Path(args.dumps).glob("room_*.tmcr")) \
        if Path(args.dumps).is_dir() else [Path(args.dumps)]
    if args.limit:
        paths = paths[:args.limit]

    tally = defaultdict(Counter)
    grad = defaultdict(list)
    done = 0
    for p in paths:
        try:
            r = RE.load_room(p)
        except Exception:
            continue
        d = decompose_room(r, args.layer)
        if d is None:
            continue
        mat, shd = d
        cls = RE.classify_room(r, args.layer)
        for cy in range(min(r.cells_h, mat.shape[0] // 16)):
            for cx in range(min(r.cells_w, mat.shape[1] // 16)):
                blk = mat[cy*16:(cy+1)*16, cx*16:(cx+1)*16]
                v, c = np.unique(blk[blk >= 0], return_counts=True)
                if not len(v):
                    continue
                tally[(r.area, int(v[c.argmax()]))][cls[cy, cx]] += 1
                g, cov = cell_shade_gradient(mat, shd, cy, cx)
                if cov >= 0.25:
                    grad[cls[cy, cx]].append(g)
        done += 1

    if not done:
        sys.exit("no room could be decomposed (needs a v3+ dump with palettes)")

    rows = []
    for k, c in tally.items():
        n = sum(c.values())
        if n < args.min_cells:
            continue
        top, cnt = c.most_common(1)[0]
        rows.append((cnt / n, n, k, top))
    rows.sort(reverse=True)

    print(f"Rooms decomposed: {done}\n")
    print("  SHADING as an elevation cue")
    g = np.array(grad.get(RE.CLASS_GROUND, []))
    w = np.array(grad.get(RE.CLASS_WALL, []))
    if len(g) > 20 and len(w) > 20:
        pooled = np.sqrt(0.5 * (g.var() + w.var()))
        dp = abs(w.mean() - g.mean()) / max(pooled, 1e-9)
        print(f"    wall {w.mean():+.4f} vs ground {g.mean():+.4f}  ->  d' = {dp:.3f}")
        print(f"    {'no usable signal' if dp < 0.3 else 'SIGNAL PRESENT -- investigate'}"
              f" (TMC shades for texture, not for light direction)")
    else:
        print("    not enough cells to judge")

    print(f"\n  MATERIAL as a terrain cue  ({len(rows)} materials over "
          f"{sum(r[1] for r in rows)} cells)")
    if rows:
        wp = np.average([r[0] for r in rows], weights=[r[1] for r in rows])
        allc = Counter()
        for c in tally.values():
            allc.update(c)
        base = max(allc.values()) / sum(allc.values())
        print(f"    weighted purity {wp*100:.1f}%   base rate {base*100:.1f}%")
        print(f"\n    {'purity':>7} {'cells':>6}  material -> class")
        for p_, n, k, top in rows[:args.show]:
            print(f"    {p_*100:6.1f}% {n:6d}  area {k[0]:3d} mat {k[1]:3d} -> {top}")
        if args.table:
            out = Path(args.table)
            with out.open("w") as f:
                f.write("# area material dominant_class purity cells\n")
                for p_, n, k, top in rows:
                    f.write(f"{k[0]} {k[1]} {top} {p_:.4f} {n}\n")
            print(f"\n    material table -> {out}")


def ramp_reference(banks, mode="bright"):
    """Pick one canonical colour per ramp: the material's albedo.

    Shading is position within a ramp; albedo is the ramp itself. Collapsing a
    ramp to a single colour therefore removes the hand-drawn shading while
    keeping the material -- which is precisely what is needed to relight the
    art with a real light source instead of the one the artists implied.
    """
    ref = {}
    for b in range(16):
        ramp, shade = detect_ramps(banks[b])
        for rid in set(ramp):
            if rid < 0:
                continue
            idxs = [i for i in range(16) if ramp[i] == rid]
            if mode == "bright":
                pick = max(idxs, key=lambda i: hls_of(banks[b][i])[1])
            elif mode == "dark":
                pick = min(idxs, key=lambda i: hls_of(banks[b][i])[1])
            else:                                   # mid
                ls = sorted(idxs, key=lambda i: hls_of(banks[b][i])[1])
                pick = ls[len(ls) // 2]
            ref[b * 16 + rid] = banks[b][pick]
    return ref


def cmd_albedo(args):
    """Split a room's art into ALBEDO x SHADE, and write both.

    The art as drawn is one image with material and hand-shading fused. This
    separates them:

        albedo   each pixel replaced by its ramp's reference colour -- what the
                 surface IS, with the artist's shading removed
        shade    each pixel's position within its ramp, as greyscale -- what
                 the artist DID to it

    Multiplying them back together approximately reconstructs the original, and
    the reconstruction error is reported so the claim is checkable rather than
    asserted.

    This is what makes engine lighting possible. Lighting albedo gives a
    consistently lit world; lighting the art as drawn would multiply the
    engine's light by a second, inconsistent one that points nowhere in
    particular (measured: direction concentration R = 0.019).
    """
    import extract_art as A
    from PIL import Image

    r = RE.load_room(Path(args.path))
    banks = room_palette_banks(r)
    if banks is None:
        sys.exit("room has no palette")
    d = decompose_room(r, args.layer)
    if d is None:
        sys.exit("could not decompose this room")
    mat, shd = d
    ref = ramp_reference(banks, args.reference)

    art = RE.room_art_rgb(r, args.layer)
    if art is None:
        sys.exit("no art")
    h, w = mat.shape
    art = art[:h, :w].astype(np.float32)

    alb = np.zeros((h, w, 3), np.float32)
    for m in np.unique(mat):
        if m < 0:
            continue
        c = ref.get(int(m))
        if c is None:
            continue
        alb[mat == m] = c
    lit = np.clip(shd, 0, 1)[:, :, None]

    # Reconstruct: albedo scaled by shade. The ramps are not linear in
    # luminance, so this is an approximation -- report how good it is.
    gain = args.floor + (1.0 - args.floor) * lit
    recon = np.clip(alb * gain, 0, 255)
    valid = mat >= 0
    err = np.abs(recon[valid] - art[valid]).mean()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"room_{r.area:02d}_{r.room:02d}"
    Image.fromarray(art.astype(np.uint8)).save(out / f"{stem}_original.png")
    Image.fromarray(alb.astype(np.uint8)).save(out / f"{stem}_albedo.png")
    Image.fromarray((lit[:, :, 0] * 255).astype(np.uint8)).save(out / f"{stem}_shade.png")
    Image.fromarray(recon.astype(np.uint8)).save(out / f"{stem}_recon.png")

    print(f"{r}  ->  {out}")
    print(f"  reference colour per ramp: {args.reference}")
    print(f"  reconstruction error |albedo*shade - original|: {err:.1f} / 255")
    print(f"  materials: {len(set(mat[valid].ravel().tolist()))}")
    print("\n  albedo is what to light. The art as drawn already contains a "
          "light that points nowhere (R=0.019), so lighting IT would stack two "
          "inconsistent lights on top of each other.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ramps", help="show the ramps in a room's palette")
    a.add_argument("path")
    a.add_argument("--banks", type=int, nargs="*", default=[0, 1, 2])
    a.set_defaults(func=cmd_ramps)

    b = sub.add_parser("analyse", help="test shading and material as cues")
    b.add_argument("dumps")
    b.add_argument("--layer", type=int, default=0)
    b.add_argument("--limit", type=int, default=12)
    b.add_argument("--min-cells", type=int, default=15)
    b.add_argument("--show", type=int, default=15)
    b.add_argument("--table", default="")
    b.set_defaults(func=cmd_analyse)

    c = sub.add_parser("albedo", help="split art into albedo x shade")
    c.add_argument("path")
    c.add_argument("--layer", type=int, default=0)
    c.add_argument("--out", default="albedo")
    c.add_argument("--reference", choices=("bright", "mid", "dark"), default="bright")
    c.add_argument("--floor", type=float, default=0.45,
                   help="shade value that maps to full albedo brightness")
    c.set_defaults(func=cmd_albedo)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
