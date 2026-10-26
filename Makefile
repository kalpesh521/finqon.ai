.DEFAULT_GOAL := help
BACKEND := backend
PY      := $(BACKEND)/.venv/bin

.PHONY: help install lint format typecheck test test-unit test-integration check \
        setup api eval docker-api web

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## Create backend/.venv and install the API with dev tools
	cd $(BACKEND) && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"

lint: ## Ruff: lint and formatting (API, tests, migrations, scripts)
	cd $(BACKEND) && .venv/bin/ruff check . ../dataset ../eval
	cd $(BACKEND) && .venv/bin/ruff format --check . ../dataset ../eval

format: ## Apply Ruff fixes and formatting
	cd $(BACKEND) && .venv/bin/ruff check --fix . ../dataset ../eval
	cd $(BACKEND) && .venv/bin/ruff format . ../dataset ../eval

typecheck: ## mypy over the finqon package
	cd $(BACKEND) && .venv/bin/mypy

test-unit: ## Unit tests: no database, no network, no model
	cd $(BACKEND) && .venv/bin/pytest tests/unit -q

test-integration: ## Integration tests: needs FINQON_* database URLs and seed users
	cd $(BACKEND) && .venv/bin/pytest tests/integration -q

test: ## Everything; integration tests skip when the database is not configured
	cd $(BACKEND) && .venv/bin/pytest -q

check: lint typecheck test-unit ## What CI runs on every push

setup: ## One-off: roles, migrations, seed data (needs FINQON_ADMIN_DATABASE_URL)
	cd $(BACKEND) && .venv/bin/python -m finqon.db.bootstrap_roles \
	  && .venv/bin/alembic upgrade head \
	  && .venv/bin/python ../dataset/load_ledger.py

api: ## Run the API with reload on http://localhost:8000
	cd $(BACKEND) && .venv/bin/uvicorn finqon.main:app_factory --factory --reload --port 8000

web: ## Run the React UI on http://localhost:5173
	cd frontend && npm run dev

eval: ## Run the evaluation set against the live model (spends model quota)
	cd $(BACKEND) && .venv/bin/python ../eval/run_eval.py

db: ## Start local Postgres 16 (pgvector) and wait until it accepts connections
	docker compose up -d --wait postgres

docker: ## Start Postgres, run setup once, then the API and the UI
	docker compose up -d --wait postgres
	docker compose --profile setup run --rm setup
	docker compose up --build api web

docker-api: ## Build and run the API against the Compose Postgres service
	docker compose up -d --wait postgres
	docker compose up --build api
