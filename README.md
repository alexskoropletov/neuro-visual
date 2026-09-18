# videomake

Локальный гибридный text-to-video без облачных фильтров: из текста собирается сценарий, **Wan 2.2** в **ComfyUI** генерирует клипы, **ffmpeg** склеивает финальный mp4.

Локальный UI оркестрирует пайплайн. Генерация кадров по-прежнему идёт в ComfyUI.

```text
текст (RU) → Ollama qwen (EN + детали)
      → Ollama dolphin → промпты сцен
      → Leonardo → референс-кадр на сцену
      → ComfyUI + Wan 2.2 (I2V) → клип в карточке сцены
      → ffmpeg → final.mp4
```

## Ограничения

- Всё крутится на своей машине. После скачивания весов интернет не нужен.
- Контент 18+: взрослые персонажи. Генерация с участием несовершеннолетних запрещена.
- Облачные inference API, safety-checker и partner/API-ноды ComfyUI не используются.

## Железо

Минимум для дефолтной модели **Wan2.2-TI2V-5B**:

- GPU: ~8 GB VRAM (NVIDIA, нативный offload ComfyUI)
- RAM: 32 GB желательно (16 GB часто упирается в page file)
- Диск: SSD, десятки гигабайт под веса (5B fp16 + UMT5 + VAE)

Старт генерации: **832×480**, **81 кадр**, 24 fps (~3.4 с на сцену).

**Wan 2.2 T2V 14B** — опциональный апгрейд при 16–24 GB+ VRAM, см. [docs/003-setup-comfyui.md](docs/003-setup-comfyui.md).

## Быстрый старт

1. Поставить ComfyUI и веса Wan 2.2 TI2V-5B — [docs/003-setup-comfyui.md](docs/003-setup-comfyui.md).
2. Поставить [Ollama](https://ollama.com/download) и модель без встроенного отказа, например `dolphin-llama3`.
3. Поставить `ffmpeg`.
4. Скопировать `.env.example` → `.env` и заполнить URL/ключи (`LEONARDO_API_KEY` и т.д.). Файл `.env` в git не коммитится.
5. Поднять весь стек одной командой (Ollama, ComfyUI, UI):

   ```bash
   make
   ```

   Открыть [http://127.0.0.1:8765](http://127.0.0.1:8765). Остановка: `make down`. Логи: `make logs`. Если ComfyUI лежит не в `./ComfyUI`, `../ComfyUI` или `~/ComfyUI`:

   ```bash
   make COMFYUI_DIR=/path/to/ComfyUI
   ```

   Только UI, если сервисы уже запущены: `python3 ui/server.py`.

Результаты работы (сценарии, Leonardo-референсы, клипы, `final.mp4`) лежат в `output/` и в репозиторий не попадают.

CLI без UI по-прежнему работает: `scripts/split_scenes.py`, ComfyUI workflow, `scripts/stitch.sh`.

Подробный цикл: [docs/004-pipeline.md](docs/004-pipeline.md). Архитектура: [docs/002-architecture.md](docs/002-architecture.md). Inference на LAN (`.188`): [docs/005-lan-gpu-host.md](docs/005-lan-gpu-host.md). Проекты / шаблоны / референсы: [docs/006-roadmap-projects.md](docs/006-roadmap-projects.md). Исходные ссылки: [docs/001-links.md](docs/001-links.md).

## Что в репозитории

| Путь | Назначение |
| --- | --- |
| `Makefile` | `make` поднимает Ollama + ComfyUI + UI |
| `ui/` | Локальный веб-UI (`python3 ui/server.py`) |
| `docs/` | Цель, установка, пайплайн |
| `workflows/wan22-ti2v-5b.json` | ComfyUI workflow на базе официального шаблона Wan 2.2 5B |
| `scripts/split_scenes.py` | Qwen (RU→EN+детали) → Dolphin (нарезка сцен) |
| `scripts/stitch.py` | Склейка клипов ffmpeg (Windows/macOS/Linux) |
| `scripts/stitch.sh` | То же для bash/Linux |

Вне скоупа: поставка LoRA, озвучка и субтитры.
