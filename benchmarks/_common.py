"""Shared helpers for the in-repo bench harness.

The conventions baked in here apply to every script in this directory:

  * Env-var-driven config so callers can re-target the bench without
    forking the script (set ``Z3RNO_BENCH_BASE`` / ``Z3RNO_BENCH_KEY``
    / ``Z3RNO_BENCH_AGENT``).
  * Rate-limit-off pre-flight via ``check_rate_limit_off()``. The
    v0.22.3 RCA showed that running benches against a rate-limited
    server measures the rate limiter, not the engine; the pre-flight
    refuses to run unless rate limiting is off (or the caller sets
    ``Z3RNO_BENCH_ALLOW_RATE_LIMIT=1``).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from urllib.request import Request, urlopen

BASE = os.environ.get("Z3RNO_BENCH_BASE", "http://localhost:8000")
API_KEY = os.environ.get("Z3RNO_BENCH_KEY", "z3rno_sk_user_test")
AGENT = os.environ.get(
    "Z3RNO_BENCH_AGENT", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(len(values) * p / 100)))
    return values[k]


def summarize(samples: list[float]) -> dict[str, float]:
    """Standard latency-distribution shape: p50/p95/p99 + min/mean/max."""
    if not samples:
        return {k: 0.0 for k in ("p50_ms", "p95_ms", "p99_ms",
                                  "min_ms", "max_ms", "mean_ms")}
    return {
        "p50_ms": round(percentile(samples, 50), 2),
        "p95_ms": round(percentile(samples, 95), 2),
        "p99_ms": round(percentile(samples, 99), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "mean_ms": round(statistics.mean(samples), 2),
    }


def check_rate_limit_off() -> None:
    """Refuse to run when the server reports rate limiting on.

    The bench is meant to measure the engine — measuring the rate
    limiter is what tripped the V0-22-3 RCA. Override with
    ``Z3RNO_BENCH_ALLOW_RATE_LIMIT=1`` if you really want to bench
    against a rate-limited server (e.g. to measure retry-after
    behaviour explicitly, or for the pentest's rate-limit probe).
    """
    try:
        req = Request(
            f"{BASE}/v1/limits",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        with urlopen(req, timeout=2) as r:
            body = json.loads(r.read())
    except Exception:
        return  # endpoint missing on older servers; warn-only
    if not body.get("rate_limit_enabled"):
        return
    per_min = body.get("rate_limit_per_minute", "?")
    print(
        "FATAL: server reports rate_limit_enabled=true "
        f"(rate_limit_per_minute={per_min}). This bench will measure "
        "the rate limiter, not the engine. Set RATE_LIMIT_ENABLED=false "
        "on the server, or layer compose.override.yml. Override with "
        "Z3RNO_BENCH_ALLOW_RATE_LIMIT=1 if you really want to bench "
        "against the rate limiter.",
        file=sys.stderr,
    )
    if not os.environ.get("Z3RNO_BENCH_ALLOW_RATE_LIMIT"):
        sys.exit(2)
