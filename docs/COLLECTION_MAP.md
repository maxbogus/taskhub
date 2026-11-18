# Collection map — where agents find tasks and notes

> **Purpose.** Single reference for TaskHub collection: which folders have been
> imported, which still need checking, and — for every kind of folder — exactly
> where an agent should look for tasks and for ledgers/notes.
>
> **Rule of thumb for agents**
> 1. Query TaskHub first (`list_tasks`, `project://{slug}/backlog`, `search_notes`).
> 2. Only if something is missing, read the source files listed below.
> 3. Never treat a folder as "no work" until the places in the table are checked.

## What TaskHub scans automatically

Which files are collected is declared in `src/taskhub/sources.yaml` (not in
code). Override the whole file with `TASKHUB_SOURCES_FILE`.

Tasks (`services/backlog_importer.py`):

| Source | Pattern |
|---|---|
| Task folders | `ai/tasks/**`, `ai/backlog/**`, `docs/backlog/**`, `docs/tasks/**`, `backlog/**`, `tasks/**` |
| Analysis / roadmaps | `ai/analysis/**/*.md` (roadmap, RICE, task matrix) |
| Root backlogs | `BACKLOG.md`, `backlog.md`, `docs/TASKS.md`, `TASKS.md` |
| Project README | `README.md` / `readme.md` **only when it has a Roadmap/TODO/Backlog/Plan section** |

Notes and ledgers (`services/note_importer.py`):

| Source | Pattern |
|---|---|
| Ledgers | `docs/ledger/*.jsonl` (decisions, findings, submissions, benchmarks) |
| Handoff | `docs/HANDOFF.md`, `HANDOFF.md` |
| Notes | `docs/notes/*.md` |
| Sessions | `docs/sessions/*.md`, `reports/sessions/*.md` |
| Ledger rules | `.clinerules/*-ledger*.md` (`meta.ledger_type=rule`) |
| Global memory | `~/.cline/memory/*.jsonl` → pseudo-project `memory` |

## ✅ Checked and imported (43 projects)

| Project (slug) | Root | Task sources |
|---|---|---|
| ai-gateway | `ai-gateway/` | `ai/tasks/**` |
| architecturekata | `architectureKata/` | `ai/tasks/**` |
| archive-parser-microservices-2026-06-20 | `archive/parser-microservices-2026-06-20/` | nested |
| audiolog | `audiolog/` | `ai/tasks/**` |
| books | `books/` | `ai/tasks/**` |
| bot-history | `bot_history/` | `ai/tasks/**` |
| candidate-tracker | `candidate-tracker/` | `docs/backlog/**` |
| clients-tatyana-mgimo-school21 | `clients/tatyana-mgimo-school21/` | nested |
| cogniforge | `cogniforge/` | `ai/tasks/**` |
| conf | `conf/` | `backlog/**` |
| conf-productconf2026-latex | `conf/ProductConf2026-LaTeX/` | nested |
| court-report | `court_report/` | `ai/tasks/**`, `backlog/**` |
| dataanalyzis | `dataAnalyzis/` | `ai/tasks/**` |
| debt-notifications | `debt_notifications/` | `docs/backlog/**` |
| digitalmgimo | `digitalMgimo/` | `ai/tasks/**` |
| expeditionds | `expeditionDS/` | `docs/backlog/**` |
| fdc-v5-atom-auto | `fdc-v5-atom-auto/` | `ai/tasks/**` |
| fiass | `fiass/` | `ai/tasks/**`, `tasks/**` |
| finance-academy | `finance_academy/` | `ai/tasks/**` |
| financial-wealth-calculator | `financial-wealth-calculator/` | `ai/tasks/**` |
| findocs | `finDocs/` | `ai/tasks/**`, `docs/tasks/**` |
| gigaschool-hackathon-2026 | `gigaschool_hackathon_2026/` | `ai/tasks/**`, `docs/backlog/**` |
| hh-matcher | `hh-matcher/` | `ai/tasks/**` |
| hr-system | `hr_system/` | `ai/tasks/**` |
| komanda-2 | `komanda-2/` | `docs/backlog/**` |
| law-rag-review | `law_rag_review/` | `docs/backlog/**` |
| lawcopilot | `lawcopilot/` | `backlog/**` |
| mcp-servers | `mcp-servers/` | `ai/tasks/**` |
| mlaw-rag | `mlaw-rag/` | `docs/backlog/**` |
| neuro-slop-detector | `neuro-slop-detector/` | `ai/tasks/**` |
| personal-assistant | `personal-assistant/` | `ai/tasks/**` |
| platform | `platform/` | `docs/backlog/**` |
| product-factory | `product_factory/` | `docs/tasks/**`, `docs/TASKS.md` |
| prolog-jit | `prolog-jit/` | `ai/tasks/**`, `ai/backlog/**` |
| ptcg-playground-agent | `ptcg-playground-agent/` | `docs/backlog/**` |
| puzzle | `puzzle/` | `ai/tasks/**`, `ai/backlog/**` |
| recommendations | `recommendations/` | `ai/tasks/**` |
| school21intensive3 | `school21intensive3/` | `docs/tasks/**` |
| shared-infrastructure | `shared-infrastructure/` | `tasks/BACKLOG.md` |
| study-diplommagistrate | `study/diplomMagistrate/` | nested |
| study-product-programming-project-5 | `study/Product_Programming_Project_5.ID_1577983-1/` | nested |
| telegram-chat-analysis | `telegram-chat-analysis/` | `ai/tasks/**`, `ai/analysis/**` |
| ukassa-mcp | `ukassa_mcp/` | `ai/tasks/**`, `docs/backlog/**` |

## ⚠️ Requires checking (tasks or ledgers not yet imported)

### Backlogs / analysis not yet registered

| Folder | Where to look for tasks |
|---|---|
| `career-platform/` | root `BACKLOG.md` (markdown table with ID / Задача / P / SP / Dep columns), `README.md` |
| `lawcopilot-api/` | `ai/analysis/roadmap.md`, `roadmap-m1.md`, `roadmap-m4-m6.md`, `rice-scoring.md`, `ai/analysis/tasks/` |

### Ledgers / notes not yet imported (register + `import-notes`)

| Folder | Tasks | Notes / ledgers |
|---|---|---|
| `chem_trial/` | `README.md` | `docs/ledger/{decisions,findings,submissions}.jsonl`, `docs/HANDOFF.md`, `docs/notes/` |
| `evehicle_pred/` | `README.md` | `docs/ledger/findings.jsonl`, `docs/HANDOFF.md` |
| `insurance/` | `README.md` | `docs/ledger/{decisions,findings}.jsonl`, `docs/HANDOFF.md`, `docs/notes/`, `.clinerules/14-decisions-ledger.md` |
| `kaggle/` | `README.md` | `docs/ledger/{decisions,findings,submissions}.jsonl`, `docs/HANDOFF.md`, `.clinerules/14-decisions-ledger.md` |
| `rsna_knee/` | `README.md` | `docs/ledger/{decisions,findings,submissions}.jsonl`, `docs/HANDOFF.md`, `.clinerules/14-decisions-ledger.md` |
| `neuro-slop-detector/` | ✅ already imported | `docs/ledger/{benchmarks,decisions,findings}.jsonl`, `docs/HANDOFF.md`, `.clinerules/14-decisions-ledger.md` |
| `lawcopilot/` | ✅ already imported | `HANDOFF.md`, `ai/analysis/**` |
| `product_factory/` | ✅ already imported | `docs/HANDOFF.md`, `docs/sessions/`, `reports/sessions/` |
| `ukassa_mcp/` | ✅ already imported | `docs/HANDOFF.md` |
| `expeditionDS/` | ✅ already imported | `docs/ledger/*.jsonl`, `docs/HANDOFF.md`, `docs/notes/`, `docs/sessions/`, `.clinerules/09-expds-ledger.md` |
| `debt_notifications/` | ✅ already imported | `docs/notes/` |
| `gigaschool_hackathon_2026/` | ✅ already imported | `docs/notes/` |
| `mlaw-rag/` | ✅ already imported | `docs/notes/` |

### README-only — check for Roadmap / TODO sections

`ai_any_full_stack_application_with_vulnarabilities/`, `ai-best-practices/`
(+`INDEX.md`), `blog/`, `cline_report/`, `expeditionDSpart2/`, `fltrVd/`,
`infrastructure/`, `landings/`, `mlaw-rag-public/`, `montecarlo/`,
`mvp_edtech/`, `obs-backgroundremoval/`, `platform_plans/`, `resume/`,
`security_project/`, `tbank_invest_premarket/`, `telemetry/`,
`TextNavigator/`, `ux-pipeline/`, `video_production/`, `vkrMgimo/`.

### Empty / quick check (no source markers found)

`agent-evaluator/`, `hackathon/`, `mock_sobes/`, `posterAgent/`, `state/`,
`syneps/` (check `syneps/syneps-frontend/docs/notes/`), `syneps-backend/`,
`task-form/`, `taskhub/` (the tool itself).

### Containers — nested projects may remain

`archive/*` (only `parser-microservices-2026-06-20` imported), `clients/*`
(only `tatyana-mgimo-school21`), `conf/*`, `study/*` (two imported).

### Root portfolio files (descriptions / indexes / roadmap)

`INDEX.md`, `README.md`, `STATUS.md`, `SUMMARY.md`, `ML_TRAINING_ROADMAP.md`,
`REPORT_FEB-OCT-2026.md`, `RESUME_SKILLS_REPORT.md`, `HARD_SKILLS_ANALYSIS.md`,
`portfolio_data.json`, `about_data.json`, `skills_data.json`,
`education_data.json`, `experience_data.json`.

These describe projects more than they list tasks. Use them as project
descriptions (TaskHub also captures the README first paragraph into
`Project.description`); roadmap items in `ML_TRAINING_ROADMAP.md` can be
imported as tasks if desired.

## Commands

```bash
# register newly found projects (adds career-platform, lawcopilot-api, ...)
uv run taskhub register-projects --root ~/Repositories --dry-run
uv run taskhub register-projects --root ~/Repositories

# import tasks (optionally also notes in the same run)
uv run taskhub import-all --dry-run
uv run taskhub import-all --notes

# import only ledgers / notes (+ ~/.cline/memory)
uv run taskhub import-notes --no-backup
uv run taskhub import-notes --no-memory        # project ledgers only

# export a digest for writing posts
uv run taskhub export --project chem_trial --format markdown --out posts/chem.md
uv run taskhub export --format json --since 2026-10-01
```

## Relationship to the `.clinerules` ledgers

`.clinerules/*-ledger*.md` describe **how** to write `docs/ledger/*.jsonl`.
TaskHub imports both: the rules as `kind=ledger, meta.ledger_type=rule`, and
the ledger entries themselves as `decision` / `insight` / `ledger` notes linked
to tasks by `tickets` ids.

