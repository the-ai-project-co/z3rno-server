# Load Tests

Locust-based load/performance tests for z3rno-server.

## Prerequisites

Install dev dependencies (includes Locust):

```bash
uv sync --dev
```

Ensure the server is running:

```bash
make dev-up
```

## Running

### Headless (CI-friendly)

```bash
locust -f tests/load/locustfile.py --headless -u 100 -r 10 -t 60s
```

- `-u 100` — 100 concurrent users
- `-r 10` — spawn 10 users per second
- `-t 60s` — run for 60 seconds

### With Web UI

```bash
locust -f tests/load/locustfile.py
```

Open http://localhost:8089 in your browser to configure and monitor the test.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `Z3RNO_API_KEY` | `z3rno_sk_test_localdev` | API key for authentication |
| `Z3RNO_HOST` | `http://localhost:8000` | Target server URL |

## Task Weights

| Task | Weight | Description |
|---|---|---|
| `recall_memories` | 5 | Search memories by query |
| `store_memory` | 3 | Store a new memory |
| `get_memory` | 2 | Retrieve a single memory by ID |
| `forget_memory` | 1 | Soft-delete a memory |

## Target Latencies

- Store: p95 < 50ms
- Recall: p95 < 100ms
- Get: p95 < 30ms
- Forget: p95 < 50ms
