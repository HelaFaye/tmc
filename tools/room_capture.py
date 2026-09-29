#!/usr/bin/env python3
"""room_capture.py -- a room as the running game shows it, sprites and all.

The room dumps hold the tile layers; what the game draws with sprites --
a doorway's arch, a shelf of plates, a water barrel -- is not in them.
This drives the port's room-capture harness (port/port_repro_roomcap.c):
it starts a new game, warps Link into the room and saves the screen.
From those screenshots it builds:

  shown   the room as the game shows it: tile layers and object sprites,
          without the HUD and without Link
  sprite  where that differs from the room's own tile layers: what the
          sprites draw

The harness hides the HUD with the game's own flags (gHUD.hideFlags =
HUD_HIDE_ALL) and stops drawing Link (TMC_ROOMCAP_CLEAN), so a capture is
the room alone. (With CLEAN off -- a game built without that option --
each view is captured twice with Link in two places, each showing what
he hides in the other, and the HUD is masked where it sits.) A capture is placed on the room where it best matches the
room's tile art; a room larger than the screen is covered by several
views.

Usage:
  room_capture.py DUMPS AA_RR [--game DIR] [--out DIR]
    writes DIR/room_AA_RR_shown.png and room_AA_RR_sprite.png (default
    vr/captures/, ignored: your ROM's art, keep it local)

The game directory holds a built tmc_pc with baserom.gba and assets (see
AGENTS.md); it runs headless (TMC_AUTOPLAY, dummy SDL drivers). Set
"color_correction": false in its config.json: the screen then shows the
tile art's own colours (with it on, the correction is learned back, less
exactly).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import extract_art  # noqa: E402
import room_explore as RE  # noqa: E402

OUT = HERE.parent / "vr" / "captures"
SCREEN = (240, 160)
# the HUD, in screen pixels (x0, y0, x1, y1): hearts, the item buttons,
# the rupee count
HUD = ((0, 0, 40, 20), (-64, 4, 0, 40), (-52, 142, 0, 160))   # x < 0: from the right
LINK_BOX = (-14, -30, 14, 8)    # px round Link's feet his sprite may cover
ALIGN_REACH = 16                # px either way to search for a capture's place
SETTLE = 300                    # frames after the warp before the capture
PROGRESS = 1                    # dungeons cleared: past the prologue, whose scripts
                                # take over a warp into Hyrule Field or the town
MATCH_MIN = 0.7                 # a capture this much like the room's tile art is it
CLEAN = True                    # the harness hides the HUD (the game's own
                                # HUD_HIDE_ALL) and Link (TMC_ROOMCAP_CLEAN):
                                # one capture a view, nothing to mask


def layers(r):
    """The room's tile layers as shown: the top layer over the ground's."""
    a0 = np.asarray(extract_art.room_art(r, 0))[..., :3].astype(np.int16)
    if len(r.layers) > 1 and r.layers[1]["present"]:
        a1 = np.asarray(extract_art.room_art(r, 1))
        top = a1[..., 3] > 0
        comp = a0.copy()
        comp[top] = a1[..., :3][top]
        return a0, comp
    return a0, a0


BINARY = os.environ.get("TMC_BINARY", "tmc_pc")
SHADE_SLOPES = np.arange(0.5, 0.97, 1 / 32)   # a blend keeps this share of the art
SHADE_FIT = 12                  # px value: how close a pixel must follow the blend
SHADE_SHARE = 0.25              # of the differing pixels the blend must explain


def passing_shade(shown, comp):
    """Pixels where the picture is the tile art under a blended layer --
    out = s * art + t, the same s and t (per channel) wherever it passes:
    the overworld's cloud shadows. The blend is learned from the pixels
    that differ, the one that explains most of them; none if it explains
    too few (they are sprites, not shade)."""
    diff = np.abs(shown - comp).sum(axis=2) > 24
    mask = np.zeros(diff.shape, bool)
    passing_shade.mask = mask
    if diff.sum() < 64:
        return mask
    a = comp[diff].astype(float)
    b = shown[diff].astype(float)
    best = None
    for sl in SHADE_SLOPES:
        t = np.median(b - sl * a, axis=0)
        fit = (np.abs(b - (sl * a + t)) <= SHADE_FIT).all(axis=1)
        if best is None or fit.sum() > best[0]:
            best = (int(fit.sum()), sl, t)
    n, sl, t = best
    if n < SHADE_SHARE * len(a):
        return mask
    mask = diff & (np.abs(shown.astype(float) - (sl * comp + t)) <= SHADE_FIT).all(axis=2)
    passing_shade.mask = mask
    return mask


def capture(game, area, room, x, y, out, settle=SETTLE):
    """Run the game once: warp Link to (x, y) in the room's own pixels (the
    debug warp's coordinates; above 0x3ff they mean "keep the position")
    and save the screen. Returns (RGB, camera (x, y) in room pixels or
    None); raises if the game ended up in another room."""
    env = dict(os.environ, TMC_AUTOPLAY="1", TMC_ROOMCAP="1",
               TMC_ROOMCAP_WARP=f"{area:#x},{room:#x},{x:#x},{y:#x},0",
               TMC_ROOMCAP_OUT=str(out), TMC_ROOMCAP_SETTLE=str(settle),
               TMC_ROOMCAP_PROGRESS=str(PROGRESS),
               TMC_ROOMCAP_CLEAN="1" if CLEAN else "0",
               SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    p = subprocess.run([f"./{BINARY}", "--no-audio"], cwd=game, env=env, timeout=600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    if not Path(out).is_file():
        raise RuntimeError(f"no capture for room {area:02d}_{room:02d} at ({x}, {y})")
    cam = None
    import re
    m = re.search(r"\[roomcap\] view room=0x([0-9a-f]+)/0x([0-9a-f]+) scroll=(-?\d+),(-?\d+)", p.stderr)
    if m:
        if (int(m.group(1), 16), int(m.group(2), 16)) != (area, room):
            raise RuntimeError(f"captured room {m.group(1)}_{m.group(2)}, not {area:02x}_{room:02x}")
        cam = (int(m.group(3)), int(m.group(4)))
    return np.asarray(Image.open(out).convert("RGB")).astype(np.int16), cam


def learn_curve(caps, comp):
    """The port's colour correction, learned: per 8-bit value the tile art
    has (a GBA 5-bit channel, times 8), the value the screen shows it as --
    the most common one where they meet. Returns a 256-entry table from
    screen value back to the tile art's."""
    votes = {}
    for c, (x, y) in caps:
        ref = comp[y:y + c.shape[0], x:x + c.shape[1]]
        ok = ~hud_mask(c.shape)
        for ch in range(3):
            a = ref[..., ch][ok].ravel()
            b = c[..., ch][ok].ravel()
            for v in np.unique(a):
                vals, cnt = np.unique(b[a == v], return_counts=True)
                votes.setdefault(int(v), {})
                for w, n in zip(vals.tolist(), cnt.tolist()):
                    votes[int(v)][w] = votes[int(v)].get(w, 0) + n
    fwd = {v: max(d, key=d.get) for v, d in votes.items() if d}
    if not fwd:
        return np.arange(256)
    src = np.array(sorted(fwd))
    dst = np.array([fwd[v] for v in src])
    order = np.argsort(dst)
    back = np.interp(np.arange(256), dst[order], src[order])
    # snap to the art's own values
    return src[np.abs(back[:, None] - src[None, :]).argmin(axis=1)]


def to_art_colours(c, lut):
    return lut[np.clip(c, 0, 255)].astype(np.int16)


def hud_mask(shape=None):
    """The HUD's places on a screen of this shape (height, width): the
    hearts at the left, the buttons and rupees at the right -- a
    widescreen port puts them at its own right edge."""
    h, w = shape[:2] if shape is not None else (SCREEN[1], SCREEN[0])
    m = np.zeros((h, w), bool)
    for x0, y0, x1, y1 in HUD:
        if x0 < 0 or (x1 <= 0 and x0 < 0):
            x0, x1 = w + x0, w + x1
        m[y0:y1, x0:x1] = True
    return m


def place(cap, comp, guess):
    """Where on the room a capture lies: the offset near the guess where it
    matches the room's tile art best (sprites and HUD are a small share)."""
    H, W = comp.shape[:2]
    sh, sw = cap.shape[:2]
    ok = np.ones((sh, sw), bool) if CLEAN else ~hud_mask(cap.shape)
    best, at = None, guess
    gx, gy = guess
    for dy in range(-ALIGN_REACH, ALIGN_REACH + 1):
        for dx in range(-ALIGN_REACH, ALIGN_REACH + 1):
            x, y = gx + dx, gy + dy
            if x < 0 or y < 0 or x + sw > W or y + sh > H:
                continue
            d = (np.abs(cap - comp[y:y + sh, x:x + sw]).sum(axis=2) > 24) & ok
            s = int(d.sum())
            if best is None or s < best:
                best, at = s, (x, y)
    place.match = 1.0 - (best or 0) / max(1, int(ok.sum()))
    return at


def camera_guess(lx, ly, W, H, screen=SCREEN):
    """Where the game's camera sits for Link at (lx, ly), room pixels."""
    sw, sh = screen
    cx = min(max(lx - sw // 2, 0), max(0, W - sw))
    cy = min(max(ly - sh // 2, 0), max(0, H - sh))
    return cx, cy


def standing_spots(r, cls):
    """Pairs of places for Link, room pixels: one pair per screen of room,
    the two far enough apart that neither hides what the other does."""
    W, H = r.cells_w * 16, r.cells_h * 16
    walk = cls == RE.CLASS_GROUND
    ys, xs = np.nonzero(walk)
    if not len(ys):
        return []
    pts = np.stack([xs * 16 + 8, ys * 16 + 12], 1)
    def starts(n, s):
        """Screen origins covering n pixels, s at a time, the last flush."""
        if n <= s:
            return [0]
        out = list(range(0, n - s, s - 32))
        return out + [n - s]
    views = []
    for vy in starts(H, SCREEN[1]):
        for vx in starts(W, SCREEN[0]):
            cx, cy = vx + SCREEN[0] // 2, vy + SCREEN[1] // 2
            inside = pts[(np.abs(pts[:, 0] - cx) < SCREEN[0] // 2 - 24)
                         & (np.abs(pts[:, 1] - cy) < SCREEN[1] // 2 - 24)]
            if len(inside) < 2:
                continue
            d = np.hypot(inside[:, 0] - cx, inside[:, 1] - cy)
            a = inside[int(np.argmin(d))]
            far = np.hypot(inside[:, 0] - a[0], inside[:, 1] - a[1])
            b = inside[int(np.argmax(far))]
            views.append(((int(a[0]), int(a[1])), (int(b[0]), int(b[1]))))
    return views


def room_capture(dumps, rid, game, out):
    import tilevox as TV
    f = Path(dumps) / f"room_{rid}.tmcr"
    r = RE.load_room(f)
    cls, _H, _bl, _fl, _d = TV.room_heights(r, 0, path=str(f))
    a0, comp = layers(r)
    H, W = comp.shape[:2]
    shown = np.full((H, W, 3), -1, np.int16)
    have = np.zeros((H, W), bool)
    log = []
    lut = None
    with tempfile.TemporaryDirectory() as td:
        for k, (p, q) in enumerate(standing_spots(r, cls)):
            caps = []
            for j, (lx, ly) in enumerate((p,) if CLEAN else (p, q)):
                try:
                    c, cam = capture(game, r.area, r.room, lx, ly, Path(td) / f"c{k}_{j}.png")
                except RuntimeError as e:
                    print(f"[capture] {rid}: {e}", file=sys.stderr)
                    continue
                scr = (min(c.shape[1], W), min(c.shape[0], H))
                c = c[:scr[1], :scr[0]]
                g = cam if cam is not None else camera_guess(lx, ly, W, H, scr)
                g = (min(max(g[0], 0), W - scr[0]), min(max(g[1], 0), H - scr[1]))
                if lut is None:
                    ref = comp[g[1]:g[1] + scr[1], g[0]:g[0] + scr[0]]
                    same = (c == ref).all(axis=2)[~hud_mask(c.shape)].mean()
                    # the port's GBA-LCD colour correction off (config.json
                    # "color_correction": false), the screen is the art's own
                    # colours; else learn the correction back
                    lut = np.arange(256) if same >= 0.5 else learn_curve([(c, g)], comp)
                c = to_art_colours(c, lut)
                at = place(c, comp, g)
                log.append(dict(link=[lx, ly], at=list(at), match=round(place.match, 3)))
                if place.match < MATCH_MIN:
                    # not this room as its tiles draw it: a story scene took
                    # over, or the warp went elsewhere
                    print(f"[capture] {rid}: view at ({lx}, {ly}) matches {place.match:.0%}, dropped",
                          file=sys.stderr)
                    continue
                caps.append((c, at, (lx, ly)))
            if len(caps) < (1 if CLEAN else 2):
                continue
            for i, (c, (x, y), (lx, ly)) in enumerate(caps):
                if CLEAN:                       # the game drew neither
                    sl = (slice(y, y + c.shape[0]), slice(x, x + c.shape[1]))
                    new = ~have[sl]
                    shown[sl][new] = c[new]
                    have[sl] |= new
                    continue
                keep = ~hud_mask(c.shape)
                # Link: his box, where the other capture (him elsewhere)
                # differs -- that one shows what is behind him
                bx0, by0 = lx - x + LINK_BOX[0], ly - y + LINK_BOX[1]
                bx1, by1 = lx - x + LINK_BOX[2], ly - y + LINK_BOX[3]
                box = np.zeros_like(keep)
                box[max(0, by0):max(0, by1), max(0, bx0):max(0, bx1)] = True
                keep &= ~box
                sl = (slice(y, y + c.shape[0]), slice(x, x + c.shape[1]))
                new = keep & ~have[sl]
                shown[sl][new] = c[new]
                have[sl] |= new
    # anything no capture saw (under the HUD in every view): the tile art
    shown[~have] = comp[~have]
    # passing shade (the overworld's drifting cloud shadows): a layer
    # blended over the ground, out = s * art + t per channel -- not a thing
    # in the room, not part of its picture
    shown[passing_shade(shown, comp)] = comp[passing_shade.mask]
    sprite = (np.abs(shown - comp).sum(axis=2) > 24) & (np.abs(shown - a0).sum(axis=2) > 24)
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(shown.astype(np.uint8)).save(out / f"room_{rid}_shown.png")
    Image.fromarray((sprite * 255).astype(np.uint8)).save(out / f"room_{rid}_sprite.png")
    (out / f"room_{rid}_capture.json").write_text(json.dumps(dict(views=log), indent=1))
    return shown, sprite


def load_shown(rid, out=OUT):
    """(shown RGB, sprite mask) for a captured room, or None."""
    p = Path(out) / f"room_{rid}_shown.png"
    if not p.is_file():
        return None
    shown = np.asarray(Image.open(p).convert("RGB"))
    sp = Path(out) / f"room_{rid}_sprite.png"
    sprite = np.asarray(Image.open(sp)) > 0 if sp.is_file() else np.zeros(shown.shape[:2], bool)
    return shown, sprite


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("room", help="AA_RR")
    ap.add_argument("--game", default=os.environ.get("TMC_GAME_DIR", "dist/USA"))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    shown, sprite = room_capture(a.dumps, a.room, a.game, Path(a.out))
    print(f"room {a.room}: {int(sprite.sum())} sprite pixels -> {a.out}")


if __name__ == "__main__":
    main()
