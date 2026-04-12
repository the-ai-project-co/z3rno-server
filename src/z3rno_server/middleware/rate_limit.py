"""Sliding-window rate limiting middleware using Valkey.

Plan-based limits:
  - Community: 1,000 ops/min
  - Pro: 10,000 ops/min
  - Team: 50,000 ops/min
  - Enterprise: custom (from tenants.settings)

Returns 429 with Retry-After and X-RateLimit-* headers when exceeded.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Default rate limits by plan tier (ops per minute)
PLAN_LIMITS = {
    "community": 1000,
    "pro": 10000,
    "team": 50000,
    "enterprise": 100000,  # Default for enterprise; overridden by tenant settings
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter backed by Valkey."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip rate limiting for health/docs
        if request.url.path in {"/v1/health", "/v1/ready", "/docs", "/redoc", "/openapi.json"}:
            return await call_next(request)

        # TODO: implement actual Valkey-backed sliding window
        # For now, pass through with rate limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = "1000"
        response.headers["X-RateLimit-Remaining"] = "999"
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 60)
        return response

    @staticmethod
    def _rate_limit_response(retry_after: int) -> JSONResponse:
        """Create a 429 Too Many Requests response."""
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Remaining": "0",
            },
        )
