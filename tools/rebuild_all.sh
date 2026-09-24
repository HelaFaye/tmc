#!/bin/bash
# Rebuild every object manifest and every fitted mesh from the current room
# dumps, using the current techniques, then verify the result by SIZE.
#
# Safe to re-run: it regenerates from the dumps each time and overwrites its
# own outputs. It does not touch the game tree or the port.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
# An explicit root wins: the kit and the game tree are not always nested
# (they were during development, and the walk-up below quietly assumed it).
ROOT="${TMC_ROOT:-}"; d="$here"
if [ -n "$ROOT" ]; then d="$ROOT"; fi
if [ -z "$ROOT" ]; then
for _ in 1 2 3 4 5 6; do
  if [ -f "$d/include/area.h" ] || [ -f "$d/xmake.lua" ]; then ROOT="$d"; break; fi
  d="$(dirname "$d")"; [ "$d" = "/" ] && break
done
fi
[ -z "$ROOT" ] && { echo "!! no tmc repo above $here; set TMC_ROOT" >&2; exit 1; }
cd "$ROOT" || exit 1
KIT="$(cd "$here/.." && pwd)"

if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then PY="$VIRTUAL_ENV/bin/python"
else PY="python3"; fi
echo "python: $PY"
"$PY" -c "import numpy, PIL" 2>/dev/null || {
  echo "!! $PY lacks numpy/pillow" >&2; exit 1; }

# Which dumps? Prefer a state capture if one exists, else the plain one.
ROOMS="${TMC_ROOMS:-}"
if [ -z "$ROOMS" ]; then
  for c in "$ROOT/states/p0" "$ROOT/night/vrdump" "$ROOT/vrdump"; do
    if [ -n "$(ls -A "$c" 2>/dev/null)" ]; then ROOMS="$c"; break; fi
  done
fi
[ -z "$ROOMS" ] && { echo "!! no room dumps found" >&2; exit 1; }
LOG="$ROOT/rebuild.log"
echo "=== rebuild $(date) ===" | tee "$LOG"
echo "rooms: $ROOMS ($(ls "$ROOMS" | wc -l) files)" | tee -a "$LOG"

mkdir -p "$ROOT/objects" "$ROOT/geom"

echo "-- manifests" | tee -a "$LOG"
"$PY" "$KIT/tools/mk_manifests.py" --rooms "$ROOMS" --out "$ROOT/objects" 2>&1 \
  | grep -v "^  [a-z]* *[0-9]*%" | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -ne 0 ] && { echo "!! manifests failed" | tee -a "$LOG"; exit 1; }

echo "-- verifying sizes BEFORE meshing (exit status is not geometry)" | tee -a "$LOG"
"$PY" "$KIT/tools/verify_fits.py" "$ROOT"/objects/*.scene --rooms "$ROOMS" 2>&1 | tee -a "$LOG"
VER=${PIPESTATUS[0]}

echo "-- meshing" | tee -a "$LOG"
for m in "$ROOT"/objects/*.scene; do
  n="$(basename "$m" .scene)"
  echo "   $n" | tee -a "$LOG"
  "$PY" "$KIT/tools/shapefit.py" scene "$m" --rooms "$ROOMS" \
      --out "$ROOT/geom/$n.obj" 2>&1 | tail -3 | tee -a "$LOG"
done

cd "$ROOT" || exit 1
tar czf rebuild.tgz objects geom rebuild.log 2>/dev/null
echo "=== done $(date) ===" | tee -a "$LOG"
ls -la "$ROOT/rebuild.tgz" | tee -a "$LOG"
du -sh "$ROOT"/geom/*.obj 2>/dev/null | tee -a "$LOG"
[ "$VER" -ne 0 ] && echo "!! verification flagged more than 5% undersized -- read above" | tee -a "$LOG"
exit 0
