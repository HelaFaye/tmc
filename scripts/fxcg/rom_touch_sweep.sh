#!/bin/sh
# ROM working-set sweep for the fx-CG50 port plan (docs/fx-cg50-port-plan.md).
#
# usage: rom_touch_sweep.sh <tmc_pc> <baserom.gba> <outdir> [frames] [jobs] [area:room ...]
#   area:room  hexadecimal area:room pair; defaults to room 0 for areas 0x00-0x8f
#
# Each run boots a fresh post-intro game headless, warps to the requested room,
# verifies the settled destination, and records ROM pages for <frames> ticks.
# Summarise with rom_touch_report.py <outdir>.
set -eu

usage() {
    echo "usage: $0 <tmc_pc> <baserom.gba> <outdir> [frames] [jobs] [area:room ...]" >&2
    exit 2
}

if [ "$#" -lt 3 ]; then
    usage
fi

if [ "$1" = "--one" ]; then
    # Internal: --one <bin> <rom> <out> <frames> <area:room>
    if [ "$#" -ne 6 ]; then
        usage
    fi
    BIN=$2
    ROM=$3
    OUT=$4
    FRAMES=$5
    ROOM=$6
    case "$ROOM" in
        *:*) A=${ROOM%%:*}; R=${ROOM#*:} ;;
        *) echo "invalid area:room value: $ROOM" >&2; exit 2 ;;
    esac
    if [ -z "$A" ] || [ -z "$R" ]; then
        echo "invalid area:room value: $ROOM" >&2
        exit 2
    fi

    D="$OUT/run-$A-$R"
    LOG="$OUT/log-$A-$R.txt"
    TOUCH="$OUT/touch-$A-$R.txt"
    STATUS="$OUT/status-$A-$R.txt"
    mkdir -p "$D"
    # Do not let a previous invocation's ROM page cache contaminate load data.
    rm -rf "$D/rom_data"
    rm -f "$LOG" "$TOUCH" "$STATUS"
    ln -sf "$ROM" "$D/baserom.gba"
    cd "$D"

    if timeout --signal=TERM --kill-after=5 300 \
        env OMP_NUM_THREADS=1 TMC_AUTOPLAY=1 TMC_ROOMCAP=1 \
        TMC_ROOMCAP_POSTINTRO=1 \
        TMC_ROOMCAP_WARP="$A,$R,0x80,0x80,1" TMC_ROOMCAP_SETTLE=100000 \
        SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
        TMC_ROM_TOUCH_LOG="$TOUCH" TMC_ROM_TOUCH_FRAMES="$FRAMES" \
        "$BIN" --no-audio > "$LOG" 2>&1; then
        RC=0
    else
        RC=$?
    fi
    printf '%s\n' "$RC" > "$STATUS"
    if [ "$RC" -ne 0 ]; then
        echo "room $A:$R failed (exit $RC); see $LOG" >&2
        exit "$RC"
    fi
    exit 0
fi

BIN=$(realpath "$1")
ROM=$(realpath "$2")
OUT=$(realpath -m "$3")
FRAMES=${4:-1200}
JOBS=${5:-4}
case "$FRAMES" in ''|*[!0-9]*) echo "frames must be a positive integer" >&2; exit 2 ;; esac
case "$JOBS" in ''|*[!0-9]*) echo "jobs must be a positive integer" >&2; exit 2 ;; esac
if [ "$FRAMES" -le 0 ] || [ "$JOBS" -le 0 ]; then
    echo "frames and jobs must be positive integers" >&2
    exit 2
fi
shift $(( $# < 5 ? $# : 5 ))
mkdir -p "$OUT"
if [ "$#" -eq 0 ]; then
    set -- $(i=0; while [ "$i" -lt 144 ]; do printf "0x%02x:0x00 " "$i"; i=$((i+1)); done)
fi
SELF=$(realpath "$0")
for room in "$@"; do printf '%s\n' "$room"; done |
    xargs -P "$JOBS" -I{} "$SELF" --one "$BIN" "$ROM" "$OUT" "$FRAMES" {}
