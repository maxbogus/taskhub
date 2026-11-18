# Промпт: перенос задач из репозитория в TaskHub

> Скопируйте агенту (Cline/Claude/Cursor) и подставьте `<PROJECT_PATH>` и
> `<PROJECT_SLUG>`. Промпт рассчитан на агента с доступом к shell и, желательно,
> к MCP-серверу `taskhub`.

---

Ты — агент миграции задач. Цель: перенести **все** задачи и заметки/леджеры из
репозитория `<PROJECT_PATH>` в TaskHub (локальный трекер задач/бэклога/заметок)
без дублей и без изменения исходных файлов.

## Сначала прочитай

1. `~/Repositories/taskhub/docs/COLLECTION_MAP.md` — где в папках лежат задачи
   и заметки/леджеры.
2. `~/Repositories/taskhub/src/taskhub/sources.yaml` — какие источники
   собираются автоматически и каким парсером (`auto`, `task_sections`,
   `backlog_table`, `jsonl_ledger`, `markdown_note`, …).
3. `~/Repositories/taskhub/AGENTS.md` — команды и конвенции.

## Инварианты (нарушать нельзя)

1. Сначала всегда `--dry-run`, только потом реальный импорт.
2. Импорт идемпотентен по `(project, external_ref)`: повторный запуск не должен
   создавать дубли — проверь это.
3. Ничего не удалять: только `status=archived`.
4. Не редактировать исходные файлы (леджеры append-only); импорт только читает.
5. Не коммитить без явной просьбы.
6. Не заходить в `api-managment-portals/` (запрещённый проект).
7. Не выдумывать приоритеты/оценки: нет в тексте → `priority=3`,
   `estimate_hours=None`.
8. Сохраняй исходные ID (`T-001`, `RAG-ARCH-1`, `F1.1`) в `external_ref`, чтобы
   переимпорт оставался стабильным.

## Шаги

### 1. Определи проект
- `<PROJECT_PATH>` — корень проекта, `<PROJECT_SLUG>` — slug в TaskHub
  (получить: `taskhub list-projects`).

### 2. Проверь регистрацию
```bash
uv run --directory ~/Repositories/taskhub taskhub list-projects
uv run --directory ~/Repositories/taskhub taskhub register-projects --root ~/Repositories --dry-run
```
Если проекта нет в списке — смотри «Особые случаи» ниже.

### 3. Сухой прогон задач
```bash
uv run --directory ~/Repositories/taskhub taskhub import-all --dry-run --project <PROJECT_SLUG>
```
Зафиксируй `created / updated / skipped / errors` и список источников.

### 4. Проверь, всё ли покрыто
Просмотри реальные источники в `<PROJECT_PATH>` (по `COLLECTION_MAP.md`):
`ai/tasks`, `ai/backlog`, `ai/analysis`, `docs/backlog`, `docs/tasks`,
`backlog/`, `tasks/`, корневой `BACKLOG.md`, `docs/TASKS.md`, README-секции
Roadmap / TODO / Backlog / План / Задачи.
- Если нашёл источник, которого нет в `sources.yaml` (новая папка/формат) —
  добавь спеку в `sources.yaml` (декларативно, без хардкода в Python) и сообщи
  об этом в отчёте.

### 5. Реальный импорт
```bash
uv run --directory ~/Repositories/taskhub taskhub register-projects --root ~/Repositories
uv run --directory ~/Repositories/taskhub taskhub import-all --notes --project <PROJECT_SLUG>
```
`--notes` дополнительно импортирует леджеры/заметки. Если нужна только миграция
задач — убери `--notes`.

### 6. Задачи, которые не распознались
Если задача есть в тексте, но парсер её не взял (нестандартная таблица, список
в прозе, вложенность), сделай одно из двух:
- **предпочтительно** — добавь источник в `sources.yaml` с подходящим парсером
  (`task_sections` / `backlog_table`) и повтори `import-all --dry-run`;
- либо создай задачу вручную через MCP:
  `create_task(project_slug, title, description, priority, estimate_hours,
  external_ref, type="task")` (критерии приёмки клади в `description`).

Не создавай вручную то, что уже импортировано автоматически.

### 7. Леджеры/заметки и связи
`import-notes` (CLI) / `import_notes(project_slugs=[...])` (MCP) подхватывает:
`docs/ledger/*.jsonl`, `docs/HANDOFF.md`, `docs/notes/`, `docs/sessions/`,
`.clinerules/*-ledger*.md`, плюс `~/.cline/memory` (если `include_memory`).
Проверь, что ID в `tickets`/тексте заметок совпадают с `external_ref` задач —
тогда заметка привяжется к задаче (`work_item_id`).

### 8. Валидация (обязательно)
```bash
# повторный сухой прогон: created должно быть 0
uv run --directory ~/Repositories/taskhub taskhub import-all --dry-run --project <PROJECT_SLUG>

# заметки: created=0, updated/skipped допустимы
uv run --directory ~/Repositories/taskhub taskhub import-notes --dry-run
```
Плюс выборочно проверь 3–5 задач (`list_tasks` / `task://<id>`) и выгрузку:
```bash
uv run --directory ~/Repositories/taskhub taskhub export --project <PROJECT_SLUG> --format markdown | head -40
```

### 9. Отчёт
В конце отдай таблицу и списки (шаблон ниже). Без отчёта задача не считается
выполненной.

## Инструменты

**CLI**
```bash
taskhub register-projects [--root PATH] [--max-depth N] [--dry-run]
taskhub import-all [--dry-run] [--notes] [--project SLUG]...
taskhub import-notes [--dry-run] [--no-memory]
taskhub export [--project SLUG] [--kind KIND] [--format markdown|json] [--out FILE]
taskhub list-projects
```
(`taskhub` запускай как `uv run --directory ~/Repositories/taskhub taskhub …`.)

**MCP `taskhub`** (если подключён; префикс `mcp__taskhub__`)
- `register_projects(root, max_depth=2, dry_run=False)`
- `import_all_backlogs(dry_run=False, skip_backup=False, project_slugs=None, include_notes=False)`
- `import_backlog(project_slug, path=None, dry_run=False, skip_backup=False)`
- `import_notes(project_slugs=None, dry_run=False, skip_backup=False, include_memory=True)`
- `create_task(project_slug, title, type="task", description=None, parent_id=None, priority=3, estimate_hours=None, required_skill=None, due_at=None, external_ref=None)`
- `update_task(...)`, `change_status(item_id, status, ...)`, `list_tasks(project_slug, status=None, limit=100)`
- `get_context(project_slug)`, `project_status(project_slug)`
- `add_note(project_slug, content, work_item_id=None, kind="comment", author="mcp-client")`
- `search_notes(project_slug, query, kind=None)`
- `export_notes(project_slug=None, kind=None, ledger_type=None, since=None, output_format="markdown", limit=500)`
- ресурсы: `task://<id>`, `task://<id>/context`, `project://<slug>/backlog`, `project://<slug>/notes`

## Особые случаи

- **Проект не регистрируется** (например, задачи только в README): добавь в
  `sources.yaml` запись для этой папки с `register_project: true` (например,
  `files: [["README.md"]]`, `parser: task_sections`,
  `requires_task_section: true`) и повтори регистрацию. Не правь Python.
- **Нестандартный формат**: выбери `parser: task_sections` (заголовки
  Roadmap/TODO/План + чекбоксы/буллеты) или `backlog_table` (таблица с
  колонками ID/Задача/P/SP/Dep). Если не подходит — ручной `create_task`.
- **Таблицы без ID**: возьми в качестве `external_ref` стабильный ключ
  (`<file>:<row>`), чтобы переимпорт не дублировал.
- **Запрещённый проект**: `api-managment-portals/` не читать и не импортировать.
- **Ошибки парсинга** (`errors` в отчёте): не игнорируй — выпиши файл и причину
  в отчёт, при необходимости добавь/поправь спеку источника.

## Шаблон отчёта

```markdown
## Миграция <PROJECT_SLUG> → TaskHub

| Метрика | До | После |
|---|---:|---:|
| Задач | … | … |
| Заметок | … | … |
| Заметок, связанных с задачами | … | … |

- Задачи: created=…, updated=…, skipped=…, errors=…
- Заметки: created=…, updated=…, skipped=…, linked=…, errors=…
- Обработанные файлы: <список>
- Создано вручную (create_task): <список + почему>
- Новые источники в sources.yaml: <список или «нет»>
- Ошибки/нераспознанное: <список + причина>
- Повторный dry-run: created=0 (подтверждение идемпотентности)
```

## Критерий готовности
1. Все текстовые задачи проекта либо импортированы, либо явно перечислены как
   нераспознанные (с причиной).
2. Повторный импорт не создаёт дублей (`created=0`).
3. Исходные файлы не изменены.
4. Отчёт с цифрами приложен.

