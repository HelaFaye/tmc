#!/usr/bin/env python3
"""
make_test_room.py — synthesise .tmcr room snapshots for testing.

The terrain pipeline should be testable without launching the game. This
writes v4 .tmcr files matching port/vr/vr_world.c's writer exactly, with
terrain you choose, so a tool's behaviour can be checked against a KNOWN
answer instead of against whatever the game happened to produce.

That matters because the whole Phase 0 sequence was tools agreeing with
themselves. A check that cannot fail on purpose has not been tested.

Usage
-----
  python3 make_test_room.py out/ --kind varied --count 8
  python3 make_test_room.py out/ --kind identical --count 8   # distinct must flag
  python3 make_test_room.py out/ --kind uniform  --count 4    # single-class
"""

import argparse
import struct
from pathlib import Path

import numpy as np

MAP_DIM, TILESET = 64, 2048
MAP_CELLS = MAP_DIM * MAP_DIM

# Collision values the shipped classifier maps to distinct classes. Kept here
# so the fixtures exercise real table entries rather than invented ones.
COLL_GROUND, COLL_WALL, COLL_WATER, COLL_HOLE = 0x00, 0x0F, 0x30, 0x21


def build_room(area, room, seed, kind, cells_w=32, cells_h=24,
               origin_x=0, origin_y=0):
    rng = np.random.default_rng(seed)
    coll = np.full((MAP_DIM, MAP_DIM), COLL_GROUND, np.uint8)

    if kind == "uniform":
        pass                                        # all ground, one class
    elif kind == "unknown":
        # Collision values deliberately absent from CLASS_BY_COLLISION, so the
        # fallback has to swallow them. Proves the provenance report fires
        # rather than assuming it would.
        coll[:cells_h, :cells_w] = rng.choice(
            [COLL_GROUND, 0x77, 0x78, 0x79],
            size=(cells_h, cells_w), p=[0.4, 0.3, 0.2, 0.1]).astype(np.uint8)
    elif kind == "identical":
        coll[4:10, 4:20] = COLL_WALL                # same every time: no seed
        coll[14:18, 6:12] = COLL_WATER
    else:                                           # varied
        coll[:cells_h, :cells_w] = rng.choice(
            [COLL_GROUND, COLL_WALL, COLL_WATER, COLL_HOLE],
            size=(cells_h, cells_w), p=[0.6, 0.25, 0.1, 0.05]).astype(np.uint8)

    parts = [b"TMCR", struct.pack("<I", 4), bytes([area, room]),
             struct.pack("<6H", origin_x, origin_y,
                         cells_w * 16, cells_h * 16,
                         cells_w, cells_h),
             bytes([1, 0])]

    for li in range(2):
        # Non-zero tile indices matter: classify_cell treats tile==0 AND
        # collision==0 as an empty cell, so a fixture left at zero reads as
        # void no matter what collision says.
        tile = np.zeros(MAP_CELLS, "<u2")
        if li == 0:
            tile[:] = 1
        c = coll if li == 0 else np.zeros((MAP_DIM, MAP_DIM), np.uint8)
        parts += [tile.tobytes(), c.tobytes(),
                  np.zeros(MAP_CELLS, np.uint8).tobytes(),
                  np.zeros(TILESET, "<u2").tobytes(),
                  np.zeros(TILESET * 4, "<u2").tobytes(),
                  struct.pack("<H", 0)]

    parts.append(np.zeros(0x4000 * 2, "<u2").tobytes())      # both subtilemaps
    parts.append(struct.pack("<H", 1))                        # one entity: Link
    parts.append(bytes([1, 0, 0, 0, 0, 0]) +
                 struct.pack("<iii", 100 << 16, 80 << 16, 0))
    parts.append(np.zeros(256, "<u2").tobytes())              # bg palette
    parts.append(struct.pack("<I", 0x10000))
    parts.append(np.zeros(0x10000, np.uint8).tobytes())       # bg vram
    parts.append(np.zeros(256, "<u2").tobytes())              # v4 src palette
    parts.append(bytes([0]))                                  # fade inactive
    return b"".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--kind", choices=("varied", "identical", "uniform", "unknown", "mosaic", "overlap"),
                    default="varied")
    ap.add_argument("--count", type=int, default=8)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.kind in ("mosaic", "overlap"):
        # A 2x2 layout with KNOWN origins, so the stitcher can be checked
        # against an answer rather than against its own output. "overlap"
        # shifts the tiles so they collide, which must be detected.
        cw, ch = 16, 12
        step_x = cw * 16 if a.kind == "mosaic" else cw * 8
        step_y = ch * 16 if a.kind == "mosaic" else ch * 8
        n = 0
        for gy in range(2):
            for gx in range(2):
                (out / f"room_00_{n:02d}.tmcr").write_bytes(
                    build_room(0, n, seed=n, kind="varied",
                               cells_w=cw, cells_h=ch,
                               origin_x=gx * step_x, origin_y=gy * step_y))
                n += 1
        print(f"wrote {n} '{a.kind}' rooms ({cw}x{ch} cells, "
              f"step {step_x}x{step_y}px) -> {out}")
        return
    for i in range(a.count):
        p = out / f"room_00_{i:02d}.tmcr"
        p.write_bytes(build_room(0, i, seed=i, kind=a.kind))
    print(f"wrote {a.count} '{a.kind}' rooms -> {out}")


if __name__ == "__main__":
    main()
