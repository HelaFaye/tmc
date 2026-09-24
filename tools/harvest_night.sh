#!/bin/bash
# Overnight harvest. Everything here is data I cannot get from a static
# repo: sprite animation frames, and a room sweep that records the LAYER
# each object sits on. Safe to re-run; each step is skipped if its output
# already exists.
set -u
# Find the repo by walking up for a file only it has, rather than assuming
# how deep this script sits. The kit unzips as kit/ at top level, so
# "unzip -d kit" nests it one deeper and a fixed ../.. lands in the wrong
# place.
here="$(cd "$(dirname "$0")" && pwd)"
ROOT=""
d="$here"
for _ in 1 2 3 4 5 6; do
  if [ -f "$d/include/area.h" ] || [ -f "$d/xmake.lua" ]; then ROOT="$d"; break; fi
  d="$(dirname "$d")"
  [ "$d" = "/" ] && break
done
if [ -z "$ROOT" ]; then
  echo "!! could not find the tmc repo above $here" >&2
  exit 1
fi
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
fi          # the kit dir, wherever it landed
LOG="$ROOT/night.log"
echo "=== night harvest $(date) ===" | tee "$LOG"

# Check the hook COMPILES before touching the tree. Last run died because
# an inserted block landed inside an existing comment, so its own */ closed
# that comment early and the comment's prose became code ("'the' undeclared").
"$PY" "$KIT/tools/check_hook.py" 2>&1 | tee -a "$LOG"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  echo "!! hook is malformed -- not touching the tree" | tee -a "$LOG"
  exit 1
fi

"$PY" "$KIT/tools/apply_roomcap_hook.py" 2>&1 | tee -a "$LOG"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  echo "!! hook did not apply -- stopping. Building without it dumps nothing." | tee -a "$LOG"
  exit 1
fi

xmake -y 2>&1 | tail -20 | tee -a "$LOG"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  echo "!! build failed -- see $LOG" | tee -a "$LOG"
  exit 1
fi

# The target is tmc_pc. A bare "xmake run" picks xmake's default target,
# which here is a build tool (agb2mid) -- it printed its usage and the
# harvest dumped nothing.
BIN="$(find "$ROOT/build" "$ROOT/dist" -maxdepth 6 -type f -perm -u+x \
        -name 'tmc_pc*' 2>/dev/null | head -1)"
if [ -n "$BIN" ]; then
  echo "binary: $BIN" | tee -a "$LOG"
  RUN="$BIN"
else
  echo "no tmc_pc binary on disk; using 'xmake run tmc_pc'" | tee -a "$LOG"
  RUN="xmake run tmc_pc"
fi

# 1. Every sprite's animation frames. This is the blocking data: a flame's
#    shape is only legible across its cycle, and the same is true of every
#    other animated object.
# Guard on OUTPUT, not on the directory: a previous run's mkdir created
# night/anim before it failed, so the next run saw the directory, decided
# the work was done and skipped it. The whole harvest finished in 13s.
if [ -z "$(ls -A "$ROOT/night/anim" 2>/dev/null)" ]; then
  mkdir -p "$ROOT/night/anim"
  # Unbuffered and straight to the terminal: this runs in the FOREGROUND,
  # so its progress lines are the only sign it is alive. Piping through
  # tail would hide them until it finished.
  # The hook lives in the room-capture path, so the game has to be driven
  # INTO a room or it never runs. Booting it bare just reached the title and
  # quit: the log had no "[roomcap] sprite sweep" line at all. These are the
  # same vars harvest_rooms.py uses to get there. The sprite table is global,
  # so any room will do.
  TMC_AUTOPLAY=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  TMC_ROOMCAP=1 TMC_ROOMCAP_WARP="0,0,128,176,1" TMC_ROOMCAP_SETTLE=8 \
  TMC_ROOMCAP_SPRITERANGE="0:511,0,31" \
  TMC_ROOMCAP_SPRITEDIR="$ROOT/night/anim" \
  timeout 1800 $RUN 2>&1 | tee -a "$LOG"
  n_anim="$(ls "$ROOT/night/anim" 2>/dev/null | wc -l)"
  echo "anim frames: $n_anim" | tee -a "$LOG"
  if [ "$n_anim" -eq 0 ]; then
    echo "!! the sweep produced nothing. Look for a '[roomcap] sprite sweep'" | tee -a "$LOG"
    echo "!! line above: if it is absent the hook never ran, which means the" | tee -a "$LOG"
    echo "!! game did not reach a room capture. If it IS present but no files" | tee -a "$LOG"
    echo "!! landed, VrDump_SpriteRange is rejecting the sprite indices." | tee -a "$LOG"
  fi
fi

# 2. Fresh room sweep. The existing dumps are fine, but re-running is cheap
#    and guarantees the layer data matches this build.
if [ -z "$(ls -A "$ROOT/night/vrdump" 2>/dev/null)" ]; then
  mkdir -p "$ROOT/night/vrdump"
  TMC_ROOMCAP_OUT="$ROOT/night/vrdump" \
  timeout 7200 "$PY" "$KIT/tools/harvest_rooms.py" --out "$ROOT/night/vrdump" \
    2>&1 | tee -a "$LOG"
fi

# 3. Package whatever landed, so one file comes back.
cd "$ROOT" || exit 1
tar czf night_harvest.tgz night/anim night/vrdump night.log 2>/dev/null
echo "=== done $(date) ===" | tee -a "$LOG"
ls -la "$ROOT/night_harvest.tgz" 2>/dev/null | tee -a "$LOG"
du -sh "$ROOT/night"/* 2>/dev/null | tee -a "$LOG"
