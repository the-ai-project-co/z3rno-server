"""Request body size limit and Content-Type validation middleware."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# 10 MB limit
MAX_BODY_SIZE = 10 * 1024 * 1024

# HTTP methods that require a JSON Content-Type
_METHODS_REQUIRING_BODY = {"POST", "PUT", "PATCH"}

# Paths that skip Content-Type validation (public / non-API)
_CONTENT_TYPE_SKIP_PATHS = {"/v1/health", "/v1/ready", "/docs", "/redoc", "/openapi.json"}

# Paths that legitimately use ``multipart/form-data`` (file uploads).
# These have their own size and content-type enforcement at the route layer
# (e.g. ``INGEST_MAX_FILE_BYTES`` for /v1/ingest/file).
_MULTIPART_PATHS = {"/v1/ingest/file"}


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies larger than MAX_BODY_SIZE (10 MB).

    Also validates that POST/PUT/PATCH requests to API endpoints
    send ``Content-Type: application/json``.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        is_multipart_path = request.url.path in _MULTIPART_PATHS

        # --- 1. Request body size check via Content-Length header ---
        # Skip the JSON-tier 10 MB cap on multipart endpoints; those routes
        # enforce their own per-feature limit (e.g. INGEST_MAX_FILE_BYTES).
        if not is_multipart_path:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_BODY_SIZE:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "error": "payload_too_large",
                                "detail": f"Request body exceeds {MAX_BODY_SIZE} bytes limit",
                            },
                        )
                except ValueError:
                    pass  # Non-integer Content-Length — let downstream handle

        # --- 2. Content-Type validation for mutation methods ---
        if (
            request.method in _METHODS_REQUIRING_BODY
            and request.url.path not in _CONTENT_TYPE_SKIP_PATHS
        ):
            content_type = request.headers.get("content-type", "")
            allowed = content_type.startswith("application/json") or (
                is_multipart_path and content_type.startswith("multipart/form-data")
            )
            if not allowed:
                return JSONResponse(
                    status_code=415,
                    content={
                        "error": "unsupported_media_type",
                        "detail": "Content-Type must be application/json (or multipart/form-data on file endpoints)",
                    },
                )

        return await call_next(request)
