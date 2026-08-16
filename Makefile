.PHONY: dev install migrate test test-unit test-integration test-nightly test-cov lint docker-up seed

install:
	pip install -e ".[dev]"

migrate:
	alembic upgrade head

dev:
	phera all

PYTEST ?= .venv/bin/pytest

# Default: unit tests only (no database)
test:
	OTEL_ENABLED=0 REDIS_URL= $(PYTEST) tests/unit -m unit -q

test-unit:
	OTEL_ENABLED=0 REDIS_URL= $(PYTEST) tests/unit -m unit -q

# Ephemeral in-memory SQLite — created and destroyed per session
test-integration:
	OTEL_ENABLED=0 REDIS_URL= $(PYTEST) tests/integration -m integration -q

# Nightly: full suite (unit + integration), no Docker/Postgres required
test-nightly:
	OTEL_ENABLED=0 REDIS_URL= $(PYTEST) tests/unit tests/integration -q

test-cov:
	OTEL_ENABLED=0 REDIS_URL= $(PYTEST) tests/unit tests/integration -q \
		--cov=phera --cov-report=term-missing --cov-report=xml

lint:
	ruff check src tests

docker-up:
	docker compose up -d postgres redis otel-collector

seed:
	python -m phera.db.seed
