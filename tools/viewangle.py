#!/usr/bin/env python3
"""One camera and one light, shared by every fitter.

Until now each builder carried its own idea of the view. build_boulder
treated a drawing as a plan, build_tree treated one as an elevation, and
build_chest split the difference with a hand-set fraction -- three answers
to the same question, and the disagreements produced a 56-deep tree slab
and a striped lotus.

THE CAMERA
    The ground projects 1:1: a floor tile is 16x16 world units and is drawn
    16x16 pixels, so one unit of DEPTH is one row on screen. Height is drawn
    into the same axis. That gives

        drawn_height = height + depth

    a 45-degree oblique, which is the classic three-quarter top-down view.
    It checks out against an object measured independently: the chest is
    drawn 16 rows, and those rows split 9 of body and 7 of lid deck.

    The useful consequence is that a drawing is never purely a plan or
    purely an elevation -- it is both, added together. Knowing an object's
    footprint therefore gives its height for free, and vice versa:

        height = drawn_height - depth
        depth  = drawn_height - height

THE LIGHT
    From above and to the upper LEFT (north-west). Every object measured so
    far agrees: the rock's shaded band sits lower-right, the tree canopy's
    highlights run from the upper left, the torch's stone is brightest on
    its top face. Checked against collision, which is stored separately
    from any palette: blocked cells average 0.49 shade against 0.77 for
    open floor, and blocked is darker in 98.7% of 78 rooms.

    So brightness reads as orientation: bright is a face turned up and
    toward the light, dark is a face turned away or down.
"""
import numpy as np

# --- camera -----------------------------------------------------------
GROUND_PX_PER_UNIT = 1.0     # a floor tile: 16 units deep, 16 rows drawn
HEIGHT_PX_PER_UNIT = 1.0     # height shares the same screen axis
OBLIQUE_DEG = 45.0           # the angle those two together imply

# --- light ------------------------------------------------------------
LIGHT_FROM = (-1.0, -1.0)    # (dx, dy) in tile space: upper left
LIGHT_NAME = "north-west, from above"


def split_drawn(drawn_h, depth=None, height=None):
    """Split a drawn vertical extent into height and depth.

    Give whichever you know; the other follows from drawn = height + depth.
    """
    if depth is not None:
        return max(0, int(round(drawn_h - depth))), int(depth)
    if height is not None:
        return int(height), max(0, int(round(drawn_h - height)))
    # Nothing known: an object as deep as it is tall splits evenly.
    h = int(round(drawn_h / 2.0))
    return h, int(drawn_h - h)


def depth_from_footprint(width):
    """A compact object is about as deep as it is wide."""
    return int(round(width))


def light_factor(dx, dy):
    """How strongly a face pointing (dx, dy) in the tile plane catches the
    light. 1.0 faces the light squarely, 0.0 faces away."""
    n = np.hypot(dx, dy)
    if n < 1e-6:
        return 1.0                      # facing straight up
    lx, ly = LIGHT_FROM
    ln = np.hypot(lx, ly)
    return float(max(0.0, (dx * lx + dy * ly) / (n * ln)))


def west_bias(dx):
    """The west side faces the light, so it is the lit, rounded side.

    Positive dx is east. Returns 1.0 on the west flank, 0.0 on the east.
    """
    return float(max(0.0, -dx))


def describe():
    return (f"camera: {OBLIQUE_DEG:.0f} deg oblique, drawn = height + depth, "
            f"ground {GROUND_PX_PER_UNIT:g}px/unit; "
            f"light: {LIGHT_NAME}")


if __name__ == "__main__":
    print(describe())
    for name, drawn, depth in (("floor tile", 16, 16), ("chest", 16, 7),
                               ("torch", 16, 14), ("rock", 16, 16)):
        h, d = split_drawn(drawn, depth=depth)
        print(f"  {name:11s} drawn {drawn:2d} rows, depth {d:2d} -> height {h:2d}")
