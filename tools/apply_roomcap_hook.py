#!/usr/bin/env python3
"""
apply_roomcap_hook.py — install (or reinstall) the TMC_VR hook in
port/port_repro_roomcap.c.

Why this exists instead of a .patch: the hook has been revised three times, and
`patch` cannot reconcile a tree that already holds an older revision — reverting
matches some hunks and not others, leaving a half-patched file and a .rej. This
script does not care what state the file is in. It finds any existing TMC_VR
block, removes it, and inserts the current one. Running it twice is a no-op.

Usage (from the repo root):
    python3 tools/apply_roomcap_hook.py            # install / update
    python3 tools/apply_roomcap_hook.py --check    # report, change nothing
    python3 tools/apply_roomcap_hook.py --remove   # strip the hook entirely
"""

import argparse
import re
import sys
from pathlib import Path

TARGET = Path("port/port_repro_roomcap.c")

# The hook is inserted immediately before roomcap's PNG output, which is the
# point where the warp has settled and the map data is fully populated.
ANCHOR = '        const char* out = getenv("TMC_ROOMCAP_OUT");'

# Second insertion point: immediately after the synthesized save becomes active,
# before the game starts. Global flags live in gSave, so they can only be set
# once SetActiveSave has run.
PROGRESS_ANCHOR = "        SetActiveSave(0);"

PBEGIN = "/* ==== TMC_VR PROGRESS BEGIN (managed) ==== */"
PEND = "/* ==== TMC_VR PROGRESS END ==== */"

PROGRESS_HOOK = f'''{PBEGIN}
#ifdef TMC_VR
        /* Story progress. roomcap boots a pristine save, and several areas only
         * exist at a later game state:
         *
         *   Hyrule Town redirects to Festival Town while global_progress == 1
         *   and TABIDACHI is unset (roomInit.c:4429).
         *   Dungeon boss arenas and Dark Hyrule Castle exteriors need LVn_CLEAR.
         *
         * global_progress is DERIVED, not stored — UpdateGlobalProgress()
         * (gameUtils.c:939) recomputes it from these flags, so we set the flags
         * and let the engine do the rest.
         *
         *   TMC_ROOMCAP_PROGRESS=<n>   n dungeons cleared, 0..6. Any n sets
         *                              TABIDACHI, which alone is enough to get
         *                              the real Hyrule Town.
         */
        {{
            const char* pg = getenv("TMC_ROOMCAP_PROGRESS");
            if (pg && *pg) {{
                int n = atoi(pg);
                if (n < 0) n = 0;
                if (n > 6) n = 6;
                SetGlobalFlag(TABIDACHI);
                static const u32 kLv[6] = {{ LV1_CLEAR, LV2_CLEAR, LV3_CLEAR,
                                            LV4_CLEAR, LV5_CLEAR, LV6_CLEAR }};
                for (int i = 0; i < n; i++)
                    SetGlobalFlag(kLv[i]);
                UpdateGlobalProgress();
                fprintf(stderr, "[roomcap] progress: %d dungeons, global_progress=%u\\n",
                        n, (unsigned)gSave.global_progress);
            }}
        }}
#endif
{PEND}
'''

PMANAGED = re.compile(re.escape(PBEGIN) + r".*?" + re.escape(PEND) + r"\n", re.DOTALL)

BEGIN = "/* ==== TMC_VR HOOK BEGIN (managed by tools/apply_roomcap_hook.py) ==== */"
END = "/* ==== TMC_VR HOOK END ==== */"

HOOK = f"""{BEGIN}
#ifdef TMC_VR
        /* Sprite harvest. The dumper reads OBJ VRAM, so it only sees frames the
         * engine has already DMA'd in; dumping a range cold yields blanks.
         * VrDump_AutoTick drives the player through the range one frame per
         * tick, letting each frame's tiles load first.
         *   TMC_ROOMCAP_SPRITES="first,last[,dirmask]"  bit0=N 1=E 2=S 3=W
         *   TMC_ROOMCAP_SPRITEDIR=<dir> */
        {{
            static int sprites_started = 0;
            const char* sp = getenv("TMC_ROOMCAP_SPRITES");
            if (sp && *sp) {{
                extern void VrDump_StartAuto(const char*, Entity*, unsigned, unsigned, int);
                extern int VrDump_AutoTick(void);
                extern unsigned VrDump_AutoCount(void);
                if (!sprites_started) {{
                    unsigned f0 = 0, f1 = 0;
                    int mask = 0xF;
                    sscanf(sp, "%u,%u,%i", &f0, &f1, &mask);
                    const char* sd = getenv("TMC_ROOMCAP_SPRITEDIR");
                    VrDump_StartAuto(sd && *sd ? sd : "spritedump",
                                     &gPlayerEntity.base, f0, f1, mask);
                    sprites_started = 1;
                    fprintf(stderr, "[roomcap] sprite harvest %u..%u mask=0x%x\\n",
                            f0, f1, mask);
                }}
                if (VrDump_AutoTick())
                    return;
                fprintf(stderr, "[roomcap] sprite harvest done: %u frames\\n",
                        VrDump_AutoCount());
                fflush(stderr);
                _Exit(0);
            }}
        }}

        /* Wait out any in-flight palette fade. gBgPltt is PAL_RAM, the fade's
         * DESTINATION (src/fade.c:146 writes gPaletteBuffer -> PAL_RAM through
         * the active fade function), so capturing while gFadeControl.active is
         * set records colours pulled toward the fade colour. Bounded so a
         * sustained fade can never hang the harvest. */
        {{
            static int fade_waits = 0;
            if (gFadeControl.active && fade_waits < 600) {{
                fade_waits++;
                return;
            }}
            if (gFadeControl.active) {{
                fprintf(stderr, "[roomcap] WARNING: fade still active after %d "
                                "frames; palette may be off\\n", fade_waits);
            }}
        }}

        /* World snapshot. The warp has settled, so gMapTop/gMapBottom are fully
         * populated and gRoomControls.width/height are non-zero — the exact
         * conditions the interactive auto-dump had to wait for. */
        {{
            const char* vrdir = getenv("TMC_ROOMCAP_TMCR");
            if (vrdir && *vrdir) {{
                extern void VrWorld_Capture(void);
                extern int VrWorld_Dump(const char* dir);
                /* Verify we actually ARRIVED. A warp to coordinates outside the
                 * room can leave Link crossing a border, and by the settle frame
                 * the engine has moved him elsewhere. Without this check we would
                 * dump the wrong room — silently wrong data is worse than a
                 * visible failure. */
                if ((unsigned)gRoomControls.area != a ||
                    (unsigned)gRoomControls.room != r) {{
                    fprintf(stderr,
                            "[roomcap] tmcr ABORT: asked 0x%02x/0x%02x, landed 0x%02x/0x%02x\\n",
                            a, r, (unsigned)gRoomControls.area,
                            (unsigned)gRoomControls.room);
                    fflush(stderr);
                    _Exit(6);
                }}
                VrWorld_Capture();
                int vok = VrWorld_Dump(vrdir);
                fprintf(stderr,
                        "[roomcap] tmcr -> %d (area=0x%02x room=0x%02x %ux%u cells)\\n",
                        vok, (unsigned)gRoomControls.area,
                        (unsigned)gRoomControls.room,
                        (unsigned)(gRoomControls.width / 16),
                        (unsigned)(gRoomControls.height / 16));
                fflush(stderr);
                _Exit(vok ? 0 : 5);
            }}
        }}
#endif
{END}
"""

# Matches the current managed block, and also the two earlier hand-patched
# revisions, which had no markers.
LEGACY = re.compile(
    r"[ \t]*#ifdef TMC_VR\n"
    r"(?:(?!#ifdef|#endif).*\n)*?"
    r"[ \t]*#endif\n"
    r"(?=[ \t]*const char\* out = getenv\(\"TMC_ROOMCAP_OUT\"\);)",
    re.MULTILINE)

MANAGED = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n",
                     re.DOTALL)


def strip(src):
    """Remove any hook revision. Returns (text, how_many_removed)."""
    src, n1 = MANAGED.subn("", src)
    src, n2 = LEGACY.subn("", src)
    src, n3 = PMANAGED.subn("", src)
    return src, n1 + n2 + n3


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--file", default=str(TARGET))
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        sys.exit(f"not found: {path}\nRun this from the repository root.")

    src = path.read_text()
    has_managed = bool(MANAGED.search(src))
    has_any = has_managed or bool(LEGACY.search(src))

    if args.check:
        print(f"{path}: "
              + ("current hook installed" if has_managed
                 else "older hook present, needs updating" if has_any
                 else "no hook"))
        print("  tmcr ABORT check:", "yes" if "tmcr ABORT" in src else "NO")
        print("  sprite harvest:  ", "yes" if "sprite harvest" in src else "NO")
        print("  progress flags:  ", "yes" if PBEGIN in src else "NO")
        return

    cleaned, removed = strip(src)

    if args.remove:
        path.write_text(cleaned)
        print(f"removed {removed} hook block(s) from {path}")
        return

    if ANCHOR not in cleaned:
        sys.exit(f"anchor line not found in {path}.\n"
                 f"Expected: {ANCHOR.strip()}\n"
                 "The file may have diverged; restore it from your Picori "
                 "checkout and re-run.")

    out = cleaned.replace(ANCHOR, HOOK + ANCHOR, 1)

    if PROGRESS_ANCHOR in out:
        out = out.replace(PROGRESS_ANCHOR,
                          PROGRESS_ANCHOR + "\n" + PROGRESS_HOOK, 1)
    else:
        print("  WARNING: progress anchor not found; --progress will not work")

    # flags.h is needed for SetGlobalFlag and the LVn_CLEAR / TABIDACHI names.
    if '#include "fade.h"' not in out:
        out = out.replace('#include "room.h"',
                          '#include "room.h"\n#include "fade.h"     /* TMC_VR: gFadeControl */', 1)

    if '#include "flags.h"' not in out:
        out = out.replace('#include "save.h"',
                          '#include "save.h"\n#include "flags.h"    /* TMC_VR: SetGlobalFlag, LVn_CLEAR, TABIDACHI */', 1)

    path.write_text(out)
    print(f"{path}: removed {removed} old block(s), installed current hook")
    print("  tmcr ABORT check: yes")
    print("  sprite harvest:   yes")
    print("  progress flags:   yes" if PBEGIN in out else "  progress flags:   NO")

    rej = path.with_suffix(path.suffix + ".rej")
    if rej.exists():
        rej.unlink()
        print(f"  cleaned up {rej.name}")
    orig = path.with_suffix(path.suffix + ".orig")
    if orig.exists():
        orig.unlink()
        print(f"  cleaned up {orig.name}")


if __name__ == "__main__":
    main()
