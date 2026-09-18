# videomake local stack: Ollama + ComfyUI + UI
#   make          — поднять всё
#   make down     — остановить то, что поднял make
#
# ComfyUI: COMFYUI_DIR=/path/to/ComfyUI
# Ollama model: OLLAMA_MODEL=dolphin-llama3

SHELL := /bin/bash
STACK := ./scripts/stack.sh

.DEFAULT_GOAL := up

.PHONY: up down stop run status logs check help ui comfy ollama

help:
	@echo "make up       поднять Ollama, ComfyUI и UI"
	@echo "make down     остановить процессы из .run/"
	@echo "make run      up + логи (Ctrl+C = down)"
	@echo "make status   что слушает порты"
	@echo "make logs     tail логов"
	@echo "make check    python3 / ffmpeg / ollama / ComfyUI"
	@echo "make ui       только интерфейс"
	@echo
	@echo "COMFYUI_DIR   путь к клону ComfyUI (локальный старт)"
	@echo "COMFYUI_URL   URL ComfyUI (по умолчанию из .env / localhost)"
	@echo "OLLAMA_HOST   URL Ollama"
	@echo "OLLAMA_MODEL  модель для нарезки сцен"
	@echo "VIDEOMAKE_REMOTE=1  не стартовать локальные Ollama/ComfyUI"

up:
	@$(STACK) up

down stop:
	@$(STACK) down

run:
	@$(STACK) run

status:
	@$(STACK) status

logs:
	@$(STACK) logs

check:
	@$(STACK) check

ui:
	python3 ui/server.py

ollama:
	ollama serve

comfy:
	@dir="$${COMFYUI_DIR:-}"; \
	if [[ -z "$$dir" ]]; then \
	  for d in "$(CURDIR)/ComfyUI" "$(CURDIR)/../ComfyUI" "$$HOME/ComfyUI"; do \
	    if [[ -f "$$d/main.py" ]]; then dir="$$d"; break; fi; \
	  done; \
	fi; \
	if [[ -z "$$dir" || ! -f "$$dir/main.py" ]]; then \
	  echo "Задайте COMFYUI_DIR"; exit 1; \
	fi; \
	cd "$$dir" && python3 main.py --listen 127.0.0.1 --port 8188
