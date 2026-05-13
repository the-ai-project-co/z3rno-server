# z3rno-server benchmarks

Versioned, reusable benchmark + security-probe harness for
`z3rno-server`. The defaults baked in here are the lessons learned
from the v0.21 → v0.22 bench arc — most importantly, **rate limiting
is OFF by default for benches** because the production-default
`PLAN_LIMITS["community"]=1000 req/min/op` turns single-process
micro-benches into measurements of the rate limiter, not the engine.
The full evidence trail is in the v0.22.3 RCA writeup under
`z3rno-local/benchmarks/V0-22-3-TAIL-OUTLIER-RCA-2026-05-13/`.

## Quick start

From the repo root:

```bash
./benchmarks/run_all.sh                # full suite, results in benchmarks/last-run/
./benchmarks/run_all.sh /tmp/my-run    # specific results dir
SEED_N=500 ./benchmarks/run_all.sh     # heavier seed corpus
FORGE=1   ./benchmarks/run_all.sh      # also run forge_burst (needs LLM key + flags)
```

Or run one script at a time (after the stack is up + seeded):

```bash
./benchmarks/run.sh                    # just the load ramp
python3 benchmarks/microbench.py /tmp/out.json
python3 benchmarks/throughput.py /tmp/out.json
python3 benchmarks/pentest.py /tmp/out.json
```

## What's here

| File | Purpose |
|---|---|
| `compose.override.yml` | Bench-flavored env (rate limit off, batched bumps on). Layer on top of `docker-compose.dev.yml`. |
| `_common.py` | Shared helpers: env-var loading, `summarize()` for latency dists, `check_rate_limit_off()` pre-flight. |
| `seed.py` | Seed N memories so recall has something to retrieve. |
| `microbench.py` | Per-verb latency at concurrency=1. p50/p95/p99 across N iterations for store / recall_{VECTOR,LEXICAL,AUTO} / audit / get_memory / store_and_turn / session_open_close / forget. |
| `throughput.py` | Sustained ops/sec at concurrency 10 / 50 / 200 for store + recall_VECTOR. Long holds — measures the ceiling. |
| `load_ramp.py` | Concurrency ramp 1 → 30 for recall_VECTOR. Short holds — reads the saturation curve. Refuses to run if rate limiting is on; counts 429 retries separately so they can't disappear into latency. |
| `recall_load.py` | Long-hold fixed-concurrency load with per-request JSONL output. Use alongside `pg_sampler.sh` for tail-outlier RCA. |
| `forge_burst.py` | Forge ingest+distill pipeline burst. Needs `INGEST_ENABLED=true` + `DISTILL_ENABLED=true` + LLM key. |
| `pentest.py` | 12 security probes (auth, schema validation, RLS isolation, body limits, injection, public paths, rate limit). The rate-limit probe auto-skips when the server has rate limiting off. |
| `pg_sampler.sh` | 100 ms-cadence sampler of `pg_stat_activity` + `pg_locks` + `pg_stat_progress_vacuum`. The combination of `recall_load.py` + `pg_sampler.sh` is what found the V0-22-3 rate-limiter root cause in one pass. |
| `run.sh` | One-shot wrapper for just the load ramp. |
| `run_all.sh` | Full suite wrapper — stand up stack, seed, run every script, save reports. |

## Env vars

Every script reads the same set:

| Env var | Default | Notes |
|---|---|---|
| `Z3RNO_BENCH_BASE` | `http://localhost:8000` | Server URL. |
| `Z3RNO_BENCH_KEY` | `z3rno_sk_user_test` | API key. |
| `Z3RNO_BENCH_AGENT` | `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb` | Agent UUID. |
| `Z3RNO_BENCH_N` | `200` | Iterations for `microbench.py`. |
| `Z3RNO_BENCH_HOLD_S` | `10` | Hold seconds per level for `load_ramp.py`. |
| `Z3RNO_BENCH_LEVELS` | `1,2,5,10,15,20,25,30` | Concurrency levels for `load_ramp.py`. |
| `Z3RNO_BENCH_THROUGHPUT_S` | `20` | Hold seconds per level for `throughput.py`. |
| `Z3RNO_BENCH_THROUGHPUT_LEVELS` | `10,50,200` | Concurrency levels for `throughput.py`. |
| `Z3RNO_BENCH_ALLOW_RATE_LIMIT` | unset | Set to `1` to bypass the pre-flight rate-limit-off check (only meaningful in scripts that include the check; `pentest.py` deliberately skips it). |
| `STACK_DIR` | repo root | Where the compose file lives. |
| `COMPOSE_FILE` | `docker-compose.dev.yml` | Override for the `run.sh`/`run_all.sh` wrappers. |
| `PY` | `python3` | Python interpreter for the wrappers. |
| `SEED_N` | `200` | Memories to seed before the suite. |
| `FORGE` | `0` | Set to `1` to include `forge_burst` in `run_all.sh`. |

## Investigating a latency tail

The combination that found the V0-22-3 rate-limiter root cause:

```bash
# Terminal A — start the load
./benchmarks/recall_load.py 10 60 recall.jsonl

# Terminal B (in parallel) — sample PG state at 100 ms
./benchmarks/pg_sampler.sh 90 .
```

Afterwards: any line in `recall.jsonl` with `elapsed_ms >= 5000` is a
slow request. Cross-reference its `started_wall` / `ended_wall`
against same-timestamp rows in `pg_activity.jsonl` / `pg_locks.jsonl`
to see what PG was doing during the stall. If PG is idle on
`Client/ClientRead` (as it was in V0-22-3), the hang is application-side
— check the server logs in the same window. If PG is waiting on a
lock, `pg_locks.jsonl` shows the holder.

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
| `V0-22-0-RECALL-COUNT-BATCH-2026-05-13` | Real batched-bump win (-19 to -25% p99 at high concurrency). |
| `V0-22-2-AUDIT-LISTENER-2026-05-13` | Falsified the audit-drain hypothesis. |
| `V0-22-3-TAIL-OUTLIER-RCA-2026-05-13` | RCA: the tail outlier was always the rate limiter. **The reason this harness exists.** |

All five writeups live under `z3rno-local/benchmarks/`.
