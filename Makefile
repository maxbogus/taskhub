# TaskHub — developer entry points. Run `make` or `make help`.

.DEFAULT_GOAL := help
.PHONY: help install init-db test test-cov lint lint-fix format format-check type-check check secrets secrets-scan secrets-staged register-projects import import-notes export backup mcp mcp-config web docker-up docker-down

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## / — /' | sed 's/^/  make /'

install: ## Install dependencies (uv sync --extra dev)
	uv sync --extra dev

init-db: ## Create tables under $TASKHUB_HOME (default ~/.taskhub)
	uv run taskhub init-db

test: ## Run the test suite
	uv run pytest

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
