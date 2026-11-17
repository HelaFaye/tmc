#!/bin/sh
# Compile the game code for the fx-CG50 (SH-4A, big-endian, no FPU) to check
# that it builds and to measure its size. Per-file objects, not linked.
#
# usage: scripts/fxcg/sh_compile_check.sh <out-dir>
# Needs sh-elf-gcc (fxSDK) on PATH and a PC build first (xmake build tmc_pc):
# the file list is the PC build's own src/ objects, and the generated headers
# under build/USA come from it.
set -e
T=$(cd "$(dirname "$0")/../.." && pwd)
O=$(mkdir -p "$1" && cd "$1" && pwd)
LIST=$(cd "$T/build/.objs/tmc_pc" && find . -path "*/src/*.o" | sed 's|.*/release/||; s|\.o$||' | grep '^src/' | sort)
FLAGS="-m4-nofpu -mb -Os -ffunction-sections -fdata-sections -std=gnu11 -w
 -DPC_PORT -DNON_MATCHING -DUSA -DENGLISH -DREVISION=0 -DTMC_FXCG -DMULTI_REGION
 -I$T/include -I$T/build/USA -I$T/port -I$T -I$T/port/ppu/include"
ok=0; bad=0; : > "$O/failed.txt"; : > "$O/errors.log"
for f in $LIST port/data_stubs_autogen.c port/data_const_stubs.c port/port_linked_stubs.c port/stubs_autogen.c; do
    out="$O/$(echo "$f" | tr / _).o"
    if sh-elf-gcc $FLAGS -c "$T/$f" -o "$out" 2>>"$O/errors.log"; then ok=$((ok+1))
    else bad=$((bad+1)); echo "$f" >> "$O/failed.txt"; fi
done
echo "compiled $ok, failed $bad"
sh-elf-size -t "$O"/*.o | tail -1
