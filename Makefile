# TaskHub — developer entry points. Run `make` or `make help`.

.DEFAULT_GOAL := help
.PHONY: help install init-db test test-fast test-cov lint lint-fix format format-check type-check check secrets secrets-scan secrets-staged register-projects sync-dry sync audit import import-notes export backup mcp mcp-config web docker-up docker-down prices sync-prices economics eval eval-mock eval-agent eval-seed

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## / — /' | sed 's/^/  make /'

install: ## Install dependencies (uv sync --extra dev)
	uv sync --extra dev

init-db: ## Create tables under $TASKHUB_HOME (default ~/.taskhub)
	uv run taskhub init-db

test: ## Run the test suite
	uv run pytest

test-fast: ## Run the test suite without the live-model (slow) cases
	uv run pytest -m "not slow"

test-cov: ## Run tests with coverage
	uv run pytest --cov=taskhub --cov-report=term-missing

lint: ## Ruff lint
	uv run ruff check .

lint-fix: ## Ruff lint with autofix
	uv run ruff check --fix .

format: ## Ruff format
	uv run ruff format .

format-check: ## Ruff format check (no changes)
	uv run ruff format --check .

type-check: ## mypy (strict)
	uv run mypy

check: lint type-check test ## Run all quality gates

secrets: ## Scan full git history for secrets (gitleaks)
	gitleaks detect --no-banner

secrets-scan: ## Scan the working tree for secrets (gitleaks)
	gitleaks dir . --no-banner

secrets-staged: ## Scan staged files for secrets (gitleaks)
	gitleaks git --staged --no-banner

register-projects: ## Discover projects under ~/Repositories
	uv run taskhub register-projects --root ~/Repositories

sync-dry: ## Preview the registry sync (moves, archives, registrations)
	uv run taskhub sync-projects --root ~/Repositories --dry-run

sync: ## Align the registry with the folders on disk
	uv run taskhub sync-projects --root ~/Repositories

audit: ## Registry health: stale roots, duplicates, uncovered directories
	uv run taskhub audit-projects --root ~/Repositories --format markdown

import: ## Import tasks for all projects
	uv run taskhub import-all

import-notes: ## Import ledgers/notes (+ ~/.cline/memory)
	uv run taskhub import-notes

export: ## Export a markdown digest (PROJECT=<slug>)
	uv run taskhub export $(if $(PROJECT),--project $(PROJECT),) --format markdown

backup: ## Online SQLite backup with retention
	uv run taskhub backup --keep 7

mcp: ## Run the MCP server (stdio)
	uv run taskhub-mcp

mcp-config: ## Regenerate .mcp.json for this checkout (MCP clients)
	uv run taskhub mcp-config

web: ## Run FastAPI REST + dashboard on :8000
	uv run taskhub-web

docker-up: ## Build and start web + MCP containers
	docker compose up -d --build

docker-down: ## Stop containers
	docker compose down


# ---------- Agent economics (M11) ----------
prices: ## Show the known model prices
	uv run taskhub prices list

sync-prices: ## Apply the price list to the database
	uv run taskhub prices sync --no-dry-run

economics: ## Show what the agents cost (PROJECT=<slug>)
	uv run taskhub economics $(if $(PROJECT),--project $(PROJECT),)

# ---------- Eval (MCP tool contract) ----------
# Layer 1 is the pytest contract and always runs with the test suite.
# Layer 2a replays the cases deterministically (no model, no server).
# Layer 2b calls the real MCP server through promptfoo.
eval-mock: ## Replay the MCP contract cases deterministically (CI-safe)
	@uv run python scripts/build_promptfoo_cases.py
	@./node_modules/.bin/promptfoo eval -c eval/promptfooconfig.mock.yaml --no-cache --no-progress-bar 2>&1 | tail -12

eval: ## Run the MCP contract cases against the real MCP server
	@uv run python scripts/build_promptfoo_cases.py
	@mkdir -p docs/evaluation
	@./node_modules/.bin/promptfoo eval -c eval/promptfooconfig.yaml --no-cache --no-progress-bar -o docs/evaluation/eval-mcp.json 2>&1 | tail -12
	@uv run python scripts/summarize_eval.py docs/evaluation/eval-mcp.json

# Layer 3 drives the real MCP tools with a local model. It needs Ollama,
# so it is not part of CI — the pytest contract marks it `slow`.
EVAL_MODEL ?= qwen2.5:7b-instruct-q4_K_M
eval-seed: ## Seed the eval database with the demo project
	@uv run python scripts/eval_seed.py

eval-agent: ## Run the agentic scenarios against the real MCP tools (needs Ollama)
	@uv run python scripts/eval_seed.py
	@uv run python scripts/agent_eval.py --model $(EVAL_MODEL)
