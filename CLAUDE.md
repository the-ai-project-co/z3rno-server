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
- `src/z3rno_server/workers/backends/` — **Phase F slice 6:** pluggable `JobBackend` (CeleryBackend default + lazy ModalBackend + lazy K8sJobsBackend) selected by `DISTRIBUTED_BACKEND`. Callers use `enqueue_job(task_name, payload, options=)` instead of direct `celery_app.send_task`.
- `src/z3rno_server/workers/modal/z3rno_modal_app.py` — Modal app scaffold; deploy via `modal deploy`.
- `deploy/k8s/job-template.yaml` — reference K8s Job manifest for the `k8s_jobs` backend.
- `src/z3rno_server/config.py` — pydantic-settings (DATABASE_URL, VALKEY_URL, etc.)
- `src/z3rno_server/dependencies.py` — FastAPI DI: database session with RLS context

## API Endpoints

> Canonical surface = seven Z3rno verbs (`store`, `recall`, `forget`, `audit`, `ingest`, `distill`, `refine`). See `../z3rno-docs/concepts/verbs.mdx` for the public-facing reference.

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
- `GET /v1/graph/data` — Memo subgraph (nodes + edges) for the `/graph` viewer; RLS-isolated; always registered.

### Phase A — Forge (registered only when `DISTILL_ENABLED=true`)

- `POST /v1/distill` — Enqueue a Forge distillation job (returns `202 Accepted` + `job_id`); RBAC: admin/write
- `GET /v1/distill/{job_id}` — Poll job status (RLS-isolated by org_id); RBAC: admin/write/read

The Celery task `z3rno.forge_distill` runs the pipeline asynchronously. With `DISTILL_ENABLED=false` (default), the routes are not registered and the worker self-rejects messages — OpenAPI is byte-identical to pre-Phase-A.

See `../z3rno-process-docs/improvements/PHASE-A-IMPLEMENTATION.md` for full operator reference.

### Phase B.1 — Ingestion (registered only when `INGEST_ENABLED=true`)

- `POST /v1/ingest` — JSON body for `text` or `url` ingest; returns `202` + `job_id`; RBAC: admin/write
- `POST /v1/ingest/file` — multipart file upload (PDF / DOCX / CSV / MD / code / text + Phase B.2 image/audio); RBAC: admin/write
- `GET /v1/ingest/{job_id}` — Poll job status; RBAC: admin/write/read
- `POST /v1/datasets` — Create a dataset (`UNIQUE (org_id, name)` → 409 on duplicate); RBAC: admin/write
- `GET /v1/datasets` — Paginated list (limit 1..500); RBAC: admin/write/read
- `GET /v1/datasets/{id}` — Fetch one (RLS-isolated → 404 cross-tenant); RBAC: admin/write/read
- `DELETE /v1/datasets/{id}` — Soft-delete + detach memories (memo rows preserved); RBAC: admin/write

The Celery task `z3rno.ingest_run` bridges to `IngestPipeline.run()`. When `INGEST_AUTO_DISTILL=true` AND `DISTILL_ENABLED=true`, every successful ingest chains into a `forge_distill` run automatically.

`BodyLimitMiddleware` whitelists `multipart/form-data` for `/v1/ingest/file` only; that endpoint enforces its own size cap via `INGEST_MAX_FILE_BYTES`.

See `../z3rno-process-docs/improvements/PHASE-B1-IMPLEMENTATION.md` for full operator reference.

### Phase B.2 — Multimodal + Search + S3 (opt-in)

- `POST /v1/ingest/search` — Tavily-driven discovery; registered only when `INGEST_ENABLED=true` AND `TAVILY_API_KEY` set. Returns 202 with one `job_id` per discovered URL. RBAC: admin/write.
- `POST /v1/ingest/file` accepts `image/*` and `audio/*` MIME types when `MULTIMODAL_ENABLED=true`. Loaders route through `MultimodalProvider` (vision + Whisper).
- `STORAGE_BACKEND=s3` swaps `LocalStorageBackend` for `S3StorageBackend`; same `_make_storage()` factory in the worker.
- `URL_PLAYWRIGHT_ENABLED=true` + `[playwright]` extra activates the JS-rendered URL fallback inside the existing URL loader.

See `../z3rno-process-docs/improvements/PHASE-B2-IMPLEMENTATION.md` for full operator reference.

### Phase D — Refine + Feedback (registered only when `REFINE_ENABLED=true`)

- `POST /v1/feedback` — record a -1/0/+1 signal on a Memo (`memory_id`) or AGE edge (`edge_id`); RBAC: admin/write. Exactly-one-of enforced at Pydantic + DB CHECK.
- `POST /v1/refine` — enqueue a refine run; returns `202` + `job_id`. RBAC: admin only (refine mutates Memos via dedupe).
- `GET /v1/refine/{job_id}` — poll job state (RLS-isolated → 404 cross-tenant); RBAC: admin/write/read.

The Celery task `z3rno.refine_run` runs the pipeline asynchronously. With `REFINE_ENABLED=false` (default), the routes are not registered and the worker self-rejects messages — OpenAPI is byte-identical to pre-Phase-D.

Optional capability flags (each independent of the others):
- `ONTOLOGY_RESOLVER=rdflib` + `ONTOLOGY_FILE_PATH=...` → Forge grounds distilled entities to OWL URIs. Requires `[ontology]` extra in z3rno-core.
- `REFINE_INFER_ENABLED=true` and/or `REFINE_SUMMARIZE_ENABLED=true` → LLM-driven refine stages. Reuses the Phase A `LLM_*` keys.
- `CODEGRAPH_ENABLED=true` → ingest of Python/TypeScript sources also writes function-level call graph. Requires `[codegraph]` extra in z3rno-core. Surfaces via the new `CODE` retrieval strategy.

See `../z3rno-process-docs/improvements/PHASE-D-IMPLEMENTATION.md` for full operator reference.

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

### Phase B.1 — Ingestion (all default to dormant)

- `INGEST_ENABLED` — Master switch (default: `false`). When `false`, `/v1/ingest` and `/v1/datasets` are not registered and the worker self-rejects.
- `STORAGE_BACKEND` — `local` (default) or `s3` (Phase B.2)
- `STORAGE_LOCAL_DIR` — Filesystem root for the `local` backend (default: `/var/lib/z3rno/artifacts`)
- `INGEST_MAX_FILE_BYTES` — Hard cap on uploads + URL responses (default: 50 MB)
- `INGEST_MAX_CSV_ROWS` — Cap on CSV row expansion (default: 10000)
- `INGEST_AUTO_DISTILL` — When `true` *and* `DISTILL_ENABLED=true`, ingest chains into `forge_distill` (default: `true`)
- `INGEST_DEFAULT_CHUNK_SIZE` — Override-on-request chunk size (default: 1024)
- `URL_FETCH_TIMEOUT_SECONDS` — Per-request timeout for URL ingest (default: 15)
- `URL_ALLOWED_SCHEMES` — Comma-separated allowlist (default: `http,https`)

### Phase B.2 — Multimodal + Search + S3 (all default to dormant)

- `MULTIMODAL_ENABLED` — Master switch for image/audio loaders (default: `false`)
- `MULTIMODAL_VISION_MODEL` — LiteLLM vision model (default: `openai/gpt-4o-mini`)
- `MULTIMODAL_AUDIO_MODEL` — LiteLLM audio model (default: `whisper-1`)
- `MULTIMODAL_API_KEY` — Falls back to `OPENAI_API_KEY`
- `MULTIMODAL_MAX_AUDIO_BYTES` (25 MB) / `MULTIMODAL_MAX_IMAGE_BYTES` (20 MB)
- `S3_BUCKET` — Required when `STORAGE_BACKEND=s3`
- `S3_REGION` (default: `us-east-1`) / `S3_ENDPOINT_URL` (for MinIO/R2)
- `S3_PREFIX` (default: `z3rno`) / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (empty → default AWS chain)
- `TAVILY_API_KEY` — When set, registers `POST /v1/ingest/search`
- `TAVILY_SEARCH_DEPTH` (default: `basic`) / `TAVILY_MAX_RESULTS` (default: `5`)
- `URL_PLAYWRIGHT_ENABLED` (default: `false`); requires `pip install 'z3rno-core[playwright]'`
- `URL_PLAYWRIGHT_TIMEOUT_SECONDS` (default: `30`)

### Phase D — Refine + Ontology + Codegraph (all default to dormant)

- `REFINE_ENABLED` — Master switch (default: `false`). When `false`, `/v1/feedback` and `/v1/refine` are not registered and the worker self-rejects.
- `REFINE_SCHEDULE` — Cron expression for beat scheduler (default: `cron:0 */6 * * *`). Plumbed; multi-tenant fan-out is a follow-up.
- `FEEDBACK_WEIGHT_DECAY` — EMA blend factor per refine cycle (default: `0.95`).
- `ONTOLOGY_RESOLVER` — `none` (default) or `rdflib`. Requires `pip install 'z3rno-core[ontology]'` when `rdflib`.
- `ONTOLOGY_FILE_PATH` — Path to OWL/TTL/RDF file (required when `ONTOLOGY_RESOLVER=rdflib`)
- `ONTOLOGY_MATCHING_STRATEGY` — `exact` or `fuzzy` (default: `fuzzy`)
- `ONTOLOGY_FUZZY_THRESHOLD` — Minimum score 0..1 for fuzzy match (default: `0.80`)
- `REFINE_INFER_ENABLED` (default: `false`) — LLM proposes edges for under-connected Memos
- `REFINE_SUMMARIZE_ENABLED` (default: `false`) — LLM writes per-cluster SUMMARY Memos with cluster-hash cache
- `REFINE_INFER_MAX_CANDIDATES` (default: `50`) — Per-cycle LLM call cap
- `CODEGRAPH_ENABLED` (default: `false`) — Run tree-sitter extractor during ingest of code sources; requires `pip install 'z3rno-core[codegraph]'`
- `CODEGRAPH_LANGUAGES` (default: `python,typescript`) — Comma-separated allowlist

## Docker Compose

`docker-compose.dev.yml` runs 4 services: postgres (z3rno-postgres:17), valkey, server, worker. All on the `z3rno` network. Postgres uses platform: linux/amd64 for Apple Silicon compatibility.

`docker-compose.prod.yml` runs the same services plus Traefik for TLS termination. Uses required env vars, resource limits, health checks, password-protected Valkey, and real Celery worker command.

## Published images

- `ghcr.io/the-ai-project-co/z3rno-postgres:17` — Postgres + pgvector + AGE + pg_cron, built from z3rno-core.
- `ghcr.io/the-ai-project-co/z3rno-server:latest` — this server. Built + pushed by `.github/workflows/server-image.yml` on every main commit (also tagged `main-<sha>`); release tags publish `vX.Y.Z`. Downstream consumers (starter-kit compose, evals CI) drop their from-source bootstrap once this image is available.
