#!/bin/bash
# Bulk voxelation: every room, the overworld, and every fitted object.
#
# Runs in the FOREGROUND and prints progress as it goes. Safe to re-run and
# resumable: outputs that already exist are skipped unless FORCE=1.
#
#   TMC_ROOT    repo root            (default: auto-detect, then $PWD)
#   TMC_ROOMS   room dump directory  (default: first non-empty of
#                                     states/p0, night/vrdump, vrdump)
#   STAGES      which to run         (default: objects,rooms,world)
#   FORCE=1     rebuild everything, ignoring what is already there
#
# Outputs, all under $TMC_ROOT and all gitignored -- they are derived from
# your ROM and are not distributable:
#   geom/*.obj         objects, one mesh per class
#   geom/rooms/*.obj   one mesh per room
#   geom/world/*.obj   one mesh per area
set -u

here="$(cd "$(dirname "$0")" && pwd)"
KIT="$(cd "$here/.." && pwd)"
ROOT="${TMC_ROOT:-}"
if [ -z "$ROOT" ]; then
  d="$here"
  for _ in 1 2 3 4 5 6; do
    if [ -f "$d/include/area.h" ] || [ -f "$d/xmake.lua" ]; then ROOT="$d"; break; fi
    d="$(dirname "$d")"; [ "$d" = "/" ] && break
  done
fi
[ -z "$ROOT" ] && ROOT="$PWD"
cd "$ROOT" || exit 1

if   [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then PY="$VIRTUAL_ENV/bin/python"
else PY="python3"; fi
"$PY" -c "import numpy, PIL" 2>/dev/null || {
  echo "!! $PY has no numpy/pillow. Activate the venv, or:" >&2
  echo "   $PY -m pip install --break-system-packages numpy pillow" >&2; exit 1; }

ROOMS="${TMC_ROOMS:-}"
if [ -z "$ROOMS" ]; then
  for c in "$ROOT/states/p0" "$ROOT/night/vrdump" "$ROOT/vrdump"; do
    # non-EMPTY, not merely present: a failed run leaves its mkdir behind,
    # and an existence check once made a whole harvest skip in 13 seconds
    # while reporting success.
    if [ -n "$(ls -A "$c" 2>/dev/null)" ]; then ROOMS="$c"; break; fi
  done
fi
[ -z "$ROOMS" ] && { echo "!! no room dumps; set TMC_ROOMS" >&2; exit 1; }

STAGES="${STAGES:-objects,rooms,world}"
FORCE="${FORCE:-0}"
# Authored heights are committed at vr/world/ in the repo; the standalone
# kit layout keeps them at world/. Try the repo path first.
HEIGHTS=""
for h in "$KIT/vr/world/heights.txt" "$KIT/world/heights.txt"; do
  if [ -f "$h" ]; then HEIGHTS="$h"; break; fi
done
LOG="$ROOT/voxelate.log"
N=$(ls "$ROOMS"/room_*.tmcr 2>/dev/null | wc -l)

mkdir -p geom/rooms geom/world objects
{
echo "=== voxelate $(date) ==="
echo "root   $ROOT"
echo "python $PY"
echo "rooms  $ROOMS ($N dumps)"
echo "stages $STAGES   force=$FORCE"
[ -n "$HEIGHTS" ] && echo "heights $HEIGHTS" || echo "heights: none (flat classes)"
} | tee "$LOG"

stage(){ case ",$STAGES," in *",$1,"*) return 0;; *) return 1;; esac; }
t0=$(date +%s)

# ---- objects ---------------------------------------------------------
if stage objects; then
  echo "" | tee -a "$LOG"
  echo "-- stage 1/3: objects (manifests, fit, mesh)" | tee -a "$LOG"
  TMC_ROOT="$ROOT" TMC_ROOMS="$ROOMS" bash "$KIT/tools/rebuild_all.sh" 2>&1 \
    | tee -a "$LOG" | grep -E "fittable|built|placed|undersized|!!" || true
fi

# ---- rooms -----------------------------------------------------------
if stage rooms; then
  echo "" | tee -a "$LOG"
  echo "-- stage 2/3: $N rooms -> geom/rooms/" | tee -a "$LOG"
  i=0; made=0; skip=0; fail=0; lay1=0; empty=0
  for f in "$ROOMS"/room_*.tmcr; do
    i=$((i+1))
    b="$(basename "$f" .tmcr)"
    o="geom/rooms/$b.obj"
    if [ "$FORCE" != "1" ] && [ -s "$o" ]; then skip=$((skip+1)); continue; fi
    if "$PY" "$KIT/tools/room_explore.py" voxel "$f" --out "$o" --overlay \
         ${HEIGHTS:+--heights "$HEIGHTS"} >>"$LOG" 2>&1; then
      made=$((made+1))
    elif "$PY" "$KIT/tools/room_explore.py" voxel "$f" --out "$o" --layer 1 \
         ${HEIGHTS:+--heights "$HEIGHTS"} >>"$LOG" 2>&1; then
      # Some captures have no layer 0 at all -- room_33_20..26 among them.
      # That is a property of the dump, not a failure of the build, so try
      # the other layer and only give up if that is empty too.
      made=$((made+1)); lay1=$((lay1+1))
    elif [ "$("$PY" -c "
import sys; sys.path.insert(0,'$KIT/tools')
import room_explore as RE
from pathlib import Path
try:
    r = RE.load_room(Path('$f'))
    print(int(r.cells_w) * int(r.cells_h))
except Exception:
    print(-1)" 2>/dev/null)" = "0" ]; then
      # Zero cells wide: the capture is EMPTY, so there is nothing to
      # voxelate. That is a bad dump, not a broken build -- re-harvest
      # these rooms rather than debugging the mesher.
      empty=$((empty+1)); echo "   empty dump: $b (0 cells -- re-harvest it)"
    else
      fail=$((fail+1)); echo "   FAILED $b (see $LOG)"
    fi
    if [ $((i % 25)) -eq 0 ] || [ "$i" -eq "$N" ]; then
      el=$(( $(date +%s) - t0 ))
      printf "\r   %4d/%-4d  built %-4d skipped %-4d failed %-3d  %ds elapsed" \
        "$i" "$N" "$made" "$skip" "$fail" "$el"
    fi
  done
  echo "" | tee -a "$LOG"
  echo "   rooms: $made built ($lay1 via layer 1), $skip already present," \
       "$empty empty dumps, $fail failed" | tee -a "$LOG"
fi

# ---- overworld -------------------------------------------------------
if stage world; then
  echo "" | tee -a "$LOG"
  echo "-- stage 3/3: overworld by area -> geom/world/" | tee -a "$LOG"
  AREAS=$(ls "$ROOMS"/room_*.tmcr 2>/dev/null \
          | sed 's#.*/room_\([0-9]\{1,3\}\)_.*#\1#' | sort -un)
  na=$(echo "$AREAS" | wc -w); j=0; wmade=0; wskip=0; wfail=0
  for a in $AREAS; do
    j=$((j+1))
    # Strip the zero padding. worldgen parses --area with int(a, 0), where
    # a leading zero is an octal prefix, so "01".."09" are all invalid
    # literals and every one of those areas died. 10# forces decimal.
    a=$((10#$a))
    # worldgen writes a DIRECTORY (one mesh per level), not a single file,
    # so the resume test is "is it non-empty", not "-s".
    o="geom/world/area_$a"
    if [ "$FORCE" != "1" ] && [ -n "$(ls -A "$o" 2>/dev/null)" ]; then
      wskip=$((wskip+1)); continue; fi
    if "$PY" "$KIT/tools/room_explore.py" worldgen "$ROOMS" --area "$a" \
         --out "$o" --texture --albedo ${HEIGHTS:+--heights "$HEIGHTS"} \
         >>"$LOG" 2>&1; then
      wmade=$((wmade+1))
    else
      wfail=$((wfail+1)); echo "   FAILED area $a (see $LOG)"
    fi
    printf "\r   area %3s   %2d/%-2d  built %-3d skipped %-3d failed %-2d" \
      "$a" "$j" "$na" "$wmade" "$wskip" "$wfail"
  done
  echo "" | tee -a "$LOG"
  echo "   world: $wmade built, $wskip already present, $wfail failed" | tee -a "$LOG"
fi

echo "" | tee -a "$LOG"
echo "=== done in $(( $(date +%s) - t0 ))s ===" | tee -a "$LOG"
du -sh geom/* 2>/dev/null | tee -a "$LOG"
echo "log: $LOG"
