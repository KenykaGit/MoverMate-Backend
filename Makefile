.DEFAULT_GOAL := help
SHELL := /bin/bash

# The app_test database and Redis DB 15 of the local stack (`make infra`).
LOCAL_TEST_DB := postgresql+asyncpg://app:app@localhost:5432/app_test
LOCAL_TEST_REDIS := redis://localhost:6379/15

.PHONY: help
help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-17s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install dependencies from uv.lock and the pre-commit hooks
	uv sync --frozen
	uv run pre-commit install

.PHONY: up
up: ## Start the whole stack (db, redis, migrate, api, worker, scheduler, nginx on :8000)
	docker compose up --build -d --wait

.PHONY: down
down: ## Stop the stack (data is kept)
	docker compose down

.PHONY: infra
infra: ## Start only db and redis (for running the app/tests on the host)
	docker compose up -d --wait db redis

.PHONY: logs
logs: ## Follow api, worker, scheduler and nginx logs
	docker compose logs -f api worker scheduler nginx

.PHONY: db-reset
db-reset: ## DESTRUCTIVE: drop the local database volume (app_test is re-created on start)
	@read -p "Delete the local database volume? [y/N] " answer && [ "$$answer" = "y" ]
	docker compose down -v

.PHONY: run
run: ## Run the API on the host with autoreload (needs `make infra` and `make migrate`)
	uv run uvicorn app.main:create_app --factory --reload

.PHONY: migrate
migrate: ## Apply migrations to DATABASE__URL
	uv run alembic upgrade head

.PHONY: revision
revision: ## Autogenerate a migration from model changes: make revision m="add field"
	@test -n "$(m)" || (echo 'usage: make revision m="message"' && exit 1)
	@# Sequential ids (0002, 0003...): file names in apply order. Two branches that both
	@# add the same number are caught by the single-head check (CI, migrations-check).
	uv run alembic revision --autogenerate -m "$(m)" \
		--rev-id $$(printf "%04d" $$(( $$(ls migrations/versions/[0-9]*.py | wc -l) + 1 )))

.PHONY: lint
lint: ## Ruff (lint + format check), mypy strict, import-linter contracts
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run lint-imports

.PHONY: fmt
fmt: ## Auto-fix lint findings and format
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: test
test: ## All tests with coverage (integration ones start Postgres/Redis in testcontainers)
	uv run pytest --cov --cov-report=term

.PHONY: test-unit
test-unit: ## Fast tests only: domain, use cases on in-memory fakes, architecture (no Docker)
	uv run pytest -m "not integration"

.PHONY: test-local
test-local: infra ## All tests against the local stack instead of testcontainers
	TEST_DATABASE_URL=$(LOCAL_TEST_DB) TEST_REDIS_URL=$(LOCAL_TEST_REDIS) uv run pytest

.PHONY: migrations-check
migrations-check: infra ## As in CI: single head, upgrade, drift check, downgrade base, upgrade
	@test "$$(uv run alembic heads | wc -l)" -eq 1 || (echo "Multiple alembic heads: merge them" && exit 1)
	DATABASE__URL=$(LOCAL_TEST_DB) uv run alembic upgrade head
	DATABASE__URL=$(LOCAL_TEST_DB) uv run alembic check
	DATABASE__URL=$(LOCAL_TEST_DB) uv run alembic downgrade base
	DATABASE__URL=$(LOCAL_TEST_DB) uv run alembic upgrade head

.PHONY: check
check: ## Everything CI runs before the build (sequential even under make -j)
	@# Not prerequisites: under -j they would run in parallel.
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) migrations-check
