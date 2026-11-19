#!/usr/bin/env python3
"""Validate and summarise a rom_touch_sweep.sh output directory.

usage: rom_touch_report.py <outdir>

A run counts only when the process exited cleanly, the post-intro state was
seeded, the completed ROM-touch summary matches the touch log, and the harness
settled in the requested area/room. Cleanly completed but unreachable or
redirected destinations are reported and excluded; incomplete/corrupt runs
are reported as fatal validation errors.
"""
import os
import sys
import statistics

from rom_touch_validation import discover_tags, validate_run

PAGE = 4096


def main(out_dir):
    if not os.path.isdir(out_dir):
        print("not a directory: %s" % out_dir, file=sys.stderr)
        return 2

    tags = discover_tags(out_dir)
    if not tags:
        print("no run results in", out_dir)
        return 1

    rooms = []
    union = set()
    load_union = set()
    rejected = []
    fatal_count = 0

    for tag in tags:
        result, reason, fatal = validate_run(out_dir, tag)
        if reason:
            rejected.append((tag, reason, fatal))
            fatal_count += int(fatal)
            continue
        rooms.append((tag, len(result["play"])))
        union.update(result["play"])
        load_union.update(result["load"])

    if rooms:
        sizes = sorted(count for _, count in rooms)
        median_pages = statistics.median(sizes)
        print("rooms validated: %d" % len(rooms))
        print("play pages per room: min %d, median %g, max %d (max %.0f KiB)" % (
            sizes[0], median_pages, sizes[-1], sizes[-1] * PAGE / 1024))
        print("play pages, union of validated rooms: %d (%.2f MiB)" % (
            len(union), len(union) * PAGE / 2**20))
        print("load-phase pages, union: %d (%.2f MiB)" % (
            len(load_union), len(load_union) * PAGE / 2**20))
        print("largest rooms:")
        for tag, count in sorted(rooms, key=lambda item: (-item[1], item[0]))[:8]:
            print("  %s: %d pages (%.0f KiB)" % (
                tag, count, count * PAGE / 1024))
    else:
        print("no validated rooms")

    if rejected:
        print("rejected runs: %d" % len(rejected))
        for tag, reason, fatal in rejected:
            kind = "fatal" if fatal else "destination"
            print("  %s [%s]: %s" % (tag, kind, reason))

    if not rooms or fatal_count:
        print("validation failed: %d fatal run error(s)" % fatal_count,
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: rom_touch_report.py <outdir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
