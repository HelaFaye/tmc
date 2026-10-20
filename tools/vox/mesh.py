"""The solved pixels as a surface.

A pixel at row R, column X, with height yb at its lower edge and rise s
spans from (z = R + 1 + yb, y = yb) to (z = R + yb + s, y = yb + s): a
level quad when s = 0, an upright one when s = 1. Where two neighbours
do not meet, a face closes the gap; where an object's column ends on
lower ground behind it, its back closes down to that ground. Every face
wears its own pixel: the room's picture is the texture.

Built with numpy over the whole crop at once (a whole room is millions
of faces), as arrays: V (4n x 3), F (n x 4, each quad's vertex indices),
UV (n x 2, each quad's pixel).
"""
import numpy as np

EPS = 1e-6


def build(yb, s, ground, ox=0, oz=0, px0=0, py0=0, open_=None):
    """(V, F, UV): vertices, quads, per-quad pixel (u, v) in the picture's
    pixels. ox, oz place the crop in the world; px0, py0 are its origin in
    the picture. open_: pixels that close nothing down to lower
    neighbours (an awning: a sheet, open under it)."""
    h, w = yb.shape
    yb = np.asarray(yb, float)
    top = yb + s
    R, X = np.mgrid[0:h, 0:w]
    R = R.astype(float)
    X = X.astype(float)
    closes = np.ones((h, w), bool) if open_ is None else ~open_
    quads, uvs = [], []

    def add(pts, u, v):
        # pts: 4 arrays (k x 3); u, v: (k,)
        if len(u):
            quads.append(np.stack(pts, axis=1))
            uvs.append(np.stack([u, v], axis=1))

    def P(x, y, z):
        return np.stack([ox + x, y, oz + z], axis=1)

    y0, y1 = yb.ravel(), top.ravel()
    r, x = R.ravel(), X.ravel()
    # every pixel's own quad
    z0, z1 = r + 1 + y0, r + y1
    add([P(x, y1, z1), P(x + 1, y1, z1), P(x + 1, y0, z0), P(x, y0, z0)], x, r)
    # back: the pixel above is lower at their shared edge
    m = np.zeros((h, w), bool)
    m[1:] = (yb[:-1] < top[1:] - EPS) & closes[1:]
    if m.any():
        rr, xx = R[m], X[m]
        t1, ya = top[m], np.roll(yb, 1, axis=0)[m]
        zb = rr + t1
        add([P(xx + 1, t1, zb), P(xx, t1, zb), P(xx, ya, zb), P(xx + 1, ya, zb)], xx, rr)
        # the floor behind ends at depth R + ya (its pixel's lower edge);
        # the back stands at zb. Fill between, in that pixel's own colour:
        # the game's camera never sees it (the raised top in front covers
        # its rows)
        ta = np.roll(top, 1, axis=0)[m]
        f = np.abs(ta - ya) < EPS
        if f.any():
            rf, xf, yf, zf = rr[f], xx[f], ya[f], zb[f]
            add([P(xf, yf, rf + yf), P(xf + 1, yf, rf + yf), P(xf + 1, yf, zf), P(xf, yf, zf)],
                xf, rf - 1)
    # sides: the neighbour is lower along the shared edge
    for dx in (1, -1):
        nb0 = np.full((h, w), np.inf)
        nb1 = np.full((h, w), np.inf)
        if dx == 1:
            nb0[:, :-1], nb1[:, :-1] = yb[:, 1:], top[:, 1:]
        else:
            nb0[:, 1:], nb1[:, 1:] = yb[:, :-1], top[:, :-1]
        m = ((yb > nb0 + EPS) | (top > nb1 + EPS)) & closes & np.isfinite(nb0)
        if not m.any():
            continue
        rr, xx, a0, a1, n0, n1 = R[m], X[m], yb[m], top[m], nb0[m], nb1[m]
        xe = xx + 1 if dx == 1 else xx
        A = P(xe, a1, rr + a1)
        B = P(xe, a0, rr + 1 + a0)
        C = P(xe, np.where(n0 < a0, n0, a0), rr + 1 + a0)
        D = P(xe, np.where(n1 < a1, n1, a1), rr + a1)
        add([A, B, C, D] if dx == 1 else [B, A, D, C], xx, rr)
    if not quads:
        return np.zeros((0, 3)), np.zeros((0, 4), int), np.zeros((0, 2))
    Q = np.concatenate(quads)
    UV = np.concatenate(uvs) + np.array([px0 + 0.5, py0 + 0.5])
    V = Q.reshape(-1, 3)
    F = np.arange(len(V)).reshape(-1, 4)
    return V, F, UV
