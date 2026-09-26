#!/usr/bin/env python3
"""find_stairs.py — every staircase and stairway doorway in the game.

Stairs have no single label. Four signals find them, all from the game's own
tables rather than from colour:

  steps         Flights of steps: cells whose action byte is a slope,
                SURFACE_SLOPE_GNDGND_V (0x26, rising north/south) or _H
                (0x27, rising east/west) -- walkable ground that carries Link
                between two floor heights. 251 cells in 57 rooms.
  stair-arch    A doorway in a wall with steps rising into it, like the
                bridge entrance of area 141. Tile types 0x91-0x93, 0x9a,
                0x9b (tiles.h names 0x92 STAIRS_UP and 0x93 STAIRS_DOWN).
  stairwell     Steps going down through a railed opening in the floor.
                Tile types 0x8f, 0x90.
  steps-to-door A short run of steps leading up to a doorway. Tile types
                0x4d6, 0x503, 0x520, 0x99.

The tile-type groups other than 0x92/0x93 were sorted by looking at every
room-transition doorway (SURFACE_DOOR_13, 0x28) in the game; house doors,
tree doors, Minish holes and burrows were left out. Tile types are per
tileset in principle, so a new group may turn up in a room not yet
harvested: check the --sheet output.

Neighbouring cells of one kind are merged into one run. Each output line is
a manifest line: coordinates only, no art.

Usage
-----
  python3 tools/find_stairs.py vrdump                       # print the list
  python3 tools/find_stairs.py vrdump --out objects/stairs.txt
  python3 tools/find_stairs.py vrdump --sheet stairs.png    # labelled crops
  python3 tools/find_stairs.py vrdump --kind steps --sheet steps.png

For flights of steps it also measures each step from the drawing (see
measure_steps): steps=N, and per step the drawn pitch in pixels split into
tread (lit) and riser (dark) -- by the 45-degree rule, depth and height.
--sheet draws the step boundaries it found in red, for checking.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_explore as RE  # noqa: E402

SLOPE_V, SLOPE_H = 0x26, 0x27
BY_TILETYPE = {
    "stair-arch": (0x91, 0x92, 0x93, 0x9a, 0x9b),
    "stairwell": (0x8f, 0x90),
    "steps-to-door": (0x4d6, 0x503, 0x520, 0x99),
}


def runs(mask):
    """4-connected components of a boolean cell mask -> list of cell lists."""
    seen = np.zeros_like(mask, bool)
    out = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, cells = [(y0, x0)], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            cells.append((int(y), int(x)))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if (0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]
                        and mask[ny, nx] and not seen[ny, nx]):
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append(cells)
    return out


def composite_rgb(r):
    """Both layers as the game draws them, as a float RGB array."""
    import extract_art as A
    img = A.room_art(r, 0)
    if img is None:
        return None
    img = img.copy()
    top = A.room_art(r, 1)
    if top is not None:
        m = top[:, :, 3] > 0
        img[m] = top[m]
    return img[:, :, :3].astype(float)


# Step boundaries. Chosen against six flights counted by eye, and checked by
# drawing the result on every flight (--sheet). A boundary is a row (a column
# for an east-west flight) that is DARKER than its surroundings across the
# whole width of the stairs:
#   STEP_WINDOW  rows of local average the darkness is measured against, so
#                a flight that darkens toward the bottom still reads
#   STEP_DEPTH   how much darker than that average (0..255 luminance)
#   STEP_MINGAP  closest two boundaries may be; nearer ones are one line
#   STEP_GROW    how much longer than the flight's median step a step
#                beyond the slope cells may be and still count
#   STEP_STRONG  how dark, as a share of the flight's median boundary, a
#                boundary beyond the slope cells must be
#   STEP_COLOUR  how far (RGB distance) the lit colour of a step beyond the
#                slope cells may be from the flight's treads
STEP_WINDOW, STEP_DEPTH, STEP_MINGAP, STEP_GROW, STEP_STRONG, STEP_COLOUR = \
    15, 12, 4, 1.75, 0.6, 40.0


def measure_steps(img, rect, rise):
    """Boundaries between the steps of one flight, read off the drawing.

    Takes the median across the width of the flight at every row, so stone
    texture that varies from column to column cancels and only lines that run
    the whole width survive -- the dark nose line of each step, and the
    edges to the landing above and the floor below.

    Returns dict(edges=[world-pixel rows or columns, from the room origin],
    bands=[(start, end, tread_px, riser_px)], steps=len(bands)). Each band is
    one step as drawn; by the 45-degree rule (drawn = height + depth, see
    viewangle.py) its lit part is the tread's depth and its dark part is the
    riser's height.
    """
    x0, y0, x1, y1 = rect
    # Read one cell beyond each end of the flight. The slope cells mark
    # where Link walks, and the drawn steps often run past them: the top
    # step of the temple stairs in room 49_00 starts half a cell above its
    # slope cells, and the Hyrule Town gate's sideways steps are wider than
    # their one-cell strip.
    if rise == "ew":
        lo, hi = x0 * 16, (x1 + 1) * 16
        rx0, rx1 = max(0, x0 - 1), min(img.shape[1] // 16 - 1, x1 + 1)
        rgb = img[y0 * 16:(y1 + 1) * 16, rx0 * 16:(rx1 + 1) * 16].transpose(1, 0, 2)
        start = rx0 * 16
    else:
        lo, hi = y0 * 16, (y1 + 1) * 16
        ry0, ry1 = max(0, y0 - 1), min(img.shape[0] // 16 - 1, y1 + 1)
        rgb = img[ry0 * 16:(ry1 + 1) * 16, x0 * 16:(x1 + 1) * 16]
        start = ry0 * 16
    reg = rgb.mean(axis=2)
    row_rgb = np.median(rgb, axis=1)          # one colour per row
    if reg.shape[0] < 4 or reg.shape[1] < 1:
        return dict(edges=[], bands=[], steps=0)
    p = np.median(reg, axis=1)
    w = STEP_WINDOW
    base = np.convolve(np.pad(p, w // 2, mode="edge"), np.ones(w) / w,
                       mode="valid")[:len(p)]
    d = p - base
    dips = [i for i in range(1, len(d) - 1)
            if d[i] < -STEP_DEPTH and d[i] <= d[i - 1] and d[i] <= d[i + 1]]
    def merge(ix):
        # Darkest first; keep a line only if no kept line is within
        # STEP_MINGAP. Merging in order instead chained lines 3px apart
        # (135, 138, 141 in room 104_00) into one and lost a step whose
        # real boundaries were 6px apart.
        out = []
        for i in sorted(ix, key=lambda j: d[j]):
            if all(abs(i - k) >= STEP_MINGAP for k in out):
                out.append(i)
        return sorted(out)

    # Inside the slope cells and outside them are merged separately, so a
    # line at the cell edge is never swallowed by a darker one just past it
    # (which made the reader lose steps it had found before).
    inside = [i for i in dips if lo <= start + i + 1 < hi]
    core = merge(inside)
    before = merge([i for i in dips if start + i + 1 < lo])
    after = merge([i for i in dips if start + i + 1 >= hi])
    found = before + core + after
    # The boundaries inside the slope cells are the flight. Grow it outward
    # one boundary at a time while the next gap still looks like a step --
    # up to STEP_GROW times the flight's median spacing -- so a taller top
    # step joins but the pattern of the floor beyond does not.
    # A boundary beyond them must also be as strong a line as the flight's
    # own: at least STEP_STRONG of its median darkness. The temple's top
    # step edge is darker than its median; floor pattern and a dirt bank's
    # texture are fainter, and without this test they were taken as steps.
    # And the band it adds must be drawn in the flight's own colours: its
    # lit part within STEP_COLOUR (RGB distance) of the flight's treads. The
    # desert temple's walls have strong, evenly spaced brick courses right
    # above the steps; they are brown, the treads are cream.
    def tread_colour(a, b):
        seg = d[a + 1:b + 1]
        lit = row_rgb[a + 1:b + 1][seg > 0]
        return lit.mean(axis=0) if len(lit) else None

    if len(core) >= 2:
        pitch = float(np.median(np.diff(core)))
        strong = STEP_STRONG * float(np.median(d[core]))
        cols = [c for c in (tread_colour(a, b) for a, b in zip(core, core[1:]))
                if c is not None]
        ref = np.median(cols, axis=0) if cols else None

        def like_flight(a, b):
            c = tread_colour(a, b)
            return (ref is not None and c is not None
                    and float(np.linalg.norm(c - ref)) <= STEP_COLOUR)

        edges = list(core)
        for i in reversed([i for i in before if i < core[0] - STEP_MINGAP + 1]):
            if (edges[0] - i <= STEP_GROW * pitch and d[i] <= strong
                    and like_flight(i, edges[0])):
                edges.insert(0, i)
            else:
                break
        for i in [i for i in after if i > core[-1] + STEP_MINGAP - 1]:
            if (i - edges[-1] <= STEP_GROW * pitch and d[i] <= strong
                    and like_flight(edges[-1], i)):
                edges.append(i)
            else:
                break
    else:
        edges = found
    bands = []
    for a, b in zip(edges, edges[1:]):
        seg = d[a + 1:b + 1]
        lit = int((seg > 0).sum())
        bands.append((start + a + 1, start + b + 1, lit, len(seg) - lit))
    # Steps in one flight are drawn close to evenly. A band more than twice
    # the median pitch means a boundary was missed or the reading picked up
    # something beside the stairs: report it, don't trust it.
    pitches = [b - a for a, b, _, _ in bands]
    irregular = bool(pitches) and max(pitches) > 2 * float(np.median(pitches))
    return dict(edges=[start + e + 1 for e in edges], bands=bands,
                steps=len(bands), irregular=irregular)


def find(room_path):
    r = RE.load_room(room_path)
    found = []
    if not r.cells_w:
        return found
    img = None
    for li in (0, 1):
        L = r.layers[li]
        if not L["present"]:
            continue
        H, W = r.cells_h, r.cells_w
        act = L["act"][:H, :W]
        tt = L["tiletype"][np.clip(L["tile"], 0, len(L["tiletype"]) - 1)][:H, :W]
        kinds = [("steps", act == SLOPE_V, "ns"), ("steps", act == SLOPE_H, "ew")]
        kinds += [(k, np.isin(tt, ts), "") for k, ts in BY_TILETYPE.items()]
        for kind, mask, rise in kinds:
            for cells in runs(mask):
                ys = [c[0] for c in cells]
                xs = [c[1] for c in cells]
                it = dict(room=room_path.name, layer=li, kind=kind,
                          rise=rise, n=len(cells),
                          rect=(min(xs), min(ys), max(xs), max(ys)))
                if kind == "steps":
                    if img is None:
                        img = composite_rgb(r)
                    if img is not None:
                        it["measure"] = measure_steps(img, it["rect"], rise)
                found.append(it)
    return found


def sheet(items, dumps, out, per_row=5):
    from PIL import Image, ImageDraw
    import extract_art as A
    cache, tiles = {}, []
    for it in items:
        name = it["room"]
        if name not in cache:
            r = RE.load_room(Path(dumps) / name)
            img = A.room_art(r, 0).copy()
            top = A.room_art(r, 1)
            if top is not None:
                m = top[:, :, 3] > 0
                img[m] = top[m]
            cache[name] = img
        img = cache[name]
        x0c, y0c, x1c, y1c = it["rect"]
        cx, cy = (x0c + x1c) // 2, (y0c + y1c) // 2
        ox, oy = max(0, (cx - 3) * 16), max(0, (cy - 3) * 16)
        crop = Image.fromarray(img[oy:oy + 112, ox:ox + 112]).convert("RGB")
        d = ImageDraw.Draw(crop)
        d.rectangle([x0c * 16 - ox, y0c * 16 - oy,
                     (x1c + 1) * 16 - ox - 1, (y1c + 1) * 16 - oy - 1],
                    outline=(255, 0, 255))
        crop = crop.resize((224, 224), Image.NEAREST)
        d = ImageDraw.Draw(crop)
        m = it.get("measure")
        if m:
            for e in m["edges"]:
                if it["rise"] == "ew":
                    xx = (e - ox) * 2
                    d.line([xx, (y0c * 16 - oy) * 2, xx, ((y1c + 1) * 16 - oy) * 2],
                           fill=(255, 40, 40), width=2)
                else:
                    yy = (e - oy) * 2
                    d.line([(x0c * 16 - ox) * 2, yy, ((x1c + 1) * 16 - ox) * 2, yy],
                           fill=(255, 40, 40), width=2)
        d.rectangle([0, 0, 224, 11], fill=(0, 0, 0))
        label = f"{it['kind']} {name[5:-5]} {x0c},{y0c}"
        if m:
            label += f"  {m['steps']} steps" + ("  CHECK" if m.get("irregular") else "")
        d.text((2, 0), label, fill=(255, 255, 0))
        tiles.append(crop)
    rows = (len(tiles) + per_row - 1) // per_row
    W = Image.new("RGB", (224 * per_row, 224 * rows), (20, 20, 20))
    for i, t in enumerate(tiles):
        W.paste(t, ((i % per_row) * 224, (i // per_row) * 224))
    W.save(out)
    print(f"sheet: {len(tiles)} crops -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--out", default="", help="write the list here (a manifest)")
    ap.add_argument("--kind", default="", help="only this kind")
    ap.add_argument("--sheet", default="", help="also draw labelled crops to this PNG")
    ap.add_argument("--sheet-max", type=int, default=60)
    a = ap.parse_args()

    items = []
    for f in sorted(Path(a.dumps).glob("room_*.tmcr")):
        try:
            items += find(f)
        except Exception as e:
            print(f"  {f.name}: {e}", file=sys.stderr)
    if a.kind:
        items = [i for i in items if i["kind"] == a.kind]

    lines = ["# stairs, from tools/find_stairs.py. Coordinates only.",
             "# room              cells (cx0,cy0,cx1,cy1)  kind  options"]
    for it in items:
        x0, y0, x1, y1 = it["rect"]
        opt = f"layer={it['layer']}" + (f" rise={it['rise']}" if it["rise"] else "")
        m = it.get("measure")
        if m and m["steps"]:
            # pitch = drawn px per step; tread/riser split by shading
            opt += (f" steps={m['steps']}"
                    f" pitch={'/'.join(str(b - a) for a, b, _, _ in m['bands'])}"
                    f" tread={'/'.join(str(t) for _, _, t, _ in m['bands'])}"
                    f" riser={'/'.join(str(r_) for _, _, _, r_ in m['bands'])}")
            if m.get("irregular"):
                opt += " check=uneven"
        rect = f"{x0},{y0},{x1},{y1}"
        lines.append(f"{it['room']:<18} {rect:<14} {it['kind']:<13} "
                     f"{opt}   # {it['n']} cells")
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)
    counts = {}
    for it in items:
        k = counts.setdefault(it["kind"], [0, 0, set()])
        k[0] += 1
        k[1] += it["n"]
        k[2].add(it["room"])
    for k, (runs_, cells, rooms) in sorted(counts.items()):
        print(f"  {k:<13} {runs_:4d} runs, {cells:4d} cells, {len(rooms):3d} rooms",
              file=sys.stderr)
    if a.sheet:
        # A spread across rooms rather than the first N of one big room.
        seen, pick = set(), []
        for it in items:
            key = (it["room"], it["kind"])
            if key not in seen:
                seen.add(key)
                pick.append(it)
        sheet(pick[:a.sheet_max], a.dumps, a.sheet)


if __name__ == "__main__":
    main()
