#!/usr/bin/env python3
"""
room_explore.py — offline room analysis and classifier iteration.

Consumes the .tmcr files written by port/vr/vr_world.c and gives you the loop
you need before writing any renderer code:

    survey    Histogram collision values and tile types across many rooms.
    preview   Render a top-down classification PNG for one room.
    mesh      Classify, extrude, greedy-mesh and export OBJ.

Why this exists
---------------
Only 4 of TMC's 88 collision values carry annotations, and those are sound-effect
hints (FX_WATER_SPLASH, FX_FALL_DOWN), not semantics. About 20 of ~1700 tile
types are named. So the terrain classifier cannot be read off an enum — it has
to be derived by looking at real rooms and correlating values against what you
know is there. `survey` and `preview` are that correlation loop.

The classifier below is a STARTING POINT, deliberately shaped like PotatoVoxel's
fallback chain (pin -> actTile -> collision -> tileType -> procedural). Edit
CLASS_BY_COLLISION as you learn what the values mean. That table is your
voxel_heights.lua.

Usage
-----
  python3 room_explore.py survey  dumps/
  python3 room_explore.py preview dumps/room_00_01.tmcr --out preview.png
  python3 room_explore.py mesh    dumps/room_00_01.tmcr --out room.obj

Dependencies: pillow, numpy.
"""

import argparse
import struct
import time
import sys
from collections import Counter
from pathlib import Path

import numpy as np

MAP_DIM = 64
MAP_CELLS = MAP_DIM * MAP_DIM
TILESET = 2048
LAYERS = 2


# ----------------------------------------------------------------------------
# progress reporting
# ----------------------------------------------------------------------------

class Progress:
    """Progress for long runs, on a terminal AND through a pipe.

    A carriage-return bar is invisible the moment output is piped into `tail`,
    which is how these commands are usually run. So: redraw in place on a TTY,
    and emit a plain line every `step` percent otherwise. Either way it goes to
    STDERR, leaving stdout clean for the reports the tools already print.
    """

    def __init__(self, total, label="working", step=5, width=32, stream=None):
        self.total = max(1, int(total))
        self.label = label
        self.step = step
        self.width = width
        self.stream = stream or sys.stderr
        self.tty = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.n = 0
        self.t0 = time.time()
        self.last_pct = -1
        self.note = ""

    @staticmethod
    def _hms(sec):
        sec = int(max(0, sec))
        if sec < 60:
            return f"{sec}s"
        if sec < 3600:
            return f"{sec // 60}m{sec % 60:02d}s"
        return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"

    def update(self, n=1, note=""):
        self.n += n
        if note:
            self.note = note
        pct = int(100 * self.n / self.total)
        el = time.time() - self.t0
        eta = (el / self.n) * (self.total - self.n) if self.n else 0
        if self.tty:
            fill = int(self.width * self.n / self.total)
            bar = "#" * fill + "-" * (self.width - fill)
            self.stream.write(
                f"\r  {self.label} [{bar}] {pct:3d}%  {self.n}/{self.total}  "
                f"elapsed {self._hms(el)}  eta {self._hms(eta)}  {self.note[:28]:<28}")
            self.stream.flush()
        elif pct >= self.last_pct + self.step or self.n == self.total:
            self.last_pct = pct
            self.stream.write(
                f"  {self.label} {pct:3d}%  {self.n}/{self.total}  "
                f"elapsed {self._hms(el)}  eta {self._hms(eta)}"
                f"{('  ' + self.note) if self.note else ''}\n")
            self.stream.flush()

    def done(self, msg=""):
        el = time.time() - self.t0
        if self.tty:
            self.stream.write("\r" + " " * (self.width + 96) + "\r")
        self.stream.write(f"  {self.label}: {self.n}/{self.total} in "
                          f"{self._hms(el)}{('  ' + msg) if msg else ''}\n")
        self.stream.flush()


# ----------------------------------------------------------------------------
# .tmcr parsing
# ----------------------------------------------------------------------------

class Room:
    __slots__ = ("area", "room", "origin_x", "origin_y", "width", "height",
                 "cells_w", "cells_h", "layers", "version",
                 "entities", "bg_palette", "bg_vram",
                 "src_palette", "fade_active")

    def __repr__(self):
        return (f"<Room area={self.area} room={self.room} "
                f"{self.cells_w}x{self.cells_h} cells>")


V1_BYTES = 40984


def load_room(path: Path) -> Room:
    d = path.read_bytes()
    if d[:4] != b"TMCR":
        raise ValueError(f"{path}: not a TMCR file")
    ver = struct.unpack_from("<I", d, 4)[0]
    if ver not in (1, 2, 3, 4):
        raise ValueError(f"{path}: unsupported version {ver}")

    r = Room()
    r.version = ver
    r.entities, r.bg_palette, r.bg_vram = [], None, None
    r.src_palette, r.fade_active = None, 0
    off = 8
    r.area, r.room = d[off], d[off + 1]
    off += 2
    (r.origin_x, r.origin_y, r.width, r.height,
     r.cells_w, r.cells_h) = struct.unpack_from("<6H", d, off)
    off += 12
    present = (d[off], d[off + 1])
    off += 2

    r.layers = []
    for li in range(LAYERS):
        tile = np.frombuffer(d, "<u2", MAP_CELLS, off).reshape(MAP_DIM, MAP_DIM)
        off += MAP_CELLS * 2
        coll = np.frombuffer(d, "u1", MAP_CELLS, off).reshape(MAP_DIM, MAP_DIM)
        off += MAP_CELLS
        act = np.frombuffer(d, "u1", MAP_CELLS, off).reshape(MAP_DIM, MAP_DIM)
        off += MAP_CELLS
        ttype = np.frombuffer(d, "<u2", TILESET, off)
        off += TILESET * 2
        sub, bgctl = None, 0
        if ver >= 2:
            sub = np.frombuffer(d, "<u2", TILESET * 4, off); off += TILESET * 8
        if ver >= 3:
            (bgctl,) = struct.unpack_from("<H", d, off); off += 2
        r.layers.append({
            "present": bool(present[li]),
            "tile": tile, "collision": coll, "act": act, "tiletype": ttype,
            "subtile": sub, "bgcontrol": bgctl,
            # char_base = ((bgcnt >> 2) & 3) * 0x4000, per mode1.c:426
            "char_base": ((bgctl >> 2) & 3) * 0x4000,
            "bpp8": bool((bgctl >> 7) & 1),
        })

    if ver >= 3:
        for li in range(2):
            r.layers[li]["subtilemap"] = np.frombuffer(d, "<u2", 0x4000, off)
            off += 0x8000

    if ver >= 2:
        (n,) = struct.unpack_from("<H", d, off); off += 2
        for i in range(n):
            kind, eid, etype, direction, layer, frame = d[off:off + 6]
            off += 6
            x, y, z = struct.unpack_from("<iii", d, off); off += 12
            r.entities.append({
                "index": i, "is_player": i == 0,
                "kind": kind, "id": eid, "type": etype,
                "direction": direction, "layer": layer, "frame": frame,
                # Q16.16 -> world pixels. z stays on its own axis; the 2D draw
                # path folds it into screen Y, which is exactly what we don't
                # want in 3D.
                "x": x / 65536.0, "y": y / 65536.0, "z": z / 65536.0,
            })
        r.bg_palette = np.frombuffer(d, "<u2", 256, off); off += 512
        (vram_bytes,) = struct.unpack_from("<I", d, off); off += 4
        r.bg_vram = np.frombuffer(d, "u1", vram_bytes, off); off += vram_bytes

    if ver >= 4:
        # gPaletteBuffer: the fade SOURCE, so it carries true colours even when
        # the capture happened mid-fade. fade_active says whether bg_palette
        # (PAL_RAM, the fade destination) can be trusted.
        r.src_palette = np.frombuffer(d, "<u2", 256, off); off += 512
        r.fade_active = d[off]; off += 1

    return r


def iter_rooms(p: Path):
    if p.is_file():
        yield load_room(p)
        return
    for f in sorted(p.glob("room_*.tmcr")):
        try:
            yield load_room(f)
        except Exception as e:  # keep surveying past a bad dump
            print(f"  skip {f.name}: {e}", file=sys.stderr)


# ----------------------------------------------------------------------------
# Classifier — EDIT THIS. It is the whole point of the tool.
# ----------------------------------------------------------------------------

CLASS_VOID = "void"
CLASS_GROUND = "ground"
CLASS_WATER = "water"
CLASS_WALL = "wall"
CLASS_LEDGE = "ledge"
CLASS_HOLE = "hole"

# Heights in world pixels, applied on top of the cell's floor.
CLASS_HEIGHT = {
    CLASS_VOID: 0,
    CLASS_GROUND: 0,
    CLASS_WATER: -2,
    CLASS_WALL: 16,
    CLASS_LEDGE: 8,
    CLASS_HOLE: -16,
}

CLASS_COLOUR = {
    CLASS_VOID: (24, 24, 28),
    CLASS_GROUND: (110, 160, 90),
    CLASS_WATER: (60, 110, 190),
    CLASS_WALL: (150, 130, 110),
    CLASS_LEDGE: (190, 175, 120),
    CLASS_HOLE: (18, 18, 20),
}

# Start: 0 is walkable ground everywhere; anything else is solid until proven
# otherwise. The four annotated values give you a foothold.
# Derived from 10 real rooms (Link's house, Hyrule Town, Castle approach,
# Minish Woods, Minish Village). 0x0F alone is ~50% of all cells and is solid:
# interiors show a clean wall border and Hyrule Town resolves into a street
# grid once it is named. Everything still unlisted also reads as wall — the
# long tail sits at 20-35% ground-adjacency, same as known solids — so the
# fallback is correct rather than merely tolerated.
CLASS_BY_COLLISION = {
    0x00: CLASS_GROUND,  # walkable floor; 39% of bottom-layer cells
    0x0F: CLASS_WALL,    # generic solid; 50% of bottom-layer cells
    0x21: CLASS_HOLE,    # FX_FALL_DOWN; 25% of its cells sit on a room edge
    0x24: CLASS_WATER,   # FX_WATER_SPLASH
    0x25: CLASS_WATER,   # FX_LAVA_SPLASH — split out once lava is handled
    0x30: CLASS_WATER,   # FX_WATER_SPLASH; the only water seen in the field
    # --- derived from 764 harvested rooms -----------------------------------
    # 0x10-0x13 are a CORNER family. Each has open floor on exactly two
    # perpendicular sides and never on the other two, across ~1,500 cells:
    #   0x10  floor N+W  (never S/E)   NW corner
    #   0x11  floor N+E  (never S/W)   NE corner
    #   0x12  floor S+W  (never N/E)   SW corner
    #   0x13  floor S+E  (never N/W)   SE corner
    # Geometrically these are diagonal wall corners; a voxel pass should bevel
    # them rather than emit a square block.
    0x10: CLASS_WALL, 0x11: CLASS_WALL, 0x12: CLASS_WALL, 0x13: CLASS_WALL,

    # 0x2B: floor to N and S, never E or W — an east-west running band. 61% of
    # its cells are in Mt Crenel, rest Veil Falls / Crenel Dig Cave. Reads as a
    # cliff ledge you drop off southward. Treated as a ledge, not a full wall.
    0x2B: CLASS_LEDGE,

    # 0x2A: 885 cells, 100% of them in Cloud Tops, and perfectly isotropic
    # (25/25/25/25 neighbour split) — scattered, not structural. The clouds.
    0x2A: CLASS_GROUND,

    # Water-adjacent: 0x22 concentrates in Lake Hylia / Minish Caves /
    # Temple of Droplets. Likely deep or swimmable water.
    0x22: CLASS_WATER,

    # Deep-in-solid dungeon fills (8-10% ground adjacency, dungeon areas only).
    0x27: CLASS_WALL, 0x23: CLASS_WALL, 0x29: CLASS_WALL, 0x26: CLASS_WALL,

    # Wall variants seen across the overworld. Differentiating these is a
    # tileType/art job, not a collision one — collision says "solid", the art
    # says "tree" or "house" or "cliff".
    0x5F: CLASS_WALL, 0x0C: CLASS_WALL, 0x1D: CLASS_WALL, 0x03: CLASS_WALL,
    0x05: CLASS_WALL, 0x0A: CLASS_WALL, 0x04: CLASS_WALL, 0x02: CLASS_WALL,
    0x01: CLASS_WALL, 0x08: CLASS_WALL, 0x17: CLASS_WALL, 0x18: CLASS_WALL,
}

# Named tile types worth special-casing early (include/tiles.h).
CLASS_BY_TILETYPE = {
    0x1c: CLASS_GROUND,  # CUT_BUSH
    0x1d: CLASS_GROUND,  # CUT_GRASS
    0x92: CLASS_LEDGE,   # STAIRS_UP
    0x93: CLASS_LEDGE,   # STAIRS_DOWN
    0x55: CLASS_WALL,    # ROCK
    0x1d3: CLASS_WALL, 0x1d4: CLASS_WALL,
    0x1d5: CLASS_WALL, 0x1d6: CLASS_WALL,   # PERMA_ROCK 1-4
}

# Your pin table. Keyed (area, tileIndex) -> class. This is the file that grows
# to thousands of lines; PotatoVoxel's equivalent is 6,469 for a simpler game.
PINS = {}


# Which rule decided a cell. The class alone cannot tell you: the fallback
# returns CLASS_WALL, so "wall" is simultaneously a real class and the bucket
# for everything unrecognised. A corpus that is 47% wall means nothing until
# you know how much of that came from rule 3 versus rule 4.
SRC_PIN, SRC_EMPTY, SRC_TILETYPE, SRC_COLLISION, SRC_FALLBACK = (
    "pin", "empty", "tiletype", "collision", "fallback")


def classify_cell_src(area, layer, cx, cy):
    """Classify one cell, returning (class, deciding_rule, collision_value)."""
    tile = int(layer["tile"][cy, cx])
    coll = int(layer["collision"][cy, cx])

    if (area, tile) in PINS:                      # 1. authored pin
        return PINS[(area, tile)], SRC_PIN, coll

    if tile == 0 and coll == 0:                   # empty cell
        return CLASS_VOID, SRC_EMPTY, coll

    tt = int(layer["tiletype"][tile]) if tile < TILESET else 0
    if tt in CLASS_BY_TILETYPE:                   # 2. named tile type
        return CLASS_BY_TILETYPE[tt], SRC_TILETYPE, coll

    if coll in CLASS_BY_COLLISION:                # 3. collision
        return CLASS_BY_COLLISION[coll], SRC_COLLISION, coll

    return CLASS_WALL, SRC_FALLBACK, coll         # 4. fallback


def classify_cell(area, layer, cx, cy):
    return classify_cell_src(area, layer, cx, cy)[0]


def classify_room(r: Room, layer_index=0):
    layer = r.layers[layer_index]
    out = np.empty((r.cells_h, r.cells_w), dtype=object)
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            out[cy, cx] = classify_cell(r.area, layer, cx, cy)
    return out


# ----------------------------------------------------------------------------
# survey
# ----------------------------------------------------------------------------

def cmd_distinct(args):
    """Confirm the classified grids actually DIFFER between rooms.

    The terrain counterpart to hull_carve's `diag`, and it exists for the same
    reason: a statistic computed over rooms that are secretly identical looks
    exactly like a statistic over rooms that are genuinely varied. In Phase 0 a
    hypothesis "held" 41/41 because it was comparing an image against itself.
    Run this before quoting any number about terrain.

    Four failure modes, each of which produces confident-looking output:
      - identical grids      classification is ignoring the room data
      - single-class rooms   the fallback chain is swallowing everything
      - empty rooms          the capture is bad, not the classifier
      - one class dominating the whole corpus
    """
    rooms = list(iter_rooms(Path(args.dumps)))
    if not rooms:
        sys.exit(f"no .tmcr files under {args.dumps}")
    rooms.sort(key=lambda r: (r.area, r.room))
    if args.limit:
        rooms = rooms[:args.limit]

    import hashlib
    by_hash = {}
    per_room = []
    corpus = Counter()

    prog = Progress(len(rooms), label="distinct", step=10)
    for r in rooms:
        prog.update(1)
        name = f"room_{r.area:02d}_{r.room:02d}"
        grid = classify_room(r, args.layer)
        cells = grid[:r.cells_h, :r.cells_w] if r.cells_h and r.cells_w else grid
        counts = Counter(cells.ravel().tolist())
        corpus.update(counts)
        h = hashlib.sha1(cells.tobytes()).hexdigest()[:12]
        by_hash.setdefault(h, []).append(name)
        per_room.append((name, r, counts, h, cells.size))

    prog.done()
    n = len(per_room)
    dupes = {h: v for h, v in by_hash.items() if len(v) > 1}
    dupe_rooms = sum(len(v) for v in dupes.values())
    single = [(nm, next(iter(c))) for nm, r, c, h, sz in per_room if len(c) == 1]
    empty = [nm for nm, r, c, h, sz in per_room if sz == 0]

    print(f"Rooms analysed: {n}   (layer {args.layer})\n")
    print(f"  distinct classified grids : {len(by_hash)}/{n}")
    print(f"  rooms sharing a grid      : {dupe_rooms}"
          f"{'' if not dupes else f' across {len(dupes)} groups'}")
    print(f"  single-class rooms        : {len(single)}")
    print(f"  zero-cell rooms           : {len(empty)}")

    total = sum(corpus.values()) or 1
    print("\n  class distribution across the corpus:")
    for cls, cnt in corpus.most_common():
        bar = "#" * int(40 * cnt / total)
        print(f"    {cls:<8s} {100.0*cnt/total:5.1f}%  {bar}")

    if dupes:
        print("\n  duplicate groups (first few):")
        for h, names in list(dupes.items())[:5]:
            print(f"    {h}: {', '.join(names[:6])}"
                  f"{' ...' if len(names) > 6 else ''}")

    if single:
        print("\n  single-class rooms (first few):")
        for nm, cls in single[:8]:
            print(f"    {nm}: entirely '{cls}'")

    # Verdict. Duplicates are the signal that matters most: a handful can be
    # legitimate (small identical closets), but a large share means the
    # classifier is not reading the room.
    frac = dupe_rooms / max(n, 1)
    print()
    if n < 2:
        print("  -> need at least 2 rooms to say anything.")
    elif frac > 0.5:
        print("  -> MORE THAN HALF the rooms share a grid with another room. "
              "Treat every terrain statistic as unverified until this is "
              "explained; the likely cause is the classifier ignoring the "
              "per-room data.")
    elif len(single) > n * 0.25:
        print("  -> a quarter or more of rooms classify to a SINGLE class. "
              "The fallback chain is swallowing the data.")
    else:
        top = corpus.most_common(1)[0]
        print(f"  -> grids are distinct ({len(by_hash)}/{n}); "
              f"most common class '{top[0]}' at {100.0*top[1]/total:.1f}%. "
              f"Terrain statistics on this corpus are meaningful.")


def cmd_provenance(args):
    """Report WHICH classifier rule decided each cell, and what is unknown.

    `distinct` says the grids differ; it cannot say they are right. The class
    histogram hides its own weakest number, because rule 4 returns CLASS_WALL
    for every value the tables do not know. If most "wall" is fallback, the
    terrain is not classified -- it is defaulted, and a voxel world built on it
    would be a maze of blocks.

    The unknown-collision histogram at the end is the work list: each row is a
    value that appears in real rooms and has no entry in CLASS_BY_COLLISION,
    ordered by how much of the world it decides.
    """
    rooms = list(iter_rooms(Path(args.dumps)))
    if not rooms:
        sys.exit(f"no .tmcr files under {args.dumps}")
    rooms.sort(key=lambda r: (r.area, r.room))
    if args.limit:
        rooms = rooms[:args.limit]

    by_src = Counter()
    cls_by_src = {}
    unknown = Counter()
    unknown_areas = {}

    for r in rooms:
        layer = r.layers[args.layer]
        if not layer["present"]:
            continue
        for cy in range(min(r.cells_h, MAP_DIM)):
            for cx in range(min(r.cells_w, MAP_DIM)):
                cls, src, coll = classify_cell_src(r.area, layer, cx, cy)
                by_src[src] += 1
                cls_by_src.setdefault(src, Counter())[cls] += 1
                if src == SRC_FALLBACK:
                    unknown[coll] += 1
                    unknown_areas.setdefault(coll, Counter())[r.area] += 1

    total = sum(by_src.values()) or 1
    print(f"Cells classified: {total}  across {len(rooms)} rooms "
          f"(layer {args.layer})\n")
    print("  deciding rule:")
    for src, cnt in by_src.most_common():
        share = 100.0 * cnt / total
        top = ", ".join(f"{c}={100.0*n/cnt:.0f}%"
                        for c, n in cls_by_src[src].most_common(3))
        print(f"    {src:<10s} {share:5.1f}%  {'#' * int(30 * cnt / total):<30s} {top}")

    fb = by_src.get(SRC_FALLBACK, 0)
    fb_share = 100.0 * fb / total
    wall_total = sum(c.get(CLASS_WALL, 0) for c in cls_by_src.values())
    wall_fb = cls_by_src.get(SRC_FALLBACK, Counter()).get(CLASS_WALL, 0)

    print(f"\n  'wall' cells: {wall_total} total, {wall_fb} of them from the "
          f"fallback ({100.0*wall_fb/max(wall_total,1):.1f}%)")

    if unknown:
        print(f"\n  unknown collision values, by share of the world "
              f"({len(unknown)} distinct):")
        print(f"    {'coll':>6}  {'cells':>8}  {'% world':>8}  top areas")
        for coll, cnt in unknown.most_common(args.top):
            areas = ", ".join(f"0x{a:02x}({n})"
                              for a, n in unknown_areas[coll].most_common(3))
            print(f"    0x{coll:02x}  {cnt:8d}  {100.0*cnt/total:7.2f}%  {areas}")

    print()
    if fb_share > 25:
        print(f"  -> {fb_share:.1f}% of the world is DEFAULTED, not classified. "
              f"The class histogram is not trustworthy: add the values above to "
              f"CLASS_BY_COLLISION before building geometry on it.")
    elif fb_share > 5:
        print(f"  -> {fb_share:.1f}% fallback. Usable, but the values above are "
              f"worth adding -- each one is terrain currently rendering as wall.")
    else:
        print(f"  -> {fb_share:.1f}% fallback. The classifier is reading the "
              f"data, not defaulting it.")


def cmd_survey(args):
    coll_hist, tt_hist, sizes = Counter(), Counter(), Counter()
    n = 0
    for r in iter_rooms(Path(args.path)):
        n += 1
        sizes[(r.cells_w, r.cells_h)] += 1
        for layer in r.layers:
            if not layer["present"]:
                continue
            sub = layer["collision"][:r.cells_h, :r.cells_w]
            coll_hist.update(sub.ravel().tolist())
            tiles = layer["tile"][:r.cells_h, :r.cells_w].ravel()
            valid = tiles[tiles < TILESET]
            tt_hist.update(layer["tiletype"][valid].tolist())

    print(f"{n} rooms\n")
    print("collision values by frequency (edit CLASS_BY_COLLISION from this):")
    for v, c in coll_hist.most_common(25):
        known = CLASS_BY_COLLISION.get(v)
        mark = f"  -> {known}" if known else "  -> UNCLASSIFIED (falls back to wall)"
        print(f"  0x{v:02X}  {c:8d}{mark}")

    unclassified = sum(c for v, c in coll_hist.items() if v not in CLASS_BY_COLLISION)
    total = sum(coll_hist.values())
    if total:
        print(f"\n  {unclassified/total:6.1%} of cells hit the wall fallback. "
              f"Drive this down by naming values above.")

    print("\nroom sizes:")
    for (w, h), c in sizes.most_common(12):
        note = "  (single screen: engine camera never scrolls)" if w <= 15 and h <= 10 else ""
        print(f"  {w:3d} x {h:3d}  {c:4d} rooms{note}")

    print("\nmost common tile types:")
    for v, c in tt_hist.most_common(15):
        known = CLASS_BY_TILETYPE.get(v)
        print(f"  0x{v:03X}  {c:8d}" + (f"  -> {known}" if known else ""))


# ----------------------------------------------------------------------------
# preview
# ----------------------------------------------------------------------------

def room_art_rgb(r, layer_index=0, composite=True):
    """The room's art as an HxWx3 float array, top layer composited over bottom.

    Compositing is not optional for colour. In Hyrule Field the top layer
    covers about a third of the room -- every tree canopy is up there -- and
    the bottom layer underneath it holds tiles the game never displays. Reading
    colour from layer 0 alone paints the voxels under every tree with hidden
    filler, which is exactly what happened until someone looked at a tree and
    asked why it was made of bricks.

    Classification still uses layer 0 on its own: that is the collision layer,
    and it is the right source for what the terrain IS. This is only about what
    it LOOKS like.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import extract_art
        from PIL import Image
    except Exception:
        return None
    def as_arr(x):
        if x is None:
            return None
        return np.asarray(x)
    bottom = as_arr(extract_art.room_art(r, layer_index))
    if bottom is None:
        return None
    out = bottom.copy()
    if composite and layer_index == 0 and len(r.layers) > 1 \
            and r.layers[1]["present"]:
        top = as_arr(extract_art.room_art(r, 1))
        if top is not None and top.shape[:2] == out.shape[:2] \
                and top.shape[2] == 4 and out.shape[2] == 4:
            m = top[:, :, 3] > 0
            out[m] = top[m]
    return out[:, :, :3].astype(float)


def cmd_overlay(args):
    """Render the room's ART with the CLASSIFICATION drawn over it.

    `provenance` proves the classifier is reading the data; it cannot prove the
    mapping is right. One collision value, 0x0F, decides roughly half the
    world, and "generic solid" is an inference from frequency, not from an
    enum. If it is wrong, half of Hyrule becomes wall and every number still
    looks healthy.

    The only check that can catch that is the art itself: walls should sit on
    things that LOOK solid -- cliffs, buildings, trees -- and ground on things
    you can obviously walk on. Three panels: art, classification, and the two
    blended, so a misalignment is visible rather than inferred.
    """
    from PIL import Image
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import extract_art

    r = load_room(Path(args.path))
    if not r.layers[args.layer]["present"] or not r.cells_w:
        sys.exit(f"layer {args.layer} not present in {args.path}")

    a = room_art_rgb(r, args.layer)
    if a is None:
        sys.exit("room_art returned nothing -- needs a v3+ dump")
    art = Image.fromarray(a.astype(np.uint8)).convert("RGB")

    # One metatile is 16x16 px, so the class grid scales to the art exactly.
    cls = classify_room(r, args.layer)
    cel = Image.new("RGB", (r.cells_w, r.cells_h))
    cp = cel.load()
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            cp[cx, cy] = CLASS_COLOUR[cls[cy, cx]]
    cel = cel.resize(art.size, Image.NEAREST)

    blend = Image.blend(art, cel, args.alpha)
    gap = 8
    sheet = Image.new("RGB", (art.width * 3 + gap * 2, art.height), (20, 20, 26))
    sheet.paste(art, (0, 0))
    sheet.paste(cel, (art.width + gap, 0))
    sheet.paste(blend, (art.width * 2 + gap * 2, 0))
    if args.scale != 1:
        sheet = sheet.resize((sheet.width * args.scale, sheet.height * args.scale),
                             Image.NEAREST)
    sheet.save(args.out)

    counts = Counter(cls.ravel().tolist())
    print(f"{r} layer {args.layer} -> {args.out}   (art | classes | blend)")
    for k, v in counts.most_common():
        print(f"  {CLASS_COLOUR.get(k)}  {k:8s} {v:5d}  {v/cls.size:5.1%}")
    print("\nWalls should land on cliffs, buildings and trees. If they land on "
          "open floor, CLASS_BY_COLLISION[0x0F] is wrong and half the world "
          "with it.")


def cmd_agree(args):
    """Does the CLASSIFICATION track anything visible in the ART?

    `provenance` shows the classifier reads the data; it cannot show the
    mapping is right. One value, 0x0F, decides about half the world, and
    "generic solid" was inferred from frequency, not read from an enum.

    The first version of this check tested whether ground cells "look like
    grass". That was built from two overworld rooms and is wrong for the
    corpus: most of the 561 rooms are caves, houses and dungeons with no grass
    in them, so it scored real dungeon classification as failure. The lesson is
    the recurring one -- a metric derived from a couple of examples looks
    universal and is not.

    The replacement is palette-agnostic. Within EACH room, ask whether the
    cells called ground and the cells called wall look different FROM EACH
    OTHER, in units of that room's own colour spread:

        d' = |mean(ground) - mean(wall)| / pooled within-class std

    d' near 0 means the classifier is drawing a line the art does not show --
    the failure worth catching. Large d' means it is tracking a real boundary,
    whether that is grass against roof or cave floor against cave wall.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import extract_art
    from PIL import Image

    rooms = list(iter_rooms(Path(args.dumps)))
    if not rooms:
        sys.exit(f"no .tmcr files under {args.dumps}")
    rooms.sort(key=lambda r: (r.area, r.room))
    if args.limit:
        rooms = rooms[:args.limit]

    def dprime(A, B, flat_eps):
        """Separation in units of within-class spread, or None if degenerate.

        A floor of 1e-6 on the denominator produced d' in the tens of millions
        for rooms whose art has almost no colour variance -- blank or flat
        rooms scoring as the BEST separated in the corpus, and a corpus mean of
        185,000. Those rooms are not well classified, they are featureless, and
        a ratio is meaningless when the denominator is noise. Report them as
        their own category instead of letting them inflate the statistics.
        """
        A, B = np.asarray(A, float), np.asarray(B, float)
        pooled = np.sqrt(0.5 * (A.var(0).mean() + B.var(0).mean()))
        if pooled < flat_eps:
            return None
        return float(np.linalg.norm(A.mean(0) - B.mean(0)) / pooled)

    def boundary_dprime(a, cls, r, flat_eps):
        """Do class boundaries land where the PICTURE changes?

        The mean-colour test asks whether ground and wall are different
        colours on average. That fails on dungeons, where floor and walls are
        drawn from one stone palette -- area 112 scored 0.23 while being
        correctly classified. Averages cannot see a boundary inside a single
        palette.

        This asks the question that survives a shared palette: between each
        pair of adjacent cells, how big is the colour step, and is it bigger
        where the CLASS changes than where it does not? Tiles still differ
        locally where two materials meet, whatever their average hue. On the
        same area-112 room this reads 1.30 against the 0.23 above.
        """
        h = min(r.cells_h, a.shape[0] // 16)
        w_ = min(r.cells_w, a.shape[1] // 16)
        if h < 2 or w_ < 2:
            return None
        M = a[:h * 16, :w_ * 16].reshape(h, 16, w_, 16, 3).mean(axis=(1, 3))
        same, diff = [], []
        for dy, dx in ((0, 1), (1, 0)):
            A = M[:h - dy, :w_ - dx]
            B = M[dy:, dx:]
            step = np.linalg.norm(A - B, axis=2).ravel()
            ka = cls[:h - dy, :w_ - dx].ravel()
            kb = cls[dy:h, dx:w_].ravel()
            changed = ka != kb
            diff.append(step[changed])
            same.append(step[~changed])
        same = np.concatenate(same)
        diff = np.concatenate(diff)
        if len(same) < 8 or len(diff) < 8:
            return None
        pooled = np.sqrt(0.5 * (same.var() + diff.var()))
        if pooled < flat_eps:
            return None
        return float((diff.mean() - same.mean()) / pooled)

    scored, flat, skipped = [], [], 0
    prog = Progress(len(rooms), label="agree", step=5)
    for r in rooms:
        prog.update(1, f"room_{r.area:02d}_{r.room:02d}")
        layer = r.layers[args.layer]
        if not layer["present"] or not r.cells_w:
            skipped += 1
            continue
        try:
            a = room_art_rgb(r, args.layer)
        except Exception:
            skipped += 1
            continue
        if a is None:
            skipped += 1
            continue
        cls = classify_room(r, args.layer)
        g, w = [], []
        for cy in range(min(r.cells_h, a.shape[0] // 16)):
            for cx in range(min(r.cells_w, a.shape[1] // 16)):
                blk = a[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16].reshape(-1, 3)
                k = cls[cy, cx]
                if k == CLASS_GROUND:
                    g.append(blk.mean(0))
                elif k == CLASS_WALL:
                    w.append(blk.mean(0))
        if len(g) < args.min_cells or len(w) < args.min_cells:
            skipped += 1
            continue
        d = dprime(g, w, args.flat_eps)
        b = boundary_dprime(a, cls, r, args.flat_eps)
        name = f"room_{r.area:02d}_{r.room:02d}"
        if d is None or b is None:
            flat.append(name)
        else:
            scored.append((b, d, name, len(g), len(w)))

    prog.done()
    if not scored:
        sys.exit("no room had enough ground AND wall cells to compare")

    ds = np.array([x[0] for x in scored])          # boundary d' (primary)
    ms = np.array([x[1] for x in scored])          # mean-colour d' (secondary)
    weak = int((ds < args.weak).sum())
    print(f"Rooms compared: {len(scored)}  (skipped {skipped}, "
          f"featureless {len(flat)}; layer {args.layer})\n")
    if flat:
        print(f"  {len(flat)} room(s) have art with almost no colour variance, "
              f"so no ratio is meaningful:\n    "
              + ", ".join(flat[:6]) + (" ..." if len(flat) > 6 else "") + "\n")
    print(f"  boundary d' -- do class edges land on visible edges? (primary)")
    print(f"    median {np.median(ds):.2f}   mean {ds.mean():.2f}   "
          f"min {ds.min():.2f}   max {ds.max():.2f}")
    print(f"  mean-colour d' -- are the two classes different colours? "
          f"(secondary; low is NORMAL in dungeons)")
    print(f"    median {np.median(ms):.2f}   mean {ms.mean():.2f}")
    for lo, hi in ((0, .25), (.25, .5), (.5, 1), (1, 2), (2, 99)):
        n = int(((ds >= lo) & (ds < hi)).sum())
        lbl = f"{lo:.2f}-{hi:.2f}" if hi < 99 else f">{lo:.0f}"
        print(f"    {lbl:>10}  {n:4d}  {'#' * int(50 * n / len(ds))}")

    print(f"\n  rooms below d'={args.weak}: {weak} "
          f"({100.0 * weak / len(scored):.1f}%)")
    if weak:
        print("  weakest -- classification draws a line the art does not show:")
        for b, d, nm, ng, nw in sorted(scored)[:args.show]:
            print(f"    {nm}  boundary d'={b:.2f}  (colour d'={d:.2f}; "
                  f"{ng} ground, {nw} wall cells)")
        print("  Inspect these with `overlay`.")

    print()
    if np.median(ds) < args.weak:
        print(f"  -> MOST rooms show class boundaries that do NOT fall on "
              f"visible edges. The collision mapping is not tracking the art.")
    else:
        print(f"  -> median boundary d'={np.median(ds):.2f}: class edges fall "
              f"on edges in the art. The collision mapping tracks the picture, "
              f"including where floor and wall share one palette.")


def cmd_preview(args):
    from PIL import Image
    r = load_room(Path(args.path))
    cls = classify_room(r, args.layer)
    px = args.cell
    img = Image.new("RGB", (r.cells_w * px, r.cells_h * px), (0, 0, 0))
    pix = img.load()
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            c = CLASS_COLOUR[cls[cy, cx]]
            for dy in range(px):
                for dx in range(px):
                    pix[cx * px + dx, cy * px + dy] = c
    img.save(args.out)
    counts = Counter(cls.ravel().tolist())
    print(f"{r}  layer {args.layer} -> {args.out}")
    for k, v in counts.most_common():
        print(f"  {k:8s} {v:5d}  {v/cls.size:5.1%}")


# ----------------------------------------------------------------------------
# greedy meshing
# ----------------------------------------------------------------------------

def greedy_quads(mask: np.ndarray):
    """Merge a 2-D boolean mask into maximal axis-aligned rectangles.

    Yields (x, y, w, h). This is the whole reason Quest is viable: naive
    per-voxel quads blow a mobile tile-based GPU's budget inside one room.
    """
    m = mask.copy()
    h, w = m.shape
    for y in range(h):
        x = 0
        while x < w:
            if not m[y, x]:
                x += 1
                continue
            # extend right
            rw = 1
            while x + rw < w and m[y, x + rw]:
                rw += 1
            # extend down while the whole span stays set
            rh = 1
            while y + rh < h and m[y + rh, x:x + rw].all():
                rh += 1
            m[y:y + rh, x:x + rw] = False
            yield x, y, rw, rh
            x += rw


def cmd_mesh(args):
    r = load_room(Path(args.path))
    cls = classify_room(r, args.layer)

    verts, faces = [], []

    def quad(p0, p1, p2, p3):
        base = len(verts) + 1           # OBJ is 1-indexed
        verts.extend([p0, p1, p2, p3])
        faces.append((base, base + 1, base + 2, base + 3))

    # One top face per class band, greedy-merged.
    for klass, height in CLASS_HEIGHT.items():
        if klass == CLASS_VOID:
            continue
        mask = (cls == klass)
        if not mask.any():
            continue
        for x, y, w, h in greedy_quads(mask):
            # world pixels; +X east, +Y up, +Z south
            x0 = r.origin_x + x * 16
            x1 = x0 + w * 16
            z0 = r.origin_y + y * 16
            z1 = z0 + h * 16
            yv = float(height)
            quad((x0, yv, z0), (x1, yv, z0), (x1, yv, z1), (x0, yv, z1))

    out = Path(args.out)
    with out.open("w") as f:
        f.write(f"# {r}  layer {args.layer}\n")
        f.write(f"# world-pixel units; apply metresPerPixel in the model matrix\n")
        for v in verts:
            f.write(f"v {v[0]:.1f} {v[1]:.1f} {v[2]:.1f}\n")
        for a, b, c, d in faces:
            f.write(f"f {a} {b} {c} {d}\n")

    naive = int((cls != CLASS_VOID).sum())
    print(f"{r} -> {out}")
    print(f"  {len(faces)} quads after greedy merge "
          f"(naive per-cell would be {naive}; "
          f"{100 * (1 - len(faces) / max(naive, 1)):.0f}% saved)")


# ----------------------------------------------------------------------------

KIND_NAMES = {1: "PLAYER", 2: "?2", 3: "ENEMY", 4: "PROJECTILE",
              5: "?5", 6: "OBJECT", 7: "NPC", 8: "?8", 9: "MANAGER"}


def measured_drop(r, layer_index, cls):
    """How tall is each tier, measured from the drawing?

    CLASS_HEIGHT gives every wall 16px and every ledge 8px, so a one-step
    kerb and a cliff come out the same height. The drawing knows better: a
    face turned away from the light is drawn dark, and that dark band IS the
    face, so its depth in pixels is the drop -- heights project 1:1 here.

    The bright/dark split is worth trusting: checked against the collision
    map, which is stored separately from any palette, blocked cells average
    0.49 shade against 0.77 for open floor, and blocked is darker in 98.7%
    of 78 rooms.

    Returns a per-cell height, falling back to CLASS_HEIGHT where the art
    gives no answer.
    """
    try:
        import shading as _SH
        d = _SH.decompose_room(r, layer_index)
    except Exception:
        d = None
    base = np.zeros((r.cells_h, r.cells_w), np.int32)
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            base[cy, cx] = CLASS_HEIGHT.get(cls[cy, cx], 0)
    if d is None:
        return base
    shd = d[1]
    H, W = shd.shape
    hi = float(np.percentile(shd, 70))
    lo = float(np.percentile(shd, 30))
    if hi - lo < 0.08:                 # a flat-lit room says nothing
        return base
    out = base.copy()
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            if cls[cy, cx] not in (CLASS_WALL, CLASS_LEDGE):
                continue
            x0, x1 = cx * 16, min(W, cx * 16 + 16)
            if x1 <= x0:
                continue
            # walk DOWN from this cell counting rows that stay dark: that is
            # the face, and it ends where the lit ground begins.
            rows = 0
            y = cy * 16
            while y < H and rows < 48:
                band = shd[y, x0:x1]
                if band.size == 0 or float(np.median(band)) > lo:
                    break
                rows += 1
                y += 1
            if rows >= 2:
                out[cy, cx] = int(rows)
    return out


def heightfield(r, cls, layer_index=0, measured=True):
    """Class grid -> per-cell height in world pixels."""
    if measured:
        try:
            return measured_drop(r, layer_index, cls)
        except Exception:
            pass
    h = np.zeros((r.cells_h, r.cells_w), np.int32)
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            h[cy, cx] = CLASS_HEIGHT.get(cls[cy, cx], 0)
    return h


# ------------------------------------------------------------------ relief --
# The 45-degree rule applied to the whole room (viewangle.py): a thing
# raised h is drawn h rows north of where it stands. So give every drawn
# pixel its height above the ground and move it south by that height.
#
# The blocked cells of a wall are its drawn FRONT FACE: a column of blocked
# pixels ending on floor at row b is a face whose pixel at row y stands
# b - y high. Moved south, the whole face lands on row b -- a vertical wall
# exactly as tall as the artist drew it -- and anything beyond FACE_MAX is
# the wall's top, at the face's height. Walls were a fixed 16px before, so
# arches, buildings and brazier stands came out short and their upper
# parts were projected onto their tops.
FACE_MAX = 48               # px: tallest front face; beyond it, wall top
DOOR_ACTS = (0x28, 0x29)    # SURFACE_DOOR_13, SURFACE_DOOR: openings


def relief_field(r, cls, step, layer_index=0):
    """Plan heights and solidity at `step`-pixel resolution, by relief.

    Returns (H, solid) shaped like the room's grid at that step. Ground,
    water and holes keep their class heights; walls and ledges become faces
    and tops as above; door cells are openings at ground height (they were
    blocked, which stood a block in every doorway).
    """
    ch, cw = r.cells_h, r.cells_w
    act = r.layers[layer_index]["act"][:ch, :cw]
    blocked_c = np.zeros((ch, cw), bool)
    base_c = np.zeros((ch, cw), np.int32)
    solid_c = np.zeros((ch, cw), bool)
    for cy in range(ch):
        for cx in range(cw):
            c = cls[cy, cx]
            solid_c[cy, cx] = c != CLASS_VOID
            if int(act[cy, cx]) in DOOR_ACTS:
                continue                      # an opening, at ground
            if c in (CLASS_WALL, CLASS_LEDGE):
                blocked_c[cy, cx] = True
            else:
                base_c[cy, cx] = CLASS_HEIGHT.get(c, 0)
    # Plateaus: ground cut off from the room's main floor is a wall top or
    # an upper floor, not the floor itself (the desert temple draws the sand
    # above its walls as walkable ground). The main floor is the largest
    # connected walkable region.
    walk = np.zeros((ch, cw), bool)
    for cy in range(ch):
        for cx in range(cw):
            walk[cy, cx] = (not blocked_c[cy, cx] and solid_c[cy, cx]
                            and base_c[cy, cx] >= 0)
    comp, ncomp = _label(walk)
    sizes = np.bincount(comp.ravel(), minlength=ncomp + 1)
    sizes[0] = 0
    main = int(sizes.argmax()) if ncomp else 0

    Hp, Wp = ch * 16, cw * 16
    up = lambda a: np.repeat(np.repeat(a, 16, 0), 16, 1)
    B = up(blocked_c)
    h = up(base_c).astype(np.int32)
    solid = up(solid_c)
    comp_p = up(comp)
    faces = []                                 # (x, a, b, f) per face run
    long_runs = []
    for x in range(Wp):
        col = B[:, x]
        y = 0
        while y < Hp:
            if not col[y]:
                y += 1
                continue
            a = y
            while y < Hp and col[y]:
                y += 1
            b = y                              # face base: first row below
            if b - a > 2 * FACE_MAX:
                long_runs.append((x, a, b))    # a side wall, seen edge-on
                continue
            f = min(b - a, FACE_MAX)
            faces.append((x, a, b, f))
            rows = np.arange(a, b)
            h[a:b, x] = np.where(rows >= b - f, b - rows, f)
    # A side wall runs north-south, so its column is one long blocked run
    # that is not a face. It stands as tall as the faces around it.
    typical = int(np.median([f for _, _, _, f in faces])) if faces else 16
    for x, a, b in long_runs:
        h[a:b, x] = typical
    # Raise each plateau to the height of the faces whose tops touch it.
    lift_of = {}
    for x, a, b, f in faces:
        if a > 0:
            k = int(comp_p[a - 1, x])
            if k and k != main:
                lift_of.setdefault(k, []).append(f)
    for k, fs in lift_of.items():
        h[(comp_p == k)] = int(np.median(fs))
    # move south by height; highest wins; gaps filled from above
    out = np.full((Hp, Wp), -10 ** 6, np.int32)
    ys, xs = np.nonzero(solid)
    zs = ys + np.maximum(h[ys, xs], 0)
    keep = zs < Hp
    order = np.argsort(h[ys, xs][keep], kind="stable")
    zk, xk, hk = zs[keep][order], xs[keep][order], h[ys, xs][keep][order]
    out[zk, xk] = hk
    got = out > -10 ** 6
    for _ in range(FACE_MAX + 1):
        fill = ~got & np.roll(got, 1, 0) & solid
        fill[0] = False
        if not fill.any():
            break
        out[fill] = np.roll(out, 1, 0)[fill]
        got |= fill
    out[~got] = 0
    gh, gw = Hp // step, Wp // step
    Hs = out[:gh * step, :gw * step].reshape(gh, step, gw, step).max(axis=(1, 3))
    Ss = solid[:gh * step, :gw * step].reshape(gh, step, gw, step).any(axis=(1, 3))
    return Hs.astype(np.int32), Ss


def cell_colours(r, layer_index, cells_h, cells_w):
    """Mean art colour per cell, or None when the room has no usable art."""
    try:
        a = room_art_rgb(r, layer_index)
        if a is None:
            return None
    except Exception:
        return None
    out = np.full((cells_h, cells_w, 3), 128.0)
    for cy in range(min(cells_h, a.shape[0] // 16)):
        for cx in range(min(cells_w, a.shape[1] // 16)):
            out[cy, cx] = a[cy*16:(cy+1)*16, cx*16:(cx+1)*16].reshape(-1, 3).mean(0)
    return out / 255.0


# ---------------------------------------------------------------- canopies --
# Tree canopies live on layer 1. Extruded like everything else, a whole
# forest became flat plates 24px up. A crown is a rounded mass of leaf
# clumps, and the drawing says so: its edge outlines the mass, and each
# clump is lit from the north-west with a dark line between clumps.
CANOPY_HUE = (60, 235)      # green through teal (Hyrule Field ~115, woods ~210)
CANOPY_MIN_SAT = 0.4        # roofs and garden beds on layer 1 are duller
CANOPY_MAX_FILL = 0.93      # of the bounding box: bridges and decks are boxes
CANOPY_MIN_PX = 64
CROWN_R = 14                # px from the drawn edge to full crown height
CROWN_H = 14                # how far a crown rises above its base
BUMP_H = 3                  # leaf-clump relief from the shading
BUMP_BLUR = 5               # px: shading is measured against this local mean


def _label(mask):
    """4-connected components of a boolean image -> (labels, count)."""
    lab = np.zeros(mask.shape, np.int32)
    n = 0
    H, W = mask.shape
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n += 1
        st = [(y0, x0)]
        lab[y0, x0] = n
        while st:
            y, x = st.pop()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = n
                    st.append((ny, nx))
    return lab, n


def _box_blur(a, k):
    pad = np.pad(a, k, mode="edge")
    c = np.cumsum(np.cumsum(pad, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    n = 2 * k + 1
    return (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)


def canopy_mask(r):
    """Drawn pixels of layer 1 that are tree canopy, and the layer-1 art.

    A layer-1 blob is canopy when it is leaf-coloured (hue within
    CANOPY_HUE, median saturation at least CANOPY_MIN_SAT) and ragged
    (fills under CANOPY_MAX_FILL of its bounding box). Checked on Minish
    Woods, Hyrule Field and Hyrule Town: canopies pass; roofs, bridges and
    a town garden bed do not.
    """
    import colorsys
    import extract_art
    top = extract_art.room_art(r, 1)
    if top is None:
        return None, None
    drawn = top[:, :, 3] > 0
    lab, n = _label(drawn)
    out = np.zeros(drawn.shape, bool)
    for k in range(1, n + 1):
        ys, xs = np.nonzero(lab == k)
        if len(ys) < CANOPY_MIN_PX:
            continue
        px = top[ys, xs, :3].astype(float) / 255.0
        px = px[::max(1, len(px) // 400)]
        hsv = np.array([colorsys.rgb_to_hsv(*c) for c in px])
        hue, sat = np.median(hsv[:, 0]) * 360.0, np.median(hsv[:, 1])
        fill = len(ys) / float((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1))
        if (CANOPY_HUE[0] <= hue <= CANOPY_HUE[1] and sat >= CANOPY_MIN_SAT
                and fill < CANOPY_MAX_FILL):
            out[ys, xs] = True
    return out, top


def canopy_field(r, step=2, lift=0):
    """Height and colour of the room's tree crowns, on a plan grid.

    Returns (H, colour, mask) at `step`-pixel resolution in room pixels, or
    None when the room has no canopy. H is height above the crown's base.

      dome    rises with distance from the drawn edge, as a quarter circle
              of radius CROWN_R to CROWN_H, so a lone tree is a dome and a
              forest is rounded masses rather than one plateau
      bumps   each leaf clump's shading against its local mean (BUMP_BLUR):
              lit clump tops rise up to BUMP_H, the dark lines between
              clumps sink as far
      45deg   a crown point drawn at row y at height h stands at z = y + h
              (drawn = height + depth, viewangle.py), so crowns move south
              over their trunks. h is the WHOLE height, `lift` (where the
              crown's base sits) included. Colour stays where it was drawn,
              so projecting the art from the game's camera lands on it.
    """
    mask, top = canopy_mask(r)
    if mask is None or not mask.any():
        return None
    Hh, Ww = mask.shape
    # distance from the drawn edge, 4-neighbour steps, capped at CROWN_R
    dist = np.where(mask, CROWN_R, 0).astype(np.int32)
    edge = mask & ~(np.roll(mask, 1, 0) & np.roll(mask, -1, 0)
                    & np.roll(mask, 1, 1) & np.roll(mask, -1, 1))
    dist[edge] = 1
    for _ in range(CROWN_R):
        nb = np.minimum.reduce([np.roll(dist, 1, 0), np.roll(dist, -1, 0),
                                np.roll(dist, 1, 1), np.roll(dist, -1, 1)])
        dist = np.where(mask, np.minimum(dist, nb + 1), 0)
    t = np.clip(dist / float(CROWN_R), 0, 1)
    dome = CROWN_H * np.sqrt(1.0 - (1.0 - t) ** 2)
    lum = top[:, :, :3].astype(float).mean(axis=2)
    rel = lum - _box_blur(np.where(mask, lum, 0.0), BUMP_BLUR) \
        / np.maximum(_box_blur(mask.astype(float), BUMP_BLUR), 1e-6)
    bump = BUMP_H * np.clip(rel / 40.0, -1.0, 1.0)
    h = np.where(mask, np.maximum(1.0, dome + bump), 0.0)

    # 45 degrees: move each drawn pixel south by its height, keeping the
    # highest where several land on one plan pixel.
    Hp = np.zeros((Hh + lift + CROWN_H + BUMP_H + 2, Ww), float)
    Cp = np.zeros(Hp.shape + (3,), float)
    ys, xs = np.nonzero(mask)
    zs = ys + lift + np.round(h[ys, xs]).astype(int)
    order = np.argsort(h[ys, xs], kind="stable")   # low first, so high wins
    Hp[zs[order], xs[order]] = h[ys, xs][order]
    Cp[zs[order], xs[order]] = top[ys, xs, :3][order] / 255.0
    got = Hp > 0
    # A steep crown edge moves more than a pixel per row and leaves gaps:
    # fill each from the pixel above it in the same column.
    for _ in range(CROWN_H + BUMP_H):
        up = np.roll(got, 1, 0)
        fill = ~got & up & np.roll(got, -1, 0)
        if not fill.any():
            break
        Hp[fill] = np.roll(Hp, 1, 0)[fill]
        Cp[fill] = np.roll(Cp, 1, 0)[fill]
        got = got | fill
    # keep the full shifted extent: crowns at the room's south edge now
    # stand past it, which is where they are
    Hh = Hp.shape[0]
    # down to the step grid: max height, mean colour
    gh, gw = Hh // step, Ww // step
    Hs = Hp[:gh * step, :gw * step].reshape(gh, step, gw, step).max(axis=(1, 3))
    Ms = got[:gh * step, :gw * step].reshape(gh, step, gw, step).any(axis=(1, 3))
    wsum = got[:gh * step, :gw * step].reshape(gh, step, gw, step).sum(axis=(1, 3))
    Cs = (Cp[:gh * step, :gw * step].reshape(gh, step, gw, step, 3).sum(axis=(1, 3))
          / np.maximum(wsum, 1)[..., None])
    # Heights in 2px steps: finer is below what the bumps mean, and equal
    # neighbours merge into far fewer faces.
    return (2 * np.round(Hs / 2.0)).astype(int), Cs, Ms


def crown_cells(r, cmask, top):
    """Cells whose layer-1 drawing is mostly canopy: these get crowns, and
    the flat overlay plate is skipped for them."""
    crown = np.zeros((r.cells_h, r.cells_w), bool)
    drawn1 = top[:, :, 3] > 0
    for cy in range(r.cells_h):
        for cx in range(r.cells_w):
            dcell = drawn1[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            if dcell.any():
                ccell = cmask[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                crown[cy, cx] = ccell.sum() * 2 > dcell.sum()
    return crown


def emit_canopy(quad, cf, ox, oz, base, step=2, uvf=None, merge=False):
    """Emit the crowns from canopy_field as a closed surface.

    Top faces at base + H per plan cell; walls wherever a neighbour is lower,
    down to the neighbour or to the base at the crown's edge; and an
    underside at the base, so a crown seen from below is not hollow.

    uvf(points) -> uv list gives textured output its coordinates. With
    merge, tops at one height become greedy rectangles and walls with the
    same top and bottom along a row or column become one face -- right when
    a texture supplies the detail, wrong for vertex colour, where each cell
    carries its own. Returns the number of quads.
    """
    H, C, M = cf
    rows, cols = H.shape
    n = 0

    def put(pts, c):
        quad(pts, c, uvf(pts)) if uvf else quad(pts, c)

    # Height of each cell and of what lies past each of its four sides.
    Hb = np.where(M, base + H, 0)
    def nb(dy, dx):
        out = np.full(Hb.shape, base)
        ys = slice(max(0, dy), rows + min(0, dy))
        yd = slice(max(0, -dy), rows + min(0, -dy))
        xs = slice(max(0, dx), cols + min(0, dx))
        xd = slice(max(0, -dx), cols + min(0, -dx))
        src = np.where(M, Hb, base)
        out[yd, xd] = src[ys, xs]
        return out

    if merge:
        for hv in sorted(set(int(v) for v in np.unique(H[M]))):
            h = base + hv
            for x, y, w, hg in greedy_quads(M & (H == hv)):
                x0 = ox + x * step; x1 = x0 + w * step
                z0 = oz + y * step; z1 = z0 + hg * step
                put([(x0, h, z0), (x1, h, z0), (x1, h, z1), (x0, h, z1)],
                    C[y:y + hg, x:x + w].reshape(-1, 3).mean(0))
                n += 1
    else:
        for gy, gx in zip(*np.nonzero(M)):
            h = int(Hb[gy, gx])
            x0 = ox + gx * step; x1 = x0 + step
            z0 = oz + gy * step; z1 = z0 + step
            put([(x0, h, z0), (x1, h, z0), (x1, h, z1), (x0, h, z1)], C[gy, gx])
            n += 1

    # Walls. For each side, a cell shows a wall when what lies past it is
    # lower. Runs along the wall's own direction with the same top and
    # bottom merge into one face when `merge` is set.
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        low = nb(dy, dx)
        wall = M & (low < Hb)
        along_x = dx == 0              # north/south walls run along x
        lines = range(rows) if along_x else range(cols)
        for li in lines:
            k = 0
            length = cols if along_x else rows
            while k < length:
                gy, gx = (li, k) if along_x else (k, li)
                if not wall[gy, gx]:
                    k += 1
                    continue
                h, b = int(Hb[gy, gx]), int(low[gy, gx])
                e = k + 1
                if merge:
                    while e < length:
                        ey, ex = (li, e) if along_x else (e, li)
                        if not (wall[ey, ex] and int(Hb[ey, ex]) == h
                                and int(low[ey, ex]) == b):
                            break
                        e += 1
                if along_x:
                    x0 = ox + k * step; x1 = ox + e * step
                    z = oz + (gy + (1 if dy == 1 else 0)) * step
                    p_ = ([(x0, h, z), (x1, h, z), (x1, b, z), (x0, b, z)] if dy == 1
                          else [(x1, h, z), (x0, h, z), (x0, b, z), (x1, b, z)])
                else:
                    z0 = oz + k * step; z1 = oz + e * step
                    x = ox + (gx + (1 if dx == 1 else 0)) * step
                    p_ = ([(x, h, z0), (x, h, z1), (x, b, z1), (x, b, z0)] if dx == 1
                          else [(x, h, z1), (x, h, z0), (x, b, z0), (x, b, z1)])
                put(p_, C[gy, gx] * 0.8)
                n += 1
                k = e
    under = np.array([0.12, 0.2, 0.14])
    for x, y, w, hg in greedy_quads(M):
        x0 = ox + x * step; x1 = x0 + w * step
        z0 = oz + y * step; z1 = z0 + hg * step
        put([(x0, base, z1), (x1, base, z1), (x1, base, z0), (x0, base, z0)], under)
        n += 1
    return n


def overlay_skirt_bottom(hh, H, occ, cy, cx, dy, dx, rows, cols,
                         reach, fixed):
    """How far down the apron of a lifted surface should go.

    The overlay used to drop a fixed number of pixels and stop, which makes
    every canopy and bridge deck a plate hanging in the air with a decorative
    lip. Nothing held it up, because nothing was ever asked to.

    A lifted surface meets the world in one of two ways, and the drop below it
    says which. Where the ground comes up close -- a bridge reaching its bank,
    a roof meeting the wall it sits on -- the apron goes all the way down and
    the thing is supported. Where the drop is large, it is a real overhang that
    the player is meant to walk under, and closing it would wall off the space
    the second level exists to create. So `reach` is the line between a leg and
    a wall, and it is the only judgement here; everything else is measured.
    """
    ny, nx = cy + dy, cx + dx
    ground = H[cy, cx]
    if 0 <= ny < rows and 0 <= nx < cols and not occ[ny, nx]:
        ground = max(ground, H[ny, nx])
    if hh - ground <= reach:
        return int(ground), True
    return int(hh - fixed), False


def report_floaters(occ, hh_of, H, rows, cols, reach, label="overlay"):
    """Name every lifted component that reaches nothing.

    A floater is not a bad-looking quad, it is a connected piece of world with
    no path to the ground, and that is a property of the cell grid rather than
    of the mesh. Counting them is what turns 'the voxels look wrong' into a
    number that can go to zero.
    """
    seen = np.zeros((rows, cols), bool)
    anchored = floating = 0
    worst = []
    for y in range(rows):
        for x in range(cols):
            if not occ[y, x] or seen[y, x]:
                continue
            stack, comp = [(y, x)], []
            seen[y, x] = True
            while stack:
                ay, ax = stack.pop()
                comp.append((ay, ax))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = ay + dy, ax + dx
                    if 0 <= ny < rows and 0 <= nx < cols and occ[ny, nx] \
                            and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            drop = min(hh_of[cy][cx] - H[cy, cx] for cy, cx in comp)
            if drop <= reach:
                anchored += 1
            else:
                floating += 1
                worst.append((len(comp), int(drop)))
    if anchored or floating:
        print(f"  {label} support: {anchored} anchored, {floating} floating")
        for n, d in sorted(worst, reverse=True)[:5]:
            print(f"    floating component: {n} cells, nearest ground {d}px below")
    return floating


def cmd_voxel(args):
    """Extrude the classified room into a solid, coloured mesh.

    `mesh` emits only top faces, which leaves every wall a plate floating over
    nothing -- fine for a flat overhead preview, wrong the moment a head can
    move. This emits the sides too, wherever height changes, including at the
    room border where the drop is to the outside.

    Colour comes from the room's own art rather than the class palette, so the
    geometry carries TMC's tileset instead of a legend.

    The invariant at the end is what makes this checkable: total side-face area
    must equal the sum of every height drop times the cell width. If the mesh
    has gaps -- a wall with a missing side -- the two disagree, and no amount
    of looking at a wireframe reliably catches that.
    """
    r = load_room(Path(args.path))
    if not r.layers[args.layer]["present"] or not r.cells_w:
        sys.exit(f"layer {args.layer} not present in {args.path}")
    cls = classify_room(r, args.layer)

    sub = max(1, int(args.subdiv))
    lod = None
    if getattr(args, "lod", False):
        sub = max(sub, 4)
    if sub > 1:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import shading as SH
        d = SH.decompose_room(r, args.layer)
        if d is None:
            sys.exit("subdivision needs a v3+ dump with palettes")
        if getattr(args, "lod", False):
            det = detail_map(d[0], cls, r.cells_h, r.cells_w, args.lod_radius)
            lod = lod_map(det, args.lod_fine, args.lod_mid, sub,
                          args.lod_floor)
            u, c_ = np.unique(lod, return_counts=True)
            sh = {int(k): 100.0 * v / lod.size for k, v in zip(u, c_)}
            print("  LOD from measured detail: "
                  + ", ".join(f"{16 // k}px {sh[k]:.0f}%" for k in sorted(sh)))
        H, solid, col, CELL = subcell_fields(
            r, cls, d[0], sub, args.layer,
            load_height_overrides(getattr(args, 'heights', '')), lod)
        rows, cols = H.shape
    else:
        H = heightfield(r, cls)
        col = cell_colours(r, args.layer, r.cells_h, r.cells_w)
        solid = cls != CLASS_VOID
        CELL = 16
        rows, cols = r.cells_h, r.cells_w
    if getattr(args, "relief", False):
        H, solid = relief_field(r, cls, CELL, args.layer)
    OUTSIDE = args.outside          # height treated as beyond the room edge

    verts, faces, vcols = [], [], []

    def quad(p, c):
        base = len(verts) + 1
        verts.extend(p)
        vcols.extend([c] * 4)
        faces.append((base, base + 1, base + 2, base + 3))

    # ---- top faces, greedy-merged within each height band -------------------
    tops = 0
    for height in sorted(set(int(v) for v in np.unique(H))):
        mask = solid & (H == height)
        if not mask.any():
            continue
        for x, y, w, hgt in greedy_quads(mask):
            x0 = r.origin_x + x * CELL; x1 = x0 + w * CELL
            z0 = r.origin_y + y * CELL; z1 = z0 + hgt * CELL
            c = (col[y:y+hgt, x:x+w].reshape(-1, 3).mean(0)
                 if col is not None else np.array([0.6, 0.6, 0.6]))
            quad([(x0, height, z0), (x1, height, z0),
                  (x1, height, z1), (x0, height, z1)], c)
            tops += 1

    # ---- side faces at every height drop ------------------------------------
    # Not greedy-merged: correctness first. Merging sides is an optimisation
    # for later, and the area invariant below would still hold after it.
    sides = 0
    drop_area = 0
    for cy in range(rows):
        for cx in range(cols):
            if not solid[cy, cx]:
                continue
            hh = int(H[cy, cx])
            x0 = r.origin_x + cx * CELL; x1 = x0 + CELL
            z0 = r.origin_y + cy * CELL; z1 = z0 + CELL
            c = (col[cy, cx] if col is not None else np.array([0.5, 0.5, 0.5]))
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = cx + dx, cy + dz
                if 0 <= nx < cols and 0 <= nz < rows and solid[nz, nx]:
                    nh = int(H[nz, nx])
                else:
                    nh = OUTSIDE
                if nh >= hh:
                    continue
                drop_area += (hh - nh) * CELL
                sides += 1
                # Wind each face outward from the taller cell.
                if dx == 1:
                    p = [(x1, hh, z0), (x1, hh, z1), (x1, nh, z1), (x1, nh, z0)]
                elif dx == -1:
                    p = [(x0, hh, z1), (x0, hh, z0), (x0, nh, z0), (x0, nh, z1)]
                elif dz == 1:
                    p = [(x0, hh, z1), (x1, hh, z1), (x1, nh, z1), (x0, nh, z1)]
                else:
                    p = [(x1, hh, z0), (x0, hh, z0), (x0, nh, z0), (x1, nh, z0)]
                quad(p, c)

    # ---- the invariant ------------------------------------------------------
    # Computed a DIFFERENT way from the emitter on purpose. The first version
    # of this re-ran the emitter's own nested loop and compared the results,
    # which can only catch a bug in the bookkeeping, not a bug in the idea --
    # any shared misconception about neighbours or borders would agree with
    # itself and pass. This shifts the padded heightfield and sums positive
    # differences with array ops, so the two derivations share nothing but the
    # heightfield.
    Hs = np.where(solid, H, OUTSIDE).astype(np.int64)
    P = np.full((rows + 2, cols + 2), OUTSIDE, np.int64)
    P[1:-1, 1:-1] = Hs
    core = P[1:-1, 1:-1]
    expect = 0
    for shift in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb = np.roll(P, shift, axis=(0, 1))[1:-1, 1:-1]
        expect += int(np.clip(core - nb, 0, None)[solid].sum()) * CELL

    # ---- second collision layer, lifted --------------------------------------
    # TMC keeps a whole second level of the world on layer 1: canopies Link
    # walks under, bridge decks over water, roofs. It has its own collision
    # data -- 43.8% of cells in Hyrule Field, 7.7% in Minish Woods -- and
    # generating from layer 0 alone silently discarded all of it, which is why
    # bridges looked like paint on the water and trees had no crown.
    #
    # This is derived, not authored: the game itself says these cells are a
    # separate level. Only the LIFT is a choice.
    if args.overlay and len(r.layers) > 1 and r.layers[1]["present"]:
        L1 = r.layers[1]
        c1 = classify_room(r, 1)
        t1 = L1["tile"]; k1 = L1["collision"]
        col1 = cell_colours(r, 1, r.cells_h, r.cells_w)
        occupied = np.zeros((r.cells_h, r.cells_w), bool)
        for cy in range(r.cells_h):
            for cx in range(r.cells_w):
                occupied[cy, cx] = (t1[cy, cx] != 0) or (k1[cy, cx] != 0)
        lift = args.overlay_lift
        # The layer-0 surface underneath, in the same units as hh.
        H0_for_overlay = np.zeros((r.cells_h, r.cells_w), np.int64)
        for cy in range(r.cells_h):
            for cx in range(r.cells_w):
                H0_for_overlay[cy, cx] = CLASS_HEIGHT.get(cls[cy, cx], 0)
        hh_of = [[CLASS_HEIGHT.get(c1[cy, cx], 0) + lift
                  for cx in range(r.cells_w)] for cy in range(r.cells_h)]
        report_floaters(occupied[:r.cells_h, :r.cells_w], hh_of,
                        H0_for_overlay, r.cells_h, r.cells_w,
                        args.overlay_reach)
        n1 = 0
        # Tree crowns get their own surface (canopy_field); the flat plate
        # below stays for bridges, decks and roofs. A cell is crown when most
        # of what layer 1 draws in it is canopy.
        # Vertex-coloured output: 4px crown cells, each with its own colour
        # (2px quadruples the mesh for detail colour cannot show).
        crown = np.zeros((r.cells_h, r.cells_w), bool)
        cf = (canopy_field(r, step=4, lift=CLASS_HEIGHT[CLASS_GROUND] + lift)
              if getattr(args, "canopy", False) else None)
        if cf is not None:
            cmask, _top = canopy_mask(r)
            crown = crown_cells(r, cmask, _top)
            n1 += emit_canopy(quad, cf, r.origin_x, r.origin_y,
                              CLASS_HEIGHT[CLASS_GROUND] + lift, step=4)
        for cy in range(r.cells_h):
            for cx in range(r.cells_w):
                if not occupied[cy, cx] or crown[cy, cx]:
                    continue
                hh = CLASS_HEIGHT.get(c1[cy, cx], 0) + lift
                x0 = r.origin_x + cx * 16; x1 = x0 + 16
                z0 = r.origin_y + cy * 16; z1 = z0 + 16
                c = (col1[cy, cx] if col1 is not None
                     else np.array([0.45, 0.55, 0.4]))
                quad([(x0, hh, z0), (x1, hh, z0),
                      (x1, hh, z1), (x0, hh, z1)], c)
                n1 += 1
                # Skirts only where the overlay ENDS, so a continuous canopy
                # stays a canopy instead of becoming a grid of boxes.
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, nz = cx + dx, cy + dz
                    inside = (0 <= nx < r.cells_w and 0 <= nz < r.cells_h
                              and occupied[nz, nx])
                    if inside:
                        continue
                    b, _sup = overlay_skirt_bottom(
                        hh, H0_for_overlay, occupied, cy, cx, dz, dx,
                        r.cells_h, r.cells_w, args.overlay_reach,
                        args.overlay_skirt)
                    if dx == 1:
                        p_ = [(x1,hh,z0),(x1,hh,z1),(x1,b,z1),(x1,b,z0)]
                    elif dx == -1:
                        p_ = [(x0,hh,z1),(x0,hh,z0),(x0,b,z0),(x0,b,z1)]
                    elif dz == 1:
                        p_ = [(x0,hh,z1),(x1,hh,z1),(x1,b,z1),(x0,b,z1)]
                    else:
                        p_ = [(x1,hh,z0),(x0,hh,z0),(x0,b,z0),(x1,b,z0)]
                    quad(p_, c)
                    n1 += 1
        print(f"  overlay layer 1: {int(occupied.sum())} cells "
              f"({int(crown.sum())} tree crown), {n1} quads, lifted {lift}px")

    out = Path(args.out)
    with out.open("w") as f:
        f.write(f"# {r} layer {args.layer}; world-pixel units, +X east +Y up +Z south\n")
        for v, c in zip(verts, vcols):
            f.write(f"v {v[0]:.1f} {v[1]:.1f} {v[2]:.1f} "
                    f"{c[0]:.3f} {c[1]:.3f} {c[2]:.3f}\n")
        for a, b, cc, d in faces:
            f.write(f"f {a} {b} {cc} {d}\n")

    print(f"{r} -> {out}")
    print(f"  subdiv {sub} ({CELL}px voxels), {tops} top quads (greedy), "
          f"{sides} side quads, {len(verts)} vertices")
    print(f"  colour source: {'room art' if col is not None else 'flat grey (no art)'}")
    ok = (drop_area == expect)
    print(f"  side-area invariant: emitted {drop_area} vs expected {expect}  "
          f"{'OK' if ok else 'MISMATCH'}")
    if not ok:
        sys.exit("side faces do not account for every height drop -- "
                 "the mesh has holes")


CLASS_CODE = {CLASS_VOID: 0, CLASS_GROUND: 1, CLASS_WATER: 2, CLASS_WALL: 3,
              CLASS_LEDGE: 4, CLASS_HOLE: 5}
CODE_CLASS = {v: k for k, v in CLASS_CODE.items()}


def cmd_world(args):
    """Stitch every room of an area into one map, and check that it is sound.

    Rooms carry origin_x/origin_y, so assembly is per AREA -- a dungeon and the
    overworld do not share a coordinate space. Placing tiles by origin is easy;
    the part worth building carefully is knowing whether the result is right.

    Three things are measured, and only the first is obvious:

      coverage   how much of the area's bounding box any room claims. Gaps are
                 normal (areas are not rectangles) but a huge gap means rooms
                 are missing from the harvest.

      overlap    cells claimed by more than one room. TMC rooms do abut and
                 sometimes share edge columns, so a little is expected.

      conflict   cells where overlapping rooms DISAGREE about the class. This
                 is the one that matters. Rooms are captured independently, so
                 if two of them describe the same world cell differently, then
                 either origin_x/origin_y do not mean what this assumes, or the
                 capture caught the same room in two states. Either way the
                 stitched world is not trustworthy, and no picture of it would
                 show the problem -- the later room just overwrites the earlier.
    """
    rooms = list(iter_rooms(Path(args.dumps)))
    if not rooms:
        sys.exit(f"no .tmcr files under {args.dumps}")

    by_area = {}
    for r in rooms:
        by_area.setdefault(r.area, []).append(r)

    wanted = ([int(a, 0) for a in args.area.split(",")] if args.area
              else sorted(by_area))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'area':>5} {'rooms':>5} {'cells W x H':>14} {'coverage':>9} "
          f"{'overlap':>8} {'conflict':>9}")
    print("  " + "-" * 60)

    totals = dict(rooms=0, cov=0, box=0, ov=0, cf=0, areas=0)
    worst = []
    stacked = []
    mixed = []

    for area in wanted:
        rs = by_area.get(area)
        if not rs:
            continue
        rs = [r for r in rs if r.cells_w and r.cells_h
              and r.layers[args.layer]["present"]]
        if not rs:
            continue

        # Tiled or stacked? Overworld areas place rooms at distinct origins
        # within one coordinate space. Interiors do not: each house or dungeon
        # room is its own space starting at (0,0), so several rooms share an
        # origin and describe DIFFERENT places. Stitching those together is a
        # category error -- it reports conflicts that are not errors, and the
        # signature is unmistakable (overlap equal to the whole grid).
        # Tiled, stacked, or mixed?
        #
        # Overworld rooms tile: their rectangles abut, sometimes sharing a
        # narrow strip at the seam. Interior rooms do not tile at all -- each
        # is its own space near (0,0), so their rectangles sit on top of each
        # other.
        #
        # An earlier version tested for an identical origin, which was too
        # narrow: area 104's rooms sit at (6,1), (0,0), (0,0) and (0,0) with
        # rectangles overlapping 40-100%, so one of them slipped through and
        # its 104 disagreeing cells were reported as conflicts in a stitch
        # that should never have been attempted. What separates the two cases
        # is not the origin but HOW MUCH the rectangles overlap: a seam is a
        # few percent, a co-located room is most of its own area.
        def _rect(rm):
            x, y = rm.origin_x // 16, rm.origin_y // 16
            return x, y, x + rm.cells_w, y + rm.cells_h

        def _ovl(a, b):
            ax0, ay0, ax1, ay1 = _rect(a)
            bx0, by0, bx1, by1 = _rect(b)
            w = min(ax1, bx1) - max(ax0, bx0)
            h = min(ay1, by1) - max(ay0, by0)
            if w <= 0 or h <= 0:
                return 0.0
            return (w * h) / max(1, min((ax1-ax0)*(ay1-ay0), (bx1-bx0)*(by1-by0)))

        parent = list(range(len(rs)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                if _ovl(rs[i], rs[j]) > args.colocated:
                    parent[find(i)] = find(j)
        groups = {}
        for i, rm in enumerate(rs):
            groups.setdefault(find(i), []).append(rm)
        alts = sum(len(v) - 1 for v in groups.values())
        if alts and len(groups) == 1:
            stacked.append((area, len(rs), len(groups), alts))
            print(f"  {area:3d}  {len(rs):5d}  {'stacked':>14}  "
                  f"every room overlaps the others -- independent spaces, "
                  f"not tiles")
            continue
        if alts:
            mixed.append((area, len(rs), len(groups), alts))
        rs = [v[0] for v in groups.values()]

        x0 = min(r.origin_x // 16 for r in rs)
        y0 = min(r.origin_y // 16 for r in rs)
        x1 = max(r.origin_x // 16 + r.cells_w for r in rs)
        y1 = max(r.origin_y // 16 + r.cells_h for r in rs)
        W, H = x1 - x0, y1 - y0
        if W <= 0 or H <= 0 or W * H > args.max_cells:
            print(f"  {area:3d}  {len(rs):5d}  {W:6d} x {H:<5d}  "
                  f"(skipped: {W*H} cells)")
            continue

        grid = np.zeros((H, W), np.uint8)       # class code, 0 = unclaimed
        count = np.zeros((H, W), np.uint16)     # how many rooms claim it
        conflict = np.zeros((H, W), bool)

        for r in rs:
            cls = classify_room(r, args.layer)
            ox, oy = r.origin_x // 16 - x0, r.origin_y // 16 - y0
            for cy in range(r.cells_h):
                for cx in range(r.cells_w):
                    gx, gy = ox + cx, oy + cy
                    if not (0 <= gx < W and 0 <= gy < H):
                        continue
                    code = CLASS_CODE.get(cls[cy, cx], 0)
                    if count[gy, gx] and grid[gy, gx] != code:
                        conflict[gy, gx] = True
                    grid[gy, gx] = code
                    count[gy, gx] += 1

        covered = int((count > 0).sum())
        overlap = int((count > 1).sum())
        confl = int(conflict.sum())
        box = W * H
        note = f"  (+{alts} alt)" if alts else ""
        print(f"  {area:3d}  {len(rs):5d}  {W:6d} x {H:<5d}  "
              f"{100.0*covered/box:7.1f}%  {overlap:8d}  {confl:9d}{note}")

        totals["rooms"] += len(rs); totals["cov"] += covered
        totals["box"] += box; totals["ov"] += overlap
        totals["cf"] += confl; totals["areas"] += 1
        if overlap:
            worst.append((confl / max(overlap, 1), area, confl, overlap))

        if args.png:
            from PIL import Image
            img = Image.new("RGB", (W, H), (12, 12, 16))
            px = img.load()
            for gy in range(H):
                for gx in range(W):
                    if conflict[gy, gx]:
                        px[gx, gy] = (255, 0, 160)      # conflicts shout
                    elif count[gy, gx]:
                        px[gx, gy] = CLASS_COLOUR[CODE_CLASS[grid[gy, gx]]]
            if args.scale != 1:
                img = img.resize((W * args.scale, H * args.scale), Image.NEAREST)
            img.save(out_dir / f"area_{area:02d}.png")

    if stacked:
        n_rooms = sum(x[1] for x in stacked)
        print(f"\n  {len(stacked)} area(s), {n_rooms} rooms, are STACKED -- "
              f"rooms sharing an origin, i.e. independent interiors rather "
              f"than tiles of one map. Excluded from the stitch:")
        for area, nr, no, al in stacked:
            print(f"    area {area:3d}: {nr} rooms at {no} distinct origin(s)")
    if mixed:
        tot_alt = sum(x[3] for x in mixed)
        print(f"\n  {len(mixed)} area(s) are MIXED: most rooms tile, but "
              f"{tot_alt} sit on top of another room rather than beside it. "
              f"One per location was stitched; the rest are alternates -- most "
              f"likely upper FLOORS sharing a footprint, which need separating "
              f"in Y rather than merging into the floor below:")
        for area, nr, no, al in mixed:
            print(f"    area {area:3d}: {nr} rooms, {no} location(s), "
                  f"{al} alternate(s)")

    print(f"\n  {totals['areas']} tiled areas, {totals['rooms']} rooms, "
          f"{100.0*totals['cov']/max(totals['box'],1):.1f}% of bounding boxes covered")
    print(f"  overlapping cells {totals['ov']}, of which conflicting "
          f"{totals['cf']} "
          f"({100.0*totals['cf']/max(totals['ov'],1):.1f}%)")

    if worst:
        worst.sort(reverse=True)
        bad = [w for w in worst if w[2]]
        if bad:
            print("\n  areas where overlapping rooms DISAGREE:")
            for frac, area, cf, ov in bad[:args.show]:
                print(f"    area {area:3d}  {cf} conflicting of {ov} "
                      f"overlapping ({frac*100:.0f}%)")

    print()
    if totals["ov"] == 0:
        print(f"  -> across {totals['areas']} tiled areas no two rooms claim "
              f"the same cell, so there is nothing to disagree about. TMC "
              f"rooms abut rather than overlap, so this is the expected shape "
              f"of a correct stitch -- confirm placement in a PNG, since "
              f"rooms all landing at one origin would also print zero.")
    elif totals["cf"] == 0:
        print("  -> rooms overlap and AGREE everywhere they do. The origin "
              "model is consistent across independently captured rooms, which "
              "is the strongest evidence available that the stitch is correct.")
    else:
        frac = 100.0 * totals["cf"] / totals["ov"]
        print(f"  -> {totals['cf']} disagreeing cells in {totals['ov']} "
              f"overlapping ({frac:.1f}%). With co-located rooms excluded, "
              f"what remains are SEAMS: narrow strips two adjacent rooms both "
              f"claim and render differently, which is a real property of the "
              f"game rather than a stitching error. Magenta in the PNGs shows "
              f"where. Pick one side per seam when building geometry.")


def assign_levels(rs, colocated=0.25):
    """Split an area's rooms into LEVELS of mutually non-overlapping rooms.

    Rooms that share a footprint are different places -- in TMC, different
    floors of one dungeon. No floor index exists in the dump, so it has to be
    derived, and the derivation is simply: rooms that can coexist in the plane
    belong on one floor; a room that collides with everything already placed
    starts a new one. That is greedy colouring of the overlap graph.

    This replaces an earlier union-find grouping that was wrong for a subtle
    reason: it merged transitively, so one large room overlapping three small
    tiled rooms chained all four into a single "co-located" group even though
    the small ones only abut each other. Levels are about pairwise collision,
    not connectivity.

    Returns a list of lists, lowest level first, each internally overlap-free.
    """
    def rect(r):
        x, y = r.origin_x // 16, r.origin_y // 16
        return x, y, x + r.cells_w, y + r.cells_h

    def ovl(a, b):
        ax0, ay0, ax1, ay1 = rect(a)
        bx0, by0, bx1, by1 = rect(b)
        w = min(ax1, bx1) - max(ax0, bx0)
        h = min(ay1, by1) - max(ay0, by0)
        if w <= 0 or h <= 0:
            return 0.0
        return w * h / max(1, min((ax1 - ax0) * (ay1 - ay0),
                                  (bx1 - bx0) * (by1 - by0)))

    # Largest rooms first: a big room should anchor the ground floor rather
    # than be exiled upstairs by a small one that happened to be placed first.
    order = sorted(rs, key=lambda r: -(r.cells_w * r.cells_h))
    levels = []
    for r in order:
        for lv in levels:
            if all(ovl(r, o) <= colocated for o in lv):
                lv.append(r)
                break
        else:
            levels.append([r])
    return levels


def load_height_overrides(path):
    """Read 'area material height' lines -- the hand-tuned part.

    Subdivision fixes horizontal resolution: boundaries follow the art instead
    of the 16px collision grid. It does NOT invent vertical detail, because
    heights still come from six class values, and no amount of resolution
    turns six numbers into the shape of an archway.

    That last step cannot be derived, because the information is not in the
    dump: collision says "solid", and the tile art depicts an arch without ever
    stating how tall it is. So this is the authored layer -- one line per
    material, the place where "this hedge is 24px, that archway is 40px and
    hollow underneath" gets written down. `shading.py analyse --table` emits
    the starting list with each material's purity, worst first.
    """
    out = {}
    if not path:
        return out
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        f = line.split()
        if len(f) >= 3:
            try:
                out[(int(f[0]), int(f[1]))] = int(f[2])
            except ValueError:
                continue
    return out


def material_heights(r, cls, mat, layer_index=0, overrides=None):
    """Map each material to a height, via the class it usually coincides with.

    Collision is per 16px cell; materials are per pixel and about 90% pure
    against the classifier. So a material inherits the height of whatever class
    it mostly sits under, and can then be applied at FINER than cell
    resolution. That is what lets a one-cell object -- a sign, a bridge plank,
    the lip of a fountain -- stop being quantised into a 16px block with its
    neighbours.
    """
    from collections import Counter, defaultdict
    votes = defaultdict(Counter)
    ch = min(r.cells_h, mat.shape[0] // 16)
    cw = min(r.cells_w, mat.shape[1] // 16)
    for cy in range(ch):
        for cx in range(cw):
            blk = mat[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
            v, c = np.unique(blk[blk >= 0], return_counts=True)
            if not len(v):
                continue
            k = cls[cy, cx]
            for mi, n in zip(v, c):
                votes[int(mi)][k] += int(n)
    out = {}
    for mi, c in votes.items():
        out[mi] = CLASS_HEIGHT.get(c.most_common(1)[0][0], 0)
    for (area, mi), h in (overrides or {}).items():
        if area == r.area:
            out[int(mi)] = int(h)
    return out


def detail_map(mat, cls, cells_h, cells_w, radius=1):
    """Per-cell structural detail, measured over a cell and the cells it touches.

    A uniform subdivision is wrong in both directions: 4px voxels across open
    grass multiply the mesh for nothing, while 16px voxels destroy a bridge
    that is three pixels of plank and two of gap.

    The first version of this counted material BOUNDARIES per cell, and it
    failed in exactly the way busy scenes always fail here: grass tufts and
    dithering are dense in edges while carrying no structure, so open field
    scored as high as a bridge. Edge count measures texture, not shape.

    What separates them is COMPOSITION. Grass is one material plus speckle --
    its dominant material covers ~90% of the cell. A bridge is planks and gaps
    in comparable proportion, and a bridge cell also neighbours water it does
    not resemble. So:

      mix       1 - (largest material's share of the cell). Speckle on a field
                stays low; two materials in real proportion score high.
      contrast  how much the cell's dominant material differs from its
                neighbours' dominants -- a thin structure crossing something
                else, which is what a bridge or a fence IS.

    Both are cheap, both come from the material map, and neither rewards noise.
    """
    H = min(cells_h, mat.shape[0] // 16)
    W = min(cells_w, mat.shape[1] // 16)
    mix = np.zeros((H, W), np.float32)
    dom = np.full((H, W), -1, np.int64)
    for cy in range(H):
        for cx in range(W):
            blk = mat[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
            vals = blk[blk >= 0]
            if vals.size == 0:
                continue
            v, c = np.unique(vals, return_counts=True)
            top = c.max()
            dom[cy, cx] = int(v[c.argmax()])
            mix[cy, cx] = 1.0 - (top / float(vals.size))

    contrast = np.zeros((H, W), np.float32)
    for cy in range(H):
        for cx in range(W):
            if dom[cy, cx] < 0:
                continue
            diff = tot = 0
            for ny in range(max(0, cy - radius), min(H, cy + radius + 1)):
                for nx in range(max(0, cx - radius), min(W, cx + radius + 1)):
                    if (ny, nx) == (cy, cx) or dom[ny, nx] < 0:
                        continue
                    tot += 1
                    diff += (dom[ny, nx] != dom[cy, cx])
            contrast[cy, cx] = diff / float(tot) if tot else 0.0

    def norm(a):
        lo, hi = float(a.min()), float(a.max())
        return (a - lo) / (hi - lo) if hi > lo else np.zeros_like(a)

    # Both terms must be meaningful for a cell to refine: a speckled field has
    # contrast without mix, a dithered gradient has mix without contrast.
    # Multiplying demands structure AND a neighbourhood that disagrees.
    return np.sqrt(norm(mix) * norm(contrast))


def lod_map(detail, fine=0.55, mid=0.28, max_sub=4, floor=2):
    """Detail score -> subdivision factor per cell, never below `floor`.

    The floor is the important parameter, and getting that wrong was the
    original error in this function. It was written to SAVE voxels on
    "uninteresting" terrain, which is the wrong objective: every surface here
    is being shown from angles it was never drawn for, so grass needs enough
    geometry to read as grass from the side just as much as a bridge needs
    enough to read as planks. Nothing is uninteresting; some things are finer.

    So the floor sets the resolution everything gets, and the detail score only
    ever raises a cell ABOVE it. Powers of two only, so every level lands on
    the same grid, neighbouring cells of different level still share edges
    exactly, and there are no cracks to stitch.
    """
    lod = np.full(detail.shape, max(1, int(floor)), np.int32)
    lod = np.maximum(lod, np.where(detail >= mid, 2, 1).astype(np.int32))
    lod = np.maximum(lod, np.where(detail >= fine, max_sub, 1).astype(np.int32))
    return np.minimum(lod, max_sub)


def subcell_fields(r, cls, mat, subdiv, layer_index=0, overrides=None, lod=None):
    """Height / solidity / colour at 16//subdiv pixel resolution.

    subdiv=1 reproduces the per-cell behaviour exactly; 2 gives 8px voxels and
    4 gives 4px. Each sub-cell takes the height of ITS OWN dominant material
    rather than its parent cell's class, so geometry follows the art instead of
    the collision grid.
    """
    step = 16 // subdiv
    ch = min(r.cells_h, mat.shape[0] // 16)
    cw = min(r.cells_w, mat.shape[1] // 16)
    SH_ = ch * subdiv
    SW_ = cw * subdiv
    H = np.zeros((SH_, SW_), np.int32)
    solid = np.zeros((SH_, SW_), bool)
    col = np.full((SH_, SW_, 3), 0.5, np.float32)
    mh = material_heights(r, cls, mat, layer_index, overrides)
    art = room_art_rgb(r, layer_index)

    for sy in range(SH_):
        for sx in range(SW_):
            cy, cx = sy // subdiv, sx // subdiv
            if cls[cy, cx] == CLASS_VOID:
                continue
            y0, x0 = sy * step, sx * step
            # Coarser LOD: widen the sample to the block this sub-cell belongs
            # to, so the whole block resolves to one value. The grid stays
            # uniform and the greedy mesher merges the result back into large
            # quads by itself -- adaptive detail without a hierarchical mesh.
            if lod is not None:
                l = int(lod[cy, cx])
                if l < subdiv:
                    grp = max(1, subdiv // max(1, l))
                    gy = (sy // grp) * grp
                    gx = (sx // grp) * grp
                    y0, x0 = gy * step, gx * step
                    blk = mat[y0:y0 + step * grp, x0:x0 + step * grp]
                else:
                    blk = mat[y0:y0 + step, x0:x0 + step]
            else:
                blk = mat[y0:y0 + step, x0:x0 + step]
            v, c = np.unique(blk[blk >= 0], return_counts=True)
            if len(v):
                dom = int(v[c.argmax()])
                H[sy, sx] = mh.get(dom, CLASS_HEIGHT.get(cls[cy, cx], 0))
            else:
                H[sy, sx] = CLASS_HEIGHT.get(cls[cy, cx], 0)
            solid[sy, sx] = True
            if art is not None and y0 < art.shape[0] and x0 < art.shape[1]:
                ay0, ax0 = sy * step, sx * step
                col[sy, sx] = art[ay0:ay0 + step,
                                  ax0:ax0 + step].reshape(-1, 3).mean(0) / 255.0
    return H, solid, col, step


def area_albedo(rs, layer_index=0):
    """Stitch an area's ALBEDO into one image, for engine lighting.

    The art as drawn already carries hand shading whose direction is not
    recoverable (measured concentration R = 0.019 -- the gradient histogram is
    symmetric, which is outlines and texture, not a light). Lighting that image
    would stack the engine's light on top of one that points nowhere.

    Albedo is the material layer with the artist's shading divided out, so it
    is the correct thing to hand a real light source.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import shading as SH
    except Exception:
        return None
    xs0 = min(r.origin_x for r in rs); ys0 = min(r.origin_y for r in rs)
    xs1 = max(r.origin_x + r.cells_w * 16 for r in rs)
    ys1 = max(r.origin_y + r.cells_h * 16 for r in rs)
    W, H = xs1 - xs0, ys1 - ys0
    if W <= 0 or H <= 0 or W * H > 64_000_000:
        return None
    canvas = np.zeros((H, W, 3), np.uint8)
    for r in rs:
        banks = SH.room_palette_banks(r)
        d = SH.decompose_room(r, layer_index)
        if banks is None or d is None:
            continue
        mat, _ = d
        ref = SH.ramp_reference(banks, "bright")
        a = np.zeros((mat.shape[0], mat.shape[1], 3), np.uint8)
        for m in np.unique(mat):
            if m < 0:
                continue
            c = ref.get(int(m))
            if c is not None:
                a[mat == m] = c
        ox = r.origin_x - xs0; oy = r.origin_y - ys0
        h = min(a.shape[0], H - oy); w = min(a.shape[1], W - ox)
        if h > 0 and w > 0:
            canvas[oy:oy + h, ox:ox + w] = a[:h, :w]
    return canvas


def area_texture(rs, layer_index=0, pad=0):
    """Stitch an area's room art into one image, placed by room origin.

    This image is the texture atlas, and choosing it that way is what keeps UV
    mapping compatible with greedy meshing. The alternative -- packing each
    tile into an atlas -- breaks the moment a merged quad spans two cells,
    because their tiles are no longer adjacent in atlas space. Here a quad's
    UVs are just its world position normalised, so a 1x1 quad and a 30x12
    merged quad are handled by the same two lines of arithmetic.

    Returns (image_array HxWx3 uint8, ox, oy, W, H) in world-pixel units.
    """
    from PIL import Image
    xs0 = min(r.origin_x for r in rs)
    ys0 = min(r.origin_y for r in rs)
    xs1 = max(r.origin_x + r.cells_w * 16 for r in rs)
    ys1 = max(r.origin_y + r.cells_h * 16 for r in rs)
    W, H = xs1 - xs0 + pad * 2, ys1 - ys0 + pad * 2
    if W <= 0 or H <= 0 or W * H > 64_000_000:
        return None
    canvas = np.zeros((H, W, 3), np.uint8)
    for r in rs:
        a = room_art_rgb(r, layer_index)
        if a is None:
            continue
        ox = r.origin_x - xs0 + pad
        oy = r.origin_y - ys0 + pad
        h, w = a.shape[0], a.shape[1]
        h = min(h, H - oy); w = min(w, W - ox)
        if h <= 0 or w <= 0:
            continue
        canvas[oy:oy + h, ox:ox + w] = a[:h, :w].astype(np.uint8)
    return canvas, xs0, ys0, W, H


def cmd_worldgen(args):
    """Emit geometry for whole areas, with floors separated in Y.

    Rooms that share a footprint are stacked into levels (see assign_levels)
    and each level is lifted by --floor-height, so an upper floor sits above
    the one below instead of being merged into it or silently dropped.

    Two self-checks, both of which can fail:
      - no two rooms on one level may overlap (the colouring's own postcondition)
      - side-face area must equal the sum of height drops, as in `voxel`
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    rooms = list(iter_rooms(Path(args.dumps)))
    if not rooms:
        sys.exit(f"no .tmcr files under {args.dumps}")
    by_area = {}
    for r in rooms:
        if r.cells_w and r.cells_h and r.layers[args.layer]["present"]:
            by_area.setdefault(r.area, []).append(r)

    wanted = ([int(a, 0) for a in args.area.split(",")] if args.area
              else sorted(by_area))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    CELL = 16
    grand = dict(areas=0, rooms=0, levels=0, quads=0, bad=0)
    total_rooms = sum(len(by_area.get(a, [])) for a in wanted)
    prog = Progress(max(1, total_rooms), label="worldgen", step=2)
    print(f"{'area':>5} {'rooms':>6} {'levels':>7} {'top':>7} {'side':>7} "
          f"{'invariant':>10}")
    print("  " + "-" * 52)

    for area in wanted:
        rs = by_area.get(area)
        if not rs:
            continue
        levels = assign_levels(rs, args.colocated)

        # postcondition: each level is internally overlap-free
        def rect(r):
            x, y = r.origin_x // 16, r.origin_y // 16
            return x, y, x + r.cells_w, y + r.cells_h
        clash = 0
        for lv in levels:
            for i in range(len(lv)):
                for j in range(i + 1, len(lv)):
                    ax0, ay0, ax1, ay1 = rect(lv[i])
                    bx0, by0, bx1, by1 = rect(lv[j])
                    w = min(ax1, bx1) - max(ax0, bx0)
                    h = min(ay1, by1) - max(ay0, by0)
                    if w > 0 and h > 0:
                        a = min((ax1-ax0)*(ay1-ay0), (bx1-bx0)*(by1-by0))
                        if (w * h) / max(1, a) > args.colocated:
                            clash += 1
        tex = area_texture(rs, args.layer) if args.texture else None
        project = not getattr(args, "footprint", False)
        verts, faces, vcols, uvs = [], [], [], []

        def quad(p, c, uv=None):
            base = len(verts) + 1
            verts.extend(p)
            vcols.extend([c] * 4)
            if uv is not None:
                ub = len(uvs) + 1
                uvs.extend(uv)
                faces.append(((base, ub), (base + 1, ub + 1),
                              (base + 2, ub + 2), (base + 3, ub + 3)))
            else:
                faces.append((base, base + 1, base + 2, base + 3))

        def uv_top(x0, x1, z0, z1):
            """UVs for a horizontal quad: world position, normalised.

            Works for any quad size, which is why the atlas is the stitched
            area rather than a packed tile sheet -- a greedy-merged 30x12 quad
            maps as simply as a 1x1 one.
            """
            if tex is None:
                return None
            _, tx, ty, TW, TH = tex
            u0 = (x0 - tx) / TW; u1 = (x1 - tx) / TW
            v0 = 1.0 - (z0 - ty) / TH; v1 = 1.0 - (z1 - ty) / TH
            return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]

        def uv_proj(pts, base):
            """UVs by projecting the art from the game's camera.

            A point at height y over (x, z) takes the pixel drawn at
            (x, z - (y - base)), base being the floor the room was drawn
            on. A raised top takes the drawing that many rows north, which
            is where its top is drawn; a south-facing wall takes the band
            drawn above its base, which is its front face -- the art the
            footprint rule pasted onto block tops.
            """
            if tex is None:
                return None
            _, tx, ty, TW, TH = tex
            return [((x - tx) / TW, 1.0 - ((z - (y - base)) - ty) / TH)
                    for x, y, z in pts]

        def uv_side(ax0, ax1, az0, az1):
            """UVs for a vertical face.

            A top-down tileset has no art for the sides of things, so there is
            nothing to look up -- the choice is what to invent. This samples
            the footprint the face belongs to, so a wall reads as an extrusion
            of its own top. That is the standard voxel-art answer and it keeps
            materials consistent; it is an invention either way, and pretending
            otherwise would be the mistake.
            """
            if tex is None:
                return None
            _, tx, ty, TW, TH = tex
            u0 = (ax0 - tx) / TW; u1 = (ax1 - tx) / TW
            v0 = 1.0 - (az0 - ty) / TH; v1 = 1.0 - (az1 - ty) / TH
            if abs(u1 - u0) < 1e-9:
                u1 = u0 + (16.0 / TW)
            if abs(v1 - v0) < 1e-9:
                v1 = v0 - (16.0 / TH)
            return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]

        tops = sides = floaters = 0
        drop = expect = 0
        for li, lv in enumerate(levels):
            lift = li * args.floor_height
            for r in lv:
                prog.update(1, f"area {area} room {r.room}")
                cls = classify_room(r, args.layer)
                ox, oy = r.origin_x, r.origin_y
                subn = max(1, int(getattr(args, "subdiv", 1)))
                if getattr(args, "lod", False):
                    subn = max(subn, 4)
                if subn > 1:
                    sys.path.insert(0, str(Path(__file__).resolve().parent))
                    import shading as _SH
                    _d = _SH.decompose_room(r, args.layer)
                    if _d is None:
                        subn = 1
                if subn > 1:
                    _lod = None
                    if getattr(args, "lod", False):
                        _det = detail_map(_d[0], cls, r.cells_h, r.cells_w,
                                          args.lod_radius)
                        _lod = lod_map(_det, args.lod_fine, args.lod_mid,
                                       subn, args.lod_floor)
                    H, solid, col, CELL = subcell_fields(
                        r, cls, _d[0], subn, args.layer,
                        load_height_overrides(getattr(args, "heights", "")),
                        _lod)
                    rows_, cols_ = H.shape
                else:
                    col = cell_colours(r, args.layer, r.cells_h, r.cells_w)
                    H = heightfield(r, cls)
                    solid = cls != CLASS_VOID
                    CELL = 16
                    rows_, cols_ = r.cells_h, r.cells_w
                if getattr(args, "relief", False):
                    H, solid = relief_field(r, cls, CELL, args.layer)
                for height in sorted(set(int(v) for v in np.unique(H))):
                    mask = solid & (H == height)
                    if not mask.any():
                        continue
                    for x, y, w, hh in greedy_quads(mask):
                        x0 = ox + x * CELL; x1 = x0 + w * CELL
                        z0 = oy + y * CELL; z1 = z0 + hh * CELL
                        yv = height + lift
                        c = (col[y:y+hh, x:x+w].reshape(-1, 3).mean(0)
                             if col is not None else np.array([.6, .6, .6]))
                        pts = [(x0, yv, z0), (x1, yv, z0),
                               (x1, yv, z1), (x0, yv, z1)]
                        quad(pts, c, uv_proj(pts, lift) if project
                             else uv_top(x0, x1, z0, z1))
                        tops += 1
                for cy in range(rows_):
                    for cx in range(cols_):
                        if not solid[cy, cx]:
                            continue
                        hgt = int(H[cy, cx])
                        x0 = ox + cx * CELL; x1 = x0 + CELL
                        z0 = oy + cy * CELL; z1 = z0 + CELL
                        c = (col[cy, cx] if col is not None
                             else np.array([.5, .5, .5]))
                        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            nx, nz = cx + dx, cy + dz
                            nh = (int(H[nz, nx])
                                  if 0 <= nx < cols_ and 0 <= nz < rows_
                                  and solid[nz, nx] else args.outside)
                            if nh >= hgt:
                                continue
                            drop += (hgt - nh) * CELL
                            sides += 1
                            a, b = hgt + lift, nh + lift
                            if dx == 1:
                                p = [(x1,a,z0),(x1,a,z1),(x1,b,z1),(x1,b,z0)]
                            elif dx == -1:
                                p = [(x0,a,z1),(x0,a,z0),(x0,b,z0),(x0,b,z1)]
                            elif dz == 1:
                                p = [(x0,a,z1),(x1,a,z1),(x1,b,z1),(x0,b,z1)]
                            else:
                                p = [(x1,a,z0),(x0,a,z0),(x0,b,z0),(x1,b,z0)]
                            quad(p, c, uv_proj(p, lift) if project
                                 else uv_side(x0, x1, z0, z1))
                # Layer 1: the world's second level -- canopies, bridge decks,
                # roofs. It carries its own collision data, so generating from
                # layer 0 alone drops it entirely.
                if args.overlay and len(r.layers) > 1 and r.layers[1]["present"]:
                    L1 = r.layers[1]
                    c1 = classify_room(r, 1)
                    col1 = cell_colours(r, 1, r.cells_h, r.cells_w)
                    occ = (L1["tile"][:r.cells_h, :r.cells_w] != 0) | \
                          (L1["collision"][:r.cells_h, :r.cells_w] != 0)
                    H0_ov = np.zeros((r.cells_h, r.cells_w), np.int64)
                    for cy in range(r.cells_h):
                        for cx in range(r.cells_w):
                            H0_ov[cy, cx] = (CLASS_HEIGHT.get(cls[cy, cx], 0)
                                             + lift)
                    hh_ov = [[CLASS_HEIGHT.get(c1[cy, cx], 0) + lift
                              + args.overlay_lift
                              for cx in range(r.cells_w)]
                             for cy in range(r.cells_h)]
                    floaters += report_floaters(
                        occ, hh_ov, H0_ov, r.cells_h, r.cells_w,
                        args.overlay_reach, f"area {r.area} room {r.room} overlay")
                    # Tree crowns (--canopy): shaped surfaces instead of
                    # flat plates, projected like the terrain. Bridges,
                    # decks and roofs keep their plates for now.
                    crown = np.zeros((r.cells_h, r.cells_w), bool)
                    if getattr(args, "canopy", False):
                        cbase = CLASS_HEIGHT[CLASS_GROUND] + args.overlay_lift
                        cf = canopy_field(r, step=4, lift=cbase)
                        if cf is not None:
                            cmask, _top = canopy_mask(r)
                            crown = crown_cells(r, cmask, _top)
                            q = emit_canopy(
                                quad, cf, ox, oy, lift + cbase, step=4,
                                uvf=(lambda pts, _b=lift: uv_proj(pts, _b))
                                if tex is not None else None,
                                merge=tex is not None)
                            tops += q
                    for cy in range(r.cells_h):
                        for cx in range(r.cells_w):
                            if not occ[cy, cx] or crown[cy, cx]:
                                continue
                            hh = CLASS_HEIGHT.get(c1[cy, cx], 0) + lift + args.overlay_lift
                            ax0 = ox + cx * CELL; ax1 = ax0 + CELL
                            az0 = oy + cy * CELL; az1 = az0 + CELL
                            cc = (col1[cy, cx] if col1 is not None
                                  else np.array([0.45, 0.55, 0.4]))
                            quad([(ax0, hh, az0), (ax1, hh, az0),
                                  (ax1, hh, az1), (ax0, hh, az1)], cc,
                                 uv_top(ax0, ax1, az0, az1))
                            tops += 1
                            for dxx, dzz in ((1,0),(-1,0),(0,1),(0,-1)):
                                nxx, nzz = cx + dxx, cy + dzz
                                if 0 <= nxx < r.cells_w and 0 <= nzz < r.cells_h \
                                        and occ[nzz, nxx]:
                                    continue
                                bb, _sup = overlay_skirt_bottom(
                                    hh, H0_ov, occ, cy, cx, dzz, dxx,
                                    r.cells_h, r.cells_w,
                                    args.overlay_reach, args.overlay_skirt)
                                if dxx == 1:
                                    pp = [(ax1,hh,az0),(ax1,hh,az1),(ax1,bb,az1),(ax1,bb,az0)]
                                elif dxx == -1:
                                    pp = [(ax0,hh,az1),(ax0,hh,az0),(ax0,bb,az0),(ax0,bb,az1)]
                                elif dzz == 1:
                                    pp = [(ax0,hh,az1),(ax1,hh,az1),(ax1,bb,az1),(ax0,bb,az1)]
                                else:
                                    pp = [(ax1,hh,az0),(ax0,hh,az0),(ax0,bb,az0),(ax1,bb,az0)]
                                quad(pp, cc, uv_side(ax0, ax1, az0, az1))
                                sides += 1

                Hs = np.where(solid, H, args.outside).astype(np.int64)
                P = np.full((rows_ + 2, cols_ + 2), args.outside, np.int64)
                P[1:-1, 1:-1] = Hs
                core = P[1:-1, 1:-1]
                for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    nb = np.roll(P, sh, axis=(0, 1))[1:-1, 1:-1]
                    expect += int(np.clip(core - nb, 0, None)[solid].sum()) * CELL

        ok = (drop == expect) and (clash == 0)
        out = out_dir / f"area_{area:02d}.obj"
        png = out_dir / f"area_{area:02d}.png"
        mtl = out_dir / f"area_{area:02d}.mtl"
        if tex is not None:
            from PIL import Image
            Image.fromarray(tex[0]).save(png)
            if args.albedo:
                alb = area_albedo(rs, args.layer)
                if alb is not None:
                    Image.fromarray(alb).save(
                        out_dir / f"area_{area:02d}_albedo.png")
            with mtl.open("w") as f:
                f.write("# Derived from the user's own ROM. Not redistributable.\n")
                f.write("newmtl tmc\nKa 1 1 1\nKd 1 1 1\nd 1\nillum 1\n")
                f.write(f"map_Kd {png.name}\n")
        with out.open("w") as f:
            f.write(f"# area {area}: {len(rs)} rooms in {len(levels)} level(s), "
                    f"floor height {args.floor_height}\n")
            f.write("# Derived from the user's own ROM; regenerate, do not redistribute.\n")
            if tex is not None:
                f.write(f"mtllib {mtl.name}\nusemtl tmc\n")
            for v, c in zip(verts, vcols):
                f.write(f"v {v[0]:.1f} {v[1]:.1f} {v[2]:.1f} "
                        f"{c[0]:.3f} {c[1]:.3f} {c[2]:.3f}\n")
            for u, vv in uvs:
                f.write(f"vt {u:.6f} {vv:.6f}\n")
            for fc in faces:
                if isinstance(fc[0], tuple):
                    f.write("f " + " ".join(f"{a_}/{b_}" for a_, b_ in fc) + "\n")
                else:
                    f.write("f " + " ".join(str(i) for i in fc) + "\n")

        flag = "OK" if ok else ("CLASH" if clash else "MISMATCH")
        print(f"  {area:3d}  {len(rs):6d}  {len(levels):7d}  {tops:7d}  "
              f"{sides:7d}  {flag:>10}")
        grand["areas"] += 1; grand["rooms"] += len(rs)
        grand["levels"] += len(levels); grand["quads"] += tops + sides
        grand["bad"] += (0 if ok else 1)

    prog.done(f"{grand['quads']} quads")
    print(f"\n  {grand['areas']} areas, {grand['rooms']} rooms, "
          f"{grand['levels']} levels, {grand['quads']} quads -> {out_dir}")
    multi = grand['levels'] - grand['areas']
    print(f"  {multi} extra level(s) beyond one per area -- rooms that share a "
          f"footprint, lifted by {args.floor_height}px instead of merged")
    if grand["bad"]:
        sys.exit(f"{grand['bad']} area(s) failed a self-check")
    print("  all areas passed: levels are overlap-free and every height drop "
          "has its side faces")


def cmd_entities(args):
    from collections import Counter
    p = Path(args.path)
    rooms = list(iter_rooms(p))
    v2 = [r for r in rooms if r.version >= 2]
    if not v2:
        print("No v2 dumps found. Entities and tileset art need format v2 —\n"
              "re-harvest with the updated vr_world.c.")
        return
    per_kind, per_type, total = Counter(), Counter(), 0
    for r in v2:
        for e in r.entities:
            if args.kind is not None and e["kind"] != args.kind:
                continue
            total += 1
            per_kind[e["kind"]] += 1
            per_type[(e["kind"], e["id"], e["type"])] += 1
    print(f"{len(v2)} v2 rooms, {total} spawned entities "
          f"({total / max(len(v2), 1):.1f} per room)\n")
    print("by kind:")
    for k, n in per_kind.most_common():
        print(f"  {k} {KIND_NAMES.get(k, '?'):<11} {n:>6}")
    print("\nmost common (kind, id, type) — these are your hull build targets:")
    for (k, i, t), n in per_type.most_common(20):
        print(f"  kind={k} id=0x{i:02x} type=0x{t:02x}  {n:>5}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_wg = sub.add_parser("worldgen", help="emit per-area geometry with floors")
    p_wg.add_argument("dumps")
    p_wg.add_argument("--area", default="")
    p_wg.add_argument("--layer", type=int, default=0)
    p_wg.add_argument("--out", default="geom")
    p_wg.add_argument("--floor-height", type=int, default=64)
    p_wg.add_argument("--outside", type=int, default=-16)
    p_wg.add_argument("--colocated", type=float, default=0.25)
    p_wg.add_argument("--relief", action="store_true",
                      help="heights from drawn front faces (see relief_field)")
    p_wg.add_argument("--canopy", action="store_true",
                      help="shape tree crowns (see canopy_field)")
    p_wg.add_argument("--footprint", action="store_true",
                      help="texture as before: tops from their plan position, "
                           "walls from their footprint, instead of projecting "
                           "the art from the game's camera")
    p_wg.add_argument("--texture", action="store_true",
                      help="emit .mtl + .png and UV-map the geometry")
    p_wg.add_argument("--lod", action="store_true",
                      help="per-cell voxel size from measured detail")
    p_wg.add_argument("--lod-fine", type=float, default=0.55)
    p_wg.add_argument("--lod-mid", type=float, default=0.28)
    p_wg.add_argument("--lod-radius", type=int, default=1)
    p_wg.add_argument("--lod-floor", type=int, default=2)
    p_wg.add_argument("--subdiv", type=int, default=1,
                      help="1=16px voxels, 2=8px, 4=4px; uses the material map")
    p_wg.add_argument("--heights", default="",
                      help="file of 'area material height' overrides")
    p_wg.add_argument("--overlay", action="store_true",
                      help="emit layer 1 (canopies, bridge decks) lifted")
    p_wg.add_argument("--overlay-lift", type=int, default=24)
    p_wg.add_argument("--overlay-skirt", type=int, default=12)
    p_wg.add_argument("--overlay-reach", type=int, default=24,
                   help="max drop a lifted surface may bridge down to the "
                        "ground; beyond this it stays a walk-under overhang")
    p_wg.add_argument("--albedo", action="store_true",
                      help="also emit area_NN_albedo.png for engine lighting")
    p_wg.set_defaults(func=cmd_worldgen)

    p_wd = sub.add_parser("world", help="stitch an area's rooms and check it")
    p_wd.add_argument("dumps")
    p_wd.add_argument("--area", default="", help="comma list; default all")
    p_wd.add_argument("--layer", type=int, default=0)
    p_wd.add_argument("--out", default="world")
    p_wd.add_argument("--png", action="store_true")
    p_wd.add_argument("--scale", type=int, default=2)
    p_wd.add_argument("--show", type=int, default=8)
    p_wd.add_argument("--colocated", type=float, default=0.25,
                      help="rect overlap fraction above which two rooms\n                            are separate spaces, not neighbouring tiles")
    p_wd.add_argument("--max-cells", type=int, default=4_000_000)
    p_wd.set_defaults(func=cmd_world)

    p_vx = sub.add_parser("voxel", help="extrude to a solid coloured mesh")
    p_vx.add_argument("path")
    p_vx.add_argument("--layer", type=int, default=0)
    p_vx.add_argument("--out", default="room.obj")
    p_vx.add_argument("--overlay", action="store_true",
                      help="also emit layer 1 (canopies, bridge decks) lifted")
    p_vx.add_argument("--overlay-lift", type=int, default=24)
    p_vx.add_argument("--relief", action="store_true",
                      help="heights from drawn front faces (see relief_field)")
    p_vx.add_argument("--canopy", action="store_true",
                      help="shape tree crowns as domed, bumpy surfaces "
                           "(work in progress: large meshes)")
    p_vx.add_argument("--overlay-skirt", type=int, default=12)
    p_vx.add_argument("--overlay-reach", type=int, default=24,
                   help="max drop a lifted surface may bridge down to the "
                        "ground; beyond this it stays a walk-under overhang")
    p_vx.add_argument("--heights", default="",
                      help="file of 'area material height' overrides")
    p_vx.add_argument("--lod", action="store_true",
                      help="pick voxel size per cell from measured art detail")
    p_vx.add_argument("--lod-fine", type=float, default=0.55)
    p_vx.add_argument("--lod-mid", type=float, default=0.28)
    p_vx.add_argument("--lod-radius", type=int, default=1)
    p_vx.add_argument("--lod-floor", type=int, default=2,
                      help="minimum subdivision everywhere (2 = 8px)")
    p_vx.add_argument("--subdiv", type=int, default=1,
                      help="1=16px voxels, 2=8px, 4=4px; uses the material map")
    p_vx.add_argument("--outside", type=int, default=-16,
                      help="height beyond the room edge")
    p_vx.set_defaults(func=cmd_voxel)

    p_ag = sub.add_parser("agree", help="does classification match the art?")
    p_ag.add_argument("dumps")
    p_ag.add_argument("--layer", type=int, default=0)
    p_ag.add_argument("--limit", type=int, default=0)
    p_ag.add_argument("--show", type=int, default=8)
    p_ag.add_argument("--min-cells", type=int, default=40)
    p_ag.add_argument("--flat-eps", type=float, default=1.0,
                      help="pooled std below this = featureless art")
    p_ag.add_argument("--weak", type=float, default=0.5,
                      help="d' below this means no visible boundary")
    p_ag.set_defaults(func=cmd_agree)

    p_ov = sub.add_parser("overlay", help="art vs classification side by side")
    p_ov.add_argument("path")
    p_ov.add_argument("--layer", type=int, default=0)
    p_ov.add_argument("--out", default="overlay.png")
    p_ov.add_argument("--alpha", type=float, default=0.55)
    p_ov.add_argument("--scale", type=int, default=1)
    p_ov.set_defaults(func=cmd_overlay)

    p_prov = sub.add_parser("provenance", help="which rule decided each cell")
    p_prov.add_argument("dumps")
    p_prov.add_argument("--layer", type=int, default=0)
    p_prov.add_argument("--limit", type=int, default=0)
    p_prov.add_argument("--top", type=int, default=15)
    p_prov.set_defaults(func=cmd_provenance)

    p_dis = sub.add_parser("distinct", help="check rooms actually differ")
    p_dis.add_argument("dumps")
    p_dis.add_argument("--layer", type=int, default=0)
    p_dis.add_argument("--limit", type=int, default=0)
    p_dis.set_defaults(func=cmd_distinct)


    s = sub.add_parser("survey", help="histogram collision/tile values across rooms")
    s.add_argument("path", help="a .tmcr file or a directory of them")
    s.set_defaults(func=cmd_survey)

    p = sub.add_parser("preview", help="render a classification PNG")
    p.add_argument("path")
    p.add_argument("--out", default="preview.png")
    p.add_argument("--layer", type=int, default=0, help="0 bottom, 1 top")
    p.add_argument("--cell", type=int, default=8, help="px per metatile")
    p.set_defaults(func=cmd_preview)

    e = sub.add_parser("entities", help="list spawns across rooms (v2 dumps)")
    e.add_argument("path")
    e.add_argument("--kind", type=int, help="filter by Entity.kind")
    e.set_defaults(func=cmd_entities)

    m = sub.add_parser("mesh", help="classify, extrude, greedy-mesh, export OBJ")
    m.add_argument("path")
    m.add_argument("--out", default="room.obj")
    m.add_argument("--layer", type=int, default=0)
    m.set_defaults(func=cmd_mesh)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
