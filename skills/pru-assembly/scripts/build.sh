#!/usr/bin/env bash
# Build PRU firmware with TI clpru. Run it unchanged (SKILL.md, step 5).
#
# For PRU Software Support Package targets (AM335x, AM437x, AM57x) and standalone files.
# Inside an OpenPRU checkout, use the repo makefiles instead (references/openpru.md).
#
# Usage:
#   scripts/build.sh <output.out> <linker.cmd> <source.c|source.asm>...
#
# Environment:
#   PRU_CGT      Path to TI PRU Code Generation Tools (default: /usr/share/ti/cgt-pru)
#   PRU_INCLUDE  Optional extra include directory, e.g. the support package's include/am335x
#
# Install, if clpru is missing:
#   Download "PRU Code Generation Tools" from https://www.ti.com/tool/PRU-CGT,
#   install it, then: export PRU_CGT=/path/to/ti-cgt-pru_<version>
#   BeagleBoard Debian images usually ship it at /usr/share/ti/cgt-pru.
#
# Target flags below are for AM335x. Change them once per project for another SoC,
# not per build.
set -euo pipefail

TARGET_FLAGS=(-v3 --endian=little --hardware_mac=on)
CFLAGS=("${TARGET_FLAGS[@]}" -O2 --display_error_number)

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <output.out> <linker.cmd> <source.c|source.asm>..." >&2
  exit 2
fi

OUT=$1; LINKER_CMD=$2; shift 2
PRU_CGT=${PRU_CGT:-/usr/share/ti/cgt-pru}
CLPRU="$PRU_CGT/bin/clpru"

if [[ ! -x "$CLPRU" ]]; then
  echo "clpru not found at $CLPRU." >&2
  echo "Install TI PRU Code Generation Tools (https://www.ti.com/tool/PRU-CGT) and set PRU_CGT." >&2
  exit 1
fi
[[ -f "$LINKER_CMD" ]] || { echo "Linker command file not found: $LINKER_CMD" >&2; exit 1; }

INCLUDES=(--include_path="$PRU_CGT/include")
[[ -n "${PRU_INCLUDE:-}" ]] && INCLUDES+=(--include_path="$PRU_INCLUDE")

BUILD_DIR="$(dirname "$OUT")/obj"
mkdir -p "$BUILD_DIR"

OBJECTS=()
for SRC in "$@"; do
  [[ -f "$SRC" ]] || { echo "Source not found: $SRC" >&2; exit 1; }
  OBJ="$BUILD_DIR/$(basename "${SRC%.*}").object"
  echo "Compiling $SRC"
  "$CLPRU" "${INCLUDES[@]}" "${CFLAGS[@]}" -fe "$OBJ" "$SRC"
  OBJECTS+=("$OBJ")
done

echo "Linking $OUT"
"$CLPRU" "${CFLAGS[@]}" -z -i"$PRU_CGT/lib" -i"$PRU_CGT/include" \
  -o "$OUT" -m "${OUT%.*}.map" "${OBJECTS[@]}" "$LINKER_CMD" --library=libc.a

echo "Built $OUT (map: ${OUT%.*}.map)"
