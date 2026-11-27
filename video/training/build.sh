#!/usr/bin/env bash
# Builds the training videos: every part that has a narration file, or only the parts named.
#
#   ./build.sh          all parts, then the coverage check over all of them
#   ./build.sh 2        part 2 only
#
# Needs: npm install, the Python venv in .venv, the Kokoro model files in build/models,
# ffmpeg, and PRU_PYTHON pointing at a Python that has the dashboard's requirements
# (default: python3). See README.md.
set -euo pipefail
cd "$(dirname "$0")"
py=".venv/bin/python"
all=$(ls narration/part*.txt | sed -E 's/.*part([0-9]+)\.txt/\1/' | sort -n | tr '\n' ' ')
parts=${*:-$all}
cfg=../../memory.cfg
before=$(md5sum < "$cfg")
# Checked on every exit, also after a failed step: the build must never change memory.cfg.
trap 'if [ "$(md5sum < "$cfg")" != "$before" ]; then echo "FAIL: the build changed $cfg" >&2; exit 1; fi' EXIT

for n in $parts; do
  echo "== part $n"
  env -u PYTHONPATH "$py" tts.py "$n"
  node capture.mjs "$n" --sheet
  node check.mjs timeline "$n"
  env -u PYTHONPATH "$py" mix.py "$n"
  ./assemble.sh "$n"
  node check.mjs output "$n"
  ./sheet.sh "$n" > /dev/null
done

if [ "$parts" = "$all" ]; then node check.mjs coverage "$(echo $all | tr ' ' ',')"; fi

echo "memory.cfg unchanged"
ls -la build/*.mp4
