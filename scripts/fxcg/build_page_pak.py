#!/usr/bin/env python3
"""Build the page pak the fx-CG50 port streams from the ESP32 (see
docs/fx-cg50-port-plan.md, "Preloading over the ESP32 link").

usage: build_page_pak.py <baserom.gba> <out.pak> <touch-dir> [<touch-dir> ...]

The touch directories come from rom_touch_sweep.sh. Every ROM page any room
touched during play is stored once, deflate-compressed on its own, so the
calculator can cache and fetch single pages. Pages every room touches form
the resident baseline. Each room gets a manifest: the pages it needs.

Layout (all integers little-endian):
  header      magic "PCPK", u16 version=1, u8 codec (1 = raw deflate),
              u8 page_shift (12), u32 rom_size, u32 page_count,
              u32 room_count, u32 baseline_count, u32 manifest_entries
  page table  page_count x { u16 rom_page, u16 stored_size, u32 data_offset }
              sorted by rom_page; data_offset is from the start of the data
              blob; stored_size == 4096 means stored uncompressed
  baseline    baseline_count x u16 index into the page table
  rooms       room_count x { u8 area, u8 room, u16 page_count, u32 first }
              first indexes the manifest list
  manifests   manifest_entries x u16 index into the page table
  data        the compressed pages
A room is listed only if its run reached it (the harness logged "warp -> 1").
The pak holds ROM data: build it from your own ROM and do not distribute it.
"""
import glob
import hashlib
import os
import re
import struct
import sys
import zlib

PAGE_SHIFT = 12
PAGE = 1 << PAGE_SHIFT
USA_SHA1 = "b4bd50e4131b027c334547b4524e2dbbd4227130"


def deflate(data):
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    return compressor.compress(data) + compressor.flush()


def load_rooms(directories):
    rooms = {}
    for directory in directories:
        for path in glob.glob(os.path.join(directory, "touch-*.txt")):
            tag = os.path.basename(path)[len("touch-"):-len(".txt")]
            log = os.path.join(directory, "log-%s.txt" % tag)
            try:
                if "warp -> 1" not in open(log, errors="replace").read():
                    continue
            except OSError:
                continue
            area, room = (int(part, 16) for part in tag.split("-"))
            pages = {int(line.split()[1]) for line in open(path) if line.startswith("play ")}
            rooms[(area, room)] = pages
    return rooms


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    rom = open(argv[1], "rb").read()
    if hashlib.sha1(rom).hexdigest() != USA_SHA1:
        print("warning: ROM is not the USA release tmc.sha1 names", file=sys.stderr)
    rooms = load_rooms(argv[3:])
    if not rooms:
        print("no completed room runs found", file=sys.stderr)
        return 1

    all_pages = sorted(set().union(*rooms.values()))
    baseline = set.intersection(*rooms.values())
    index_of = {page: i for i, page in enumerate(all_pages)}

    blobs = []
    for page in all_pages:
        raw = rom[page * PAGE:(page + 1) * PAGE]
        packed = deflate(raw)
        blobs.append(packed if len(packed) < PAGE else raw)

    keys = sorted(rooms)
    manifests = []
    room_table = []
    for key in keys:
        pages = sorted(rooms[key] - baseline)
        room_table.append((key[0], key[1], len(pages), len(manifests)))
        manifests.extend(index_of[page] for page in pages)

    out = bytearray()
    out += struct.pack("<4sHBBIIIII", b"PCPK", 1, 1, PAGE_SHIFT, len(rom),
                       len(all_pages), len(keys), len(baseline), len(manifests))
    offset = 0
    for page, blob in zip(all_pages, blobs):
        out += struct.pack("<HHI", page, len(blob), offset)
        offset += len(blob)
    out += b"".join(struct.pack("<H", index_of[p]) for p in sorted(baseline))
    for area, room, count, first in room_table:
        out += struct.pack("<BBHI", area, room, count, first)
    out += b"".join(struct.pack("<H", i) for i in manifests)
    header_size = len(out)
    out += b"".join(blobs)
    open(argv[2], "wb").write(out)

    # Round trip: every stored page must decode to the ROM's bytes.
    data = out[header_size:]
    for i, page in enumerate(all_pages):
        _, size, start = struct.unpack_from("<HHI", out, 28 + 8 * i)
        blob = bytes(data[start:start + size])
        decoded = blob if size == PAGE else zlib.decompressobj(-15).decompress(blob)
        assert decoded == rom[page * PAGE:(page + 1) * PAGE], "page %d" % page

    raw_total = len(all_pages) * PAGE
    per_room = sorted(sum(len(blobs[index_of[p]]) for p in rooms[k] - baseline) for k in keys)
    baseline_bytes = sum(len(blobs[index_of[p]]) for p in baseline)
    print("rooms %d, pages %d (%.2f MB raw) -> pak %.2f MB (%.0f%%), index %d B" % (
        len(keys), len(all_pages), raw_total / 2**20, len(out) / 2**20,
        100 * (len(out) - header_size) / raw_total, header_size))
    print("baseline %d pages, %.0f KB compressed" % (len(baseline), baseline_bytes / 1024))
    print("per-room compressed: median %.0f KB, p90 %.0f KB, max %.0f KB" % (
        per_room[len(per_room) // 2] / 1024, per_room[int(len(per_room) * 0.9)] / 1024,
        per_room[-1] / 1024))
    for baud in (115200, 460800, 921600):
        rate = baud / 10.0  # 8N1: ten bits per byte, before protocol overhead
        print("  at %6d baud: median room %.2f s, p90 %.2f s, max %.2f s, whole pak %.0f s" % (
            baud, per_room[len(per_room) // 2] / rate, per_room[int(len(per_room) * 0.9)] / rate,
            per_room[-1] / rate, len(out) / rate))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
