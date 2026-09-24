#!/usr/bin/env python3
"""
harvest_rooms.py — walk every room in the game headlessly and dump its world
snapshot, using Picori's existing roomcap warp harness.

Why this and not a TAS: roomcap boots a fresh save, warps straight to a target
(area, room), waits for the load to settle, and exits. No save file, no route,
no desync, no input replay. One process per room, fully deterministic, and it
reaches rooms a playthrough would never visit in one run (unused rooms,
post-game states, both Ezlo-cutscene variants).

The room table is generated from include/roomid.h and include/area.h, so it
tracks the decomp rather than being hardcoded here.

Usage
-----
  python3 tools/harvest_rooms.py --list                    # build the room table
  python3 tools/harvest_rooms.py --out vrdump --jobs 8     # harvest everything
  python3 tools/harvest_rooms.py --out vrdump --area 2     # just Hyrule Town

Run from the repository root. Requires a built dist/<REGION>/tmc_pc and the
roomcap TMCR hook (add-roomcap-tmcr.patch).
"""

import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def _find_repo():
    """The decomp root, which is NOT simply this file's grandparent.

    The kit is normally unzipped as <repo>/kit/, so parent.parent lands on
    <repo>/kit and include/area.h is not there. Walk up for the headers
    instead, then fall back to the working directory.
    """
    here = Path(__file__).resolve()
    for p in list(here.parents):
        if (p / "include" / "area.h").exists():
            return p
    for p in [Path.cwd()] + list(Path.cwd().parents):
        if (p / "include" / "area.h").exists():
            return p
    return here.parent.parent


REPO = _find_repo()


# ---------------------------------------------------------------------------
# Room table, parsed from the decomp headers
# ---------------------------------------------------------------------------

def build_room_table():
    """Return [(area_id, room_id, area_name, room_name), ...] for all 842 rooms."""
    area_ids, n = {}, 0
    for line in (REPO / "include" / "area.h").read_text().splitlines():
        m = re.match(r"\s*(AREA_\w+)\s*(=\s*(\w+))?\s*,", line)
        if m:
            if m.group(3) is not None:
                n = int(m.group(3), 0)
            area_ids[m.group(1)] = n
            n += 1

    rooms, cur, k = [], None, 0
    for line in (REPO / "include" / "roomid.h").read_text().splitlines():
        m = re.match(r"\s*//\s*(AREA_\w+)", line)
        if m:
            cur, k = m.group(1), 0
            continue
        m = re.match(r"\s*(ROOM_\w+)\s*(=\s*(\w+))?\s*,?\s*$", line)
        if m and cur:
            if m.group(3) is not None:
                k = int(m.group(3), 0)
            if cur in area_ids:
                rooms.append((area_ids[cur], k, cur, m.group(1)))
            k += 1
    return rooms


# ---------------------------------------------------------------------------
# One room
# ---------------------------------------------------------------------------

def capture(binary, workdir, outdir, area, room, settle, timeout,
            args_x=0x80, args_y=0xb0, entsheet=None):
    """Warp to (area, room) in a fresh process and dump what was asked for.

    With `entsheet`, the run writes one PNG per live entity and exits instead
    of writing a .tmcr. Sprites never appear in room art -- they live in OBJ
    VRAM -- so no amount of searching the tilemap dumps will find an object
    that is drawn as an entity. The only way to see them all is to visit every
    room and ask it what is in it.
    """
    env = dict(os.environ)
    env.update({
        "TMC_AUTOPLAY": "1",
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        "TMC_ROOMCAP": "1",
        # x,y must be INSIDE the room. 0,0 puts Link on a border, and by the
        # settle frame the engine has transitioned him into a neighbouring
        # area — the first run asked for area 2 and landed in 0x15. 0x80,0xb0
        # is the value Picori's own kWarpSpawnOverrides table reaches for, and
        # Port_DebugAction_ArmWarpNudge() spirals to a walkable tile from
        # there. layer 1 = LAYER_BOTTOM (port_debug_actions.c:307).
        "TMC_ROOMCAP_WARP": f"{area},{room},{args_x},{args_y},1",
        "TMC_ROOMCAP_SETTLE": str(settle),
    })
    if entsheet:
        env["TMC_ROOMCAP_ENTSHEET"] = str(Path(entsheet).resolve())
    else:
        env["TMC_ROOMCAP_TMCR"] = str(outdir.resolve())
    try:
        p = subprocess.run([str(binary)], cwd=str(workdir), env=env,
                           capture_output=True, timeout=timeout)
        return p.returncode, p.stderr.decode("utf-8", "replace")[-400:]
    except subprocess.TimeoutExpired:
        return -1, "timeout"


# v1 is exactly this; v2 is larger and variable (entity count differs), so we
# check "at least v1 size" rather than equality.
VALID_BYTES = 40984


# Some areas legitimately redirect on a fresh save, because the game state a
# cold boot implies is not the state in which that area exists. These are not
# failures — the geometry is real and gets captured under the id the engine
# actually loads, which the harvester visits as its own table entry.
KNOWN_REDIRECTS = {
    0x02: (0x15, "Hyrule Town -> Festival Town: a fresh save is during the "
                 "Picori Festival, so the town's festival variant is what "
                 "loads. Same map, different area id. Captured as area 21."),
    0x19: (0x0b, "Hylia Dig Caves -> Lake Hylia: the cave entrances are part "
                 "of the parent overworld area until dug open."),
    0x49: (0x00, "Deepwood Boss -> Minish Woods: the boss arena needs dungeon "
                 "progress flags to exist."),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default="dist/USA/tmc_pc")
    ap.add_argument("--out", default="vrdump")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--spawn", default="0x80,0xb0",
                    help="warp x,y inside the room (default 0x80,0xb0)")
    ap.add_argument("--settle", type=int, default=300,
                    help="frames to wait after the warp (default 300)")
    ap.add_argument("--timeout", type=int, default=120, help="seconds per room")
    ap.add_argument("--area", type=int, action="append",
                    help="restrict to these area ids (repeatable)")
    ap.add_argument("--list", action="store_true", help="print the table and exit")
    ap.add_argument("--redo", action="store_true",
                    help="recapture rooms that already have a valid .tmcr")
    ap.add_argument("--entsheet", action="store_true",
                    help="dump every live entity as a PNG per room instead of "
                         "capturing .tmcr geometry")
    args = ap.parse_args()

    sx, sy = (int(v, 0) for v in args.spawn.split(","))
    rooms = build_room_table()
    if args.area:
        rooms = [r for r in rooms if r[0] in set(args.area)]

    if args.list:
        for a, r, an, rn in rooms:
            print(f"{a:3d} {r:3d}  {an:<34} {rn}")
        print(f"\n{len(rooms)} rooms")
        return

    binary = (REPO / args.binary).resolve()
    if not binary.exists():
        sys.exit(f"binary not found: {binary}")
    workdir = binary.parent          # tmc_pc resolves the ROM next to itself
    outdir = (REPO / args.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    todo = []
    for a, r, an, rn in rooms:
        if args.entsheet:
            d = outdir / f"a{a:02d}_r{r:02d}"
            if not args.redo and d.is_dir() and any(d.glob("*.png")):
                continue
        else:
            f = outdir / f"room_{a:02d}_{r:02d}.tmcr"
            if not args.redo and f.exists() and f.stat().st_size == VALID_BYTES:
                continue
        todo.append((a, r, an, rn))

    print(f"{len(rooms)} rooms in table, {len(todo)} to capture, "
          f"{args.jobs} parallel, settle={args.settle}")

    ok = bad = redirected = 0
    results = []

    def work(item):
        a, r, an, rn = item
        es = (outdir / f"a{a:02d}_r{r:02d}") if args.entsheet else None
        rc, err = capture(binary, workdir, outdir, a, r, args.settle,
                          args.timeout, sx, sy, es)
        if args.entsheet:
            # A room with no entities is a valid outcome, not a failure, so
            # "the process finished" is the bar here rather than "files exist".
            good = rc in (0, 6)
        else:
            f = outdir / f"room_{a:02d}_{r:02d}.tmcr"
            good = f.exists() and f.stat().st_size >= VALID_BYTES
        return a, r, an, rn, rc, good, err

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, (a, r, an, rn, rc, good, err) in enumerate(ex.map(work, todo), 1):
            if good:
                ok += 1
            elif rc == 6 and a in KNOWN_REDIRECTS:
                redirected += 1
            else:
                bad += 1
                results.append((a, r, rn, rc, err.strip().splitlines()[-1:] or [""]))
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] ok={ok} redirected={redirected} "
                      f"failed={bad}", flush=True)

    print(f"\ncaptured {ok}, redirected {redirected}, failed {bad} -> {outdir}")
    if redirected:
        print("\nredirected (expected, not failures):")
        for src, (dst, why) in KNOWN_REDIRECTS.items():
            print(f"  area 0x{src:02x} -> 0x{dst:02x}: {why}")
    if results:
        print("\nfailures (room id, exit code, last stderr line):")
        for a, r, rn, rc, tail in results[:40]:
            print(f"  {a:3d},{r:<3d} rc={rc:<4} {rn:<40} {tail[0][:70] if tail else ''}")
        if len(results) > 40:
            print(f"  ... and {len(results) - 40} more")
        print("\nExit codes: 6 = warp landed in the wrong room (try --spawn),\n"
              "3 = roomcap timeout, -1 = process timeout. Some rooms genuinely\n"
              "cannot be warped to cold: AREA_NULL_* slots are rejected outright\n"
              "by Port_DebugAction_AreaIsWarpable, and cutscene-only states need\n"
              "save flags. A failure here is information, not necessarily a bug.")


if __name__ == "__main__":
    main()
