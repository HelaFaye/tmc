#!/bin/sh
# Host-side conversion: retail USA baserom.gba + completed ROM-touch sweeps
# -> compact PCPK page pak + checksum/metadata sidecars.
#
# usage: prepare_calculator_data.sh <USA-baserom.gba> <out.pak> <touch-dir> [...]
set -eu

if [ "$#" -lt 3 ]; then
    echo "usage: $0 <USA-baserom.gba> <out.pak> <touch-dir> [...]" >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$SCRIPT_DIR/build_page_pak.py" "$@"
