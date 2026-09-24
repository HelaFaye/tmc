#!/usr/bin/env python3
"""
verify_repro.py — prove the asset pipeline is reproducible from the ROM.

The repository ships code that DERIVES assets, never the assets. That promise
is only worth something if regeneration is deterministic: if two runs over the
same dump produce different bytes, then the committed code does not actually
determine the output, and shipping without the assets would mean shipping
something nobody can rebuild.

So this runs the generator twice into separate directories and compares every
byte. It is a check that can fail -- pass --break to see it fail on purpose,
which is how you know it is testing anything.

Usage
-----
  python3 verify_repro.py vrdump --area 3
  python3 verify_repro.py vrdump --area 3 --break     # must report a mismatch
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def digest_dir(d: Path):
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps")
    ap.add_argument("--area", default="")
    ap.add_argument("--texture", action="store_true", default=True)
    ap.add_argument("--break", dest="sabotage", action="store_true",
                    help="corrupt the second run, to prove this check works")
    a = ap.parse_args()

    here = Path(__file__).resolve().parent
    runs = []
    tmp = Path(tempfile.mkdtemp(prefix="tmcrepro"))
    try:
        for i in (0, 1):
            out = tmp / f"run{i}"
            cmd = [sys.executable, str(here / "room_explore.py"), "worldgen",
                   a.dumps, "--out", str(out)]
            if a.area:
                cmd += ["--area", a.area]
            if a.texture:
                cmd += ["--texture"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-2000:]); print(r.stderr[-2000:])
                sys.exit(f"generation failed on run {i}")
            if i == 1 and a.sabotage:
                victim = sorted(out.rglob("*.obj"))[0]
                victim.write_bytes(victim.read_bytes() + b"\n# tampered\n")
            runs.append(digest_dir(out))

        a_, b_ = runs
        only_a = sorted(set(a_) - set(b_))
        only_b = sorted(set(b_) - set(a_))
        diff = sorted(k for k in set(a_) & set(b_) if a_[k] != b_[k])

        print(f"  run 1: {len(a_)} files")
        print(f"  run 2: {len(b_)} files")
        if only_a or only_b:
            print(f"  files only in one run: {len(only_a) + len(only_b)}")
            for k in (only_a + only_b)[:6]:
                print(f"    {k}")
        if diff:
            print(f"  files differing in content: {len(diff)}")
            for k in diff[:6]:
                print(f"    {k}")

        ok = not (only_a or only_b or diff)
        print()
        if ok:
            combined = hashlib.sha256(
                "".join(f"{k}:{v}" for k, v in sorted(a_.items())).encode()
            ).hexdigest()[:16]
            print(f"  -> byte-identical across runs. Pipeline digest {combined}")
            print("     The ROM plus this repository reproduce these assets "
                  "exactly, so none of them need to be distributed.")
        else:
            print("  -> OUTPUT DIFFERS between runs. Something in the pipeline "
                  "is non-deterministic (dict ordering, a timestamp, an "
                  "unsorted glob). Fix that before relying on regeneration.")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
