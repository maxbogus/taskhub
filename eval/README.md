# Eval — контракт MCP-тулов TaskHub

Три слоя, все детерминированные. LLM-as-judge не используется: он сделал
бы прогон невоспроизводимым.

| Слой | Что проверяет | Команда | Нужна модель |
|---|---|---|---|
| 1 — pytest | Каждый кейс как реальный вызов `call_tool` | `uv run pytest tests/test_eval_contract.py` | нет |
| 2a — replay | Тот же набор через promptfoo `exec:` | `make eval-mock` | нет |
| 2b — реальный MCP | Тот же набор через promptfoo `mcp` (сервер поднимается promptfoo) | `make eval` | нет |

## Формат кейса

`cases/tool_contract.jsonl` — один JSON на строку (как в донорах
`school21intensive3` и `ukassa_mcp`):

```json
{
  "id": "TC-01-economics-summary",
  "case": "economics_summary",
  "description": "Сводка по проекту",
  "tool": "economics_summary",
  "args": {"project_slug": "demo"},
  "expect": {
    "status": "ok",
    "json_contains": {"project": "demo"},
    "json_path_equals": {"roi": null}
  }
}
```

`expect.status: "error"` проверяет `error_contains` в тексте ошибки.

## Источник истины

`cases/tool_contract.jsonl` — единственный источник. Слой 1 читает его
напрямую; `scripts/build_promptfoo_cases.py` конвертирует его в
`cases/tool_contract.promptfoo.jsonl` (promptfoo требует `vars` в каждом
кейсе и схлопывает массивы внутри `vars`, поэтому контракт едет одной
JSON-строкой).

Сгенерированный файл тоже в git: прогон работает без Python.

## Подготовка

```bash
npm install        # один раз: promptfoo 0.125.1
make eval-mock     # быстрый прогон без сервера
make eval          # реальный MCP-прогон, отчёт в docs/evaluation/
```

`scripts/mcp_server.py` поднимает TaskHub-MCP по stdio. Promptfoo
запускает `.py`-серверы системным `python3`, поэтому скрипт
перезапускает себя интерпретатором `.venv` — в конфиге нет абсолютных
путей.

## Отчёт

`scripts/summarize_eval.py` печатает счётчики и причины провалов и
возвращает ненулевой код, если кейс упал.
