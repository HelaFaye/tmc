#!/usr/bin/env python3
"""Build a compact, page-addressable ROM pak for the fx-CG50.

usage: build_page_pak.py <USA-baserom.gba> <out.pak> [--boot=<touch.txt> ...] <touch-dir> [<touch-dir> ...]

--boot adds every page of a boot capture (a ROM-touch log of a run from
power-on through the title screen and file select, recorded with
TMC_ROM_ONLY=1; see docs/fx-cg50-data-prep.md) to the resident baseline.

The calculator target currently links the USA ROM symbol map. This tool
therefore accepts only the exact supported USA ROM image. It stores each
4 KiB page observed by the room-touch sweeps once, compressed independently
with raw DEFLATE (or verbatim when compression would expand it). The output
is a derived personal-use data file; do not commit or distribute it.

PCPK v1 layout (all integers little-endian):
  header      magic "PCPK", u16 version=1, u8 codec=1 (raw DEFLATE),
              u8 page_shift=12, u32 rom_size, u32 page_count,
              u32 room_count, u32 baseline_count, u32 manifest_entries
  page table  page_count x { u16 rom_page, u16 stored_size, u32 data_offset }
  baseline    baseline_count x u16 index into the page table
  rooms       room_count x { u8 area, u8 room, u16 page_count, u32 first }
  manifests   manifest_entries x u16 index into the page table
  data        independent page blobs; stored_size == 4096 means raw bytes

The room-touch logs must come from completed room runs (the harness prints
"warp -> 1" after successfully reaching its target room).
"""
import hashlib
import json
import os
import struct
import sys
import zlib

from rom_touch_validation import discover_tags, validate_run

PAGE_SHIFT = 12
PAGE = 1 << PAGE_SHIFT
ROM_SIZE = 0x1000000
USA_SHA1 = "b4bd50e4131b027c334547b4524e2dbbd4227130"
HEADER_SIZE = 28
PAGE_ENTRY_SIZE = 8


def deflate(data):
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    return compressor.compress(data) + compressor.flush()


def load_rooms(directories):
    """Collect only runs that pass the shared sweep evidence validator.

    Fatal/incomplete captures stop packaging. Expected but unreachable or
    redirected destinations are excluded without contaminating the pak.
    """
    rooms = {}
    load_pages = set()
    for directory in directories:
        for tag in discover_tags(directory):
            result, reason, fatal = validate_run(directory, tag)
            if reason:
                if fatal:
                    raise ValueError("invalid ROM-touch run %s/%s: %s" % (directory, tag, reason))
                continue

            key = (result["area"], result["room"])
            # Repeated captures of the same room are accumulated, not replaced.
            rooms.setdefault(key, set()).update(result["play"])
            load_pages.update(result["load"])

    # Port_LoadRom() still reads ROM-window pages during startup (header,
    # region tables and pointer tables). Until every such path is replaced by
    # generated/baked data, keep the union of measured load pages resident.
    # Adding it to every room makes it part of the PCPK v1 common baseline.
    for pages in rooms.values():
        pages.update(load_pages)
    return rooms, load_pages


def decode_page(blob, stored_size, page_number):
    if stored_size == PAGE:
        decoded = blob
    else:
        decoder = zlib.decompressobj(-15)
        decoded = decoder.decompress(blob) + decoder.flush()
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid DEFLATE stream for ROM page %d" % page_number)
    if len(decoded) != PAGE:
        raise ValueError("wrong decoded length for ROM page %d: %d" % (page_number, len(decoded)))
    return decoded


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2

    rom_path, output_path = argv[1], argv[2]
    with open(rom_path, "rb") as stream:
        rom = stream.read()
    if len(rom) != ROM_SIZE:
        raise SystemExit("unsupported ROM size: expected exactly 16 MiB, got %d bytes" % len(rom))
    rom_sha1 = hashlib.sha1(rom).hexdigest()
    if rom_sha1 != USA_SHA1:
        raise SystemExit("ROM SHA-1 mismatch: this fx-CG50 build's linker symbols target the supported USA ROM; got %s" % rom_sha1)

    boot_files = [a[len("--boot="):] for a in argv[3:] if a.startswith("--boot=")]
    directories = [a for a in argv[3:] if not a.startswith("--boot=")]
    rooms, load_pages = load_rooms(directories)
    if not rooms:
        raise SystemExit("no completed room runs found; run rom_touch_sweep.sh first")
    boot_pages = set()
    for path in boot_files:
        with open(path) as stream:
            for line in stream:
                fields = line.split()
                if len(fields) >= 2 and fields[0] in ("load", "play"):
                    page = int(fields[1])
                    if not 0 <= page < ROM_SIZE // PAGE:
                        raise SystemExit("boot capture %s names page %d" % (path, page))
                    boot_pages.add(page)
        if not boot_pages:
            raise SystemExit("boot capture %s has no ROM pages" % path)
    # Like the load phase, the boot path runs before any room: keep it resident.
    for pages in rooms.values():
        pages.update(boot_pages)

    all_pages = sorted(set().union(*rooms.values()))
    baseline = set.intersection(*rooms.values())
    if not all_pages:
        raise SystemExit("room sweeps contained no ROM pages")
    if len(all_pages) > 0xFFFF:
        raise SystemExit("too many unique ROM pages for PCPK v1")

    index_of = {page: i for i, page in enumerate(all_pages)}
    blobs = []
    raw_count = 0
    for page in all_pages:
        raw = rom[page * PAGE:(page + 1) * PAGE]
        if len(raw) != PAGE:
            raise ValueError("ROM page %d is incomplete" % page)
        packed = deflate(raw)
        if len(packed) < PAGE:
            blobs.append(packed)
        else:
            blobs.append(raw)
            raw_count += 1

    keys = sorted(rooms)
    manifests = []
    room_table = []
    for area, room in keys:
        pages = sorted(rooms[(area, room)] - baseline)
        if len(pages) > 0xFFFF:
            raise ValueError("room %02x-%02x has too many pages" % (area, room))
        room_table.append((area, room, len(pages), len(manifests)))
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
    data_offset_in_file = len(out)
    out += b"".join(blobs)

    # Round-trip every page and ensure the emitted index points to the right bytes.
    for index, page in enumerate(all_pages):
        table_pos = HEADER_SIZE + PAGE_ENTRY_SIZE * index
        stored_page, stored_size, data_offset = struct.unpack_from("<HHI", out, table_pos)
        if stored_page != page:
            raise AssertionError("page table ordering mismatch at index %d" % index)
        blob = bytes(out[data_offset_in_file + data_offset:data_offset_in_file + data_offset + stored_size])
        decoded = decode_page(blob, stored_size, page)
        if decoded != rom[page * PAGE:(page + 1) * PAGE]:
            raise AssertionError("round-trip mismatch for ROM page %d" % page)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    temporary_path = output_path + ".tmp"
    with open(temporary_path, "wb") as stream:
        stream.write(out)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_path, output_path)

    pak_sha256 = hashlib.sha256(out).hexdigest()
    with open(output_path + ".sha256", "w", encoding="ascii") as stream:
        stream.write("%s  %s\n" % (pak_sha256, os.path.basename(output_path)))
    metadata = {
        "format": "PCPK", "version": 1, "codec": "raw-deflate-per-4KiB-page",
        "rom_region": "USA", "rom_size": len(rom), "rom_sha1": rom_sha1,
        "page_size": PAGE, "unique_pages": len(all_pages), "rooms": len(keys),
        "baseline_pages": len(baseline), "load_pages": len(load_pages), "raw_pages": raw_count,
        "pak_size": len(out), "pak_sha256": pak_sha256,
        "note": "Personal-use ROM-derived data. Do not commit or redistribute."
    }
    with open(output_path + ".json", "w", encoding="utf-8") as stream:
        json.dump(metadata, stream, indent=2, sort_keys=True)
        stream.write("\n")

    raw_total = len(all_pages) * PAGE
    compressed_total = sum(len(blob) for blob in blobs)
    per_room = sorted(sum(len(blobs[index_of[p]]) for p in rooms[key] - baseline) for key in keys)
    median_room = per_room[len(per_room) // 2] if per_room else 0
    p90_room = per_room[min(len(per_room) - 1, int(len(per_room) * 0.9))] if per_room else 0
    max_room = per_room[-1] if per_room else 0
    print("ROM: USA 16 MiB; SHA-1 %s" % rom_sha1)
    print("Rooms %d; unique pages %d (%.2f MiB raw) -> pak %.2f MiB (%.1f%% payload ratio)" % (
        len(keys), len(all_pages), raw_total / 2**20, len(out) / 2**20,
        100.0 * compressed_total / raw_total))
    print("Load-phase pages in resident baseline: %d (%.1f KiB raw)" % (len(load_pages), len(load_pages) * PAGE / 1024))
    print("Baseline %d pages; %.1f KiB compressed; %d pages stored raw" % (
        len(baseline), sum(len(blobs[index_of[p]]) for p in baseline) / 1024, raw_count))
    print("Per-room beyond baseline: median %.1f KiB, p90 %.1f KiB, max %.1f KiB" % (
        median_room / 1024, p90_room / 1024, max_room / 1024))
    print("Pak SHA-256 %s" % pak_sha256)
    print("Wrote %s (metadata: %s.json; checksum: %s.sha256)" % (
        output_path, output_path, output_path))
    print("Only the .pak is intended for calculator transfer (as /Picori.pak; read by Picori built with TMC_FXCG_D1_PAGER=ON).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (OSError, ValueError, struct.error) as exc:
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(1)
