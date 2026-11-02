"""Ground levels the way a player finds them.

Everywhere Link can walk without taking steps (or jumping off a ledge) is
one level: a walkable area. Between two areas, one behind the other down a
pixel column, the cliff between them is drawn as a face, and by the
45-degree rule its drawn rows are the difference in height. So:

  1. the walkable cells are split into connected areas, cut at flights of
     steps;
  2. down every column, wherever one area lies behind another across
     nothing but cliff (face rows, and the lip at its top), the face rows
     are one measure of how much higher the area behind is (a fence,
     posts or a stone in the way are things on the ground: no height); along every
     row, two areas side by side across a fence, posts or a bush (no
     cliff face) are level: a player sees across;
  3. each pair's measures give a median; the areas are set from the
     largest one along the pairs with the most measures. Areas no cliff
     joins to it (across a river) form their own group, whose lowest area
     is put at the lowest level of the largest one's group;
  4. water is not measured (a bank's lip is often drawn like a face): it
     lies just below the lowest walkable area beside it.

Measures across a building, a tree or anything the overhead layer draws
over are not taken: those are not cliffs. Cells in no area keep
tilevox's measured level (heights.ground_levels).
"""
import numpy as np

from .room import RE

CLIFF_KINDS = ("face", "top")       # what lies between two areas across a cliff
MIN_MEASURES = 4                    # columns: fewer relate two areas too weakly
SIDE_GAP = 2                        # cells: areas side by side across this little are level
WATER_BELOW = 2                     # px: water lies this far below its lowest bank


def areas(rm, flights):
    """(labels, n): cell labels of the walkable areas; steps' cells
    belong to none."""
    walk = rm.cls == RE.CLASS_GROUND
    for f in flights:
        x0, y0, x1, y1 = f["rect"]
        walk[y0:y1 + 1, x0:x1 + 1] = False
    return RE._label(walk)


def measures(lab, K0, alpha_cells, things=None):
    """{(south, north): [face rows]} down every column of cells, at pixel
    resolution of 16 rows per cell. things: cells of named things standing
    on the ground (a fence, a post, a stone): they are no cliff, and add no
    height."""
    rows, cols = lab.shape
    if things is None:
        things = np.zeros((rows, cols), bool)
    out = {}
    for cx in range(cols):
        cy = rows - 1
        while cy > 0:
            a = lab[cy, cx]
            if a == 0:
                cy -= 1
                continue
            # leave area a northwards
            g = cy - 1
            faces, ok = 0, True
            while g >= 0 and lab[g, cx] == 0:
                if things[g, cx]:
                    g -= 1
                    continue
                if alpha_cells[g, cx] or K0[g, cx] not in CLIFF_KINDS:
                    ok = False
                faces += K0[g, cx] == "face"
                g -= 1
            if g >= 0 and ok and lab[g, cx] != a and g < cy - 1:
                out.setdefault((int(a), int(lab[g, cx])), []).append(16 * faces)
            cy = g
    # side by side across a fence, posts or a bush (at most SIDE_GAP cells,
    # none of them a cliff face): one level, as a player sees across it
    for cy in range(rows):
        cx = 0
        while cx < cols:
            a = lab[cy, cx]
            if a == 0:
                cx += 1
                continue
            g = cx + 1
            while g < cols and lab[cy, g] == a:
                g += 1
            e = g
            while e < cols and lab[cy, e] == 0 and e - g < SIDE_GAP:
                if (K0[cy, e] == "face" and not things[cy, e]) or alpha_cells[cy, e]:
                    break
                e += 1
            if g < cols and e < cols and lab[cy, e] not in (0, a) and e > g:
                out.setdefault((int(a), int(lab[cy, e])), []).extend([0] * 16)
            cx = g if g > cx else cx + 1
    return out


def solve(rm, K0, alpha_cells, H0, flights, things=None):
    """(H, labels, {area: level}): per cell, the level from the cliffs
    between walkable areas (H0, tilevox's, for cells no area covers)."""
    lab, n = areas(rm, flights)
    if n == 0:
        return np.asarray(H0, float), lab, {}
    m = measures(lab, K0, alpha_cells, things)
    pairs = {}
    for (a, b), d in m.items():
        if len(d) < MIN_MEASURES:
            continue
        key = (min(a, b), max(a, b))
        sign = 1 if a == key[0] else -1      # level[b] - level[a] = median rise, south to north
        prev = pairs.get(key)
        if prev is None or len(d) > prev[1]:
            pairs[key] = (sign * float(np.median(d)), len(d))
    size = np.bincount(lab.ravel(), minlength=n + 1)
    edges = sorted(pairs.items(), key=lambda kv: -kv[1][1])
    level, groups = {}, []
    # from the largest area, along the best-measured pairs first; then the
    # next largest area no cliff has reached, and so on
    for root in [int(j) for j in np.argsort(-size[1:]) + 1]:
        if root in level or size[root] == 0:
            continue
        group = {root: 0.0}
        grew = True
        while grew:
            grew = False
            for (a, b), (d, _cnt) in edges:
                if a in group and b not in group and b not in level:
                    group[b] = group[a] + d
                    grew = True
                elif b in group and a not in group and a not in level:
                    group[a] = group[b] - d
                    grew = True
        if not groups:
            base = float(np.median(H0[lab == root]))
            shift = base
            floor0 = base + min(group.values())
        else:
            shift = floor0 - min(group.values())
        for j, v in group.items():
            level[j] = v + shift
        groups.append(group)
    H = np.asarray(H0, float).copy()
    for j, v in level.items():
        H[lab == j] = v
    # water just below the lowest walkable area beside it
    water = rm.cls == RE.CLASS_WATER
    wl, wn = RE._label(water)
    for j in range(1, wn + 1):
        m = wl == j
        ring = np.zeros_like(m)
        ring[1:] |= m[:-1]; ring[:-1] |= m[1:]; ring[:, 1:] |= m[:, :-1]; ring[:, :-1] |= m[:, 1:]
        near = [level[int(a)] for a in np.unique(lab[ring & (lab > 0)]) if int(a) in level]
        if near:
            H[m] = min(near) - WATER_BELOW
    return H, lab, level


def seat(lab, level, cells):
    """Per cell in cells (a thing on a wall cell: a post, a stone), the
    level of the walkable ground beside it: the area left, right or behind
    it (north), within two cells; in front of it (south) only if nothing
    else is near."""
    rows, cols = lab.shape
    out = {}
    for cy, cx in zip(*np.nonzero(cells)):
        found = []
        for dy, dx in ((0, -1), (0, 1), (-1, 0), (0, -2), (0, 2), (-2, 0)):
            y, x = cy + dy, cx + dx
            if 0 <= y < rows and 0 <= x < cols and lab[y, x] and int(lab[y, x]) in level:
                found.append(level[int(lab[y, x])])
        if not found:
            for dy in (1, 2):
                y = cy + dy
                if y < rows and lab[y, cx] and int(lab[y, cx]) in level:
                    found.append(level[int(lab[y, cx])])
                    break
        if found:
            out[(int(cy), int(cx))] = float(np.median(found))
    return out
