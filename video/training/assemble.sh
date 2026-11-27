#!/usr/bin/env bash
# build/partN/{frames,narration.wav,subs.srt,chapters.ffmeta} -> build/pru-simulator-training-N-<slug>.mp4
# 1920x1080, 30 fps, H.264, mono AAC 48 kHz, English mov_text subtitle track, chapter marks.
set -euo pipefail
cd "$(dirname "$0")"
part=${1:?usage: assemble.sh <part>}
dir=build/part$part
slug=$(node -p "JSON.parse(require('fs').readFileSync('$dir/cues.json','utf8')).slug")
out=build/pru-simulator-training-$part-$slug.mp4
ffmpeg -v error -stats -y \
  -framerate 30 -i "$dir/frames/%06d.png" \
  -i "$dir/narration.wav" \
  -i "$dir/subs.srt" \
  -i "$dir/chapters.ffmeta" \
  -map 0:v -map 1:a -map 2:s -map_metadata 3 -map_chapters 3 \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 128k -ar 48000 -ac 1 \
  -c:s mov_text -metadata:s:s:0 language=eng \
  -movflags +faststart "$out"
echo "wrote $out"
