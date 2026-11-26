#!/usr/bin/env python3
"""Write a synthetic PCPK v1 pak for D1-PAK-PROBE (no ROM needed).

usage: make_test_pak.py <out.pak> [pages]

Page p holds big-endian words (p << 16) ^ offset ^ 0x5A000000, the pattern
D1-PAGER-PROBE generates, so the probe's stages run unchanged against a
real pak. Every seventh page is stored raw (stored_size 4096), the rest raw
DEFLATE, so both decode paths run; pages 5 and 6 form the baseline, so
start-up pinning (one page of each parity) runs too. Layout as in
scripts/fxcg/build_page_pak.py.
"""
import struct
import sys
import zlib

PAGE = 4096


def page_bytes(p):
    return b"".join(struct.pack(">I", (p << 16) ^ o ^ 0x5A000000) for o in range(0, PAGE, 4))


def main(argv):
    out_path = argv[1]
    pages = int(argv[2]) if len(argv) > 2 else 512
    blobs = []
    for p in range(pages):
        raw = page_bytes(p)
        if p % 7 == 3:
            blobs.append(raw)
        else:
            c = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
            packed = c.compress(raw) + c.flush()
            assert len(packed) < PAGE
            blobs.append(packed)
    baseline = [5, 6]
    out = bytearray(struct.pack("<4sHBBIIIII", b"PCPK", 1, 1, 12, pages * PAGE, pages, 0, len(baseline), 0))
    offset = 0
    for p, blob in enumerate(blobs):
        out += struct.pack("<HHI", p, len(blob), offset)
        offset += len(blob)
    out += b"".join(struct.pack("<H", i) for i in baseline)
    out += b"".join(blobs)
    with open(out_path, "wb") as f:
        f.write(out)
    print("wrote %s: %d pages, %d bytes" % (out_path, pages, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
