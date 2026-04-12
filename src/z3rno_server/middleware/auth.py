"""API key authentication middleware.

Reads the API key from Authorization: Bearer <key> or X-API-Key header,
looks up the org_id via the api_keys table, and attaches it to request.state.

For local development, accepts the Z3RNO_API_KEY env var as a bypass.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Paths that skip authentication
PUBLIC_PATHS = {"/v1/health", "/v1/ready", "/docs", "/redoc", "/openapi.json"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via API key and attach org_id to request state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth for public endpoints
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Extract API key
        api_key = _extract_api_key(request)
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Missing API key"},
            )

        # TODO: Look up org_id from api_keys table (BCrypt verify + Valkey cache)
        # For now, accept any non-empty key and use a placeholder org_id
        request.state.api_key = api_key
        request.state.org_id = None  # Set by org_context middleware after DB lookup

        return await call_next(request)


def _extract_api_key(request: Request) -> str | None:
    """Extract API key from Authorization or X-API-Key header."""
    # Try Authorization: Bearer <key>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    # Try X-API-Key
    return request.headers.get("X-API-Key")
