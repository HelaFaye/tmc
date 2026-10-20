"""Posterize a room's picture before anything is read from it.

Shape and shading are read from a picture reduced to POSTER_COLOURS
colours (median cut over the whole room, so one material is one colour
everywhere in it). Dithering, texture and anti-aliasing fold into the
colour they stand for, so regions are whole materials and shading is
their few drawn tones. Faces are still textured from the full picture:
from the game's camera the build looks as the game does.
"""
import numpy as np
from PIL import Image

POSTER_COLOURS = 24     # colours per room
POSTER_MIN_RUN = 2      # px: a colour narrower than this joins its neighbour


def posterize(px, n=POSTER_COLOURS):
    """(posterized picture, index map)."""
    im = Image.fromarray(np.ascontiguousarray(px[..., :3].astype(np.uint8)))
    q = im.quantize(colors=n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    idx = np.asarray(q)
    pal = np.asarray(q.getpalette()[:3 * n], np.uint8).reshape(-1, 3)
    # single-pixel specks (texture) take their row neighbours' colour
    for _ in range(POSTER_MIN_RUN - 1):
        l, r = np.roll(idx, 1, 1), np.roll(idx, -1, 1)
        speck = (idx != l) & (idx != r) & (l == r)
        idx = np.where(speck, l, idx)
    return pal[idx], idx
