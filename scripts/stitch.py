#!/usr/bin/env python3
"""Concatenate scene clips into one H.264 mp4 (Windows/macOS/Linux)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import re

EXTS = {".mp4", ".webm", ".mkv", ".mov"}
# Only primary scene-NN.ext — ignore takes like scene-01__v02.webm
PRIMARY_CLIP_RE = re.compile(r"^scene-\d+\.(mp4|webm|mkv|mov)$", re.I)


def find_clips(clips_dir: Path) -> list[Path]:
    clips = [
        p
        for p in clips_dir.iterdir()
        if p.is_file() and PRIMARY_CLIP_RE.match(p.name)
    ]
    return sorted(clips, key=lambda p: p.name.lower())


def run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        raise RuntimeError(detail)


def stitch(clips_dir: Path, output: Path) -> int:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH")
    if not clips_dir.is_dir():
        raise RuntimeError(f"Clips directory not found: {clips_dir}")

    clips = find_clips(clips_dir)
    if not clips:
        raise RuntimeError(f"No scene-*.mp4/webm/mkv/mov files in {clips_dir}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="videomake-stitch-") as tmp:
        work = Path(tmp)
        list_file = work / "list.txt"
        lines: list[str] = []
        for i, src in enumerate(clips, start=1):
            normalized = work / f"{i:02d}.mp4"
            # Normalize codecs/fps so concat -c copy is safe.
            run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(src),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "24",
                    "-movflags",
                    "+faststart",
                    str(normalized),
                ]
            )
            # ffmpeg concat demuxer wants forward slashes / escaped quotes
            path = normalized.resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{path}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Re-encode (not stream copy) so moov is at the start — browsers need this
        # without relying solely on HTTP Range.
        run_ffmpeg(
            [
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "24",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
    return len(clips)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clips_dir", nargs="?", default="output/scenes")
    parser.add_argument("output", nargs="?", default="output/final.mp4")
    args = parser.parse_args()
    try:
        n = stitch(Path(args.clips_dir), Path(args.output))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Wrote {args.output} from {n} clips")


if __name__ == "__main__":
    main()
