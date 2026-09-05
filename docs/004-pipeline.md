# Пайплайн: текст → сцены → клипы → склейка

Основной путь — локальный UI:

```bash
make
```

Открыть `http://127.0.0.1:8765`. Команда поднимает Ollama, ComfyUI и UI, если они ещё не запущены. Стоп: `make down`. Нужны установленные Ollama, ffmpeg и клон ComfyUI (`COMFYUI_DIR`, иначе ищет `./ComfyUI`, `../ComfyUI`, `~/ComfyUI`).

Только интерфейс, если сервисы уже крутятся: `python3 ui/server.py` или `make ui`.

Ручной CLI тот же пайплайн без UI.

## 1. Сценарий

Текст в файл, например `script.txt`. Это может быть сюжет, список кадров или длинный промпт. Персонажи — только взрослые (18+).

## 2. Нарезка сцен (Ollama)

Нужны [Ollama](https://ollama.com/download) и модель без встроенного отказа:

```bash
ollama pull dolphin-llama3
```

```bash
python3 scripts/split_scenes.py --input script.txt --output scenes.json
```

Полезные флаги: `--model`, `--scenes 6` (диапазон 4–8), `--host http://127.0.0.1:11434`.

Выход — JSON:

```json
{
  "title": "…",
  "character_bible": "одни и те же взрослые персонажи на все сцены",
  "scenes": [
    {
      "id": 1,
      "filename": "scene-01",
      "visual_prompt": "cinematic visual prompt for Wan…",
      "negative_prompt": "…",
      "duration_sec": 4,
      "notes": "…"
    }
  ]
}
```

`visual_prompt` копируется в ComfyUI. `filename` — имя клипа при сохранении (`scene-01`, `scene-02`, …).

Если Ollama нет, тот же JSON можно написать вручную: 4–8 сцен, в каждом промпте повторить внешность персонажей.

## 3. Генерация в ComfyUI

1. Открыть [`workflows/wan22-ti2v-5b.json`](../workflows/wan22-ti2v-5b.json).
2. Для сцены вставить `visual_prompt` в **CLIP Text Encode (Positive Prompt)**.
3. Queue Prompt.
4. Забрать файл из `ComfyUI/output/` (префикс `scenes/scene`, webm/webp).
5. Скопировать и назвать единообразно в каталог склейки:

   ```text
   output/scenes/scene-01.webm
   output/scenes/scene-02.webm
   …
   ```

   Подойдут `.mp4`, `.webm`, `.mkv`. Имена должны сортироваться по номеру сцены.

Повторить для каждой сцены. Сид можно менять; для серии лучше фиксировать, если нужна похожая картинка.

Дефолт workflow: 832×480, 81 кадр, 24 fps. Длину кадров (`length`) меняют в `Wan22ImageToVideoLatent`. 81 кадр ≈ 3.4 с при 24 fps.

## 4. Склейка

```bash
bash scripts/stitch.sh output/scenes output/final.mp4
```

Скрипт перекодирует клипы в общий H.264 и склеивает через concat. Нужен `ffmpeg` в PATH.

Первый аргумент — каталог клипов, второй — выходной mp4 (по умолчанию `output/final.mp4`).

## Чеклист

- Ollama отвечает на `localhost:11434`, ComfyUI — на `8188`, сеть для генерации не нужна.
- В CLIP загружен UMT5, в VAE — `wan2.2_vae.safetensors`.
- `start_image` не подключён.
- Клипы лежат как `scene-01`, `scene-02`, без дыр в нумерации, если хотите простой порядок сортировки.
