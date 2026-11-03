"""A built room as a compact mesh for a live viewer.

assemble.build gives one quad per pixel face, each textured with one
texel. Here a run of pixel quads along a row with the same heights and
depths at their edges becomes one strip, its texture running along it, and
the rest keep their one texel. The mesh is written as:

  <rid>.mesh.gz  gzip of, little-endian: magic "TMCV", version u16, flags u16,
               quads u32, then per vertex (4 per quad, in order)
               x, y, z  int16 (quarter pixels)  and  u, v  uint16 (quarter
               texels), then per quad  wall u8  (1: a wall or other thing
               a viewer may fade to see into a room)
  <rid>_tex.png  its texture (the picture, and under it the copy house
               bodies take their walls' colours from)

Both are made from the player's own ROM art: they stay local or on the
private review page, never in the repository.
"""
import gzip
import struct
from pathlib import Path

import numpy as np
from PIL import Image

MAGIC = b"TMCV"
VERSION = 1
Q = 4.0             # quarter pixels


def merge_rows(V, F, UV):
    """(corners, uv, kept): quads merged along x where they are pixel quads
    of one row with equal heights and depths. corners: (n, 4, 3) floats;
    uv: (n, 4, 2) texel coordinates per corner (UV in assemble's meshes is
    each quad's texel centre); kept: index of the first source quad of
    each output quad."""
    V = np.asarray(V, float)
    F = np.asarray(F, int).reshape(-1, 4)
    UV = np.asarray(UV, float)
    C = V[F]                                     # (n, 4, 3)
    n = len(C)
    # a pixel quad of a row: corners 0,1 share y,z (top edge), 2,3 share
    # y,z (bottom edge), 0 and 3 at x = a, 1 and 2 at x = a + 1
    x0, x1 = C[:, 0, 0], C[:, 1, 0]
    rowq = (np.abs(C[:, 0, 1] - C[:, 1, 1]) < 1e-6) & (np.abs(C[:, 0, 2] - C[:, 1, 2]) < 1e-6) & \
        (np.abs(C[:, 2, 1] - C[:, 3, 1]) < 1e-6) & (np.abs(C[:, 2, 2] - C[:, 3, 2]) < 1e-6) & \
        (np.abs(C[:, 3, 0] - x0) < 1e-6) & (np.abs(C[:, 2, 0] - x1) < 1e-6) & \
        (np.abs(x1 - x0 - 1) < 1e-6) & (np.abs(UV[:, 0] - 0.5 - x0) < 1e-6)
    key = np.stack([C[:, 0, 1], C[:, 0, 2], C[:, 3, 1], C[:, 3, 2], UV[:, 1]], 1)
    idx = np.nonzero(rowq)[0]
    order = idx[np.lexsort((x0[idx], *key[idx].T[::-1]))]
    ks, xs = key[order], x0[order]
    brk = np.ones(len(order), bool)
    if len(order) > 1:
        brk[1:] = np.any(ks[1:] != ks[:-1], axis=1) | (np.abs(xs[1:] - xs[:-1] - 1) > 1e-6)
    starts = np.nonzero(brk)[0]
    ends = np.append(starts[1:], len(order)) - 1
    A, B = order[starts], order[ends]
    mc = C[A].copy()
    mc[:, 1, 0] = mc[:, 2, 0] = C[B, 1, 0]
    v = UV[A, 1] - 0.5
    xa, xb = x0[A], C[B, 1, 0]
    muv = np.stack([np.stack([xa, v], 1), np.stack([xb, v], 1),
                    np.stack([xb, v + 1], 1), np.stack([xa, v + 1], 1)], 1)
    rest = np.nonzero(~rowq)[0]
    ruv = np.repeat(UV[rest][:, None, :], 4, axis=1)     # its one texel's centre
    return (np.concatenate([mc, C[rest]]), np.concatenate([muv, ruv]),
            np.concatenate([A, rest]).astype(int))



def write(path, V, F, UV, tex, wall=None):
    """Write <path>.mesh.gz and <path>.png. wall: per source quad, whether
    a viewer may fade it."""
    corners, uv, kept = merge_rows(V, F, UV)
    w = np.zeros(len(kept), np.uint8) if wall is None else np.asarray(wall, bool)[kept].astype(np.uint8)
    pos = np.clip(np.round(corners * Q), -32768, 32767).astype("<i2").reshape(-1, 3)
    tuv = np.clip(np.round(uv * Q), 0, 65535).astype("<u2").reshape(-1, 2)
    verts = np.concatenate([pos.view("<u2"), tuv], 1).astype("<u2")
    path = Path(path)
    with gzip.open(str(path) + ".mesh.gz", "wb", compresslevel=9) as f:
        f.write(MAGIC + struct.pack("<HHI", VERSION, 0, len(kept)))
        f.write(verts.tobytes())
        f.write(w.tobytes())
    Image.fromarray(np.ascontiguousarray(tex[:, :, :3])).save(str(path) + "_tex.png")
    return len(kept)


def wall_quads(rm, UV):
    """Per quad, whether it is part of a wall a viewer may fade (the
    room's walls, upright faces): from assemble's wall_px."""
    wp = rm.meta.get("wall_px")
    if wp is None:
        return None
    h, w = wp.shape
    u = np.clip(np.asarray(UV)[:, 0].astype(int), 0, w - 1)
    v = np.asarray(UV)[:, 1].astype(int) % h
    return wp[v, u]
