# Quickstart: z3rno-server

A detailed getting-started guide for the Z3rno FastAPI REST server.

## Prerequisites

- Docker and Docker Compose v2
- (Optional) Python 3.11+ and [uv](https://docs.astral.sh/uv/) for local development without Docker
- An OpenAI API key (for embedding generation) or another LiteLLM-compatible provider

## Step-by-step Installation

### 1. Clone the repository

```bash
git clone https://github.com/the-ai-project-co/z3rno-server.git
cd z3rno-server
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
OPENAI_API_KEY=sk-...          # Required for embeddings
Z3RNO_API_KEY=z3rno_sk_test_localdev  # Pre-set for local dev
```

### 3. Start the stack

```bash
docker compose -f docker-compose.dev.yml up
```

This starts four services:
- **PostgreSQL 17** (pgvector, Apache AGE, pg_cron) on port 5432
- **Valkey 8** (Redis-compatible) on port 6379
- **z3rno-server** (FastAPI) on port 8000
- **z3rno-worker** (Celery) for background tasks

On first startup, the server automatically:
- Runs all database migrations (creates tables, indexes, RLS policies)
- Seeds a development tenant and agent (org_id: `aaaaaaaa-...`, agent_id: `bbbbbbbb-...`)

Wait until you see `z3rno-server: starting uvicorn...` in the logs.

## Running Locally

Once the stack is running, the API is available at `http://localhost:8000`.

### Interactive API docs

Open in your browser:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Health check

```bash
curl http://localhost:8000/v1/health
# {"status": "ok"}
```

## First Working Example

### Store a memory

```bash
curl -X POST http://localhost:8000/v1/memories \
  -H "Authorization: Bearer z3rno_sk_test_localdev" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "content": "User prefers dark mode",
    "memory_type": "semantic"
  }'
```

Response:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "content": "User prefers dark mode",
  "memory_type": "semantic",
  "created_at": "2026-04-20T12:00:00Z"
}
```

### Recall memories

```bash
curl -X POST http://localhost:8000/v1/memories/recall \
  -H "Authorization: Bearer z3rno_sk_test_localdev" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "query": "What does the user prefer?",
    "top_k": 5
  }'
```

### Forget a memory

```bash
curl -X POST http://localhost:8000/v1/memories/forget \
  -H "Authorization: Bearer z3rno_sk_test_localdev" \
  -H "Content-Type: application/json" \
  -d '{"memory_id": "550e8400-e29b-41d4-a716-446655440000"}'
```

### Query the audit log

```bash
curl http://localhost:8000/v1/audit \
  -H "Authorization: Bearer z3rno_sk_test_localdev"
```

## Stopping the Stack

```bash
docker compose -f docker-compose.dev.yml down
```

To also remove volumes (resets the database):

```bash
docker compose -f docker-compose.dev.yml down -v
```

## Common Issues / Troubleshooting

### 1. "port 5432 already in use"

Another PostgreSQL instance is running on port 5432. Either stop it or change the port in `.env`:

```bash
POSTGRES_HOST_PORT=5433
```

### 2. Slow startup on Apple Silicon

The PostgreSQL image is linux/amd64 and runs under Rosetta emulation. First pull takes longer than usual. Subsequent starts are fast.

### 3. "OPENAI_API_KEY not set" or embedding errors

Ensure your `.env` file contains a valid `OPENAI_API_KEY`. The server uses this for generating vector embeddings via LiteLLM. Alternatively, configure a different `EMBEDDING_MODEL` that does not require OpenAI.

### 4. "connection refused" from server to postgres

The server may have started before PostgreSQL was ready. Restart just the server:

```bash
docker compose -f docker-compose.dev.yml restart server
```

### 5. How to run without Docker (native)

```bash
uv sync --dev
export DATABASE_URL="postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno"
export REDIS_URL="redis://localhost:6379/0"
export OPENAI_API_KEY="sk-..."
uv run uvicorn z3rno_server.main:app --reload --port 8000
```

You will need PostgreSQL and Valkey/Redis running separately.
