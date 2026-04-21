# z3rno-server

[![PyPI](https://img.shields.io/pypi/v/z3rno-server)](https://pypi.org/project/z3rno-server/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/the-ai-project-co/z3rno-server/actions/workflows/ci.yml/badge.svg)](https://github.com/the-ai-project-co/z3rno-server/actions/workflows/ci.yml)

FastAPI REST API server for Z3rno -- wraps z3rno-core engine functions as HTTP endpoints.

## Quickstart

### Run with Docker Compose

```bash
cp .env.example .env   # configure DATABASE_URL, API keys, etc.
docker compose -f docker-compose.dev.yml up
```

This starts PostgreSQL 17 (with pgvector, Apache AGE), Valkey, the API server on `localhost:8000`, and a Celery worker.

### Store a memory

```bash
curl -X POST http://localhost:8000/v1/memories \
  -H "Authorization: Bearer z3rno_sk_test_localdev" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent-1", "content": "User prefers dark mode", "memory_type": "semantic"}'
```

### Recall memories

```bash
curl -X POST http://localhost:8000/v1/memories/recall \
  -H "Authorization: Bearer z3rno_sk_test_localdev" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent-1", "query": "What does the user prefer?", "top_k": 5}'
```

### Forget a memory

```bash
curl -X POST http://localhost:8000/v1/memories/forget \
  -H "Authorization: Bearer z3rno_sk_test_localdev" \
  -H "Content-Type: application/json" \
  -d '{"memory_id": "<memory-uuid>"}'
```

For a detailed step-by-step setup, see [QUICKSTART.md](QUICKSTART.md).

Full documentation: [astron-bb4261fd.mintlify.app](https://astron-bb4261fd.mintlify.app)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/memories` | Store a new memory |
| POST | `/v1/memories/recall` | Recall memories by semantic query |
| POST | `/v1/memories/forget` | Soft-delete a memory |
| GET | `/v1/audit` | Query the audit log |
| POST | `/v1/sessions` | Start a new session |
| POST | `/v1/sessions/{id}/end` | End a session |
| GET | `/v1/sessions/{id}` | Get session state |
| GET | `/v1/health` | Health check |
| GET | `/v1/ready` | Readiness check |

## Configuration

All configuration is via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string (asyncpg) | required |
| `VALKEY_URL` | Valkey URL (falls back to `REDIS_URL`) | `redis://localhost:6379/0` |
| `Z3RNO_API_KEY` | API key for authentication | required |
| `EMBEDDING_MODEL` | LiteLLM embedding model name | `text-embedding-3-small` |
| `OPENAI_API_KEY` | OpenAI API key (for embeddings) | -- |
| `LOG_LEVEL` | Logging level | `INFO` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) | -- |

## OpenAPI Documentation

When running locally, interactive API docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Development

```bash
uv sync --dev
uv run ruff check .
uv run mypy .
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## License

Apache 2.0 -- see [LICENSE](LICENSE).
