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
SCENES_DIR = ROOT / "output" / "scenes"
FINAL_MP4 = ROOT / "output" / "final.mp4"
SCENES_JSON = ROOT / "output" / "scenes.json"
STITCH = ROOT / "scripts" / "stitch.sh"

sys.path.insert(0, str(ROOT / "scripts"))
from split_scenes import DEFAULT_NEGATIVE, SplitError, split_script  # noqa: E402

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
HOST = os.environ.get("VIDEOMAKE_HOST", "127.0.0.1")
PORT = int(os.environ.get("VIDEOMAKE_PORT", "8765"))

_JOB_LOCK = threading.Lock()
_JOB = {
    "status": "idle",
    "phase": "",
    "scene_id": None,
    "message": "",
    "error": None,
    "clips": [],
    "final": False,
}


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


def _health() -> dict:
    ollama_ok = _reachable(f"{OLLAMA_HOST}/api/tags")
    comfy_ok = _reachable(f"{COMFYUI_URL}/system_stats")
    return {
        "ollama": ollama_ok,
        "comfyui": comfy_ok,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ollama_host": OLLAMA_HOST,
        "comfyui_url": COMFYUI_URL,
    }


def _list_clips() -> list[dict]:
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    clips = []
    for path in sorted(SCENES_DIR.iterdir()):
        if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"} and path.is_file():
            clips.append({"name": path.name, "bytes": path.stat().st_size})
    return clips


def _snapshot() -> dict:
    with _JOB_LOCK:
        job = dict(_JOB)
    job["clips"] = _list_clips()
    job["final"] = FINAL_MP4.is_file()
    return job


def _set_job(**kwargs) -> None:
    with _JOB_LOCK:
        _JOB.update(kwargs)


def build_wan_prompt(
    positive: str,
    negative: str,
    filename_prefix: str,
    *,
    seed: int | None = None,
    width: int = 832,
    height: int = 480,
    length: int = 81,
) -> dict:
    seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
    return {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "wan2.2_ti2v_5B_fp16.safetensors",
                "weight_dtype": "default",
            },
        },
        "38": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        },
        "39": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "wan2.2_vae.safetensors"},
        },
        "48": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["37", 0], "shift": 8.0},
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
            "inputs": {
                "vae": ["39", 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
            },
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["48", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["55", 0],
                "seed": seed,
                "steps": 30,
                "cfg": 5.0,
                "sampler_name": "uni_pc",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
        },
        "47": {
            "class_type": "SaveWEBM",
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": filename_prefix,
                "codec": "vp9",
                "fps": 24.0,
                "crf": 16,
            },
        },
    }


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


def _queue_and_wait(prompt: dict, dest: Path, timeout: int = 3600) -> None:
    submitted = _http_json(f"{COMFYUI_URL}/prompt", {"prompt": prompt}, timeout=60)
    prompt_id = submitted.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not accept the prompt: {submitted}")
    deadline = time.time() + timeout
    while time.time() < deadline:
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


def _run_generate(scenes: list[dict], only_id: int | None) -> None:
    try:
        targets = scenes
        if only_id is not None:
            targets = [s for s in scenes if int(s.get("id", 0)) == only_id]
            if not targets:
                raise RuntimeError(f"Scene {only_id} not found.")
        for scene in targets:
            sid = int(scene["id"])
            filename = scene.get("filename") or f"scene-{sid:02d}"
            _set_job(
                status="running",
                phase="generate",
                scene_id=sid,
                message=f"Генерация {filename}…",
                error=None,
            )
            dest = SCENES_DIR / f"{filename}.webm"
            prompt = build_wan_prompt(
                scene.get("visual_prompt") or "",
                scene.get("negative_prompt") or DEFAULT_NEGATIVE,
                f"scenes/{filename}",
            )
            _queue_and_wait(prompt, dest)
        _set_job(status="idle", phase="", scene_id=None, message="Клипы готовы.", error=None)
    except Exception as exc:
        _set_job(status="error", message=str(exc), error=str(exc))


def _run_stitch() -> None:
    try:
        _set_job(status="running", phase="stitch", scene_id=None, message="Склейка…", error=None)
        SCENES_DIR.mkdir(parents=True, exist_ok=True)
        FINAL_MP4.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["bash", str(STITCH), str(SCENES_DIR), str(FINAL_MP4)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "stitch failed")
        _set_job(status="idle", phase="", message="Склейка готова.", error=None)
    except Exception as exc:
        _set_job(status="error", message=str(exc), error=str(exc))


def _start_thread(fn, *args) -> bool:
    with _JOB_LOCK:
        if _JOB["status"] == "running":
            return False
        _JOB.update(status="running", error=None, message="Старт…")
    threading.Thread(target=fn, args=args, daemon=True).start()
    return True


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
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json(_health())
            return
        if path == "/api/job":
            self._json(_snapshot())
            return
        if path == "/api/state":
            data = {}
            if SCENES_JSON.is_file():
                data = json.loads(SCENES_JSON.read_text(encoding="utf-8"))
            snap = _snapshot()
            snap["project"] = data
            self._json(snap)
            return
        if path == "/api/media/final.mp4":
            self._file(FINAL_MP4, "video/mp4")
            return
        if path.startswith("/api/media/scene/"):
            name = Path(urllib.parse.unquote(path.rsplit("/", 1)[-1])).name
            suffix = Path(name).suffix.lower()
            mime = {
                ".mp4": "video/mp4",
                ".webm": "video/webm",
                ".mkv": "video/x-matroska",
                ".mov": "video/quicktime",
            }.get(suffix, "application/octet-stream")
            self._file(SCENES_DIR / name, mime)
            return
        if path in {"/", "/index.html"}:
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._json({"error": "Invalid JSON"}, 400)
            return

        if path == "/api/split":
            try:
                data = split_script(
                    body.get("script") or "",
                    scene_count=int(body.get("scenes") or 6),
                    model=str(body.get("model") or "dolphin-llama3"),
                    host=OLLAMA_HOST,
                    timeout=int(body.get("timeout") or 180),
                )
            except SplitError as exc:
                self._json({"error": str(exc)}, 400)
                return
            SCENES_JSON.parent.mkdir(parents=True, exist_ok=True)
            SCENES_JSON.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            self._json(data)
            return

        if path == "/api/generate":
            scenes = body.get("scenes") or []
            only_id = body.get("scene_id")
            only_id = int(only_id) if only_id is not None else None
            if not scenes:
                self._json({"error": "Нет сцен для генерации."}, 400)
                return
            SCENES_JSON.parent.mkdir(parents=True, exist_ok=True)
            SCENES_JSON.write_text(
                json.dumps({"scenes": scenes}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if not _start_thread(_run_generate, scenes, only_id):
                self._json({"error": "Уже выполняется другая задача."}, 409)
                return
            self._json({"ok": True})
            return

        if path == "/api/stitch":
            if not _start_thread(_run_stitch):
                self._json({"error": "Уже выполняется другая задача."}, 409)
                return
            self._json({"ok": True})
            return

        self._json({"error": "Not found"}, 404)


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"videomake UI: http://{HOST}:{PORT}", flush=True)
    print(f"Ollama: {OLLAMA_HOST}", flush=True)
    print(f"ComfyUI: {COMFYUI_URL}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
        httpd.server_close()


if __name__ == "__main__":
    main()
