#!/usr/bin/env python3
"""picori_labels.py -- the labels Project Picori (the decompilation) gives.

Read from the source tree at run time, so nothing is copied or derived
from the ROM:

  include/tiles.h     TileType   the named tile types (CUT_BUSH, ROCK,
                                 CHEST, TORCH, STAIRS_UP, SIGNPOST, ...)
                      SpecialTile  the object tiles, 0x4000 on, with what
                                 each is (Pots, Boulder, Grave, Portal ...)
                      ActTile    each surface action's surface and users
  include/area.h      the areas, in order
  include/roomid.h    each area's rooms
  src/**/*.c          which game code refers to a tile type or special
                      tile by number -- bombableWallManager, dampe,
                      hiddenLadderDown -- which says what the tile is
                      (the two tables that list every tile type are left
                      out)

tileid.py seeds its families from these (SEED_BY_TYPE, SEED_BY_SPECIAL);
tile_labeler.py shows all of them beside each cell.

Usage:
  python3 tools/picori_labels.py            # what is named, in summary
  python3 tools/picori_labels.py --type 0x55 --special 0x4000 --act 0x29
"""
import argparse
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_REFS = 200        # a file naming more tile types than this is a table

# Picori's names, as our families: where the game says what a tile is
SEED_BY_NAME = {
    "CUT_BUSH": "bush", "CUT_TREE": "sapling", "CUT_SIGNPOST": "signpost",
    "SIGNPOST": "signpost", "ROCK": "rock", "PERMA_ROCK": "rock",
    "PERMA_ROCK2": "rock", "PERMA_ROCK3": "rock", "PERMA_ROCK4": "rock",
}
SEED_BY_SPECIAL_WORD = (          # first match in the SpecialTile comment
    ("Move Pot", None), ("Pots", "pot"),
    ("Move Bolder", None), ("Boulder in Hole", None), ("Boulder", "rock"),
)


def _read(rel):
    p = ROOT / rel
    return p.read_text(errors="replace") if p.is_file() else ""


def _enum_body(text, name):
    """The text of `typedef enum { ... } name;`."""
    m = re.search(r"typedef enum\s*\{(.*?)\}\s*" + re.escape(name) + r"\s*;", text, re.S)
    return m.group(1) if m else ""


@lru_cache(maxsize=None)
def tile_types():
    """{tile type: name} for the named ones (the comment after the value)."""
    out = {}
    for m in re.finditer(r"TILE_TYPE_\d+\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*//\s*([A-Z0-9_]+)",
                         _enum_body(_read("include/tiles.h"), "TileType")):
        out[int(m.group(1), 16)] = m.group(2)
    return out


@lru_cache(maxsize=None)
def special_tiles():
    """{special tile index (0x4000 on): what it is}."""
    out = {}
    for m in re.finditer(r"SPECIAL_TILE_\d+\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*//\s*([^\n]+)",
                         _enum_body(_read("include/tiles.h"), "SpecialTile")):
        out[int(m.group(1), 16)] = m.group(2).strip()
    return out


@lru_cache(maxsize=None)
def act_tiles():
    """{surface action: its note} -- the surface it gives, and who uses it."""
    out = {}
    for m in re.finditer(r"ACT_TILE_\d+\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*//\s*([^\n]+)",
                         _enum_body(_read("include/tiles.h"), "ActTile")):
        out[int(m.group(1), 16)] = m.group(2).strip()
    return out


def act_short(act):
    """The surface a cell's action gives (SURFACE_DOOR, SURFACE_WATER), or
    None. Picori's TILE_ACT_* notes name what an item does to a tile --
    the low actions are also plain floors' -- so they are not shown."""
    note = act_tiles().get(act)
    s = re.search(r"->\s*(SURFACE_[A-Z0-9_]+)", note or "")
    return s.group(1) if s else None


@lru_cache(maxsize=None)
def areas():
    """{area number: AREA_NAME}."""
    text = _read("include/area.h")
    m = re.search(r"enum\s*\w*\s*\{([^}]*AREA_MINISH_WOODS[^}]*)\}", text, re.S)
    out, n = {}, 0
    for line in (m.group(1) if m else "").splitlines():
        line = line.split("//", 1)[0]
        a = re.search(r"(AREA_\w+)\s*(?:=\s*(0x[0-9a-fA-F]+|\d+))?\s*,?", line)
        if not a:
            continue
        if a.group(2):
            n = int(a.group(2), 0)
        out[n] = a.group(1)
        n += 1
    return out


@lru_cache(maxsize=None)
def rooms():
    """{(area, room): ROOM_NAME}, from roomid.h's per-area comments."""
    by_name = {v: k for k, v in areas().items()}
    out, area, n = {}, None, 0
    for line in _read("include/roomid.h").splitlines():
        c = re.match(r"\s*//\s*(AREA_\w+)", line)
        if c:
            area, n = by_name.get(c.group(1)), 0
            continue
        r = re.match(r"\s*(ROOM_\w+)\s*(?:=\s*(0x[0-9a-fA-F]+|\d+))?\s*,", line)
        if r and area is not None:
            if r.group(2):
                n = int(r.group(2), 0)
            out[(area, n)] = r.group(1)
            n += 1
    return out


def room_name(area, room):
    return rooms().get((area, room)) or f"{areas().get(area, 'AREA_%d' % area)} room {room}"


@lru_cache(maxsize=None)
def code_refs():
    """({tile type: [files]}, {special tile: [files]}): the game code that
    names a tile by number, tables of every tile type left out."""
    tt_num = {}
    sp_num = {}
    for m in re.finditer(r"(TILE_TYPE_\d+)\s*=\s*(0x[0-9a-fA-F]+)",
                         _enum_body(_read("include/tiles.h"), "TileType")):
        tt_num[m.group(1)] = int(m.group(2), 16)
    for m in re.finditer(r"(SPECIAL_TILE_\d+)\s*=\s*(0x[0-9a-fA-F]+)",
                         _enum_body(_read("include/tiles.h"), "SpecialTile")):
        sp_num[m.group(1)] = int(m.group(2), 16)
    tt_refs, sp_refs = {}, {}
    for f in sorted((ROOT / "src").rglob("*.c")):
        text = f.read_text(errors="replace")
        tts = set(re.findall(r"\bTILE_TYPE_\d+\b", text))
        sps = set(re.findall(r"\bSPECIAL_TILE_\d+\b", text))
        name = str(f.relative_to(ROOT / "src"))[:-2]
        if len(tts) <= TABLE_REFS:
            for t in tts:
                if t in tt_num:
                    tt_refs.setdefault(tt_num[t], []).append(name)
        if len(sps) <= TABLE_REFS:
            for t in sps:
                if t in sp_num:
                    sp_refs.setdefault(sp_num[t], []).append(name)
    return tt_refs, sp_refs


@lru_cache(maxsize=None)
def seed_by_type():
    """{tile type: family} from Picori's tile type names."""
    return {v: SEED_BY_NAME[n] for v, n in tile_types().items() if n in SEED_BY_NAME}


@lru_cache(maxsize=None)
def seed_by_special():
    """{special tile: family} from Picori's special tile notes."""
    out = {}
    for v, note in special_tiles().items():
        for word, fam in SEED_BY_SPECIAL_WORD:
            if word in note:
                if fam:
                    out[v] = fam
                break
    return out


def describe_cell(tile, tile_type, act, coll):
    """Everything Picori says about one cell, as a dict of strings."""
    tt_refs, sp_refs = code_refs()
    d = {}
    if tile >= 0x4000:
        d["special"] = f"SPECIAL_TILE 0x{tile:x}" + (
            f": {special_tiles()[tile]}" if tile in special_tiles() else "")
        if tile in sp_refs:
            d["special_code"] = ", ".join(sp_refs[tile])
    else:
        d["tile_type"] = f"0x{tile_type:x}" + (
            f" {tile_types()[tile_type]}" if tile_type in tile_types() else "")
        if tile_type in tt_refs:
            d["tile_type_code"] = ", ".join(tt_refs[tile_type])
    d["act"] = f"0x{act:x}" + (f" {act_short(act)}" if act_short(act) else "")
    d["collision"] = f"0x{coll:x}"
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", help="a tile type to describe")
    ap.add_argument("--special", help="a special tile to describe")
    ap.add_argument("--act", help="a surface action to describe")
    a = ap.parse_args()
    tt_refs, sp_refs = code_refs()
    if a.type or a.special or a.act:
        if a.type:
            v = int(a.type, 0)
            print(f"tile type 0x{v:x}: {tile_types().get(v, '(unnamed)')}; "
                  f"code: {', '.join(tt_refs.get(v, [])) or '-'}")
        if a.special:
            v = int(a.special, 0)
            print(f"special tile 0x{v:x}: {special_tiles().get(v, '(no note)')}; "
                  f"code: {', '.join(sp_refs.get(v, [])) or '-'}")
        if a.act:
            v = int(a.act, 0)
            print(f"act 0x{v:x}: {act_tiles().get(v, '(no note)')}")
        return
    print(f"tile types named: {len(tile_types())}; referred to by game code: {len(tt_refs)}")
    print(f"special tiles with a note: {len(special_tiles())}; referred to by code: {len(sp_refs)}")
    print(f"surface actions with a note: {len(act_tiles())}")
    print(f"areas: {len(areas())}; rooms named: {len(rooms())}")
    print("families seeded:", {f"0x{k:x}": v for k, v in seed_by_type().items()},
          {f"0x{k:x}": v for k, v in seed_by_special().items()})


if __name__ == "__main__":
    main()
