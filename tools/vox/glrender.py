"""Fast renders of a vox mesh: WebGL2 in headless Chromium (glrender.js).

The projection, crop, light and texel lookup are refcheck.render's: a
point projects by the camera's (right, up, toward), the image is the
case rectangle's floor plus `tall` px above it, the nearest surface wins,
a face shows the one texel its pixel names, and a lit face is shaded by
0.55 + 0.45 * max(0, n . LIGHT). Only the rasterizing moves to the GPU
(SwiftShader when there is none), so a whole room's millions of quads
render in seconds rather than an hour.

Needs node and playwright (as the repo's other browser checks do).
"""
import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
import refcheck as RC  # noqa: E402  (tools/ is on the path: vox.room puts it there)


def _job(V, F, UV, tex, view, box, scale, tall, lit, d, out):
    right, up, toward = view
    V = np.asarray(V, float)
    F = np.asarray(F, int).reshape(-1, 4)
    sx = V @ right * scale[0]
    sy = -(V @ up) * scale[1]
    depth = V @ toward
    x0, x1, z0, z1 = box
    corners = np.array([[x0, 0, z0], [x1, 0, z0], [x0, 0, z1], [x1, 0, z1],
                        [x0, tall, z0], [x1, tall, z0]], float)
    cx = corners @ right * scale[0]
    cy = -(corners @ up) * scale[1]
    ox, oy = cx.min(), cy.min()
    W = int(round(cx.max() - ox))
    H = int(round(cy.max() - oy))
    lo, hi = (float(depth.min()), float(depth.max())) if len(depth) else (0.0, 1.0)
    span = max(hi - lo, 1e-9)
    clip = np.stack([(sx - ox) / W * 2 - 1, 1 - (sy - oy) / H * 2,
                     (depth - lo) / span * 1.8 - 0.9], axis=1).astype(np.float32)
    if lit and len(F):
        a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
        shade = 0.55 + 0.45 * np.maximum(0.0, n @ RC.LIGHT)
    else:
        shade = np.ones(len(F))
    # each quad's 4 corners carry its texel and shade (flat)
    uv = np.zeros((len(V), 2), np.float32)
    sh = np.zeros(len(V), np.float32)
    uv[F.ravel()] = np.repeat(np.asarray(UV, np.float32).reshape(-1, 2), 4, axis=0)
    sh[F.ravel()] = np.repeat(shade, 4).astype(np.float32)
    tri = np.concatenate([F[:, [0, 1, 2]], F[:, [0, 2, 3]]], axis=1).reshape(-1, 3)
    d.mkdir(parents=True, exist_ok=True)
    clip.tofile(d / "pos.bin")
    uv.tofile(d / "uv.bin")
    sh.tofile(d / "shade.bin")
    tri.astype(np.uint32).tofile(d / "idx.bin")
    t = np.ascontiguousarray(tex[:, :, :3]).astype(np.uint8)
    rgba = np.concatenate([t, np.full(t.shape[:2] + (1,), 255, np.uint8)], axis=2)
    rgba.tofile(d / "tex.rgba")
    return dict(dir=str(d), w=W, h=H, tw=int(t.shape[1]), th=int(t.shape[0]),
                bg=[c_ / 255.0 for c_ in RC.BG], out=str(out))


def render_views(V, F, UV, tex, size, views, tall=64):
    """Images (H x W x 3 uint8) of the mesh, one per view: ("game",) is the
    game's camera cropped to the crop (size: its height and width), as
    scenes.render does; (yaw, pitch) a turned, lit view."""
    H, W = size
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        jobs = []
        for i, v in enumerate(views):
            if v == ("game",):
                jobs.append(_job(V, F, UV, tex, RC.camera(0, 45), (0, W, 0, H),
                                 (1.0, math.sqrt(2)), tall, False, td / f"j{i}", td / f"{i}.png"))
            else:
                jobs.append(_job(V, F, UV, tex, RC.camera(*v), (0, W, 0, H),
                                 (1.0, 1.0), tall, True, td / f"j{i}", td / f"{i}.png"))
        (td / "jobs.json").write_text(json.dumps(jobs))
        npm_root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["node", str(HERE / "glrender.js"), str(td / "jobs.json")],
                           capture_output=True, text=True, env={**__import__("os").environ, "NODE_PATH": npm_root})
        if r.returncode:
            raise RuntimeError("glrender.js: " + (r.stderr or r.stdout)[-400:])
        out = []
        for i, v in enumerate(views):
            img = np.asarray(Image.open(td / f"{i}.png").convert("RGB"))
            if v == ("game",):
                img = img[tall:tall + H, :W]
            out.append(img)
        return out
