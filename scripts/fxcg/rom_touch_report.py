#!/usr/bin/env python3
"""Summarise a rom_touch_sweep.sh run: per-room and combined ROM working sets.

usage: rom_touch_report.py <outdir>

A room counts only if its run reached the target (the harness logs
"warp -> 1"). Pages are 4 KB of the 16 MB ROM.
"""
import glob
import os
import re
import sys

PAGE = 4096

def main(out):
    rooms = []
    union = set()
    load_union = set()
    for path in sorted(glob.glob(os.path.join(out, "touch-*.txt"))):
        tag = os.path.basename(path)[len("touch-"):-len(".txt")]
        log = os.path.join(out, "log-%s.txt" % tag)
        try:
            text = open(log, errors="replace").read()
        except OSError:
            continue
        if not re.search(r"warp -> 1", text):
            continue
        play, load = set(), set()
        for line in open(path):
            phase, page, _frame = line.split()
            (play if phase == "play" else load).add(int(page))
        rooms.append((tag, len(play)))
        union |= play
        load_union |= load
    if not rooms:
        print("no completed runs in", out)
        return 1
    sizes = sorted(n for _, n in rooms)
    print("rooms reached: %d" % len(rooms))
    print("play pages per room: min %d, median %d, max %d (max %.0f KB)" % (
        sizes[0], sizes[len(sizes) // 2], sizes[-1], sizes[-1] * PAGE / 1024))
    print("play pages, union of all rooms: %d (%.2f MB)" % (len(union), len(union) * PAGE / 2**20))
    print("load-phase pages, union: %d (%.2f MB)" % (len(load_union), len(load_union) * PAGE / 2**20))
    print("largest rooms:")
    for tag, n in sorted(rooms, key=lambda r: -r[1])[:8]:
        print("  %s: %d pages (%.0f KB)" % (tag, n, n * PAGE / 1024))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
