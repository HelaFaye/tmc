#!/usr/bin/env python3
"""Validate the fx-CG50 linker memory map used by the calculator port.

This is a static safety gate, not a substitute for running RAMCheck on the
calculator. It ensures the measured DRAM range, game BSS placement, and
link-time overflow guard remain in sync.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LINKER_SCRIPT = Path(__file__).resolve().parents[2] / "port" / "fxcg" / "fxcg.ld"
EXPECTED_REGIONS = {
    "rom": (0x00300000, 2 * 1024 * 1024),
    "sysram": (0x8C4E0000, 0x320000),
}
EXPECTED_ROM_WINDOW = 0x20000000


def parse_quantity(value: str) -> int:
    """Parse GNU ld integer quantities with optional k/m suffixes."""
    match = re.fullmatch(r"(0[xX][0-9a-fA-F]+|[0-9]+)([kKmM]?)", value.strip())
    if not match:
        raise ValueError("unsupported linker-script quantity: %s" % value)
    number = int(match.group(1), 0)
    suffix = match.group(2).lower()
    return number * (1024 if suffix == "k" else 1024 * 1024 if suffix == "m" else 1)


def _memory_region(text: str, name: str) -> tuple[int, int] | None:
    pattern = re.compile(
        r"^\s*" + re.escape(name)
        + r"\s*\([^)]*\)\s*:\s*o\s*=\s*([^,]+),\s*l\s*=\s*([^\s]+)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        return None
    try:
        return parse_quantity(matches[0][0]), parse_quantity(matches[0][1])
    except ValueError:
        return None


def validate_text(text: str) -> list[str]:
    """Return every memory-layout invariant violated by a linker script."""
    errors: list[str] = []

    for name, expected in EXPECTED_REGIONS.items():
        actual = _memory_region(text, name)
        if actual is None:
            errors.append("MEMORY region '%s' is missing, duplicated, or malformed" % name)
        elif actual != expected:
            errors.append(
                "MEMORY region '%s' is 0x%x + 0x%x; expected 0x%x + 0x%x"
                % (name, actual[0], actual[1], expected[0], expected[1])
            )

    window = re.findall(
        r"^\s*FXCG_ROM_WINDOW\s*=\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*;",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if len(window) != 1 or int(window[0], 0) != EXPECTED_ROM_WINDOW:
        errors.append("FXCG_ROM_WINDOW must be defined once as 0x%08x" % EXPECTED_ROM_WINDOW)

    xbss = re.search(
        r"(?<![A-Za-z0-9_])\.xbss\s*\(\s*NOLOAD\s*\)\s*:\s*\{(.*?)\}\s*>\s*sysram\s*:NONE",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if xbss is None:
        errors.append(".xbss must be NOLOAD and assigned to sysram with :NONE")

    capacity_guard = re.search(
        r"ASSERT\s*\(\s*SIZEOF\s*\(\s*\.xbss\s*\)\s*<=\s*LENGTH\s*\(\s*sysram\s*\)",
        text,
        re.IGNORECASE,
    )
    if capacity_guard is None:
        errors.append("missing ASSERT that .xbss fits LENGTH(sysram)")

    internal_bss = re.search(
        r"(?<![A-Za-z0-9_])\.bss\s*\(\s*NOLOAD\s*\)\s*:\s*\{.*?\}\s*>\s*ram\s*:NONE",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if internal_bss is None:
        errors.append(".bss must remain NOLOAD in internal ram, separate from game .xbss")

    errno_guard = re.search(
        r"ASSERT\s*\(\s*_errno\s*>=\s*ORIGIN\s*\(\s*ram\s*\)\s*&&\s*_errno\s*<\s*ORIGIN\s*\(\s*ram\s*\)\s*\+\s*LENGTH\s*\(\s*ram\s*\)",
        text,
        re.IGNORECASE,
    )
    if errno_guard is None:
        errors.append("missing _errno assertion keeping C-library runtime state in internal ram")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--linker-script",
        type=Path,
        default=DEFAULT_LINKER_SCRIPT,
        help="linker script to validate (default: port/fxcg/fxcg.ld)",
    )
    args = parser.parse_args(argv)

    try:
        text = args.linker_script.read_text(encoding="utf-8")
    except OSError as exc:
        print("cannot read %s: %s" % (args.linker_script, exc), file=sys.stderr)
        return 2

    errors = validate_text(text)
    if errors:
        for error in errors:
            print("FAIL: " + error, file=sys.stderr)
        return 1

    origin, size = EXPECTED_REGIONS["sysram"]
    print(
        "fx-CG50 linker layout: PASS (sysram 0x%08x..0x%08x, %d KiB)"
        % (origin, origin + size, size // 1024)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
