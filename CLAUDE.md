# CLAUDE.md

## Project

z3rno-server is the FastAPI REST API server for Z3rno. It wraps z3rno-core engine functions as HTTP endpoints, handles authentication, rate limiting, and manages Celery workers for background tasks. SDKs and external clients only talk to this server.

## Quick Reference

```bash
uv sync --dev                    # Install dependencies
uv run ruff check .              # Lint
uv run ruff format .             # Format
uv run mypy .                    # Type check
uv run pytest                    # Run tests
make dev-up                      # Start docker compose stack
make dev-down                    # Stop stack
make dev-psql                    # Connect to postgres shell
```

## Architecture

- `src/z3rno_server/main.py` — FastAPI app factory, middleware registration, router includes
- `src/z3rno_server/api/` — Route handlers: memories.py (store/recall/forget), audit.py, sessions.py, health.py, worker.py
- `src/z3rno_server/middleware/` — auth.py, rate_limit.py, logging.py, request_id.py, org_context.py
- `src/z3rno_server/schemas/` — Pydantic request/response models (the API contract for SDKs)
- `src/z3rno_server/workers/` — Celery tasks: lifecycle.py, embeddings.py, healthcheck.py, celery_app.py
- `src/z3rno_server/config.py` — pydantic-settings (DATABASE_URL, VALKEY_URL, etc.)
- `src/z3rno_server/dependencies.py` — FastAPI DI: database session with RLS context

## API Endpoints

- `POST /v1/memories` — Store memory (calls z3rno_core.engine.store)
- `POST /v1/memories/recall` — Recall by query (calls z3rno_core.engine.recall)
- `POST /v1/memories/forget` — Forget/delete (calls z3rno_core.engine.forget)
- `GET /v1/audit` — Query audit log (calls z3rno_core.engine.audit)
- `POST /v1/sessions` — Start session (Valkey-only)
- `POST /v1/sessions/{id}/end` — End session
- `GET /v1/health` — Liveness probe
- `GET /v1/ready` — Readiness probe
- `GET /v1/worker/health` — Celery worker healthcheck (public, no auth)
- `GET /metrics` — Prometheus metrics (public, no auth)

### Phase A — Forge (registered only when `DISTILL_ENABLED=true`)

- `POST /v1/distill` — Enqueue a Forge distillation job (returns `202 Accepted` + `job_id`); RBAC: admin/write
- `GET /v1/distill/{job_id}` — Poll job status (RLS-isolated by org_id); RBAC: admin/write/read

The Celery task `z3rno.forge_distill` runs the pipeline asynchronously. With `DISTILL_ENABLED=false` (default), the routes are not registered and the worker self-rejects messages — OpenAPI is byte-identical to pre-Phase-A.

See `../z3rno-process-docs/improvements/PHASE-A-IMPLEMENTATION.md` for full operator reference.

## Middleware Chain (order matters)

RequestId -> Logging -> Auth -> RateLimit -> Route Handler

## Key Conventions

- Python 3.11+, src/ layout, hatchling build
- z3rno-core is a git dependency (switches to PyPI version when published)
- Ruff + mypy for code quality
- API key auth via Authorization: Bearer or X-API-Key header
- Public paths skip auth: /v1/health, /v1/ready, /docs, /redoc, /openapi.json, /metrics, /v1/worker/health
- Sessions are Valkey-only (no relational sessions table)
- Celery workers use Valkey as broker and result backend
- Conventional commits

## Environment Variables

- `DATABASE_URL` — PostgreSQL connection (asyncpg driver)
- `VALKEY_URL` — Valkey connection (falls back to `REDIS_URL` for backward compat)
- `EMBEDDING_MODEL` — LiteLLM model name (default: text-embedding-3-small)
- `OPENAI_API_KEY` — For embedding generation
- `CORS_ORIGINS` — Comma-separated allowed origins
- `LOG_LEVEL` — Structlog level (default: INFO)

### Phase A — Forge (all default to dormant)

- `DISTILL_ENABLED` — Master switch (default: `false`). When `false`, `/v1/distill` is not registered and the Celery task self-rejects.
- `LLM_PROVIDER` — `openai | anthropic | gemini | bedrock | ollama` (default: `openai`)
- `LLM_MODEL` — LiteLLM-namespaced model id (default: `openai/gpt-4o-mini`)
- `LLM_API_KEY` — Falls back to `OPENAI_API_KEY` when provider is `openai`
- `LLM_TIMEOUT_SECONDS` — Per-call timeout (default: `30.0`)
- `LLM_MAX_RETRIES` — Tenacity retry budget (default: `3`)
- `STRUCTURED_OUTPUT_FRAMEWORK` — Only `instructor` supported in Phase A
- `DISTILL_CHUNK_SIZE` / `DISTILL_CHUNK_OVERLAP` — Token-budget tuning (defaults: `1024` / `128`)
- `DISTILL_MAX_CONCURRENCY` — Per-job LLM fan-out cap (default: `4`)
- `DISTILL_SUMMARY_STYLE` — `concise | bullet | abstractive` (default: `concise`)

## Docker Compose

`docker-compose.dev.yml` runs 4 services: postgres (z3rno-postgres:17), valkey, server, worker. All on the `z3rno` network. Postgres uses platform: linux/amd64 for Apple Silicon compatibility.

`docker-compose.prod.yml` runs the same services plus Traefik for TLS termination. Uses required env vars, resource limits, health checks, password-protected Valkey, and real Celery worker command.
