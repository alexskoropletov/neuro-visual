#!/usr/bin/env bash
# Concatenate scene clips into one H.264 mp4.
set -euo pipefail

CLIPS_DIR="${1:-output/scenes}"
OUTPUT="${2:-output/final.mp4}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found in PATH" >&2
  exit 1
fi

if [[ ! -d "$CLIPS_DIR" ]]; then
  echo "Clips directory not found: $CLIPS_DIR" >&2
  exit 1
fi

mapfile -t CLIPS < <(find "$CLIPS_DIR" -maxdepth 1 -type f \
  \( -iname 'scene-*.mp4' -o -iname 'scene-*.webm' -o -iname 'scene-*.mkv' -o -iname 'scene-*.mov' \) \
  | sort)

if [[ ${#CLIPS[@]} -eq 0 ]]; then
  echo "No scene-*.mp4/webm/mkv/mov files in $CLIPS_DIR" >&2
  exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

LIST="$WORKDIR/list.txt"
i=0
for src in "${CLIPS[@]}"; do
  i=$((i + 1))
  normalized="$WORKDIR/$(printf '%02d' "$i").mp4"
  ffmpeg -y -i "$src" -an \
    -c:v libx264 -pix_fmt yuv420p -r 24 -movflags +faststart \
    "$normalized" </dev/null
  printf "file '%s'\n" "$normalized" >>"$LIST"
done

mkdir -p "$(dirname "$OUTPUT")"
ffmpeg -y -f concat -safe 0 -i "$LIST" -c copy "$OUTPUT"
echo "Wrote $OUTPUT from ${#CLIPS[@]} clips"
