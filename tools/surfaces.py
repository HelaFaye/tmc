"""What each cell IS, from the game's own tables rather than from its colour.

Every classifier in this project so far has read pixels: is this blob dark,
is it saturated, is it green. Those got a long way and each one cost a
round of "that's wrong". The game already knows. Each cell carries a
collision byte and an action byte, and the decompilation names what they
mean -- so a pit is a pit because the game says FX_FALL_DOWN, not because
it looked dark.

Two independent bytes agree on the important cases, which makes them worth
checking against each other rather than trusting either alone:

  collision 0x21  FX_FALL_DOWN     act 0x0d  SURFACE_PIT
  collision 0x24  FX_WATER_SPLASH  act 0x10  SURFACE_WATER

The table comes from gen_surfaces.py, which reads the decomp. Nothing here
is derived from ROM assets.
"""
import numpy as np

from surfaces_table import ACT_SURFACE, COLLISION_FX, SURFACE_ID  # noqa: F401

# Cells the player falls into. These must NOT become solid geometry: a pit
# built as a box is a floor the player can stand on, which is the opposite
# of what it is for.
PIT_SURFACES = ("SURFACE_PIT", "SURFACE_HOLE")
WATER_SURFACES = ("SURFACE_WATER", "SURFACE_SHALLOW_WATER",
                  "SURFACE_SLOPE_GNDWATER")
CLIMB_SURFACES = ("SURFACE_LADDER", "SURFACE_AUTO_LADDER",
                  "SURFACE_CLIMB_WALL")
FALL_FX = ("FX_FALL_DOWN",)
WATER_FX = ("FX_WATER_SPLASH",)


def surface_names(room, layer=0):
    """Per-cell surface name (or None) for a loaded room."""
    act = room.layers[layer]["act"]
    out = np.empty(act.shape, dtype=object)
    for idx, val in np.ndenumerate(act):
        out[idx] = ACT_SURFACE.get(int(val))
    return out


def collision_fx(room, layer=0):
    """Per-cell collision effect name (or None)."""
    coll = room.layers[layer]["collision"]
    out = np.empty(coll.shape, dtype=object)
    for idx, val in np.ndenumerate(coll):
        out[idx] = COLLISION_FX.get(int(val))
    return out


def _flag(room, layer, surfaces, fxs):
    s = surface_names(room, layer)
    f = collision_fx(room, layer)
    a = np.isin(s.astype(str), list(surfaces))
    b = np.isin(f.astype(str), list(fxs)) if fxs else np.zeros_like(a)
    return a | b


def is_pit(room, layer=0):
    """Cells the player falls through. Boolean array over the cell grid."""
    return _flag(room, layer, PIT_SURFACES, FALL_FX)


def is_water(room, layer=0):
    return _flag(room, layer, WATER_SURFACES, WATER_FX)


def is_button(room, layer=0):
    """Floor buttons -- the things that kept being mistaken for torches."""
    return _flag(room, layer, ("SURFACE_BUTTON",), ())


def is_edge(room, layer=0):
    """Ledges: the rim you can drop off, usually ringing a pit."""
    return _flag(room, layer, ("SURFACE_EDGE",), ())


def is_climbable(room, layer=0):
    return _flag(room, layer, CLIMB_SURFACES, ())


def agreement(room, layer=0):
    """How often the two bytes agree that a cell is a pit, and where not.

    Disagreement is informative, not an error: the rim of a pit is
    SURFACE_EDGE with ordinary collision, and a bridge deck laid over a pit
    is walkable collision above pit action.
    """
    s = surface_names(room, layer).astype(str)
    f = collision_fx(room, layer).astype(str)
    a = np.isin(s, list(PIT_SURFACES))
    b = np.isin(f, list(FALL_FX))
    return int((a & b).sum()), int((a & ~b).sum()), int((~a & b).sum())
