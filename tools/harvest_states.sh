#!/bin/bash
# Capture the whole world at every story state.
#
# Art is state-dependent. flags.h names individual torches ("Lit Top Left
# Torch in Temple of Droplets Dark Lantern Maze"), chests that have been
# opened, doors, and events -- so a single pristine capture shows exactly
# one version of the world. Differencing two states isolates the thing the
# flag controls: fire vs bare torch base, chest present vs absent.
#
# That also explains the 16 chest-typed cells that drew bare sand or snow:
# they are state-dependent, not missing.
#
# Long running by design: roughly 20 minutes per state, 7 states.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
ROOT=""; d="$here"
for _ in 1 2 3 4 5 6; do
  if [ -f "$d/include/area.h" ] || [ -f "$d/xmake.lua" ]; then ROOT="$d"; break; fi
  d="$(dirname "$d")"; [ "$d" = "/" ] && break
done
[ -z "$ROOT" ] && { echo "!! no tmc repo above $here" >&2; exit 1; }
cd "$ROOT" || exit 1
KIT="$(cd "$here/.." && pwd)"

# Prefer the repo venv when there is one, so the script behaves the same
# whichever terminal launches it. The kit only needs numpy and Pillow.
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PY="$VIRTUAL_ENV/bin/python"
else
  PY="python3"
fi
echo "python: $PY"
if ! "$PY" -c "import numpy, PIL" 2>/dev/null; then
  echo "!! $PY is missing numpy and/or pillow" >&2
  echo "   python3 -m venv .venv && source .venv/bin/activate && pip install numpy pillow" >&2
  exit 1
fi
LOG="$ROOT/states.log"
echo "=== state harvest $(date) ===" | tee "$LOG"

"$PY" "$KIT/tools/check_hook.py" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -ne 0 ] && { echo "!! hook malformed" | tee -a "$LOG"; exit 1; }
"$PY" "$KIT/tools/apply_roomcap_hook.py" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -ne 0 ] && { echo "!! hook did not apply" | tee -a "$LOG"; exit 1; }
xmake -y 2>&1 | tail -8 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -ne 0 ] && { echo "!! build failed" | tee -a "$LOG"; exit 1; }

BIN="$(find "$ROOT/build" "$ROOT/dist" -maxdepth 6 -type f -perm -u+x -name 'tmc_pc*' 2>/dev/null | head -1)"
[ -z "$BIN" ] && { echo "!! no tmc_pc binary" | tee -a "$LOG"; exit 1; }
echo "binary: $BIN" | tee -a "$LOG"

for P in 0 1 2 3 4 5 6; do
  OUT="$ROOT/states/p$P"
  if [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
    echo "-- progress $P already has $(ls "$OUT" | wc -l) rooms, skipping" | tee -a "$LOG"
    continue
  fi
  mkdir -p "$OUT"
  echo "== progress $P -> $OUT   $(date +%H:%M:%S)" | tee -a "$LOG"
  TMC_ROOMCAP_PROGRESS="$P" \
  timeout 5400 "$PY" "$KIT/tools/harvest_rooms.py" --out "$OUT" 2>&1 \
    | grep -E "^\s*\[|ok=|failed" | tail -5 | tee -a "$LOG"
  echo "   progress $P: $(ls "$OUT" 2>/dev/null | wc -l) rooms" | tee -a "$LOG"
done

cd "$ROOT" || exit 1
tar czf states_harvest.tgz states states.log 2>/dev/null
echo "=== done $(date) ===" | tee -a "$LOG"
ls -la "$ROOT/states_harvest.tgz" | tee -a "$LOG"
du -sh "$ROOT/states"/* 2>/dev/null | tee -a "$LOG"
