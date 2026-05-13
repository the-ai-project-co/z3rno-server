# z3rno-server benchmarks

Versioned, reusable benchmark harness for `z3rno-server`. The defaults
baked in here are the lessons learned from the v0.21 → v0.22 bench
arc — most importantly, **rate limiting is OFF by default for benches**
because the production-default `PLAN_LIMITS["community"]=1000 req/min/op`
turns a single-process micro-bench into a measurement of the rate limiter,
not the engine. The full evidence trail for that lesson is in the v0.22.3
RCA writeup under `z3rno-local/benchmarks/V0-22-3-TAIL-OUTLIER-RCA-2026-05-13/`.

## Quick start

From the repo root:

```bash
./benchmarks/run.sh                  # standard load ramp, results in benchmarks/last-run/
./benchmarks/run.sh /tmp/my-run      # results to a specific directory
SEED_N=500 ./benchmarks/run.sh       # more seed memories
```

The wrapper:

1. Brings up the dev compose stack with `compose.override.yml` layered
   on top (disables rate limiting, enables batched recall_count).
2. Pre-flight checks that `/v1/limits` reports `rate_limit_enabled=false`.
3. Seeds N memories.
4. Runs the load ramp through the standard concurrency levels.
5. Writes `load_ramp.json` to the results directory.

## Files

| File | Purpose |
|---|---|
| `compose.override.yml` | Bench-flavored env vars (rate limit off, batched bumps on). Layer on top of `docker-compose.dev.yml` via `docker compose -f docker-compose.dev.yml -f benchmarks/compose.override.yml ...`. |
| `seed.py` | Seed N memories so recall has something to retrieve. |
| `load_ramp.py` | The canonical concurrency ramp. Refuses to run if it detects rate limiting is on (override with `Z3RNO_BENCH_ALLOW_RATE_LIMIT=1`). Counts 429 retries separately so they don't disappear silently into latency. |
| `recall_load.py` | Long-hold load at fixed concurrency. Per-request JSONL output for correlating slow requests against `pg_sampler.sh` traces. |
| `pg_sampler.sh` | 100 ms-cadence sampler of `pg_stat_activity` + `pg_locks` + `pg_stat_progress_vacuum`. Run in parallel with `recall_load.py` when investigating a latency tail. |
| `run.sh` | One-shot wrapper that ties it all together. |

## Env vars

All scripts respect a small set of overrides:

| Env var | Default | Notes |
|---|---|---|
| `Z3RNO_BENCH_BASE` | `http://localhost:8000` | Server URL. |
| `Z3RNO_BENCH_KEY` | `z3rno_sk_user_test` | API key for seed + recall. |
| `Z3RNO_BENCH_AGENT` | `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb` | Agent UUID used by seed + recall. |
| `Z3RNO_BENCH_HOLD_S` | `10` | Hold seconds per concurrency level (`load_ramp.py`). |
| `Z3RNO_BENCH_LEVELS` | `1,2,5,10,15,20,25,30` | Comma-separated concurrency levels (`load_ramp.py`). |
| `Z3RNO_BENCH_ALLOW_RATE_LIMIT` | unset | Set to `1` to bypass the pre-flight rate-limit-off check. |
| `STACK_DIR` | repo root | Where `docker-compose.dev.yml` lives. |
| `PY` | `python3` | Python interpreter the runner invokes. |
| `SEED_N` | `200` | Memories to seed before the ramp. |

## Investigating a latency tail

The combination that found the V0-22-3 rate-limiter root cause in one
pass:

```bash
# Terminal A — start the load
./benchmarks/recall_load.py 10 60 recall.jsonl

# Terminal B (in parallel) — sample PG state at 100 ms
./benchmarks/pg_sampler.sh 90 .
```

Afterwards: any line in `recall.jsonl` with `elapsed_ms >= 5000` is a
slow request. Cross-reference its `started_wall` / `ended_wall` against
the same-timestamp rows in `pg_activity.jsonl` / `pg_locks.jsonl` to
see what PG was doing during the stall. If PG is idle on
`Client/ClientRead` (as it was in V0-22-3), the hang is application-side
— check the server logs in the same window. If PG is waiting on a lock,
`pg_locks.jsonl` shows who holds what.

## Result archival

The `last-run/` directory is `.gitignore`d. Promote interesting runs
to a dated subdirectory under `z3rno-local/benchmarks/` (which is the
local-only writeup home — not versioned in any of these repos, by
design).

## Lineage

| Bench writeup | Note |
|---|---|
| `V0-21-FULL-BENCH-2026-05-12` | First v0.21 perf pass. Tail outlier surfaced; mis-attributed to audit-drain beat. |
| `V0-21-2-FULL-BENCH-2026-05-12` | Same shape post-Bug-I. |
| `V0-22-0-RECALL-COUNT-BATCH-2026-05-13` | Real batched-bump win (-19 to -25% p99 at high concurrency). Hidden under rate-limit noise but the comparison was apples-to-apples so it survived. |
| `V0-22-2-AUDIT-LISTENER-2026-05-13` | Falsified the audit-drain hypothesis. |
| `V0-22-3-TAIL-OUTLIER-RCA-2026-05-13` | RCA: the tail outlier was always the rate limiter. **The reason this harness exists.** |

All five writeups live under `z3rno-local/benchmarks/`.
