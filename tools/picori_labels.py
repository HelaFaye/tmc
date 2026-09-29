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
# Special tiles are where an object -- a sprite -- stands (pots, boulders,
# furniture); the room's art under them is bare floor, so the tile stage
# seeds no family from them: they are named by the object (entity_name)
# and built by the entity stage.
SEED_BY_SPECIAL_WORD = ()


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


def _enum_names(text):
    """{value: NAME} of an enum body, counting on from explicit values;
    /*0x00*/ index comments and // comments are ignored."""
    out, n = {}, 0
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for line in text.splitlines():
        line = line.split("//", 1)[0].strip()
        for part in line.split(","):
            m = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=\s*(0x[0-9a-fA-F]+|\d+))?\s*$", part)
            if not m:
                continue
            if m.group(2):
                n = int(m.group(2), 0)
            out[n] = m.group(1)
            n += 1
    return out


def _named_enum(text, name):
    """The body of `typedef enum {..} name;` or `enum name {..}`; @WORD is
    the unnamed enum that holds WORD."""
    if name.startswith("@"):
        for m in re.finditer(r"enum\s*\{(.*?)\}", text, re.S):
            if re.search(r"\b" + name[1:] + r"\b", m.group(1)):
                return m.group(1)
        return ""
    body = _enum_body(text, name)
    if not body:
        m = re.search(r"enum\s+" + re.escape(name) + r"\s*\{(.*?)\}", text, re.S)
        body = m.group(1) if m else ""
    return body


ENTITY_ID_ENUMS = {             # entity kind: (header, enum)
    3: ("include/enemy.h", "@OCTOROK"), 4: ("include/projectile.h", "Projectile"),
    6: ("include/object.h", "Object"), 7: ("include/npc.h", "NPC"),
    9: ("include/manager.h", "Managers"),
}


@lru_cache(maxsize=None)
def entity_kinds():
    return {v: k for v, k in _enum_names(_named_enum(_read("include/entity.h"), "EntityKind")).items()} \
        or {1: "PLAYER", 3: "ENEMY", 4: "PROJECTILE", 6: "OBJECT", 7: "NPC",
            8: "PLAYER_ITEM", 9: "MANAGER"}


@lru_cache(maxsize=None)
def entity_ids(kind):
    """{id: NAME} for an entity kind (objects, enemies, NPCs, ...)."""
    if kind not in ENTITY_ID_ENUMS:
        return {}
    header, enum = ENTITY_ID_ENUMS[kind]
    return _enum_names(_named_enum(_read(header), enum))


@lru_cache(maxsize=None)
def entity_types(kind, eid):
    """{type: NAME} where the entity's own source names its types
    (FurnitureType: BOOKSHELF, CRATE, WOODEN_TABLE, STAIRCASE, ...)."""
    name = entity_ids(kind).get(eid)
    if not name or kind not in (3, 6, 7):
        return {}
    camel = name.lower().split("_")
    camel = camel[0] + "".join(w.capitalize() for w in camel[1:])
    folder = {3: "enemy", 6: "object", 7: "npc"}[kind]
    text = _read(f"src/{folder}/{camel}.c")
    m = re.search(r"typedef enum\s*\{([^}]*)\}\s*\w*Type\s*;", text, re.S)
    return _enum_names(m.group(1)) if m else {}


def entity_name(kind, eid, etype=None):
    """OBJECT FURNITURE WOODEN_TABLE, ENEMY OCTOROK, NPC SMITH ..."""
    k = entity_kinds().get(kind, f"KIND_{kind}")
    n = entity_ids(kind).get(eid, f"0x{eid:x}")
    t = entity_types(kind, eid).get(etype) if etype is not None else None
    return " ".join(x for x in (k, n, t) if x)


@lru_cache(maxsize=None)
def area_flags():
    """{area: {AR_IS_OVERWORLD, AR_IS_DUNGEON, AR_HAS_KEYS, AR_IS_MOLE_CAVE,
    ...}} from gAreaMetadata (src/data/areaMetadata.c), in area order."""
    names = _enum_names(_named_enum(_read("include/area.h"), "AreaFlags"))
    bits = {v: n for v, n in names.items()}
    text = _read("src/data/areaMetadata.c")
    m = re.search(r"gAreaMetadata\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    macros = dict(re.findall(r"#define\s+(\w+)\s+\(([^)]*)\)", text))
    out = {}
    for n, row in enumerate(re.findall(r"\{\s*([^,}]*),", m.group(1) if m else "")):
        expr = row
        for k, v in macros.items():
            expr = expr.replace(k, v)
        flags = set()
        for tok in re.findall(r"[A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+", expr):
            if tok in bits.values():
                flags.add(tok)
            elif re.fullmatch(r"0x[0-9a-fA-F]+|\d+", tok):
                flags |= {nm for v, nm in bits.items() if int(tok, 0) & v}
        out[n] = flags
    return out


# Areas the flags do not call overworld that are out of doors: the Minish
# village is a clearing in the woods
OUTDOOR_AREAS = {"AREA_MINISH_VILLAGE"}
# dungeon areas under the sky: a castle's courtyard and bridge, a tower's
# top or roof
OPEN_AIR_WORDS = ("_OUTSIDE", "_TOP", "_ROOF", "_BRIDGE")

# Rooms the flags cannot tell: drawn another way than top-down
SPECIAL_VIEW = {
    (45, 16): "terrace",    # the library bookshelf: shelf boards stacked,
                            # each above the books under it
    (72, 32): "rotating",   # inside the Deepwood barrel: an affine background
}


def location(area, room):
    """Where a room is, for the tile stage's rules: kind is 'outdoors',
    'dungeon', 'cave' or 'indoors'; minish when Link is Minish-sized there;
    view is 'top' unless SPECIAL_VIEW says otherwise."""
    f = area_flags().get(area, set())
    name = areas().get(area, "")
    if "AR_IS_OVERWORLD" in f or name in OUTDOOR_AREAS:
        kind = "outdoors"
    elif "AR_IS_DUNGEON" in f or "AR_HAS_KEYS" in f:
        kind = "dungeon"
    elif "AR_IS_MOLE_CAVE" in f or "CAVE" in name:
        kind = "cave"
    else:
        kind = "indoors"
    minish = "MINISH" in name and "WOODS" not in name
    open_air = kind == "outdoors" or any(w in name for w in OPEN_AIR_WORDS)
    return dict(kind=kind, minish=minish, open_air=open_air,
                view=SPECIAL_VIEW.get((area, room), "top"),
                flags=sorted(f), area_name=name)


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
