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


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies larger than MAX_BODY_SIZE (10 MB).

    Also validates that POST/PUT/PATCH requests to API endpoints
    send ``Content-Type: application/json``.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # --- 1. Request body size check via Content-Length header ---
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
            if not content_type.startswith("application/json"):
                return JSONResponse(
                    status_code=415,
                    content={
                        "error": "unsupported_media_type",
                        "detail": "Content-Type must be application/json",
                    },
                )

        return await call_next(request)
