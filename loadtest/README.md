# z3rno-server load test harness

Locust-based stress test rig for v0.6.0. Three scenarios:

| File | Mix | What it measures |
|---|---|---|
| `locustfile.py` | 40% store, 50% recall, 5% audit, 4% sessions, 1% health | Realistic mixed traffic |
| `locust_read_heavy.py` | 95% recall, 5% health | Read ceiling |
| `locust_health_only.py` | 100% health | Raw framework ceiling (no auth/DB) |

## Quick start

```bash
# from z3rno-server/

# 1. Bring up the tuned dev stack (4 workers, no rate limit, EMBEDDING_PROVIDER=noop)
docker compose -f docker-compose.dev.yml \
               -f loadtest/docker-compose.loadtest.yml \
               up -d --build

# 2. Apply migrations from z3rno-core (entrypoint does this best-effort,
#    but applying directly is more reliable)
DATABASE_URL=postgresql+psycopg://z3rno:z3rno_dev_password@localhost:5433/z3rno \
    uv run --directory ../z3rno-core alembic upgrade head

# 3. Pick a scenario
./loadtest/run.sh sanity      # 100 users, 60s — validates the path
./loadtest/run.sh small       # 500 users, 5min
./loadtest/run.sh medium      # 1000 users, 5min
./loadtest/run.sh all         # all three, sequential
```

Reports land in `loadtest/reports/<scenario>_<users>u_<timestamp>.{csv,html}`.

## Why ports 5433 + 6380?

The override compose remaps Postgres to `5433` and Valkey to `6380` so the
stack doesn't collide with other dev databases on `5432` / `6379`. Only the
*host* port is remapped — services still talk to each other on the standard
ports inside the docker network.

## Notes

- `EMBEDDING_PROVIDER=noop` is required so memory writes/recalls don't hit
  OpenAI under load. The server already hardcodes `NoOpEmbeddingProvider()`
  for the memory paths, but the env var keeps any future changes honest.
- `RATE_LIMIT_ENABLED=false` is set in the override — without it, the `pro`
  plan caps at 10k req/min/org and you'll see 429s instead of real ceiling
  numbers.
- The dev-bypass API key (`z3rno_sk_test_localdev`) attaches a fixed
  `org_id` (`aaaa…`) to every request. **All synthetic traffic goes to one
  tenant**, which is the *worst case* for the audit hash chain (see report).
  For multi-tenant load tests, swap to seeded API keys via `seed_loadtest.py`.

## Findings

See [Stress-Testing-Report-v0.6.0.md](../../z3rno-benchmarking-reports/Stress-Testing-Report-v0.6.0.md)
for the full write-up. Highlights:

- Framework ceiling on a laptop (4 workers, 1k concurrent): **~2,760 RPS**.
- Single-org healthy ceiling: **~15 concurrent users / ~110 RPS**.
- Single-org bottleneck: per-org audit log hash chain serializes every write
  (and every recall). Fix is identified.
- 500 / 1,000 / 10,000 concurrent on a single org: not viable until the
  audit chain is decoupled from the request critical path.
