"""Canonical recall-VECTOR load ramp (z3rno-local/benchmarks/harness/).

Ramps concurrent workers through ``LEVELS``, holding each for
``HOLD_S`` seconds, hitting ``POST /v1/memories/recall``. Emits a JSON
report with per-level ops/sec + latency percentiles.

Lessons baked in from the v0.22.3 RCA:

  * Pre-flight sanity check: if rate limiting looks on AND we'll
    blow the bucket, abort early with a clear error. The single-process
    bench is measuring the rate limiter, not the engine.
  * Separate accounting for HTTP 429s. The Python SDK's tenacity retry
    honours ``Retry-After`` and silently rolls the sleep into request
    elapsed time, so a healthy-looking p50 can hide a 60 s outlier
    that's actually rate-limit retry. We count 429s explicitly and
    report them as `retry_429_count` in the JSON output.

CLI: python load_ramp.py [out.json]
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import httpx
from z3rno import AsyncZ3rnoClient

BASE = os.environ.get("Z3RNO_BENCH_BASE", "http://localhost:8000")
API_KEY = os.environ.get("Z3RNO_BENCH_KEY", "z3rno_sk_user_test")
AGENT = os.environ.get(
    "Z3RNO_BENCH_AGENT", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)
HOLD_S = int(os.environ.get("Z3RNO_BENCH_HOLD_S", "10"))
LEVELS = [int(x) for x in os.environ.get(
    "Z3RNO_BENCH_LEVELS", "1,2,5,10,15,20,25,30"
).split(",")]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(len(values) * p / 100)))
    return values[k]


def _check_rate_limit_off() -> None:
    """Pre-flight: refuse to run if the server reports rate limiting on.

    The bench is meant to measure the engine — measuring the rate limiter
    is what tripped the V0-22-3 RCA. Override with
    ``Z3RNO_BENCH_ALLOW_RATE_LIMIT=1`` if you really want to bench against
    a rate-limited server (e.g. to measure retry-after behaviour).
    """
    try:
        req = Request(
            f"{BASE}/v1/limits",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        with urlopen(req, timeout=2) as r:
            body = json.loads(r.read())
    except Exception:
        return  # endpoint missing on older servers — warn only
    if not body.get("rate_limit_enabled"):
        return
    per_min = body.get("rate_limit_per_minute", "?")
    print(
        f"FATAL: server reports rate_limit_enabled=true "
        f"(rate_limit_per_minute={per_min}). This bench will hit the "
        "bucket within seconds at c>=5 and the SDK will silently absorb "
        "the Retry-After: 60 sleep into request elapsed time. Set "
        "RATE_LIMIT_ENABLED=false on the server, or layer the harness "
        "compose override (benchmarks/harness/compose.override.yml). "
        "Override with Z3RNO_BENCH_ALLOW_RATE_LIMIT=1 if you really "
        "want to measure the rate limiter.",
        file=sys.stderr,
    )
    if not os.environ.get("Z3RNO_BENCH_ALLOW_RATE_LIMIT"):
        sys.exit(2)


async def _worker(client: AsyncZ3rnoClient, end: float) -> tuple[list[float], int]:
    samples: list[float] = []
    retries_429 = 0
    while time.perf_counter() < end:
        t0 = time.perf_counter()
        try:
            await client.recall(
                agent_id=AGENT, query="dashboard performance",
                top_k=5, strategy="VECTOR",
            )
            samples.append((time.perf_counter() - t0) * 1000.0)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                retries_429 += 1
            samples.append(-1.0)
        except Exception:  # noqa: BLE001
            samples.append(-1.0)
    return samples, retries_429


async def hold(concurrency: int) -> dict:
    end = time.perf_counter() + HOLD_S
    async with AsyncZ3rnoClient(base_url=BASE, api_key=API_KEY) as client:
        tasks = [_worker(client, end) for _ in range(concurrency)]
        results = await asyncio.gather(*tasks)
    flat: list[float] = [s for ts, _ in results for s in ts]
    retries_429 = sum(r for _, r in results)
    ok = [s for s in flat if s >= 0]
    fail = sum(1 for s in flat if s < 0)
    return {
        "concurrency": concurrency,
        "duration_s": HOLD_S,
        "ops_total": len(flat),
        "ops_ok": len(ok),
        "ops_fail": fail,
        "retry_429_count": retries_429,
        "ops_per_sec": round(len(ok) / HOLD_S, 1),
        "p50_ms": round(percentile(ok, 50), 1),
        "p95_ms": round(percentile(ok, 95), 1),
        "p99_ms": round(percentile(ok, 99), 1),
        "mean_ms": round(statistics.mean(ok), 1) if ok else 0.0,
        "max_ms": round(max(ok), 1) if ok else 0.0,
        "fail_pct": round(100 * fail / len(flat), 2) if flat else 0.0,
    }


async def main() -> None:
    _check_rate_limit_off()
    results = []
    for c in LEVELS:
        print(f"  hold {c}x for {HOLD_S}s...", file=sys.stderr)
        r = await hold(c)
        results.append(r)
        retry_note = (
            f"  retry_429={r['retry_429_count']}" if r["retry_429_count"] else ""
        )
        print(
            f"    -> {r['ops_per_sec']} ops/s  p50={r['p50_ms']} "
            f"p95={r['p95_ms']} p99={r['p99_ms']} max={r['max_ms']} "
            f"fail={r['fail_pct']}%{retry_note}",
            file=sys.stderr,
        )

    out = {
        "ts_utc": time.strftime("%FT%TZ", time.gmtime()),
        "hold_s_per_level": HOLD_S,
        "levels": LEVELS,
        "verb": "recall_VECTOR",
        "base": BASE,
        "results": results,
    }
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("load_ramp.json")
    raw_path.write_text(json.dumps(out, indent=2))

    # Table
    print()
    print("| Concurrent | ops/sec | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | 429 | fail % |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        print(
            f"| {r['concurrency']} | {r['ops_per_sec']} | {r['p50_ms']} | "
            f"{r['p95_ms']} | {r['p99_ms']} | {r['max_ms']} | "
            f"{r['retry_429_count']} | {r['fail_pct']} |"
        )


if __name__ == "__main__":
    asyncio.run(main())
