#!/usr/bin/env python3
"""Local videomake UI: Ollama split, ComfyUI generate, ffmpeg stitch."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
STITCH = ROOT / "scripts" / "stitch.py"

sys.path.insert(0, str(ROOT / "scripts"))
from envutil import load_dotenv  # noqa: E402
import projects as proj  # noqa: E402
from split_scenes import DEFAULT_NEGATIVE, SplitError, split_script  # noqa: E402
import leonardo as leo  # noqa: E402

load_dotenv(ROOT / ".env")
proj.ensure_dirs()

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_EXPAND_MODEL = (
    os.environ.get("OLLAMA_EXPAND_MODEL")
    or os.environ.get("OLLAMA_MODEL")
    or "qwen3.8:27b"
)
OLLAMA_SPLIT_MODEL = os.environ.get("OLLAMA_SPLIT_MODEL") or "dolphin-llama3"
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
HOST = os.environ.get("VIDEOMAKE_HOST", "127.0.0.1")
PORT = int(os.environ.get("VIDEOMAKE_PORT", "8765"))

_JOB_LOCK = threading.Lock()
_CANCEL_VIDEO = threading.Event()
_CANCEL_LEO = threading.Event()


def _empty_track() -> dict:
    return {
        "status": "idle",
        "phase": "",
        "scene_id": None,
        "message": "",
        "error": None,
    }


_TRACKS: dict[str, dict] = {
    "video": _empty_track(),  # ComfyUI generate + ffmpeg stitch
    "leonardo": _empty_track(),
}
_MEDIA: dict = {
    "last_ref": None,
    "refs_ready": [],
    "last_clip": None,
}


class JobCancelled(Exception):
    """Raised when the user aborts generate / leonardo / stitch."""


def _cancel_event(track: str) -> threading.Event:
    return _CANCEL_VIDEO if track == "video" else _CANCEL_LEO


def _snapshot() -> dict:
    with _JOB_LOCK:
        video = dict(_TRACKS["video"])
        leo_job = dict(_TRACKS["leonardo"])
        media = dict(_MEDIA)
    pid = proj.get_current()
    # Drop stale last_clip if the take was deleted
    last_clip = media.get("last_clip")
    if isinstance(last_clip, dict) and last_clip.get("clip") and pid:
        clip_path = proj.scenes_dir(pid) / Path(str(last_clip["clip"])).name
        if not clip_path.is_file():
            with _JOB_LOCK:
                cur = _MEDIA.get("last_clip")
                if isinstance(cur, dict) and cur.get("clip") == last_clip.get("clip"):
                    _MEDIA["last_clip"] = None
            media["last_clip"] = None
    running = [t for t in (video, leo_job) if t.get("status") == "running"]
    errored = [t for t in (video, leo_job) if t.get("status") == "error" and t.get("error")]
    if running:
        status = "running"
        message = " · ".join(str(t.get("message") or "").strip() for t in running if t.get("message"))
        phase = ",".join(str(t.get("phase") or "") for t in running if t.get("phase"))
        scene_id = running[0].get("scene_id")
        error = None
    elif errored and not running:
        # Prefer the most specific error; keep overall status error if any track failed
        # and the other is idle.
        status = "error"
        message = str(errored[-1].get("message") or errored[-1].get("error") or "Ошибка")
        phase = str(errored[-1].get("phase") or "")
        scene_id = errored[-1].get("scene_id")
        error = str(errored[-1].get("error") or message)
    else:
        # Prefer a recent idle message (clips ready / refs ready)
        msgs = [t.get("message") for t in (video, leo_job) if t.get("message")]
        status = "idle"
        message = msgs[-1] if msgs else ""
        phase = ""
        scene_id = None
        error = None
    return {
        "status": status,
        "phase": phase,
        "scene_id": scene_id,
        "message": message or "",
        "error": error,
        "clips": _list_clips(),
        "final": bool(pid and proj.final_mp4(pid).is_file()),
        "project_id": pid,
        "last_ref": media.get("last_ref"),
        "last_clip": media.get("last_clip"),
        "refs_ready": media.get("refs_ready") or [],
        "jobs": {"video": video, "leonardo": leo_job},
        "video_busy": video.get("status") == "running",
        "leo_busy": leo_job.get("status") == "running",
    }


def _set_track(track: str, **kwargs) -> None:
    media_keys = {"last_ref", "refs_ready", "last_clip"}
    media_update = {k: kwargs.pop(k) for k in list(kwargs.keys()) if k in media_keys}
    with _JOB_LOCK:
        if kwargs:
            _TRACKS[track].update(kwargs)
        if media_update:
            _MEDIA.update(media_update)


def _check_cancelled(track: str = "video") -> None:
    if _cancel_event(track).is_set():
        raise JobCancelled("Генерация прервана.")


def _request_cancel(track: str | None = None) -> bool:
    """Cancel one track ('video'|'leonardo') or all running tracks."""
    targets = [track] if track in {"video", "leonardo"} else ["video", "leonardo"]
    any_running = False
    for name in targets:
        with _JOB_LOCK:
            if _TRACKS[name]["status"] != "running":
                continue
            _TRACKS[name].update(message="Прерывание…")
        any_running = True
        _cancel_event(name).set()
        if name == "video":
            _interrupt_comfy()

        def _force_idle(track_name: str = name) -> None:
            with _JOB_LOCK:
                if not _cancel_event(track_name).is_set():
                    return
                if _TRACKS[track_name]["status"] != "running":
                    return
                _TRACKS[track_name].update(
                    status="idle",
                    phase="",
                    scene_id=None,
                    message="Генерация прервана.",
                    error=None,
                )

        threading.Timer(1.5, _force_idle).start()
    return any_running


def _force_reset_job() -> None:
    """Unlock UI if a worker is stuck or the process was left mid-run."""
    _CANCEL_VIDEO.set()
    _CANCEL_LEO.set()
    _interrupt_comfy()
    with _JOB_LOCK:
        for name in ("video", "leonardo"):
            _TRACKS[name].update(
                status="idle",
                phase="",
                scene_id=None,
                message="Задача сброшена.",
                error=None,
            )
    threading.Timer(0.5, lambda: (_CANCEL_VIDEO.clear(), _CANCEL_LEO.clear())).start()


def _start_track(track: str, fn, *args) -> bool:
    with _JOB_LOCK:
        if _TRACKS[track]["status"] == "running":
            return False
        _cancel_event(track).clear()
        _TRACKS[track].update(status="running", error=None, message="Старт…", phase="", scene_id=None)
    threading.Thread(target=fn, args=args, daemon=True).start()
    return True


def _http_json(url: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def _reachable(url: str, timeout: float = 1.5) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _ollama_models() -> list[str]:
    try:
        data = _http_json(f"{OLLAMA_HOST}/api/tags", timeout=5)
    except Exception:
        return []
    names: list[str] = []
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if name:
            names.append(str(name))
    return sorted(set(names), key=str.lower)


def _wan_defaults() -> dict:
    return {
        "width": int(os.environ.get("WAN_WIDTH", "832")),
        "height": int(os.environ.get("WAN_HEIGHT", "480")),
        "length": int(os.environ.get("WAN_LENGTH", "81")),
        "steps": int(os.environ.get("WAN_STEPS", "40")),
        "cfg": float(os.environ.get("WAN_CFG", "5")),
        "shift": float(os.environ.get("WAN_SHIFT", "8")),
        "seed": int(os.environ.get("WAN_SEED", "-1")),
        "sampler": os.environ.get("WAN_SAMPLER", "uni_pc").strip() or "uni_pc",
        "scheduler": os.environ.get("WAN_SCHEDULER", "simple").strip() or "simple",
        "denoise": float(os.environ.get("WAN_DENOISE", "1.0")),
        "fps": float(os.environ.get("WAN_FPS", "24")),
        "unet": os.environ.get("WAN_UNET", "wan2.2_ti2v_5B_fp16.safetensors").strip(),
        "clip": os.environ.get("WAN_CLIP", "umt5_xxl_fp8_e4m3fn_scaled.safetensors").strip(),
        "vae": os.environ.get("WAN_VAE", "wan2.2_vae.safetensors").strip(),
        "weight_dtype": os.environ.get("WAN_WEIGHT_DTYPE", "default").strip() or "default",
    }


# Curated fallbacks when ComfyUI is offline / object_info unavailable
_WAN_SAMPLER_FALLBACK = [
    "uni_pc",
    "uni_pc_bh2",
    "euler",
    "euler_ancestral",
    "dpmpp_2m",
    "dpmpp_2m_sde",
    "dpmpp_2m_sde_gpu",
    "dpmpp_sde",
    "dpmpp_sde_gpu",
    "dpmpp_2s_ancestral",
    "dpmpp_3m_sde",
    "ddim",
    "lcm",
    "dpm_fast",
    "dpm_adaptive",
]
_WAN_SCHEDULER_FALLBACK = [
    "simple",
    "normal",
    "karras",
    "exponential",
    "sgm_uniform",
    "ddim_uniform",
    "beta",
    "linear_quadratic",
    "kl_optimal",
]
_WAN_DTYPE_FALLBACK = ["default", "fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16"]
_WAN_UNET_FALLBACK = ["wan2.2_ti2v_5B_fp16.safetensors"]
_WAN_CLIP_FALLBACK = ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"]
_WAN_VAE_FALLBACK = ["wan2.2_vae.safetensors", "wan_2.1_vae.safetensors"]

_COMFY_OPTIONS_CACHE: dict | None = None
_COMFY_OPTIONS_TS = 0.0


def _combo_values(node: dict, field: str) -> list[str]:
    """Extract combo list from ComfyUI object_info input schema."""
    try:
        raw = (node.get("input") or {}).get("required") or {}
        spec = raw.get(field)
        if isinstance(spec, list) and spec:
            first = spec[0]
            if isinstance(first, list):
                return [str(x) for x in first]
            if isinstance(first, str) and first == "COMBO" and len(spec) > 1:
                opts = spec[1]
                if isinstance(opts, dict) and isinstance(opts.get("options"), list):
                    return [str(x) for x in opts["options"]]
                if isinstance(opts, list):
                    return [str(x) for x in opts]
    except Exception:
        pass
    return []


def _comfy_wan_options(force: bool = False) -> dict:
    """Samplers / models from live ComfyUI, with offline fallbacks."""
    global _COMFY_OPTIONS_CACHE, _COMFY_OPTIONS_TS
    now = time.time()
    if not force and _COMFY_OPTIONS_CACHE is not None and now - _COMFY_OPTIONS_TS < 60:
        return _COMFY_OPTIONS_CACHE

    samplers = list(_WAN_SAMPLER_FALLBACK)
    schedulers = list(_WAN_SCHEDULER_FALLBACK)
    dtypes = list(_WAN_DTYPE_FALLBACK)
    unets = list(_WAN_UNET_FALLBACK)
    clips = list(_WAN_CLIP_FALLBACK)
    vaes = list(_WAN_VAE_FALLBACK)
    source = "fallback"

    try:
        info = _http_json(f"{COMFYUI_URL}/object_info", timeout=8)
        if isinstance(info, dict):
            ks = info.get("KSampler") or {}
            s = _combo_values(ks, "sampler_name")
            sch = _combo_values(ks, "scheduler")
            if s:
                samplers = s
            if sch:
                schedulers = sch
            unet_node = info.get("UNETLoader") or {}
            u = _combo_values(unet_node, "unet_name")
            dt = _combo_values(unet_node, "weight_dtype")
            if u:
                unets = u
            if dt:
                dtypes = dt
            clip_node = info.get("CLIPLoader") or {}
            c = _combo_values(clip_node, "clip_name")
            if c:
                clips = c
            vae_node = info.get("VAELoader") or {}
            v = _combo_values(vae_node, "vae_name")
            if v:
                vaes = v
            source = "comfyui"
    except Exception:
        pass

    # Prefer Wan-related files at top of lists
    def prefer(names: list[str], needles: tuple[str, ...]) -> list[str]:
        hit = [n for n in names if any(x in n.lower() for x in needles)]
        rest = [n for n in names if n not in hit]
        return hit + rest if hit else names

    out = {
        "samplers": samplers,
        "schedulers": schedulers,
        "weight_dtypes": dtypes,
        "unets": prefer(unets, ("wan", "ti2v")),
        "clips": prefer(clips, ("umt5", "wan", "t5")),
        "vaes": prefer(vaes, ("wan",)),
        "steps": [20, 30, 40, 50, 55, 60, 70, 80],
        "cfg": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0],
        "shift": [3.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0],
        "length": [33, 49, 65, 81, 97, 113, 121],
        "denoise": [0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
        "fps": [16, 24, 30],
        "resolutions": [
            {"value": "640x360", "label": "640×360 (быстрее)"},
            {"value": "832x480", "label": "832×480 (TI2V-5B default)"},
            {"value": "1024x576", "label": "1024×576"},
            {"value": "1280x704", "label": "1280×704 (качество, больше VRAM)"},
        ],
        "source": source,
        "defaults": _wan_defaults(),
    }
    _COMFY_OPTIONS_CACHE = out
    _COMFY_OPTIONS_TS = now
    return out


def _pick_installed(preferred: str, models: list[str]) -> str:
    if not models:
        return preferred
    if preferred in models:
        return preferred
    bare = preferred.split(":")[0]
    for name in models:
        if name == bare or name.startswith(bare + ":"):
            return name
    return preferred


def _health() -> dict:
    models = _ollama_models()
    ollama_ok = bool(models) or _reachable(f"{OLLAMA_HOST}/api/tags")
    comfy_ok = _reachable(f"{COMFYUI_URL}/system_stats")
    expand_model = _pick_installed(OLLAMA_EXPAND_MODEL, models)
    split_model = _pick_installed(OLLAMA_SPLIT_MODEL, models)
    return {
        "ollama": ollama_ok,
        "comfyui": comfy_ok,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "leonardo": leo.configured(),
        "ollama_host": OLLAMA_HOST,
        "comfyui_url": COMFYUI_URL,
        "ollama_models": models,
        "ollama_expand_model": expand_model,
        "ollama_split_model": split_model,
        "ollama_model": expand_model,
        "wan": _wan_defaults(),
        "wan_options": _comfy_wan_options(),
        "leonardo_model_id": leo.model_id() if leo.configured() else None,
        "leonardo_models": leo.list_ref_models() if leo.configured() else [],
    }


def _list_clips() -> list[dict]:
    pid = proj.get_current()
    if not pid:
        return []
    return proj.list_clips(pid)


def build_wan_prompt(
    positive: str,
    negative: str,
    filename_prefix: str,
    *,
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
    length: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    shift: float | None = None,
    sampler: str | None = None,
    scheduler: str | None = None,
    denoise: float | None = None,
    fps: float | None = None,
    unet: str | None = None,
    clip: str | None = None,
    vae: str | None = None,
    weight_dtype: str | None = None,
    start_image: str | None = None,
) -> dict:
    defaults = _wan_defaults()
    if seed is None or int(seed) < 0:
        seed = random.randint(0, 2**31 - 1)
    else:
        seed = int(seed)
    width = int(width if width is not None else defaults["width"])
    height = int(height if height is not None else defaults["height"])
    length = int(length if length is not None else defaults["length"])
    steps = int(steps if steps is not None else defaults["steps"])
    cfg = float(cfg if cfg is not None else defaults["cfg"])
    shift = float(shift if shift is not None else defaults["shift"])
    sampler = (sampler or defaults["sampler"]).strip() or "uni_pc"
    scheduler = (scheduler or defaults["scheduler"]).strip() or "simple"
    denoise = float(denoise if denoise is not None else defaults["denoise"])
    fps = float(fps if fps is not None else defaults["fps"])
    unet = (unet or defaults["unet"]).strip()
    clip = (clip or defaults["clip"]).strip()
    vae = (vae or defaults["vae"]).strip()
    weight_dtype = (weight_dtype or defaults["weight_dtype"]).strip() or "default"
    latent_inputs: dict = {
        "vae": ["39", 0],
        "width": width,
        "height": height,
        "length": length,
        "batch_size": 1,
    }
    graph: dict = {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": unet,
                "weight_dtype": weight_dtype,
            },
        },
        "38": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": clip,
                "type": "wan",
                "device": "default",
            },
        },
        "39": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae},
        },
        "48": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["37", 0], "shift": shift},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["38", 0], "text": positive},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["38", 0], "text": negative or DEFAULT_NEGATIVE},
        },
        "55": {
            "class_type": "Wan22ImageToVideoLatent",
            "inputs": latent_inputs,
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["48", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["55", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": denoise,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
        },
        "47": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["8", 0], "fps": fps},
        },
        "49": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["47", 0],
                "filename_prefix": filename_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }
    if start_image:
        graph["50"] = {
            "class_type": "LoadImage",
            "inputs": {"image": start_image},
        }
        latent_inputs["start_image"] = ["50", 0]
    return graph


def _compose_positive(scene: dict, character_bible: str, world_bible: str = "") -> str:
    visual = (scene.get("visual_prompt") or "").strip()
    bible = (character_bible or scene.get("character_bible") or "").strip()
    world = (world_bible or scene.get("world_bible") or "").strip()
    cast = leo.filter_bible_for_scene(bible, scene) if bible else ""
    parts: list[str] = []
    if world:
        parts.append(
            "World / location lock (keep the same biome and background across shots): " + world
        )
    if cast and cast.lower() not in visual.lower():
        parts.append(cast)
    if visual:
        parts.append(visual)
    if leo._scene_looks_solo(scene):
        parts.append(
            "single subject only, one person in frame, no tribe, no extra people, no dialogue partner"
        )
    shot = scene.get("shot") if isinstance(scene.get("shot"), dict) else {}
    shot_bits = leo.compose_shot_bits(shot, solo=leo._scene_looks_solo(scene))
    if shot_bits:
        parts.append("Shot design: " + "; ".join(shot_bits))
    return ". ".join(parts).strip()


def _sanitize_wan(raw: dict | None) -> dict:
    defaults = _wan_defaults()
    opts = _comfy_wan_options()
    src = raw if isinstance(raw, dict) else {}
    out = dict(defaults)

    def _num(key: str, cast):
        if key not in src or src[key] in (None, ""):
            return
        try:
            out[key] = cast(src[key])
        except (TypeError, ValueError):
            pass

    for key, cast in (
        ("width", int),
        ("height", int),
        ("length", int),
        ("steps", int),
        ("cfg", float),
        ("shift", float),
        ("seed", int),
        ("denoise", float),
        ("fps", float),
    ):
        _num(key, cast)

    for key in ("sampler", "scheduler", "unet", "clip", "vae", "weight_dtype"):
        if key in src and src[key] not in (None, ""):
            out[key] = str(src[key]).strip()

    # Aliases from UI / older payloads
    if "sampler_name" in src and src["sampler_name"]:
        out["sampler"] = str(src["sampler_name"]).strip()
    if "unet_name" in src and src["unet_name"]:
        out["unet"] = str(src["unet_name"]).strip()
    if "clip_name" in src and src["clip_name"]:
        out["clip"] = str(src["clip_name"]).strip()
    if "vae_name" in src and src["vae_name"]:
        out["vae"] = str(src["vae_name"]).strip()

    out["width"] = max(256, min(1920, int(out["width"])))
    out["height"] = max(256, min(1080, int(out["height"])))
    # Wan latent length prefers 4n+1
    length = max(17, min(161, int(out["length"])))
    if length % 4 != 1:
        length = max(17, min(161, length - ((length - 1) % 4)))
    out["length"] = length
    out["steps"] = max(4, min(80, int(out["steps"])))
    out["cfg"] = max(1.0, min(15.0, float(out["cfg"])))
    out["shift"] = max(1.0, min(20.0, float(out["shift"])))
    out["denoise"] = max(0.05, min(1.0, float(out["denoise"])))
    out["fps"] = max(8.0, min(60.0, float(out["fps"])))
    try:
        out["seed"] = int(out["seed"])
    except (TypeError, ValueError):
        out["seed"] = -1

    allowed_samplers = set(opts.get("samplers") or _WAN_SAMPLER_FALLBACK)
    allowed_sched = set(opts.get("schedulers") or _WAN_SCHEDULER_FALLBACK)
    allowed_dtype = set(opts.get("weight_dtypes") or _WAN_DTYPE_FALLBACK)
    if out["sampler"] not in allowed_samplers:
        out["sampler"] = defaults["sampler"]
    if out["scheduler"] not in allowed_sched:
        out["scheduler"] = defaults["scheduler"]
    if out["weight_dtype"] not in allowed_dtype:
        out["weight_dtype"] = defaults["weight_dtype"]

    # Model filenames: allow any non-empty basename (Comfy validates)
    for key in ("unet", "clip", "vae"):
        name = Path(str(out[key])).name
        out[key] = name if name else defaults[key]

    return out


def _history_files(history: dict) -> list[dict]:
    files = []
    for node_out in history.get("outputs", {}).values():
        if not isinstance(node_out, dict):
            continue
        for key in ("gifs", "videos", "images"):
            for item in node_out.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    files.append(item)
    return files


def _fetch_comfy_file(item: dict, dest: Path) -> None:
    query = urllib.parse.urlencode(
        {
            "filename": item["filename"],
            "subfolder": item.get("subfolder") or "",
            "type": item.get("type") or "output",
        }
    )
    url = f"{COMFYUI_URL}/view?{query}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp:
        dest.write_bytes(resp.read())


def _upload_comfy_image(path: Path) -> str:
    """Upload local image to ComfyUI input folder; return remote filename."""
    boundary = "----VideomakeBoundary7MA4YWxk"
    filename = path.name
    raw = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + raw + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        f"true\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFYUI_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    name = payload.get("name") or filename
    return str(name)


def _interrupt_comfy() -> None:
    """Stop current ComfyUI run and clear its pending queue."""
    try:
        _http_json(f"{COMFYUI_URL}/interrupt", {}, timeout=5)
    except Exception:
        pass
    try:
        _http_json(f"{COMFYUI_URL}/queue", {"clear": True}, timeout=5)
    except Exception:
        pass


def _queue_and_wait(prompt: dict, dest: Path, timeout: int = 3600) -> None:
    _check_cancelled("video")
    submitted = _http_json(f"{COMFYUI_URL}/prompt", {"prompt": prompt}, timeout=60)
    prompt_id = submitted.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not accept the prompt: {submitted}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_cancelled("video")
        history = _http_json(f"{COMFYUI_URL}/history/{prompt_id}", timeout=30)
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if entry:
            status = (entry.get("status") or {}).get("status_str")
            if status == "error" or (entry.get("status") or {}).get("completed") is False:
                messages = (entry.get("status") or {}).get("messages") or []
                raise RuntimeError(f"ComfyUI job failed: {messages}")
            files = _history_files(entry)
            if files:
                _fetch_comfy_file(files[0], dest)
                return
            if (entry.get("status") or {}).get("completed"):
                raise RuntimeError("ComfyUI finished without a video file.")
        time.sleep(2)
    raise TimeoutError("Timed out waiting for ComfyUI.")


def _run_generate(
    scenes: list[dict],
    only_id: int | None,
    character_bible: str = "",
    wan: dict | None = None,
    project_id: str | None = None,
    world_bible: str = "",
    repeats: int = 1,
) -> None:
    try:
        pid = project_id or proj.require_current()
        settings = _sanitize_wan(wan)
        repeats = max(1, min(int(repeats or 1), 20))
        targets = scenes
        if only_id is not None:
            targets = [s for s in scenes if int(s.get("id", 0)) == only_id]
            if not targets:
                raise RuntimeError(f"Scene {only_id} not found.")
            if _scene_target(targets[0]) == "leonardo":
                raise RuntimeError(
                    f"Сцена {only_id} настроена на Leonardo API — "
                    "используйте генерацию кадра (цель Leonardo)."
                )
        else:
            targets = [s for s in targets if _scene_target(s) == "comfyui"]
            if not targets:
                raise RuntimeError(
                    "Нет сцен с целью ComfyUI. Смените «Цель» в настройках сцены "
                    "или запускайте Leonardo-сцены отдельно."
                )
        base_seed = int(settings.get("seed", -1))
        for scene in targets:
            sid = int(scene["id"])
            filename = str(scene.get("filename") or f"scene-{sid:02d}").strip()
            filename = Path(filename).stem or f"scene-{sid:02d}"
            ref = proj.ref_path(pid, filename)
            if ref is None:
                raise RuntimeError(
                    f"Нет референса Leonardo для {filename}. "
                    "Сначала нажмите «Референс» / «Все референсы»."
                )
            start_image = _upload_comfy_image(ref)
            for take in range(1, repeats + 1):
                _check_cancelled("video")
                take_msg = (
                    f"Генерация {filename} ({take}/{repeats})…"
                    if repeats > 1
                    else f"Генерация {filename}…"
                )
                _set_track(
                    "video",
                    status="running",
                    phase="generate",
                    scene_id=sid,
                    message=take_msg,
                    error=None,
                )
                dest = proj.next_variant_path(pid, filename, ".webm")
                # Unique Comfy output prefix per take (avoid history/file collisions)
                prefix = f"videomake/{pid}/{dest.stem}_{int(time.time())}_{take}"
                if base_seed < 0:
                    take_seed = random.randint(0, 2**31 - 1)
                elif repeats > 1:
                    take_seed = base_seed + take - 1
                else:
                    take_seed = base_seed
                _set_track(
                    "video",
                    message=(
                        f"Генерация {dest.name} (I2V)…"
                        if repeats == 1
                        else f"{filename}: take {take}/{repeats} → {dest.name}…"
                    ),
                )
                prompt = build_wan_prompt(
                    _compose_positive(scene, character_bible, world_bible),
                    scene.get("negative_prompt") or DEFAULT_NEGATIVE,
                    prefix,
                    seed=take_seed,
                    width=settings["width"],
                    height=settings["height"],
                    length=settings["length"],
                    steps=settings["steps"],
                    cfg=settings["cfg"],
                    shift=settings["shift"],
                    sampler=settings.get("sampler"),
                    scheduler=settings.get("scheduler"),
                    denoise=settings.get("denoise"),
                    fps=settings.get("fps"),
                    unet=settings.get("unet"),
                    clip=settings.get("clip"),
                    vae=settings.get("vae"),
                    weight_dtype=settings.get("weight_dtype"),
                    start_image=start_image,
                )
                _queue_and_wait(prompt, dest)
                if not dest.is_file() or dest.stat().st_size < 1000:
                    raise RuntimeError(f"Клип не сохранился: {dest.name}")
                primary = proj.promote_clip(pid, dest.name)
                variants = proj.list_scene_variants(pid, filename)
                _set_track(
                    "video",
                    last_clip={
                        "scene_id": sid,
                        "filename": filename,
                        "clip": dest.name,
                        "primary": primary,
                        "variants": variants,
                        "t": int(dest.stat().st_mtime * 1000),
                    },
                )
        done_msg = (
            f"Клипы готовы ({repeats}× на сцену)." if repeats > 1 else "Клипы готовы."
        )
        _set_track("video", status="idle", phase="", scene_id=None, message=done_msg, error=None)
    except JobCancelled:
        _interrupt_comfy()
        _set_track("video", status="idle", phase="", scene_id=None, message="Генерация прервана.", error=None)
    except Exception as exc:
        _set_track("video", status="error", message=str(exc), error=str(exc))
    finally:
        _CANCEL_VIDEO.clear()


def _scene_target(scene: dict) -> str:
    raw = str(scene.get("target") or "comfyui").strip().lower()
    if raw in {"leonardo", "leo", "api", "leonardo_api"}:
        return "leonardo"
    return "comfyui"


def _run_leonardo_refs(
    scenes: list[dict],
    only_id: int | None,
    character_bible: str = "",
    wan: dict | None = None,
    project_id: str | None = None,
    model: str | None = None,
    only_ids: list[int] | None = None,
    only_target: str | None = None,
    world_bible: str = "",
) -> None:
    try:
        if not leo.configured():
            raise RuntimeError(
                "LEONARDO_API_KEY не задан. Добавьте ключ в .env "
                "(тот же, что в Cursor MCP leonardo-gateway)."
            )
        pid = project_id or proj.require_current()
        settings = _sanitize_wan(wan)
        model_id = (model or leo.model_id()).strip()
        targets = scenes
        if only_id is not None:
            targets = [s for s in scenes if int(s.get("id", 0)) == only_id]
            if not targets:
                raise RuntimeError(f"Scene {only_id} not found.")
        elif only_ids:
            want = {int(x) for x in only_ids}
            targets = [s for s in scenes if int(s.get("id", 0)) in want]
            if not targets:
                raise RuntimeError("Scene ids not found for Leonardo.")
        elif only_target:
            want = _scene_target({"target": only_target})
            targets = [s for s in scenes if _scene_target(s) == want]
            if not targets:
                raise RuntimeError(f"Нет сцен с целью {want}.")
        seen: set[str] = set()
        prepared: list[dict] = []
        for scene in targets:
            sid = int(scene.get("id") or 0)
            filename = str(scene.get("filename") or f"scene-{sid:02d}").strip()
            filename = Path(filename).stem
            if not filename.startswith("scene-"):
                filename = f"scene-{sid:02d}"
            if filename in seen:
                filename = f"scene-{sid:02d}"
            seen.add(filename)
            item = dict(scene)
            item["id"] = sid
            item["filename"] = filename
            prepared.append(item)

        errors: list[str] = []
        done = 0
        leo_cancel = lambda: _check_cancelled("leonardo")
        for index, scene in enumerate(prepared):
            leo_cancel()
            sid = int(scene["id"])
            filename = scene["filename"]
            scene_model = str(
                scene.get("leonardo_model")
                or scene.get("leonardo_model_id")
                or model_id
            ).strip() or model_id
            _set_track(
                "leonardo",
                status="running",
                phase="leonardo",
                scene_id=sid,
                message=f"Leonardo рисует {filename} ({index + 1}/{len(prepared)})…",
                error=None,
                last_ref=None,
            )
            dest_stem = proj.refs_dir(pid) / filename
            try:
                path = leo.generate_ref_to_file(
                    scene,
                    dest_stem,
                    character_bible=character_bible,
                    world_bible=world_bible,
                    width=settings["width"],
                    height=settings["height"],
                    model=scene_model,
                    cancel_check=leo_cancel,
                )
                done += 1
                bust = int(time.time() * 1000)
                try:
                    bust = int(path.stat().st_mtime * 1000) or bust
                except OSError:
                    pass
                _set_track(
                    "leonardo",
                    last_ref={
                        "scene_id": sid,
                        "ref": path.name,
                        "filename": filename,
                        "t": bust,
                    },
                    refs_ready=_list_ref_names(pid),
                    message=f"Референс готов: {filename} ({done}/{len(prepared)})",
                )
            except JobCancelled:
                raise
            except Exception as exc:
                errors.append(f"{filename}: {exc}")
                _set_track("leonardo", message=f"Ошибка {filename}: {exc}")
            if index + 1 < len(prepared):
                for _ in range(4):
                    leo_cancel()
                    time.sleep(0.5)

        refs_ready = _list_ref_names(pid)
        if done == 0 and errors:
            raise RuntimeError("Leonardo не нарисовал ни одного референса. " + " | ".join(errors[:3]))
        msg = f"Референсы Leonardo готовы: {done}/{len(prepared)}."
        if errors:
            msg += " Ошибки: " + " | ".join(errors[:3])
        _set_track(
            "leonardo",
            status="idle" if done else "error",
            phase="",
            scene_id=None,
            message=msg,
            error=("; ".join(errors[:3]) if done == 0 else None),
            refs_ready=refs_ready,
        )
    except JobCancelled:
        _set_track("leonardo", status="idle", phase="", scene_id=None, message="Leonardo прерван.", error=None)
    except Exception as exc:
        _set_track("leonardo", status="error", message=str(exc), error=str(exc))
    finally:
        _CANCEL_LEO.clear()


def _list_ref_names(project_id: str) -> list[dict]:
    d = proj.refs_dir(project_id)
    if not d.is_dir():
        return []
    items = []
    for path in sorted(d.iterdir()):
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} and path.is_file():
            try:
                mtime_ms = int(path.stat().st_mtime * 1000)
            except OSError:
                mtime_ms = 0
            items.append({"name": path.name, "t": mtime_ms})
    return items


def _run_stitch(project_id: str | None = None) -> None:
    try:
        pid = project_id or proj.require_current()
        _set_track("video", status="running", phase="stitch", scene_id=None, message="Склейка…", error=None)
        scenes = proj.scenes_dir(pid)
        final = proj.final_mp4(pid)
        scenes.mkdir(parents=True, exist_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, str(STITCH), str(scenes), str(final)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "stitch failed")
        _set_track("video", status="idle", phase="", message="Склейка готова.", error=None)
    except Exception as exc:
        _set_track("video", status="error", message=str(exc), error=str(exc))
    finally:
        _CANCEL_VIDEO.clear()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        file_size = path.stat().st_size
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                unit, _, spec = range_header.partition("=")
                start_s, _, end_s = spec.partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else file_size - 1
                if end >= file_size:
                    end = file_size - 1
                if start > end or start < 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with path.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            except ValueError:
                pass

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile, length=64 * 1024)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/leonardo/models":
            self._json(
                {
                    "models": leo.list_ref_models() if leo.configured() else [],
                    "current": leo.model_id() if leo.configured() else None,
                }
            )
            return
        if path == "/api/health":
            self._json(_health())
            return
        if path == "/api/wan/options":
            self._json(_comfy_wan_options(force=True))
            return
        if path == "/api/job":
            self._json(_snapshot())
            return
        if path == "/api/projects":
            self._json({"projects": proj.list_projects(), "current": proj.get_current()})
            return
        if path == "/api/templates":
            self._json({"templates": proj.list_templates()})
            return
        if path == "/api/state":
            pid = proj.get_current()
            data: dict = {}
            meta: dict = {}
            if pid:
                data = proj.read_scenes(pid)
                meta = proj.read_meta(pid)
                # Attach ref filenames onto scenes for UI
                for scene in data.get("scenes") or []:
                    if not isinstance(scene, dict):
                        continue
                    fn = scene.get("filename") or f"scene-{int(scene.get('id') or 0):02d}"
                    ref = proj.ref_path(pid, fn)
                    scene["ref"] = ref.name if ref else None
                    variants = proj.list_scene_variants(pid, fn)
                    scene["clips"] = variants
                    selected = next((v["name"] for v in variants if v.get("selected")), None)
                    if not selected:
                        selected = proj.read_selected_clip(pid, fn)
                    # Prefer primary stitch file for playback when present
                    primary = None
                    for ext in (".webm", ".mp4", ".mkv", ".mov"):
                        candidate = proj.scenes_dir(pid) / f"{fn}{ext}"
                        if candidate.is_file():
                            primary = candidate.name
                            break
                    scene["clip"] = primary or selected
                    scene["selected_clip"] = selected
            snap = _snapshot()
            snap["project"] = data
            snap["meta"] = meta
            if pid and isinstance(data, dict):
                chars = data.get("characters")
                snap["project"]["characters"] = leo.characters_for_ui(
                    str(data.get("character_bible") or ""),
                    chars if isinstance(chars, list) else None,
                )
            snap["projects"] = proj.list_projects()
            snap["templates"] = proj.list_templates()
            self._json(snap)
            return
        if path == "/api/media/final.mp4":
            pid = proj.get_current()
            if not pid:
                self.send_error(404)
                return
            self._file(proj.final_mp4(pid), "video/mp4")
            return
        if path.startswith("/api/media/scene/"):
            pid = proj.get_current()
            if not pid:
                self.send_error(404)
                return
            name = Path(urllib.parse.unquote(path.rsplit("/", 1)[-1])).name
            suffix = Path(name).suffix.lower()
            mime = {
                ".mp4": "video/mp4",
                ".webm": "video/webm",
                ".mkv": "video/x-matroska",
                ".mov": "video/quicktime",
            }.get(suffix, "application/octet-stream")
            self._file(proj.scenes_dir(pid) / name, mime)
            return
        if path.startswith("/api/media/ref/"):
            pid = proj.get_current()
            if not pid:
                self.send_error(404)
                return
            name = Path(urllib.parse.unquote(path.rsplit("/", 1)[-1])).name
            suffix = Path(name).suffix.lower()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }.get(suffix, "application/octet-stream")
            self._file(proj.refs_dir(pid) / name, mime)
            return
        if path in {"/", "/index.html"}:
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js" or path.startswith("/app.js?"):
            self._file(STATIC / "app.js", "application/javascript; charset=utf-8")
            return
        if path == "/app.css" or path.startswith("/app.css?"):
            self._file(STATIC / "app.css", "text/css; charset=utf-8")
            return
        super().do_GET()

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/templates/"):
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            ok = proj.delete_template(name)
            self._json({"ok": ok}, 200 if ok else 404)
            return
        if path == "/api/project/ref":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self._json({"error": "Invalid JSON"}, 400)
                return
            pid = proj.require_current()
            scene = str(body.get("scene") or body.get("filename") or "")
            ref = proj.ref_path(pid, scene)
            if ref and ref.is_file():
                ref.unlink()
                self._json({"ok": True})
            else:
                self._json({"error": "Ref not found"}, 404)
            return
        if path == "/api/project/clip":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self._json({"error": "Invalid JSON"}, 400)
                return
            try:
                pid = proj.require_current()
                name = str(body.get("name") or body.get("clip") or "").strip()
                if not name:
                    self._json({"error": "name required"}, 400)
                    return
                result = proj.delete_clip(pid, name)
                with _JOB_LOCK:
                    lc = _MEDIA.get("last_clip")
                    if isinstance(lc, dict) and lc.get("clip") == Path(name).name:
                        _MEDIA["last_clip"] = None
                self._json(result)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, 404)
            except Exception as exc:
                self._json({"error": str(exc)}, 400)
            return
        self._json({"error": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._json({"error": "Invalid JSON"}, 400)
            return

        if path == "/api/projects":
            script = str(body.get("script") or "")
            title = str(body.get("title") or "")
            pid = proj.create_project(script=script, title=title)
            self._json({"id": pid, "projects": proj.list_projects(), "current": pid})
            return

        if path == "/api/projects/select":
            pid = str(body.get("id") or "")
            try:
                proj.set_current(pid)
            except (ValueError, FileNotFoundError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"current": pid, "projects": proj.list_projects()})
            return

        if path == "/api/templates":
            name = str(body.get("name") or "").strip()
            shot = body.get("shot") if isinstance(body.get("shot"), dict) else {}
            if not name:
                self._json({"error": "name required"}, 400)
                return
            saved = proj.save_template(name, shot)
            self._json({"ok": True, "name": saved, "templates": proj.list_templates()})
            return

        if path == "/api/project/clip/select":
            try:
                pid = proj.require_current()
                name = str(body.get("name") or body.get("clip") or "").strip()
                if not name:
                    self._json({"error": "name required"}, 400)
                    return
                primary = proj.promote_clip(pid, name)
                stem = proj.scene_stem(name)
                self._json(
                    {
                        "ok": True,
                        "primary": primary,
                        "selected": name,
                        "variants": proj.list_scene_variants(pid, stem),
                    }
                )
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, 404)
            except Exception as exc:
                self._json({"error": str(exc)}, 400)
            return

        if path == "/api/project/ref":
            pid = proj.require_current()
            scene = str(body.get("scene") or body.get("filename") or "").strip()
            stem = Path(scene).stem
            if not stem.startswith("scene-"):
                self._json({"error": "scene must be like scene-01"}, 400)
                return
            b64 = str(body.get("data_base64") or "")
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            ext = str(body.get("ext") or ".png").lower()
            if not ext.startswith("."):
                ext = "." + ext
            if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                ext = ".png"
            try:
                import base64

                raw = base64.b64decode(b64)
            except Exception:
                self._json({"error": "Invalid base64"}, 400)
                return
            if len(raw) > 12 * 1024 * 1024:
                self._json({"error": "Image too large (max 12MB)"}, 400)
                return
            # remove previous refs for this stem
            rd = proj.refs_dir(pid)
            rd.mkdir(parents=True, exist_ok=True)
            for old in rd.glob(f"{stem}.*"):
                old.unlink()
            dest = rd / f"{stem}{ext}"
            dest.write_bytes(raw)
            self._json({"ok": True, "ref": dest.name})
            return

        if path == "/api/split":
            pid = proj.require_current()
            expand_model = str(
                body.get("expand_model")
                or body.get("model")
                or OLLAMA_EXPAND_MODEL
            ).strip()
            split_model = str(body.get("split_model") or OLLAMA_SPLIT_MODEL).strip()
            skip_expand = bool(body.get("skip_expand"))
            try:
                data = split_script(
                    body.get("script") or "",
                    scene_count=int(body.get("scenes") or 6),
                    expand_model=expand_model,
                    split_model=split_model,
                    host=OLLAMA_HOST,
                    timeout=int(body.get("timeout") or 240),
                    skip_expand=skip_expand,
                )
            except SplitError as exc:
                self._json({"error": str(exc)}, 400)
                return
            chars = leo.characters_for_ui(str(data.get("character_bible") or ""))
            if chars:
                data["characters"] = chars
                data["character_bible"] = leo.join_character_bible(chars)
            proj.write_scenes(pid, data)
            proj.write_meta(
                pid,
                script=str(body.get("script") or ""),
                title=str(data.get("title") or ""),
                model=f"{data.get('expand_model') or '-'} → {data.get('split_model') or split_model}",
                english_script=str(data.get("english_script") or ""),
            )
            self._json({**data, "project_id": pid})
            return

        if path == "/api/generate":
            pid = proj.require_current()
            scenes = body.get("scenes") or []
            only_id = body.get("scene_id")
            only_id = int(only_id) if only_id is not None else None
            try:
                repeats = max(1, min(int(body.get("repeats") or body.get("count") or 1), 20))
            except (TypeError, ValueError):
                repeats = 1
            character_bible = str(body.get("character_bible") or "").strip()
            world_bible = str(body.get("world_bible") or "").strip()
            wan = _sanitize_wan(body.get("wan") if isinstance(body.get("wan"), dict) else None)
            chars_in = body.get("characters") if isinstance(body.get("characters"), list) else None
            if chars_in is not None:
                characters = leo.characters_for_ui("", chars_in)
                character_bible = leo.join_character_bible(characters) or character_bible
            else:
                characters = leo.characters_for_ui(character_bible) if character_bible else []
            if not character_bible or not world_bible:
                saved = proj.read_scenes(pid)
                if not character_bible:
                    character_bible = str(saved.get("character_bible") or "").strip()
                    if not characters:
                        characters = leo.characters_for_ui(
                            character_bible,
                            saved.get("characters") if isinstance(saved.get("characters"), list) else None,
                        )
                if not world_bible:
                    world_bible = str(saved.get("world_bible") or "").strip()
            if not scenes:
                self._json({"error": "Нет сцен для генерации."}, 400)
                return
            normalized = []
            for raw in scenes:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item["target"] = _scene_target(item)
                lm = str(item.get("leonardo_model") or item.get("leonardo_model_id") or "").strip()
                if lm:
                    item["leonardo_model"] = lm
                normalized.append(item)
            scenes = normalized
            payload = {"scenes": scenes, "wan": wan}
            if character_bible:
                payload["character_bible"] = character_bible
            if characters:
                payload["characters"] = characters
            if world_bible:
                payload["world_bible"] = world_bible
            title = str(body.get("title") or payload.get("title") or "")
            if title:
                payload["title"] = title
            proj.write_scenes(pid, payload)
            if not _start_track(
                "video",
                _run_generate,
                scenes,
                only_id,
                character_bible,
                wan,
                pid,
                world_bible,
                repeats,
            ):
                self._json({"error": "Уже выполняется генерация видео / склейка."}, 409)
                return
            self._json({"ok": True, "wan": wan, "project_id": pid, "repeats": repeats})
            return

        if path == "/api/leonardo/refs":
            if not leo.configured():
                self._json(
                    {
                        "error": "LEONARDO_API_KEY не задан в .env. "
                        "Скопируйте ключ из Cursor MCP leonardo-gateway."
                    },
                    400,
                )
                return
            pid = proj.require_current()
            scenes = body.get("scenes") or []
            only_id = body.get("scene_id")
            only_id = int(only_id) if only_id is not None else None
            character_bible = str(body.get("character_bible") or "").strip()
            world_bible = str(body.get("world_bible") or "").strip()
            wan = _sanitize_wan(body.get("wan") if isinstance(body.get("wan"), dict) else None)
            model = str(body.get("model") or body.get("leonardo_model_id") or "").strip() or None
            only_ids_raw = body.get("only_ids") or body.get("scene_ids")
            only_ids: list[int] | None = None
            if isinstance(only_ids_raw, list) and only_ids_raw:
                only_ids = [int(x) for x in only_ids_raw]
            only_target = str(body.get("only_target") or body.get("target") or "").strip() or None
            chars_in = body.get("characters") if isinstance(body.get("characters"), list) else None
            if chars_in is not None:
                characters = leo.characters_for_ui("", chars_in)
                character_bible = leo.join_character_bible(characters) or character_bible
            else:
                characters = leo.characters_for_ui(character_bible) if character_bible else []
            if not character_bible or not world_bible:
                saved = proj.read_scenes(pid)
                if not character_bible:
                    character_bible = str(saved.get("character_bible") or "").strip()
                    if not characters:
                        characters = leo.characters_for_ui(
                            character_bible,
                            saved.get("characters") if isinstance(saved.get("characters"), list) else None,
                        )
                if not world_bible:
                    world_bible = str(saved.get("world_bible") or "").strip()
            # Leonardo refs use per-scene visual + filtered cast from bible (not full script)
            if not scenes:
                self._json({"error": "Нет сцен для референсов."}, 400)
                return
            # Normalize ids/filenames so every scene gets its own ref file
            normalized = []
            for raw in scenes:
                if not isinstance(raw, dict):
                    continue
                sid = int(raw.get("id") or 0)
                fn = str(raw.get("filename") or f"scene-{sid:02d}").strip()
                fn = Path(fn).stem or f"scene-{sid:02d}"
                item = dict(raw)
                item["id"] = sid
                item["filename"] = fn
                item.pop("script", None)
                # Keep target / per-scene Leonardo model
                target = str(item.get("target") or "comfyui").strip().lower()
                item["target"] = "leonardo" if target in {"leonardo", "leo", "api"} else "comfyui"
                lm = str(item.get("leonardo_model") or item.get("leonardo_model_id") or "").strip()
                if lm:
                    item["leonardo_model"] = lm
                normalized.append(item)
            scenes = normalized
            payload = {"scenes": scenes, "wan": wan}
            if character_bible:
                payload["character_bible"] = character_bible
            if characters:
                payload["characters"] = characters
            if world_bible:
                payload["world_bible"] = world_bible
            title = str(body.get("title") or "")
            if title:
                payload["title"] = title
            if model:
                payload["leonardo_model_id"] = model
            proj.write_scenes(pid, payload)
            if not _start_track(
                "leonardo",
                _run_leonardo_refs,
                scenes,
                only_id,
                character_bible,
                wan,
                pid,
                model,
                only_ids,
                only_target,
                world_bible,
            ):
                self._json({"error": "Уже выполняется генерация референсов Leonardo."}, 409)
                return
            self._json({"ok": True, "project_id": pid, "model": model or leo.model_id()})
            return

        if path == "/api/bible/parse":
            text = str(body.get("text") or body.get("character_bible") or "")
            entries = body.get("characters") if isinstance(body.get("characters"), list) else None
            characters = leo.characters_for_ui(text, entries)
            joined = leo.join_character_bible(characters) if characters else text.strip()
            self._json({"characters": characters, "character_bible": joined})
            return

        if path == "/api/project/bible":
            pid = proj.require_current()
            entries = body.get("characters") if isinstance(body.get("characters"), list) else None
            text = str(body.get("character_bible") or "").strip()
            world_bible = str(body.get("world_bible") or "").strip()
            if entries is not None:
                characters = leo.characters_for_ui("", entries)
                text = leo.join_character_bible(characters)
            else:
                characters = leo.characters_for_ui(text)
                text = leo.join_character_bible(characters) if characters else text
            saved = proj.read_scenes(pid)
            saved["character_bible"] = text
            saved["characters"] = characters
            if "world_bible" in body or world_bible:
                saved["world_bible"] = world_bible
            proj.write_scenes(pid, saved)
            self._json(
                {
                    "ok": True,
                    "project_id": pid,
                    "character_bible": text,
                    "characters": characters,
                    "world_bible": saved.get("world_bible") or "",
                }
            )
            return

        if path == "/api/cancel":
            track = str(body.get("track") or "").strip().lower() or None
            if track in {"", "all"}:
                track = None
            if track not in {None, "video", "leonardo"}:
                self._json({"error": "track: video | leonardo | all"}, 400)
                return
            if not _request_cancel(track):
                _force_reset_job()
                self._json({"ok": True, "message": "Задача сброшена."})
                return
            self._json({"ok": True, "message": "Прерывание…", "track": track or "all"})
            return

        if path == "/api/job/reset":
            _force_reset_job()
            self._json({"ok": True, "message": "Задача сброшена."})
            return

        if path == "/api/stitch":
            pid = proj.require_current()
            if not _start_track("video", _run_stitch, pid):
                self._json({"error": "Уже выполняется генерация видео / склейка."}, 409)
                return
            self._json({"ok": True, "project_id": pid})
            return

        self._json({"error": "Not found"}, 404)


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    proj.ensure_dirs()
    if not proj.get_current():
        # Keep UX working on first launch
        proj.create_project()

    class _Server(ThreadingHTTPServer):
        # Windows SO_REUSEADDR lets a second UI steal the port while the old
        # process keeps answering — then variants never get written.
        allow_reuse_address = False

    try:
        httpd = _Server((HOST, PORT), Handler)
    except OSError as exc:
        raise SystemExit(
            f"Порт {HOST}:{PORT} занят — остановите старый UI (python ui/server.py) "
            f"и запустите снова. ({exc})"
        ) from exc
    print(f"videomake UI: http://{HOST}:{PORT}", flush=True)
    print(f"Ollama: {OLLAMA_HOST}", flush=True)
    print(f"ComfyUI: {COMFYUI_URL}", flush=True)
    print(f"Project: {proj.get_current()}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
        httpd.server_close()


if __name__ == "__main__":
    main()
