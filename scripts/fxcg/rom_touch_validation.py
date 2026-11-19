#!/usr/bin/env python3
"""Shared validation for fx-CG50 ROM-touch sweep runs.

A run is accepted only when the process completed successfully, the intended
post-intro state was seeded, the ROM-touch summary agrees with the touch log,
and the harness settled in the area/room encoded by the run tag.

A cleanly completed run that never arms its warp or settles in another room is
classified as a rejected destination (non-fatal to a multi-room sweep). Missing
or inconsistent evidence and process failures are fatal to packaging.
"""
from __future__ import annotations

import glob
import os
import re

PAGE_SIZE = 4096
MAX_PAGES = 0x1000000 // PAGE_SIZE

TAG_RE = re.compile(
    r"^(?:0[xX])?([0-9a-fA-F]{1,2})-(?:0[xX])?([0-9a-fA-F]{1,2})$"
)
SETTLED_RE = re.compile(
    r"\[roomcap\] settled requested=0x([0-9a-fA-F]{2})/0x([0-9a-fA-F]{2}) "
    r"actual=0x([0-9a-fA-F]{2})/0x([0-9a-fA-F]{2})"
)
TOUCH_SUMMARY_RE = re.compile(
    r"\[ROM touch\] (\d+) load pages, (\d+) play pages over (\d+) frames ->"
)
WARP_ARMED_RE = re.compile(r"\[roomcap\].*warp -> 1")
POSTINTRO_MARKER = "[roomcap] post-intro seed: TABIDACHI=1"


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as stream:
        return stream.read()


def discover_tags(out_dir: str) -> list[str]:
    """Return every run tag represented by any expected run artifact."""
    tags: set[str] = set()
    for prefix in ("status-", "log-", "touch-"):
        for path in glob.glob(os.path.join(out_dir, prefix + "*.txt")):
            name = os.path.basename(path)
            if name.startswith(prefix) and name.endswith(".txt"):
                tags.add(name[len(prefix):-4])
    return sorted(tags)


def validate_run(out_dir: str, tag: str):
    """Validate one run.

    Returns ``(result, reason, fatal)``. ``result`` is a dictionary with
    ``area``, ``room``, ``load``, ``play`` and ``frames`` when accepted.
    ``fatal`` distinguishes corrupt/incomplete runs from expected unreachable
    or redirected destinations, which are safely excluded from the pak.
    """
    match = TAG_RE.fullmatch(tag)
    if not match:
        return None, "invalid run tag", True
    expected_area, expected_room = (int(value, 16) for value in match.groups())

    status_path = os.path.join(out_dir, "status-%s.txt" % tag)
    log_path = os.path.join(out_dir, "log-%s.txt" % tag)
    touch_path = os.path.join(out_dir, "touch-%s.txt" % tag)

    if not os.path.isfile(status_path):
        return None, "missing exit-status file", True
    try:
        status_text = read_text(status_path).strip()
        status = int(status_text, 10)
    except (OSError, ValueError):
        return None, "invalid exit-status file", True
    if status != 0:
        return None, "process exit status %d" % status, True

    if not os.path.isfile(log_path):
        return None, "missing process log", True
    if not os.path.isfile(touch_path):
        return None, "missing ROM-touch log", True

    log = read_text(log_path)
    if POSTINTRO_MARKER not in log:
        return None, "post-intro flag was not seeded", True

    summary = TOUCH_SUMMARY_RE.search(log)
    if not summary:
        return None, "missing completed ROM-touch summary", True
    expected_load_count, expected_play_count, frames = map(int, summary.groups())
    if frames <= 0:
        return None, "ROM-touch recorder reported zero frames", True

    load: set[int] = set()
    play: set[int] = set()
    try:
        with open(touch_path, encoding="ascii") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                fields = line.split()
                if len(fields) != 3 or fields[0] not in ("load", "play"):
                    return None, "malformed touch log line %d" % number, True
                try:
                    page = int(fields[1], 10)
                    frame = int(fields[2], 10)
                except ValueError:
                    return None, "non-numeric touch-log field on line %d" % number, True
                if not 0 <= page < MAX_PAGES or frame < 0:
                    return None, "out-of-range touch-log value on line %d" % number, True
                (load if fields[0] == "load" else play).add(page)
    except OSError as error:
        return None, "cannot read touch log: %s" % error, True

    if len(load) != expected_load_count or len(play) != expected_play_count:
        return None, "touch-log counts disagree with process summary", True
    if not play:
        return None, "no play-phase ROM pages were recorded", True

    # A run may complete normally while a requested destination is not
    # reachable. Such runs are excluded, but do not invalidate other rooms.
    if not WARP_ARMED_RE.search(log):
        return None, "warp did not arm", False

    settled = SETTLED_RE.search(log)
    if not settled:
        return None, "missing settled-destination confirmation", True
    req_area, req_room, actual_area, actual_room = (
        int(value, 16) for value in settled.groups()
    )
    if (req_area, req_room) != (expected_area, expected_room):
        return None, (
            "settled log requested %02x:%02x but run tag is %02x:%02x"
            % (req_area, req_room, expected_area, expected_room)
        ), True
    if (actual_area, actual_room) != (expected_area, expected_room):
        return None, (
            "settled in %02x:%02x, expected %02x:%02x"
            % (actual_area, actual_room, expected_area, expected_room)
        ), False

    return {
        "area": expected_area,
        "room": expected_room,
        "load": load,
        "play": play,
        "frames": frames,
    }, None, False
