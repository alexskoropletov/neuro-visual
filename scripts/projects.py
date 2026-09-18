"""Project folders under output/projects/<timestamp>/."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
PROJECTS = OUTPUT / "projects"
TEMPLATES = OUTPUT / "templates"
CURRENT_FILE = OUTPUT / "current_project.json"

SAFE_NAME = re.compile(r"^[\w.\-]+$")
PROJECT_ID_RE = re.compile(r"^\d{8}-\d{6}$")


def ensure_dirs() -> None:
    PROJECTS.mkdir(parents=True, exist_ok=True)
    TEMPLATES.mkdir(parents=True, exist_ok=True)


def new_project_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def is_project_id(pid: str) -> bool:
    return bool(PROJECT_ID_RE.match(pid or ""))


def project_dir(pid: str) -> Path:
    if not is_project_id(pid):
        raise ValueError(f"Invalid project id: {pid}")
    return PROJECTS / pid


def scenes_dir(pid: str) -> Path:
    return project_dir(pid) / "scenes"


def refs_dir(pid: str) -> Path:
    return project_dir(pid) / "refs"


def scenes_json(pid: str) -> Path:
    return project_dir(pid) / "scenes.json"


def meta_json(pid: str) -> Path:
    return project_dir(pid) / "meta.json"


def final_mp4(pid: str) -> Path:
    return project_dir(pid) / "final.mp4"


def create_project(script: str = "", title: str = "") -> str:
    ensure_dirs()
    import time

    pid = new_project_id()
    attempts = 0
    while project_dir(pid).exists():
        attempts += 1
        if attempts > 5:
            raise RuntimeError("Could not allocate project id")
        time.sleep(1)
        pid = new_project_id()
    d = project_dir(pid)
    (d / "scenes").mkdir(parents=True, exist_ok=True)
    (d / "refs").mkdir(parents=True, exist_ok=True)
    meta = {
        "id": pid,
        "title": title or "",
        "script": script or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta_json(pid).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scenes_json(pid).write_text(
        json.dumps({"title": title or "", "character_bible": "", "scenes": []}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    set_current(pid)
    return pid


def set_current(pid: str) -> None:
    if not is_project_id(pid):
        raise ValueError(f"Invalid project id: {pid}")
    if not project_dir(pid).is_dir():
        raise FileNotFoundError(pid)
    ensure_dirs()
    CURRENT_FILE.write_text(json.dumps({"id": pid}, indent=2) + "\n", encoding="utf-8")


def get_current() -> str | None:
    if not CURRENT_FILE.is_file():
        return None
    try:
        data = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
        pid = str(data.get("id") or "")
        if is_project_id(pid) and project_dir(pid).is_dir():
            return pid
    except Exception:
        return None
    return None


def require_current() -> str:
    pid = get_current()
    if not pid:
        pid = create_project()
    return pid


def list_projects() -> list[dict]:
    ensure_dirs()
    items: list[dict] = []
    for path in sorted(PROJECTS.iterdir(), reverse=True):
        if not path.is_dir() or not is_project_id(path.name):
            continue
        meta = {}
        mp = path / "meta.json"
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        clips = 0
        sd = path / "scenes"
        if sd.is_dir():
            clips = sum(
                1
                for p in sd.iterdir()
                if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
            )
        items.append(
            {
                "id": path.name,
                "title": meta.get("title") or "",
                "created_at": meta.get("created_at") or "",
                "clips": clips,
                "has_final": (path / "final.mp4").is_file(),
            }
        )
    return items


def read_meta(pid: str) -> dict:
    path = meta_json(pid)
    if not path.is_file():
        return {"id": pid, "title": "", "script": "", "created_at": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"id": pid, "title": "", "script": "", "created_at": ""}


def write_meta(pid: str, **kwargs) -> dict:
    meta = read_meta(pid)
    meta.update({k: v for k, v in kwargs.items() if v is not None})
    meta["id"] = pid
    meta_json(pid).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def read_scenes(pid: str) -> dict:
    path = scenes_json(pid)
    if not path.is_file():
        return {"title": "", "character_bible": "", "scenes": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"title": "", "character_bible": "", "scenes": []}
        data.setdefault("scenes", [])
        return data
    except Exception:
        return {"title": "", "character_bible": "", "scenes": []}


def write_scenes(pid: str, data: dict) -> None:
    scenes_json(pid).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


CLIP_EXTS = {".mp4", ".webm", ".mkv", ".mov"}
# Primary stitch file: scene-01.webm — variants: scene-01__v01.webm
SCENE_STEM_RE = re.compile(r"^(scene-\d+)$", re.I)
SCENE_VARIANT_RE = re.compile(r"^(scene-\d+)__v(\d+)$", re.I)


def list_clips(pid: str) -> list[dict]:
    d = scenes_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    clips = []
    for path in sorted(d.iterdir()):
        if path.suffix.lower() in CLIP_EXTS and path.is_file():
            clips.append({"name": path.name, "bytes": path.stat().st_size})
    return clips


def scene_stem(filename: str) -> str:
    stem = Path(filename or "").stem
    m = SCENE_VARIANT_RE.match(stem) or SCENE_STEM_RE.match(stem)
    if m:
        return m.group(1).lower()
    return stem.lower()


def is_primary_clip_name(name: str) -> bool:
    stem = Path(name or "").stem
    return bool(SCENE_STEM_RE.match(stem)) and Path(name).suffix.lower() in CLIP_EXTS


def _selected_marker(pid: str, stem: str) -> Path:
    return scenes_dir(pid) / f"{stem}.selected"


def read_selected_marker_raw(pid: str, filename: str) -> str | None:
    stem = scene_stem(filename)
    marker = _selected_marker(pid, stem)
    if not marker.is_file():
        return None
    name = marker.read_text(encoding="utf-8").strip()
    return Path(name).name if name else None


def read_selected_clip(pid: str, filename: str) -> str | None:
    """Return selected take filename if the file still exists."""
    stem = scene_stem(filename)
    raw = read_selected_marker_raw(pid, stem)
    if raw:
        if (scenes_dir(pid) / raw).is_file():
            return raw
        # Stale marker (deleted take) — drop so reload won't resurrect it
        marker = _selected_marker(pid, stem)
        if marker.is_file():
            marker.unlink()
    return None


def list_scene_variants(pid: str, filename: str) -> list[dict]:
    """All takes for scene-NN (__vNN). Primary scene-NN.webm is only a stitch copy."""
    stem = scene_stem(filename)
    if not SCENE_STEM_RE.match(stem):
        return []
    d = scenes_dir(pid)
    if not d.is_dir():
        return []
    _migrate_primary_to_variant(pid, stem)
    selected = read_selected_clip(pid, stem)
    variants: list[dict] = []
    for path in sorted(d.iterdir()):
        if not path.is_file() or path.suffix.lower() not in CLIP_EXTS:
            continue
        m = SCENE_VARIANT_RE.match(path.stem)
        if m and m.group(1).lower() == stem:
            variants.append(
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "index": int(m.group(2)),
                    "selected": selected == path.name,
                }
            )
    if not variants:
        return []
    if any(v["selected"] for v in variants):
        return variants
    # No valid marker: prefer take matching primary size, else last take
    primary = None
    for ext in (".webm", ".mp4", ".mkv", ".mov"):
        cand = d / f"{stem}{ext}"
        if cand.is_file():
            primary = cand
            break
    picked = None
    if primary is not None:
        for v in variants:
            vp = d / v["name"]
            if vp.is_file() and vp.stat().st_size == primary.stat().st_size:
                picked = v
                break
    if picked is None:
        picked = variants[-1]
    picked["selected"] = True
    _selected_marker(pid, stem).write_text(picked["name"] + "\n", encoding="utf-8")
    return variants


def _migrate_primary_to_variant(pid: str, stem: str) -> None:
    """If only scene-NN.webm exists (no __v takes), copy it to scene-NN__v01.webm."""
    d = scenes_dir(pid)
    has_variant = False
    primary = None
    for path in d.iterdir():
        if not path.is_file() or path.suffix.lower() not in CLIP_EXTS:
            continue
        if SCENE_VARIANT_RE.match(path.stem) and path.stem.lower().startswith(stem + "__v"):
            has_variant = True
        elif SCENE_STEM_RE.match(path.stem) and path.stem.lower() == stem:
            primary = path
    if has_variant or primary is None:
        return
    dest = d / f"{stem}__v01{primary.suffix.lower()}"
    if dest.exists():
        return
    shutil.copy2(primary, dest)
    marker = _selected_marker(pid, stem)
    if not marker.is_file():
        marker.write_text(dest.name + "\n", encoding="utf-8")


def next_variant_path(pid: str, filename: str, ext: str = ".webm") -> Path:
    stem = scene_stem(filename)
    if not SCENE_STEM_RE.match(stem):
        raise ValueError(f"Invalid scene filename: {filename}")
    d = scenes_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for path in d.iterdir():
        if not path.is_file() or path.suffix.lower() not in CLIP_EXTS:
            continue
        m = SCENE_VARIANT_RE.match(path.stem)
        if m and m.group(1).lower() == stem:
            max_n = max(max_n, int(m.group(2)))
    n = max_n + 1
    while True:
        candidate = d / f"{stem}__v{n:02d}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


def promote_clip(pid: str, variant_name: str) -> str:
    """Copy a take to the primary scene-NN.ext used by stitch. Returns primary name."""
    name = Path(variant_name).name
    src = scenes_dir(pid) / name
    if not src.is_file():
        raise FileNotFoundError(f"Clip not found: {name}")
    stem = scene_stem(name)
    if not SCENE_STEM_RE.match(stem):
        raise ValueError(f"Not a scene clip: {name}")
    ext = src.suffix.lower() or ".webm"
    d = scenes_dir(pid)
    for other in CLIP_EXTS:
        old = d / f"{stem}{other}"
        if old.is_file() and old.resolve() != src.resolve():
            old.unlink()
    dest = d / f"{stem}{ext}"
    if dest.resolve() != src.resolve():
        shutil.copy2(src, dest)
    _selected_marker(pid, stem).write_text(name + "\n", encoding="utf-8")
    return dest.name


def delete_clip(pid: str, clip_name: str) -> dict:
    """Delete a take; if it was selected, promote another or clear primary."""
    name = Path(clip_name).name
    d = scenes_dir(pid)
    path = d / name
    if not path.is_file():
        raise FileNotFoundError(f"Clip not found: {name}")
    stem = scene_stem(name)
    marker_raw = read_selected_marker_raw(pid, stem)
    selected = read_selected_clip(pid, stem)
    was_selected = marker_raw == name or selected == name or is_primary_clip_name(name)
    path.unlink()
    # Always clear marker if it named the deleted file
    if marker_raw == name:
        marker = _selected_marker(pid, stem)
        if marker.is_file():
            marker.unlink()
    if is_primary_clip_name(name):
        remaining = list_scene_variants(pid, stem)
        marker = _selected_marker(pid, stem)
        if remaining:
            keep = (
                selected
                if selected and any(v["name"] == selected for v in remaining)
                else remaining[-1]["name"]
            )
            promote_clip(pid, keep)
            return {"ok": True, "variants": list_scene_variants(pid, stem)}
        if marker.is_file():
            marker.unlink()
        return {"ok": True, "variants": []}
    remaining = [v for v in list_scene_variants(pid, stem) if v["name"] != name]
    if was_selected:
        for ext in CLIP_EXTS:
            p = d / f"{stem}{ext}"
            if p.is_file():
                p.unlink()
        if remaining:
            promote_clip(pid, remaining[-1]["name"])
            return {"ok": True, "variants": list_scene_variants(pid, stem)}
        marker = _selected_marker(pid, stem)
        if marker.is_file():
            marker.unlink()
        return {"ok": True, "variants": []}
    return {"ok": True, "variants": list_scene_variants(pid, stem)}


def ref_path(pid: str, scene_filename: str) -> Path | None:
    """Return existing ref file for scene-01 / scene-01.webm stem, if any."""
    stem = Path(scene_filename).stem
    if not stem.startswith("scene-"):
        return None
    d = refs_dir(pid)
    if not d.is_dir():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        candidate = d / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def list_templates() -> list[dict]:
    ensure_dirs()
    items = []
    for path in sorted(TEMPLATES.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        items.append({"name": path.stem, "shot": data.get("shot") or data})
    return items


def save_template(name: str, shot: dict) -> str:
    ensure_dirs()
    safe = re.sub(r"[^\w.\-]+", "-", name.strip())[:64].strip("-") or "template"
    path = TEMPLATES / f"{safe}.json"
    path.write_text(
        json.dumps({"name": safe, "shot": shot}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return safe


def delete_template(name: str) -> bool:
    safe = Path(name).stem
    path = TEMPLATES / f"{safe}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def load_template(name: str) -> dict | None:
    safe = Path(name).stem
    path = TEMPLATES / f"{safe}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("shot") if isinstance(data.get("shot"), dict) else data
    except Exception:
        return None
