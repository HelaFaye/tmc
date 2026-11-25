#!/bin/sh
# Run the calculator configuration big-endian (qemu-ppc) and little-endian
# (native i686) on the same input and report the first frame that differs.
#
# usage: run_compare.sh <baserom.gba> <frames> <input-file> <work-dir> [ppm-every]
#   Builds both programs into <work-dir>/host-be and host-le unless
#   NO_BUILD=1, then runs each in a fresh directory (no tmc.sav: a save
#   from an earlier run changes the file-select state).
# Exit status: 0 when every frame matches.
set -e
rom=$(realpath "$1"); frames=$2; input=$(cat "$3"); work=$(realpath -m "$4"); every=${5:-1000}
here=$(dirname "$(realpath "$0")")
mkdir -p "$work"
if [ "${NO_BUILD:-0}" != 1 ]; then
    python3 "$here/build_host.py" be "$work/host-be" &
    python3 "$here/build_host.py" le "$work/host-le" &
    wait
fi
rm -rf "$work/run-be" "$work/run-le"; mkdir "$work/run-be" "$work/run-le"
(cd "$work/run-le" && "$work/host-le/picori-host" "$rom" "$frames" "$input" "$every" > out.txt 2> err.txt) &
(cd "$work/run-be" && qemu-ppc "$work/host-be/picori-host" "$rom" "$frames" "$input" "$every" > out.txt 2> err.txt) &
wait
le=$(wc -l < "$work/run-le/out.txt"); be=$(wc -l < "$work/run-be/out.txt")
first=$(diff "$work/run-le/out.txt" "$work/run-be/out.txt" | sed -n 's/^< FRAME \([0-9]*\).*/\1/p' | head -1)
echo "frames: le=$le be=$be"
if [ -z "$first" ] && [ "$le" = "$be" ]; then
    echo "RESULT=MATCH"
    exit 0
fi
echo "RESULT=DIFFER first=${first:-$be}"
exit 1
