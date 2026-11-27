#!/usr/bin/env bash
# build/partN/sheet/*.png -> build/partN/contact/NN.jpg, four stills per page with the cue key burnt in.
set -euo pipefail
cd "$(dirname "$0")"
part=${1:?usage: sheet.sh <part>}
dir=build/part$part
rm -rf "$dir/contact" "$dir/contact-tmp"; mkdir -p "$dir/contact" "$dir/contact-tmp"
i=0
for f in "$dir"/sheet/*.png; do
  name=$(basename "$f" .png)
  ffmpeg -v error -y -i "$f" -vf "scale=960:540:flags=lanczos,drawbox=x=0:y=0:w=iw:h=26:color=black@0.75:t=fill,drawtext=text='$name':x=8:y=5:fontsize=17:fontcolor=white" "$dir/contact-tmp/$(printf %03d $i).png"
  i=$((i+1))
done
ffmpeg -v error -y -framerate 1 -i "$dir/contact-tmp/%03d.png" -vf "tile=2x2:padding=4:color=white" -q:v 3 "$dir/contact/%02d.jpg"
rm -rf "$dir/contact-tmp"
ls "$dir/contact" | wc -l
