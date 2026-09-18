# План: проекты, клипы, шаблоны, референсы

## Актуальный воркфлоу

1. **Ollama** — нарезает сценарий на сцены (`visual_prompt`, shot…).
2. **Leonardo** — рисует референс-кадр на каждую сцену (`refs/scene-XX.jpg`). В UI: превью + «Перегенерить».
3. **ComfyUI Wan** — I2V от выбранного Leonardo-референса. Видео показывается **в карточке сцены**.
4. **ffmpeg** — склейка `final.mp4` (блок «Финал»).

Нужен `LEONARDO_API_KEY` в `.env` (тот же ключ, что в Cursor MCP `leonardo-gateway`).

## Структура на диске

```text
output/
  current_project.json          # {"id": "20260917-091530"}
  projects/
    20260917-091530/
      meta.json                 # title, script, created_at, model
      scenes.json               # сцены + wan + shot + refs
      scenes/
        scene-01.webm
        …
      refs/
        scene-01.jpg            # опциональный референс
        …
      final.mp4
  templates/
    noir-closeup.json           # shot-шаблон (camera/lighting/fov/themes)
```

Старые `output/scenes.json`, `output/scenes/`, `output/final.mp4` остаются для совместимости; новый поток пишет только в `projects/<id>/`.

## Фичи

### 1. Версионирование проектов
- Кнопка **Новый проект** → папка `YYYYMMDD-HHMMSS`
- Выпадающий список проектов / переключение
- Split / generate / stitch работают в **текущем** проекте
- В `meta.json` хранится исходный сценарий

### 2. Просмотр и скачивание сцен
- В блоке «Результат»: превью `<video>` + ссылка download на каждый клип
- Финал — отдельно, с download

### 3. «Применить ко всем»
- На карточке сцены: копирует `shot` (ракурс / свет / FOV / темы) на все сцены проекта

### 4. Шаблоны настроек сцены
- Сохранить текущий `shot` как именованный шаблон в `output/templates/`
- Выбрать шаблон → применить ко всем сценам
- CRUD: list / save / delete

### 5. Референс-картинки
- Upload на сцену → `projects/<id>/refs/scene-XX.*`
- Превью в карточке сцены, удаление
- При генерации: если есть ref — загрузка в ComfyUI и `start_image` у `Wan22ImageToVideoLatent` (I2V); иначе чистый T2V

## API (черновик)

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/projects` | список проектов |
| POST | `/api/projects` | создать новый, сделать current |
| POST | `/api/projects/select` | выбрать current |
| GET | `/api/state` | state + current project |
| GET | `/api/media/project/<id>/scene/<file>` | клип |
| GET | `/api/media/project/<id>/final.mp4` | финал |
| GET | `/api/media/project/<id>/ref/<file>` | референс |
| POST | `/api/templates` | сохранить шаблон |
| GET | `/api/templates` | список |
| DELETE | `/api/templates/<name>` | удалить |
| POST | `/api/project/ref` | upload base64 ref |
| DELETE | `/api/project/ref` | удалить ref |

## Порядок внедрения

1. Projects backend + UI selector / new project  
2. Клипы: превью + download в UI (пути через project)  
3. Apply-to-all + templates  
4. Ref upload + ComfyUI start_image  

## Вне скоупа этого этапа

- Переименование проектов, теги, поиск  
- Мульти-референсы на одну сцену  
- Облачный шаринг
