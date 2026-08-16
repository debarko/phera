# Phera

**Phera** (ফেরা — the return) is a headless CRM: configurable funnels, omnichannel support, event-driven workflows, and lifecycle hooks.

- Repo: https://github.com/debarko/phera
- PRD: see `notes/CRM_PRD/PRD.md` in the monorepo workspace

## Quick start

```bash
cd ~/projects/phera
docker compose up -d postgres redis otel-collector
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
phera all
```

API: http://localhost:8000/docs  
Health: http://localhost:8000/health

## Process roles

| Command | Role |
|---|---|
| `phera api` | HTTP/WS only |
| `phera worker` | Queue consumers |
| `phera all` | API + worker (local dev) |

## Observability

Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` (default). Traces, metrics, and slow SQL export via OTLP HTTP to any compatible backend (SigNoz, etc.).

## Testing

Tests split into **unit** (no database) and **integration** (ephemeral in-memory SQLite, created and destroyed per test). No Docker, Postgres, or Redis required for CI or nightly runs.

| Command | What it runs |
|---|---|
| `make test` | Unit tests only (default) |
| `make test-unit` | Same as `make test` |
| `make test-integration` | Flow tests with ephemeral SQLite |
| `make test-nightly` | Full suite — unit + integration |
| `make test-cov` | Full suite with coverage report |

```bash
make test              # fast, no DB
make test-nightly      # exhaustive regression suite
```

Unit tests override FastAPI dependencies so no SQL is executed. Integration tests spin up an in-memory SQLite schema per test, exercise real mutate/audit/outbox/form/workflow/routing flows, then drop the schema.

Environment variables used in CI and locally:

```bash
OTEL_ENABLED=0 REDIS_URL=
```

## Auth

Phera authorizes from `X-Actor-*` headers only — no JWT in the service.

## Channels

WhatsApp (Gallabox) and email (Google Group → inbound hook) setup: see [CHANNELS.md](CHANNELS.md).
