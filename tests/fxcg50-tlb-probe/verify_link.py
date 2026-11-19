#!/usr/bin/env python3
"""Verify that an application-provided .gint.tlbh overrides Gint's archive.

This is intentionally a link-time check only. The probe's TLB handler is a
nonfunctional `rte; nop` stub and must never be run on a calculator or emulator.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} path/to/fxcg_tlbh_link_probe", file=sys.stderr)
        return 2

    elf = Path(argv[1])
    if not elf.is_file():
        print(f"error: probe ELF does not exist: {elf}", file=sys.stderr)
        return 2

    nm = shutil.which("sh-elf-nm")
    if not nm:
        print("error: sh-elf-nm is not on PATH", file=sys.stderr)
        return 2

    result = subprocess.run(
        [nm, "-n", str(elf)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode

    symbols: dict[str, int] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            value = int(fields[0], 16)
        except ValueError:
            continue
        symbols[fields[-1]] = value

    required = ("_gint_tlbh", "_gint_tlbh_start", "_gint_tlbh_size")
    missing = [name for name in required if name not in symbols]
    if missing:
        print("error: missing expected linker symbols: " + ", ".join(missing), file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1

    handler = symbols["_gint_tlbh"]
    section_start = symbols["_gint_tlbh_start"]
    section_size = symbols["_gint_tlbh_size"]

    print(f"_gint_tlbh       = 0x{handler:08x}")
    print(f"_gint_tlbh_start = 0x{section_start:08x}")
    print(f"_gint_tlbh_size  = {section_size} bytes")

    if handler != section_start:
        print("error: application handler is not the first byte of .gint.tlbh", file=sys.stderr)
        return 1
    if section_size != 4:
        print(
            "error: .gint.tlbh is not exactly the 4-byte probe stub; "
            "the Gint archive handler may also have been linked",
            file=sys.stderr,
        )
        return 1

    print("GINT_TLBH_LINK_OVERRIDE=PASS")
    print("GINT_TLBH_PROBE_EXECUTION=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
