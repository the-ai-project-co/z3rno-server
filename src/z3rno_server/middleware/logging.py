"""Structured JSON logging middleware using structlog."""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log every request/response with structured JSON."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        request_id = getattr(request.state, "request_id", "unknown")

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 2)

        await logger.ainfo(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )

        return response
