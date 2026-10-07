#!/usr/bin/env python3
"""tilevox.py -- identify every room's tiles and voxelate them.

The terrain stage (room_explore.py voxel / worldgen) settles HOW HIGH each
cell stands, checked against the game's collision. This stage settles what
each cell LOOKS like up close: every distinct 16x16 drawing becomes one
voxel model at the art's own resolution, one voxel per pixel, and every
cell places the model for its drawing on top of its height.

  identify   A tile is its drawing, both layers composited as the game shows
             them, plus its role (floor, wall, block, water, pit). Keying on
             the pixels rather than the tile index makes a tile the same
             thing wherever it is drawn, whatever tileset slot it came from:
             251,628 cells in 555 rooms come down to about 31,600 drawings,
             at most about 1,600 in one area.

  voxelate   One column per pixel, textured with that pixel, standing one
             voxel plus a relief taken from the drawing's own shading on
             2px blocks -- lit grass tufts and stones stand up to RELIEF
             voxels proud, outlines and shadow stay down. A first model,
             meant to be tuned per role later.

  place      Each cell's model sits on the cell's height (the same
             heightfield the terrain uses: collision classes, measured
             faces, blocks, sprite footprints flattened), rooms stacked into
             levels as worldgen does. Vertical faces where a cell drops to a
             lower neighbour are built here too, textured with the rows
             the game draws above the cell's foot (drawn row = z - height,
             the 45-degree rule) -- so a wall's front shows its drawn front,
             not its top stretched down, and its undrawn flanks wear the
             same band.

  stairs     A flight of steps joins two floors the flat terrain put at
             one height. Where its landings are not otherwise connected,
             the upper one is raised by the height of the cliff beside the
             flight (or its drawn risers), and the flight is built as the
             steps find_stairs.py counts, spaced as drawn.

  doorways   A door cell (SURFACE_DOOR_13) in a wall stands as tall as its
             drawn arch -- the cell above, marked SURFACE_DOOR or just wall
             -- and so does the wall beside it; the opening measured from
             the drawn dark interior is carved into it one cell deep, with
             steps rising inside a stairway door. The top layer's arch
             plates over a doorway are dropped: they are the arch's front.

  buildings  A door with a roof over it (the top layer's non-leaf drawing
             over the house's blocked footprint) is a building. Its drawn
             extent is height plus depth by the 45-degree rule: half is
             height, and the volume stands on the front part of the
             drawing with the roof projected onto it -- domed where the
             roof's outline is rounded (mushroom caps), gabled where it is
             square -- and its doors carved into its front.

  uprights   Braziers and torches, found by their drawing: a flame (a
             compact, bright, saturated blob with a pale core, taller than
             wide, burning alone) over a blocked post. Their whole drawing
             is height; the silhouette is extruded upright on its foot.
             Fences and signposts (tileid) are built the same way, 4px
             deep, over the floor beside them.

  overlay    Where the top layer is drawn above the bottom one (outdoors),
             its cells are tiles too, cut out along the layer's
             transparency -- roofs, bridge decks, fence and planter tops
             -- lifted OVERLAY_LIFT like the terrain overlay, with aprons
             down to the ground where it comes close. Tree crowns keep the
             terrain's crown shape (room_explore.canopy_field), textured
             from where their leaves are drawn.

Outputs, per area, under --out (default geom/tiles; gitignored -- derived
from your ROM):
  area_NN/tiles.obj      the tile library: one object per drawing, local
                         coordinates, 16x16 base at y=0, +Z south,
                         textured from area_NN/tiles.png (the drawings)
  area_NN/placements.txt one line per cell: key room cx cy x y z role
  area_NN/stairs.txt     every flight of steps: its cells, step count, and
                         the landings it joins
  area_NN/room_AA_RR.obj (--merge) the room assembled: placed tiles plus
                         the vertical faces, world coordinates, textured
                         from room_AA_RR.png (the room's art), ready to view

Usage:
  python3 tools/tilevox.py states/p0 --area 0 --merge
  python3 tools/tilevox.py states/p0 --merge --jobs 8      # every area
"""
import argparse
import hashlib
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402
import tileid as TI  # noqa: E402
import picori_labels as PL  # noqa: E402

# Relief in voxels above the one-voxel base, per role. Water and pits are
# flat: their drawn ripples and shading are not shape.
RELIEF = {"floor": 1, "wall": 1, "block": 1, "water": 0, "pit": 0, "deck": 1,
          "grass": 5, "indoor": 0, "wallflat": 0, "furniture": 0}  # indoors: planks and tiles lie flat; their
                                    # seams read as speckle in relief
RELIEF_STEPS = {"grass": 1}     # per role; others use RELIEF_STEP
RELIEF_STEP = 2         # px: relief is measured on blocks this size
FLAT_SPREAD = 12.0      # luminance spread below which a drawing is flat


# --------------------------------------------------------------- identify --

# Tall grass is collision 0x5F, a walkable surface (1,852 cells in 69
# rooms); most of its neighbours 0x51-0x5E are desert sand, so the drawing
# must also be green. It gets blades: relief per pixel rather than per 2px
# block, up to GRASS_H, so each drawn blade stands as its own column.
GRASS_COLL = 0x5F
GRASS_H = 5
GRASS_GREEN = 0.5       # share of green pixels (hue 60-180, saturation 0.3)


def is_green(px):
    rgb = px[:, :, :3].astype(float) / 255.0
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    r_, g_, b_ = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    hue = np.where(mx == r_, ((g_ - b_) / d) % 6,
                   np.where(mx == g_, (b_ - r_) / d + 2, (r_ - g_) / d + 4)) * 60.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    return float(((hue >= 60) & (hue <= 180) & (sat >= 0.3)).mean()) >= GRASS_GREEN


def room_roles(r, cls, H, blocks, art=None):
    """Per cell role, or None where nothing is built (void)."""
    h, w = r.cells_h, r.cells_w
    role = np.empty((h, w), dtype=object)
    coll = r.layers[0]["collision"][:h, :w]
    for cy in range(h):
        for cx in range(w):
            c = cls[cy, cx]
            if c == RE.CLASS_VOID:
                role[cy, cx] = None
            elif (cy, cx) in blocks:
                role[cy, cx] = "block"
            elif c == RE.CLASS_WATER:
                role[cy, cx] = "water"
            elif c == RE.CLASS_HOLE:
                role[cy, cx] = "pit"
            elif H[cy, cx] > 0:
                role[cy, cx] = "wall"
            elif (coll[cy, cx] == GRASS_COLL and art is not None
                  and is_green(art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16])):
                role[cy, cx] = "grass"
            else:
                role[cy, cx] = "floor"
    return role


WALL_SPECKLE = 8        # px: a wall cell this far off its wall neighbours' median
WALL_SPECKLE_NB = 5     # of its 8 neighbours at least this many are wall


def despeckle_walls(cls, H):
    """Single wall cells whose measured height disagrees with the wall
    around them take that wall's median height.

    The drop is measured per cell from the drawn face below it, and next
    to gates and cliff corners one cell reads 24 or 8 in a run of 16s: it
    stood as a lone block with its dark stone top showing, a lump. Only
    cells mostly surrounded by wall are touched; edges and real steps
    between wall heights keep what they measured."""
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    H = np.array(H, dtype=np.int64)
    out = H.copy()
    rows, cols = H.shape
    for y in range(rows):
        for x in range(cols):
            if not blocked[y, x]:
                continue
            nb = [int(H[y + dy, x + dx]) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                  if (dy or dx) and 0 <= y + dy < rows and 0 <= x + dx < cols
                  and blocked[y + dy, x + dx]]
            if len(nb) < WALL_SPECKLE_NB:
                continue
            med = int(np.median(nb))
            if abs(int(H[y, x]) - med) >= WALL_SPECKLE:
                out[y, x] = med
    return out


INTERIOR_WALL_MAX = 3   # cells: tallest drawn back wall indoors
FLOOR_MATCH = 8         # mean |difference| per channel: drawn as the floor
FLOOR_LONE = 2          # cells: only a blocked cluster this small


def terrace_levels(cls, H):
    """Floors stacked one above another -- the library bookshelf's boards,
    each on the books below it. Going up a column, a floor above a run of
    wall stands at the floor below plus the run's drawn height (16px a
    cell: the wall's front is drawn as tall as it is), and the run's cells
    are that tall; ladders (ledge cells) count as wall. The lowest floor
    keeps its height. Whatever wall is left above the top floor stands on
    it by its own drawn run."""
    h, w = cls.shape
    walk = cls == RE.CLASS_GROUND
    wall = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    lab, n = RE._label(walk)
    if n < 2:
        return H
    H = np.array(H, dtype=np.int64)
    level = {}
    # the floor reaching lowest in the room is the ground
    bottom = max(range(1, n + 1), key=lambda k: np.nonzero(lab == k)[0].max())
    level[bottom] = int(np.median(H[lab == bottom]))
    # floors whose fronts are one row are one board, split by a post: they
    # share a level, the median of what each of their columns says
    span = {k: (int(np.nonzero(lab == k)[0].min()), int(np.nonzero(lab == k)[0].max()))
            for k in range(1, n + 1)}
    groups = {}
    for k, sp in span.items():
        groups.setdefault(sp[1], []).append(k)          # by the board's front row
    for sp in sorted(groups, key=lambda b: -b):          # bottom up
        ks = [k for k in groups[sp] if k != bottom]
        cand = []
        for x in range(w):
            for y in range(h):
                if not (walk[y, x] and lab[y, x] in ks):
                    continue
                if y + 1 < h and walk[y + 1, x] and lab[y + 1, x] in ks:
                    continue                    # the bottom row of the floor
                b = y + 1
                while b < h and wall[b, x]:
                    b += 1
                if b > y + 1 and b < h and walk[b, x] and lab[b, x] in level:
                    cand.append(level[lab[b, x]] + 16 * (b - y - 1))
        if cand:
            for k in ks:
                level[k] = int(np.median(cand))
    for k, v in level.items():
        H[lab == k] = v
    # wall runs take the height of the floor above them, or stand on the
    # floor below by their own run
    for x in range(w):
        y = 0
        while y < h:
            if wall[y, x]:
                a = y
                while y < h and wall[y, x]:
                    y += 1
                below = lab[y, x] if y < h and walk[y, x] else 0
                above = lab[a - 1, x] if a > 0 and walk[a - 1, x] else 0
                if above in level:
                    H[a:y, x] = level[above]
                elif below in level:
                    H[a:y, x] = level[below] + 16 * (y - a)
            else:
                y += 1
    return H


def undrawn_void(r, cls, layer=0):
    """In an 8bpp room (the Minish-sized interiors) the picture is the
    room: a cell it leaves transparent or all black -- the black round an
    oval Minish house -- is outside it, not wall. Void there."""
    L = r.layers[layer]
    if not L.get("bpp8"):
        return cls
    import extract_art
    a = extract_art.room_art(r, layer)
    if a is None:
        return cls
    cls = cls.copy()
    h, w = cls.shape
    a = np.asarray(a)[:h * 16, :w * 16]
    # nothing drawn, or drawn all black (the picture's own backdrop)
    seen = (a[..., 3] > 0) & (a[..., :3].max(axis=-1) > 0)
    cls[~seen.reshape(h, 16, w, 16).any(axis=(1, 3))] = RE.CLASS_VOID
    return cls


def sprite_floor(r, cls, H):
    """Where an object stands -- a pot, a boulder, furniture, all sprites --
    the game marks the cells with a special tile (index 0x4000 on) and
    blocks them; the room's art under them is the floor. Such a cell drawn
    as a floor cell of the room is floor, at its neighbours' height: the
    object is the entity stage's to build. Indoors or out."""
    L = r.layers[0]
    rows, cols = H.shape
    t = L["tile"][:rows, :cols]
    walk = cls == RE.CLASS_GROUND
    cand = (t >= 0x4000) & ~walk
    art = RE.room_art_rgb(r, 0)
    if art is None or not cand.any() or not walk.any():
        return H
    art = art[:rows * 16, :cols * 16, :3].astype(np.int16)

    def cell(y, x):
        return art[y * 16:y * 16 + 16, x * 16:x * 16 + 16]
    fl = np.unique(np.stack([cell(y, x) for y, x in zip(*np.nonzero(walk))
                             if cell(y, x).shape[:2] == (16, 16)]), axis=0)
    H = np.array(H, dtype=np.int64)
    like = [(y, x) for y, x in zip(*np.nonzero(cand))
            if cell(y, x).shape[:2] == (16, 16)
            and np.abs(fl - cell(y, x)[None]).mean(axis=(1, 2, 3)).min() <= FLOOR_MATCH]
    left = set(like)
    for _ in range(4):              # a run of them takes its floor from its ends
        for y, x in sorted(left):
            nb = [int(H[yy, xx]) for yy, xx in ((y + 1, x), (y - 1, x), (y, x - 1), (y, x + 1))
                  if 0 <= yy < rows and 0 <= xx < cols and (walk[yy, xx] or ((yy, xx) in like
                                                                            and (yy, xx) not in left))]
            if nb:
                H[y, x] = min(nb)
                left.discard((y, x))
    return H


def interior_walls(r, cls, H):
    """Indoors, a back wall stands as tall as it is drawn.

    A house's back wall is drawn as a face of two or three cells above the
    floor; the measured drop reads the lowest band and gives 16. In rooms
    whose top layer is not overhead (interiors), a run of blocked cells
    straight above a floor cell -- up to INTERIOR_WALL_MAX -- is that face,
    and every cell of it stands 16px per cell of the run. Only the wall
    mass joined to the room's edge, in columns solid from the floor to that
    edge: furniture and chests stand apart."""
    if RE.overlay_overhead(r) and PL.location(r.area, r.room)["open_air"]:
        return H            # out of doors: no ring of wall
    L = r.layers[0]
    rows, cols = H.shape
    tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)][:rows, :cols]
    objects = np.isin(tt, list(RE.BLOCK_TYPES) + [0x73, 0x74])   # chests, torches
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & ~objects
    walk = cls == RE.CLASS_GROUND
    H = np.array(H, dtype=np.int64)
    raised = np.zeros_like(H)
    # invisible collision: a blocked cell drawn as a floor cell of the room
    # (within FLOOR_MATCH per channel) is floor -- the smith's room has an
    # object tile drawn as a bare plank by its back wall
    art = RE.room_art_rgb(r, 0)
    if art is not None and walk.any():
        art = art[:rows * 16, :cols * 16, :3].astype(np.int16)
        fl = np.unique(np.stack([art[y * 16:y * 16 + 16, x * 16:x * 16 + 16]
                                 for y, x in zip(*np.nonzero(walk))
                                 if art[y * 16:y * 16 + 16, x * 16:x * 16 + 16].shape[:2] == (16, 16)]),
                       axis=0)
        like = np.zeros_like(blocked)
        for y, x in zip(*np.nonzero(blocked)):
            px = art[y * 16:y * 16 + 16, x * 16:x * 16 + 16]
            like[y, x] = px.shape[:2] == (16, 16) and \
                np.abs(fl - px[None]).mean(axis=(1, 2, 3)).min() <= FLOOR_MATCH
        # only a lone cell or two, or an object tile (index 0x4000 on, set
        # by the room's objects): a drop or a ledge drawn like the floor
        # (the green below the logs in the tree houses) is not
        blab, _nb = RE._label(like)
        bsize = np.bincount(blab.ravel())
        special = L["tile"][:rows, :cols] >= 0x4000      # object tiles
        for y, x in zip(*np.nonzero(like)):
            if bsize[blab[y, x]] > FLOOR_LONE and not special[y, x]:
                continue
            nb = [int(H[yy, xx]) for yy, xx in ((y + 1, x), (y - 1, x), (y, x - 1), (y, x + 1))
                  if 0 <= yy < rows and 0 <= xx < cols and walk[yy, xx]]
            if nb:
                blocked[y, x] = False
                H[y, x] = min(nb)
    # the house's walls are the blocked mass joined to the room's edge;
    # furniture stands apart from it and keeps its height
    lab, _n = RE._label(blocked)
    edge = set(lab[0].tolist()) | set(lab[-1].tolist()) | \
        set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
    edge.discard(0)
    for y in range(1, rows):
        for x in range(cols):
            if not walk[y, x] or not blocked[y - 1, x] or lab[y - 1, x] not in edge:
                continue
            run = []
            yy = y - 1
            while yy >= 0 and blocked[yy, x] and len(run) < INTERIOR_WALL_MAX:
                run.append(yy)
                yy -= 1
            # a back wall is solid from the floor to the room's edge; a
            # table with floor behind it is not
            if not blocked[:y, x].all():
                continue
            # ... and drawn as the wall is at the edge: a table pushed
            # against it is not the wall
            if any(tt[yy, x] != tt[0, x] for yy in run):
                continue
            top = 16 * len(run)
            for yy in run:
                H[yy, x] = max(int(H[yy, x]), top)
                raised[yy, x] = max(int(raised[yy, x]), top)
    # an exit: a run of SURFACE_DOOR cells from the floor out to the room's
    # edge with no door under it (the arch over a doorway has one) -- it is
    # floor, walked out of, not wall
    act = L["act"][:rows, :cols]
    exits = np.zeros((rows, cols), bool)
    for y in range(rows):
        for x in range(cols):
            if act[y, x] != ARCH_ACT or (y + 1 < rows and act[y + 1, x] == DOOR_ACT):
                continue
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                # walk outward (away from the floor) to the edge
                yy, xx = y, x
                while 0 <= yy < rows and 0 <= xx < cols and act[yy, xx] == ARCH_ACT:
                    yy, xx = yy + dy, xx + dx
                fy, fx = y - dy, x - dx
                while 0 <= fy < rows and 0 <= fx < cols and act[fy, fx] == ARCH_ACT:
                    fy, fx = fy - dy, fx - dx
                if not (0 <= yy < rows and 0 <= xx < cols) and \
                        0 <= fy < rows and 0 <= fx < cols and walk[fy, fx]:
                    exits[y, x] = True
                    H[y, x] = int(H[fy, fx])
                    break
    # the ring of wall round a room stands at least a storey: the front
    # wall's drawing is its top, where the measured drop read 2-6px under
    # its window ornaments
    ring = np.isin(lab, list(edge)) & blocked & ~exits
    H = np.where(ring, np.maximum(H, 16), H)
    # the wall behind a dresser or a shelf is the same wall: carry each
    # raised row sideways along the wall it belongs to
    for y, x in zip(*np.nonzero(raised)):
        top = int(raised[y, x])
        for step in (-1, 1):
            xx = x + step
            while 0 <= xx < cols and blocked[y, xx] and lab[y, xx] in edge and not exits[y, xx] \
                    and tt[y, xx] == tt[0, xx] and blocked[:y + 1, xx].all():
                H[y, xx] = max(int(H[y, xx]), top)
                xx += step
    return H


def room_heights(r, layer=0, blocks=True, relief=False, path=None):
    """The terrain's cell heights, built exactly as `voxel` builds them,
    plus the upper landings of flights of steps when path is given
    (stair_levels), and walls raised to their doorways (find_doors).
    Returns (cls, H, blocks, flights, doors)."""
    cls = undrawn_void(r, RE.classify_room(r, layer), layer)
    H = despeckle_walls(cls, RE.heightfield(r, cls, layer))
    if layer == 0:
        H = interior_walls(r, cls, H)
        H = sprite_floor(r, cls, H)
    if relief:
        Hr, _solid = RE.relief_field(r, cls, 16, layer)
        H = np.array(Hr, dtype=np.int64)
    bl = RE.block_cells(r, cls, layer) if blocks else {}
    if bl:
        H = RE.block_heights(H, bl, 16)
    H = RE.flatten_cells(H, RE.sprite_footprints(r, cls, layer), 16)
    if layer == 0 and PL.location(r.area, r.room)["view"] == "terrace":
        H = terrace_levels(cls, H)
    flights = []
    if path is not None and layer == 0:
        H, flights = stair_levels(path, r, cls, H)
    doors = []
    if layer == 0:
        H, doors = find_doors(r, cls, H, layer)
        H = TI.apply_height_overrides(r, H)      # vr/tiles/overrides.txt
    return cls, H, bl, flights, doors


# ----------------------------------------------------------------- stairs --
# A flight of steps (find_stairs.py: cells whose action byte is a slope)
# joins two floors. The flat terrain puts every walkable cell at 0, so both
# landings of 76 of the game's 90 layer-0 flights came out level and the
# steps had nothing to climb. stair_levels restores the upper landing, then
# stair_quads builds the steps the drawing counts.
STAIR_RISE = (8, 48)            # px: plausible total rise of one flight
STAIR_MAX_SHARE = 0.4           # never raise more of a room's floor than this


def stair_levels(path, r, cls, H):
    """Raise the upper landing of every flight; return (H, flights).

    For each flight, the floor on either side of it (stair cells excluded)
    is split into connected regions. If the two landings are one region --
    reachable from each other some other way -- they are one level and the
    flight is left as drawn relief. Otherwise the landing on the up side
    is raised: north for a north-south flight (its risers face the camera,
    so it climbs away from it), the smaller region for an east-west one.

    The rise is the drawn height of the faces beside the flight -- the
    cliff the stairs climb, measured by the terrain -- or, without one, the
    flight's own risers summed (dark rows = height, the 45-degree rule).
    Blocked clusters standing on the raised floor and touching no lower
    floor (a house, a tree on the plateau) go up with it.
    """
    import find_stairs as FS
    try:
        found = FS.find(Path(path))
    except Exception:
        return H, []
    flights = [it for it in found if it["kind"] == "steps" and it["layer"] == 0
               and (it.get("measure") or {}).get("steps")]
    if not flights:
        return H, []
    # lowest first: a flight's lower landing may be another's upper one,
    # raised before it is measured from (north-south flights climb north)
    flights.sort(key=lambda it: -it["rect"][3])
    H = np.array(H, dtype=np.int64)
    rows, cols = H.shape
    stair = np.zeros((rows, cols), bool)
    for it in flights:
        x0, y0, x1, y1 = it["rect"]
        stair[y0:y1 + 1, x0:x1 + 1] = True
    floor = (cls == RE.CLASS_GROUND) & ~stair
    total = max(1, int(floor.sum()))
    out = []
    shared = {}                 # (upper, lower landing) -> [rises measured]
    lifted = np.zeros((rows, cols), bool)
    for pass_ in (0, 1):
     for it in flights:
         x0, y0, x1, y1 = it["rect"]
         m = it["measure"]
         ns = it["rise"] == "ns"
         lab, _n = RE._label(floor)
         if ns:
             sa = [(y0 - 1, x) for x in range(x0, x1 + 1)]       # north: up
             sb = [(y1 + 1, x) for x in range(x0, x1 + 1)]
         else:
             sa = [(y, x0 - 1) for y in range(y0, y1 + 1)]       # west
             sb = [(y, x1 + 1) for y in range(y0, y1 + 1)]       # east
         inside = lambda c: 0 <= c[0] < rows and 0 <= c[1] < cols
         la = {int(lab[c]) for c in sa if inside(c) and lab[c]}
         lb = {int(lab[c]) for c in sb if inside(c) and lab[c]}
         rec = dict(rect=it["rect"], rise=it["rise"], steps=m["steps"],
                    bands=m["bands"], up=None, base=None, top=None)
         if not la or not lb or la & lb:
             if pass_:
                 out.append(rec)                 # one level: leave as drawn
             continue
         area = lambda ids: int(np.isin(lab, list(ids)).sum())
         if ns:
             up, dn, up_side = la, lb, "n"
         else:
             up, dn, up_side = ((la, lb, "w") if area(la) <= area(lb)
                                else (lb, la, "e"))
         region = np.isin(lab, list(up))
         if region.sum() > STAIR_MAX_SHARE * total:
             if pass_:
                 out.append(rec)
             continue
         base = int(max(H[c] for c in (sb if up is la else sa) if inside(c)))
         # faces beside the flight: blocked cells level with it, on either side
         faces = []
         if ns:
             for y in range(y0, y1 + 1):
                 for x in (x0 - 1, x1 + 1):
                     if inside((y, x)) and cls[y, x] in (RE.CLASS_WALL, RE.CLASS_LEDGE):
                         faces.append(int(H[y, x]) - base)
         risers = int(sum(b[3] for b in m["bands"]))
         rise = int(np.median(faces)) if faces else risers
         if ns and len(m.get("edges") or []) > 1:
             # a flight facing the camera is drawn as tall as the cliff it
             # climbs: its drawing's span is the rise (the walls' class
             # height, 16, was half of it in Hyrule Town)
             drawn = int(m["edges"][-1] - m["edges"][0])
             rise = max(rise if STAIR_RISE[0] <= rise <= STAIR_RISE[1] else risers, drawn)
         elif not (STAIR_RISE[0] <= rise <= STAIR_RISE[1]):
             rise = risers
         rise = int(min(max(rise, STAIR_RISE[0]), STAIR_RISE[1]))
         rise = 2 * ((rise + 1) // 2)
         key = (frozenset(up), base)    # one upper level, from one lower height
         if pass_ == 0:
             # every flight between the same two levels measures the same
             # step up: they share the middle of their measures
             shared.setdefault(key, []).append(rise)
             continue
         if len(shared.get(key, ())) > 1:
             rise = int(np.median(shared[key]))
             rise = 2 * ((rise + 1) // 2)
         top = base + rise
         H[region & (H < top)] = top
         H[y0:y1 + 1, x0:x1 + 1] = base     # the steps stand here, nothing else
         # Blocked clusters standing on the raised floor -- a house, a tree
         # -- go up with it: those touching the raised floor and no lower
         # floor. A cliff between the two levels touches both and stays;
         # its measured face is already the step up.
         blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & ~stair
         blab, bn = RE._label(blocked)
         lower = floor & ~region
         Pr, Pl = np.pad(region, 1), np.pad(lower, 1)
         touch_r = Pr[:-2, 1:-1] | Pr[2:, 1:-1] | Pr[1:-1, :-2] | Pr[1:-1, 2:]
         touch_l = Pl[:-2, 1:-1] | Pl[2:, 1:-1] | Pl[1:-1, :-2] | Pl[1:-1, 2:]
         for k in range(1, bn + 1):
             comp = blab == k
             if (touch_r & comp).any() and not (touch_l & comp).any():
                 if not (lifted & comp).any():   # once, by its level's first flight
                     H[comp] += rise
                     lifted |= comp
             elif (touch_r & comp).any() and ns:
                 # the cliff between the levels, in the flight's own rows:
                 # as tall as the step up (a bush beside the foot is not)
                 rows_ = np.zeros_like(comp)
                 rows_[y0:y1 + 1] = True
                 up_ = comp & rows_ & (H < top)
                 H[up_] = top
                 cliff_ = rec.setdefault("cliff", [])
                 cliff_ += [(int(a_), int(b_)) for a_, b_ in zip(*np.nonzero(up_))]
         rec.update(up=up_side, base=base, top=top, _up=[c for c in (sa if up is la else sb) if inside(c)])
         out.append(rec)
    # a later flight may raise a landing an earlier one climbs to: every
    # flight ends level with its upper landing as finally built
    for rec in out:
        ups_ = rec.pop("_up", None)
        if ups_:
            rec["top"] = max(rec["top"], max(int(H[c]) for c in ups_))
    return H, out


ARCH_MIN = 200          # px: smallest overhead shape built as a standing arch
ARCH_OPEN = 0.25        # of its lower half's box, open between its legs
ARCH_T = 6              # px: an arch's thickness
ARCH_MAX_W, ARCH_MAX_H = 192, 128   # px: an arch is a gate, not a forest


def standing_arches(top, cls, H):
    """Arches the overhead layer draws standing on two legs -- the town
    gate's: a shape whose lowest rows lie over stone and whose lower half
    is open between its legs. Each: its pixels and the plane it stands in.
    Laid flat as decks it hovered, and looked different from each side;
    drawn by the 45-degree rule it is upright, every row as high above
    its legs' foot as it is drawn above it."""
    if top is None:
        return []
    alpha = np.asarray(top)[:, :, 3] > 0
    h_, w_ = cls.shape
    alpha = alpha[:h_ * 16, :w_ * 16]
    lab, n = RE._label(alpha)
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    out = []
    for j in range(1, n + 1):
        ys, xs = np.nonzero(lab == j)
        if len(ys) < ARCH_MIN:
            continue
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        feet = ys >= y1 - 3
        on = blocked[ys[feet] // 16, xs[feet] // 16]
        if on.mean() < 0.8:
            continue
        mid = (y0 + y1) // 2
        box = lab[mid:y1 + 1, x0:x1 + 1] == j
        if 1 - box.mean() < ARCH_OPEN:
            continue
        if (x1 - x0 + 1) > ARCH_MAX_W or (y1 - y0 + 1) > ARCH_MAX_H:
            continue
        hue_, sat_, _v = TI._hsv(np.asarray(top)[ys, xs, :3])
        if ((hue_ >= 60) & (hue_ < 170) & (sat_ >= 0.3)).mean() > 0.4:
            continue            # leaf: a hedge drawn round a bed, not a gate
        # exactly two legs at its foot, apart
        foot = (lab[y1 - 3:y1 + 1, x0:x1 + 1] == j).any(axis=0)
        runs_, inrun = [], False
        for i_, v_ in enumerate(foot):
            if v_ and not inrun:
                runs_.append([i_, i_])
            if v_:
                runs_[-1][1] = i_
            inrun = bool(v_)
        if len(runs_) != 2 or runs_[1][0] - runs_[0][1] < 0.3 * (x1 - x0 + 1):
            continue
        # and open between them from under its top band to the foot
        c_ = x0 + (runs_[0][1] + runs_[1][0]) // 2
        col = lab[y0:y1 + 1, c_] == j
        if col[-1] or not col.any():
            continue
        below = len(col) - 1 - int(np.nonzero(col)[0].max())
        if below < 0.4 * (y1 - y0 + 1):
            continue
        base = int(max(H[y_ // 16, x_ // 16] for y_, x_ in zip(ys[feet], xs[feet])))
        out.append(dict(px=set(zip(ys.tolist(), xs.tolist())), zp=int(y1) + 1 + base,
                        base=base, cells={(int(y_) // 16, int(x_) // 16) for y_, x_ in zip(ys, xs)}))
    return out


def arch_quads(a, ox, oz, lift, voff):
    """An arch as an upright slab ARCH_T thick in its plane, every pixel at
    its drawn height above its legs' foot, the same face front and back."""
    P = a["px"]
    zp = a["zp"]
    za, zb = oz + zp - ARCH_T // 2, oz + zp + (ARCH_T - ARCH_T // 2)
    q = []
    for (R, X) in P:
        y0, y1 = lift + zp - R - 1, lift + zp - R
        x0, x1 = ox + X, ox + X + 1
        uv = [(X + 0.5, voff + R + 0.5)] * 4
        q.append(([(x0, y1, zb), (x1, y1, zb), (x1, y0, zb), (x0, y0, zb)], uv))   # front
        q.append(([(x1, y1, za), (x0, y1, za), (x0, y0, za), (x1, y0, za)], uv))   # back
        if (R - 1, X) not in P:
            q.append(([(x0, y1, za), (x1, y1, za), (x1, y1, zb), (x0, y1, zb)], uv))
        if (R + 1, X) not in P:
            q.append(([(x0, y0, zb), (x1, y0, zb), (x1, y0, za), (x0, y0, za)], uv))
        if (R, X + 1) not in P:
            q.append(([(x1, y1, zb), (x1, y1, za), (x1, y0, za), (x1, y0, zb)], uv))
        if (R, X - 1) not in P:
            q.append(([(x0, y1, za), (x0, y1, zb), (x0, y0, zb), (x0, y0, za)], uv))
    return q


def stair_quads(fl, ox, oz, lift):
    """The steps of one raised flight, textured from the game's camera.

    Steps as the drawing counts and proportions them -- each tread as deep
    as its lit rows, each riser as tall as its shaded rows, scaled to the
    flight's run and rise -- from the lower landing to the upper across
    the flight's cells. Each is a solid block down to the base: its tread, its riser
    facing down the flight, its two flanks. A flight facing the camera
    wears its drawing step by step: each tread its band's lit rows, its
    riser and flanks the band's shaded rest. Others project the room art
    from the game's camera, (x, z - height).
    """
    if fl.get("up") is None:
        return []
    x0, y0, x1, y1 = fl["rect"]
    n = int(fl["steps"])
    # as drawn: a step's lit rows are its tread, its shaded rows its riser
    # (the 45-degree rule), scaled together to the flight's run and rise
    bands = fl["bands"] or [(0, 2, 1, 1)] * n
    pitch = np.array([max(1, t_) for _a, _b, t_, _r in bands], float)[::-1]
    riser = np.array([max(1, r_) for _a, _b, _t, r_ in bands], float)[::-1]
    base, top = fl["base"] + lift, fl["top"] + lift
    hs = base + np.rint(np.cumsum(riser) / riser.sum() * (top - base)).astype(int)
    X0, X1 = ox + x0 * 16, ox + (x1 + 1) * 16
    Z0, Z1 = oz + y0 * 16, oz + (y1 + 1) * 16
    up = fl["up"]
    L = (Z1 - Z0) if up == "n" else (X1 - X0)
    cuts = np.rint(np.concatenate([[0], np.cumsum(pitch)]) / pitch.sum() * L).astype(int)

    def uv(pts):
        return [(x - ox, (z - oz) - (y - lift)) for x, y, z in pts]

    # the drawn bands, top of the flight first: (first row, end row, lit rows)
    drawn = [(int(a_), int(b_), max(1, min(int(t_), int(b_) - int(a_) - 1)))
             for a_, b_, t_, _r in (fl.get("bands") or [])]
    if len(drawn) != n:
        drawn = None
    quads = []
    prev = base
    for i in range(n):
        h = int(hs[i])
        a_, b_ = int(cuts[i]), int(cuts[i + 1])      # from the low end
        if b_ <= a_ or h <= base:
            prev = max(prev, h)
            continue
        if up == "n":          # low end south, rising north
            za, zb = Z1 - b_, Z1 - a_
            boxes = [
                [(X0, h, za), (X1, h, za), (X1, h, zb), (X0, h, zb)],          # tread
                [(X0, h, zb), (X1, h, zb), (X1, prev, zb), (X0, prev, zb)],    # riser
                [(X1, h, za), (X1, h, zb), (X1, base, zb), (X1, base, za)],    # east
                [(X0, h, zb), (X0, h, za), (X0, base, za), (X0, base, zb)],    # west
            ]
        elif up == "w":        # low end east, rising west
            xa, xb = X1 - b_, X1 - a_
            boxes = [
                [(xa, h, Z0), (xb, h, Z0), (xb, h, Z1), (xa, h, Z1)],
                [(xb, h, Z0), (xb, h, Z1), (xb, prev, Z1), (xb, prev, Z0)],
                [(xa, h, Z1), (xb, h, Z1), (xb, base, Z1), (xa, base, Z1)],
                [(xb, h, Z0), (xa, h, Z0), (xa, base, Z0), (xb, base, Z0)],
            ]
        else:                  # low end west, rising east
            xa, xb = X0 + a_, X0 + b_
            boxes = [
                [(xa, h, Z0), (xb, h, Z0), (xb, h, Z1), (xa, h, Z1)],
                [(xa, h, Z1), (xa, h, Z0), (xa, prev, Z0), (xa, prev, Z1)],
                [(xa, h, Z1), (xb, h, Z1), (xb, base, Z1), (xa, base, Z1)],
                [(xb, h, Z0), (xa, h, Z0), (xa, base, Z0), (xb, base, Z0)],
            ]
        band = drawn[n - 1 - i] if drawn and up == "n" else None
        for j, pts in enumerate(boxes):
            ys = {p[1] for p in pts}
            if not (len(ys) == 1 or min(ys) < max(ys)):
                continue
            if band is None:
                quads.append((pts, uv(pts)))
                continue
            # the step wears its own drawn band: its tread the band's lit
            # rows, its riser and flanks the rest -- projected, a tall
            # flight's top tread reached past the cliff's lip into the grass
            ra, rb, tr = band
            if j == 0:
                quads.append((pts, [(x - ox, ra + (z - za) / max(1, zb - za) * tr)
                                    for x, y, z in pts]))
            else:
                lo = base if j > 1 else prev
                quads.append((pts, [(x - ox, ra + tr + (h - y) / max(1, h - lo) * (rb - ra - tr))
                                    for x, y, z in pts]))
        prev = h
    return quads


# -------------------------------------------------------------- doorways --
# A doorway is a door cell (action 0x28, SURFACE_DOOR_13) in a wall, solid,
# entered from the floor to its south; a stairway doorway also has its arch
# in the cell above (action 0x29, SURFACE_DOOR): 75 such pairs and ~200
# single door cells in the game. The heightfield made each a painted wall
# 16px tall with the top layer's arch floating over it. Here the wall
# stands as tall as the drawn door, and the opening is carved into it.
DOOR_ACT, ARCH_ACT = 0x28, 0x29
DOOR_DARK = 60          # luminance: the drawn interior of an opening
DOOR_DEPTH = 16         # px: how far the opening goes into the wall
DOOR_STAIRS = (0x91, 0x92, 0x9a, 0x9b, 0x4d6)   # stair-arch tile types
DOOR_RUN = 4            # cells either side raised with the doorway


def find_doors(r, cls, H, layer=0):
    """Doorways of a room, measured from the drawing; raises their walls.

    Per doorway: the wall height is the drawn door, 16px per door cell --
    two where an arch stands over the opening, marked SURFACE_DOOR or just
    wall -- or the wall beside it if taller; the
    blocked cells either side in the same rows, up to DOOR_RUN, are raised
    to it -- the arch and its wall are drawn that tall. The opening is the
    dark interior drawn in the door cell: its columns, and its height up
    from the foot, at least 5/8 of the wall. Where the drawing has no dark interior (a lit arch, a
    closed door) a centred opening of 8px by the height less 4px.

    Returns (H, [door dicts]).
    """
    L = r.layers[layer]
    h, w = r.cells_h, r.cells_w
    act = L["act"][:h, :w]
    tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)][:h, :w]
    walk = (cls == RE.CLASS_GROUND)
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    art = RE.room_art_rgb(r, layer)
    lum = None if art is None else art[:, :, :3].astype(float).mean(axis=2)
    H = np.array(H, dtype=np.int64)
    doors = []
    for cy in range(h - 1):
        for cx in range(w):
            if act[cy, cx] != DOOR_ACT or not blocked[cy, cx] or not walk[cy + 1, cx]:
                continue
            # the arch: the cell above, marked SURFACE_DOOR or simply wall --
            # single door cells draw their arch in the wall cell over them
            top_row = (cy - 1 if cy > 0 and (act[cy - 1, cx] == ARCH_ACT
                                             or blocked[cy - 1, cx]) else cy)
            k = cy - top_row + 1
            rows_ = range(top_row, cy + 1)
            side = [int(H[y, x]) for y in rows_ for x in (cx - 1, cx + 1)
                    if 0 <= x < w and blocked[y, x]]
            hw = max([16 * k] + side)
            foot = (cy + 1) * 16
            xa, xb, ho = 4, 12, max(8, hw - 4)
            if lum is not None and lum.shape[0] >= foot and lum.shape[1] >= cx * 16 + 16:
                cell = lum[foot - 16 * k:foot, cx * 16:cx * 16 + 16]
                dark = cell < DOOR_DARK
                cols = np.where(dark[-16:].any(axis=0))[0]
                if len(cols) >= 4:
                    xa, xb = int(cols.min()), int(cols.max()) + 1
                    rows_d = np.where(dark[:, xa:xb].mean(axis=1) > 0.5)[0]
                    if len(rows_d):
                        ho = int(16 * k - rows_d.min())
            # A stairway door draws its steps lit inside the opening, so the
            # dark reaches only a few pixels up; the arch still has to clear
            # a head. Never less than 5/8 of the wall.
            ho = int(min(max(ho, (hw * 5) // 8), hw - 2))
            for y in rows_:
                H[y, cx] = hw
                for d in (-1, 1):
                    for i in range(1, DOOR_RUN + 1):
                        x = cx + d * i
                        if not (0 <= x < w) or not blocked[y, x] or act[y, x] == DOOR_ACT:
                            break
                        if H[y, x] < hw:
                            H[y, x] = hw
            doors.append(dict(cx=cx, cy=cy, top=top_row, hw=hw, ho=ho, xa=xa, xb=xb,
                              base=int(H[cy + 1, cx]),
                              stairs=int(tt[cy, cx]) in DOOR_STAIRS))
    return H, doors


def door_quads(d, ox, oz, lift):
    """The front of a doorway with its opening carved in.

    Every face takes the art as drawn on the wall's front: a point at
    height y shows the row y above the foot, in its own column -- the
    jambs and lintel their stone, the back wall and the jambs' insides the
    dark interior. The opening's floor is the doorway's floor, with three
    steps rising into it for a stairway door.
    """
    cx, cy = d["cx"], d["cy"]
    foot = (cy + 1) * 16
    base = d["base"] + lift
    hw, ho = d["hw"] + lift, d["ho"] + d["base"] + lift
    X0 = ox + cx * 16
    xa, xb = X0 + d["xa"], X0 + d["xb"]
    X1 = X0 + 16
    zf = oz + foot
    zb = zf - DOOR_DEPTH

    def fr(pts):                        # as drawn on the front
        return [(x - ox, foot - (y - base)) for x, y, z in pts]

    q = []

    def front(x0, x1, y0, y1, z):
        if x1 > x0 and y1 > y0:
            p = [(x0, y1, z), (x1, y1, z), (x1, y0, z), (x0, y0, z)]
            q.append((p, fr(p)))
    front(X0, xa, base, hw, zf)                 # left jamb
    front(xb, X1, base, hw, zf)                 # right jamb
    front(xa, xb, ho, hw, zf)                   # lintel
    front(xa, xb, base, ho, zb)                 # back of the opening
    # inside of the jambs, facing into the opening
    p = [(xa, ho, zb), (xa, ho, zf), (xa, base, zf), (xa, base, zb)]
    q.append((p, [(xa - ox + 0.5, foot - (y - base)) for _x, y, _z in p]))
    p = [(xb, ho, zf), (xb, ho, zb), (xb, base, zb), (xb, base, zf)]
    q.append((p, [(xb - ox - 0.5, foot - (y - base)) for _x, y, _z in p]))
    # ceiling, facing down
    p = [(xa, ho, zb), (xa, ho, zf), (xb, ho, zf), (xb, ho, zb)]
    q.append((p, [(x - ox, foot - (ho - base) + 0.5) for x, _y, _z in p]))
    # floor, or steps rising into a stairway door
    n = 3 if d["stairs"] else 1
    rise = min(6, max(0, d["ho"] - 24)) if d["stairs"] else 0
    prev = base
    for i in range(n):
        za, zz = zf - (i + 1) * DOOR_DEPTH // n, zf - i * DOOR_DEPTH // n
        y = base + (rise * (i + 1)) // n if n > 1 else base
        p = [(xa, y, za), (xb, y, za), (xb, y, zz), (xa, y, zz)]
        q.append((p, [(x - ox, z - oz) for x, _y, z in p]))
        if y > prev:
            p = [(xa, y, zz), (xb, y, zz), (xb, prev, zz), (xa, prev, zz)]
            q.append((p, fr(p)))
        prev = y
    return q


# -------------------------------------------------------------- buildings --
# A building is a door with a roof over it: the top layer draws the roof
# (overhead, so Link walks behind the house), and the collision blocks the
# whole drawing. By the 45-degree rule that drawing is height plus depth,
# not a footprint: the terrain stood every building on its whole drawn
# area at one wall height, a flat slab as deep as its roof is tall.
BUILDING_HB = 0.5       # share of the drawn extent that is height
BUILDING_ROOF = 12      # px: rise of a domed or gabled roof
BUILDING_ROUND = 0.75   # roof's top row narrower than this share of its
                        # widest: rounded (a mushroom cap 0.63, roofs ~1)
BUILDING_MIN = 4        # cells: a smaller roof is a hole's lip, not a house
BUILDING_COMPACT = 0.6  # a door-less roof fills this share of its box
BUILDING_FILL = 0.5     # share of the roof cells' pixels the roof draws
BUILDING_REACH = (4, 6) # cells: roof kept within this many columns of the
                        # doors and rows above the foot (town walls join
                        # their gatehouses' roofs)
BUILDING_STEP = 4       # px: plan grid of the building volume


BUILDING_MAX = 60      # cells: a larger roof blob is not one building


def leafy_cells(top, h, w):
    """(green, teal): cells whose top-layer drawing is mostly green (hue
    60-170), or mostly blue-teal (170 to CANOPY_HUE's top), saturation at
    least CANOPY_MIN_SAT, judged pixel by pixel. Green is leaves. Teal is
    leaves in Minish Woods and roofs in Hyrule Town, which neither colour,
    texture nor the canopy blob test tells apart; find_buildings decides by
    where it is."""
    a = np.asarray(top).astype(float)
    rgb = a[:, :, :3] / 255.0
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    r_, g_, b_ = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    hue = np.where(mx == r_, ((g_ - b_) / d) % 6,
                   np.where(mx == g_, (b_ - r_) / d + 2, (r_ - g_) / d + 4)) * 60.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    drawn = a[:, :, 3] > 0
    ok = drawn & (sat >= RE.CANOPY_MIN_SAT)
    g = ok & (hue >= RE.CANOPY_HUE[0]) & (hue < 170)
    t = ok & (hue >= 170) & (hue <= RE.CANOPY_HUE[1])
    green = np.zeros((h, w), bool)
    teal = np.zeros((h, w), bool)
    for cy in range(h):
        for cx in range(w):
            sl = (slice(cy * 16, cy * 16 + 16), slice(cx * 16, cx * 16 + 16))
            dn = drawn[sl].sum()
            if dn:
                green[cy, cx] = g[sl].sum() * 2 > dn
                teal[cy, cx] = t[sl].sum() * 2 > dn
    return green, teal


def cell_hues(top, h, w):
    """Per cell: circular mean hue of its saturated drawn pixels, or None
    when it has too few (grey, white, dark)."""
    hue, sat, v = _hsv(np.asarray(top)[:, :, :3])
    drawn = np.asarray(top)[:, :, 3] > 0
    out = np.empty((h, w), dtype=object)
    for cy in range(h):
        for cx in range(w):
            sl = (slice(cy * 16, cy * 16 + 16), slice(cx * 16, cx * 16 + 16))
            m = drawn[sl] & (sat[sl] >= 0.35) & (v[sl] >= 0.25)
            if m.sum() < 32:
                out[cy, cx] = None
                continue
            a = np.radians(hue[sl][m])
            out[cy, cx] = float(np.degrees(np.arctan2(np.sin(a).mean(),
                                                      np.cos(a).mean())) % 360)
    return out


def label_by_hue(mask, hues, tol=45.0):
    """4-connected components of mask, joining neighbours only when their
    hues agree within tol (or either is grey): two houses whose roofs
    touch, green and red, are two roofs."""
    lab = np.zeros(mask.shape, np.int32)
    n = 0
    H_, W_ = mask.shape
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n += 1
        lab[y0, x0] = n
        st = [(y0, x0)]
        while st:
            y, x = st.pop()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if not (0 <= ny < H_ and 0 <= nx < W_) or not mask[ny, nx] or lab[ny, nx]:
                    continue
                a, b = hues[y, x], hues[ny, nx]
                if a is not None and b is not None:
                    d = abs(a - b) % 360
                    if min(d, 360 - d) > tol:
                        continue
                lab[ny, nx] = n
                st.append((ny, nx))
    return lab, n


def find_buildings(r, cls, H, doors, cf=None, crown=None):
    """Buildings anchored on doorways; flattens the cells they stood on.

    The roof is the top-layer blob drawn at or just above a door's arch,
    over cells the bottom layer blocks (the house's own footprint) and not
    leaf-coloured (leafy_cells: green never; teal only when the cell over
    the door is teal -- a blue roof, not the forest behind a mushroom
    cap -- and self-contained, not running on past the window as forest
    does), within BUILDING_REACH of its doors; doors
    under one roof make one building. Under BUILDING_MIN cells or
    BUILDING_FILL drawn it is a hole's lip, over BUILDING_MAX not one
    building. Its columns are
    the roof's and the doors'; its foot the doors' foot. Per column the
    drawn extent E (roof top to foot) splits into height and depth; the
    height is BUILDING_HB of the deepest, never under the door plus 8px.
    The volume stands on the front part of the drawing, depth E - height
    per column, which is where the 45-degree rule puts it; the cells
    behind it are floor. The style comes from the roof's outline: rounded
    (top row under BUILDING_ROUND of its widest: a mushroom cap) domes,
    square gables.

    Returns (H, [building dicts], set of cells the buildings replace).
    """
    import extract_art
    if not doors or not RE.overlay_overhead(r):
        return H, [], set()
    top = extract_art.room_art(r, 1)
    if top is None:
        return H, [], set()
    alpha = np.asarray(top)[:, :, 3] > 0
    L1 = r.layers[1]
    h_, w_ = r.cells_h, r.cells_w
    drawn = np.zeros((h_, w_), bool)
    for cy in range(h_):
        for cx in range(w_):
            drawn[cy, cx] = alpha[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16].any()
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    green, teal = leafy_cells(top, h_, w_)
    cand = drawn & ((L1["tile"][:h_, :w_] != 0) | (L1["collision"][:h_, :w_] != 0)) & blocked
    lab, _n = RE._label(cand & ~green & ~teal)
    # a teal roof: the cell over the door is teal, so teal is roof here
    lab_t, _nt = RE._label(cand & ~green)
    H = np.array(H, dtype=np.int64)
    groups = {}
    for i, d in enumerate(doors):
        over = [(y, d["cx"]) for y in (d["top"] - 1, d["top"]) if y >= 0]
        use_t = any(teal[c] and cand[c] for c in over)
        L_ = lab_t if use_t else lab
        ids = {int(L_[y, x]) for y in range(max(0, d["top"] - 3), d["top"] + 1)
               for x in range(d["cx"] - 2, d["cx"] + 3)
               if 0 <= x < w_ and L_[y, x]}
        if ids:
            key = ("t" if use_t else "n", min(ids))
            groups.setdefault(key, (set(), [], use_t))
            groups[key][0].update(ids)
            groups[key][1].append(i)
    # Each job is a roof blob and the doors under it. Door-anchored roofs
    # first; then compact roof blobs over blocked cells with no door facing
    # south (houses stacked in a row, doors on a side): a building all the
    # same, standing on the floor south of its blocked footprint.
    jobs, used = [], set()
    for _k, (ids, di, use_t) in groups.items():
        comp = np.isin(lab_t if use_t else lab, list(ids))
        ds = [doors[i] for i in di]
        dx_lo = min(d["cx"] for d in ds) - BUILDING_REACH[0]
        dx_hi = max(d["cx"] for d in ds) + BUILDING_REACH[0]
        foot0 = max(d["cy"] for d in ds) + 1
        win = np.zeros_like(comp)
        win[max(0, foot0 - BUILDING_REACH[1]):foot0, max(0, dx_lo):dx_hi + 1] = True
        whole = int(comp.sum())
        comp &= win
        if use_t and whole > 2 * int(comp.sum()):
            continue          # teal running on past the house: forest
        if not use_t:
            used.update(ids)
        jobs.append((comp, ds))
    # door-less roofs: leaves here are tree crowns (ragged blobs), not green
    # -- a green roof is solid; roofs split where their colours part
    used_cells = np.zeros(cand.shape, bool)
    for comp, _ds in jobs:
        used_cells |= comp
    free = cand & ~used_cells & (~crown if crown is not None else ~green)
    lab_d, n_d = label_by_hue(free, cell_hues(top, h_, w_))
    for k in range(1, n_d + 1):
        comp = lab_d == k
        ys_, xs_ = np.nonzero(comp)
        bh_, bw_ = ys_.max() - ys_.min() + 1, xs_.max() - xs_.min() + 1
        if (bh_ < 2 or bw_ < 2 or comp.sum() < BUILDING_COMPACT * bh_ * bw_
                or ys_.min() == 0 or xs_.min() == 0 or xs_.max() == w_ - 1):
            continue
        jobs.append((comp, []))

    out, covered = [], set()
    for comp, ds in jobs:
        ncell = int(comp.sum())
        if ncell < BUILDING_MIN or ncell > BUILDING_MAX:
            continue
        cmask = np.zeros(alpha.shape, bool)
        big = np.kron(comp, np.ones((16, 16), bool))
        cmask[:big.shape[0], :big.shape[1]] = big[:alpha.shape[0], :alpha.shape[1]]
        if (alpha & cmask).sum() < BUILDING_FILL * ncell * 256:
            continue
        if ds:
            foot = max(d["cy"] for d in ds) + 1
            # it stands where its front doors open: a back door on the
            # level behind it (a house built against a ledge) is not its foot
            front = [d for d in ds if d["cy"] + 1 == foot]
            base = int(max(d["base"] for d in front))
            hmin = max(d["hw"] for d in front) + 8
        else:
            # the foot: where the blocked cells under the roof end
            foot, bases = 0, []
            for c in np.where(comp.any(axis=0))[0]:
                y = int(np.where(comp[:, c])[0].max()) + 1
                for _i in range(3):
                    # down its front wall, not into the next roof
                    if y < h_ and blocked[y, c] and not cand[y, c]:
                        y += 1
                foot = max(foot, y)
            if foot >= h_:
                continue
            for c in np.where(comp.any(axis=0))[0]:
                if cls[foot, c] == RE.CLASS_GROUND:
                    bases.append(int(H[foot, c]))
            if not bases:
                # a house drawn behind another (its foot on the other's
                # roof): the floor around its footprint
                ys_, xs_ = np.nonzero(comp)
                for y in range(ys_.min(), foot):
                    for x in (xs_.min() - 1, xs_.max() + 1):
                        if 0 <= x < w_ and cls[y, x] == RE.CLASS_GROUND:
                            bases.append(int(H[y, x]))
            if not bases:
                continue
            base = int(np.bincount(np.array(bases) - min(bases)).argmax() + min(bases)) \
                if not any(cls[foot, c] == RE.CLASS_GROUND
                           for c in np.where(comp.any(axis=0))[0]) else max(bases)
            hmin = 40
        cols = sorted(set(np.where(comp.any(axis=0))[0].tolist())
                      | {d["cx"] for d in ds})
        c0, c1 = cols[0], cols[-1]
        topc = {}
        for c in range(c0, c1 + 1):
            ys = np.where(comp[:, c])[0]
            ys = ys[ys < foot]
            tr = (int(ys.min()) if len(ys) else
                  (min(d["top"] for d in ds) if ds else foot - 1))
            topc[c] = tr
        E = {c: (foot - topc[c]) * 16 for c in topc}
        hb = int(max(hmin, round(max(E.values()) * BUILDING_HB)))
        hb = 2 * ((hb + 1) // 2)
        widths = (alpha & cmask).sum(axis=1)
        widths = widths[widths > 0]
        rnd = widths[:4].mean() / float(widths.max()) if len(widths) else 1.0
        style = "dome" if rnd < BUILDING_ROUND else "gable"
        for c in range(c0, c1 + 1):
            for y in range(topc[c], foot):
                if blocked[y, c] or comp[y, c]:
                    if blocked[y, c]:
                        H[y, c] = base
                    covered.add((c, y))
        for d in ds:
            d["hw"] = hb
            d["building"] = True
        if cf is not None:
            # no tree crown over the building: its drawing touched the cap
            Ms = cf[2]
            z0 = min(topc.values()) * 16 // 2
            z1 = min(Ms.shape[0], (foot * 16 + 48) // 2)
            Ms[z0:z1, c0 * 8:(c1 + 1) * 8] = False
        # the drawn front wall: rows between the roof's lowest pixel and the
        # foot, which the undrawn side walls wear
        rrows = np.where((alpha & cmask).any(axis=1))[0]
        wall0 = int(rrows.max()) + 1 if len(rrows) else foot * 16 - 16
        if foot * 16 - wall0 < 6:
            wall0 = foot * 16 - 16
        # the band: the widest run of front columns that is not a door (a
        # doorway is dark), else the middle half of the front
        span = (c1 - c0 + 1) * 16
        dcols = {d["cx"] for d in ds}
        runs_, cur = [], []
        for c in range(c0, c1 + 1):
            if c in dcols:
                if cur:
                    runs_.append(cur)
                cur = []
            else:
                cur.append(c)
        if cur:
            runs_.append(cur)
        if runs_ and ds:
            best = max(runs_, key=len)
            band = (best[0] * 16, len(best) * 16)
        else:
            band = (c0 * 16 + span // 4, span // 2)
        out.append(dict(c0=c0, c1=c1, foot=foot, top=topc, wall=(wall0, foot * 16),
                        band=band,
                        depth={c: max(16, E[c] - hb) for c in E},
                        hb=hb, base=base, style=style, doors=ds,
                        roof=set(zip(*np.nonzero(comp)))))
    return H, out, covered


def building_quads(b, ox, oz, lift):
    """The building's volume, textured by projecting the room art from the
    game's camera -- its top takes the roof drawn above it, its front the
    wall and door drawn above its foot. Door cells are notched out full
    height (door_quads builds their front) and lidded at the roof."""
    st = BUILDING_STEP
    c0, c1, foot = b["c0"], b["c1"], b["foot"]
    dmax = max(b["depth"].values())
    rows = (dmax + st - 1) // st
    cols = (c1 - c0 + 1) * 16 // st
    gx0, gz0 = c0 * 16, foot * 16 - rows * st        # room pixels
    Hg = np.zeros((rows, cols), int)
    M = np.zeros((rows, cols), bool)
    for gx in range(cols):
        c = c0 + (gx * st) // 16
        dep = b["depth"][c]
        for gy in range(rows):
            z = gz0 + gy * st
            if z >= foot * 16 - dep:
                M[gy, gx] = True
    door_cols = {d["cx"] for d in b["doors"]}
    for gx in range(cols):
        if c0 + (gx * st) // 16 in door_cols:
            for gy in range(rows):
                if gz0 + gy * st >= foot * 16 - 16:
                    M[gy, gx] = False
    # roof: dome or gable over the plan, heights in 2px steps
    ys, xs = np.nonzero(M)
    if not len(ys):
        return []
    zc, xc = (ys.min() + ys.max() + 1) / 2.0, (xs.min() + xs.max() + 1) / 2.0
    rz, rx = max(1.0, (ys.max() - ys.min() + 1) / 2.0), max(1.0, (xs.max() - xs.min() + 1) / 2.0)
    for gy, gx in zip(ys, xs):
        v = (gy + 0.5 - zc) / rz
        u = (gx + 0.5 - xc) / rx
        if b["style"] == "dome":
            add = BUILDING_ROOF * np.sqrt(max(0.0, 1.0 - u * u - v * v))
        else:
            add = BUILDING_ROOF * max(0.0, 1.0 - abs(v))
        Hg[gy, gx] = b["hb"] + 2 * int(round(add / 2.0))
    ground = b["base"] + lift
    q = []

    def uvf(pts):
        return [(x - ox, (z - oz) - (y - ground)) for x, y, z in pts]

    # The drawing shows a building's front only. Its other outer walls --
    # east, west, north, down to the ground -- wear the drawn front wall
    # (the rows between the roof's lowest pixel and the foot, stretched to
    # the wall's height), spread along their length; projecting them from the game's camera
    # pulled one column of roof down each as a smear. Walls inside the
    # roof (the steps of a dome or gable) are roof and stay projected.
    fx0, fw = b["band"]
    zfront = oz + foot * 16
    plan_z0, plan_x0 = oz + gz0, ox + gx0
    depth_px, width_px = rows * st, cols * st

    def wall_uv(pts):
        ys = [p_[1] for p_ in pts]
        if min(ys) > ground or max(ys) == min(ys):
            return uvf(pts)
        xs_ = {p_[0] for p_ in pts}
        zs_ = {p_[2] for p_ in pts}
        if len(zs_) == 1 and next(iter(zs_)) == zfront:
            return uvf(pts)
        w0, w1 = b["wall"]
        return [(fx0 + (((z - plan_z0) / float(depth_px)) if len(xs_) == 1
                        else ((x - plan_x0) / float(width_px))) * fw,
                 w1 - min(1.0, max(0.0, (y - ground) / float(b["hb"]))) * (w1 - w0))
                for x, y, z in pts]

    def quad(pts, c, uv=None):
        q.append((pts, uv))
    RE.emit_canopy(quad, (Hg, np.zeros((rows, cols, 3)), M), ox + gx0, oz + gz0,
                   ground, step=st, uvf=wall_uv, merge=True)
    # lids over the door notches
    for d in b["doors"]:
        X0, Z1 = ox + d["cx"] * 16, oz + foot * 16
        y = ground + b["hb"]
        p = [(X0, y, Z1 - 16), (X0 + 16, y, Z1 - 16), (X0 + 16, y, Z1), (X0, y, Z1)]
        q.append((p, uvf(p)))
    return q


# -------------------------------------------------------------- uprights --
# Braziers and torches have no tile type of their own everywhere (the green
# braziers on the bridge in area 141 are plain edge tiles), so they are
# found by their drawing: a blocked cell with a flame in it. An upright has
# next to no depth, so by the 45-degree rule its whole drawing -- flame tip
# down to the foot of its post -- is height. It is built as the drawn
# silhouette extruded UPRIGHT_DEPTH, standing on the floor at its foot,
# showing the drawing on its front.
UPRIGHT_DEPTH = 8
FLAME_MIN = 6           # px: smallest flame
FLAME_BLOB = 60         # px: largest single flame
FLAME_W = 10            # px: widest flame
FLAME_W_MIN = 6         # px: narrowest; every real flame in the game is 6-9
                        # wide, the flowers and ornaments that pass are 3-5
FLAME_ALONE = 8         # px: no other flame blob this close
FLAME_BOWL = 0.6        # share of the two rows under a flame that are grey or dark
FLAME_RING = 0.7        # share of a flame's neighbours that may share its hue (its glow)
FLAME_POST = 2          # cells of post below the flame, at most


def _hsv(px):
    rgb = px[:, :, :3].astype(float) / 255.0
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    r_, g_, b_ = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    hue = np.where(mx == r_, ((g_ - b_) / d) % 6,
                   np.where(mx == g_, (b_ - r_) / d + 2, (r_ - g_) / d + 4)) * 60.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    return hue, sat, mx


def flame_ok(px, blob):
    """Is this blob of flame pixels a flame?

    Bright, saturated flame pixels (flame_mask) are not enough: foliage is
    bright saturated green, cliffs are speckled with orange, stained glass
    is both. A flame is one compact blob -- FLAME_MIN..FLAME_BLOB pixels,
    at most FLAME_W wide and taller than wide -- around a pale core (fire
    is drawn with a light centre), at least FLAME_W_MIN wide and no more than
    three times as tall as wide (stripes are), whose ring of neighbours is
    not mostly of its own hue family -- its glow is, leaves are more so --
    and that burns in something: the two rows under it are mostly grey or
    dark (a bowl, a box), where a flower sits on leaves and soil.
    px is any image the blob mask matches.
    """
    size = int(blob.sum())
    ys, xs = np.nonzero(blob)
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    if (not (FLAME_MIN <= size <= FLAME_BLOB) or bw > FLAME_W or bw < FLAME_W_MIN
            or bh <= bw or bh > 3 * bw):
        return False
    y0, y1 = max(0, ys.min() - 1), min(px.shape[0], ys.max() + 2)
    x0, x1 = max(0, xs.min() - 1), min(px.shape[1], xs.max() + 2)
    sub, m = px[y0:y1, x0:x1], blob[y0:y1, x0:x1]
    hue, sat, v = _hsv(sub)
    P = np.pad(m, 1)
    near = m | P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:]
    if not (near & (v >= 0.9) & (sat < 0.45)).any():
        return False
    warm = (hue <= 55) | (hue >= 345)
    fam = warm if warm[m].mean() >= 0.5 else ((hue >= 95) & (hue <= 165))
    ring = near & ~m
    if not ring.any() or (fam & (sat >= 0.35))[ring].mean() > FLAME_RING:
        return False
    # it burns in something: the rows under it are a grey or dark bowl
    by = int(ys.max()) + 1
    under = px[by:by + 2, xs.min():xs.max() + 1]
    if under.size == 0:
        return False
    _h, us, uv = _hsv(under)
    return float(((us < 0.35) | (uv < 0.55)).mean()) >= FLAME_BOWL


def flame_mask(px):
    """Flame pixels: bright and saturated, orange-yellow or green."""
    rgb = px[:, :, :3].astype(float) / 255.0
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    r_, g_, b_ = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    hue = np.where(mx == r_, ((g_ - b_) / d) % 6,
                   np.where(mx == g_, (b_ - r_) / d + 2, (r_ - g_) / d + 4)) * 60.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    warm = (hue <= 55) | (hue >= 345)
    green = (hue >= 95) & (hue <= 165)
    return (mx >= 0.8) & (sat >= 0.5) & (warm | green)


def find_uprights(r, cls, H, art):
    """Braziers and torches: (H, [uprights]).

    Flames are found on the whole room image, not per cell: braziers are
    drawn centred on a cell boundary as often as in a cell (the bridge of
    area 141). From each flame, its post runs down through blocked cells,
    FLAME_POST at most, to its foot; the upright spans the flame's top to
    that foot, 16px wide centred on the flame. It stands on the floor
    below its foot, or on what its post stood on (a rail, a ledge). Its
    cells go down to its base only when it stands free (floor either
    side): a rail that carries braziers keeps its rail.
    """
    h, w = r.cells_h, r.cells_w
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    walk = cls == RE.CLASS_GROUND
    H = np.array(H, dtype=np.int64)
    A = art[:h * 16, :w * 16]
    fm = flame_mask(A)
    # torches have a tile type and are built as blocks (room_explore
    # BLOCK_TYPES); uprights are the flames that have none
    L0 = r.layers[0]
    tt = L0["tiletype"][np.clip(L0["tile"], 0, len(L0["tiletype"]) - 1)][:h, :w]
    typed = np.kron(np.isin(tt, RE.TORCH_TYPES), np.ones((16, 16), bool))
    fm &= ~typed[:fm.shape[0], :fm.shape[1]]
    lab, n = RE._label(fm)
    ups = []
    for k in range(1, n + 1):
        blob = lab == k
        cnt = int(blob.sum())
        if cnt < FLAME_MIN or cnt > FLAME_BLOB or not flame_ok(A, blob):
            continue
        ys, xs = np.nonzero(blob)
        # a flame burns alone: fields of such blobs are foliage, carpet
        wy0, wy1 = max(0, ys.min() - FLAME_ALONE), ys.max() + FLAME_ALONE + 1
        wx0, wx1 = max(0, xs.min() - FLAME_ALONE), xs.max() + FLAME_ALONE + 1
        others = [int((lab[wy0:wy1, wx0:wx1] == j).sum())
                  for j in np.unique(lab[wy0:wy1, wx0:wx1]) if j and j != k]
        if sum(o for o in others if o >= 4) > cnt // 2:
            continue
        xc = int(round((xs.min() + xs.max()) / 2.0))
        cy = int(ys.max()) // 16
        # its post: under whichever cell the flame covers is blocked
        cands = sorted({min(w - 1, max(0, x // 16)) for x in (xs.min(), xc, xs.max())},
                       key=lambda c: abs(c * 16 + 8 - xc))
        cx = next((c for c in cands if blocked[cy, c]
                   or (cy + 1 < h and blocked[cy + 1, c])), cands[0])
        run = []
        y = cy
        while y < h and blocked[y, cx] and len(run) <= FLAME_POST:
            run.append(y)
            y += 1
        if not run and cy + 1 < h and blocked[cy + 1, cx]:
            run = [cy + 1]
        if not run:
            continue
        last = run[-1]
        if last + 1 >= h:
            continue
        foot = (last + 1) * 16
        top = int(ys.min())
        below = int(H[last + 1, cx])
        base = below if walk[last + 1, cx] else int(min(H[yy, cx] for yy in run))
        x0 = max(0, min(A.shape[1] - 16, xc - 8))
        region = A[top:foot, x0:x0 + 16].astype(int)
        edge = np.concatenate([region[:, 0], region[:, -1]])
        vals, counts = np.unique(edge.reshape(-1, 3), axis=0, return_counts=True)
        bg = vals[counts >= max(2, counts.max() // 3)]
        mask = np.ones(region.shape[:2], bool)
        for c in bg:
            mask &= ~(region == c).all(axis=2)
        # the flame and its glow leave the post: the flame is built as
        # voxels (upright_flame), and the glow ringed the cut-out as fringe
        fy, fx = ys - top, xs - x0
        inside = (fy >= 0) & (fy < mask.shape[0]) & (fx >= 0) & (fx < 16)
        fl = np.zeros(mask.shape, bool)
        fl[fy[inside], fx[inside]] = True
        near = fl.copy()
        for _i in range(FLAME_GLOW):
            P = np.pad(near, 1)
            near = near | P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:]
        hue_, sat_, _v = _hsv(region.astype(np.uint8))
        fhue = float(np.median(_hsv(A[ys, xs][None])[0]))
        dh = np.abs(hue_ - fhue) % 360
        glow = near & (np.minimum(dh, 360 - dh) < 40) & (sat_ >= 0.35)
        mask &= ~(fl | glow)
        if mask.sum() < FLAME_MIN * 2:
            continue
        free = all((cx - 1 >= 0 and walk[yy, cx - 1]) and (cx + 1 < w and walk[yy, cx + 1])
                   for yy in run)
        if free:
            for yy in run:
                H[yy, cx] = base
        ups.append(dict(cx=cx, x0=x0, top=top, foot=foot, base=base, mask=mask,
                        flame=(ys, xs),
                        cells=[(cx, yy) for yy in run]))
    return H, ups


# ----------------------------------------------------------------- flames --
# A flame is built as a solid, not a cut-out: the drawn flame spun about
# its upright axis, one ring per drawn row as wide as that row, so it is a
# round teardrop that shows the drawing from every side. Each voxel wears
# the drawn pixel over its offset from the axis, so the pale core stays in
# the middle and the rim outside. Flames go in their own material,
# tmc_flame, which the MTL marks emissive.
FLAME_GLOW = 2          # px: glow pixels this close to a flame leave the post


def flame_voxels(ys, xs):
    """{(x, h, dz): (u, v)} for a drawn flame: x in art columns, h voxels
    above its base, dz across the axis; (u, v) the drawn pixel."""
    vox = {}
    bottom = int(ys.max())
    for y in sorted(set(ys.tolist())):
        row = xs[ys == y]
        lo, hi = int(row.min()), int(row.max())
        R = (hi - lo + 1) / 2.0
        c = (lo + hi + 1) / 2.0
        n = int(np.ceil(R))
        for dx in range(-n, n):
            for dz in range(-n, n):
                if (dx + 0.5) ** 2 + (dz + 0.5) ** 2 > R * R:
                    continue
                x = int(np.floor(c + dx))
                u = min(max(x, lo), hi)
                vox[(x, bottom - int(y), dz)] = (u, int(y))
    return vox


def voxel_quads(vox, X0, Y0, Z0):
    """Exposed faces of a voxel set, one flat-coloured texel each, placed
    with voxel (x, h, dz) at world (X0 + x, Y0 + h, Z0 + dz). Windings as
    side_face's."""
    q = []
    S_ = set(vox)
    for (x, h, dz), (u, v) in vox.items():
        X, Y, Z = X0 + x, Y0 + h, Z0 + dz
        t = (u + 0.5, v + 0.5)
        faces = []
        if (x, h + 1, dz) not in S_:
            faces.append([(X, Y + 1, Z), (X + 1, Y + 1, Z), (X + 1, Y + 1, Z + 1), (X, Y + 1, Z + 1)])
        if (x, h - 1, dz) not in S_:
            faces.append([(X, Y, Z + 1), (X + 1, Y, Z + 1), (X + 1, Y, Z), (X, Y, Z)])
        if (x, h, dz + 1) not in S_:
            faces.append([(X, Y + 1, Z + 1), (X + 1, Y + 1, Z + 1), (X + 1, Y, Z + 1), (X, Y, Z + 1)])
        if (x, h, dz - 1) not in S_:
            faces.append([(X + 1, Y + 1, Z), (X, Y + 1, Z), (X, Y, Z), (X + 1, Y, Z)])
        if (x + 1, h, dz) not in S_:
            faces.append([(X + 1, Y + 1, Z), (X + 1, Y + 1, Z + 1), (X + 1, Y, Z + 1), (X + 1, Y, Z)])
        if (x - 1, h, dz) not in S_:
            faces.append([(X, Y + 1, Z + 1), (X, Y + 1, Z), (X, Y, Z), (X, Y, Z + 1)])
        for f in faces:
            q.append((f, [t, t, t, t]))
    return q


def torch_flames(r, art, H, lift, ox, oz):
    """Voxel flames on the torches (TORCH_TYPES, built as blocks): the
    flame drawn in the cell, standing on the box's top, centred where it
    is drawn across and in the middle of the cell front to back."""
    L = r.layers[0]
    h, w = r.cells_h, r.cells_w
    tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)][:h, :w]
    q = []
    for cy, cx in zip(*np.nonzero(np.isin(tt, RE.TORCH_TYPES))):
        px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
        if px.shape[:2] != (16, 16):
            continue
        fm = flame_mask(px)
        if fm.sum() < FLAME_MIN:
            continue
        lab, n = RE._label(fm)
        best = max(range(1, n + 1), key=lambda k: (lab == k).sum())
        ys, xs = np.nonzero(lab == best)
        if len(ys) < FLAME_MIN:
            continue
        vox = flame_voxels(ys + cy * 16, xs + cx * 16)
        q += voxel_quads(vox, ox, int(H[cy, cx]) + lift, oz + cy * 16 + 8)
    return q


def upright_flame(u, ox, oz, lift):
    """The voxel flame of an upright, on its bowl: the flame's base is the
    drawn row under it, which by the 45-degree rule is that high above the
    upright's foot."""
    if u.get("flame") is None:
        return []
    ys, xs = u["flame"]
    Y0 = u["base"] + lift + (u["foot"] - (int(ys.max()) + 1))
    return voxel_quads(flame_voxels(ys, xs), ox, Y0,
                       oz + u["foot"] - UPRIGHT_DEPTH // 2)


def upright_quads(u, ox, oz, lift):
    """The drawn silhouette of an upright, extruded and stood on its foot.

    Built with emit_canopy on a grid in the plane of the drawing (column x
    by drawn row), extrusion as its height, then turned upright: drawn row
    becomes height above the foot, extrusion depth runs north from the
    front. The turn is a proper rotation, so windings stay outward. Every
    face shows the drawing as seen from the front."""
    mask = u["mask"]
    rows, cols = mask.shape
    depth = u.get("depth", UPRIGHT_DEPTH)
    Hg = np.where(mask, depth, 0).astype(int)
    q = []
    X0 = ox + u["x0"]
    base = u["base"] + lift
    zf = oz + u["foot"]

    def quad(pts, c, uv=None):
        out = []
        for (x, y, z) in pts:
            # emit frame: x across, y = extrusion, z = drawn row from top
            X = x
            Y = base + (u["foot"] - (u["top"] + z))
            Z = zf - depth + y
            out.append((X, Y, Z))
        uv = [(x - ox, u["top"] + z) for (x, _y, z) in pts]
        q.append((out, uv))
    RE.emit_canopy(quad, (Hg, np.zeros((rows, cols, 3)), mask), X0, 0, 0,
                   step=1, uvf=None, merge=True)
    return q


def hang_between(ups):
    """A rope (or chain) between two posts is drawn in the air: its
    drawing ends above the ground. It shares the posts' foot -- the line
    they stand on -- so each drawn row is as high above it as the game
    draws it, and it hangs between them."""
    at = {}
    for u in ups:
        for (cx, cy) in u["cells"]:
            at[(cx, cy)] = u
    for u in ups:
        cx, cy = u["cells"][0]
        feet = [at[(cx + d, cy)]["foot"] for d in (-1, 1)
                if (cx + d, cy) in at and at[(cx + d, cy)] is not u]
        if not feet:
            continue
        f = max(feet)
        if f > u["foot"] + HANG_MIN:
            u["foot"] = f
            u["hung"] = True


HANG_MIN = 2            # px: a drawing ending this far above its posts' foot hangs


CLIFF_DROP = 6          # px: ground this much lower lies below a cliff
CLIFF_BEHIND = 3        # cells: how far behind the band the ground above may be


def cliff_bands(r, cls, H, fam, art, role, taken=()):
    """Cliffs drawn folded out: a band of rock between the ground above and
    the ground below where the face could not be seen from the south (it
    faces north, east or west) -- the game draws it laid out beyond the
    top, like a room's walls (docs/vr/06). The band's pixels are the ones
    in neither ground's colours, with any of its teeth reaching into the
    next cell. Each becomes a face standing at the band's low edge (its
    foot), from the ground below to the ground above, wearing the band --
    the foot's edge at the bottom, the top's at the top -- a strip per
    pixel column (row, facing east or west); the band's cells become the
    ground above, drawn as the ground behind them.

    Returns dict(cells: {(cy, cx): (height, role, 16x16 pixels)},
    faces: [(corners in room px, texels)], skip: {(cx, cy, dx, dz)})."""
    h, w = cls.shape
    A = np.asarray(art)[:h * 16, :w * 16, :3]
    key = TI._pack(A)
    Hh = np.asarray(H)
    low_ok = np.isin(cls, [RE.CLASS_GROUND, RE.CLASS_WATER])
    out = dict(cells={}, faces=[], skip=set())
    found = {}
    for cy, cx in zip(*np.nonzero(cls == RE.CLASS_WALL)):
        if fam[cy, cx] is not None or (cx, cy) in taken:
            continue
        for dx, dz in ((0, -1), (1, 0), (-1, 0)):
            ny, nx = cy + dz, cx + dx
            if not (0 <= ny < h and 0 <= nx < w) or not low_ok[ny, nx]:
                continue
            behind = None
            for k in range(1, CLIFF_BEHIND + 1):
                by, bx = cy - dz * k, cx - dx * k
                if not (0 <= by < h and 0 <= bx < w):
                    break
                if cls[by, bx] == RE.CLASS_GROUND and fam[by, bx] is None:
                    behind = (by, bx)
                    break
            if behind is None:
                continue
            lo, hi = int(Hh[ny, nx]), int(Hh[behind])
            if hi - lo < CLIFF_DROP:
                continue
            found[(cy, cx)] = (dx, dz, lo, hi, behind, (ny, nx))
            break
    if not found:
        return out

    def cellpx(c):
        return key[c[0] * 16:c[0] * 16 + 16, c[1] * 16:c[1] * 16 + 16]
    for (cy, cx), (dx, dz, lo, hi, behind, below) in found.items():
        grounds = np.concatenate([cellpx(behind).ravel(), cellpx(below).ravel()])
        own = cellpx((cy, cx))
        band = ~np.isin(own, grounds)
        if band.sum() < 16:
            continue
        pal = np.unique(own[band])
        # its teeth: the band's colours reaching on into the cell beyond,
        # joined to it
        ty, tx = cy - dz, cx - dx
        big = np.zeros((48, 48), bool)
        big[16:32, 16:32] = band
        if 0 <= ty < h and 0 <= tx < w and (tx, ty) != (behind[1], behind[0]) or True:
            if 0 <= ty < h and 0 <= tx < w:
                tp = np.isin(cellpx((ty, tx)), pal)
                big[16 - dz * 16:32 - dz * 16, 16 - dx * 16:32 - dx * 16] |= tp
        lab_, _n = RE._label(big)
        ids = np.unique(lab_[16:32, 16:32][band])
        big = np.isin(lab_, ids[ids > 0])
        # the cell becomes the ground above: its band drawn as the ground
        # behind it
        px = np.array(A[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16])
        bpx = A[behind[0] * 16:behind[0] * 16 + 16, behind[1] * 16:behind[1] * 16 + 16]
        px[band] = bpx[band]
        out["cells"][(cy, cx)] = (hi, role[behind] or "floor", px)
        out["skip"].add((int(cx), int(cy), dx, dz))
        # the face, strip by strip
        X0, Z0 = cx * 16 - 16, cy * 16 - 16          # big's origin in room px
        runs = []
        for i in range(16):
            if dz:                                   # facing north: columns
                col = big[:, 16 + i]
                zs = np.nonzero(col)[0]
                runs.append((int(zs.min()) + Z0, int(zs.max()) + 1 + Z0) if len(zs) else None)
            else:                                    # east or west: rows
                row = big[16 + i, :]
                xs_ = np.nonzero(row)[0]
                runs.append((int(xs_.min()) + X0, int(xs_.max()) + 1 + X0) if len(xs_) else None)
        known = [q_ for q_ in runs if q_ is not None]
        if not known:
            continue
        for i in range(16):                          # a gap takes its neighbour's
            if runs[i] is None:
                j = min((j for j in range(16) if runs[j] is not None), key=lambda j: abs(j - i))
                runs[i] = runs[j]
        i = 0
        while i < 16:
            j = i
            while j + 1 < 16 and runs[j + 1] == runs[i]:
                j += 1
            a_, b_ = runs[i]
            if dz:                                   # north: foot at a_, top at b_
                x0_, x1_, Z = cx * 16 + i, cx * 16 + j + 1, cy * 16
                p = [(x1_, hi, Z), (x0_, hi, Z), (x0_, lo, Z), (x1_, lo, Z)]
                uv = [(x1_, b_), (x0_, b_), (x0_, a_), (x1_, a_)]
            elif dx == 1:                            # east: foot at b_, top at a_
                z0_, z1_, X = cy * 16 + i, cy * 16 + j + 1, cx * 16 + 16
                p = [(X, hi, z0_), (X, hi, z1_), (X, lo, z1_), (X, lo, z0_)]
                uv = [(a_, z0_), (a_, z1_), (b_, z1_), (b_, z0_)]
            else:                                    # west: foot at a_, top at b_
                z0_, z1_, X = cy * 16 + i, cy * 16 + j + 1, cx * 16
                p = [(X, hi, z1_), (X, hi, z0_), (X, lo, z0_), (X, lo, z1_)]
                uv = [(b_, z1_), (b_, z0_), (a_, z0_), (a_, z1_)]
            out["faces"].append((p, uv))
            i = j + 1
    return out


def drawn_upright(r, cls, fam, art, cy, cx, family, ground, base):
    """A fence or a signpost as an upright: its drawing (the wood of a
    fence, the whole of a sign, off the room's floor colours) stood on the
    floor at its foot, TI.UPRIGHT_FAMILIES deep. The cell under it shows
    the floor beside it -- the drawing is not also painted on the ground.
    None if nothing is drawn."""
    px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
    if family == "fence":
        blobs = TI.fence_blobs(px, ground)
        m = np.zeros((16, 16), bool)
        for b in blobs:
            m |= b
    else:
        m = TI.outline(px, None) & ~np.isin(TI._pack(px[..., :3]), ground)
    if m.sum() < TI.POST_MIN:
        return None
    ys = np.nonzero(m.any(axis=1))[0]
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    h, w = cls.shape
    under = (cy, cx)
    for y, x in ((cy + 1, cx), (cy - 1, cx), (cy, cx - 1), (cy, cx + 1)):
        if 0 <= y < h and 0 <= x < w and cls[y, x] == RE.CLASS_GROUND and fam[y, x] is None:
            under = (y, x)
            break
    return dict(cx=cx, x0=cx * 16, top=cy * 16 + y0, foot=cy * 16 + y1, base=base,
                mask=m[y0:y1], cells=[(cx, cy)], depth=TI.UPRIGHT_FAMILIES[family],
                under=under)


def tile_key(pixels, role, hmap=None):
    """A model's key: its drawing, its role and, for models whose shape is
    not the drawing's own relief, the shape."""
    k = hashlib.sha1(pixels.tobytes())
    if hmap is not None:
        k.update(np.ascontiguousarray(hmap, dtype=np.int16).tobytes())
    return k.hexdigest()[:12] + ROLE_CODE.get(role, role[:2])


ROLE_CODE = {"floor": "f", "indoor": "i", "wallflat": "wf", "furniture": "fu", "wall": "w", "block": "k", "water": "a", "pit": "p",
             "deck": "d", "grass": "g", "bush": "bu", "sapling": "sa", "rock": "ro",
             "mushroom": "mu", "stump": "st", "planter": "pl", "prop": "pr", "pot": "po",
             "stone": "sn", "boulder": "bo", "spiky_rock": "sk", "hedge": "hg",
             "signpost": "si", "foliage": "fo", "flowers": "fl"}


# -------------------------------------------------------------- voxelate --

def tile_relief(px, R, step=None, mask=None):
    """Relief 0..R per pixel from the drawing's own shading.

    Measured on step x step blocks: per pixel, dithering and outlines turn
    the relief into noise -- 160 quads a cell in Minish Woods -- where 2px
    blocks keep the grass tufts and stones and cost 40. The texture stays
    at full resolution either way. With a mask, only drawn pixels count:
    a cut-out tile's transparent pixels are not dark ones.
    """
    step = step or RELIEF_STEP
    if R <= 0:
        return np.zeros(px.shape[:2], np.int64)
    lum = px[:, :, :3].astype(float).mean(axis=2)
    n = 16 // step
    if mask is None:
        b = lum.reshape(n, step, n, step).mean(axis=(1, 3))
        seen = np.ones((n, n), bool)
    else:
        wsum = mask.reshape(n, step, n, step).sum(axis=(1, 3))
        b = (np.where(mask, lum, 0.0).reshape(n, step, n, step).sum(axis=(1, 3))
             / np.maximum(wsum, 1))
        seen = wsum > 0
        if not seen.any():
            return np.zeros(lum.shape, np.int64)
    lo, hi = np.percentile(b[seen], 10), np.percentile(b[seen], 90)
    if hi - lo < FLAT_SPREAD:
        return np.zeros(lum.shape, np.int64)
    t = np.rint(np.clip((b - lo) / (hi - lo), 0.0, 1.0) * R).astype(np.int64)
    return np.kron(t, np.ones((step, step), np.int64))


def tile_quads(px, R, mask=None, step=None, hmap=None):
    """The voxel model of one drawing: [(4 corners, 4 texels)], y up from 0.

    Columns of 1 + relief voxels, one per pixel. Colour comes from the
    drawing through texture coordinates -- (s, t) in the tile's own pixels
    -- so faces merge by SHAPE alone: a flat floor tile is one top quad
    whatever its dithering, where one colour per face made it ninety.
    Tops are greedy-merged by height. A side is emitted where a column
    stands above its neighbour, or above the floor at the tile's edge,
    merged along its row; its texels run along the pixels it borders, so
    each column's side shows that column's pixel.

    mask (16x16 bool) keeps only the pixels a layer actually draws: a
    top-layer tile is cut out along its transparency, so a fence or a roof
    edge has the drawn silhouette instead of a square plate.

    hmap (16x16, 0 = no column) gives the columns' heights outright, for
    the identified families (tileid): a bush's dome, a stump's drum.
    """
    h = (np.asarray(hmap, dtype=np.int64) if hmap is not None
         else 1 + tile_relief(px, R, step=step, mask=mask))
    if mask is not None:
        h = np.where(mask, h, 0)
    quads = []
    for y in sorted(set(int(v) for v in np.unique(h)) - {0}):
        for x, z, w, d in RE.greedy_quads(h == y):
            quads.append(([(x, y, z), (x + w, y, z), (x + w, y, z + d), (x, y, z + d)],
                          [(x, z), (x + w, z), (x + w, z + d), (x, z + d)]))

    def nb(z, x):
        return int(h[z, x]) if 0 <= z < 16 and 0 <= x < 16 else 0

    for dz in (1, -1):              # faces along x, merged along x
        for z in range(16):
            run = None
            for x in range(17):
                seg = None
                if x < 16:
                    lo, hi = nb(z + dz, x), int(h[z, x])
                    if hi > lo:
                        seg = (lo, hi)
                if run and seg == run[1]:
                    continue
                if run:
                    x0, (lo, hi) = run
                    zz, t = (z + 1 if dz == 1 else z), z + 0.5
                    if dz == 1:
                        p = [(x0, hi, zz), (x, hi, zz), (x, lo, zz), (x0, lo, zz)]
                        uv = [(x0, t), (x, t), (x, t), (x0, t)]
                    else:
                        p = [(x, hi, zz), (x0, hi, zz), (x0, lo, zz), (x, lo, zz)]
                        uv = [(x, t), (x0, t), (x0, t), (x, t)]
                    quads.append((p, uv))
                run = (x, seg) if seg else None
    for dx in (1, -1):              # faces along z, merged along z
        for x in range(16):
            run = None
            for z in range(17):
                seg = None
                if z < 16:
                    lo, hi = nb(z, x + dx), int(h[z, x])
                    if hi > lo:
                        seg = (lo, hi)
                if run and seg == run[1]:
                    continue
                if run:
                    z0, (lo, hi) = run
                    xx, sx = (x + 1 if dx == 1 else x), x + 0.5
                    if dx == 1:
                        p = [(xx, hi, z0), (xx, hi, z), (xx, lo, z), (xx, lo, z0)]
                        uv = [(sx, z0), (sx, z), (sx, z), (sx, z0)]
                    else:
                        p = [(xx, hi, z), (xx, hi, z0), (xx, lo, z0), (xx, lo, z)]
                        uv = [(sx, z), (sx, z0), (sx, z0), (sx, z)]
                    quads.append((p, uv))
                run = (z, seg) if seg else None
    return quads


# ---------------------------------------------------------- standing --
# A thing standing on the ground -- a sapling, a bush, a rock, a pot -- is
# drawn the way the game's camera sees it: a point (x, y, z) shows at
# (x, z - y), so the drawing's foot row is where it stands and each row
# above it is a slice that much higher. Read that way, a sapling is a thin
# trunk under its crown, a bush a mound as wide as its outline at each
# height -- not a dome over the whole drawing as if it were seen from
# above. Each slice is a disc (as deep as it is wide) round the slice's own
# middle; the stack stands on the cell's ground at y = 0 and never below.
# Every face shows the pixel the camera would see there, (x, z - y), or
# the nearest pixel of the thing in that row: no colour is stretched down
# a side. The ground round it takes the drawing's own ground pixels.
STAND_FAMILIES = ("sapling", "mushroom")     # drawn standing up: slices
MOUND_FAMILIES = ("bush", "rock", "stone", "boulder", "pot", "stump",
                  "spiky_rock")              # drawn mostly from above: mounds
SHAPED_FAMILIES = STAND_FAMILIES + MOUND_FAMILIES
MOUND_MIN_DEPTH = 2.0   # px: half-depth a mound column keeps at least


def _nearest(sel):
    """For each pixel of a 16x16 tile, the nearest pixel where sel is true
    (rows count double: a row is a height step, a column is not)."""
    yy, xx = np.mgrid[0:16, 0:16]
    pts = np.argwhere(sel)
    if not len(pts):
        return np.stack([yy, xx], -1)
    d = (yy[..., None] - pts[:, 0]) ** 2 * 4 + (xx[..., None] - pts[:, 1]) ** 2
    return pts[d.argmin(-1)]


def _shaped_quads(vox, m, row_of):
    """Faces of a voxel solid vox[y, z, x] (y = 0 is a one-voxel ground
    plate) for a thing drawn with mask m. row_of(fx, fy, fz, n) gives the
    drawn row a face centre (fx, fy, fz) with outward normal n shows; the
    face takes that row's pixel in its column, or the thing's nearest pixel
    in the row -- so no colour runs down a side. The plate shows the
    drawing's own ground."""
    thing, ground = _nearest(m), _nearest(~m)
    n = vox.shape[0]
    quads = []

    def filled(y, z, x):
        return 0 <= y < n and 0 <= z < 16 and 0 <= x < 16 and vox[y, z, x]

    def texel(fx, fy, fz, nrm, plate):
        x = min(15, max(0, int(fx)))
        if plate:
            r_, c_ = ground[min(15, max(0, int(fz))), x]
        else:
            row = min(15, max(0, int(row_of(fx, fy, fz, nrm))))
            r_, c_ = (row, x) if m[row, x] else thing[row, x]
        return [(c_ + 0.5, r_ + 0.5)] * 4
    for y in range(n):
        plate = y == 0
        for z in range(16):
            for x in range(16):
                if not vox[y, z, x]:
                    continue
                y0, y1 = y, y + 1
                if not filled(y + 1, z, x):
                    quads.append(([(x, y1, z), (x + 1, y1, z), (x + 1, y1, z + 1), (x, y1, z + 1)],
                                  texel(x + .5, y1, z + .5, (0, 1, 0), plate)))
                if not filled(y, z + 1, x):
                    quads.append(([(x, y1, z + 1), (x + 1, y1, z + 1), (x + 1, y0, z + 1), (x, y0, z + 1)],
                                  texel(x + .5, y + .5, z + 1, (0, 0, 1), plate)))
                if not filled(y, z - 1, x):
                    quads.append(([(x + 1, y1, z), (x, y1, z), (x, y0, z), (x + 1, y0, z)],
                                  texel(x + .5, y + .5, z, (0, 0, -1), plate)))
                if not filled(y, z, x + 1):
                    quads.append(([(x + 1, y1, z + 1), (x + 1, y1, z), (x + 1, y0, z), (x + 1, y0, z + 1)],
                                  texel(x + 1, y + .5, z + .5, (1, 0, 0), plate)))
                if not filled(y, z, x - 1):
                    quads.append(([(x, y1, z), (x, y1, z + 1), (x, y0, z + 1), (x, y0, z)],
                                  texel(x, y + .5, z + .5, (-1, 0, 0), plate)))
    return quads


SHAPE_GROUND_D = 90     # sum |RGB|: a pixel this close to a ground colour is ground


def off_ground(px, gcols):
    """The drawn thing's pixels: the largest blob of pixels farther than
    SHAPE_GROUND_D from every one of the room's ground colours -- the
    grass round a sapling's roots, in shades the outline test keeps, is
    ground, not sapling. Everything when the room has no ground colours
    or nothing is left."""
    if not len(gcols):
        return np.ones(px.shape[:2], bool)
    d = np.abs(px[..., :3].astype(np.int64)[:, :, None, :] - gcols[None, None]).sum(-1).min(-1)
    m = d > SHAPE_GROUND_D
    lab, n = RE._label(m)
    if not n:
        return np.ones(px.shape[:2], bool)
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    keep = lab == sizes.argmax()
    return keep if keep.sum() >= 12 else np.ones(px.shape[:2], bool)


def stand_quads(px, m):
    """A thing drawn standing up (a sapling, a mushroom): the drawing's
    foot row is where it stands and each row above is a slice that much
    higher, a disc as wide as the drawing at that row -- a trunk under a
    crown. Stands on the cell's ground, never below it."""
    m = np.asarray(m, bool)
    rows = np.nonzero(m.any(axis=1))[0]
    if not len(rows):
        return tile_quads(px, 1)
    foot, top = int(rows.max()), int(rows.min())
    Hh = foot - top + 1
    R = np.zeros(Hh)
    XC = np.zeros(Hh)
    for hh in range(Hh):
        xs = np.nonzero(m[foot - hh])[0]
        if len(xs):
            R[hh] = (xs.max() - xs.min() + 1) / 2.0
            XC[hh] = (xs.min() + xs.max() + 1) / 2.0
        elif hh:
            R[hh], XC[hh] = R[hh - 1], XC[hh - 1]
    zc = foot + 1 - max(R[0], 1.0)          # the base's front is the foot row
    yy, xx = np.mgrid[0:16, 0:16]
    vox = np.zeros((Hh + 1, 16, 16), bool)
    vox[0] = True
    for hh in range(Hh):
        if R[hh] > 0:
            vox[hh + 1] = (xx + 0.5 - XC[hh]) ** 2 + (yy + 0.5 - zc) ** 2 <= R[hh] ** 2
    # each slice wears its own drawn row all the way round, as a label
    # round a can: the front is the drawing, the unseen back its mirror
    return _shaped_quads(vox, m, lambda fx, fy, fz, n: foot - max(0, int(np.floor(fy - 0.5)) - 1))


def mound_quads(px, m, height):
    """A low thing drawn mostly from above (a bush, a rock, a pot): a
    mound, fitted column by column so the game's camera draws it exactly
    as wide and as tall as the drawing. In each column a half-ellipse of
    height h and half-depth d is drawn from its front at the foot row up
    d + sqrt(d^2 + h^2) rows (a point at height y over z shows at row
    z - y); the column's drawn span fixes d for its h. The mound's h is the
    family's height, rounding off across the thing's width and capped so
    every column keeps some depth. Stands on the cell's ground."""
    m = np.asarray(m, bool)
    cols = np.nonzero(m.any(axis=0))[0]
    if not len(cols):
        return tile_quads(px, 1)
    x0_, x1_ = int(cols.min()), int(cols.max()) + 1
    xc, rx = (x0_ + x1_) / 2.0, (x1_ - x0_) / 2.0
    hmax = int(np.ceil(height)) + 1
    vox = np.zeros((hmax + 1, 16, 16), bool)
    vox[0] = True
    zz = np.arange(16) + 0.5
    ZC = np.full(16, 8.0)
    T = np.zeros(16, int)
    for x in cols:
        ys = np.nonzero(m[:, x])[0]
        t, b = int(ys.min()), int(ys.max())
        span = b - t + 1
        h = height * np.sqrt(max(0.0, 1 - ((x + 0.5 - xc) / rx) ** 2))
        h = min(h, span - 2 * MOUND_MIN_DEPTH) if span > 2 * MOUND_MIN_DEPTH else 1.0
        h = max(h, 1.0)
        d = max(MOUND_MIN_DEPTH, (span * span - h * h) / (2.0 * span))
        zc = b + 1 - d
        ZC[x], T[x] = zc, t
        prof = h * np.sqrt(np.clip(1 - ((zz - zc) / d) ** 2, 0, 1))
        for z in range(16):
            top = int(np.rint(prof[z]))
            if top > 0:
                vox[1:top + 1, z, x] = True

    def row_of(fx, fy, fz, n):
        # what the game's camera sees keeps its drawn pixel, (x, z - y);
        # a face it cannot see -- turned away, or behind the drawn top --
        # wears its mirror point on the front, so the back stays the
        # thing's own
        x = min(15, max(0, int(fx)))
        row = int(np.floor(fz - fy))
        if n[2] < 0 or row < T[x]:
            row = int(np.floor(2 * ZC[x] - fz - fy))
        return row
    return _shaped_quads(vox, m, row_of)


# ---------------------------------------------------------------- hedges --
# A clipped hedge is a box: the game draws its lit top, and below that its
# dark front band, all inside a dark outline. Its collision is one row of
# wall cells -- the front band's -- and its top is drawn in the cell above
# (a point at height y over z shows at row z - y). Built as wall cells it
# stood 16 px tall with the front band laid flat on top, and the lit top
# lay flat on the ground behind it. Read from the drawing instead, each
# column's run of leaf is top rows over front rows: the front band is the
# box's height, the rest its depth, and the box ends where the drawing's
# outline does. Its top wears the drawing h rows up, its front the band.
HEDGE_LEAF_SHARE = 0.6  # of a wall cell's pixels in leaf green, for a hedge
HEDGE_DARK_V = 0.72     # brightness: below, a leaf pixel is the front band
HEDGE_OUTLINE_V = 0.3   # brightness: below (and not leaf), the outline
HEDGE_MIN_H = 4         # px: shortest front a hedge is built from
HEDGE_LONG = 3          # cells: a hedge runs at least this far
HEDGE_TWO_TONE = 0.75   # share of its columns drawn lit top then dark front
                        # (a tree's leaf shading turns over and over)


def hedge_cells(cls, fam, art):
    """{(cy, cx): quads} for a room's clipped hedges, cell-local, with
    texture coordinates in the room's art relative to the cell (so they
    may reach into the cell above); and the wall cells they replace."""
    h_, w_ = cls.shape
    A = art[:h_ * 16, :w_ * 16]
    hue, sat, v = TI._hsv(A)
    leaf = (hue >= 60) & (hue < 170) & (sat >= 0.3)
    outline = ~leaf & (v < HEDGE_OUTLINE_V)
    cand = np.zeros((h_, w_), bool)
    for cy, cx in zip(*np.nonzero((cls == RE.CLASS_WALL) | (fam == "foliage"))):
        if fam[cy, cx] not in (None, "foliage"):
            continue
        sl = (slice(cy * 16, cy * 16 + 16), slice(cx * 16, cx * 16 + 16))
        lf = leaf[sl]
        if lf.mean() + outline[sl].mean() >= HEDGE_LEAF_SHARE + 0.2 and lf.mean() >= 0.4 \
                and sat[sl][lf].mean() >= TI.HEDGE_SAT and (v[sl][lf] < HEDGE_DARK_V).any():
            cand[cy, cx] = True
    if not cand.any():
        return {}, set()
    candpx = np.kron(cand, np.ones((16, 16), bool))
    region = cand | np.vstack([cand[1:], np.zeros((1, w_), bool)])   # and the cell above
    regpx = np.kron(region, np.ones((16, 16), bool))
    Hp, Wp = A.shape[:2]
    # each column's runs of leaf through a hedge cell: [t, b], outlines
    runs = []
    for x in range(Wp):
        col = leaf[:, x] & regpx[:, x]
        y = 0
        while y < Hp:
            if not col[y]:
                y += 1
                continue
            t = y
            while y < Hp and col[y]:
                y += 1
            b = y - 1
            if not candpx[t:b + 1, x].any():
                continue
            f = 0
            while b - f >= t and v[b - f, x] < HEDGE_DARK_V:
                f += 1
            ob = 0
            while b + 1 + ob < Hp and ob < 4 and outline[b + 1 + ob, x]:
                ob += 1
            ot = 0
            while t - 1 - ot >= 0 and ot < 4 and outline[t - 1 - ot, x]:
                ot += 1
            dk = v[t:b + 1, x] < HEDGE_DARK_V
            flips = int((dk[:-1] & ~dk[1:]).sum())      # dark back to lit
            if (~dk).sum() < 3:
                flips = 99                              # no lit top: no hedge
            runs.append((x, t - ot, b + ob + 1, f + ob, f, flips))
    # a hedge is drawn two-tone, lit top over dark front; a tree's leaf
    # clumps are shaded light and dark over and over.
    # Judged per connected stretch of candidate cells, by the runs whose
    # foot is in it
    lab, nlab = RE._label(cand)
    def comp(x, zs):
        cy_, cx_ = min(h_ - 1, (zs - 1) // 16), x // 16
        return lab[cy_, cx_]
    per = {}
    for x, zn, zs, hf, f, fl in runs:
        per.setdefault(comp(x, zs), []).append((hf, f, fl))
    hh_of = {}
    for c_, rs in per.items():
        if not c_:
            continue
        ys_, xs_ = np.nonzero(lab == c_)
        if max(ys_.max() - ys_.min(), xs_.max() - xs_.min()) + 1 < HEDGE_LONG:
            continue            # a round shrub, not a hedge
        fr = [(hf, fl) for hf, f, fl in rs if f >= HEDGE_MIN_H]
        if len(fr) < 8:
            continue
        med = float(np.median([hf for hf, _ in fr]))
        two = np.mean([fl <= 1 for _, fl in fr])
        if os.environ.get("TV_DBG"):
            print("HEDGEC", c_, len(fr), med, round(two, 2), file=sys.stderr)
        if two >= HEDGE_TWO_TONE:
            hh_of[c_] = int(round(med))
    if not hh_of:
        return {}, set()
    keep = np.isin(lab, list(hh_of))
    cand &= keep
    region = cand | np.vstack([cand[1:], np.zeros((1, w_), bool)])
    runs = [(x, zn, zs, hf, f, hh_of[comp(x, zs)]) for x, zn, zs, hf, f, _fl in runs
            if comp(x, zs) in hh_of]
    box = np.zeros((Hp, Wp), int)            # height over each pixel
    south = np.zeros((Hp, Wp), int)          # the column's south edge
    north = np.zeros((Hp, Wp), int)          # the column's drawn north edge
    for x, zn, zs, _hf, _f, hh in runs:
        z0 = max(zn, zs - max(2, zs - zn - hh))
        box[z0:zs, x] = hh
        south[z0:zs, x] = zs
        north[z0:zs, x] = zn
    hedge = np.zeros((Hp, Wp), bool)         # drawn as hedge (top, front, outline)
    for x, zn, zs, _hf, _f, _h in runs:
        hedge[zn:zs, x] = True
    # the ground the box was drawn over: what lies beyond its outline,
    # mirrored -- the grass or path behind it, row for row, not one row
    # stretched (a smear)
    gsrc = np.tile(np.arange(Hp)[:, None], (1, Wp))
    for x in range(Wp):
        y = 0
        while y < Hp:
            if not hedge[y, x]:
                y += 1
                continue
            t = y
            while y < Hp and hedge[y, x]:
                y += 1
            for yy in range(t, y):
                up_, dn_ = 2 * t - 1 - yy, 2 * y - 1 - yy   # mirrored north, south
                for src in ((up_, dn_) if yy - t < y - yy else (dn_, up_)):
                    if 0 <= src < Hp and not hedge[src, x]:
                        gsrc[yy, x] = src
                        break
                else:
                    gsrc[yy, x] = t - 1 if t > 0 else min(Hp - 1, y)

    def uv(row, x, cy, cx):
        row = min(Hp - 1, max(0, int(row)))
        return [(x - cx * 16 + 0.5, row - cy * 16 + 0.5)] * 4

    out = {}
    for cy, cx in zip(*np.nonzero(region)):
        q = []
        for zl in range(16):
            for xl in range(16):
                z, x = cy * 16 + zl, cx * 16 + xl
                hgt = box[z, x]
                if not hgt:
                    q.append(([(xl, 0, zl), (xl + 1, 0, zl), (xl + 1, 0, zl + 1), (xl, 0, zl + 1)],
                              uv(gsrc[z, x], x, cy, cx)))
                    continue
                zs, zn = south[z, x], north[z, x]
                # every face wears the hedge's own drawing, never the path
                # or the grass beyond its outline
                band = lambda row: min(zs - 1, max(zn, row))
                q.append(([(xl, hgt, zl), (xl + 1, hgt, zl), (xl + 1, hgt, zl + 1), (xl, hgt, zl + 1)],
                          uv(band(z - hgt), x, cy, cx)))
                for y in range(hgt):
                    y0, y1 = y, y + 1
                    if z + 1 >= Hp or box[z + 1, x] < y1:      # front: the band
                        q.append(([(xl, y1, zl + 1), (xl + 1, y1, zl + 1), (xl + 1, y0, zl + 1), (xl, y0, zl + 1)],
                                  uv(band(z - y), x, cy, cx)))
                    if z == 0 or box[z - 1, x] < y1:           # back: the band again
                        q.append(([(xl + 1, y1, zl), (xl, y1, zl), (xl, y0, zl), (xl + 1, y0, zl)],
                                  uv(band(zs - 1 - y), x, cy, cx)))
                    if x + 1 >= Wp or box[z, x + 1] < y1:      # ends: the band
                        q.append(([(xl + 1, y1, zl + 1), (xl + 1, y1, zl), (xl + 1, y0, zl), (xl + 1, y0, zl + 1)],
                                  uv(band(zs - 1 - y), x, cy, cx)))
                    if x == 0 or box[z, x - 1] < y1:
                        q.append(([(xl, y1, zl), (xl, y1, zl + 1), (xl, y0, zl + 1), (xl, y0, zl)],
                                  uv(band(zs - 1 - y), x, cy, cx)))
        out[(int(cy), int(cx))] = q
    return out, {(int(cy), int(cx)) for cy, cx in zip(*np.nonzero(cand))}


# ----------------------------------------------------------------- shell --
# An enclosed room -- a house, a Minish home, a dungeon room -- is a floor
# inside a ring of wall of one height. Cell by cell the ring came out a
# staircase: the back wall stood as tall as its drawn face, the sides and
# the front 16, and an oval room was squares. The shell builds the ring as
# one prism, at pixel resolution, from the 45-degree rule: a wall's top is
# drawn Hw rows above where it stands, so its plan is the drawn wall moved
# down Hw -- less wherever floor is drawn, which is where the back wall's
# face ends and doorways open. That one rule gives the back wall its drawn
# face (moulding, moss), shows the sides and front by their tops, keeps
# doorways in any wall, and follows an oval. Every face is textured by
# projection: a point (x, y, z) shows the art at (x, z - y).
SHELL_REACH = 4         # cells: wall this far from the floor is the ring
SHELL_H = (16, 48)      # px: the ring's height, from the back wall's


# Heights of furniture, from the people who use it. Link stands about 119cm
# (a Wind Waker child) and his sprite about 24px: some 5cm a pixel. Minish
# rooms are drawn at Minish scale (Link 5.5cm in the same 24px), so their
# furniture takes the same pixel heights; a human thing seen at Minish
# scale -- a 25cm book on the library shelf -- is ~108px, and the shelves
# there stand ~100px apart. By Picori's furniture type, else a table's.
def cell_labels(r, h, w):
    """{(cy, cx): LABEL} from the overrides file's label= rules: what a
    person has said a thing is (vr/tiles/overrides.txt)."""
    out = {}
    for rule in TI.load_overrides():
        if "label" in rule:
            m = TI.override_mask(r, rule)
            for y, x in zip(*np.nonzero(m[:h, :w])):
                out[(int(y), int(x))] = rule["label"].upper()
    return out


# a thing on the wall, drawn on it: part of the wall, not an object
RELIEF_WORDS = ("WINDOW", "CURTAIN", "POSTER", "PAINTING", "PICTURE", "RELIEF",
                "MOULDING", "MOLDING", "TRIM", "EMBLEM", "SHELF_ON_WALL")


LINK_PX = 24
FURNITURE_PX = (        # (words in the object's name, px)
    (("STOOL", "CHAIR", "SEAT", "BENCH", "SHOES", "MUG", "CHEESE", "APPLE", "COOKIES"), 8),
    (("BED", "HAY", "SACK", "CUSHION"), 10),
    (("POT", "JUG", "CAULDRON"), 12),
    (("SMALL_DRESSER", "NIGHTSTAND", "PLANT", "VASE"), 16),
    (("TABLE", "DESK", "COUNTER"), 14),
    (("BARREL", "CRATE", "LOGS", "CART", "MACHINE"), 16),
    (("FORGE", "STOVE", "OVEN"), 20),
    (("DRAWERS", "DRESSER", "STATUE"), 24),
    (("SHELF", "BOOKCASE", "CLOSET", "RACK", "LADDER", "STAIRCASE"), 32),
)
FURNITURE_DEFAULT = 14  # px: a table


def furniture_px(name):
    for words, px in FURNITURE_PX:
        if any(w in name for w in words):
            return px
    return FURNITURE_DEFAULT


PIECE_MAX = 12          # cells: an unnamed piece bigger than a bed and a
                        # dresser together is not furniture


def furniture_heights(r, cls, shell, fam, taken=(), art=None, sprites_seen=False):
    """{(cy, cx): px above the floor} for the furniture of an enclosed room:
    blocked cells off the ring, not a prop family, block, door or upright.
    A connected piece takes the height of the object standing on it (by
    Picori's name) or a table's."""
    h, w = cls.shape
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & ~shell["R"]
    for y, x in zip(*np.nonzero(fam != None)):  # noqa: E711
        blocked[y, x] = False
    for y, x in taken:
        if 0 <= y < h and 0 <= x < w:
            blocked[y, x] = False
    if "exits" in shell:
        blocked &= ~shell["exits"]
    if not blocked.any():
        return {}
    names = {}
    for e in r.entities[1:]:
        if e["kind"] != 6:
            continue
        cx, cy = int(e["x"] - r.origin_x) // 16, int(e["y"] - r.origin_y) // 16
        if 0 <= cx < w and 0 <= cy < h:
            names.setdefault((cy, cx), PL.entity_name(e["kind"], e["id"], e["type"]))
    sprites = set(names)                        # objects: sprites, not art
    labels = cell_labels(r, h, w)
    names.update(labels)                        # a person's word wins
    sprites -= set(labels)
    for c, nm in list(names.items()):
        if any(wd in nm for wd in RELIEF_WORDS):
            blocked[c] = False                  # on the wall: the ring's
    # pieces: each named object's cells on their own (a cabinet the table
    # touches is not the table), then what is left, joined
    lab = np.zeros((h, w), np.int32)
    n = 0
    byname = {}
    for c, nm in names.items():
        if blocked[c]:
            byname.setdefault(nm, []).append(c)
    for nm, cs in byname.items():
        m = np.zeros((h, w), bool)
        for c in cs:
            m[c] = True
        ml, mn = RE._label(m)
        for j in range(1, mn + 1):
            n += 1
            lab[ml == j] = n
    rest, rn = RE._label(blocked & (lab == 0))
    if art is not None and rn:
        # an unnamed piece may be several things side by side -- stools
        # and dressers along a wall: each blob of its own drawing (not the
        # floor's colours) is one, and a cell goes with its biggest blob
        one16_ = np.ones((16, 16), bool)
        for j in range(1, rn + 1):
            piece = rest == j
            Fp = np.kron(~piece & ~shell["R"] & (cls == RE.CLASS_GROUND), one16_)
            D = drawn_piece(art, piece, Fp, np.kron(piece, one16_))
            bl_, bn_ = RE._label(D)
            if bn_ < 2:
                n += 1
                lab[piece] = n
                continue
            base_n = n
            for (cy, cx) in zip(*np.nonzero(piece)):
                sub = bl_[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                ids, cnt = np.unique(sub[sub > 0], return_counts=True)
                lab[cy, cx] = base_n + (int(ids[cnt.argmax()]) if len(ids) else bn_ + 1)
            n = base_n + bn_ + 1
    else:
        lab[rest > 0] = rest[rest > 0] + n
        n += rn
    out = {}
    drawn_rows = shell.setdefault("drawn", {})   # rows of front the art shows
    one16 = np.ones((16, 16), bool)
    for k in range(1, n + 1):
        piece = lab == k
        cells = list(zip(*np.nonzero(piece)))
        Fp = np.kron(~piece & ~shell["R"] & (cls == RE.CLASS_GROUND), one16)
        if len(cells) > PIECE_MAX and not any(c in names for c in cells):
            if os.environ.get("TV_DBG"):
                print("SKIP", r.area, r.room, "too big for furniture", len(cells), file=sys.stderr)
            continue            # the room's own wall band the box left over
                                # (34_02's right column and bottom row),
                                # not a piece of furniture
        if all(c in sprites for c in cells) and not sprites_seen:
            continue            # an object (furniture.c draws a sprite and
                                # marks its cells): the entity stage's --
                                # unless the room was captured, its sprites
                                # drawn in the picture
        if art is not None:
            D = drawn_piece(art, piece, Fp, np.kron(piece, one16))
            if D.sum() < 0.25 * 256 * len(cells) or all(drawn_as_floor(art, cls, c) for c in cells):
                if os.environ.get("TV_DBG"):
                    print("SKIP", r.area, r.room, cells, int(D.sum()), file=sys.stderr)
                continue        # not in the room's art: a sprite on bare floor,
                                # the entity stage's to build
        px = [furniture_px(names[c]) for c in cells if c in names]
        fr = None
        backed = bool((np.roll(piece, 1, axis=0) & ~piece)[1:].any() and
                      (shell["R"][:-1] & piece[1:]).any())
        if art is not None and not backed and (not px or max(px) == FURNITURE_DEFAULT):
            # a table: as tall as its front is drawn, under its lit top
            fr = drawn_front(art, drawn_piece(art, piece, Fp, np.kron(piece, one16)))
        if px:
            hh = max(px)
        elif art is not None:
            hh = drawn_height(art, piece, Fp, shell.get("W"))
        else:
            hh = FURNITURE_DEFAULT
        if fr is not None and not px:
            hh = FURNITURE_DEFAULT      # a table, by the people who use it
        for rule in TI.load_overrides():  # a person's height wins
            if "height" in rule:
                m_ = TI.override_mask(r, rule)
                if any(m_[c] for c in cells):
                    hh = rule["height"]
        if os.environ.get("TV_DBG"):
            print("PIECE", r.area, r.room, sorted((int(c[1]), int(c[0])) for c in cells),
                  [names.get(c) for c in cells if c in names][:1], "front", fr, "h", hh,
                  file=sys.stderr)
        for c in cells:
            out[(int(c[0]), int(c[1]))] = hh
            if fr is not None:
                drawn_rows[(int(c[0]), int(c[1]))] = min(fr, hh)
    return out


def drawn_as_floor(art, cls, cell):
    """Is this cell drawn as some walkable cell of the room is (within
    FLOOR_MATCH)? Then what stands there is a sprite, not the art."""
    y, x = cell
    A = np.asarray(art)
    px = A[y * 16:y * 16 + 16, x * 16:x * 16 + 16, :3].astype(np.int16)
    if px.shape[:2] != (16, 16):
        return False
    best = 1e9
    for yy, xx in zip(*np.nonzero(cls == RE.CLASS_GROUND)):
        q = A[yy * 16:yy * 16 + 16, xx * 16:xx * 16 + 16, :3].astype(np.int16)
        if q.shape[:2] == (16, 16):
            best = min(best, float(np.abs(q - px).mean()))
            if best <= FLOOR_MATCH:
                return True
    return False


def drawn_front(art, D):
    """Rows of a piece's front: the camera shows a thing's top (lit) above
    its front (shaded) -- one row per pixel of depth, one per pixel of
    height (docs/vr/06) -- so where each column's drawing turns from the
    top's light to the front's shade, the rows below it are its height.
    The split is the row with the most contrast between the two; the
    median over columns. None where no column shows one."""
    h, w = D.shape
    A = np.asarray(art)[:h, :w, :3].astype(np.float64)
    lum = A @ np.array([0.299, 0.587, 0.114])
    fronts = []
    for x in np.nonzero(D.any(axis=0))[0]:
        zs = np.nonzero(D[:, x])[0]
        z0, z1 = int(zs.min()), int(zs.max()) + 1
        col = lum[z0:z1, x]
        n = len(col)
        if n < 8:
            continue
        best, bs = 0.0, None
        for s_ in range(3, n - 2):
            c = col[:s_].mean() - col[s_:].mean()
            if c > best:
                best, bs = c, s_
        if bs is not None and best >= DRAWN_SPLIT_MIN:
            fronts.append(n - bs)
    if len(fronts) < max(3, int(0.3 * len(np.nonzero(D.any(axis=0))[0]))):
        return None
    return int(np.median(fronts))


DRAWN_SPLIT_MIN = 12.0  # luminance: a top this much lighter than its front


def drawn_height(art, piece, F, wall=None):
    """An unnamed thing's height from its drawing: drawn as its depth plus
    its height (45 degrees), and no deeper than it is wide -- height =
    extent - min(width, extent / 2). A table: 14; a stool: 8. A thing
    against the back wall is drawn up over the wall band: its drawing is
    followed upward past its cells while it is neither floor nor wall."""
    D = drawn_piece(art, piece, F, np.kron(piece, np.ones((16, 16), bool)))
    if wall is not None and len(wall) and D.any():
        A = np.asarray(art)[:D.shape[0], :D.shape[1], :3].astype(np.int64)
        key = (A[..., 0] << 16) | (A[..., 1] << 8) | A[..., 2]
        floorc = np.unique(key[F]) if F.any() else np.zeros(0, np.int64)
        other = ~np.isin(key, wall) & ~np.isin(key, floorc)
        for x in np.nonzero(D.any(axis=0))[0]:
            z = int(np.nonzero(D[:, x])[0].min()) - 1
            while z >= 0 and other[z, x] and z >= int(np.nonzero(D[:, x])[0].min()) - 48:
                D[z, x] = True
                z -= 1
    ys, xs = np.nonzero(D)
    if not len(ys):
        return FURNITURE_DEFAULT
    ext = int(np.median([np.count_nonzero(D[:, x]) for x in np.unique(xs)]))
    wid = int(xs.max() - xs.min() + 1)
    return int(np.clip(ext - min(wid, ext // 2), 6, 40))


def room_shell(r, cls, H, fam, doors, exclude=()):
    """The ring of an enclosed room: dict(R cells, Wp plan mask in px,
    Hw, base, fill runs), or None out of doors or where there is no ring."""
    loc = PL.location(r.area, r.room)
    if loc["open_air"] or loc["view"] != "top":
        return None
    L = r.layers[0]
    h, w = cls.shape
    t = L["tile"][:h, :w]
    tt = L["tiletype"][np.clip(t, 0, len(L["tiletype"]) - 1)][:h, :w]
    walk = cls == RE.CLASS_GROUND
    objects = np.isin(tt, list(RE.BLOCK_TYPES) + [0x73, 0x74]) & (t < 0x4000)
    blocked = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & ~objects
    for y, x in zip(*np.nonzero(fam != None)):  # noqa: E711 -- props stand apart
        blocked[y, x] = False
    for d in doors:
        for y in range(d["top"], d["cy"] + 1):
            blocked[y, d["cx"]] = False
    for (x, y) in exclude:
        blocked[y, x] = False
    # exits -- SURFACE_DOOR cells with no door under them -- are openings
    # in the ring, walked through at floor height: the two doors of a Minish
    # house, the way out of Link's
    act = L["act"][:h, :w]
    below = np.zeros_like(act)
    below[:-1] = act[1:]
    exits = blocked & (act == ARCH_ACT) & (below != DOOR_ACT)
    blocked &= ~exits
    walk = walk | exits
    if not walk.any() or not blocked.any():
        return None
    # the room's floor: walkable ground walls enclose -- not the grass round
    # a shrine, open to the edge; all of it if nothing is enclosed
    # (the exits left out: a door on the room's edge row opens the room to
    # its neighbour, not to the outside -- 34_02's front door -- and back in
    # where they meet that floor)
    wl, wn = RE._label(walk & ~exits)
    open_ = set(wl[0].tolist()) | set(wl[-1].tolist()) | set(wl[:, 0].tolist()) | set(wl[:, -1].tolist())
    open_.discard(0)
    inner = walk & ~exits & ~np.isin(wl, list(open_))
    if inner.any():
        P = np.pad(inner, 1)
        inner |= exits & (P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:])
    else:
        inner = walk
    # near that floor
    near = inner.copy()
    for _ in range(SHELL_REACH):
        P = np.pad(near, 1)
        near = near | P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:] | \
            P[:-2, :-2] | P[:-2, 2:] | P[2:, :-2] | P[2:, 2:]
    # wall, not furniture: some straight line from it gets out -- to the
    # room's edge or open ground -- without crossing the floor
    R = np.zeros_like(blocked)
    for y, x in zip(*np.nonzero(blocked & near)):
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            yy, xx = y + dy, x + dx
            while 0 <= yy < h and 0 <= xx < w and not inner[yy, xx] \
                    and (blocked[yy, xx] or cls[yy, xx] == RE.CLASS_VOID or fam[yy, xx] is not None):
                yy, xx = yy + dy, xx + dx
            if not (0 <= yy < h and 0 <= xx < w) or not inner[yy, xx]:
                R[y, x] = True
                break
    if R.sum() < 4:
        return None
    walk = inner
    fl = [int(H[y, x]) for y, x in zip(*np.nonzero(walk))]
    base = int(np.median(fl)) if fl else 0
    # The ring is a band of wall round the outline, told by its trim: the
    # walls' own colours, learned where nothing stands against them, and
    # its thickness there. Whatever stands in the band drawn in other
    # colours -- a plant, a stool, a dresser, a cabinet against the back
    # wall -- is a thing of its own, built as furniture. Rays through the
    # collision could not tell a cabinet from the wall it stands against.
    Hp, Wpx = h * 16, w * 16
    A = shown_art(r)
    if A is None:
        return None
    A = np.asarray(A)[:Hp, :Wpx, :3].astype(np.int64)
    key = (A[..., 0] << 16) | (A[..., 1] << 8) | A[..., 2]
    one = np.ones((16, 16), bool)
    # the room and all in it: its wall, its floor, whatever blocks near the
    # floor (blocks, furniture) -- not the grass or leaves round a shrine
    solidall = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & (fam == None)  # noqa: E711
    O = np.kron(R | walk | (solidall & near), one)
    if L.get("bpp8"):
        # outside the picture: undrawn or black, joined to the room's edge
        # (black inside is a line of the moulding)
        import extract_art
        a8 = np.asarray(extract_art.room_art(r, 0))[:Hp, :Wpx]
        blank = (a8[..., 3] == 0) | (a8[..., :3].max(axis=-1) == 0)
        bl, _bn = RE._label(blank)
        outl = set(bl[0].tolist()) | set(bl[-1].tolist()) | set(bl[:, 0].tolist()) | set(bl[:, -1].tolist())
        outl.discard(0)
        O &= ~np.isin(bl, list(outl))
    Q = np.pad(~O, 1, constant_values=True)
    edge = O & (Q[:-2, 1:-1] | Q[2:, 1:-1] | Q[1:-1, :-2] | Q[1:-1, 2:])
    d = _grow_dist(edge, O, 256)
    Rp = np.kron(R, one)
    ys, xs = np.nonzero(O)
    if not len(ys):
        return None
    ylo = ys.min() + (2 * (ys.max() - ys.min())) // 3
    rows_ = np.arange(Hp)[:, None]
    sel = O & Rp & (d <= 32) & (rows_ >= ylo)
    if sel.sum() < 32:
        sel = O & Rp & (d <= 32)
    vals, counts = np.unique(key[sel], return_counts=True)
    W = vals[counts >= SHELL_PALETTE * counts.sum()]
    inW = np.isin(key, W) & O
    x0, x1 = xs.min(), xs.max()
    mid = range(x0 + (x1 - x0) // 3, x1 - (x1 - x0) // 3 + 1)

    def run(x, down):
        col = np.nonzero(O[:, x])[0]
        if not len(col):
            return None
        z, n = (col.min(), 0) if down else (col.max(), 0)
        while 0 <= z < Hp and O[z, x] and inW[z, x]:
            n += 1
            z += 1 if down else -1
        return n
    south = [v for v in (run(x, False) for x in mid) if v]
    north = [v for v in (run(x, True) for x in mid) if v]
    T = int(np.clip(np.median(south) if south else 16, 6, 64))
    Tn = int(np.median(north)) if north else T
    Hw = int(np.clip(max(Tn - T, SHELL_WALL_MIN), *SHELL_H))
    # the sides may be thicker than the front: measure each, in the
    # middle third of its rows, where a window or curtain may interrupt
    # the trim -- the plain stretches (75th percentile) say how thick
    y0_, y1_ = ys.min(), ys.max()
    rmid = range(y0_ + (y1_ - y0_) // 3, y1_ - (y1_ - y0_) // 3 + 1)

    def hrun(y, right):
        row = np.nonzero(O[y])[0]
        if not len(row):
            return None
        x, n = (row.max(), 0) if right else (row.min(), 0)
        while 0 <= x < Wpx and O[y, x] and inW[y, x]:
            n += 1
            x += -1 if right else 1
        return n
    Ts = {}
    for side, right in (("west", False), ("east", True)):
        rr = [v for v in (hrun(y, right) for y in rmid) if v]
        Ts[side] = int(np.clip(np.percentile(rr, 75), T, 96)) if rr else T
    xc = (xs.min() + xs.max()) / 2.0
    cols_ = np.arange(Wpx)[None, :]
    Tmap = np.where(cols_ < xc, Ts["west"], Ts["east"])
    band = O & (d <= np.minimum(np.maximum(T, Tmap), 96))
    # the back wall's face, as deep as it is drawn below its top
    vtop = np.zeros((Hp, Wpx), np.int32)
    for x in range(Wpx):
        col = np.nonzero(O[:, x])[0]
        if len(col):
            vtop[col.min():, x] = np.arange(Hp - col.min())
    band |= O & (vtop <= Tn) & (rows_ < (ys.min() + ys.max()) // 2)
    # things in the band: blocked cells mostly beyond it, or mostly not in
    # the wall's colours
    objs = np.zeros_like(R)
    for y, x in zip(*np.nonzero(R)):
        co = O[y * 16:y * 16 + 16, x * 16:x * 16 + 16]
        if not co.any():
            continue
        cb = band[y * 16:y * 16 + 16, x * 16:x * 16 + 16][co]
        cw = inW[y * 16:y * 16 + 16, x * 16:x * 16 + 16][co]
        share = cb.mean()
        if share >= SHELL_RELIEF:
            continue            # within the band: wall, reliefs and posters too
        if cw.mean() >= SHELL_IN_WALL:
            continue            # the wall's own trim where the curve runs wide
        if share < SHELL_IN_BAND or cw[cb].mean() < SHELL_IN_WALL:
            objs[y, x] = True
    for (y, x), nm in cell_labels(r, h, w).items():
        if not (blocked[y, x] or objs[y, x] or R[y, x]):
            continue
        if any(wd in nm for wd in RELIEF_WORDS):
            objs[y, x] = False
            R[y, x] = True
        elif nm not in ("WALL",):
            objs[y, x] = True                   # a person named a thing here
    R = R & ~objs
    # doorways the game marks with an object -- an archway, a door: the
    # blocked cells of the ring under it and beside it open
    for e in r.entities[1:]:
        nm = PL.entity_name(e["kind"], e["id"], e["type"])
        if e["kind"] != 6 or not any(wd in nm for wd in ("ARCHWAY", "DOOR")):
            continue
        cx, cy = int(e["x"] - r.origin_x) // 16, int(e["y"] - r.origin_y) // 16
        for yy, xx in ((cy + dy_, cx + dx_) for dy_ in (-1, 0, 1) for dx_ in (-1, 0, 1)):
            if 0 <= yy < h and 0 <= xx < w and (R[yy, xx] or objs[yy, xx]) \
                    and np.asarray(A[yy * 16:yy * 16 + 16, xx * 16:xx * 16 + 16]).mean() < DOOR_DARK:
                R[yy, xx] = objs[yy, xx] = False
                exits[yy, xx] = True
    if R.sum() < 4:
        return None
    Dw = O & np.kron(R, one)            # every drawn pixel of a wall cell
    F = np.kron(~R & ~objs & (cls != RE.CLASS_VOID), one)
    # the wall's plan stops at what stands in front of it
    Wp, fill = projective_plan(Dw, F | np.kron(objs, one), Hw)
    sh = dict(R=R, Wp=Wp, Hw=Hw, base=base, fill=fill, F=F, exits=exits, T=T, W=W)
    # the walls themselves: the box convention (docs/vr/06): every band
    # round the floor is a wall's inner face, standing at the floor's edge
    gap = open_doorways(r, cls, exits | stairwell_cells(r, cls, R, exits), one)
    for dd in doors:
        for yy in range(dd["top"], dd["cy"] + 1):
            gap[yy * 16:yy * 16 + 16, dd["cx"] * 16:dd["cx"] * 16 + 16] = True
    # the floor and everything on it: all but the ring, the void, the exits
    stand = ~R & (cls != RE.CLASS_VOID) & ~exits
    for dd in doors:
        for yy in range(dd["top"], dd["cy"] + 1):
            stand[yy, dd["cx"]] = False
    things = np.kron(solidall & ~R, one) & O     # what stands on the floor
    lumof = lambda a: np.asarray(a)[:h * 16, :w * 16, :3].astype(np.int64) @ np.array([299, 587, 114]) // 1000
    darkpx = lumof(shown_art(r)) < STAIR_DARK             # the dark the game shows
    drawnpx = lumof(RE.room_art_rgb(r, 0)) < STAIR_DARK   # the dark the room draws
    sh["box"] = box_walls(O, inW, np.kron(walk, one), gap, things,
                          np.kron(stand, one) & O, dark=darkpx, dark_drawn=drawnpx)
    # one box is one room: where its floor misses much of the walkable
    # floor (a cave of several parts, an L), the cells stay
    b_ = sh["box"]
    if b_ is not None:
        wpx = np.kron(walk & ~exits, one)
        cover = (b_["floor"][:wpx.shape[0], :wpx.shape[1]] & wpx[:b_["floor"].shape[0], :b_["floor"].shape[1]]).sum()
        if os.environ.get("TV_DBG"):
            print("BOXCOVER", r.area, r.room, int(cover), int(wpx.sum()), file=sys.stderr)
        if cover < BOX_COVER * max(1, wpx.sum()):
            sh["box"] = None
    return sh


BOX_COVER = 0.85        # a box's floor holds this share of the walkable floor
BOX_LINTEL_MIN = 6      # px: the least lintel over a box room's doorway
HUMANOID_PX = 32        # px: the tallest ordinary person the game draws (townsfolk)
BOX_DOOR = HUMANOID_PX + 2   # px: a doorway's opening, with a little head room


BOX_RAYS = 720          # rays from the floor's middle round the room
BOX_SMOOTH = 15         # rays: a band's thickness is the median over this many
BOX_H = (24, 64)        # px: a wall's height, from the back wall's face


def box_walls(O, inW, floor, gap, objs, stand=None, dark=None, dark_drawn=None):
    """The walls of an enclosed room as the game draws them: a box seen
    from above its middle, every inner face folded out round the floor
    (Link's house draws its corners as diagonal seams from the floor's
    corner to the room's). Along rays from the floor's middle: the
    outline, and inward from it the run in the wall's colours -- the band,
    that wall's face. A thing drawn over the band (a plant, a dresser)
    breaks the run; the median over neighbouring rays is the wall's.
    Returns dict(inner, outer: [(x, z)], gap: [bool], H, floor mask), or
    None."""
    ys, xs = np.nonzero(floor)
    if not len(ys):
        return None
    cz, cx = ys.mean(), xs.mean()
    Hp, Wpx = O.shape
    outer, run, gapr, runc, lasts, okc, dobj, wallr, darkr, darkd = [], [], [], [], [], [], [], [], [], []
    for i in range(BOX_RAYS):
        th = 2 * np.pi * i / BOX_RAYS
        dx, dz = np.cos(th), np.sin(th)
        t, last, miss = 0.0, None, 0
        while True:
            x, z = int(round(cx + dx * t)), int(round(cz + dz * t))
            if not (0 <= x < Wpx and 0 <= z < Hp):
                break
            if O[z, x]:
                last, miss = t, 0
            elif last is not None:
                miss += 1
                if miss > 3:
                    break
            t += 0.5
        if last is None:
            outer.append((cx, cz)); run.append(0); gapr.append(True); runc.append(0); lasts.append(0)
            wallr.append(False)
            darkr.append(False)
            darkd.append(False)
            okc.append(False)
            dobj.append(None)
            continue
        lasts.append(last)
        ox_, oz_ = cx + dx * last, cz + dz * last
        outer.append((ox_, oz_))
        # inward from the outline, in the wall's colours
        k, bad = 0.0, 0
        while k < last:
            x, z = int(round(ox_ - dx * k)), int(round(oz_ - dz * k))
            if inW[z, x]:
                bad = 0
            else:
                bad += 1
                if bad > 2:
                    k -= bad * 0.5
                    break
            k += 0.5
        # from the outline in to the first thing standing on the floor
        q, dob = 0.0, None
        while q < last:
            x, z = int(round(ox_ - dx * q)), int(round(oz_ - dz * q))
            if objs[z, x]:
                dob = q
                break
            q += 0.5
        dobj.append(dob)
        runc.append(max(0.0, k))                # by the wall's colours
        # the band on this ray drawn as wall (not a doorway's dark)
        us = np.arange(max(0.0, last - BOX_GAP_REACH), last, 1.0)
        hits = [inW[int(round(cz + dz * u)), int(round(cx + dx * u))] for u in us
                if 0 <= int(round(cz + dz * u)) < Hp and 0 <= int(round(cx + dx * u)) < Wpx]
        wallr.append(bool(hits) and float(np.mean(hits)) >= 0.5)
        dk = [dark[int(round(cz + dz * u)), int(round(cx + dx * u))] for u in us
              if dark is not None and 0 <= int(round(cz + dz * u)) < Hp
              and 0 <= int(round(cx + dx * u)) < Wpx]
        darkr.append(bool(dk) and float(np.mean(dk)) >= 0.5)
        dd_ = dark_drawn if dark_drawn is not None else dark
        dk2 = [dd_[int(round(cz + dz * u)), int(round(cx + dx * u))] for u in us
               if dd_ is not None and 0 <= int(round(cz + dz * u)) < Hp
               and 0 <= int(round(cx + dx * u)) < Wpx]
        darkd.append(bool(dk2) and float(np.mean(dk2)) >= 0.5)
        # clean: just inside that run is walkable floor, not a thing
        px_, pz_ = int(round(ox_ - dx * (k + 3))), int(round(oz_ - dz * (k + 3)))
        okc.append(0 <= px_ < Wpx and 0 <= pz_ < Hp and bool(floor[pz_, px_]))
        if stand is not None:
            # where the floor ends -- walkable ground and what stands on it
            f, lastf = 0.0, 0.0
            while f < last:
                x, z = int(round(cx + dx * f)), int(round(cz + dz * f))
                if stand[z, x]:
                    lastf = f
                elif f > lastf + 2:
                    break
                f += 0.5
            k = max(0.0, last - lastf - 0.5)
        run.append(max(0.0, k))
        # a doorway: the ray crosses an opening in the band
        seg = [(int(round(cx + dx * u)), int(round(cz + dz * u)))
               for u in np.arange(max(0.0, last - BOX_GAP_REACH), last, 1.0)]
        gapr.append(any(gap[z, x] for x, z in seg if 0 <= x < Wpx and 0 <= z < Hp))
    run = np.array(run)
    runc = np.array(runc)
    n = len(run)
    # the dark drawn in a doorway is its opening, not the wall's face: an
    # opening takes in the dark rays beside it
    grow = True
    while grow:
        grow = False
        for i in range(n):
            if darkr[i] and not gapr[i] and (gapr[(i - 1) % n] or gapr[(i + 1) % n]):
                gapr[i] = True
                grow = True
    inner = fit_wall_foot(outer, run, runc, gapr, lasts, (cx, cz), okc, dobj)
    sm = np.array([np.hypot(outer[i][0] - inner[i][0], outer[i][1] - inner[i][1]) for i in range(n)])
    north = [sm[i] for i in range(n) if np.sin(2 * np.pi * i / n) < -0.94]
    H = int(np.clip(np.median(north) if north else 32, *BOX_H))
    if any(gapr):
        # a doorway lets any ordinary person through, under a lintel
        H = max(H, BOX_DOOR + BOX_LINTEL_MIN)
    outer = dewarp_outer(inner, sm, gapr, fit_wall_foot.exponent, Wpx, Hp)
    from PIL import Image, ImageDraw
    im = Image.new("1", (Wpx, Hp), 0)
    ImageDraw.Draw(im).polygon([(float(x), float(z)) for x, z in inner], fill=1)
    fm = np.asarray(im, dtype=bool) & ~gap
    return dict(inner=inner, outer=outer, gap=gapr, H=H, floor=fm, wall=wallr, dark=darkd,
                dark_uv=(tuple(float(v) for v in np.argwhere(dark)[0][::-1] + 0.5)
                         if dark is not None and dark.any() else None))


def dewarp_outer(inner, sm, gap, e, Wpx, Hp):
    """Where each point of the wall's foot is drawn at the wall's top: out
    from the foot square to the wall (its normal), by that side's band --
    not along a ray from the room's middle, which near a corner crosses
    into the next wall's drawing and warps the face. In a box (a high
    exponent) the normal is squared, so a corner goes out to the drawn
    corner of the outline -- the diagonal seam; round a curve it stays
    the band's width."""
    n = len(inner)
    th = 2 * np.pi * np.arange(n) / n
    sin, cos = np.sin(th), np.cos(th)
    ok = ~np.asarray(gap, bool)

    def band(m):
        v = sm[m & ok]
        return float(np.median(v)) if len(v) else float(np.median(sm[ok])) if ok.any() else 16.0
    Tn, Ts = band(sin < -0.94), band(sin > 0.94)
    Tw, Te = band(cos < -0.94), band(cos > 0.94)
    P = np.array(inner, float)
    K = BOX_NORMAL_K
    out = []
    for i in range(n):
        a, b = P[(i - K) % n], P[(i + K) % n]
        tx, tz = b[0] - a[0], b[1] - a[1]
        nx, nz = tz, -tx                            # a normal of the foot
        c = P.mean(axis=0)
        if (P[i][0] - c[0]) * nx + (P[i][1] - c[1]) * nz < 0:
            nx, nz = -nx, -nz                       # outward
        L = np.hypot(nx, nz) or 1.0
        nx, nz = nx / L, nz / L
        if e >= BOX_SQUARE_N:
            m_ = max(abs(nx), abs(nz)) or 1.0
            nx, nz = nx / m_, nz / m_
            if abs(nx) > 0.4 and abs(nz) > 0.4:     # a corner: to the seam
                nx, nz = np.sign(nx), np.sign(nz)
            elif abs(nx) >= abs(nz):
                nx, nz = np.sign(nx), 0.0
            else:
                nx, nz = 0.0, np.sign(nz)
        tx_ = Te if nx > 0 else Tw
        tz_ = Ts if nz > 0 else Tn
        ox_ = float(np.clip(P[i][0] + nx * tx_, 0, Wpx - 1))
        oz_ = float(np.clip(P[i][1] + nz * tz_, 0, Hp - 1))
        out.append((ox_, oz_))
    return out


BOX_NORMAL_K = 3        # rays each side: the foot's direction at a point
BOX_SQUARE_N = 6.0      # a foot this square is a box: its faces are flat
BOX_CLEAN = 12          # px: where the wall's colours and the floor agree
BOX_N = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 12.0, 20.0, 40.0)


def fit_wall_foot(outer, run, runc, gap, lasts, origin, okc=None, dobj=None):
    """The wall's foot round the room, fitted: a superellipse (an oval at
    n=2, a stadium near 4, a rectangle as n grows), each side its own
    extent. Fitted to the rays where the band's colours end where the
    floor does -- the moulding, nothing standing in front of it; a side
    with none of those (the back row of a Minish home is all furniture)
    takes the floor's edge. Every ray's foot is where it meets the curve,
    behind the plants and dressers as much as anywhere."""
    n = len(run)
    th = 2 * np.pi * np.arange(n) / n
    dxs, dzs = np.cos(th), np.sin(th)
    cx, cz = origin
    outer = np.array(outer, float)
    foot_c = outer - np.stack([dxs, dzs], 1) * np.array(runc)[:, None]
    foot_f = outer - np.stack([dxs, dzs], 1) * np.array(run)[:, None]
    clean = (np.abs(np.array(runc) - np.array(run)) <= BOX_CLEAN) & ~np.array(gap) & (np.array(runc) > 0)
    if okc is not None:
        clean &= np.array(okc, bool)
    x0 = (outer[:, 0].min() + outer[:, 0].max()) / 2.0
    z0 = (outer[:, 1].min() + outer[:, 1].max()) / 2.0

    def reach(mask_side, coord, sign):
        """(outline's extent, wall foot's extent or None) on one side."""
        c0 = (x0, z0)[coord]
        o = float(np.median(sign * (outer[mask_side, coord] - c0))) if mask_side.any() else 8.0
        pts = foot_c[clean & mask_side]
        f = float(np.median(sign * (pts[:, coord] - c0))) if len(pts) >= 3 else None
        return o, f
    sides = {"w": reach(dxs < -0.9, 0, -1), "e": reach(dxs > 0.9, 0, 1),
             "n": reach(dzs < -0.9, 1, -1), "s": reach(dzs > 0.9, 1, 1)}
    opposite = {"w": "e", "e": "w", "n": "s", "s": "n"}
    ext = {}
    for k, (o, f) in sides.items():
        if f is None:
            # nothing clean on this side (the back row of a Minish home is
            # all furniture): things stand in front of the wall, so its foot
            # is where its colours stop and their drawings begin
            m_ = {"w": dxs < -0.9, "e": dxs > 0.9, "n": dzs < -0.9, "s": dzs > 0.9}[k]
            c = 0 if k in "we" else 1
            sg = -1 if k in "wn" else 1
            hit = np.array([d is not None for d in (dobj or [None] * n)])
            if dobj is not None and (m_ & hit).sum() >= 3:
                dd = np.array([d if d is not None else 0.0 for d in dobj])
                pc = outer - np.stack([dxs, dzs], 1) * dd[:, None]
                f = float(np.median(sg * (pc[m_ & hit, c] - (x0, z0)[c])))
            elif len(foot_c[m_ & ~np.array(gap) & (np.array(runc) > 0)]):
                pc = foot_c[m_ & ~np.array(gap) & (np.array(runc) > 0)]
                f = float(np.median(sg * (pc[:, c] - (x0, z0)[c])))
            else:
                pts = foot_f[m_ & ~np.array(gap)]
                f = float(np.median(sg * (pts[:, c] - (x0, z0)[c]))) if len(pts) else o - 16
        ext[k] = max(4.0, f)
    aw, ae, bn, bs = ext["w"], ext["e"], ext["n"], ext["s"]

    def level(px, pz, e):
        a = np.where(px < x0, aw, ae)
        b = np.where(pz < z0, bn, bs)
        return (np.abs(px - x0) / a) ** e + (np.abs(pz - z0) / b) ** e
    pts = foot_c[clean]
    best, e_best = None, 8.0
    for e in BOX_N:
        if len(pts) < 8:
            break
        res = float(np.mean((level(pts[:, 0], pts[:, 1], e) ** (1.0 / e) - 1.0) ** 2))
        if best is None or res < best:
            best, e_best = res, e
    inner = []
    for i in range(n):
        lo, hi = 0.0, float(lasts[i]) if lasts[i] else 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if level(cx + dxs[i] * mid, cz + dzs[i] * mid, e_best) < 1.0:
                lo = mid
            else:
                hi = mid
        inner.append((cx + dxs[i] * lo, cz + dzs[i] * lo))
    fit_wall_foot.exponent = e_best
    return inner


RECESS_DEPTH = 16       # px: how far a dark doorway's unlit space runs back


def doorway_recesses(b, ox, oz, y0, ho):
    """Behind each doorway drawn opening onto the dark: an unlit, empty
    space -- its back, sides, ceiling and floor all the drawn dark -- as
    tall as the doorway and RECESS_DEPTH deep. What is drawn in it (a
    stairwell's steps) stands inside it; a lit doorway (the way out) has
    none, it opens onto what lies beyond."""
    gap, dark, inner, outer = b["gap"], b.get("dark"), b["inner"], b["outer"]
    uv = b.get("dark_uv")
    if not dark or uv is None:
        return []
    n = len(gap)
    q = []
    seen = set()
    for i in range(n):
        if not gap[i] or i in seen or gap[(i - 1) % n] and len(seen) < n and (i - 1) % n not in seen and not all(gap):
            continue
        run = []
        k = i
        while gap[k] and k not in seen:
            seen.add(k)
            run.append(k)
            k = (k + 1) % n
        if len(run) < 2 or sum(dark[j] for j in run) * 2 < len(run):
            continue                                # lit: open beyond
        a, c = run[0], run[-1]
        P0, P1 = np.array(inner[a], float), np.array(inner[c], float)
        nv = np.mean([np.array(outer[j], float) - np.array(inner[j], float) for j in run], axis=0)
        L = np.hypot(*nv) or 1.0
        nv = nv / L * RECESS_DEPTH
        B0, B1 = P0 + nv, P1 + nv
        yt = y0 + ho
        faces = [
            [(B0, y0), (B1, y0), (B1, yt), (B0, yt)],          # back
            [(P0, y0), (B0, y0), (B0, yt), (P0, yt)],          # side
            [(P1, y0), (B1, y0), (B1, yt), (P1, yt)],          # side
        ]
        for f in faces:
            pts = [(ox + pp[0], yy, oz + pp[1]) for pp, yy in f]
            q.append((pts, [uv] * 4))
            q.append((pts[::-1], [uv] * 4))                  # seen from either side
        for yy in (y0 + 0.02, yt):                            # floor, ceiling
            pts = [(ox + pp[0], yy, oz + pp[1]) for pp in (P0, P1, B1, B0)]
            q.append((pts, [uv] * 4))
            q.append((pts[::-1], [uv] * 4))
    return q


def box_quads(b, ox, oz, base):
    """A box's walls standing on its floor's edge, each wall face wearing
    its band (inner edge at the floor, outline at the top), a thin cap in
    the outline's colour, and the floor inside, flat."""
    q = []
    inner, outer, gap, H = b["inner"], b["outer"], b["gap"], b["H"]
    n = len(inner)
    cxm = np.mean([p[0] for p in inner])
    czm = np.mean([p[1] for p in inner])
    y0, y1 = base, base + H
    CAP = 3
    # over a doorway the wall comes back above the opening: a lintel from
    # the door's top (clearing a head, as find_doors does) to the wall's,
    # wearing the wall beside the opening mirrored across its edge -- the
    # opening's own drawing is the dark beyond it
    ho = int(min(H - BOX_LINTEL_MIN, BOX_DOOR))
    wallr = b.get("wall") or [not g for g in gap]
    ok = [not gap[i] and wallr[i] for i in range(n)]

    def mirror(i):
        if not gap[i] or not any(ok):
            return i
        best = None
        for step in (1, -1):
            for d in range(1, n):
                k = (i + step * d) % n
                if ok[k]:
                    if best is None or d < best[0]:
                        best = (d, k, step)
                    break
        d, k, step = best
        m = (k + step * d) % n                  # as far beyond as it is in
        return m if ok[m] else k

    def at(i, t):                               # the band's texel t of the way up
        (ix, iz), (qx, qz) = inner[i], outer[i]
        return (ix + (qx - ix) * t, iz + (qz - iz) * t)
    for i in range(n):
        j = (i + 1) % n
        lintel = gap[i] or gap[j]
        if lintel and ho >= H:
            continue
        (x0, z0), (x1, z1) = inner[i], inner[j]
        if abs(x1 - x0) + abs(z1 - z0) < 1e-6:
            continue
        si, sj = (mirror(i), mirror(j)) if lintel else (i, j)
        ya, ta = (y0 + ho, ho / H) if lintel else (y0, 0.0)
        mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
        nx, nz = cxm - mx, czm - mz                     # facing into the room
        ax, az = nz, nx                                 # room_explore's winding
        fwd = (x1 - x0) * ax + (z1 - z0) * az > 0
        a, bb = ((x0, z0, si), (x1, z1, sj)) if fwd else ((x1, z1, sj), (x0, z0, si))
        pts = [(ox + a[0], y1, oz + a[1]), (ox + bb[0], y1, oz + bb[1]),
               (ox + bb[0], ya, oz + bb[1]), (ox + a[0], ya, oz + a[1])]
        uv = [at(a[2], 1.0), at(bb[2], 1.0), at(bb[2], ta), at(a[2], ta)]
        q.append((pts, uv))
        # the cap: a thin top, outward, in the outline's colour
        l = np.hypot(nx, nz) or 1.0
        ux, uz = -nx / l * CAP, -nz / l * CAP
        cap = [(ox + x0, y1, oz + z0), (ox + x1, y1, oz + z1),
               (ox + x1 + ux, y1, oz + z1 + uz), (ox + x0 + ux, y1, oz + z0 + uz)]
        area = sum(cap[k][0] * cap[(k + 1) % 4][2] - cap[(k + 1) % 4][0] * cap[k][2] for k in range(4))
        uvc = [outer[si], outer[sj], outer[sj], outer[si]]
        if area < 0:
            cap, uvc = cap[::-1], uvc[::-1]
        q.append((cap, uvc))
    q += doorway_recesses(b, ox, oz, y0, ho)
    for x, z, wd, d in RE.greedy_quads(b["floor"]):
        p = [(ox + x, y0, oz + z), (ox + x + wd, y0, oz + z),
             (ox + x + wd, y0, oz + z + d), (ox + x, y0, oz + z + d)]
        q.append((p, [(x, z), (x + wd, z), (x + wd, z + d), (x, z + d)]))
    return q


SHELL_PALETTE = 0.005   # a colour this common in the plain band is the wall's
SHELL_RELIEF = 0.9      # a cell this much in the band is wall, whatever is drawn
SHELL_IN_BAND = 0.6     # a wall cell lies this much in the band ...
SHELL_IN_WALL = 0.6     # ... and is drawn this much in the wall's colours
SHELL_WALL_MIN = 32     # px: a wall stands at least this tall (Link is 24)


def _grow_dist(seed, mask, limit):
    """Steps (4-connected) from seed through mask, up to limit; limit+1 beyond."""
    d = np.where(seed, 0, limit + 1).astype(np.int32)
    front = seed.copy()
    for i in range(1, limit + 1):
        P = np.pad(front, 1)
        nb = (P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:]) & mask & (d > limit)
        if not nb.any():
            break
        d[nb] = i
        front = nb
    return d


def sloped_heights(Wp, fill, F, Hw):
    """Heights over a sloped ring's plan: the constant plan, plus the
    floor it uncovered behind the front (which becomes the foot of the
    slope). Zero at the floor's edge, Hw at the outer edge, linear across
    the band: every point shows the drawing at (x, z - h), which runs from
    the floor's edge to the outline as the drawing does."""
    P = Wp.copy()
    for x, z0, z1, _src in fill:
        P[z0:z1, x] = True
    rows, cols = P.shape
    Fp = np.zeros_like(P)
    Fp[:F.shape[0]] = F & ~P[:F.shape[0]]
    lim = 4 * Hw + 64
    near_floor = np.zeros_like(P)
    Q = np.pad(Fp, 1)
    near_floor = (Q[:-2, 1:-1] | Q[2:, 1:-1] | Q[1:-1, :-2] | Q[1:-1, 2:]) & P
    Q = np.pad(~P & ~Fp, 1, constant_values=True)
    near_out = (Q[:-2, 1:-1] | Q[2:, 1:-1] | Q[1:-1, :-2] | Q[1:-1, 2:]) & P
    din = _grow_dist(near_floor, P, lim).astype(float)
    dout = _grow_dist(near_out, P, lim).astype(float)
    h = np.where(P, np.rint(Hw * din / np.maximum(din + dout, 1)), 0).astype(np.int32)
    h[P & (din > lim)] = Hw          # no floor near: the wall's full height
    return np.where(P, np.maximum(h, 1), 0)


def drawn_piece(art, one, F, D):
    """The piece's own drawing within its cells: pixels in none of the
    colours of the floor round it, filled down each column to the cells'
    foot (the front stands on the floor). The rug above a table's top is
    not the table."""
    h, w = one.shape
    A = np.asarray(art)[:h * 16, :w * 16, :3].astype(np.int64)
    key = (A[..., 0] << 16) | (A[..., 1] << 8) | A[..., 2]
    ring = np.zeros_like(one)
    P = np.pad(one, 1)
    ring = (P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:]) & ~one
    near = np.kron(ring, np.ones((16, 16), bool)) & F
    drawn_piece.raw = None
    if not near.any():
        return D
    # the floor's colours: those at least as common round the piece as in
    # it -- the rug, not the stools beside a table drawn in its wood
    rc, rn = np.unique(key[near], return_counts=True)
    inside = D & np.kron(one, np.ones((16, 16), bool))
    pc, pn = np.unique(key[inside], return_counts=True)
    pd = dict(zip(pc.tolist(), (pn / max(1, inside.sum())).tolist()))
    dens = rn / max(1, near.sum())
    floor_cols = np.array([c for c, d in zip(rc.tolist(), dens) if d >= pd.get(c, 0.0)],
                          dtype=np.int64)
    mine = D & ~np.isin(key, floor_cols)
    # one thing: its biggest blob (not a stool reaching into its cells)
    # (a pixel's erosion first: things side by side touch at their outlines)
    lum_ = A @ np.array([299, 587, 114]) // 1000
    Pm = np.pad(mine, 1)
    core = mine & Pm[:-2, 1:-1] & Pm[2:, 1:-1] & Pm[1:-1, :-2] & Pm[1:-1, 2:] \
        & (lum_ >= OUTLINE_LUM)             # split at the outlines between
    bl, bn = RE._label(core)
    if bn > 1:
        sizes = np.bincount(bl.ravel())[1:]
        keep = bl == int(sizes.argmax()) + 1
        # its outline and legs back: what of the rest lies in its own
        # columns and joins it
        xs_ = np.nonzero(keep.any(axis=0))[0]
        span = np.zeros_like(mine)
        span[:, xs_.min():xs_.max() + 1] = True
        jl, _jn = RE._label(keep | (mine & span & (lum_ < OUTLINE_LUM)))
        ids = np.unique(jl[keep])
        mine = np.isin(jl, ids[ids > 0])
    drawn_piece.raw = mine
    out = np.zeros_like(D)
    for x in np.nonzero(mine.any(axis=0))[0]:
        zs = np.nonzero(mine[:, x])[0]
        z0, z1 = int(zs.min()), int(zs.max()) + 1     # down to its feet
        out[z0:z1, x] = D[z0:z1, x]
    return out if out.sum() >= 16 else D


OUTLINE_LUM = 70        # darker than this is an outline, not a surface


def projective_plan(D, F, hh):
    """Plan of a solid drawn at D (pixels), hh tall, with F what is drawn
    at floor level: its top is drawn hh rows above where it stands, so the
    plan is D moved down hh, less wherever floor is drawn. Also the floor
    runs the plan uncovers under floor drawn above them -- (x, z0, z1,
    source row) -- which take that floor's colour."""
    Hp, Wpx = D.shape
    Wp = np.zeros((Hp + hh, Wpx), bool)
    Wp[hh:hh + Hp] = D
    Wp[:Hp] &= ~F
    fill = []
    for x in range(Wpx):
        z = 0
        while z < Hp:
            if D[z, x] and not Wp[z, x]:
                z0 = z
                while z < Hp and D[z, x] and not Wp[z, x]:
                    z += 1
                if z0 > 0 and F[z0 - 1, x]:
                    fill.append((x, z0, z, z0 - 1))
            else:
                z += 1
    return Wp, fill


def furniture_prisms(cls, shell, heights, art=None):
    """Each piece of furniture as a projected prism, like the ring: its
    collision covers its drawing, top and front; so its top shows the
    drawn tabletop and its front the drawn legs. [(cells, prism dict)]."""
    h, w = cls.shape
    pieces = {}
    for (y, x), hh in heights.items():
        pieces.setdefault(hh, set()).add((y, x))
    out = []
    for hh, cells in pieces.items():
        m = np.zeros((h, w), bool)
        for y, x in cells:
            m[y, x] = True
        lab, n = RE._label(m)
        for k in range(1, n + 1):
            one = lab == k
            D = np.kron(one, np.ones((16, 16), bool))
            F = np.kron(~one & ~shell["R"] & (cls != RE.CLASS_VOID), np.ones((16, 16), bool))
            if art is not None:
                D = drawn_piece(art, one, F, D)
            # the art shows dr rows of front for hh of height: the plan is
            # where the drawing puts the top, and the front's rows stretch
            # up the height (docs/vr/06)
            dr = [shell.get("drawn", {}).get((int(y), int(x)))
                  for y, x in zip(*np.nonzero(one))]
            dr = min((d for d in dr if d), default=hh)
            raw = getattr(drawn_piece, "raw", None) if art is not None else None
            tq = table_quads(raw, dr, hh, art) if (raw is not None and dr < hh) else None
            if os.environ.get("TV_DBG"):
                print("TABLE", hh, dr, raw is not None and raw.sum(), tq is not None, file=sys.stderr)
            if tq is not None:
                out.append((one, dict(quads=tq, base=shell["base"])))
                continue
            Wp, fill = projective_plan(D, F, dr)
            out.append((one, dict(Wp=Wp, Hw=hh, draw=dr, base=shell["base"], fill=fill, F=F)))
    return out


TABLE_LEG_ROWS = 0.6    # a row this much of the table's width is apron, less is legs


def cuboid(x0, x1, y0, y1, z0, z1, front, top=None):
    """A box's faces, in room pixels (y up from 0): front = (u0, u1, v_top,
    v_bottom) of the drawing its south face wears -- the north face wears
    it too, the sides its edge columns; top = (u0, u1, v0, v1) for the top
    face (else the front's top row). Windings as side_face's."""
    u0, u1, vt, vb = front
    q = [([(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)],
          [(u0, vt), (u1, vt), (u1, vb), (u0, vb)]),                      # south
         ([(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)],
          [(u1, vt), (u0, vt), (u0, vb), (u1, vb)]),                      # north
         ([(x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (x1, y0, z0)],
          [(u1 - 0.5, vt), (u1 - 0.5, vt), (u1 - 0.5, vb), (u1 - 0.5, vb)]),  # east
         ([(x0, y1, z1), (x0, y1, z0), (x0, y0, z0), (x0, y0, z1)],
          [(u0 + 0.5, vt), (u0 + 0.5, vt), (u0 + 0.5, vb), (u0 + 0.5, vb)]),  # west
         ([(x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (x0, y0, z0)],
          [(u0, vb - 0.5)] * 2 + [(u1, vb - 0.5)] * 2)]                   # under
    t0, t1, s0, s1 = top if top is not None else (u0, u1, vt, vt + 1)
    q.append(([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
              [(t0, s0), (t1, s0), (t1, s1), (t0, s1)]))                  # top
    return q


STAIR_STEP = 4          # px: a stairwell's step, deep and tall
BOX_GAP_REACH = 40      # px in from the outline: a ray meets a doorway in the band
DOORWAY_DARK = 0.5      # a column (row) this much dark beyond is the opening


QUARTER = 8             # px: the game builds each 16px tile of four 8px pieces
ARCH_REACH = 2          # cells either side of an archway its sprite covers
QUARTER_SHARE = 0.5     # a quarter this much in one kind of pixel is that kind
WALL_CORE = 0.03        # a colour this much of the wall band is the wall's own


def quarter_kinds(art, cls, wall, floor=None):
    """Each 8x8 quarter of the room's drawing, by what it is drawn in:
    "wall" (the wall band's colours), "dark" (the dark beyond a doorway),
    "floor" (the walkable floor's colours) or "other" (a thing, a plate,
    a mark). The game composes every tile of four such pieces, so a tile
    holding two things -- a wall and a jamb, a cliff and a post -- is
    split along them. Returns a (rows*2, cols*2) array of kinds."""
    h, w = cls.shape
    A = np.asarray(art)[:h * 16, :w * 16, :3].astype(np.int64)
    key = (A[..., 0] << 16) | (A[..., 1] << 8) | A[..., 2]
    lum = A @ np.array([299, 587, 114]) // 1000
    if floor is None:
        fm = np.kron(cls == RE.CLASS_GROUND, np.ones((16, 16), bool))
        floor = np.unique(key[fm]) if fm.any() else np.zeros(0, np.int64)
    isw = np.isin(key, wall)
    isf = np.isin(key, floor)
    isd = lum < STAIR_DARK
    Q = QUARTER
    out = np.full((h * 2, w * 2), "other", dtype=object)
    for qy in range(h * 2):
        for qx in range(w * 2):
            sl = (slice(qy * Q, qy * Q + Q), slice(qx * Q, qx * Q + Q))
            for kind, m in (("wall", isw), ("dark", isd), ("floor", isf)):
                if m[sl].mean() >= QUARTER_SHARE:
                    out[qy, qx] = kind
                    break
    return out


def hide_under_arches(r, cls, art, shell):
    """The drawing with what an archway covers taken off the wall: the
    game draws its archway objects (ARCHWAY sprites) over a doorway's
    jambs, hiding the plates drawn there (the developers' "kabe" -- wall
    -- marks). Within ARCH_REACH cells of an archway, a quarter of the
    wall band that is neither wall, floor nor dark takes the nearest wall
    quarter along the wall instead."""
    arches = []
    for e in r.entities[1:]:
        if e["kind"] == 6 and "ARCHWAY" in PL.entity_name(e["kind"], e["id"], e["type"]):
            arches.append((int(e["x"] - r.origin_x) // 16, int(e["y"] - r.origin_y) // 16))
    if not arches:
        return art
    h, w = cls.shape
    band = shell["R"] | shell["exits"]
    # the wall's own colours: the ones it is mostly drawn in -- not a
    # plate's, rare in the band however often its plates repeat
    A_ = np.asarray(art)[:h * 16, :w * 16, :3].astype(np.int64)
    key = (A_[..., 0] << 16) | (A_[..., 1] << 8) | A_[..., 2]
    bm = np.kron(band, np.ones((16, 16), bool))
    c_, n_ = np.unique(key[bm], return_counts=True)
    core = c_[n_ >= WALL_CORE * bm.sum()]
    kinds = quarter_kinds(art, cls, core)
    A = np.array(art)
    Q = QUARTER
    wallq = np.argwhere(kinds == "wall")
    if not len(wallq):
        return art
    for ax, ay in arches:
        for cy in range(max(0, ay - ARCH_REACH), min(h, ay + ARCH_REACH + 1)):
            for cx in range(max(0, ax - ARCH_REACH), min(w, ax + ARCH_REACH + 1)):
                if not band[cy, cx]:
                    continue
                for qy in (cy * 2, cy * 2 + 1):
                    for qx in (cx * 2, cx * 2 + 1):
                        if kinds[qy, qx] != "other":
                            continue
                        # the nearest wall quarter, along the wall first
                        d = np.abs(wallq[:, 0] - qy) * 4 + np.abs(wallq[:, 1] - qx)
                        d2 = np.abs(wallq[:, 1] - qx) * 4 + np.abs(wallq[:, 0] - qy)
                        sy, sx = wallq[int(np.argmin(np.minimum(d, d2)))]
                        A[qy * Q:qy * Q + Q, qx * Q:qx * Q + Q] = \
                            np.asarray(art)[sy * Q:sy * Q + Q, sx * Q:sx * Q + Q]
    return A


CAPTURES = Path(os.environ.get("TMC_CAPTURES", str(Path(__file__).resolve().parent.parent
                                                    / "vr" / "captures")))
_SHOWN = {}


TEX_SRC = {}             # (area, room, cx, cy) -> (sx, sy): whose drawing a cell wears
ACTOR_KINDS = (3, 7)    # entity kinds: enemy, NPC (include/entity.h)
ACTOR_PAD = 4           # px: an actor's position may sit just off its sprite


def shown(r):
    """(RGB, sprite mask) of the room as the running game shows it --
    tile layers and object sprites, no HUD, no Link -- from a capture
    (tools/room_capture.py), or None where the room has none."""
    k = (r.area, r.room)
    if k not in _SHOWN:
        import room_capture as RCap
        got = RCap.load_shown(f"{r.area:02d}_{r.room:02d}", CAPTURES)
        _SHOWN[k] = None
        if got is not None:
            h, w = r.cells_h * 16, r.cells_w * 16
            rgb, spr = got
            if rgb.shape[0] >= h and rgb.shape[1] >= w:
                rgb = np.array(rgb[:h, :w, :3])
                spr = np.array(spr[:h, :w])
                # people and enemies are actors, built by the entity stage
                # and moving about: their sprites are not the room's
                # surface. Each sprite shape round where one stands takes
                # the tile art back.
                tiles = RE.room_art_rgb(r, 0)
                actors = [(e["x"] - r.origin_x, e["y"] - r.origin_y) for e in r.entities
                          if e["kind"] in ACTOR_KINDS and not e.get("is_player")]
                if actors and tiles is not None:
                    tiles = tiles[:h, :w, :3]
                    lab, n = RE._label(spr)
                    for j in range(1, n + 1):
                        ys_, xs_ = np.nonzero(lab == j)
                        if any(xs_.min() - ACTOR_PAD <= ax <= xs_.max() + ACTOR_PAD
                               and ys_.min() - ACTOR_PAD <= ay <= ys_.max() + ACTOR_PAD
                               for ax, ay in actors):
                            rgb[ys_, xs_] = tiles[ys_, xs_]
                            spr[ys_, xs_] = False
                _SHOWN[k] = (np.ascontiguousarray(rgb), spr)
    return _SHOWN[k]


def shown_art(r):
    """The room's picture as the game shows it where it was captured
    (arches over doorways, shelves on the floor), else its ground layer."""
    got = shown(r)
    return got[0] if got is not None else RE.room_art_rgb(r, 0)


def stairwell_cells(r, cls, R, exits):
    """Cells of a box room's wall band the game marks as a way through
    (Picori's door surfaces) that are neither ring nor exit."""
    act = r.layers[0]["act"][:cls.shape[0], :cls.shape[1]]
    names = {v: PL.act_short(int(v)) or "" for v in np.unique(act)}
    door = np.vectorize(lambda v: "DOOR" in names[v])(act)
    return door & (cls == RE.CLASS_WALL) & ~R & ~exits


def open_doorways(r, cls, ways, one):
    """The pixels of a room's doorways that are open: where a doorway is
    drawn opening onto the dark (a stairwell, a back door), only the dark
    between its jambs -- the jambs and the plates above them are the
    wall's; where it is lit (the way out, onto the path), all of it."""
    h, w = cls.shape
    gap = np.kron(ways, one)
    lum_ = lambda a: np.asarray(a)[:h * 16, :w * 16, :3].astype(np.int64) @ np.array([299, 587, 114]) // 1000
    drawn_dark = lum_(RE.room_art_rgb(r, 0)) < STAIR_DARK      # lit or dark: the room's own
    dark = lum_(shown_art(r)) < STAIR_DARK                      # how wide: what the game shows
    lab, n = RE._label(ways)
    for k in range(1, n + 1):
        m = np.kron(lab == k, one)
        if not (drawn_dark & m).any():
            continue                                # lit: all of it
        ys, xs = np.nonzero(m)
        wide = (xs.max() - xs.min()) >= (ys.max() - ys.min())
        frac = ((dark & m).sum(axis=0) / np.maximum(1, m.sum(axis=0))) if wide else \
               ((dark & m).sum(axis=1) / np.maximum(1, m.sum(axis=1)))
        openl = frac >= DOORWAY_DARK
        keep = m & (openl[None, :] if wide else openl[:, None])
        gap[m] = keep[m]
    return gap


def stairwell_pieces(r, cls, shell, art=None):
    """A box room's stairwell (Link's, up to his loft): a run of cells in
    the wall's band that the game marks as a way through (Picori's door
    surfaces) but is neither the ring nor an exit. Its steps are built as
    drawn: each lit tread (a blob of the stairs' lighter colours) is a
    step, the dark band under it its riser -- as tall as it is drawn --
    so the k-th tread from the bottom stands k risers high, its footprint
    the drawn tread moved that far south (row = depth - height). A curved
    flight stays curved; only the steps the doorway shows are built, the
    dark beyond them left open."""
    if art is None:
        return []
    well = stairwell_cells(r, cls, shell["R"], shell["exits"])
    if not well.any():
        return []
    h, w = cls.shape
    A = np.asarray(art)[:h * 16, :w * 16, :3].astype(np.int64)
    lum = A @ np.array([299, 587, 114]) // 1000
    cells = np.kron(well, np.ones((16, 16), bool))
    drawn = cells & (lum >= STAIR_DARK)             # not the dark beyond
    if drawn.sum() < 32:
        return []
    cut = np.median(lum[drawn])
    tread = drawn & (lum >= cut)
    riser = drawn & (lum < cut)
    lab, n = RE._label(tread)
    blobs = [lab == k for k in range(1, n + 1) if (lab == k).sum() >= STAIR_TREAD_MIN]
    if not blobs:
        return []
    blobs.sort(key=lambda m: -np.nonzero(m)[0].mean())     # bottom step first
    # a riser's height: the dark rows under a tread, the median over columns
    rs = []
    for x in np.nonzero(riser.any(axis=0))[0]:
        col = riser[:, x]
        k = 0
        for v in col:
            if v:
                k += 1
            elif k:
                rs.append(k)
                k = 0
        if k:
            rs.append(k)
    e = int(np.clip(np.median(rs) if rs else STAIR_STEP, 2, 8))
    F = np.zeros_like(cells)
    out = []
    for k, m in enumerate(blobs, 1):
        hh = e * k
        # the tread and the riser drawn under it: one step, solid to the floor
        D = m.copy()
        for x in np.nonzero(m.any(axis=0))[0]:
            z1 = int(np.nonzero(m[:, x])[0].max()) + 1
            z2 = z1
            while z2 < D.shape[0] and riser[z2, x]:
                z2 += 1
            D[z1:z2, x] = True
        Wp, _fill = projective_plan(D, F, hh)
        out.append(dict(Wp=Wp, Hw=hh, base=shell["base"], fill=[], F=F))
    return out


STAIR_DARK = 40         # darker than this in a stairwell is the dark beyond
STAIR_TREAD_MIN = 12    # px: a lit blob this big is a tread


def table_quads(raw, fr, hh, art=None):
    """A table as a table: its top (the drawing above its front), its
    apron (the front rows drawn full width) and four legs (the front
    rows drawn as posts; the back pair mirrors the front, a table being
    symmetrical), hh tall. None if its drawing shows no two legs."""
    if raw is None or not raw.any():
        return None
    ys, xs = np.nonzero(raw)
    r0, rb = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    wid = x1 - x0
    s_ = rb + 1 - fr                                # first row of its front
    # the apron is drawn solid across; the legs' rows have the floor
    # showing between them
    legs_from = None
    for y in range(s_, rb + 1):
        xs_ = np.nonzero(raw[y])[0]
        if len(xs_) and (raw[y, xs_.min():xs_.max() + 1].mean() < 0.95):
            legs_from = y
            break
    if legs_from is None or legs_from <= s_:
        return None                                 # no posts under an apron
    # the legs: the runs of its feet
    row = raw[rb, x0:x1]
    runs, x = [], 0
    while x < wid:
        if row[x]:
            a = x
            while x < wid and row[x]:
                x += 1
            runs.append((x0 + a, x0 + x))
        else:
            x += 1
    if len(runs) < 2:
        return None
    runs = [runs[0], runs[-1]]
    depth = s_ - r0                                  # its top, drawn
    zf = rb + 1                                      # its front, at its feet
    zb = zf - depth
    k = hh / float(rb + 1 - s_)                      # px of height per drawn row
    apron = max(1.0, (legs_from - s_) * k)
    ar = np.nonzero(raw[legs_from - 1])[0]           # the apron's own width
    ax0, ax1 = (int(ar.min()), int(ar.max()) + 1) if len(ar) else (x0, x1)
    q = cuboid(ax0, ax1, hh - apron, hh, zb, zf,
               (ax0 + 1, ax1 - 1, s_, legs_from), (x0, x1, r0, s_))
    for a, b in runs:
        w = b - a
        for z0 in (zf - w, zb):                     # front pair, back pair
            q += cuboid(a, b, 0, hh - apron, z0, z0 + w,
                        (a, b, legs_from, rb + 1))
    # the floor under it, which the game never draws: the floor just
    # north of its drawing, as deep as it is (a rug's middle, not its hem)
    d_ = zf - zb
    v0 = max(0, r0 - d_)
    q.append(([(x0, 0.05, zb), (x1, 0.05, zb), (x1, 0.05, zf), (x0, 0.05, zf)],
              [(x0, v0), (x1, v0), (x1, v0 + d_), (x0, v0 + d_)]))
    return q


def heightmap_quads(hm, ox, oz, y0):
    """Pixel columns hm high (0 = none) from y0, textured by projection."""
    rows, cols = hm.shape

    def uv(pts):
        return [(x - ox, (z - oz) - (y - y0)) for x, y, z in pts]
    q = []
    for v in np.unique(hm):
        if v <= 0:
            continue
        for x, z, wd, d in RE.greedy_quads(hm == v):
            y = y0 + int(v)
            p = [(ox + x, y, oz + z), (ox + x + wd, y, oz + z),
                 (ox + x + wd, y, oz + z + d), (ox + x, y, oz + z + d)]
            q.append((p, uv(p)))

    def ht(z, x):
        return int(hm[z, x]) if 0 <= z < rows and 0 <= x < cols else 0
    for dz in (1, -1):
        for z in range(rows):
            x = 0
            while x < cols:
                hi, lo = ht(z, x), ht(z + dz, x)
                if hi > lo:
                    x0 = x
                    while x < cols and ht(z, x) == hi and ht(z + dz, x) == lo:
                        x += 1
                    zz = oz + (z + 1 if dz == 1 else z)
                    a, b = y0 + hi, y0 + lo
                    if dz == 1:
                        p = [(ox + x0, a, zz), (ox + x, a, zz), (ox + x, b, zz), (ox + x0, b, zz)]
                    else:
                        p = [(ox + x, a, zz), (ox + x0, a, zz), (ox + x0, b, zz), (ox + x, b, zz)]
                    q.append((p, uv(p)))
                else:
                    x += 1
    for dx in (1, -1):
        for x in range(cols):
            z = 0
            while z < rows:
                hi, lo = ht(z, x), ht(z, x + dx)
                if hi > lo:
                    z0 = z
                    while z < rows and ht(z, x) == hi and ht(z, x + dx) == lo:
                        z += 1
                    xx = ox + (x + 1 if dx == 1 else x)
                    a, b = y0 + hi, y0 + lo
                    if dx == 1:
                        p = [(xx, a, oz + z0), (xx, a, oz + z), (xx, b, oz + z), (xx, b, oz + z0)]
                    else:
                        p = [(xx, a, oz + z), (xx, a, oz + z0), (xx, b, oz + z0), (xx, b, oz + z)]
                    q.append((p, uv(p)))
                else:
                    z += 1
    return q


def shell_quads(sh, ox, oz, lift):
    """The ring's prism and the floor behind its front, projected; for a
    room with its box walls found, those instead."""
    if sh.get("box") is not None:
        return box_quads(sh["box"], ox, oz, sh["base"] + lift)
    if "quads" in sh:                   # built already (a table)
        y0 = sh["base"] + lift
        return [([(ox + x, y0 + y, oz + z) for x, y, z in p], uv) for p, uv in sh["quads"]]
    if "slope" in sh:
        return heightmap_quads(sh["slope"], ox, oz, sh["base"] + lift)
    Wp, Hw = sh["Wp"], sh["Hw"]
    y0 = sh["base"] + lift
    y1 = y0 + Hw
    rows, cols = Wp.shape

    k_ = sh.get("draw", Hw) / Hw if Hw else 1.0   # drawn rows per px of height

    def uv(pts):
        return [(x - ox, (z - oz) - (y - y0) * k_) for x, y, z in pts]
    q = []
    for x, z, wd, d in RE.greedy_quads(Wp):
        p = [(ox + x, y1, oz + z), (ox + x + wd, y1, oz + z),
             (ox + x + wd, y1, oz + z + d), (ox + x, y1, oz + z + d)]
        q.append((p, uv(p)))

    def at(z, x):
        return 0 <= z < rows and 0 <= x < cols and Wp[z, x]
    for dz in (1, -1):                  # faces along x
        for z in range(rows):
            x = 0
            while x < cols:
                if at(z, x) and not at(z + dz, x):
                    x0 = x
                    while x < cols and at(z, x) and not at(z + dz, x):
                        x += 1
                    zz = oz + (z + 1 if dz == 1 else z)
                    if dz == 1:
                        p = [(ox + x0, y1, zz), (ox + x, y1, zz), (ox + x, y0, zz), (ox + x0, y0, zz)]
                    else:
                        p = [(ox + x, y1, zz), (ox + x0, y1, zz), (ox + x0, y0, zz), (ox + x, y0, zz)]
                    if dz == 1 and not (z + 1 < sh["F"].shape[0] and sh["F"][z + 1, x0:x].any()):
                        # the outside of the front wall: never drawn; it
                        # wears the wall top's outer edge
                        v = min(z - sh.get("draw", Hw) - 2, sh["F"].shape[0] - 1) + 0.5
                        q.append((p, [(xp - ox, v) for xp, _y, _z in p]))
                    else:
                        q.append((p, uv(p)))
                else:
                    x += 1
    for dx in (1, -1):                  # faces along z
        for x in range(cols):
            z = 0
            while z < rows:
                if at(z, x) and not at(z, x + dx):
                    z0 = z
                    while z < rows and at(z, x) and not at(z, x + dx):
                        z += 1
                    xx = ox + (x + 1 if dx == 1 else x)
                    if dx == 1:
                        p = [(xx, y1, oz + z0), (xx, y1, oz + z), (xx, y0, oz + z), (xx, y0, oz + z0)]
                    else:
                        p = [(xx, y1, oz + z), (xx, y1, oz + z0), (xx, y0, oz + z0), (xx, y0, oz + z)]
                    # its own edge column, not the one beside it
                    q.append((p, [(x + 0.5, v) for _u, v in uv(p)]))
                else:
                    z += 1
    for x, z0, z1, src in sh["fill"]:   # floor behind the front wall
        p = [(ox + x, y0, oz + z0), (ox + x + 1, y0, oz + z0),
             (ox + x + 1, y0, oz + z1), (ox + x, y0, oz + z1)]
        q.append((p, [(x + 0.5, src + 0.5)] * 4))
    return q


# ----------------------------------------------------------------- faces --

def side_face(cx, cy, dx, dz, top, bottom, ox, oz):
    """One vertical face of cell (cx, cy) from height top down to bottom.

    Texels are in room-art pixels. Every face takes the d rows drawn above
    the cell's foot, bottom row at the foot (drawn row = z - height), one
    quad, since that mapping is linear. For a south face that IS the drawn
    front of the thing: a door frame, a cliff face. The game draws no
    north, east or west faces, so those wear the same front band, running
    left to right as seen from outside -- bark on a trunk's flank, rock on
    a cliff's end. Stretching the cell's edge pixel instead, as a first
    version did, streaked every flank.
    """
    x0, z0 = cx * 16, cy * 16
    X0, X1, Z0, Z1 = ox + x0, ox + x0 + 16, oz + z0, oz + z0 + 16
    a, b, d = top, bottom, top - bottom
    foot = z0 + 16
    tt, tb = foot - d, foot
    # Windings are room_explore's, outward. The band runs left to right as
    # seen from outside: on the east and west faces the first corner is the
    # right-hand one, so their texels run the other way.
    ltr = [(x0, tt), (x0 + 16, tt), (x0 + 16, tb), (x0, tb)]
    rtl = [(x0 + 16, tt), (x0, tt), (x0, tb), (x0 + 16, tb)]
    if dz == 1:
        return [(X0, a, Z1), (X1, a, Z1), (X1, b, Z1), (X0, b, Z1)], ltr
    if dz == -1:
        return [(X1, a, Z0), (X0, a, Z0), (X0, b, Z0), (X1, b, Z0)], ltr
    if dx == 1:
        return [(X1, a, Z0), (X1, a, Z1), (X1, b, Z1), (X1, b, Z0)], rtl
    return [(X0, a, Z1), (X0, a, Z0), (X0, b, Z0), (X0, b, Z1)], rtl


def pit_face(cx, cy, dx, dz, top, bottom, ox, oz):
    """The part of a face below the floor, down into a pit: the game never
    draws a pit's walls, so it wears the pit's own drawing -- the cell it
    drops into -- rather than the band above the thing's foot, which
    stacked copies of a fire box down the column it stands on."""
    pts, _uv = side_face(cx, cy, dx, dz, top, bottom, ox, oz)
    nx0, nz0 = (cx + dx) * 16, (cy + dz) * 16
    ltr = [(nx0, nz0), (nx0 + 16, nz0), (nx0 + 16, nz0 + 16), (nx0, nz0 + 16)]
    return pts, ltr


def inside_room(y, x, rows, cols):
    return 0 <= y < rows and 0 <= x < cols


def grain_face(pts, grain, dx, dz):
    """A face wearing one strip of wood grain (grain = (x0, y0, length),
    a clean run of the shelf board's grain in room-art pixels, 16 rows
    deep): the strip runs up the face, continuous across faces by the
    face's height above the room's floor, split only where it wraps. The
    sides of a bookcase, which the game never draws."""
    x0, y0, length = grain
    ys = sorted({p[1] for p in pts})
    lo, hi = ys[0], ys[-1]
    xs = sorted({p[0] for p in pts})
    zs = sorted({p[2] for p in pts})
    along_z = len(zs) > 1                  # east/west faces run along z
    out = []
    y = lo
    while y < hi:
        u0 = y % length
        y2 = min(hi, y + (length - u0))
        seg, uv = [], []
        for px, py, pz in pts:
            yy = y2 if py == hi else y
            seg.append((px, yy, pz))
            t = (pz - zs[0]) if along_z else (px - xs[0])
            span = max(16, (zs[-1] - zs[0]) if along_z else (xs[-1] - xs[0]))
            uv.append((x0 + (yy - y) + u0, y0 + min(16, t * 16 / span)))
        out.append((seg, uv))
        y = y2
    return out


def wood_patch(art):
    """(x0, y0, length): the longest clean run of plain wood -- a shelf
    board's grain -- 16 rows deep, in the drawing; or None."""
    hue, sat, v = _hsv(np.asarray(art)[:, :, :3].astype(np.uint8))
    wood = (hue >= 15) & (hue < 50) & (sat >= 0.2) & (sat < 0.75) & (v >= 0.6)
    best = None
    for y in range(0, wood.shape[0] - 16):
        colok = wood[y:y + 16].mean(axis=0) >= 0.95
        x, run_start, bestrun = 0, 0, (0, 0)
        while x < len(colok):
            if colok[x]:
                s0 = x
                while x < len(colok) and colok[x]:
                    x += 1
                if x - s0 > bestrun[1] - bestrun[0]:
                    bestrun = (s0, x)
            else:
                x += 1
        n = bestrun[1] - bestrun[0]
        if n >= 32 and (best is None or n > best[2]):
            best = (bestrun[0], y, n)
    return best


CASE_EDGE_SHARE = 0.5   # a row this much board front is a shelf's edge


def bookcase_edges(cls):
    """The rows of a bookcase's board fronts (docs/vr/06): the first wall
    row under a board -- walk one or two rows above it -- across at least
    CASE_EDGE_SHARE of the room; of adjacent such rows, the top one."""
    h, w = cls.shape
    walk = cls == RE.CLASS_GROUND
    wall = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    E = []
    for y in range(1, h):
        above = walk[y - 1] | (walk[y - 2] if y >= 2 else False)
        if (wall[y] & above).mean() >= CASE_EDGE_SHARE and not (E and E[-1] == y - 1):
            E.append(y)
    return E


def bookcase_quads(cls, H, art, ox, oz, lift):
    """A bookcase standing up: (quads, rows it covers) or None.

    The camera draws a point z deep and y high at row z - y (docs/vr/06),
    so a bookcase drawn as boards and books stacked up the screen is one
    upright case, not a terrace: every board's front edge (the one-row
    strip under its top) is in one plane, Zf, and the band above an edge
    row e -- the board's top and the books standing on it -- is at level
    Zf - 16 e. Each board cell lies at depth row*16 + level; each run of
    books stands where it meets its board, a vertical face; what stands
    on no board stands at the front. Its sides and back wear the shelf's
    grain, which the game never draws."""
    E = bookcase_edges(cls)
    if not E:
        return None
    h, w = cls.shape
    walk = cls == RE.CLASS_GROUND
    wall = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
    Em = E[-1]
    fl = walk[Em + 1:]
    floor = int(np.median(np.asarray(H)[Em + 1:][fl])) if fl.any() else 0
    Zf = (Em + 1) * 16 + floor
    q = []
    X, Zs = ox, oz

    def up(x, y_, z0, z1, v0, v1):
        p = [(X + x, y_, Zs + z0), (X + x + 16, y_, Zs + z0),
             (X + x + 16, y_, Zs + z1), (X + x, y_, Zs + z1)]
        return p, [(x, v0), (x + 16, v0), (x + 16, v1), (x, v1)]

    def down(x, y_, z0, z1, v):
        p = [(X + x, y_, Zs + z1), (X + x + 16, y_, Zs + z1),
             (X + x + 16, y_, Zs + z0), (X + x, y_, Zs + z0)]
        return p, [(x, v), (x + 16, v), (x + 16, v + 1), (x, v + 1)]

    def front(x, z, lo, hi, v_lo, v_hi):
        p = [(X + x, hi, Zs + z), (X + x + 16, hi, Zs + z),
             (X + x + 16, lo, Zs + z), (X + x, lo, Zs + z)]
        return p, [(x, v_hi), (x + 16, v_hi), (x + 16, v_lo), (x, v_lo)]

    zmin = Zf
    top = floor
    prev = -1
    for i, e in enumerate(E):
        L = Zf - 16 * e
        r0, r1 = prev + 1, e - 1                # the band above edge e
        ceil_ = (Zf - 16 * E[i - 1] - 16) if i else None   # next board's underside
        for cx in range(w):
            x = cx * 16
            # the board's front edge
            q.append(front(x, Zf, L - 16 + lift, L + lift, e * 16 + 16, e * 16))
            y = r0
            while y <= r1:
                if walk[y, cx]:
                    z0 = y * 16 + L
                    q.append(up(x, L + lift, z0, z0 + 16, y * 16, y * 16 + 16))
                    q.append(down(x, L - 16 + lift, z0, z0 + 16, e * 16 + 8))
                    zmin = min(zmin, z0)
                    y += 1
                    continue
                if not wall[y, cx]:
                    y += 1
                    continue
                a = y
                while y <= r1 and wall[y, cx]:
                    y += 1
                b = y                                   # run [a, b)
                on = b <= r1 and walk[b, cx]
                if not on and walk[r0:a, cx].any():
                    # in the board's top (a ladder's hole): lies with it
                    for yy in range(a, b):
                        z0 = yy * 16 + L
                        q.append(up(x, L + lift, z0, z0 + 16, yy * 16, yy * 16 + 16))
                    continue
                z = b * 16 + L if on else Zf
                hi = L + 16 * (b - a)
                q.append(front(x, z, L + lift, hi + lift, z - L, z - hi))
                zmin = min(zmin, z)
                top = max(top, hi)
                if ceil_ is not None and ceil_ > hi:
                    # up to the board above, behind its edge: the run's top row
                    q.append(front(x, z, hi + lift, ceil_ + lift, a * 16 + 1, a * 16))
        prev = e
    # the case's sides, back and top, in the shelf's grain
    grain = wood_patch(art)
    Zb = zmin - 16
    full = [cx for cx in range(w) if wall[:Em + 1, cx].all()]
    xl = (min(full) if full and min(full) < w // 2 else 0) * 16
    xr = ((max(full) + 1) if full and max(full) >= w // 2 else w) * 16
    lo, hi = floor + lift, top + lift
    faces = []
    for xx, dx in ((xl, -1), (xl + 16, 1), (xr - 16, -1), (xr, 1)):
        if dx == 1:
            p = [(X + xx, hi, Zs + Zb), (X + xx, hi, Zs + Zf), (X + xx, lo, Zs + Zf), (X + xx, lo, Zs + Zb)]
        else:
            p = [(X + xx, hi, Zs + Zf), (X + xx, hi, Zs + Zb), (X + xx, lo, Zs + Zb), (X + xx, lo, Zs + Zf)]
        faces.append((p, dx, 0))
    # inside, the back is the dark wood of the boards' edges, as the
    # game draws it where a book is missing
    v = E[0] * 16 + 8
    q.append(([(X + xl, hi, Zs + Zb), (X + xr, hi, Zs + Zb),
               (X + xr, lo, Zs + Zb), (X + xl, lo, Zs + Zb)],
              [(xl, v), (xr, v), (xr, v + 1), (xl, v + 1)]))
    faces.append(([(X + xr, hi, Zs + Zb), (X + xl, hi, Zs + Zb),
                   (X + xl, lo, Zs + Zb), (X + xr, lo, Zs + Zb)], 0, -1))     # back, outside
    for p, dx, dz in faces:
        if grain is not None:
            q.extend(grain_face(p, grain, dx, dz))
        else:
            q.append((p, [(0, 0)] * 4))
    if grain is not None:                                               # top
        gx, gy, gl = grain
        gw = min(gl, xr - xl)
        q.append(([(X + xl, hi, Zs + Zb), (X + xr, hi, Zs + Zb),
                   (X + xr, hi, Zs + Zf), (X + xl, hi, Zs + Zf)],
                  [(gx, gy), (gx + gw, gy), (gx + gw, gy + 16), (gx, gy + 16)]))
    return q, Em


def drop_faces(H, solid, ox, oz, lift, outside, skip=(), grain=None, skipd=()):
    """Vertical faces wherever a cell drops to a lower neighbour; not the
    south face of the cells in skip (doorways, built by door_quads). With
    grain, faces other than south fronts wear that wood patch."""
    quads = []
    rows, cols = H.shape
    for cy in range(rows):
        for cx in range(cols):
            if not solid[cy, cx]:
                continue
            hh = int(H[cy, cx])
            for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                ny, nx = cy + dz, cx + dx
                nh = (int(H[ny, nx]) if 0 <= ny < rows and 0 <= nx < cols
                      and solid[ny, nx] else outside)
                if nh < hh and not (dz == 1 and (cx, cy) in skip) \
                        and (cx, cy, dx, dz) not in skipd:
                    # above the floor, the drawn front; below it, the pit's
                    # own drawing (pit_face)
                    split = min(hh, max(nh, 0)) if nh < 0 and inside_room(ny, nx, rows, cols) else nh
                    if split < hh:
                        f = side_face(cx, cy, dx, dz, hh + lift, split + lift, ox, oz)
                        if grain is not None and dz != 1:
                            quads.extend(grain_face(f[0], grain, dx, dz))
                        else:
                            quads.append(f)
                    if split > nh:
                        quads.append(pit_face(cx, cy, dx, dz, split + lift, nh + lift,
                                              ox, oz))
    return quads


# ---------------------------------------------------------------- overlay --
# Layer 1 is the world's second level: canopies, bridge decks, roofs, the
# tops of fences drawn over Link. Only where it is drawn ABOVE layer 0
# (room_explore.overlay_overhead); indoors it is floor detail and is already
# in the composited floor tiles.
OVERLAY_LIFT = 24       # px above the cell's class height, as the terrain
OVERLAY_REACH = 24      # skirt reaches the ground when it is this close
OVERLAY_SKIRT = 12      # otherwise it hangs this far
DECK_MIN = 3            # cells: smallest group of lifted top-layer plates


def room_overlay(r, H, canopy=True):
    """(deck cells, crown field or None, top RGBA art, occupancy, c1, crown
    cells).

    Deck cells are layer-1 cells that draw something and are not tree
    crown: [(cx, cy, height)]. Crowns keep the terrain's shape
    (canopy_field), which a flat tile cannot carry.
    """
    import extract_art
    if not RE.overlay_overhead(r):
        return [], None, None, None, None, None
    top = extract_art.room_art(r, 1)
    if top is None:
        return [], None, None, None, None, None
    top = np.asarray(top)
    L1 = r.layers[1]
    h, w = r.cells_h, r.cells_w
    c1 = RE.classify_room(r, 1)
    occ = (L1["tile"][:h, :w] != 0) | (L1["collision"][:h, :w] != 0)
    crown = np.zeros((h, w), bool)
    cf = None
    if canopy:
        cmask, _top = RE.canopy_mask(r)
        if cmask is not None and cmask.any():
            crown = RE.crown_cells(r, cmask, np.asarray(_top))
            cf = RE.canopy_field(r, step=2,
                                 lift=RE.CLASS_HEIGHT[RE.CLASS_GROUND] + OVERLAY_LIFT)
            if cf is not None:
                # crowns moved south by their height run past the room's
                # south edge, over nothing; seen from outside, their
                # undersides and edges were thin spikes. Keep the room's plan.
                Hc, Cc, Mc = cf
                Mc = Mc.copy()
                Mc[(r.cells_h * 16) // 2:, :] = False
                Mc[:, (r.cells_w * 16) // 2:] = False
                cf = (Hc, Cc, Mc)
    decks = []
    for cy in range(h):
        for cx in range(w):
            if not occ[cy, cx] or crown[cy, cx]:
                continue
            a = top[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            if a.shape[:2] != (16, 16) or not (a[:, :, 3] > 0).any():
                continue
            decks.append((cx, cy, RE.CLASS_HEIGHT.get(c1[cy, cx], 0) + OVERLAY_LIFT))
    return decks, cf, top, occ, c1, crown


def deck_skirts(decks, occ, H, ox, oz, lift, top=None):
    """Aprons under the edges of lifted decks, where the ground comes close
    enough to carry them (a roof on its wall, a bridge on its bank) --
    room_explore.overlay_skirt_bottom decides. Where it does not, no apron:
    the deck's own tile has its edge."""
    quads = []
    rows, cols = H.shape
    Hi = np.asarray(H, dtype=np.int64)
    for cx, cy, hh in decks:
        for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny, nx = cy + dz, cx + dx
            if 0 <= ny < rows and 0 <= nx < cols and occ[ny, nx]:
                continue
            if top is not None:
                # the deck must draw along this edge; where it is mostly
                # transparent, an apron shows the ground under it as a board
                a = top[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16, 3] > 0
                edge = (a[-1] if dz == 1 else a[0] if dz == -1
                        else a[:, -1] if dx == 1 else a[:, 0])
                if edge.mean() < 0.5:
                    continue
            bottom, sup = RE.overlay_skirt_bottom(
                hh, Hi, occ, cy, cx, dz, dx, rows, cols,
                OVERLAY_REACH, OVERLAY_SKIRT)
            # only aprons that carry the deck: a hanging one shows whatever
            # is drawn under the deck's edge (sand, grass) as a board
            if sup and bottom < hh:
                quads.append(side_face(cx, cy, dx, dz, hh + lift, bottom + lift,
                                       ox, oz))
    return quads


def crown_quads(cf, ox, oz, lift):
    """The terrain's crown surface, textured by projecting the room art from
    the game's camera: a crown point at height y over (x, z) takes the pixel
    drawn at (x, z - y), where its leaves are drawn."""
    out = []
    base = RE.CLASS_HEIGHT[RE.CLASS_GROUND] + OVERLAY_LIFT

    def uvf(pts):
        return [(x - ox, (z - oz) - (y - lift)) for x, y, z in pts]

    def quad(pts, c, uv=None):
        out.append((pts, uv))
    RE.emit_canopy(quad, cf, ox, oz, lift + base, step=2, uvf=uvf, merge=True)
    return out


# ------------------------------------------------------------------ write --

class Obj:
    """Minimal textured OBJ writer: one texture, UVs from texel coords."""

    def __init__(self, path, header, texture, tw, th):
        self.path = Path(path)
        mtl = self.path.with_suffix(".mtl")
        with open(mtl, "w") as m:
            m.write("# Derived from the user's own ROM. Not redistributable.\n"
                    "newmtl tmc\nKa 1 1 1\nKd 1 1 1\nd 1\nillum 1\n"
                    f"map_Kd {texture}\n"
                    "newmtl tmc_flame\nKa 1 1 1\nKd 1 1 1\nKe 1 1 1\nd 1\nillum 1\n"
                    f"map_Kd {texture}\nmap_Ke {texture}\n")
        self.f = open(self.path, "w")
        self.f.write(header + f"mtllib {mtl.name}\nusemtl tmc\n")
        self.tw, self.th = float(tw), float(th)
        self.n = 0
        self.faces = 0

    def obj(self, name):
        self.f.write(f"o {name}\n")

    def use(self, material):
        self.f.write(f"usemtl {material}\n")

    def quads(self, quads, off=(0, 0, 0), toff=(0, 0)):
        ox, oy, oz = off
        su, sv = toff
        seen = {}
        lines, fl = [], []
        for pts, uvs in quads:
            idx = []
            for (x, y, z), (u, v) in zip(pts, uvs):
                k = (x + ox, y + oy, z + oz, u + su, v + sv)
                i = seen.get(k)
                if i is None:
                    self.n += 1
                    i = seen[k] = self.n
                    lines.append(f"v {k[0]} {k[1]} {k[2]}\n"
                                 f"vt {k[3] / self.tw:.6f} {1.0 - k[4] / self.th:.6f}\n")
                idx.append(i)
            fl.append("f %d/%d %d/%d %d/%d %d/%d\n"
                      % (idx[0], idx[0], idx[1], idx[1], idx[2], idx[2], idx[3], idx[3]))
        self.f.write("".join(lines))
        self.f.write("".join(fl))
        self.faces += len(fl)

    def close(self):
        self.f.close()


HEADER = ("# Derived from the user's own ROM; regenerate, do not redistribute.\n"
          "# world-pixel units, +X east, +Y up, +Z south\n")


ATLAS_COLS = 64                 # tiles per atlas row


def build_area(job):
    from PIL import Image
    global RELIEF_STEP
    area, paths, a = job
    RELIEF_STEP = a.relief_step
    rooms = []
    dump_path = {}
    for p in paths:
        try:
            r = RE.load_room(Path(p))
        except Exception:
            continue
        if PL.location(r.area, r.room)["view"] == "rotating":
            continue            # an affine background: nothing still to build
        if r.cells_w and r.cells_h and r.layers[a.layer]["present"]:
            art0 = RE.room_art_rgb(r, a.layer)
            if art0 is not None and len(np.unique(
                    art0[:r.cells_h * 16, :r.cells_w * 16, :3].reshape(-1, 3), axis=0)) <= 1:
                continue        # one flat colour: a placeholder room the game
                                # never draws (136_27, 72_22, 88_05, 88_06)
        if r.cells_w and r.cells_h and r.layers[a.layer]["present"]:
            rooms.append(r)
            dump_path[(r.area, r.room)] = p
    if not rooms:
        return area, 0, 0, 0, 0
    out = Path(a.out) / f"area_{area:02d}"
    out.mkdir(parents=True, exist_ok=True)
    levels = RE.assign_levels(rooms)

    # identify: every cell's drawing and role, keyed; heights as the terrain
    lib = {}                      # key -> (index, pixels, role)
    placed = []                   # (r, lift, art, H, solid, cells, overlay, flights)
    cases = {}                    # (area, room): a standing bookcase's inputs
    cliffs = {}                   # (area, room): cliff_bands' faces
    for li, lv in enumerate(levels):
        lift = li * a.floor_height
        for r in lv:
            art = RE.room_art_rgb(r, a.layer)
            if art is None:
                continue
            art = np.ascontiguousarray(art[:, :, :3].astype(np.uint8))
            cls, H, bl, flights, doors = room_heights(
                r, a.layer, blocks=not a.no_blocks, relief=a.relief,
                path=None if a.no_stairs else dump_path.get((r.area, r.room)))
            overlay = (None if a.no_overlay or len(r.layers) < 2
                       or not r.layers[1]["present"]
                       else room_overlay(r, H, canopy=not a.no_canopy))
            blds, covered = [], set()
            if overlay is not None and overlay[2] is not None and not a.no_buildings:
                H, blds, covered = find_buildings(r, cls, H, doors, overlay[1],
                                                  overlay[5])
            ups = []
            if not a.no_uprights:
                H, ups = find_uprights(r, cls, H, art)
            # what each cell is (tileid): props stand on the floor as their
            # own shape, foliage rounds off, flowers stand up
            fam = np.empty(cls.shape, dtype=object)
            shapes = {}
            under = {}
            if not a.no_families:
                fam, ffloor = TI.families(r, cls, H, art)
                ground = TI.ground_palette(art, cls)
                gcols = np.stack([(ground >> 16) & 255, (ground >> 8) & 255, ground & 255], -1)
                taken = set(covered) | {c for u in ups for c in u["cells"]} \
                    | {(d["cx"], y) for d in doors for y in range(d["top"], d["cy"] + 1)}
                for (bcy, bcx) in bl:
                    taken.add((bcx, bcy))
                for cy, cx in zip(*np.nonzero(fam != None)):  # noqa: E711
                    if (cx, cy) in taken:
                        fam[cy, cx] = None
                        continue
                    f = fam[cy, cx]
                    px = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                    if f in TI.PROP_FAMILIES:
                        H[cy, cx] = ffloor[cy, cx]
                        fl_px = None
                        for yy, xx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                            if 0 <= yy < r.cells_h and 0 <= xx < r.cells_w \
                                    and cls[yy, xx] == RE.CLASS_GROUND:
                                fl_px = art[yy * 16:yy * 16 + 16, xx * 16:xx * 16 + 16]
                                break
                        if fl_px is None:   # in water: what it stands in
                            wet = [(yy, xx) for yy, xx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1))
                                   if 0 <= yy < r.cells_h and 0 <= xx < r.cells_w
                                   and cls[yy, xx] == RE.CLASS_WATER]
                            if wet:         # standing up out of the water's surface
                                H[cy, cx] = min(int(H[yy, xx]) for yy, xx in wet)
                            for yy, xx in wet + [(yy, xx) for yy, xx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1))
                                                 if 0 <= yy < r.cells_h and 0 <= xx < r.cells_w and fam[yy, xx] is None]:
                                fl_px = art[yy * 16:yy * 16 + 16, xx * 16:xx * 16 + 16]
                                break
                        if f in SHAPED_FAMILIES:
                            m_ = TI.outline(px, fl_px) & off_ground(px, gcols)
                            if f == "stone":     # the water's ripple is water
                                hue_, sat_, _v = TI._hsv(px)
                                m_ = m_ & ~((hue_ >= 160) & (hue_ < 260) & (sat_ >= 0.25))
                            shapes[(cy, cx)] = (np.where(m_, 2, 1).astype(np.int64), 0)
                        else:
                            shapes[(cy, cx)] = (TI.shape_heights(px, f, fl_px), 0)
                    elif f == "flowers":
                        shapes[(cy, cx)] = (TI.flower_hmap(px, ground), 0)
                    elif f in TI.UPRIGHT_FAMILIES:
                        u = drawn_upright(r, cls, fam, art, cy, cx, f, ground,
                                          int(ffloor[cy, cx]))
                        if u is None:
                            fam[cy, cx] = None
                            continue
                        ups.append(u)
                        H[cy, cx] = u["base"]
                        under[(cy, cx)] = u["under"]
                hang_between(ups)
                # a cliff a flight climbs: its cells are drawn as its face,
                # which stands at their foot; raised to the upper level,
                # their tops are that level's ground, not the face again
                for fl_ in flights:
                    cl_ = set(fl_.get("cliff", ()))
                    if cl_ and fl_.get("top") is not None:
                        # the stone the flight cuts into is one block: up
                        # its columns while it is stone (a pillar's top),
                        # and its foot row when ground lies before it
                        hb_, wb_ = cls.shape
                        blk_ = lambda y_, x_: (0 <= y_ < hb_ and 0 <= x_ < wb_ and cls[y_, x_] in
                                               (RE.CLASS_WALL, RE.CLASS_LEDGE) and fam[y_, x_] is None)
                        more = set()
                        for (cy, cx) in cl_:
                            y_ = cy - 1
                            while blk_(y_, cx) and (y_, cx) not in cl_:
                                more.add((y_, cx))
                                y_ -= 1
                            if (cy + 1, cx) not in cl_ and blk_(cy + 1, cx) and not blk_(cy + 2, cx) \
                                    and not (0 <= cy + 2 < hb_ and cls[cy + 2, cx] in (RE.CLASS_WALL, RE.CLASS_LEDGE)):
                                more.add((cy + 1, cx))
                        for c_ in more:
                            if H[c_] < fl_["top"]:
                                H[c_] = fl_["top"]
                    for (cy, cx) in cl_:
                        # the ground right behind the cliff; a pillar or a
                        # wall that goes on behind it keeps its own top
                        sy = cy - 1
                        while (sy, cx) in cl_:
                            sy -= 1
                        if sy < 0 or cls[sy, cx] != RE.CLASS_GROUND:
                            continue
                        # the plainest ground near there (grass, not a pot
                        # or a flower bed, which would stand there twice)
                        best = None
                        for yy in range(max(0, sy - 3), sy + 1):
                            for xx in range(max(0, cx - 3), min(cls.shape[1], cx + 4)):
                                if cls[yy, xx] != RE.CLASS_GROUND or fam[yy, xx] is not None \
                                        or (yy, xx) in cl_:
                                    continue
                                px_ = art[yy * 16:yy * 16 + 16, xx * 16:xx * 16 + 16].reshape(-1, 3)
                                sc = (len(np.unique(px_, axis=0)), abs(xx - cx) + (sy - yy))
                                if best is None or sc < best[0]:
                                    best = (sc, (yy, xx))
                        if best is not None:
                            under[(cy, cx)] = best[1]
                    if os.environ.get("TV_DBG") and cl_:
                        print("CLIFFTOP", fl_["rect"], len(cl_), file=sys.stderr)
                # foliage: the solid stops FOLIAGE_ROUND short, and the
                # rounded top rises from there -- walls end where it begins
                fol = TI.foliage_hmaps(art, fam)
                for key_, hm in fol.items():
                    H[key_] = int(H[key_]) - TI.FOLIAGE_ROUND
                    shapes[key_] = (hm, 0)
                # clipped hedges: boxes as tall as their drawn front
                hedges, hedge_walls = hedge_cells(cls, fam, art)
                if os.environ.get("TV_DBG") and hedges:
                    print("HEDGE", f"{r.area:02d}_{r.room:02d}", len(hedge_walls),
                          sorted((x_, y_) for y_, x_ in hedge_walls)[:40], file=sys.stderr)
                for key_ in hedges:
                    if key_ in hedge_walls:
                        H[key_] = ffloor[key_]
                    shapes.pop(key_, None)
                    if fam[key_] == "foliage":
                        fam[key_] = None
            else:
                hedges = {}
            role = room_roles(r, cls, H, bl, art)
            for (cy, cx) in shapes:
                role[cy, cx] = fam[cy, cx]
            for key_ in hedges:
                role[key_] = "hedge"
            # built floors lie flat: every room indoors, in a dungeon or a
            # cave -- whatever its layer priorities say (246 dungeon rooms
            # draw layer 1 overhead) -- and any room whose layer 1 is not
            # overhead. Only open ground under the sky keeps its relief.
            loc = PL.location(r.area, r.room)
            if not RE.overlay_overhead(r) or not loc["open_air"]:
                role[role == "floor"] = "indoor"
                # built walls are flat: their drawing's shading is moss and
                # moulding, not relief
                role[role == "wall"] = "wallflat"
            for (cy, cx), (sy, sx) in under.items():
                role[cy, cx] = role[sy, sx] if role[sy, sx] in ("floor", "grass", "indoor") else "floor"
            # a cliff drawn folded out beyond its top (facing north, east or
            # west) is a face from the ground above to the ground below; the
            # cells it was drawn in are the ground above
            cb = cliff_bands(r, cls, H, fam, art, role,
                             taken={c for u in ups for c in u["cells"]})
            tilepix = {}
            if cb["cells"]:
                H = np.array(H)
                for (cy, cx), (hh_, ro_, px_) in cb["cells"].items():
                    H[cy, cx] = hh_
                    role[cy, cx] = ro_
                    tilepix[(cy, cx)] = px_
                cliffs[(r.area, r.room)] = cb
                if os.environ.get("TV_DBG"):
                    print("CLIFF", f"{r.area:02d}_{r.room:02d}", len(cb["cells"]), len(cb["faces"]), file=sys.stderr)
            # an enclosed room's ring of wall is one shell, not cells
            shell = None if a.no_shell else room_shell(
                r, cls, H, fam, doors,
                exclude={c for u in ups for c in u["cells"]} | set(covered))
            if shell is not None:
                H = np.array(H)
                H[shell["R"]] = shell["base"]
                # furniture: flat-topped, as tall as it is for the people
                # who use it (furniture_heights)
                taken_ = {(y_, x_) for (y_, x_) in bl} | \
                    {(y_, d["cx"]) for d in doors for y_ in range(d["top"], d["cy"] + 1)} | \
                    {(c[1], c[0]) for u in ups for c in u["cells"]}
                sh_ = shown(r)
                fart = sh_[0] if sh_ is not None else art   # sprites drawn in it
                fh = furniture_heights(r, cls, shell, fam, taken_, fart,
                                       sprites_seen=sh_ is not None)
                shell["pieces"] = [pz for _one, pz in furniture_prisms(cls, shell, fh, fart)]
                if shell.get("box") is not None:
                    shell["pieces"] += stairwell_pieces(r, cls, shell, fart)
                for (y_, x_) in fh:
                    H[y_, x_] = shell["base"]
                    shell["R"][y_, x_] = True       # built by its prism, not a tile
                # what is left blocked off the ring is where a sprite stands
                # (furniture objects): floor, for the entity stage to stand on
                left = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) & ~shell["R"] & (fam == None)  # noqa: E711
                for (y_, x_) in zip(*np.nonzero(left)):
                    if (int(y_), int(x_)) not in taken_ and not shell["exits"][y_, x_]:
                        H[y_, x_] = shell["base"]
                        role[y_, x_] = "indoor"
            # a bookcase stands up by itself (bookcase_quads): its rows are
            # no tiles, and the floor before it no drop
            caserows = -1
            if PL.location(r.area, r.room)["view"] == "terrace":
                E_ = bookcase_edges(cls)
                if E_:
                    caserows = E_[-1]
                    cases[(r.area, r.room)] = (cls.copy(), np.array(H), art)
                    H = np.array(H)
                    fl_ = (cls == RE.CLASS_GROUND)[caserows + 1:]
                    H[:caserows + 1] = int(np.median(H[caserows + 1:][fl_])) if fl_.any() else 0
            cells = []
            for cy in range(r.cells_h):
                for cx in range(r.cells_w):
                    ro = role[cy, cx]
                    if ro is None:
                        continue
                    if cy <= caserows:
                        continue
                    if shell is not None and shell["R"][cy, cx]:
                        continue
                    if shell is not None and shell.get("box") is not None \
                            and (not shell["exits"][cy, cx]
                                 or ro in ("wall", "wallflat", "ledge")):
                        continue            # the box draws the room: its floor
                                            # one surface, its walls and doors
                                            # openings (their jambs too); the
                                            # ground through an exit alone
                                            # stays tiles
                    sy, sx = under.get((cy, cx), (cy, cx))
                    if (cx, cy) in covered:
                        # under or behind a building: the floor behind it,
                        # else the floor in front
                        ro = "floor"
                        ys_ = [y for y in range(cy, -1, -1) if (cx, y) not in covered]
                        sy = ys_[0] if ys_ and cls[ys_[0], cx] == RE.CLASS_GROUND else None
                        if sy is None:
                            fy = max(b["foot"] for b in blds)
                            sy = min(fy, r.cells_h - 1)
                    pix = tilepix[(cy, cx)] if (sy, sx) == (cy, cx) and (cy, cx) in tilepix \
                        else art[sy * 16:sy * 16 + 16, sx * 16:sx * 16 + 16]
                    if pix.shape[:2] != (16, 16):
                        continue
                    hm, dy_ = shapes.get((cy, cx), (None, 0))
                    if ro == "hedge" and (cy, cx) in hedges:
                        hm = hedges[(cy, cx)]
                        k = hashlib.sha1(repr(hm).encode()).hexdigest()[:12] + "hg"
                    else:
                        k = tile_key(pix, ro, hm)
                    if k not in lib:
                        lib[k] = (len(lib), pix, ro, hm)
                    cells.append((k, cx, cy, int(H[cy, cx]) + dy_ + lift, ro))
                    if sy is not None and (sy, sx) != (cy, cx) and (cy, cx) not in tilepix \
                            and (cx, cy) not in covered:    # seen through its doorway: its own
                        TEX_SRC[(r.area, r.room, cx, cy)] = (sx, sy)
            ov = None
            if overlay is not None:
                decks, cf, top, occ, _c1, _crown = overlay
                roofs = {(x_, y_) for b in blds for (y_, x_) in b["roof"]}
                decks = [dk for dk in decks if (dk[0], dk[1]) not in roofs]
                # A plate over a solid cell is the top of that thing -- a
                # flower box, a fence post, a sign -- which the composited
                # tile already shows; lifted, it floated with an apron. Keep
                # only what Link walks under: over open ground, water or a
                # pit, in groups of DECK_MIN or more (canopies, bridges).
                blocked0 = np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE])
                keep = np.zeros(cls.shape, bool)
                for dk in decks:
                    if not blocked0[dk[1], dk[0]]:
                        keep[dk[1], dk[0]] = True
                klab, kn = RE._label(keep)
                big = {k for k in range(1, kn + 1) if (klab == k).sum() >= DECK_MIN}
                decks = [dk for dk in decks if klab[dk[1], dk[0]] in big]
                # the top layer's arch over a doorway is part of the arch's
                # front, already drawn there; lifted, it floated
                arch = {(d["cx"] + dx_, y_) for d in doors for dx_ in (-1, 0, 1)
                        for y_ in range(d["top"], d["cy"] + 1)}
                decks = [dk for dk in decks if (dk[0], dk[1]) not in arch]
                arches = standing_arches(top, cls, H)
                acells = {c for a_ in arches for c in a_["cells"]}
                decks = [dk for dk in decks if (dk[1], dk[0]) not in acells]
                for cx, cy, hh in decks:
                    pix = np.ascontiguousarray(
                        top[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16].astype(np.uint8))
                    pix[pix[:, :, 3] == 0] = 0
                    k = tile_key(pix, "deck")
                    if k not in lib:
                        lib[k] = (len(lib), pix, "deck", None)
                    cells.append((k, cx, cy, hh + lift, "deck"))
                ov = (decks, cf, occ, top, arches)
            solid_ = cls != RE.CLASS_VOID
            solid_[:caserows + 1] = False           # the bookcase's own
            if shown(r) is not None:
                # captured: the room's faces wear what the game shows -- the
                # arches over its doorways, its sprites
                art = shown(r)[0]
            elif shell is not None and shell.get("W") is not None:
                # what the game's archway sprites cover (the plates on the
                # jambs) is not the wall's face: the wall runs on over it
                art = hide_under_arches(r, cls, art, shell)
            placed.append((r, lift, art, H, solid_, cells, ov,
                           flights, doors, blds, ups, shell))

    # voxelate: one model per drawing, packed into the area's atlas
    n = len(lib)
    arows = max(1, (n + ATLAS_COLS - 1) // ATLAS_COLS)
    atlas = np.zeros((arows * 16, ATLAS_COLS * 16, 3), np.uint8)
    models = {}
    lib_obj = Obj(out / "tiles.obj", HEADER + f"# area {area}: tile library, "
                  f"{n} drawings; atlas tiles.png, {ATLAS_COLS} per row\n",
                  "tiles.png", ATLAS_COLS * 16, arows * 16)
    for k, (i, pix, ro, hm) in lib.items():
        ty, tx = divmod(i, ATLAS_COLS)
        atlas[ty * 16:ty * 16 + 16, tx * 16:tx * 16 + 16] = pix[:, :, :3]
        if ro == "hedge":
            models[k] = hm
        elif ro in STAND_FAMILIES and hm is not None:
            models[k] = stand_quads(pix, np.asarray(hm) == 2)
        elif ro in MOUND_FAMILIES and hm is not None:
            models[k] = mound_quads(pix, np.asarray(hm) == 2, TI.SHAPES.get(ro, ("dome", 12))[1])
        else:
            models[k] = tile_quads(pix, RELIEF.get(ro, 1),
                                   pix[:, :, 3] > 0 if pix.shape[2] == 4 else None,
                                   RELIEF_STEPS.get(ro), hm)
        lib_obj.obj(f"t_{k}")
        lq = models[k]
        if ro == "hedge":       # its texture reaches the cell above: not in the atlas
            lq = [(p_, [(min(16, max(0, u_)), min(16, max(0, v_))) for u_, v_ in uv_])
                  for p_, uv_ in lq]
        lib_obj.quads(lq, toff=(tx * 16, ty * 16))
    lib_obj.close()
    Image.fromarray(atlas).save(out / "tiles.png")

    # place
    nfaces = ncell = 0
    with open(out / "placements.txt", "w") as place:
        place.write("# key room cx cy x y z role -- tile model origin, world pixels\n")
        for r, lift, art, H, solid, cells, ov, flights, doors, blds, ups, shell in placed:
            name = f"room_{r.area:02d}_{r.room:02d}"
            ox, oz = r.origin_x, r.origin_y
            for k, cx, cy, y, ro in cells:
                place.write(f"{k} {name} {cx} {cy} {ox + cx * 16} {y} "
                            f"{oz + cy * 16} {ro}\n")
            ncell += len(cells)
            if not a.merge:
                continue
            # what hangs overhead (an arch, a canopy) has its own strip
            # below the room's picture: the captured picture composites it
            # over the ground, which then wore it a second time
            deck_cells = [(cx, cy) for _k, cx, cy, _y, ro in cells if ro == "deck"]
            if ov is not None:
                deck_cells += [(cx, cy) for a_ in ov[4] for (cy, cx) in a_["cells"]]
            tex = art
            if deck_cells and ov is not None and ov[3] is not None:
                top_ = np.asarray(ov[3])
                low = RE.room_art_rgb(r, 0)
                tex = np.array(art)
                strip = np.zeros_like(tex)
                hh_, ww_ = tex.shape[:2]
                for cx, cy in deck_cells:
                    sl = (slice(cy * 16, cy * 16 + 16), slice(cx * 16, cx * 16 + 16))
                    t_ = top_[sl]
                    if t_.shape[:2] != (16, 16):
                        continue
                    strip[sl] = t_[:, :, :3]
                    if low is not None:
                        on = t_[:, :, 3] > 0 if t_.shape[2] == 4 else np.ones((16, 16), bool)
                        tex[sl][on] = np.asarray(low)[sl][:, :, :3][on]
                tex = np.vstack([tex, strip])
            Image.fromarray(np.ascontiguousarray(tex.astype(np.uint8))).save(out / f"{name}.png")
            m = Obj(out / f"{name}.obj", HEADER + f"# {name}: tiles placed on the terrain\n",
                    f"{name}.png", tex.shape[1], tex.shape[0])
            m.obj(name)
            for k, cx, cy, y, ro in cells:
                dv = art.shape[0] if (ro == "deck" and tex is not art) else 0
                # a cell built from another's drawing (the ground under a
                # fence, behind a building, on a cliff's top) wears that one
                tx_, ty_ = TEX_SRC.get((r.area, r.room, cx, cy), (cx, cy)) if not dv else (cx, cy)
                m.quads(models[k], (ox + cx * 16, y, oz + cy * 16), (tx_ * 16, ty_ * 16 + dv))
            skip = {(d["cx"], d["cy"]) for d in doors}
            grain = (wood_patch(art) if PL.location(r.area, r.room)["view"] == "terrace"
                     else None)
            if shell is not None and shell.get("box") is not None:
                # the box draws its walls and floor: no cell faces there
                solid = np.zeros_like(solid)
            cb = cliffs.get((r.area, r.room))
            m.quads(drop_faces(H, solid, ox, oz, lift, a.outside, skip, grain,
                               cb["skip"] if cb else ()))
            if cb:
                m.quads([([(ox + x, y + lift, oz + z) for x, y, z in p], uv)
                         for p, uv in cb["faces"]])
            if (r.area, r.room) in cases:
                bc = bookcase_quads(*cases[(r.area, r.room)], ox, oz, lift)
                if bc is not None:
                    m.quads(bc[0])
            if shell is not None:
                m.quads(shell_quads(shell, ox, oz, lift))
                for pz in shell.get("pieces", []):
                    m.quads(shell_quads(pz, ox, oz, lift))
            box_ = shell is not None and shell.get("box") is not None
            for d in doors:
                if not box_:            # in a box, a doorway is its wall's opening
                    m.quads(door_quads(d, ox, oz, lift))
            for b in blds:
                m.quads(building_quads(b, ox, oz, lift))
            for u in ups:
                m.quads(upright_quads(u, ox, oz, lift))
            m.use("tmc_flame")
            for u in ups:
                m.quads(upright_flame(u, ox, oz, lift))
            m.quads(torch_flames(r, art, H, lift, ox, oz))
            m.use("tmc")
            for fl in flights:
                m.quads(stair_quads(fl, ox, oz, lift))
            if ov is not None:
                decks, cf, occ, top_, arches_ = ov
                m.quads(deck_skirts(decks, occ, H, ox, oz, lift, top_))
                for a_ in arches_:
                    m.quads(arch_quads(a_, ox, oz, lift, art.shape[0]))
                if cf is not None:
                    m.quads(crown_quads(cf, ox, oz, lift))
            nfaces += m.faces
            m.close()
    with open(out / "stairs.txt", "w") as st:
        st.write("# room cx0,cy0,cx1,cy1 rise steps up base top -- flights of "
                 "steps; up/base/top are '-' where the landings are one level\n")
        for r, lift, _art, _H, _solid, _cells, _ov, flights, _doors, _b, _u, _s in placed:
            name = f"room_{r.area:02d}_{r.room:02d}"
            for fl in flights:
                raised = fl.get("up") is not None
                st.write(f"{name} {','.join(str(v) for v in fl['rect'])} "
                         f"{fl['rise']} {fl['steps']} "
                         f"{fl['up'] if raised else '-'} "
                         f"{fl['base'] + lift if raised else '-'} "
                         f"{fl['top'] + lift if raised else '-'}\n")
    return area, len(rooms), ncell, n, nfaces


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", help="directory of .tmcr dumps, or one dump")
    ap.add_argument("--area", default="", help="comma-separated areas; default all")
    ap.add_argument("--out", default="geom/tiles")
    ap.add_argument("--layer", type=int, default=0)
    ap.add_argument("--merge", action="store_true",
                    help="also write each room assembled, for viewing")
    ap.add_argument("--relief", action="store_true",
                    help="cell heights from room_explore --relief")
    ap.add_argument("--no-overlay", action="store_true",
                    help="leave out the top layer (canopies, decks, roofs)")
    ap.add_argument("--no-canopy", action="store_true",
                    help="tree crowns as flat deck tiles instead of shaped crowns")
    ap.add_argument("--no-stairs", action="store_true",
                    help="leave flights of steps flat (no raised landings, "
                         "no steps)")
    ap.add_argument("--no-families", action="store_true",
                    help="leave identified props, foliage and flowers to their "
                         "terrain roles (tileid.py)")
    ap.add_argument("--no-uprights", action="store_true",
                    help="leave braziers and torches to the heightfield")
    ap.add_argument("--no-buildings", action="store_true",
                    help="leave buildings to the heightfield")
    ap.add_argument("--no-shell", action="store_true",
                    help="build an enclosed room's ring of wall cell by cell")
    ap.add_argument("--no-blocks", action="store_true",
                    help="leave dungeon blocks to the measured heights")
    ap.add_argument("--relief-step", type=int, default=RELIEF_STEP,
                    choices=(1, 2, 4, 8, 16),
                    help="px block the relief is measured on (1 = per pixel)")
    ap.add_argument("--floor-height", type=int, default=64)
    ap.add_argument("--outside", type=int, default=-16)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()

    src = Path(a.dumps)
    files = [src] if src.is_file() else sorted(src.glob("room_*.tmcr"))
    by_area = {}
    for f in files:
        try:
            area = int(f.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        by_area.setdefault(area, []).append(str(f))
    want = ([int(x, 10) for x in a.area.split(",")] if a.area else sorted(by_area))
    jobs = [(ar, by_area[ar], a) for ar in want if ar in by_area]
    if not jobs:
        sys.exit("no rooms")
    tot = [0, 0, 0, 0]
    print(f"{'area':>5} {'rooms':>6} {'cells':>7} {'tiles':>6} {'faces':>9}")
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for area, nr, nc, nt, nf in ex.map(build_area, jobs):
            print(f"{area:5d} {nr:6d} {nc:7d} {nt:6d} {nf:9d}", flush=True)
            for i, v in enumerate((nr, nc, nt, nf)):
                tot[i] += v
    print(f"\n{tot[0]} rooms, {tot[1]} cells placed from {tot[2]} tile models "
          f"(per-area libraries) -> {a.out}")


if __name__ == "__main__":
    main()
