# z3rno-server

> Self-hostable FastAPI REST server for Z3rno. Imports `z3rno-core` as a library and exposes `/v1/memories/*`, `/v1/audit`, `/v1/sessions/*` endpoints with API-key auth, plan-based rate limiting, and multi-tenant isolation via PostgreSQL RLS.

**License:** Apache 2.0
**Status:** Early development
**Part of:** [Z3rno](https://github.com/the-ai-project-co) — the database for AI agent memory

## What this is

`z3rno-server` is the stateless FastAPI server that fronts the Z3rno memory engine. It handles HTTP request/response, authentication, rate limiting, org-context propagation (for Row-Level Security), Celery task dispatch for async work (embedding generation, decay, summarisation), and observability (OpenTelemetry tracing, Prometheus metrics, structured logging).

All business logic lives here or in `z3rno-core`. The SDKs (`z3rno-sdk-python`, `z3rno-sdk-typescript`) are thin HTTP clients that call this server.

## Running locally

```bash
docker compose -f docker-compose.dev.yml up
# server at http://localhost:8000
# postgres at localhost:5432
# valkey at localhost:6379
```

## What this is not

- Not the database (the schema lives in `z3rno-core`).
- Not an SDK (those are `z3rno-sdk-python`, `z3rno-sdk-typescript`).
- Not the managed cloud (that is the private `z3rno-cloud` repo, which wraps this server).
