#!/bin/sh
# ROM working-set sweep for the fx-CG50 port plan (docs/fx-cg50-port-plan.md).
#
# usage: rom_touch_sweep.sh <tmc_pc> <baserom.gba> <outdir> [frames] [jobs] [area:room ...]
#   area:room  hex pairs to visit (default: room 0 of every area 0x00-0x8f)
#
# Each run boots a fresh game headless (TMC_ROOMCAP), warps to the room and
# logs every ROM page it touches (TMC_ROM_TOUCH_LOG, see port/port_rom_touch.h)
# for <frames> game frames. Summarise with rom_touch_report.py <outdir>.
set -e
if [ "$1" = "--one" ]; then
    # Internal: one run. args: --one <bin> <rom> <out> <frames> <area:room>
    BIN=$2; ROM=$3; OUT=$4; FRAMES=$5; A=${6%%:*}; R=${6##*:}
    D="$OUT/run-$A-$R"
    mkdir -p "$D"
    ln -sf "$ROM" "$D/baserom.gba"
    cd "$D"
    OMP_NUM_THREADS=1 TMC_AUTOPLAY=1 TMC_ROOMCAP=1 \
    TMC_ROOMCAP_WARP="$A,$R,0x80,0x80,1" TMC_ROOMCAP_SETTLE=100000 \
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
    TMC_ROM_TOUCH_LOG="$OUT/touch-$A-$R.txt" TMC_ROM_TOUCH_FRAMES="$FRAMES" \
        timeout -s KILL 300 "$BIN" --no-audio > "$OUT/log-$A-$R.txt" 2>&1 || true
    exit 0
fi
BIN=$(realpath "$1"); ROM=$(realpath "$2"); OUT=$(realpath -m "$3")
FRAMES=${4:-1200}; JOBS=${5:-4}
shift $(( $# < 5 ? $# : 5 ))
mkdir -p "$OUT"
if [ $# -eq 0 ]; then
    set -- $(i=0; while [ $i -lt 144 ]; do printf "0x%02x:0x00 " $i; i=$((i+1)); done)
fi
SELF=$(realpath "$0")
for room in "$@"; do echo "$room"; done |
    xargs -P "$JOBS" -I{} "$SELF" --one "$BIN" "$ROM" "$OUT" "$FRAMES" {}
