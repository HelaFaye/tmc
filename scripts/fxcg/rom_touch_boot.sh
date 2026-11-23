#!/bin/sh
# Boot capture for the fx-CG50 page pak: the ROM pages the game touches from
# power-on through the title screen, file select and the start of a new file,
# read the way the calculator reads them (TMC_ROM_ONLY=1: no asset tree).
# Pass the result to build_page_pak.py with --boot=<touch.txt>.
#
# usage: rom_touch_boot.sh <tmc_pc> <baserom.gba> <out-touch.txt> [frames]
set -eu
BIN=$(realpath "$1"); ROM=$(realpath "$2"); OUT=$(realpath -m "$3"); FRAMES=${4:-6000}
RUN=$(mktemp -d)
trap 'rm -rf "$RUN"' EXIT
ln -s "$ROM" "$RUN/baserom.gba"
# Start at frame 300, then A every 150 frames: title, file select, a new
# file's name entry and the opening scenes.
IN="300:start:4"
t=400
while [ "$t" -le "$FRAMES" ]; do IN="$IN,$t:a:4"; t=$((t + 150)); done
cd "$RUN"
OMP_NUM_THREADS=1 TMC_AUTOPLAY=1 TMC_ROM_ONLY=1 \
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy TMC_TEST_INPUT="$IN" \
TMC_ROM_TOUCH_LOG="$OUT" TMC_ROM_TOUCH_FRAMES="$FRAMES" \
    timeout -s KILL 900 "$BIN" --no-audio > "$OUT.log" 2>&1 || true
grep '^\[ROM touch\]' "$OUT.log" || { echo "boot capture failed; see $OUT.log" >&2; exit 1; }
