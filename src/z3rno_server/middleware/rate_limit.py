"""Sliding-window rate limiting middleware using Valkey.

Plan-based limits:
  - Community: 1,000 ops/min
  - Pro: 10,000 ops/min
  - Team: 50,000 ops/min
  - Enterprise: custom (from tenants.settings)

Uses a Valkey sorted set per tenant with timestamps as scores
to implement a precise sliding window. Returns 429 with Retry-After
and X-RateLimit-* headers when the limit is exceeded.
"""

from __future__ import annotations

import contextlib
import logging
import time

import redis.asyncio as aioredis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from z3rno_server.config import get_settings

logger = logging.getLogger(__name__)

# Default rate limits by plan tier (ops per minute)
PLAN_LIMITS: dict[str, int] = {
    "community": 1000,
    "pro": 10000,
    "team": 50000,
    "enterprise": 100000,
}

# Paths that skip rate limiting
SKIP_PATHS = {"/v1/health", "/v1/ready", "/docs", "/redoc", "/openapi.json"}

# Sliding window size in seconds
WINDOW_SECONDS = 60

# Module-level Redis client (lazy init)
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    """Get or create the async Redis client for rate limiting."""
    global _redis  # noqa: PLW0603
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    return _redis


def _derive_operation(path: str) -> str:
    """Derive a rate-limit operation name from the request path.

    Maps API paths to logical operation names so that each operation
    gets its own rate-limit counter. For example:

    - ``/v1/memories`` -> ``store``
    - ``/v1/memories/recall`` -> ``recall``
    - ``/v1/memories/forget`` -> ``forget``
    - ``/v1/memories/batch`` -> ``store_batch``
    - ``/v1/audit`` -> ``audit``
    - ``/v1/sessions`` -> ``sessions``
    - Anything else -> ``other``
    """
    p = path.rstrip("/")
    if p.endswith("/memories/recall"):
        return "recall"
    if p.endswith("/memories/forget"):
        return "forget"
    if p.endswith("/memories/batch"):
        return "store_batch"
    if p.endswith("/memories") or ("/memories/" in p and not p.endswith("/history")):
        return "store"
    if "/memories/" in p and p.endswith("/history"):
        return "history"
    if p.endswith("/audit") or "/audit" in p:
        return "audit"
    if "/sessions" in p:
        return "sessions"
    return "other"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter backed by Valkey."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip rate limiting for health/docs
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        # Get tenant identifier from auth middleware
        org_id = getattr(request.state, "org_id", None)
        if not org_id:
            # Not authenticated — auth middleware will reject, just pass through
            return await call_next(request)

        # Determine rate limit for this tenant
        plan_tier = getattr(request.state, "plan_tier", "community")
        limit = PLAN_LIMITS.get(str(plan_tier), PLAN_LIMITS["community"])

        # Derive operation name from path for per-endpoint rate buckets.
        # This ensures high-frequency recall won't count against store quotas.
        operation = _derive_operation(request.url.path)

        # Check and record the request in the sliding window
        try:
            allowed, remaining, reset_at = await _check_rate_limit(
                str(org_id), limit, operation=operation
            )
        except Exception:
            # If Valkey is down, fail open — allow the request but log
            logger.warning("Rate limit check failed, allowing request", exc_info=True)
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = "unknown"
            response.headers["X-RateLimit-Reset"] = str(int(time.time()) + WINDOW_SECONDS)
            return response

        if not allowed:
            retry_after = max(1, reset_at - int(time.time()))
            return _rate_limit_response(limit, retry_after)

        # Request allowed — proceed and add rate limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response


async def _check_rate_limit(
    org_id: str,
    limit: int,
    *,
    operation: str = "global",
) -> tuple[bool, int, int]:
    """Check and record a request in the sliding window.

    Uses a Valkey sorted set with:
      - Key: ``z3rno:ratelimit:{org_id}:{operation}``
      - Members: unique request IDs (timestamp + counter)
      - Scores: Unix timestamp of the request

    The *operation* parameter creates per-endpoint rate buckets so that
    high-frequency recall requests don't count against store quotas.
    Each operation shares the same limit but has its own counter.

    Returns (allowed, remaining, reset_timestamp).
    """
    r = _get_redis()
    now = time.time()
    window_start = now - WINDOW_SECONDS
    key = f"z3rno:ratelimit:{org_id}:{operation}"

    # Atomic pipeline: remove expired entries, count current, add new entry
    async with r.pipeline(transaction=True) as pipe:
        # Remove entries older than the window
        pipe.zremrangebyscore(key, "-inf", window_start)
        # Count entries in the current window
        pipe.zcard(key)
        # Add the new request (score = timestamp, member = timestamp for uniqueness)
        pipe.zadd(key, {f"{now}": now})
        # Set TTL on the key (auto-cleanup)
        pipe.expire(key, WINDOW_SECONDS + 1)
        results = await pipe.execute()

    current_count = int(results[1])  # zcard result (before adding new entry)
    reset_at = int(now) + WINDOW_SECONDS

    if current_count >= limit:
        # Over limit — remove the entry we just added
        with contextlib.suppress(Exception):
            await r.zrem(key, f"{now}")
        remaining = 0
        return (False, remaining, reset_at)

    remaining = max(0, limit - current_count - 1)
    return (True, remaining, reset_at)


def _rate_limit_response(limit: int, retry_after: int) -> JSONResponse:
    """Create a 429 Too Many Requests response."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )
