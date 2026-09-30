#!/usr/bin/env python3
"""room_capture.py -- rooms as the running game shows them, sprites and all.

The room dumps hold the tile layers; what the game draws with sprites --
a doorway's arch, a shelf of plates, a water barrel -- is not in them.
This drives the port's room-capture harness (port/port_repro_roomcap.c)
on a tour: one game session warps Link through every view of every room
asked for, capturing each (as the voxel tour in port_repro_npc_talk.c
walks every room). For each room it builds:

  shown   the room as the game shows it: tile layers and object sprites,
          without the HUD, Link, or the overworld's passing cloud shadows
  sprite  where that differs from the room's own tile layers: what the
          sprites draw

The harness hides the HUD with the game's own flags (gHUD.hideFlags =
HUD_HIDE_ALL), stops drawing Link, advances any dialogue that takes
control, and logs where its camera was for each capture; each capture
is placed there, and dropped if it is not this room as its tiles draw it
(a story scene took over, an exit was stepped on).

Usage:
  room_capture.py DUMPS ROOMS [--game DIR] [--out DIR] [--settle N]
    ROOMS: AA_RR[,AA_RR...], or AA (a whole area), or all
    writes DIR/room_AA_RR_shown.png, _sprite.png and _capture.json
    (default vr/captures/, ignored: your ROM's art, keep it local)

The game directory holds a built tmc_pc (port_repro_roomcap.c with its
tour) with baserom.gba and assets (see AGENTS.md); it runs headless. Set
"color_correction": false in its config.json: the screen then shows the
tile art's own colours (with it on, the correction is learned back, less
exactly).
"""
import argparse
import json
import os
import re
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
BINARY = os.environ.get("TMC_BINARY", "tmc_pc")
SCREEN = (240, 160)             # the GBA's; a widescreen port shows more, which
                                # only means views overlap more
SETTLE = 160                    # frames in the room before its capture: the
                                # enter-room text box (enterRoomTextboxManager.c)
                                # shows for 120
PROGRESS = 1                    # dungeons cleared: past the prologue, whose scripts
                                # take over a warp into Hyrule Field or the town
MATCH_MIN = 0.7                 # a capture this much like the room's tile art is it
ART_MIN = 0.05                  # of a view with art, for its match to be judged
GBA_W = 240                     # px: the GBA's screen, left of a widescreen capture
STRIP_MIN = 32                  # px: a widescreen strip this wide is worth keeping
ALIGN = 8                       # px: the logged camera can be this far off the picture
NO_ART = (248, 0, 248)          # the dump's colour where a tile's art never loaded,
                                # and ours for the backdrop no tile layer draws
SECONDS_PER_VIEW = 12           # the tour's time budget, per view
SHADE_SLOPES = np.arange(0.5, 0.97, 1 / 32)   # a blend keeps this share of the art
SHADE_FIT = 12                  # px value: how close a pixel must follow the blend
SHADE_SHARE = 0.25              # of the differing pixels the blend must explain


def layers(r):
    """The room's tile layers as shown: (ground layer, top layer over it).
    Where neither draws (the backdrop: a sky, a void the game fills with
    another layer or a colour), NO_ART."""
    A0 = np.asarray(extract_art.room_art(r, 0))
    a0 = A0[..., :3].astype(np.int16)
    a0[A0[..., 3] == 0] = NO_ART
    comp = a0
    if len(r.layers) > 1 and r.layers[1]["present"]:
        a1 = np.asarray(extract_art.room_art(r, 1))
        top = a1[..., 3] > 0
        comp = a0.copy()
        comp[top] = a1[..., :3][top]
    return a0, comp


def standing_spots(r, cls):
    """Where Link stands for each view, room pixels: walkable ground nearest
    the middle of each screen-sized view, views tiling the room with the
    last flush with its far edges."""
    W, H = r.cells_w * 16, r.cells_h * 16
    ys, xs = np.nonzero(cls == RE.CLASS_GROUND)
    if not len(ys):
        return []
    pts = np.stack([xs * 16 + 8, ys * 16 + 12], 1)

    def starts(n, s):
        if n <= s:
            return [0]
        return list(range(0, n - s, s - 32)) + [n - s]
    spots = []
    for vy in starts(H, SCREEN[1]):
        for vx in starts(W, SCREEN[0]):
            cx, cy = vx + SCREEN[0] // 2, vy + SCREEN[1] // 2
            inside = pts[(np.abs(pts[:, 0] - cx) < SCREEN[0] // 2 - 24)
                         & (np.abs(pts[:, 1] - cy) < SCREEN[1] // 2 - 24)]
            if not len(inside):
                continue
            d = np.hypot(inside[:, 0] - cx, inside[:, 1] - cy)
            a = inside[int(np.argmin(d))]
            spots.append((int(a[0]), int(a[1])))
    return spots


def tour(game, views, outdir, settle=SETTLE):
    """Run the game through every view [(area, room, x, y)]: returns
    {view index: (capture path, camera (x, y), (area, room))}. If the game
    ends before the last view -- a scene that never gave control back, a
    crash -- the tour resumes after the view it stopped at."""
    outdir = Path(outdir)
    plan = outdir / "plan.txt"
    plan.write_text("".join(f"{a:#x} {r:#x} {x} {y} 0\n" for a, r, x, y in views))
    got = {}
    start = 0
    while start < len(views):
        a0, r0, x0, y0 = views[start]
        env = dict(os.environ, TMC_AUTOPLAY="1", TMC_ROOMCAP="1",
                   TMC_ROOMCAP_WARP=f"{a0:#x},{r0:#x},{x0:#x},{y0:#x},0",
                   TMC_ROOMCAP_TOUR=str(plan), TMC_ROOMCAP_TOUR_OUT=str(outdir),
                   TMC_ROOMCAP_TOUR_START=str(start),
                   TMC_ROOMCAP_SETTLE=str(settle), TMC_ROOMCAP_PROGRESS=str(PROGRESS),
                   SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        try:
            p = subprocess.run([f"./{BINARY}", "--no-audio"], cwd=game, env=env,
                               timeout=120 + SECONDS_PER_VIEW * (len(views) - start),
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, errors="replace")
            err = p.stderr
        except subprocess.TimeoutExpired as e:
            err = (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        last = start - 1
        for m in re.finditer(r"\[roomcap\] tour (\d+) view room=0x([0-9a-f]+)/0x([0-9a-f]+) "
                             r"scroll=(-?\d+),(-?\d+) size=\d+,\d+ ok=(\d)", err):
            i = int(m.group(1))
            last = max(last, i)
            if m.group(6) == "1":
                got[i] = (outdir / f"{i:05d}.png", (int(m.group(4)), int(m.group(5))),
                          (int(m.group(2), 16), int(m.group(3), 16)))
        for m in re.finditer(r"\[roomcap\] tour (\d+) (skip|stuck) room=0x([0-9a-f]+)/0x([0-9a-f]+)", err):
            last = max(last, int(m.group(1)))
            print(f"[capture] {int(m.group(3), 16):02d}_{int(m.group(4), 16):02d}: "
                  f"view {m.group(1)} {'never arrived' if m.group(2) == 'skip' else 'stuck'}",
                  file=sys.stderr)
        if "[roomcap] tour done" in err:
            break
        start = last + 1 if last >= start else start + 1
    return got


def match(cap, comp, at):
    """Share of the capture like the tile art under it, where there is art."""
    x, y = at
    ref = comp[y:y + cap.shape[0], x:x + cap.shape[1]]
    art = ~(ref == NO_ART).all(axis=2)
    if art.mean() < ART_MIN:
        return 1.0      # all sky: nothing to judge by; the room is right
    return float((np.abs(cap - ref).sum(axis=2) <= 24)[art].mean())


def align(cap, comp, at):
    """Where the capture sits: the logged camera, or the best match within
    ALIGN px of it (the camera can lag or shake the picture a few px)."""
    H, W = comp.shape[:2]
    h, w = cap.shape[:2]
    best = (match(cap, comp, at), at)
    if best[0] >= 0.98:
        return best[1]
    for dy in range(-ALIGN, ALIGN + 1):
        for dx in range(-ALIGN, ALIGN + 1):
            x, y = at[0] + dx, at[1] + dy
            if (dx or dy) and 0 <= x <= W - w and 0 <= y <= H - h:
                m = match(cap[::2, ::2], comp[y:y + h:2, x:x + w:2], (0, 0))
                if m > best[0]:
                    best = (m, (x, y))
    return best[1]


def learn_curve(c, ref):
    """The port's colour correction, learned back: per screen value, the
    tile art's value most often under it (channels alike)."""
    votes = {}
    for ch in range(3):
        a, b = ref[..., ch].ravel(), c[..., ch].ravel()
        for v in np.unique(b):
            vals, cnt = np.unique(a[b == v], return_counts=True)
            votes.setdefault(int(v), {})
            for w, k in zip(vals.tolist(), cnt.tolist()):
                votes[int(v)][w] = votes[int(v)].get(w, 0) + k
    lut = np.arange(256)
    for v, d in votes.items():
        lut[v] = max(d, key=d.get)
    return lut


def passing_shade(shown, comp):
    """Pixels where the picture is the tile art under a blended layer --
    out = s * art + t, the same s and t (per channel) wherever it passes:
    the overworld's cloud shadows. The blend is learned from the pixels
    that differ, the one that explains most of them; none if it explains
    too few (they are sprites, not shade)."""
    diff = np.abs(shown - comp).sum(axis=2) > 24
    mask = np.zeros(diff.shape, bool)
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
    return diff & (np.abs(shown.astype(float) - (sl * comp + t)) <= SHADE_FIT).all(axis=2)


MOVE_GROW = 2                   # px round what two views disagree on
SPECK = 40                      # px: a sprite shape is at least this big ...
SPECK_WET = 400                 # ... and by the water, where tiles animate, this


def assemble(r, rid, caps, out, cls=None):
    """The room's shown picture and sprite mask from its captures
    [(path, camera, (area, room), spot)]; writes them to out."""
    a0, comp = layers(r)
    H, W = comp.shape[:2]
    shown = np.zeros((H, W, 3), np.int16)
    have = np.zeros((H, W), bool)
    moving = np.zeros((H, W), bool)
    lut = None
    log = []
    for path, cam, room, spot in caps:
        entry = dict(link=list(spot), camera=list(cam), room=f"{room[0]:02x}_{room[1]:02x}")
        log.append(entry)
        if room != (r.area, r.room):
            entry["dropped"] = "another room"
            continue
        c = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
        c = c[:min(c.shape[0], H), :min(c.shape[1], W)]
        at = (min(max(cam[0], 0), W - c.shape[1]), min(max(cam[1], 0), H - c.shape[0]))
        at = align(c, comp, at)
        if list(at) != list(cam):
            entry["placed"] = list(at)
        ref = comp[at[1]:at[1] + c.shape[0], at[0]:at[0] + c.shape[1]]
        # "color_correction": false -- the screen is the art's colours; else
        # learn the correction back, from the first view that is this room
        # (a view under fog or a scene would teach the wrong curve)
        use = lut
        if use is None:
            same = (c == ref).all(axis=2)
            same = max(same.mean(), same[:, GBA_W:].mean() if same.shape[1] > GBA_W else 0)
            use = np.arange(256) if same >= 0.5 else learn_curve(c, ref)
        c = use[np.clip(c, 0, 255)].astype(np.int16)
        entry["match"] = round(match(c, comp, at), 3)
        if lut is None and entry["match"] >= MATCH_MIN:
            lut = use
        if entry["match"] < MATCH_MIN and c.shape[1] >= GBA_W + STRIP_MIN:
            # the screen's own effects (a dark room's light, the Cave of
            # Flames' haze) cover only the GBA's 240 px; the widescreen
            # strip right of it shows the room as it is
            strip = round(match(c[:, GBA_W:], comp, (at[0] + GBA_W, at[1])), 3)
            if strip >= MATCH_MIN:
                entry["strip"] = strip
                c = c[:, GBA_W:]
                at = (at[0] + GBA_W, at[1])
        if entry["match"] < MATCH_MIN and "strip" not in entry:
            entry["dropped"] = "not this room as its tiles draw it"
            print(f"[capture] {rid}: view at {spot} matches {entry['match']:.0%}, dropped",
                  file=sys.stderr)
            continue
        sl = (slice(at[1], at[1] + c.shape[0]), slice(at[0], at[0] + c.shape[1]))
        # where two views (taken at different moments) disagree, something
        # moves: an animated tile, a creature wandering -- not the room's
        moving[sl] |= have[sl] & (np.abs(shown[sl] - c).sum(axis=2) > 24)
        new = ~have[sl]
        shown[sl][new] = c[new]
        have[sl] |= new
    shown[~have] = comp[~have]                  # what no view saw: the tile art
    shown[~have & (comp == NO_ART).all(axis=2)] = 0     # ... or none, as the dump
    # where the dump has no art, the capture is the room: compare it with itself
    noart = have & (comp == NO_ART).all(axis=2)
    comp = comp.copy()
    comp[noart] = shown[noart]
    a0 = a0.copy()
    a0[noart] = shown[noart]
    # what moves (and a pixel or two round it): the tile art
    if moving.any():
        mv = moving.copy()
        for _ in range(MOVE_GROW):
            P = np.pad(mv, 1)
            mv = mv | P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:]
        mv &= np.abs(shown - comp).sum(axis=2) > 24
        shown[mv] = comp[mv]
    shade = passing_shade(shown, comp)
    shown[shade] = comp[shade]
    sprite = (np.abs(shown - comp).sum(axis=2) > 24) & (np.abs(shown - a0).sum(axis=2) > 24)
    # animated tiles (water, foam, waterfalls) caught at another moment than
    # the dump: specks, not shapes -- small anywhere, larger by the water
    if cls is not None:
        wet = np.kron(cls == RE.CLASS_WATER, np.ones((16, 16), bool))[:H, :W]
        P = np.pad(wet, 16)
        near = np.zeros_like(wet)
        for dy in (-16, 0, 16):
            for dx in (-16, 0, 16):
                near |= P[16 + dy:16 + dy + H, 16 + dx:16 + dx + W]
    else:
        near = np.zeros((H, W), bool)
    lab, n = RE._label(sprite)
    if n:
        sizes = np.bincount(lab.ravel())
        wetsz = np.bincount(lab.ravel(), weights=near.ravel().astype(float))
        drop = (sizes < SPECK) | ((wetsz > 0.5 * sizes) & (sizes < SPECK_WET))
        drop[0] = False
        dm = drop[lab]
        shown[dm] = comp[dm]
        sprite &= ~dm
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(shown.astype(np.uint8)).save(out / f"room_{rid}_shown.png")
    Image.fromarray((sprite * 255).astype(np.uint8)).save(out / f"room_{rid}_sprite.png")
    (out / f"room_{rid}_capture.json").write_text(json.dumps(dict(
        seen=round(float(have.mean()), 3), views=log), indent=1))
    return shown, sprite, float(have.mean())


def capture_rooms(dumps, rids, game, out, settle=SETTLE):
    """Capture the rooms in one tour; returns {rid: share of the room seen}."""
    import tilevox as TV
    rooms, views, owner = {}, [], []
    for rid in rids:
        f = Path(dumps) / f"room_{rid}.tmcr"
        r = RE.load_room(f)
        if RE.room_art_rgb(r, 0) is None:
            continue
        cls, _H, _bl, _fl, _d = TV.room_heights(r, 0, path=str(f))
        rooms[rid] = (r, cls)
        for x, y in standing_spots(r, cls):
            views.append((r.area, r.room, x, y))
            owner.append((rid, (x, y)))
    if not views:
        return {}
    with tempfile.TemporaryDirectory() as td:
        got = tour(game, views, td, settle)
        per = {}
        for i, (rid, spot) in enumerate(owner):
            if i in got:
                path, cam, room = got[i]
                per.setdefault(rid, []).append((path, cam, room, spot))
        seen = {}
        for rid, (r, cls) in rooms.items():
            if rid in per:
                seen[rid] = assemble(r, rid, per[rid], Path(out), cls)[2]
    return seen


def load_shown(rid, out=OUT):
    """(shown RGB, sprite mask) for a captured room, or None."""
    p = Path(out) / f"room_{rid}_shown.png"
    if not p.is_file():
        return None
    shown = np.asarray(Image.open(p).convert("RGB"))
    sp = Path(out) / f"room_{rid}_sprite.png"
    sprite = np.asarray(Image.open(sp)) > 0 if sp.is_file() else np.zeros(shown.shape[:2], bool)
    return shown, sprite


def room_ids(dumps, spec):
    have = sorted(f.stem[5:] for f in Path(dumps).glob("room_*.tmcr"))
    if spec == "all":
        return have
    out = []
    for s in spec.split(","):
        out += [h for h in have if h == s or (len(s) <= 3 and h.split("_")[0] == f"{int(s):02d}")]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("rooms", help="AA_RR[,AA_RR...], AA (an area), or all")
    ap.add_argument("--game", default=os.environ.get("TMC_GAME_DIR", "dist/USA"))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--settle", type=int, default=SETTLE)
    a = ap.parse_args()
    rids = room_ids(a.dumps, a.rooms)
    seen = capture_rooms(a.dumps, rids, a.game, a.out, a.settle)
    for rid in rids:
        print(f"room {rid}: " + (f"{seen[rid]:.0%} seen" if rid in seen else "no capture"))


if __name__ == "__main__":
    main()
