"""Leonardo.ai still-image client for scene reference frames."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://cloud.leonardo.ai/api/rest/v1"

# Leonardo Kino XL — cinematic, good for video start frames
DEFAULT_MODEL_ID = "aa77f04e-3eec-4034-9c07-d0f619684628"

# Curated models for reference stills (id, label)
REF_MODELS: list[tuple[str, str]] = [
    ("aa77f04e-3eec-4034-9c07-d0f619684628", "Leonardo Kino XL (cinema)"),
    ("de7d3faf-762f-48e0-b3b7-9d0ac3a3fcf3", "Phoenix 1.0"),
    ("5c232a9e-9061-4777-980a-ddc8e65647c6", "Leonardo Vision XL"),
    ("1e60896f-3c26-4296-8ecc-53e2afecc132", "Leonardo Diffusion XL"),
    ("b24e16ff-06e3-43eb-8d33-4416c2d75876", "Leonardo Lightning XL"),
    ("1dd50843-d653-4516-a8e3-f0238ee453ff", "Flux Schnell (fast)"),
    ("b2614463-296c-462a-9586-aafdb8f00e36", "Flux Dev"),
    ("7b592283-e8a7-4c5a-9ba6-d18c31f258b9", "Lucid Origin"),
    ("05ce0082-2d80-4a2d-8653-4d1c85e2418e", "Lucid Realism"),
    ("e71a1c2f-4f80-4800-934f-2c68979d8cc8", "Leonardo Anime XL"),
]

SAFE_STEM = re.compile(r"^scene-\d{2}$")


class LeonardoError(RuntimeError):
    pass


def api_key() -> str:
    return (os.environ.get("LEONARDO_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def model_id() -> str:
    return (os.environ.get("LEONARDO_MODEL_ID") or DEFAULT_MODEL_ID).strip()


def list_ref_models() -> list[dict]:
    current = model_id()
    items = [{"id": mid, "name": label} for mid, label in REF_MODELS]
    if current and current not in {m["id"] for m in items}:
        items.insert(0, {"id": current, "name": f"Custom ({current[:8]}…)"})
    return items


def _request(method: str, path: str, payload: dict | None = None, timeout: int = 60) -> dict:
    key = api_key()
    if not key:
        raise LeonardoError("LEONARDO_API_KEY не задан в .env")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LeonardoError(f"Leonardo HTTP {exc.code}: {body[:400]}") from exc
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def pick_size(width: int | None = None, height: int | None = None) -> tuple[int, int]:
    """Leonardo-friendly cinematic size (multiples of 8, within common limits)."""
    w = int(width or os.environ.get("LEONARDO_WIDTH") or os.environ.get("WAN_WIDTH") or 1024)
    h = int(height or os.environ.get("LEONARDO_HEIGHT") or os.environ.get("WAN_HEIGHT") or 576)
    # Prefer 16:9; 1024×576 is widely accepted across XL models
    if w >= h:
        return 1024, 576
    return 576, 1024


# Aliases help match bible labels to wording in visual_prompt
_CHAR_ALIASES: dict[str, tuple[str, ...]] = {
    "biker": ("biker", "rider", "motorcyclist", "motorcycle rider", "bike rider", "protagonist"),
    "rider": ("rider", "biker", "motorcyclist", "motorcycle rider"),
    "motorcyclist": ("motorcyclist", "biker", "rider", "motorcycle rider"),
    "protagonist": ("protagonist", "main character", "hero", "heroine"),
    "tribe": ("tribe", "tribal", "tribeswomen", "tribesmen"),
    "man": ("man", "male", "guy"),
    "woman": ("woman", "female", "lady"),
    "women": ("women", "woman", "females", "ladies", "tribe"),
    "girl": ("girl", "woman", "female"),
    "boy": ("boy", "man", "male"),
    "driver": ("driver", "motorist"),
    "hunter": ("hunter",),
    "soldier": ("soldier", "trooper"),
    "doctor": ("doctor", "physician"),
    "nurse": ("nurse",),
    "cop": ("cop", "police", "officer"),
    "officer": ("officer", "cop", "police"),
}

# Too generic to match a scene by themselves
_WEAK_ALIASES = frozenset(
    {"man", "male", "guy", "woman", "female", "girl", "boy", "lady", "ladies", "person", "people"}
)

_VERB = r"(?:is|are|has|have|wears|wear|consists|include|includes|contain|contains|feature|features)"


def parse_character_bible(bible: str) -> list[dict]:
    """Split project character_bible into per-character entries."""
    text = (bible or "").strip()
    if not text:
        return []

    split_re = re.compile(
        rf"(?=(?:^|[.!?]\s+)(?:"
        rf"The\s+[\w\-]+(?:\s+[\w\-]+){{0,4}}\s+{_VERB}\b"
        rf"|[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){{0,2}}\s*:"
        rf"|[A-Z][\w\-]+\s+{_VERB}\b"
        rf"))",
        re.MULTILINE,
    )
    chunks = [c.strip(" .\n") for c in split_re.split(text) if c and c.strip(" .\n")]
    if len(chunks) <= 1:
        soft = re.split(r"(?<=[.!?])\s+(?=The\s+[\w])", text)
        chunks = [c.strip(" .\n") for c in soft if c and c.strip(" .\n")] or [text]

    entries: list[dict] = []
    label_re = re.compile(
        rf"^(?:The\s+)?([A-Za-z][\w\-]+(?:\s+[A-Za-z][\w\-]+){{0,4}})\s*(?::|(?:\s+{_VERB}\b))",
        re.IGNORECASE,
    )
    for chunk in chunks:
        m = label_re.match(chunk)
        if not m:
            if entries:
                entries[-1]["text"] = (entries[-1]["text"] + " " + chunk).strip()
            continue
        label = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        label = re.sub(r"^(?:a|an|the)\s+", "", label)
        head = label.split()[0] if label else ""
        aliases = {label, head, *label.split()}
        chunk_l = chunk.lower()
        for key, vals in _CHAR_ALIASES.items():
            if key == head or key in label or re.search(rf"\b{re.escape(key)}\b", chunk_l):
                aliases.update(vals)
                aliases.add(key)
        strong = {a for a in aliases if a and a not in _WEAK_ALIASES and len(a) > 2}
        entries.append(
            {
                "label": label,
                "aliases": sorted(strong or {a for a in aliases if a}),
                "text": chunk,
            }
        )
    return entries


def join_character_bible(entries: list[dict] | None) -> str:
    """Rebuild character_bible string from editable per-character cards."""
    parts: list[str] = []
    for raw in entries or []:
        if not isinstance(raw, dict):
            continue
        label = re.sub(r"\s+", " ", str(raw.get("label") or "").strip())
        label = re.sub(r"^(?:a|an|the)\s+", "", label, flags=re.I)
        text = str(raw.get("text") or "").strip()
        if not text and not label:
            continue
        if not text:
            text = f"The {label}."
        elif label:
            # Keep description parseable: ensure it opens with the label identity
            opens = re.match(
                rf"^(?:The\s+)?{re.escape(label)}\b",
                text,
                flags=re.I,
            )
            if not opens:
                body = text[0].lower() + text[1:] if text and text[0].isupper() else text
                if not re.match(r"^(?:is|are|has|have|wears|wear|consists)\b", body, re.I):
                    body = "is " + body
                text = f"The {label} {body}"
        parts.append(text.rstrip(" .") + ".")
    return " ".join(parts)


def characters_for_ui(bible: str = "", entries: list[dict] | None = None) -> list[dict]:
    """Normalized character cards for the editor (id, label, text)."""
    source = entries if isinstance(entries, list) and entries else parse_character_bible(bible)
    out: list[dict] = []
    for i, entry in enumerate(source):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not label and not text:
            continue
        cid = str(entry.get("id") or f"char-{i + 1}")
        out.append({"id": cid, "label": label or f"character {i + 1}", "text": text})
    return out


def filter_bible_for_scene(bible: str, scene: dict) -> str:
    """Return only character_bible blurbs for cast present in this scene."""
    entries = parse_character_bible(bible)
    if not entries:
        return ""

    visual = str(scene.get("visual_prompt") or "")
    notes = str(scene.get("notes") or "")
    hay = f"{visual}\n{notes}".lower()

    explicit = scene.get("characters") or scene.get("cast") or []
    explicit_names: list[str] = []
    if isinstance(explicit, list):
        explicit_names = [str(x).strip().lower() for x in explicit if str(x).strip()]
    elif isinstance(explicit, str) and explicit.strip():
        explicit_names = [p.strip().lower() for p in re.split(r"[,;/]+", explicit) if p.strip()]

    chosen: list[dict] = []
    if explicit_names:
        for entry in entries:
            aliases = set(entry["aliases"]) | {entry["label"]}
            if any(name in aliases or any(name in a or a in name for a in aliases) for name in explicit_names):
                chosen.append(entry)
    else:
        for entry in entries:
            if any(re.search(rf"\b{re.escape(alias)}\b", hay) for alias in entry["aliases"]):
                chosen.append(entry)

    # Unsplit blob: keep only sentences that mention aliases found in the scene
    if len(chosen) == 1:
        text = chosen[0]["text"]
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 1:
            matched_alias = [
                a for a in chosen[0]["aliases"] if re.search(rf"\b{re.escape(a)}\b", hay)
            ]
            keep = [
                s
                for s in sentences
                if any(re.search(rf"\b{re.escape(a)}\b", s.lower()) for a in matched_alias)
            ]
            group_words = ("women", "tribe", "females", "girls", "ladies", "crowd")
            if keep and not any(re.search(rf"\b{re.escape(g)}\b", hay) for g in group_words):
                keep = [
                    s
                    for s in keep
                    if not any(re.search(rf"\b{re.escape(g)}\b", s.lower()) for g in group_words)
                ] or keep
            if keep:
                return " ".join(k.rstrip(".") + "." for k in keep)

    group_aliases = ("women", "tribe", "crowd", "group", "females", "girls", "ladies")
    solo_hints = ("solo", "alone", "single", "only one", "one person")
    mentions_group = any(re.search(rf"\b{re.escape(g)}\b", hay) for g in group_aliases)
    if (any(h in hay for h in solo_hints) or not mentions_group) and len(chosen) > 1:
        preferred = [
            e
            for e in chosen
            if not any(a in group_aliases for a in e["aliases"])
            and not any(g in e["label"] for g in group_aliases)
        ]
        if preferred:
            chosen = preferred[:1]
        elif any(h in hay for h in solo_hints):
            chosen = chosen[:1]

    if not chosen:
        return ""
    return " ".join(e["text"].rstrip(".") + "." for e in chosen)


def _scene_looks_solo(scene: dict) -> bool:
    visual = str(scene.get("visual_prompt") or "").lower()
    notes = str(scene.get("notes") or "").lower()
    hay = f"{visual}\n{notes}"
    if any(h in hay for h in ("solo", "alone", "single", "only one", "one person", "no other")):
        return True
    group_words = (
        "women",
        "tribe",
        "crowd",
        "group",
        "girls",
        "people",
        "villagers",
        "warriors",
        "they",
        "them",
    )
    if any(re.search(rf"\b{re.escape(w)}\b", hay) for w in group_words):
        return False
    return bool(
        re.search(r"\b(he|she|motorcyclist|biker|rider|man|woman|protagonist)\b", hay)
    )


def compose_shot_bits(shot: dict | None, *, solo: bool = False) -> list[str]:
    """Human-readable shot/environment fragments for prompts."""
    shot = shot if isinstance(shot, dict) else {}
    camera_raw = str(shot.get("camera") or "").strip()
    lighting = str(shot.get("lighting") or "").strip()
    fov = str(shot.get("fov") or "").strip()
    environment = str(shot.get("environment") or shot.get("location") or "").strip()
    time_of_day = str(shot.get("time_of_day") or shot.get("time") or "").strip()
    weather = str(shot.get("weather") or "").strip()
    palette = str(shot.get("palette") or shot.get("color_palette") or "").strip()
    ambience = str(shot.get("ambience") or shot.get("atmosphere") or "").strip()
    extras_free = str(shot.get("extras") or shot.get("notes") or "").strip()
    themes = shot.get("themes") or []
    theme_bits = [str(t).strip() for t in themes if isinstance(themes, list) and str(t).strip()]

    camera_map = {
        "eye-level medium shot": (
            "eye-level medium shot, camera at subject eye height, straight-on framing"
        ),
        "low angle looking up": (
            "low-angle shot looking up at the subject, camera near the ground"
        ),
        "high angle looking down": (
            "high-angle shot looking down on the subject, camera elevated above eye level"
        ),
        "dutch angle": "dutch angle / canted frame, horizon tilted",
        "over-the-shoulder": (
            # Classic dialogue OTS implies TWO people — only use when cast is plural.
            "over-the-shoulder shot (OTS), camera behind one person's shoulder in the "
            "foreground looking toward the other subject, shallow depth of field on the "
            "far subject, classic dialogue framing, shoulder and head edge visible in foreground"
        ),
        "POV first person": "first-person POV shot, camera as the character's eyes",
        "tracking side shot": "side tracking shot, camera moving laterally beside the subject",
    }
    camera_map_solo = {
        **camera_map,
        "over-the-shoulder": (
            "rear three-quarter view from behind the sole subject, camera looking past "
            "their own shoulder into the environment ahead, only one person in the entire frame, "
            "no second character, no dialogue partner, no face in the foreground belonging to "
            "another person, soft out-of-focus shoulder of the same single subject only"
        ),
        "POV first person": (
            "first-person POV as the sole character's eyes, immersive viewpoint, "
            "no other people visible, empty environment ahead"
        ),
    }
    camera = (camera_map_solo if solo else camera_map).get(camera_raw, camera_raw)

    bits: list[str] = []
    if environment:
        bits.append(f"setting / background: {environment}")
    if time_of_day:
        bits.append(f"time of day: {time_of_day}")
    if weather:
        bits.append(f"weather: {weather}")
    if lighting:
        bits.append(f"lighting: {lighting}")
    if palette:
        bits.append(f"color palette: {palette}")
    if ambience:
        bits.append(f"atmosphere: {ambience}")
    if camera:
        bits.append(f"camera: {camera}")
    if fov:
        bits.append(f"lens / FOV: {fov}")
    if theme_bits:
        bits.append("mood/style: " + ", ".join(theme_bits))
    if extras_free:
        bits.append(extras_free)
    return bits


def _shot_is_ots(shot: dict | None) -> bool:
    cam = str((shot or {}).get("camera") or "").strip().lower()
    return "over-the-shoulder" in cam or cam in {"ots", "over shoulder", "over-shoulder"}


def world_negative_extras(world_bible: str = "") -> list[str]:
    """Push models away from conflicting biomes when world lock is set."""
    w = (world_bible or "").lower()
    extras: list[str] = []
    if any(x in w for x in ("jungle", "rainforest", "forest", "woodland", "canopy", "tropic")):
        extras.extend(
            [
                "desert",
                "sand dunes",
                "arid desert",
                "empty desert",
                "sahara",
                "dry wasteland",
                "barren dunes",
            ]
        )
    if any(x in w for x in ("desert", "dunes", "arid", "sahara", "wasteland")):
        extras.extend(
            [
                "jungle",
                "dense forest",
                "tropical rainforest",
                "lush canopy",
                "green forest",
            ]
        )
    if any(x in w for x in ("snow", "arctic", "winter", "ice")):
        extras.extend(["desert", "tropical beach", "jungle"])
    if any(x in w for x in ("night", "moonlight", "nocturnal")):
        extras.extend(["harsh noon sun", "bright midday sun"])
    return extras


def compose_prompt(
    scene: dict,
    character_bible: str = "",
    world_bible: str = "",
) -> str:
    """Build Leonardo prompt from scene visual + shot + world + filtered cast."""
    visual = (scene.get("visual_prompt") or "").strip()
    shot = scene.get("shot") if isinstance(scene.get("shot"), dict) else {}
    cast_bible = filter_bible_for_scene(character_bible or "", scene)
    world = (world_bible or scene.get("world_bible") or "").strip()
    solo = _scene_looks_solo(scene)

    parts: list[str] = []
    if world:
        parts.append(
            "World / location lock (same setting for every scene unless visual says otherwise): "
            + world
        )
    if cast_bible:
        parts.append("Character identity for this shot only: " + cast_bible)
    if visual:
        parts.append(visual)

    if solo:
        parts.append(
            "single subject only, one person in the entire frame, no other people in background, "
            "no crowd, no group, no tribe, no extra characters, no dialogue partner"
        )
        if _shot_is_ots(shot):
            parts.append(
                "important: this is NOT a two-person dialogue OTS - do not invent a second human; "
                "only the one described subject appears"
            )

    shot_bits = compose_shot_bits(shot, solo=solo)
    if shot_bits:
        parts.append("Shot design: " + "; ".join(shot_bits))

    parts.append(
        "cinematic still frame, photorealistic keyframe for video, high detail, sharp focus"
    )
    parts.append("adult characters only, 18+")
    return ". ".join(p for p in parts if p)


def compose_negative(scene: dict, world_bible: str = "") -> str:
    """Scene negative + anti-crowd / anti-wrong-biome defaults."""
    base = str(scene.get("negative_prompt") or "").strip()
    shot = scene.get("shot") if isinstance(scene.get("shot"), dict) else {}
    extras = [
        "crowd",
        "group of people",
        "multiple people",
        "extra people",
        "extra characters",
        "bystanders",
        "audience",
        "tribe",
        "many women",
        "semi-nude women",
        "duplicate subjects",
        "text overlay",
        "watermark",
        "logo",
        "blurry",
        "low quality",
        "deformed hands",
        "inconsistent background",
        "location change",
        "wrong biome",
    ]
    extras.extend(world_negative_extras(world_bible or str(scene.get("world_bible") or "")))
    if _scene_looks_solo(scene):
        extras.extend(
            [
                "passengers",
                "second rider",
                "companions",
                "entourage",
                "people in background",
                "figures in background",
                "tribal women",
                "female tribe",
                "two people",
                "several women",
                "second person",
                "another person",
                "dialogue partner",
                "conversation",
                "two faces",
            ]
        )
        if _shot_is_ots(shot):
            extras.extend(
                [
                    "over-the-shoulder of another character",
                    "second shoulder in foreground",
                    "face looking back at camera from foreground",
                    "listening figure",
                    "partner across from subject",
                    "classic dialogue OTS with two actors",
                    "two-shot",
                    "reaction shot of another person",
                ]
            )
    seen: set[str] = set()
    merged: list[str] = []
    for part in [base] + extras:
        for token in [t.strip() for t in part.replace(";", ",").split(",") if t.strip()]:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(token)
    return ", ".join(merged)



def create_generation(
    prompt: str,
    *,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 576,
    model: str | None = None,
) -> str:
    mid = (model or model_id()).strip() or DEFAULT_MODEL_ID
    payload: dict = {
        "prompt": prompt,
        "modelId": mid,
        "width": int(width),
        "height": int(height),
        "num_images": 1,
        "public": False,
        "alchemy": False,
    }
    if negative_prompt.strip():
        payload["negative_prompt"] = negative_prompt.strip()
    data = _request("POST", "/generations", payload, timeout=90)
    job = data.get("sdGenerationJob") or {}
    gid = job.get("generationId") or data.get("generationId")
    if not gid:
        raise LeonardoError(f"Leonardo не вернул generationId: {data}")
    return str(gid)


def wait_generation(
    generation_id: str,
    timeout: int = 300,
    interval: float = 2.0,
    cancel_check=None,
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel_check:
            cancel_check()
        data = _request("GET", f"/generations/{generation_id}", timeout=30)
        gen = data.get("generations_by_pk") or data.get("generation") or data
        status = str(gen.get("status") or "").upper()
        if status == "COMPLETE":
            return gen
        if status in {"FAILED", "ERROR"}:
            raise LeonardoError(f"Leonardo generation failed: {gen}")
        time.sleep(interval)
    raise LeonardoError(f"Leonardo timeout waiting for {generation_id}")


def first_image_url(generation: dict) -> str:
    images = generation.get("generated_images") or []
    if not images:
        raise LeonardoError("Leonardo COMPLETE без изображений")
    url = images[0].get("url")
    if not url:
        raise LeonardoError("Leonardo image без url")
    return str(url)


def download_image(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "videomake/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".jpg"
    if dest.suffix.lower() != suffix:
        dest = dest.with_suffix(suffix)
    dest.write_bytes(raw)
    return dest


def _clear_stem_images(parent: Path, stem: str) -> None:
    """Remove only this scene's ref files — never touch other scenes."""
    safe = Path(stem).stem if "." in stem else Path(stem).name
    if not SAFE_STEM.match(safe):
        return
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        path = parent / f"{safe}{ext}"
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def generate_ref_to_file(
    scene: dict,
    dest_stem: Path,
    *,
    character_bible: str = "",
    world_bible: str = "",
    width: int | None = None,
    height: int | None = None,
    model: str | None = None,
    cancel_check=None,
) -> Path:
    """Generate one still from scene visual + shot + world/character locks."""
    if cancel_check:
        cancel_check()
    w, h = pick_size(width, height)
    prompt = compose_prompt(
        scene,
        character_bible=character_bible or "",
        world_bible=world_bible or "",
    )
    negative = compose_negative(scene, world_bible=world_bible or "")
    gid = create_generation(
        prompt,
        negative_prompt=negative,
        width=w,
        height=h,
        model=model,
    )
    if cancel_check:
        cancel_check()
    gen = wait_generation(gid, cancel_check=cancel_check)
    if cancel_check:
        cancel_check()
    url = first_image_url(gen)
    parent = dest_stem.parent
    stem = Path(dest_stem.name).stem if "." in dest_stem.name else dest_stem.name
    if not SAFE_STEM.match(stem):
        # Force canonical name from scene id when possible
        sid = int(scene.get("id") or 0)
        stem = f"scene-{sid:02d}" if sid else "scene-01"
    _clear_stem_images(parent, stem)
    return download_image(url, parent / f"{stem}.jpg")
