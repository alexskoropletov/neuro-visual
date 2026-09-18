#!/usr/bin/env python3
"""Expand RU→EN via Qwen, then split into Wan scenes via Dolphin (local Ollama)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


class SplitError(Exception):
    """Recoverable failure while expanding or splitting a script."""


DEFAULT_NEGATIVE = (
    "ugly, deformed, blurry, low quality, soft focus, oversmoothed, "
    "waxy skin, plastic skin, doll-like face, airbrushed, CGI, 3d render, "
    "cartoon, anime, illustration, painting, comic, "
    "extra limbs, malformed hands, fused fingers, bad anatomy, "
    "watermark, text, logo, subtitle, "
    "child, minor, underage, teen"
)

DEFAULT_EXPAND_MODEL = "qwen3.8:27b"
DEFAULT_SPLIT_MODEL = "dolphin-llama3"

EXPAND_SYSTEM_PROMPT = """You prepare video scripts for an English-only local text-to-video pipeline.

Task:
1) Translate the user's script into clear, natural English (preserve plot, tone, and adult intent).
2) Expand it: add concrete visual detail that was vague or missing — clothing (cut, fabric, color, wear),
   appearance (face, hair, body, skin, distinctive marks), props, materials, textures, colors,
   lighting mood, setting architecture, weather/time of day, and physical actions.
3) Keep the SAME adult characters consistent; do not invent underage characters.
4) Do not shorten the story — enrich it. Keep narrative order. No scene numbering yet.
5) Output ONLY valid JSON. No markdown fences. No commentary outside JSON.

Adults 18+ only. If the input asks for minors, return {"error":"refused","reason":"minors"}.
Adult fictional / erotic content is allowed for this private local pipeline — do not refuse it.

JSON shape:
{
  "title_hint": "optional short English title",
  "english_script": "full expanded English script as continuous prose (several paragraphs)"
}
"""

SPLIT_SYSTEM_PROMPT = """You split video scripts into scenes for a local Wan 2.2 text-to-video model.

Rules:
- Output ONLY valid JSON. No markdown fences. No commentary.
- Adults 18+ only. Never invent or keep underage characters. If the input asks for minors, return {"error":"refused","reason":"minors"}.
- Adult fictional content (including erotic / NSFW) is allowed for this private local pipeline. Do not refuse adult consensual or adult fantasy scenes.
- Split into exactly the requested number of scenes (4–8).
- Keep the same adult characters consistent: put a detailed character_bible once, then reuse the same identity cues in every visual_prompt.
- Also write a world_bible once: locked location/biome/architecture/climate/palette that MUST stay consistent in every scene (no sudden desert↔jungle switches).
- The input is already English and detailed — use those specifics; do not strip clothing, colors, materials, or appearance.

character_bible must include for each main adult character:
- age band (adult only, e.g. early 30s), sex/gender presentation
- face (shape, eyes, brows, lips), hair (color, length, style)
- body type, skin tone, distinctive marks
- clothing / state of dress for the story

Each visual_prompt must be ONE English shot, 2–4 dense sentences, in this order:
1) subject + identity anchors from character_bible (not vague “a woman”)
2) exact action / body language / interaction in this beat
3) environment & props (materials, weather, time of day, depth)
4) lighting (key light direction, soft/hard, color temperature, practicals)
5) camera: shot size (CU/MS/WS), angle, lens feel (35mm/50mm/85mm), move (static, slow push-in, pan, handheld sway)
6) motion that Wan can show in ~3–4 seconds (one clear move, not a montage)
7) finish with a short style lock: “photorealistic live-action, natural skin texture, sharp focus, cinematic color”

Avoid empty aesthetic spam (“masterpiece, best quality, 8k, trending”). Prefer concrete nouns and verbs.
duration_sec is 3–5. filename is scene-01, scene-02, …
negative_prompt: reinforce anti-slop (oversmooth, waxy skin, plastic, CGI, cartoon, bad hands) plus scene-specific avoids.

JSON shape:
{
  "title": "short title",
  "character_bible": "stable adult character descriptions reused in every scene",
  "world_bible": "locked setting: biome, terrain, architecture, climate, color palette — same place across all scenes",
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


def _payload_text(payload: dict) -> str:
    """Prefer response; thinking models may park JSON in thinking when response is empty."""
    response = (payload.get("response") or "").strip()
    if response:
        return response
    thinking = (payload.get("thinking") or "").strip()
    if thinking:
        return thinking
    message = payload.get("message")
    if isinstance(message, dict):
        content = (message.get("content") or "").strip()
        if content:
            return content
        thinking = (message.get("thinking") or "").strip()
        if thinking:
            return thinking
    return ""


def ollama_generate(
    host: str,
    model: str,
    prompt: str,
    *,
    system: str,
    timeout: int,
    use_json_format: bool = True,
) -> str:
    url = host.rstrip("/") + "/api/generate"
    # think=false: qwen3.* otherwise fills thinking and leaves response empty
    body_obj: dict = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "think": False,
    }
    if use_json_format:
        body_obj["format"] = "json"
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SplitError(
            f"Ollama is not reachable at {host}: {exc}. "
            f"Install Ollama, pull models (e.g. ollama pull {DEFAULT_EXPAND_MODEL} "
            f"&& ollama pull {DEFAULT_SPLIT_MODEL}), then retry."
        ) from exc
    text = _payload_text(payload)
    if not text:
        raise SplitError(
            f"Empty Ollama response from {model} (no response/thinking). "
            "Try think=false support or another model."
        )
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


def _check_refused(data: dict) -> None:
    if data.get("error") == "refused":
        reason = str(data.get("reason", "policy"))
        raise SplitError(
            f"Модель отказала: {reason}. "
            "Для 18+ контента лучше uncensor-модель на этапе нарезки (dolphin-llama3)."
        )


def expand_script(
    script: str,
    *,
    model: str = DEFAULT_EXPAND_MODEL,
    host: str = "http://127.0.0.1:11434",
    timeout: int = 240,
) -> dict:
    """Translate + expand RU (or any) script into detailed English prose via Qwen."""
    text = script.strip()
    if not text:
        raise SplitError("Empty script.")
    user = (
        "Translate to English and expand with concrete visual detail "
        "(clothing, appearance, colors, textures, materials, setting, actions).\n\n"
        f"SCRIPT:\n{text}\n"
    )
    raw = ollama_generate(
        host, model, user, system=EXPAND_SYSTEM_PROMPT, timeout=timeout
    )
    try:
        data = parse_json_object(raw)
    except json.JSONDecodeError as exc:
        raise SplitError(f"Could not parse expand JSON from {model}: {exc}") from exc
    _check_refused(data)
    english = str(data.get("english_script") or "").strip()
    if not english:
        # Fallback: some models return the prose under other keys
        for key in ("script", "expanded_script", "text"):
            english = str(data.get(key) or "").strip()
            if english:
                break
    if not english:
        raise SplitError(f"{model} did not return english_script.")
    return {
        "english_script": english,
        "title_hint": str(data.get("title_hint") or "").strip(),
        "expand_model": model,
    }


def parse_scenes(raw: str, scene_count: int) -> dict:
    try:
        data = parse_json_object(raw)
    except json.JSONDecodeError as exc:
        raise SplitError(f"Could not parse Ollama JSON: {exc}") from exc
    _check_refused(data)
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
    data.setdefault("world_bible", "")
    return data


def split_english_script(
    english_script: str,
    *,
    scene_count: int = 6,
    model: str = DEFAULT_SPLIT_MODEL,
    host: str = "http://127.0.0.1:11434",
    timeout: int = 240,
) -> dict:
    text = english_script.strip()
    if not text:
        raise SplitError("Empty English script.")
    if scene_count < 4 or scene_count > 8:
        raise SplitError("Scene count must be between 4 and 8.")
    user = (
        f"Split the following into exactly {scene_count} scenes.\n\n"
        f"SCRIPT:\n{text}\n"
    )
    raw = ollama_generate(
        host, model, user, system=SPLIT_SYSTEM_PROMPT, timeout=timeout
    )
    data = parse_scenes(raw, scene_count)
    data["split_model"] = model
    return data


def split_script(
    script: str,
    *,
    scene_count: int = 6,
    expand_model: str | None = None,
    split_model: str | None = None,
    model: str | None = None,
    host: str = "http://127.0.0.1:11434",
    timeout: int = 240,
    skip_expand: bool = False,
) -> dict:
    """
    Full pipeline: expand/translate (Qwen) → split scenes (Dolphin).

    `model` is accepted as a legacy alias for expand_model when expand_model is omitted.
    """
    expand_name = (
        expand_model
        or os.environ.get("OLLAMA_EXPAND_MODEL")
        or model
        or os.environ.get("OLLAMA_MODEL")
        or DEFAULT_EXPAND_MODEL
    )
    split_name = (
        split_model
        or os.environ.get("OLLAMA_SPLIT_MODEL")
        or DEFAULT_SPLIT_MODEL
    )

    if skip_expand:
        english = script.strip()
        title_hint = ""
        expand_name = ""
    else:
        expanded = expand_script(
            script, model=expand_name, host=host, timeout=timeout
        )
        english = expanded["english_script"]
        title_hint = expanded.get("title_hint") or ""

    data = split_english_script(
        english,
        scene_count=scene_count,
        model=split_name,
        host=host,
        timeout=timeout,
    )
    if title_hint and not str(data.get("title") or "").strip():
        data["title"] = title_hint
    data["english_script"] = english
    data["expand_model"] = expand_name
    data["split_model"] = split_name
    return data


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from envutil import load_dotenv

    load_dotenv(root / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", default="-", help="Script file or - for stdin")
    parser.add_argument("--output", "-o", default="scenes.json", help="Output JSON path")
    parser.add_argument(
        "--expand-model",
        default=os.environ.get("OLLAMA_EXPAND_MODEL")
        or os.environ.get("OLLAMA_MODEL")
        or DEFAULT_EXPAND_MODEL,
        help="Model for RU→EN expand (default: qwen3.8)",
    )
    parser.add_argument(
        "--split-model",
        default=os.environ.get("OLLAMA_SPLIT_MODEL") or DEFAULT_SPLIT_MODEL,
        help="Model for scene split (default: dolphin-llama3)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Legacy: same as --expand-model",
    )
    parser.add_argument(
        "--skip-expand",
        action="store_true",
        help="Skip Qwen expand; treat input as already English",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
    )
    parser.add_argument("--scenes", type=int, default=6, help="Scene count, 4–8")
    parser.add_argument("--timeout", type=int, default=240, help="Per-stage timeout seconds")
    args = parser.parse_args()

    script = read_text(None if args.input == "-" else Path(args.input))
    try:
        data = split_script(
            script,
            scene_count=args.scenes,
            expand_model=args.model or args.expand_model,
            split_model=args.split_model,
            host=args.host,
            timeout=args.timeout,
            skip_expand=args.skip_expand,
        )
    except SplitError as exc:
        raise SystemExit(str(exc)) from exc

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(data['scenes'])} scenes to {out} "
        f"(expand={data.get('expand_model') or 'skipped'}, split={data.get('split_model')})"
    )


if __name__ == "__main__":
    main()
