# Генерация на GPU-хосте в LAN (192.168.0.188)

UI и склейка остаются на машине разработчика; **Ollama** и **ComfyUI + Wan 2.2** крутятся на Windows 11 в локальной сети.

```text
Клиент (~192.168.0.141)          GPU-хост (192.168.0.188)
─────────────────────────        ─────────────────────────
ui/server.py  ──HTTP──►          Ollama  :11434
браузер /api/*                   ComfyUI :8188 + веса Wan
ffmpeg / stitch.sh
output/scenes/, final.mp4
```

Детали Ollama на этом хосте: репозиторий `ollama-local` → `docs/CONNECT.md`.

## Переменные окружения (клиент)

В корне репозитория: `.env` (gitignored) и образец [`.env.example`](../.env.example).
`ui/server.py` и `scripts/stack.sh` подхватывают `.env` автоматически.

```env
OLLAMA_HOST=http://192.168.0.188:11434
COMFYUI_URL=http://192.168.0.188:8188
OLLAMA_EXPAND_MODEL=qwen3.8:27b
OLLAMA_SPLIT_MODEL=dolphin-llama3

VIDEOMAKE_HOST=127.0.0.1
VIDEOMAKE_PORT=8765

# Не поднимать Ollama/ComfyUI на клиенте
VIDEOMAKE_REMOTE=1
```

Запуск только UI (после `.env` переменные можно не задавать вручную):

```powershell
python ui/server.py
```

Открыть [http://127.0.0.1:8765](http://127.0.0.1:8765).

CLI нарезки сцен:

```bash
python scripts/split_scenes.py --host http://192.168.0.188:11434 \
  --expand-model qwen3.8:27b --split-model dolphin-llama3 \
  -i script.txt -o scenes.json
```

На `.188` нужны обе модели: `qwen3.8:27b` (перевод/детали) и `dolphin-llama3` (нарезка). В `.env`:

```env
OLLAMA_EXPAND_MODEL=qwen3.8:27b
OLLAMA_SPLIT_MODEL=dolphin-llama3
```

## Что уже есть на .188

| Сервис | Статус |
| --- | --- |
| Ollama 0.33.2 | API на LAN: `http://192.168.0.188:11434` |
| Модели | `qwen3.8:27b` (expand), `dolphin-llama3` (split) |
| LAN-настройка Ollama | `ollama-local`: `scripts\setup-ollama-lan.ps1` |
| RDP | `:3389` |

Проверка с клиента:

```powershell
curl.exe http://192.168.0.188:11434/api/tags
```

Если ping/API не ходят, а хост жив: часто **AmneziaVPN** перехватывает `192.168.0.0/24`. Обход — `ollama-local\scripts\fix-client-lan-route.ps1` (от администратора) или split-tunnel / отключить VPN.

---

## Установка ПО на .188 (Windows 11)

### A. NVIDIA

Актуальный драйвер NVIDIA. Для дефолтного **Wan2.2-TI2V-5B**: ~**8 GB+ VRAM**, желательно **32 GB RAM**, SSD под веса (десятки гигабайт). См. [003-setup-comfyui.md](003-setup-comfyui.md).

### B. ComfyUI (ещё нет — поставить)

Портативка: [comfy.org/download](https://www.comfy.org/download). Или clone:

```powershell
git clone https://github.com/Comfy-Org/ComfyUI.git C:\ComfyUI
cd C:\ComfyUI
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Старт **с доступом из LAN** (не `127.0.0.1`):

```powershell
python main.py --listen 0.0.0.0 --port 8188
```

Firewall (PowerShell от администратора):

```powershell
New-NetFirewallRule -DisplayName "ComfyUI API LAN" -Direction Inbound `
  -Protocol TCP -LocalPort 8188 -Action Allow -Profile Private
```

Проверка с клиента:

```powershell
curl.exe http://192.168.0.188:8188/system_stats
```

### C. Веса Wan 2.2 TI2V-5B

Скачать из [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files) и разложить:

```text
ComfyUI\models\
  diffusion_models\wan2.2_ti2v_5B_fp16.safetensors
  text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors
  vae\wan2.2_vae.safetensors
```

Имена файлов должны совпадать с `build_wan_prompt()` в `ui/server.py`. Для 5B нужен именно **`wan2.2_vae.safetensors`**, не VAE от 2.1.

Проверка: в ComfyUI открыть `workflows/wan22-ti2v-5b.json` из этого репозитория → Queue Prompt.

Подробности и оффлайн-режим — [003-setup-comfyui.md](003-setup-comfyui.md).

### D. Модели Ollama (expand + split)

```powershell
ollama pull qwen3.8:27b
ollama pull dolphin-llama3
```

```env
OLLAMA_EXPAND_MODEL=qwen3.8:27b
OLLAMA_SPLIT_MODEL=dolphin-llama3
```
### E. На .188 не нужно

| ПО | Где ставить |
| --- | --- |
| `ffmpeg` | На **клиенте** (склейка `scripts/stitch.sh`) |
| UI videomake (`ui/server.py`) | На **клиенте** |
| Клон neuro-visual | На клиенте; на хосте достаточно ComfyUI + веса (workflow — для ручных тестов) |

### F. Автозапуск (удобно)

- Ollama — служба / автозапуск трея
- ComfyUI — автозагрузка или Task Scheduler: `python main.py --listen 0.0.0.0 --port 8188`
- После скачивания весов можно работать оффлайн (см. 003)

---

## Чеклист готовности

На `.188`:

1. `GET http://192.168.0.188:11434/api/tags` — ок
2. `GET http://192.168.0.188:8188/system_stats` — ок
3. Веса Wan на диске, тестовый клип из workflow — ок
4. Firewall: TCP **11434** и **8188**, профиль Private
5. Сеть Private; VPN не перехватывает `192.168.0.0/24`

На клиенте: заданы `OLLAMA_HOST` / `COMFYUI_URL`, установлен `ffmpeg`, UI открывается на `:8765`.

## Список правок по коду (ориентир)

Пока стек заточен под localhost. Для чистого LAN-режима:

| Область | Суть |
| --- | --- |
| `.env.example` + загрузка `.env` | Зафиксировать хосты и модель |
| `scripts/stack.sh` / `Makefile` | `VIDEOMAKE_REMOTE=1`: не стартовать локальные Ollama/ComfyUI, ждать remote health |
| Дефолт моделей | expand=`qwen3.8:27b`, split=`dolphin-llama3` |
| Документация | README, 002, 003, 004 — сценарий «inference на LAN» |

Менять `app.js` под URL Ollama/ComfyUI не нужно: браузер ходит только в `/api/*` на UI.
