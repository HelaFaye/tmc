#!/usr/bin/env python3
"""
decomp_labels.py — resolve an entity's (kind, id) to the decomp's own name.

Project Picori already knows what every object in the game is. `object.h`,
`npc.h` and `enemy.h` carry the id tables, and entity.h says which table a
given `kind` indexes. Reading them turns "id=0xbb" into "WINDCREST", which is
the difference between identifying an object and guessing at it.

This was written after four objects were misidentified in a row by reasoning
from id numbers and their neighbours. Every one of those was named in a header
sitting in the repo:

    0xbb kind 6  ->  WINDCREST                 (guessed: tree stump)
    0xaa kind 6  ->  WATERFALL_OPENING         (guessed: stump variant)
    0x2b kind 7  ->  CASTOR_WILDS_STATUE       (guessed: tree stump)

The last one also shows why `kind` matters: 0x2b in the OBJECT table is
LILYPAD_LARGE_FALLING, which is a different wrong answer. The tables are not
interchangeable, and an id without its kind is meaningless.

Usage
-----
  python3 tools/decomp_labels.py --repo . --kind 6 --id 0xbb
  python3 tools/decomp_labels.py --repo . --grep STUMP
"""

import argparse
import re
import sys
from pathlib import Path

# entity.h: which id table each kind indexes.
KIND_TABLE = {1: "player", 3: "enemy.h", 4: "projectile", 6: "object.h",
              7: "npc.h", 9: "manager"}
KIND_NAME = {1: "PLAYER", 3: "ENEMY", 4: "PROJECTILE", 6: "OBJECT",
             7: "NPC", 9: "MANAGER"}


def parse_header(path):
    """Ids from one header.

    Two styles appear in the decomp. npc.h and enemy.h annotate each entry
    with its value in a comment; object.h is a bare sequential enum. Prefer
    the annotated form where it exists, because it survives entries being
    added or reordered -- counting positions does not.
    """
    if not path.exists():
        return {}
    text = path.read_text(errors="replace")
    annotated = {}
    for m in re.finditer(r"/\*\s*(0x[0-9a-fA-F]+)\s*\*/\s*([A-Za-z_]\w*)\s*,", text):
        annotated[int(m.group(1), 0)] = m.group(2)
    if len(annotated) >= 20:
        return annotated

    best = None
    for m in re.finditer(r"typedef\s+enum\s*\{(.*?)\}", text, re.S):
        body = m.group(1)
        n = len(re.findall(r"^\s*[A-Za-z_]\w*\s*(?:=[^,]+)?,", body, re.M))
        if best is None or n > best[0]:
            best = (n, body)
    if not best:
        return annotated
    out, v = {}, 0
    for line in best[1].splitlines():
        line = re.sub(r"/\*.*?\*/", "", line).split("//")[0].strip().rstrip(",")
        if not line:
            continue
        if "=" in line:
            nm, val = line.split("=", 1)
            nm = nm.strip()
            try:
                v = int(val.strip(), 0)
            except ValueError:
                pass
        else:
            nm = line
        if re.fullmatch(r"[A-Za-z_]\w*", nm):
            out[v] = nm
            v += 1
    return out


def parse_tile_names(path):
    """Tile-type names, which the decomp keeps in COMMENTS, not identifiers.

    include/tiles.h declares 1700-odd tile types as TILE_TYPE_<number>, which
    names nothing. But 219 of them carry a trailing comment saying what they
    actually are -- CUT_BUSH, ROCK, CHEST, STAIRS_UP, SIGNPOST, "Pots",
    "Beanstalk/Ladder", "Boulder". That comment is the label, and it is the
    only description of world geometry the project has.

    Reading it beats inferring objects from palette ids, which are per-area
    and meant nothing two areas over.
    """
    if not path.exists():
        return {}
    out = {}
    for m in re.finditer(
            r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*//\s*(.+?)\s*$",
            path.read_text(errors="replace"), re.M):
        name = m.group(3).strip()
        if not name or name.startswith("/"):
            continue
        out[int(m.group(2), 0)] = name
    return out


class Labels:
    def __init__(self, repo):
        inc = Path(repo) / "include"
        self.tables = {k: parse_header(inc / f)
                       for k, f in KIND_TABLE.items() if f.endswith(".h")}
        self.tiles = parse_tile_names(inc / "tiles.h")
        self.ok = any(self.tables.values())

    def name(self, kind, eid):
        t = self.tables.get(int(kind))
        if not t:
            return KIND_NAME.get(int(kind), f"kind{kind}")
        return t.get(int(eid), f"{KIND_NAME.get(int(kind), 'kind')}_{int(eid):02X}")

    def grep(self, pat):
        rx = re.compile(pat, re.I)
        for k, t in self.tables.items():
            for v, nm in sorted(t.items()):
                if rx.search(nm):
                    yield k, v, nm


def find_repo(start=None):
    """Walk up for the decomp root. The kit is usually unzipped INSIDE the
    repo, so the tool's own parent is not it."""
    here = Path(start or __file__).resolve()
    for p in [here] + list(here.parents):
        if (p / "include" / "object.h").exists():
            return p
    for p in [Path.cwd()] + list(Path.cwd().parents):
        if (p / "include" / "object.h").exists():
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--kind", type=lambda v: int(v, 0))
    ap.add_argument("--id", type=lambda v: int(v, 0))
    ap.add_argument("--grep")
    a = ap.parse_args()
    repo = Path(a.repo) if a.repo else find_repo()
    if repo is None:
        sys.exit("could not find the decomp root (no include/object.h above here)")
    L = Labels(repo)
    if not L.ok:
        sys.exit(f"no id tables parsed under {repo}/include")
    for k, t in sorted(L.tables.items()):
        print(f"  kind {k} ({KIND_NAME[k]}): {len(t)} ids from {KIND_TABLE[k]}")
    print(f"  tile types: {len(L.tiles)} named in tiles.h (in comments)")
    if a.grep:
        print()
        for k, v, nm in L.grep(a.grep):
            print(f"  kind {k} {KIND_NAME[k]:<9} 0x{v:02x}  {nm}")
        rx = re.compile(a.grep, re.I)
        for v, nm in sorted(L.tiles.items()):
            if rx.search(nm):
                print(f"  tile type      0x{v:04x}  {nm}")
    if a.kind is not None and a.id is not None:
        print(f"\n  kind {a.kind} id 0x{a.id:02x} = {L.name(a.kind, a.id)}")


if __name__ == "__main__":
    main()
