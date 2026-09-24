#!/usr/bin/env python3
"""
hull_carve.py — Phase 0 visual-hull carver and hypothesis verifier.

Consumes the PNG/JSON output of port/vr/vr_spritedump.c and does two jobs:

  verify   Answer the two questions Phase 0 exists to answer, before anyone
           writes a line of renderer code:
             H1  Is West just mirrored East?
             H2  Do S/N/E frame indices describe the same pose?

  carve    Build voxel hulls from the three orthogonal views and write .vox.

The entire project's character pipeline rests on H1 and H2. Neither is
guaranteed by the data — TMC's art is consistent enough that both should hold
for most cycles, but they will fail somewhere, and it is much cheaper to find
out here than in Phase 2.

Usage
-----
  python3 hull_carve.py verify dumps/ --sprite 1
  python3 hull_carve.py carve  dumps/ --sprite 1 --out vox/ --depth-scale 1.0

Dependencies: pillow, numpy.

Convention
----------
World axes:  +X east, +Y up, +Z south (toward the viewer in the front view).
This matches the remap PotatoVoxel's MagicaVoxel.lua applies, so .vox files
written here drop straight into a MagicaVoxel-oriented pipeline.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# Tolerances for the verifier. Tuned to be forgiving of a few stray pixels
# (palette edge cases, single-pixel highlights) while still catching a genuine
# pose mismatch. Tighten once you know what normal looks like for your dumps.
MIRROR_PIXEL_TOL = 0.02   # fraction of differing pixels allowed for H1
AREA_RATIO_TOL = 0.35     # silhouette area ratio S vs E allowed for H2
HEIGHT_TOL = 2            # bbox height difference in px allowed for H2


# ----------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------

def load_manifest(dump_dir: Path, sprite: int, tag: str):
    path = dump_dir / f"sprite_{sprite}_{tag}.json"
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def load_frame(dump_dir: Path, entry):
    """Return (rgba HxWx4 uint8, bbox) or (None, None) for an empty frame."""
    bbox = entry["bbox"]
    if bbox[0] < 0:
        return None, None
    img = Image.open(dump_dir / entry["file"]).convert("RGBA")
    return np.asarray(img), bbox


def silhouette(rgba):
    return rgba[:, :, 3] > 0


def crop_to_bbox(arr, bbox):
    x0, y0, x1, y1 = bbox
    return arr[y0:y1 + 1, x0:x1 + 1]


def register_top(arr, bbox):
    """Crop to bbox. The bbox TOP is the registration datum across views.

    Do not register on the canvas or the nominal 16x16 sprite box: multi-piece
    frames overflow both, and a shifted datum shears the hull.
    """
    return crop_to_bbox(arr, bbox)


def union_bbox(bboxes):
    """Smallest bbox containing all of them, in canvas coordinates."""
    bs = [b for b in bboxes if b is not None and b[0] >= 0]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs)]


def register_common(arr, ubb):
    """Crop to a bbox SHARED by every view.

    Cropping each view to its own bbox looks reasonable and is wrong: the views
    have different bbox sizes (a 24px-tall north against a 32px-tall south is
    routine), so per-view cropping slides them relative to each other and the
    carve shears. The rasterizer already places every view on one 128x128
    canvas about a common anchor, so that shared frame is the registration --
    crop all views identically and the alignment survives.
    """
    x0, y0, x1, y1 = ubb
    return arr[y0:y1 + 1, x0:x1 + 1]


# ----------------------------------------------------------------------------
# H1 — is West mirrored East?
# ----------------------------------------------------------------------------

def verify_mirror(dump_dir: Path, sprite: int):
    east = load_manifest(dump_dir, sprite, "east")
    west = load_manifest(dump_dir, sprite, "west")
    if not east or not west:
        print("  H1: skipped (need both 'east' and 'west' dumps)")
        return None

    e_by = {e["frame"]: e for e in east["frames"]}
    w_by = {e["frame"]: e for e in west["frames"]}
    shared = sorted(set(e_by) & set(w_by))
    if not shared:
        print("  H1: skipped (no overlapping frame indices)")
        return None

    mismatches, checked = [], 0
    for fi in shared:
        e_rgba, e_bb = load_frame(dump_dir, e_by[fi])
        w_rgba, w_bb = load_frame(dump_dir, w_by[fi])
        if e_rgba is None or w_rgba is None:
            continue

        e_sil = register_top(silhouette(e_rgba), e_bb)
        w_sil = register_top(silhouette(w_rgba), w_bb)
        if e_sil.shape != w_sil.shape:
            mismatches.append((fi, "shape", e_sil.shape, w_sil.shape))
            checked += 1
            continue

        diff = np.logical_xor(e_sil, np.fliplr(w_sil)).sum()
        frac = diff / max(e_sil.size, 1)
        if frac > MIRROR_PIXEL_TOL:
            mismatches.append((fi, "pixels", round(float(frac), 4), None))
        checked += 1

    ok = len(mismatches) == 0
    print(f"  H1 (West == mirrored East): {'HOLDS' if ok else 'FAILS'} "
          f"({checked - len(mismatches)}/{checked} frames agree)")
    for fi, kind, a, b in mismatches[:10]:
        print(f"       frame {fi:3d}  {kind}  {a}{'' if b is None else f' vs {b}'}")
    if len(mismatches) > 10:
        print(f"       ... and {len(mismatches) - 10} more")

    if not ok:
        print("       -> West is NOT free. Dump all four directions and carve "
              "from S/N/E/W rather than S/N/E.")
    return ok


# ----------------------------------------------------------------------------
# H2 — do frame indices correspond across directions?
# ----------------------------------------------------------------------------

def verify_correspondence(dump_dir: Path, sprite: int):
    mans = {t: load_manifest(dump_dir, sprite, t) for t in ("south", "north", "east")}
    missing = [t for t, m in mans.items() if not m]
    if missing:
        print(f"  H2: skipped (missing dumps: {', '.join(missing)})")
        return None

    by = {t: {e["frame"]: e for e in m["frames"]} for t, m in mans.items()}
    shared = sorted(set(by["south"]) & set(by["north"]) & set(by["east"]))
    if not shared:
        print("  H2: skipped (no overlapping frame indices)")
        return None

    bad, checked = [], 0
    for fi in shared:
        sils, bbs, empty = {}, {}, False
        for t in ("south", "north", "east"):
            rgba, bb = load_frame(dump_dir, by[t][fi])
            if rgba is None:
                empty = True
                break
            sils[t] = register_top(silhouette(rgba), bb)
            bbs[t] = bb
        if empty:
            continue
        checked += 1

        heights = {t: bbs[t][3] - bbs[t][1] + 1 for t in bbs}
        hs, he = heights["south"], heights["east"]
        if abs(hs - he) > HEIGHT_TOL:
            bad.append((fi, f"height S={hs} E={he}"))
            continue

        a_s = int(sils["south"].sum())
        a_e = int(sils["east"].sum())
        if a_s == 0 or a_e == 0:
            bad.append((fi, "empty silhouette"))
            continue
        ratio = abs(a_s - a_e) / max(a_s, a_e)
        if ratio > AREA_RATIO_TOL:
            bad.append((fi, f"area S={a_s} E={a_e} ratio={ratio:.2f}"))

    ok = len(bad) == 0
    print(f"  H2 (S/N/E frame indices describe the same pose): "
          f"{'HOLDS' if ok else 'PARTIAL'} ({checked - len(bad)}/{checked} frames agree)")
    for fi, why in bad[:10]:
        print(f"       frame {fi:3d}  {why}")
    if len(bad) > 10:
        print(f"       ... and {len(bad) - 10} more")

    if bad:
        print("       -> These frames fall back to single-view extrusion. "
              "A slightly flat character on a handful of frames is acceptable; "
              "a sheared hull is not.")
    return {"checked": checked, "failed": [fi for fi, _ in bad]}


# ----------------------------------------------------------------------------
# Hull carving
# ----------------------------------------------------------------------------

def chamfer_distance(sil):
    """Approximate Euclidean distance to the nearest background pixel.

    Two-pass 3-4 chamfer, scaled back to pixel units. Good to a few percent,
    needs no scipy, and runs in microseconds on a 32x32 sprite.
    """
    INF = 1 << 20
    h, w = sil.shape
    d = np.where(sil, INF, 0).astype(np.int32)

    for y in range(h):
        for x in range(w):
            if d[y, x] == 0:
                continue
            best = d[y, x]
            if y > 0:
                if x > 0:     best = min(best, d[y - 1, x - 1] + 4)
                best = min(best, d[y - 1, x] + 3)
                if x + 1 < w: best = min(best, d[y - 1, x + 1] + 4)
            if x > 0:         best = min(best, d[y, x - 1] + 3)
            d[y, x] = best

    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            if d[y, x] == 0:
                continue
            best = d[y, x]
            if y + 1 < h:
                if x + 1 < w: best = min(best, d[y + 1, x + 1] + 4)
                best = min(best, d[y + 1, x] + 3)
                if x > 0:     best = min(best, d[y + 1, x - 1] + 4)
            if x + 1 < w:     best = min(best, d[y, x + 1] + 3)
            d[y, x] = best

    return d.astype(np.float32) / 3.0


def carve_hull(s_rgba, s_bb, n_rgba, n_bb, e_rgba, e_bb,
               depth_scale=1.0, envelope=True):
    """Build a voxel hull from orthogonal silhouettes.

    Two modes:

    envelope=False — the textbook visual hull, a plain boolean product:

        occupied(x,y,z) <=> (S(x,y) or N(W-1-x,y)) and E(z,y)

    envelope=True (default) — the same intersection, but the depth allowed at
    each x is modulated PER CONNECTED SPAN in that row rather than applied
    uniformly across the row. This is the fix for phantom volume.

    The failure the plain product produces: Link holds his sword east. The front
    row contains two spans, a wide torso and a narrow blade. The side row shows
    the torso's full depth. The product gives the blade the torso's depth, so
    the sword becomes a slab and the air between arm and blade fills solid.

    Modulating per span fixes it without any authoring, because the blade is its
    own span: depth is scaled by that span's width relative to the widest span
    in the row, then tapered elliptically across the span. A narrow protrusion
    gets a narrow cross-section, which is what it physically is. The torso, being
    the widest span, keeps its full measured depth.

    Returns {(x, y, z): (r, g, b)} in world axes (+X east, +Y up, +Z south).
    """
    # Registration is per-axis, because the three views do not share all axes.
    #   Y  is common to all three  -> one vertical crop for everything.
    #   X  is the front axis, shared by SOUTH and NORTH only.
    #   The EAST view's horizontal axis is DEPTH, an axis of its own.
    # Cropping east to an x-range unioned with the front views pads it with
    # empty depth columns and inflates the hull; cropping each view to its own
    # bbox instead slides the views apart vertically and shears it. Both are
    # wrong in different directions, so split the axes.
    y0 = min(b[1] for b in (s_bb, n_bb, e_bb) if b is not None and b[0] >= 0)
    y1 = max(b[3] for b in (s_bb, n_bb, e_bb) if b is not None and b[0] >= 0)

    fx0 = min(b[0] for b in (s_bb, n_bb) if b is not None and b[0] >= 0)
    fx1 = max(b[2] for b in (s_bb, n_bb) if b is not None and b[0] >= 0)

    def crop(arr, x0, x1):
        return arr[y0:y1 + 1, x0:x1 + 1]

    s_sil = crop(silhouette(s_rgba), fx0, fx1)
    s_col = crop(s_rgba, fx0, fx1)
    e_sil = crop(silhouette(e_rgba), e_bb[0], e_bb[2])
    e_col = crop(e_rgba, e_bb[0], e_bb[2])

    if n_rgba is not None:
        n_sil = crop(silhouette(n_rgba), fx0, fx1)
        n_col = crop(n_rgba, fx0, fx1)
    else:
        n_sil = n_col = None

    h = min(s_sil.shape[0], e_sil.shape[0])
    w = s_sil.shape[1]
    d = max(1, int(round(e_sil.shape[1] * depth_scale)))

    # Front silhouette, widened by the mirrored back view where available.
    front = s_sil[:h, :].copy()
    if n_sil is not None and n_sil.shape[1] == w:
        front |= np.fliplr(n_sil[:h, :])

    side = e_sil[:h, :]

    # Local thickness of the front silhouette, in pixels. A 2px-wide blade has
    # distance <= 1 everywhere along it; a 6px torso reaches 3 at its spine.
    dist = chamfer_distance(front) if envelope else None

    vox = {}
    for y_img in range(h):
        row_f = front[y_img]
        row_s = side[y_img]
        if not row_f.any() or not row_s.any():
            continue

        zs_src = np.flatnonzero(row_s)
        if depth_scale == 1.0:
            zs = zs_src
        else:
            zs = np.unique(np.clip((zs_src * depth_scale).astype(int), 0, d - 1))
        z_min, z_max = int(zs.min()), int(zs.max())
        z_mid = 0.5 * (z_min + z_max)
        z_half = max(0.5, 0.5 * (z_max - z_min + 1))

        # Image Y grows downward; world Y grows up.
        y_world = (h - 1) - y_img

        for x_i in np.flatnonzero(row_f):
            x_i = int(x_i)
            if envelope:
                # Depth follows local thickness, capped by what the side view
                # actually measured. The cap matters: the distance transform
                # knows nothing about depth, only about width, so the side view
                # remains the authority on how deep the body is.
                local_half = min(z_half, float(dist[y_img, x_i]))
                lo = int(round(z_mid - local_half))
                hi = int(round(z_mid + local_half))
                z_range = [int(z) for z in zs if lo <= z <= hi]
                if not z_range:
                    # Never empty a column: the front silhouette must stay
                    # intact when the model is viewed head-on.
                    z_range = [int(round(z_mid))]
            else:
                z_range = [int(z) for z in zs]

            zr_min, zr_max = min(z_range), max(z_range)
            for z_i in z_range:
                # Colour priority: front face -> S, back face -> N, else E.
                if z_i == zr_max and s_sil[y_img, x_i]:
                    c = s_col[y_img, x_i][:3]
                elif z_i == zr_min and n_sil is not None \
                        and n_sil.shape[1] == w and n_sil[y_img, w - 1 - x_i]:
                    c = n_col[y_img, w - 1 - x_i][:3]
                else:
                    c = e_col[y_img, min(z_i, e_col.shape[1] - 1)][:3]
                vox[(x_i, y_world, z_i)] = (int(c[0]), int(c[1]), int(c[2]))

    return vox


def largest_component(vox):
    """Discard voxel islands not connected to the largest body.

    The cheap half of the phantom-volume mitigation: a visual hull is always an
    over-estimate, and a sword held east produces solid fill between blade and
    torso. Connectivity removes detached artifacts; it does NOT remove attached
    ones. Those need authored carve masks, the direct analogue of PotatoVoxel's
    data/voxel_heights.lua pins.
    """
    if not vox:
        return vox
    seen, comps = set(), []
    for start in vox:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            p = stack.pop()
            comp.append(p)
            x, y, z = p
            for n in ((x+1,y,z), (x-1,y,z), (x,y+1,z),
                      (x,y-1,z), (x,y,z+1), (x,y,z-1)):
                if n in vox and n not in seen:
                    seen.add(n)
                    stack.append(n)
        comps.append(comp)
    best = max(comps, key=len)
    return {p: vox[p] for p in best}


# ----------------------------------------------------------------------------
# .vox output (MagicaVoxel 150)
# ----------------------------------------------------------------------------

def write_vox(path: Path, vox):
    """Write VOX 150. MagicaVoxel axes are +X right, +Y depth, +Z up, so world
    (x, y_up, z_south) maps to file (x, z, y)."""
    if not vox:
        return False

    xs = [p[0] for p in vox]; ys = [p[1] for p in vox]; zs = [p[2] for p in vox]
    ox, oy, oz = min(xs), min(ys), min(zs)
    sx, sy, sz = max(xs) - ox + 1, max(ys) - oy + 1, max(zs) - oz + 1
    if max(sx, sy, sz) > 256:
        print(f"  warning: {path.name} exceeds 256 in some axis; VOX 150 caps there")

    # Quantize to a 255-entry palette (index 0 is empty in MagicaVoxel).
    palette, lookup = [], {}
    for c in vox.values():
        if c not in lookup and len(palette) < 255:
            lookup[c] = len(palette) + 1
            palette.append(c)

    def nearest(c):
        if c in lookup:
            return lookup[c]
        best, bi = 1 << 30, 1
        for i, p in enumerate(palette):
            d = sum((a - b) ** 2 for a, b in zip(c, p))
            if d < best:
                best, bi = d, i + 1
        return bi

    voxels = bytearray()
    for (x, y, z), c in vox.items():
        voxels += struct.pack("<BBBB",
                              (x - ox) & 0xFF, (z - oz) & 0xFF,
                              (y - oy) & 0xFF, nearest(c))

    def chunk(cid, content, children=b""):
        return cid + struct.pack("<II", len(content), len(children)) + content + children

    size = chunk(b"SIZE", struct.pack("<III", sx, sz, sy))
    xyzi = chunk(b"XYZI", struct.pack("<I", len(vox)) + bytes(voxels))

    pal = bytearray()
    for i in range(256):
        if i < len(palette):
            r, g, b = palette[i]
        else:
            r = g = b = 0
        pal += struct.pack("<BBBB", r, g, b, 255)
    rgba = chunk(b"RGBA", bytes(pal))

    children = size + xyzi + rgba
    main = chunk(b"MAIN", b"", children)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"VOX " + struct.pack("<I", 150) + main)
    return True


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def resolve_sprite(dump_dir: Path, requested):
    """Find which sprite index is actually on disk.

    The harvest names its files after the live entity's spriteIndex, which is
    not a number anyone should have to know in advance (Link is 4, not 1). If
    --sprite is omitted, pick the index with the most manifests; if it is given
    but absent, say what IS there instead of silently reporting 'skipped'.
    """
    found = {}
    for p in sorted(dump_dir.glob("sprite_*_*.json")):
        parts = p.stem.split("_")
        if len(parts) >= 3 and parts[1].isdigit():
            found.setdefault(int(parts[1]), []).append(parts[2])
    if not found:
        sys.exit(f"no sprite_*_*.json manifests in {dump_dir} — run the harvest first")
    if requested is None:
        best = max(found, key=lambda k: len(found[k]))
        if len(found) > 1:
            print(f"note: dumps present for sprites {sorted(found)}; using {best}")
        return best
    if requested not in found:
        avail = ", ".join(f"{k} ({len(found[k])} dumps)" for k in sorted(found))
        sys.exit(f"no dumps for sprite {requested} in {dump_dir}; available: {avail}")
    return requested


def load_anim_manifests(dump_dir: Path, sprite=None):
    """Load sprite_<n>_anim<NNN>.json manifests, keyed by animation index."""
    out = {}
    for p in sorted(dump_dir.glob("sprite_*_anim*.json")):
        with p.open() as f:
            m = json.load(f)
        if sprite is not None and m.get("sprite") != sprite:
            continue
        out[m["anim"]] = m
    return out


def cmd_anims(args):
    """Phase 0 on the animation-walk dumps.

    Frame indices are per-direction blocks, so they never corresponded across
    directions and the original H2 was the wrong question. The unit that does
    correspond is the keyframe ordinal k, and that is what this tests.

    Slot layout within each group of four, from the probe: 0=north, 1=east,
    2=south, 3=west (west reusing east's frames with flipX).
    """
    d = Path(args.dump_dir)
    mans = load_anim_manifests(d, args.sprite)
    if not mans:
        sys.exit(f"no sprite_*_anim*.json manifests in {d} — run the animation harvest first")

    SLOT = {0: "north", 1: "east", 2: "south", 3: "west"}
    groups = {}
    for a, m in mans.items():
        groups.setdefault(a >> 2, {})[a & 3] = m

    sil_cache = {}

    def sil_of(m, k):
        key = (m["anim"], k)
        if key in sil_cache:
            return sil_cache[key]
        ent = next((e for e in m["frames"] if e["k"] == k), None)
        val = (None, None)
        if ent:
            rgba, bb = load_frame(d, ent)
            if rgba is not None:
                val = (silhouette(rgba), bb)
        sil_cache[key] = val
        return val

    print(f"Phase 0 — animation-walk check ({len(mans)} animations, "
          f"{len(groups)} groups)\n")
    print("  group  north  east  south  west   H1 west==mirror(east)   "
          "H2 keyframe poses")
    print("  " + "-" * 74)

    h1_hold = h1_fail = h1_same = h2_hold = h2_part = 0

    for g in sorted(groups):
        slots = groups[g]
        counts = []
        for s in range(4):
            counts.append(str(len(slots[s]["frames"])) if s in slots else "-")

        # ---- H1: is west the horizontal mirror of east? ----
        # The dump cannot answer this by pixels. West has its own animation
        # index but reuses east's frame indices and east's VRAM, and the
        # rasterizer does not apply the entity-level flip, so the two PNGs come
        # out identical by construction. Identical is therefore EVIDENCE, not a
        # pass: it shows west is built from east's art, which leaves a
        # draw-time flip as the only thing that can distinguish them. Report
        # what was measured and do not dress it up as a verdict.
        h1 = "no data"
        if 1 in slots and 3 in slots:
            e_m, w_m = slots[1], slots[3]
            ne, nw = len(e_m["frames"]), len(w_m["frames"])
            e_fr = [f["frame"] for f in e_m["frames"]]
            w_fr = [f["frame"] for f in w_m["frames"]]
            ks = sorted({f["k"] for f in e_m["frames"]} & {f["k"] for f in w_m["frames"]})
            ident = mirror = checked = 0
            for k in ks:
                es, _ = sil_of(e_m, k)
                ws, _ = sil_of(w_m, k)
                if es is None or ws is None or es.shape != ws.shape:
                    continue
                checked += 1
                if np.array_equal(es, ws):
                    ident += 1
                elif np.array_equal(es, np.fliplr(ws)):
                    mirror += 1
            if ne != nw or e_fr != w_fr:
                h1 = f"DIFFERS len {ne}/{nw}"
                h1_fail += 1
            elif checked and mirror == checked:
                h1 = f"MIRRORED {mirror}/{checked}"
                h1_hold += 1
            elif checked and ident == checked:
                h1 = f"same art {ident}/{checked}*"
                h1_same += 1
            elif checked:
                h1 = f"mixed i={ident} m={mirror}/{checked}"
                h1_fail += 1

        # ---- H2: does keyframe k mean the same pose in each direction? ----
        # Compare the OFFSET of each frame from its own direction's base frame.
        # Each direction is a separate animation over its own contiguous block
        # of frame indices, so the absolute indices never match; the offset
        # sequence is what encodes the pose order, and it compares exactly as
        # integers. The earlier silhouette-height test measured OBJ piece
        # extents (quantised to 24 or 32 px), not anatomy, and reported false
        # failures on data that corresponds perfectly.
        h2 = "no data"
        present = [s_ for s_ in (0, 1, 2) if s_ in slots]
        if len(present) >= 2:
            seqs = {}
            for s_ in present:
                fr = [f["frame"] for f in sorted(slots[s_]["frames"], key=lambda f: f["k"])]
                seqs[s_] = [f - fr[0] for f in fr] if fr else []
            ref = seqs[present[0]]
            n = len(ref)
            if any(len(seqs[s_]) != n for s_ in present):
                lens = {s_: len(seqs[s_]) for s_ in present}
                h2 = f"FAILS lengths {list(lens.values())}"
                h2_part += 1
            elif n == 0:
                h2 = "no frames"
            else:
                agree = sum(1 for i in range(n)
                            if all(seqs[s_][i] == ref[i] for s_ in present))
                ok = agree == n
                h2 = f"{'HOLDS' if ok else 'PARTIAL'} {agree}/{n}"
                h2_hold += ok
                h2_part += (not ok)

        print(f"  {g:5d}  {counts[0]:>5s}  {counts[1]:>4s}  {counts[2]:>5s}  "
              f"{counts[3]:>4s}   {h1:<22s} {h2}")

    print(f"\n  H1: {h1_hold} mirrored, {h1_same} same-art (*), {h1_fail} differ")
    print(f"  H2: {h2_hold} groups hold, {h2_part} partial/fail")
    if h1_same and not h1_fail:
        print("\n  * 'same art' is not a pass. West reuses east's frames and "
              "east's VRAM, and the dump does not carry the draw-time flip, so "
              "the two PNGs are identical by construction. It rules out west "
              "having separate art; confirming the flip needs the engine side.")
    if h2_hold and not h2_part:
        print("  -> Keyframe ordinals correspond exactly across directions, so "
              "the three views of keyframe k can be carved together.")


def cmd_diag(args):
    """Cross-compare every pair of direction dumps: identical / mirrored / distinct.

    Exists because a total H1 failure (0/N) is ambiguous. It can mean the art
    really is asymmetric, or it can mean two dumps are the same pixels and the
    mirror test is measuring the sprite's asymmetry against itself. Those need
    opposite fixes, so measure rather than infer.
    """
    d = Path(args.dump_dir)
    sprite = resolve_sprite(d, args.sprite)
    tags = ("south", "north", "east", "west")
    mans = {t: load_manifest(d, sprite, t) for t in tags}
    have = [t for t in tags if mans[t]]
    print(f"Sprite {sprite} — direction dump cross-check ({', '.join(have)})\n")

    sil = {}
    for t in have:
        for e in mans[t]["frames"]:
            rgba, bb = load_frame(d, e)
            if rgba is not None:
                sil[(t, e["frame"])] = silhouette(rgba)

    for i, a in enumerate(have):
        for b in have[i + 1:]:
            frames = sorted({f for (t, f) in sil if t == a} &
                            {f for (t, f) in sil if t == b})
            if not frames:
                print(f"  {a:6s} vs {b:6s}: no shared frames")
                continue
            same = mirror = 0
            for f in frames:
                pa, pb = sil[(a, f)], sil[(b, f)]
                # Compare on the full canvas: identical dumps are identical
                # everywhere, so no bbox registration is needed or wanted here.
                if pa.shape == pb.shape:
                    if np.array_equal(pa, pb):
                        same += 1
                    if np.array_equal(pa, np.fliplr(pb)):
                        mirror += 1
            n = len(frames)
            verdict = "distinct"
            if same == n:
                verdict = "IDENTICAL — same pixels, direction had no effect"
            elif mirror == n:
                verdict = "MIRRORED — one is the horizontal flip of the other"
            elif same or mirror:
                verdict = f"mixed ({same} identical, {mirror} mirrored)"
            print(f"  {a:6s} vs {b:6s}: {verdict}  [{n} frames]")

    print("\nReading this: 'east vs west IDENTICAL' means the dumper is not "
          "applying the entity-level horizontal flip, so H1 is untestable as "
          "dumped — a bug in the harvest, not a fact about the art.")


def cmd_verify(args):
    d = Path(args.dump_dir)
    args.sprite = resolve_sprite(d, args.sprite)
    print(f"Sprite {args.sprite} — Phase 0 hypothesis check")
    verify_mirror(d, args.sprite)
    verify_correspondence(d, args.sprite)
    print("\nExit criterion: if H1 and H2 both hold for Link's walk and idle "
          "cycles, the hull pipeline is viable and Phase 1 can start.")


def cmd_carve(args):
    d = Path(args.dump_dir)
    out = Path(args.out)
    args.sprite = resolve_sprite(d, args.sprite)
    mans = {t: load_manifest(d, args.sprite, t) for t in ("south", "north", "east")}
    if not mans["south"] or not mans["east"]:
        sys.exit("carve needs at least 'south' and 'east' dumps")

    by = {t: {e["frame"]: e for e in m["frames"]} for t, m in mans.items() if m}
    frames = sorted(set(by["south"]) & set(by["east"]))

    built = skipped = 0
    for fi in frames:
        s_rgba, s_bb = load_frame(d, by["south"][fi])
        e_rgba, e_bb = load_frame(d, by["east"][fi])
        if s_rgba is None or e_rgba is None:
            skipped += 1
            continue
        if "north" in by and fi in by["north"]:
            n_rgba, n_bb = load_frame(d, by["north"][fi])
        else:
            n_rgba, n_bb = None, None

        vox = carve_hull(s_rgba, s_bb, n_rgba, n_bb, e_rgba, e_bb,
                         depth_scale=args.depth_scale,
                         envelope=not args.no_envelope)
        if args.connected:
            vox = largest_component(vox)

        if write_vox(out / f"sprite_{args.sprite}_{fi:03d}.vox", vox):
            built += 1
        else:
            skipped += 1

    print(f"carved {built} hulls, skipped {skipped} -> {out}")
    print("Eyeball these in MagicaVoxel before trusting the pipeline. Look for "
          "solid fill between limbs and torso — that is the phantom-volume "
          "failure, and it is what carve masks exist to fix.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="check H1 and H2")
    v.add_argument("dump_dir")
    v.add_argument("--sprite", type=int, default=None,
                   help="sprite index; auto-detected from the dump dir if omitted")
    v.set_defaults(func=cmd_verify)

    a = sub.add_parser("anims", help="Phase 0 on animation-walk dumps")
    a.add_argument("dump_dir")
    a.add_argument("--sprite", type=int, default=None)
    a.set_defaults(func=cmd_anims)

    g = sub.add_parser("diag", help="cross-compare direction dumps")
    g.add_argument("dump_dir")
    g.add_argument("--sprite", type=int, default=None,
                   help="sprite index; auto-detected from the dump dir if omitted")
    g.set_defaults(func=cmd_diag)

    c = sub.add_parser("carve", help="build voxel hulls")
    c.add_argument("dump_dir")
    c.add_argument("--sprite", type=int, default=None,
                   help="sprite index; auto-detected from the dump dir if omitted")
    c.add_argument("--out", default="vox")
    c.add_argument("--depth-scale", type=float, default=1.0,
                   help="scale the side view's depth; <1 flattens the hull")
    c.add_argument("--no-envelope", action="store_true",
                   help="plain boolean visual hull; disables distance-transform "
                        "depth shaping (useful for A/B comparison)")
    c.add_argument("--connected", action="store_true",
                   help="keep only the largest connected component")
    c.set_defaults(func=cmd_carve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
