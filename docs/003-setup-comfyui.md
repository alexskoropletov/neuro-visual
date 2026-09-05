# Установка ComfyUI и Wan 2.2 TI2V-5B

После этой страницы ComfyUI генерирует клипы локально, без аккаунта и без облачного inference.

Официальная установка: [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI#installing).  
Веса, уже нарезанные под ComfyUI: [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged).  
Исходные модели: [Wan-AI](https://huggingface.co/Wan-AI).

Нужны Python 3.10+, Git, драйвер NVIDIA + CUDA, `ffmpeg` (для склейки, не для ComfyUI).

## ComfyUI

```bash
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt
python main.py
```

Откроется `http://127.0.0.1:8188`. Нужна свежая сборка: ноды Wan 2.2 (`Wan22ImageToVideoLatent` и др.) есть в актуальном master / recent portable. Если при загрузке workflow ноды красные — обновить ComfyUI.

Из корня videomake весь стек (Ollama + ComfyUI + UI): `make` или `make COMFYUI_DIR=/path/to/ComfyUI`.

Портативная сборка Windows: [comfy.org/download](https://www.comfy.org/download).

## Веса v1 (TI2V-5B)

Скачать из [split_files](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files) и разложить так:

```text
ComfyUI/
└── models/
    ├── diffusion_models/
    │   └── wan2.2_ti2v_5B_fp16.safetensors
    ├── text_encoders/
    │   └── umt5_xxl_fp8_e4m3fn_scaled.safetensors
    └── vae/
        └── wan2.2_vae.safetensors
```

Для 5B обязателен **`wan2.2_vae.safetensors`**. VAE от Wan 2.1 (`wan_2.1_vae.safetensors`) даёт порчу картинки на 5B.

5B с native offload рассчитан примерно на **8 GB VRAM**. Старт в нашем workflow: 832×480, 81 кадр. Если OOM — уменьшить `length` или разрешение в ноде `Wan22ImageToVideoLatent`. Для очень тесной памяти есть GGUF (custom node [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) + кванты с Hugging Face); в v1 это не обязательно.

## Workflow

Файл репозитория: [`workflows/wan22-ti2v-5b.json`](../workflows/wan22-ti2v-5b.json) — официальный шаблон [text_to_video_wan22_5B.json](https://github.com/comfyanonymous/ComfyUI_examples/blob/master/wan22/text_to_video_wan22_5B.json) с разрешением и длиной под этот пайплайн.

В ComfyUI: Workflow → Open → этот JSON. Либо Workflow → Browse Templates → Video → «Wan2.2 5B», затем выставить 832×480 и length 81.

Проверить ноды:

1. `UNETLoader` / Load Diffusion Model → `wan2.2_ti2v_5B_fp16.safetensors`
2. `CLIPLoader` → `umt5_xxl_fp8_e4m3fn_scaled.safetensors`, type `wan`
3. `VAELoader` → `wan2.2_vae.safetensors`
4. У `Wan22ImageToVideoLatent` вход `start_image` **пустой** (это text-to-video). Image-to-video — позже, тем же чекпоинтом.
5. Промпт правится в CLIP Text Encode (Positive). Negative можно оставить из шаблона.

Queue Prompt (Ctrl+Enter). Клип пишется в `ComfyUI/output/` (webm/webp с префиксом `scenes/scene`).

Гайд Comfy: [Wan2.2 Video Generation](https://docs.comfy.org/tutorials/video/wan/wan2_2).

## Без фильтров и без облака

- Не добавлять ноды safety checker / NSFW filter. В native Wan-workflow их нет, пока вы сами их не вставите.
- Не ставить ComfyUI API / partner nodes, которые шлют промпт на чужой сервер.
- После скачивания весов — оффлайн.

ComfyUI Manager (`custom_nodes/ComfyUI-Manager/config.ini` или путь, который Manager печатает при старте):

```ini
[default]
network_mode = offline
```

Переменные после того, как все файлы уже на диске:

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

Проверка: отключить сеть и сгенерировать тестовый клип.

## Опционально: 14B T2V

Не входит в дефолт v1. Имеет смысл при 16–24 GB+ VRAM (fp8; полный bf16/fp16 — ещё больше).

Другой VAE: для 14B нужен **`wan_2.1_vae.safetensors`**, не `wan2.2_vae`.

```text
ComfyUI/models/diffusion_models/
    wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors
    wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors
ComfyUI/models/vae/
    wan_2.1_vae.safetensors
```

Шаблон в ComfyUI: «Wan2.2 14B T2V». Тот же text encoder UMT5.

## LoRA

Веса в репозиторий не кладём. Каталоги вроде [CivitAI](https://civitai.com/models) — на свой выбор и по лицензии модели. LoRA: `ComfyUI/models/loras/`.
