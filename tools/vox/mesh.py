"""The solved pixels as a surface.

A pixel at row R, column X, with height yb at its lower edge and rise s
spans from (z = R + 1 + yb, y = yb) to (z = R + yb + s, y = yb + s): a
level quad when s = 0, an upright one when s = 1. Where two neighbours
do not meet, a face closes the gap; where an object's column ends on
lower ground behind it, its back closes down to that ground. Every face
wears its own pixel: the room's picture is the texture.
"""
import numpy as np


def build(yb, s, ground, ox=0, oz=0, px0=0, py0=0):
    """(V, F, UV): vertices, quads (index lists), per-quad pixel (u, v)
    in the picture's pixels. ox, oz place the crop in the world; px0, py0
    are its origin in the picture."""
    h, w = yb.shape
    V, F, UV = [], [], []

    def quad(pts, u, v):
        F.append(list(range(len(V), len(V) + 4)))
        V.extend(pts)
        UV.append((u + px0 + 0.5, v + py0 + 0.5))

    top = yb + s
    for R in range(h):
        for X in range(w):
            y0, y1 = yb[R, X], top[R, X]
            z0, z1 = R + 1 + y0, R + y1          # lower, upper edge depths
            x0, x1 = X, X + 1
            quad([(ox + x0, y1, oz + z1), (ox + x1, y1, oz + z1),
                  (ox + x1, y0, oz + z0), (ox + x0, y0, oz + z0)], X, R)
            # back: the pixel above is lower at their shared edge
            if R > 0:
                ya = yb[R - 1, X]
                if ya < y1 - 1e-6:
                    zb = R + y1
                    quad([(ox + x1, y1, oz + zb), (ox + x0, y1, oz + zb),
                          (ox + x0, ya, oz + zb), (ox + x1, ya, oz + zb)], X, R)
            # sides: the neighbour east is lower along the shared edge
            for dx, xe in ((1, x1), (-1, x0)):
                Xn = X + dx
                if not (0 <= Xn < w):
                    continue
                yn0, yn1 = yb[R, Xn], top[R, Xn]
                lo0, lo1 = min(y0, yn0), min(y1, yn1)
                if y0 > yn0 + 1e-6 or y1 > yn1 + 1e-6:
                    # a strip from this pixel's edge down to the neighbour's
                    a = (xe, y1, R + y1)
                    b = (xe, y0, R + 1 + y0)
                    c = (xe, yn0 if yn0 < y0 else y0, R + 1 + y0)
                    d = (xe, yn1 if yn1 < y1 else y1, R + y1)
                    pts = [a, b, c, d] if dx == 1 else [b, a, d, c]
                    quad([(ox + p[0], p[1], oz + p[2]) for p in pts], X, R)
    return np.asarray(V, float), F, UV
