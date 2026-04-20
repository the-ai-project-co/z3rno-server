"""Structured JSON logging middleware using structlog."""

from __future__ import annotations

import time
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()

# Headers whose values must be redacted in logs
_SENSITIVE_HEADERS = frozenset({"authorization", "x-api-key"})

_REDACTED = "[REDACTED]"


def _redact(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced by ``[REDACTED]``.

    Comparison is case-insensitive so both ``Authorization`` and
    ``authorization`` are caught.
    """
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADERS:
            redacted[key] = _REDACTED
        else:
            redacted[key] = value
    return redacted


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log every request/response with structured JSON.

    Sensitive headers (Authorization, X-API-Key) are redacted so that
    API keys and bearer tokens never appear in log output.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        request_id = getattr(request.state, "request_id", "unknown")

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 2)

        log_kwargs: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "headers": _redact(dict(request.headers)),
        }

        await logger.ainfo("http_request", **log_kwargs)

        return response
