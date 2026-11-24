#!/usr/bin/env python3
"""Give C bit-field runs a big-endian variant with the GBA's bit layout.

GCC lays bit-fields out from the least significant bit on little-endian
targets (the GBA, the PC) and from the most significant bit on big-endian
ones (the fx-CG50's SH-4). Game and port code that sees the raw bits --
the PPU reading OAM, bytes copied from the ROM, masks on raw bytes -- then
disagrees with code that uses the fields by name.

For every run of consecutive bit-field declarations this script emits

    #if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
        <the same fields, reversed within each storage unit, with explicit
         padding for partly filled units>
    #else
        <the original lines, unchanged>
    #endif

so little-endian builds compile exactly the original text. Storage units
follow GCC's rules for the declared type; 32-bit units are split into two
16-bit halves when no field straddles bit 16 (the GBA's memory is read as
halfwords: OAM attributes, I/O registers). Runs whose struct already
handles byte order (contains __BYTE_ORDER__) are left alone.

usage: be_bitfields.py [--check] <file.h> ...
  --check: report what would change, write nothing
"""
import re
import sys

FIELD = re.compile(r"^(\s*)(/\*[^*]*\*/\s*)?(u8|u16|u32|s8|s16|s32|unsigned int|unsigned|int|bool8)\s+(\w+)\s*:\s*(\d+)\s*;(.*)$")
WIDTH = {"u8": 8, "s8": 8, "bool8": 8, "u16": 16, "s16": 16, "u32": 32, "s32": 32,
         "unsigned": 32, "unsigned int": 32, "int": 32}
SIGNED = {"s8", "s16", "s32", "int"}


def unit_type(bits, signed):
    return ("s" if signed else "u") + str(bits)


def plan_units(fields):
    """Pack (type, name, bits, line) fields into storage units the way GCC
    does for the GBA. Returns [(unit_bits, [(name, bits, offset, type)])] or
    None when the run cannot be represented (mixed or straddling fields)."""
    width = WIDTH[fields[0][0]]
    if any(WIDTH[f[0]] != width for f in fields):
        return None
    units, cur, used = [], [], 0
    for ftype, name, bits, line in fields:
        if bits == 0 or bits > width:
            return None
        if used + bits > width:
            units.append(cur)
            cur, used = [], 0
        cur.append((name, bits, used, ftype))
        used += bits
    if cur:
        units.append(cur)
    out = []
    for unit in units:
        if width == 32:
            lo = [f for f in unit if f[2] + f[1] <= 16]
            hi = [f for f in unit if f[2] >= 16]
            if len(lo) + len(hi) != len(unit):
                return None  # a field straddles bit 16
            out.append((16, lo))
            if hi:
                out.append((16, [(n, b, o - 16, t) for n, b, o, t in hi]))
        else:
            out.append((width, unit))
    return out


def be_lines(indent, units):
    lines = []
    for bits, fields in units:
        if not fields:
            lines.append("%su%d : %d; /* BE: unit padding */" % (indent, bits, bits))
            continue
        signed = any(t in SIGNED for _, _, _, t in fields)
        top = fields[-1][2] + fields[-1][1]
        if top < bits:
            lines.append("%s%s : %d; /* BE: padding above the GBA fields */" % (
                indent, unit_type(bits, False), bits - top))
        for name, fbits, _, ftype in reversed(fields):
            lines.append("%s%s %s : %d;" % (indent, unit_type(bits, ftype in SIGNED), name, fbits))
    return lines


def transform(text):
    lines = text.split("\n")
    out, i, changed, skipped = [], 0, 0, []
    depth_has_order = []
    while i < len(lines):
        line = lines[i]
        if "__BYTE_ORDER__" in line:
            # Leave the enclosing struct alone (it handles byte order itself).
            out.append(line)
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("}"):
                out.append(lines[i])
                i += 1
            continue
        m = FIELD.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        run, j = [], i
        while j < len(lines):
            m2 = FIELD.match(lines[j])
            if m2:
                run.append((m2.group(3), m2.group(4), int(m2.group(5)), lines[j]))
                j += 1
                continue
            stripped = lines[j].strip()
            if stripped == "" or (stripped.startswith("/*") and stripped.endswith("*/")) or stripped.startswith("//"):
                # Comment/blank inside a run: keep scanning only if more fields follow.
                k = j
                while k < len(lines) and not FIELD.match(lines[k]) and (
                        lines[k].strip() == "" or lines[k].strip().startswith(("/*", "//"))):
                    k += 1
                if k < len(lines) and FIELD.match(lines[k]):
                    j = k
                    continue
            break
        # Split at changes of declared width; that is only safe when the
        # earlier unit is full (GCC may otherwise pack across the change).
        subruns, units = [], []
        for f in run:
            if subruns and WIDTH[subruns[-1][-1][0]] == WIDTH[f[0]]:
                subruns[-1].append(f)
            else:
                subruns.append([f])
        for n, sub in enumerate(subruns):
            planned = plan_units(sub)
            if planned is None:
                units = None
                break
            if n + 1 < len(subruns) and sum(b for _, b, _, _ in planned[-1][1]) != planned[-1][0]:
                units = None
                break
            units.extend(planned)
        original = lines[i:j]
        if units is None:
            skipped.append(run[0][1])
            out.extend(original)
        else:
            indent = m.group(1)
            out.append("#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__ /* be_bitfields.py: GBA bit layout */")
            out.extend(be_lines(indent, units))
            out.append("#else")
            out.extend(original)
            out.append("#endif")
            changed += 1
        i = j
    return "\n".join(out), changed, skipped


def main(argv):
    check = "--check" in argv
    files = [a for a in argv[1:] if a != "--check"]
    total = 0
    for path in files:
        text = open(path).read()
        new, changed, skipped = transform(text)
        if changed:
            total += changed
            if not check:
                open(path, "w").write(new)
        if changed or skipped:
            print("%s: %d runs%s" % (path, changed, ("; not representable: " + ", ".join(skipped)) if skipped else ""))
    print("total runs:", total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
