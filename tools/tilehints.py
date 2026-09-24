"""What a 16x16 tile's outline and shading say about its shape.

Two findings drive this file, both established earlier in the project:

  * A black outline is not decoration. It marks an OBLIQUE ANGLE
    TRANSITION -- the artist drew a line where one surface turns into
    another. So the outline is a map of a tile's edges in three dimensions,
    and where the line runs tells you which way the surface turns.

  * Shading tracks orientation, under a light from the north-west and
    above. Verified independently of any palette: collision-blocked cells
    average 0.490 brightness against 0.770 for open ones, and blocked came
    out darker in 98.7% of 78 rooms.

Every builder so far has re-derived some piece of this from scratch, badly
and differently. This reads it once, per distinct tile, and says what the
tile is: flat ground, a solid block, the top of a ledge, a stack of
courses, a round thing.

Nothing here reads a ROM. It works on the decoded art in a room dump.
"""
import numpy as np

import viewangle as VA

# A tile is 16x16. An outline stroke is near-black RELATIVE to the tile:
# dungeon art is dark overall and a fixed threshold calls half a room an
# outline.
OUTLINE_ABS = 56.0      # never call anything above this an outline
OUTLINE_REL = 0.42      # ...nor anything above this fraction of tile max
EDGE_FRAC = 0.55        # a border counts as outlined at this coverage
BAND_STEP = 24.0        # luminance apart enough to be a different face


def luma(tile):
    return tile[:, :, :3].astype(float).mean(axis=2)


def outline_mask(tile):
    """The near-black stroke, judged against this tile's own range."""
    lum = luma(tile)
    hi = float(lum.max())
    if hi <= 1.0:
        return np.zeros(lum.shape, bool)
    return (lum <= OUTLINE_ABS) & (lum <= hi * OUTLINE_REL)


def edges_outlined(ol):
    """Which of the tile's four borders carry a stroke."""
    h, w = ol.shape
    return {
        "north": float(ol[0, :].mean()) >= EDGE_FRAC,
        "south": float(ol[h - 1, :].mean()) >= EDGE_FRAC,
        "west": float(ol[:, 0].mean()) >= EDGE_FRAC,
        "east": float(ol[:, w - 1].mean()) >= EDGE_FRAC,
    }


def interior_lines(ol):
    """Rows and columns inside the tile that the artist drew a line along.

    A stack of horizontal lines is a stack of courses -- planks on a chest
    lid, stones in a wall. Vertical lines are uprights: posts, palings.
    """
    h, w = ol.shape
    inner = ol[1:h - 1, 1:w - 1]
    if inner.size == 0:
        return 0, 0
    rows = int((inner.mean(axis=1) >= EDGE_FRAC).sum())
    cols = int((inner.mean(axis=0) >= EDGE_FRAC).sum())
    return rows, cols


def shade_bands(tile, ol):
    """How many distinct flat shades the tile is painted in.

    One band is a flat surface. Two or three are faces meeting at an angle
    -- the thing has form. Many is texture or a gradient.
    """
    lum = luma(tile)[~ol]
    if lum.size == 0:
        return 0, 0.0
    vals = np.sort(lum)
    bands = 1
    ref = vals[0]
    for v in vals:
        if v - ref >= BAND_STEP:
            bands += 1
            ref = v
    return bands, float(vals.max() - vals.min())


def light_vector(tile, ol):
    """Which way the tile is lit: bright centroid minus dark centroid.

    Under a fixed north-west light this points along the surface normal's
    horizontal part. A convex thing -- a boulder, a dome -- is bright on
    the side facing the light and dark opposite, so the vector runs
    north-west. A hollow reverses it.
    """
    lum = luma(tile)
    ok = ~ol
    if ok.sum() < 8:
        return 0.0, 0.0, 0.0
    v = lum[ok]
    hi = v >= np.percentile(v, 75)
    lo = v <= np.percentile(v, 25)
    ys, xs = np.where(ok)
    if hi.sum() == 0 or lo.sum() == 0:
        return 0.0, 0.0, 0.0
    dx = float(xs[hi].mean() - xs[lo].mean())
    dy = float(ys[hi].mean() - ys[lo].mean())
    n = float(np.hypot(dx, dy))
    return dx, dy, n


def classify(tile):
    """One tile in, one shape hint out."""
    ol = outline_mask(tile)
    e = edges_outlined(ol)
    rows, cols = interior_lines(ol)
    bands, spread = shade_bands(tile, ol)
    dx, dy, mag = light_vector(tile, ol)
    ring = sum(e.values())
    frac = float(ol.mean())

    # The light runs north-west, so a convex surface is brighter toward the
    # upper left. Agreement with that is what separates a rounded thing
    # from a flat one that merely has two colours on it.
    lit = VA.light_factor(dx, dy) if mag > 0.5 else 0.0

    if frac < 0.02 and bands <= 1:
        kind = "flat"          # ground: no edges, no form
    elif ring == 4 and rows == 0 and cols == 0:
        kind = "block"         # boxed on all sides, one solid face
    elif ring == 4 and rows >= 2:
        kind = "courses"       # boxed, with horizontal banding inside
    elif ring == 4:
        kind = "solid"
    elif e["south"] and not e["north"]:
        kind = "ledge"         # a line along the bottom only: a drop away
    elif e["north"] and not e["south"]:
        kind = "riser"         # line along the top: a face rising up
    elif rows >= 2 and cols == 0:
        kind = "courses"
    elif cols >= 2 and rows == 0:
        kind = "uprights"
    elif bands >= 3 and lit > 0.35:
        kind = "round"         # several faces, lit the way a convex thing is
    elif bands >= 2:
        kind = "faceted"
    else:
        kind = "flat"

    return {
        "kind": kind, "outline_frac": frac, "ring": ring,
        "rows": rows, "cols": cols, "bands": bands, "spread": spread,
        "lit": lit, "dx": dx, "dy": dy,
        "north": e["north"], "south": e["south"],
        "west": e["west"], "east": e["east"],
    }
