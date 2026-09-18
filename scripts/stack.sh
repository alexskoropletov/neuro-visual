#!/usr/bin/env bash
# Start/stop local videomake stack: Ollama, ComfyUI, UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.run"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:8188}"
UI_URL="http://${VIDEOMAKE_HOST:-127.0.0.1}:${VIDEOMAKE_PORT:-8765}"
OLLAMA_EXPAND_MODEL="${OLLAMA_EXPAND_MODEL:-${OLLAMA_MODEL:-qwen3.8:27b}}"
OLLAMA_SPLIT_MODEL="${OLLAMA_SPLIT_MODEL:-dolphin-llama3}"
OLLAMA_MODEL="${OLLAMA_MODEL:-$OLLAMA_EXPAND_MODEL}"
VIDEOMAKE_REMOTE="${VIDEOMAKE_REMOTE:-0}"

is_loopback_url() {
  case "$1" in
    http://127.0.0.1:*|http://localhost:*|https://127.0.0.1:*|https://localhost:*) return 0 ;;
    *) return 1 ;;
  esac
}

remote_mode() {
  [[ "$VIDEOMAKE_REMOTE" == "1" || "$VIDEOMAKE_REMOTE" == "true" ]] && return 0
  ! is_loopback_url "$OLLAMA_HOST" && return 0
  ! is_loopback_url "$COMFYUI_URL" && return 0
  return 1
}

mkdir -p "$RUN" "$ROOT/output/scenes"

alive() {
  curl -sf -o /dev/null --max-time 2 "$1"
}

wait_http() {
  local url="$1" name="$2" tries="${3:-60}"
  local i
  for i in $(seq 1 "$tries"); do
    if alive "$url"; then
      echo "$name готов ($url)"
      return 0
    fi
    sleep 1
  done
  echo "$name не поднялся: $url" >&2
  echo "лог: $RUN" >&2
  return 1
}

stop_pidfile() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  local pid
  pid="$(cat "$file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

find_comfy() {
  if [[ -n "${COMFYUI_DIR:-}" && -f "$COMFYUI_DIR/main.py" ]]; then
    (cd "$COMFYUI_DIR" && pwd)
    return 0
  fi
  local d
  for d in "$ROOT/ComfyUI" "$ROOT/../ComfyUI" "$HOME/ComfyUI"; do
    if [[ -f "$d/main.py" ]]; then
      (cd "$d" && pwd)
      return 0
    fi
  done
  return 1
}

comfy_python() {
  local dir="$1"
  local p
  for p in "$dir/.venv/bin/python" "$dir/venv/bin/python"; do
    if [[ -x "$p" ]]; then
      echo "$p"
      return 0
    fi
  done
  command -v python3
}

start_ollama() {
  if alive "$OLLAMA_HOST/api/tags"; then
    echo "Ollama уже запущен ($OLLAMA_HOST)"
    return 0
  fi
  if remote_mode || ! is_loopback_url "$OLLAMA_HOST"; then
    echo "Ollama недоступен: $OLLAMA_HOST (remote — запусти на GPU-хосте)" >&2
    return 1
  fi
  if ! command -v ollama >/dev/null; then
    echo "Нет ollama в PATH. https://ollama.com/download" >&2
    return 1
  fi
  echo "Стартую Ollama…"
  nohup ollama serve >"$RUN/ollama.log" 2>&1 &
  echo $! >"$RUN/ollama.pid"
  wait_http "$OLLAMA_HOST/api/tags" Ollama 40
}

ensure_model() {
  local models=("$OLLAMA_EXPAND_MODEL" "$OLLAMA_SPLIT_MODEL")
  local m
  if remote_mode || ! is_loopback_url "$OLLAMA_HOST"; then
    echo "Модели: expand=$OLLAMA_EXPAND_MODEL split=$OLLAMA_SPLIT_MODEL (remote — pull на GPU-хосте)"
    return 0
  fi
  command -v ollama >/dev/null || return 0
  for m in "${models[@]}"; do
    [ -n "$m" ] || continue
    if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Eq "^${m}(:|$)"; then
      echo "Модель $m есть"
      continue
    fi
    echo "Качаю $m…"
    ollama pull "$m"
  done
}

start_comfy() {
  if alive "$COMFYUI_URL/system_stats" || alive "$COMFYUI_URL/"; then
    echo "ComfyUI уже запущен ($COMFYUI_URL)"
    return 0
  fi
  if remote_mode || ! is_loopback_url "$COMFYUI_URL"; then
    echo "ComfyUI недоступен: $COMFYUI_URL (remote — запусти на GPU-хосте)" >&2
    return 1
  fi
  local dir py
  if ! dir="$(find_comfy)"; then
    echo "ComfyUI не найден. Клонируйте его и задайте COMFYUI_DIR=/path/to/ComfyUI" >&2
    echo "см. docs/003-setup-comfyui.md" >&2
    return 1
  fi
  py="$(comfy_python "$dir")"
  echo "Стартую ComfyUI из $dir ($py)…"
  (
    cd "$dir"
    nohup "$py" main.py --listen 127.0.0.1 --port 8188 >"$RUN/comfy.log" 2>&1 &
    echo $! >"$RUN/comfy.pid"
  )
  if ! wait_http "$COMFYUI_URL/system_stats" ComfyUI 90; then
    wait_http "$COMFYUI_URL/" ComfyUI 30
  fi
}

start_ui() {
  if alive "$UI_URL/api/health"; then
    echo "UI уже запущен"
    return 0
  fi
  if ! command -v python3 >/dev/null; then
    echo "Нет python3" >&2
    return 1
  fi
  echo "Стартую UI…"
  nohup python3 "$ROOT/ui/server.py" >"$RUN/ui.log" 2>&1 &
  echo $! >"$RUN/ui.pid"
  wait_http "$UI_URL/api/health" UI 20
}

cmd_check() {
  local ok=0
  command -v python3 >/dev/null && echo "python3: ok" || { echo "python3: нет"; ok=1; }
  command -v ffmpeg >/dev/null && echo "ffmpeg: ok" || { echo "ffmpeg: нет (нужен для склейки)"; ok=1; }
  command -v curl >/dev/null && echo "curl: ok" || { echo "curl: нет"; ok=1; }
  if alive "$OLLAMA_HOST/api/tags"; then
    echo "Ollama: up ($OLLAMA_HOST)"
  else
    echo "Ollama: down ($OLLAMA_HOST)"
    ok=1
  fi
  if alive "$COMFYUI_URL/system_stats" || alive "$COMFYUI_URL/"; then
    echo "ComfyUI: up ($COMFYUI_URL)"
  else
    echo "ComfyUI: down ($COMFYUI_URL)"
    ok=1
  fi
  if remote_mode; then
    echo "режим: remote (локальный ComfyUI/Ollama не стартуем)"
  elif find_comfy >/dev/null; then
    echo "ComfyUI dir: $(find_comfy)"
  else
    echo "ComfyUI dir: не найден (COMFYUI_DIR) — нужен только для локального старта"
  fi
  return "$ok"
}

cmd_up() {
  cmd_check || true
  if ! command -v ffmpeg >/dev/null; then
    echo "Предупреждение: без ffmpeg склейка в UI не заработает." >&2
  fi
  start_ollama
  ensure_model
  start_comfy
  start_ui
  echo
  echo "UI:      $UI_URL"
  echo "ComfyUI: $COMFYUI_URL"
  echo "Ollama:  $OLLAMA_HOST"
  echo "стоп:    make down"
}

cmd_down() {
  stop_pidfile "$RUN/ui.pid"
  stop_pidfile "$RUN/comfy.pid"
  stop_pidfile "$RUN/ollama.pid"
  echo "Остановлены процессы, которые стартовал make (pid в .run/)."
  echo "Уже крутившиеся отдельно Ollama/ComfyUI не трогались."
}

cmd_status() {
  printf "Ollama  %s\n" "$(alive "$OLLAMA_HOST/api/tags" && echo up || echo down)"
  if alive "$COMFYUI_URL/system_stats" || alive "$COMFYUI_URL/"; then
    echo "ComfyUI up"
  else
    echo "ComfyUI down"
  fi
  printf "UI      %s\n" "$(alive "$UI_URL/api/health" && echo up || echo down)"
  [[ -f "$RUN/ollama.pid" ]] && echo "  ollama.pid $(cat "$RUN/ollama.pid")"
  [[ -f "$RUN/comfy.pid" ]] && echo "  comfy.pid  $(cat "$RUN/comfy.pid")"
  [[ -f "$RUN/ui.pid" ]] && echo "  ui.pid     $(cat "$RUN/ui.pid")"
}

cmd_logs() {
  local files=()
  [[ -f "$RUN/ui.log" ]] && files+=("$RUN/ui.log")
  [[ -f "$RUN/comfy.log" ]] && files+=("$RUN/comfy.log")
  [[ -f "$RUN/ollama.log" ]] && files+=("$RUN/ollama.log")
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "Логов пока нет. Сначала: make up"
    return 1
  fi
  tail -n 50 -F "${files[@]}"
}

cmd_run() {
  cmd_up
  trap 'echo; cmd_down' INT TERM
  cmd_logs
}

usage() {
  echo "usage: $0 {up|down|status|logs|run|check}"
}

case "${1:-up}" in
  up) cmd_up ;;
  down|stop) cmd_down ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  run) cmd_run ;;
  check) cmd_check ;;
  *) usage; exit 2 ;;
esac
