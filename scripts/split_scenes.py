#!/usr/bin/env python3
"""Split a script into Wan visual-prompt scenes via local Ollama."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


class SplitError(Exception):
    """Recoverable failure while splitting a script."""


DEFAULT_NEGATIVE = (
    "ugly, deformed, blurry, low quality, cartoon, illustration, "
    "unrealistic skin, extra limbs, malformed hands, watermark, text, "
    "bad anatomy, missing fingers, fused limbs, child, minor, underage"
)

SYSTEM_PROMPT = """You split video scripts into scenes for a local Wan 2.2 text-to-video model.

Rules:
- Output ONLY valid JSON. No markdown fences.
- Adults 18+ only. Never invent or keep underage characters. If the input asks for minors, return {"error":"refused","reason":"minors"}.
- Split into exactly the requested number of scenes (4–8).
- Keep the same adult characters consistent in every visual_prompt (hair, body, clothes, age as adult).
- visual_prompt is English, concrete, cinematic: subject, setting, lighting, camera, motion. One shot per scene.
- duration_sec is 3–5.
- filename is scene-01, scene-02, …

JSON shape:
{
  "title": "short title",
  "character_bible": "stable adult character descriptions reused in every scene",
  "scenes": [
    {
      "id": 1,
      "filename": "scene-01",
      "visual_prompt": "...",
      "negative_prompt": "...",
      "duration_sec": 4,
      "notes": "optional"
    }
  ]
}
"""


def read_text(path: Path | None) -> str:
    if path is None or str(path) == "-":
        return sys.stdin.read()
    return path.read_text(encoding="utf-8")


def ollama_generate(host: str, model: str, prompt: str, timeout: int) -> str:
    url = host.rstrip("/") + "/api/generate"
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
            "format": "json",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SplitError(
            f"Ollama is not reachable at {host}: {exc}. "
            "Install Ollama, pull a model (e.g. ollama pull dolphin-llama3), then retry."
        ) from exc
    text = payload.get("response", "")
    if not text:
        raise SplitError(f"Empty Ollama response: {payload!r}")
    return text


def parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise SplitError("Ollama JSON root must be an object.")
    return data


def parse_scenes(raw: str, scene_count: int) -> dict:
    try:
        data = parse_json_object(raw)
    except json.JSONDecodeError as exc:
        raise SplitError(f"Could not parse Ollama JSON: {exc}") from exc
    if data.get("error") == "refused":
        raise SplitError(f"Refused: {data.get('reason', 'policy')}")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SplitError("Model did not return a scenes list.")
    cleaned = []
    for i, scene in enumerate(scenes[:scene_count], start=1):
        if not isinstance(scene, dict):
            continue
        filename = scene.get("filename") or f"scene-{i:02d}"
        cleaned.append(
            {
                "id": i,
                "filename": filename,
                "visual_prompt": str(scene.get("visual_prompt", "")).strip(),
                "negative_prompt": str(scene.get("negative_prompt") or DEFAULT_NEGATIVE).strip(),
                "duration_sec": int(scene.get("duration_sec") or 4),
                "notes": str(scene.get("notes") or "").strip(),
            }
        )
        if not cleaned[-1]["visual_prompt"]:
            raise SplitError(f"Scene {i} has an empty visual_prompt.")
    if not cleaned:
        raise SplitError("No usable scenes in model output.")
    data["scenes"] = cleaned
    data.setdefault("title", "")
    data.setdefault("character_bible", "")
    return data


def split_script(
    script: str,
    *,
    scene_count: int = 6,
    model: str = "dolphin-llama3",
    host: str = "http://127.0.0.1:11434",
    timeout: int = 180,
) -> dict:
    text = script.strip()
    if not text:
        raise SplitError("Empty script.")
    if scene_count < 4 or scene_count > 8:
        raise SplitError("Scene count must be between 4 and 8.")
    user = (
        f"Split the following into exactly {scene_count} scenes.\n\n"
        f"SCRIPT:\n{text}\n"
    )
    raw = ollama_generate(host, model, user, timeout)
    return parse_scenes(raw, scene_count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", default="-", help="Script file or - for stdin")
    parser.add_argument("--output", "-o", default="scenes.json", help="Output JSON path")
    parser.add_argument("--model", default="dolphin-llama3")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--scenes", type=int, default=6, help="Scene count, 4–8")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    script = read_text(None if args.input == "-" else Path(args.input))
    try:
        data = split_script(
            script,
            scene_count=args.scenes,
            model=args.model,
            host=args.host,
            timeout=args.timeout,
        )
    except SplitError as exc:
        raise SystemExit(str(exc)) from exc

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['scenes'])} scenes to {out}")


if __name__ == "__main__":
    main()
