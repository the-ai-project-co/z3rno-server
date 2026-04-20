"""API key and JWT authentication middleware.

Authentication flow:
  1. Extract token from Authorization: Bearer <token> or X-API-Key header.
  2. If token looks like a JWT (contains two dots), verify as JWT:
     a. Decode using jwt_secret_key with configured algorithm.
     b. Extract org_id, user_id (sub), role from payload.
     c. Validate expiry (exp claim).
     d. Attach org_id, user_id, role to request.state.
  3. Otherwise, verify as API key:
     a. Check dev bypass (Z3RNO_API_KEY env var — local development only).
     b. Check Valkey cache (SHA-256 of raw key → org_id|api_key_id, 60s TTL).
     c. On cache miss: query api_keys table by prefix, BCrypt-verify suffix.
     d. On success: cache in Valkey, attach org_id + api_key_id to request.state.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from uuid import UUID

import bcrypt
import jwt
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from z3rno_server.config import get_settings
from z3rno_server.dependencies import _get_engine

logger = logging.getLogger(__name__)

# Paths that skip authentication
PUBLIC_PATHS = {
    "/v1/health",
    "/v1/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/v1/worker/health",
}

# Module-level Redis client (lazy init)
_redis: aioredis.Redis | None = None  # type: ignore[type-arg]


def _get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Get or create the async Redis client for auth caching."""
    global _redis  # noqa: PLW0603
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


@dataclass(frozen=True)
class AuthResult:
    """Successful authentication result."""

    org_id: UUID
    api_key_id: UUID | None = None
    user_id: str | None = field(default=None)
    role: str | None = field(default=None)


def _is_jwt(token: str) -> bool:
    """Check if a token looks like a JWT (three base64 segments separated by dots)."""
    return token.count(".") == 2  # noqa: PLR2004


def verify_jwt(token: str) -> AuthResult | None:
    """Verify a JWT token and extract auth claims.

    Returns AuthResult on success, None on failure.

    Expected JWT payload:
        {
            "sub": "<user_id>",
            "org_id": "<org_id>",
            "role": "admin|write|read|audit",
            "exp": <unix_timestamp>,
            "iat": <unix_timestamp>
        }
    """
    settings = get_settings()

    if not settings.jwt_secret_key:
        logger.warning("JWT authentication attempted but jwt_secret_key is not configured")
        return None

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "org_id", "role", "exp", "iat"]},
        )
    except jwt.ExpiredSignatureError:
        logger.info("JWT token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.info("Invalid JWT token: %s", e)
        return None

    try:
        org_id = UUID(payload["org_id"])
    except (ValueError, KeyError):
        logger.warning("JWT contains invalid org_id")
        return None

    return AuthResult(
        org_id=org_id,
        api_key_id=None,
        user_id=payload.get("sub"),
        role=payload.get("role"),
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via API key or JWT and attach auth context to request state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth for public endpoints
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Extract token from headers
        token = _extract_api_key(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Missing API key"},
            )

        # Check if this looks like a JWT — if so, verify as JWT
        if _is_jwt(token):
            result = verify_jwt(token)
            if not result:
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "detail": "Invalid or expired JWT token"},
                )
            # Attach JWT auth context to request state
            request.state.api_key = None
            request.state.org_id = result.org_id
            request.state.api_key_id = None
            request.state.user_id = result.user_id
            request.state.role = result.role
            return await call_next(request)

        # Otherwise, verify as API key (dev bypass → cache → database)
        result = await verify_api_key(token)
        if not result:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Invalid API key"},
            )

        # Attach API key auth context to request state
        request.state.api_key = token
        request.state.org_id = result.org_id
        request.state.api_key_id = result.api_key_id

        return await call_next(request)


async def verify_api_key(raw_key: str) -> AuthResult | None:
    """Verify an API key via dev bypass, Valkey cache, or database lookup.

    Returns AuthResult on success, None on failure.
    """
    settings = get_settings()

    # 1. Dev bypass — accept Z3RNO_API_KEY env var without DB/cache lookup
    if settings.z3rno_api_key and raw_key == settings.z3rno_api_key:
        if settings.z3rno_dev_org_id:
            return AuthResult(org_id=UUID(settings.z3rno_dev_org_id))
        # Dev key set but no org_id — look up first tenant from DB
        return await _dev_key_lookup()

    # 2. Check Valkey cache (fast path)
    cached = await _check_cache(raw_key)
    if cached is not None:
        return cached

    # 3. Verify against database (slow path)
    try:
        result = await _verify_against_db(raw_key)
    except Exception:
        logger.warning("Database API key verification failed", exc_info=True)
        return None

    if result:
        await _set_cache(raw_key, result)
        return result

    return None


async def _check_cache(raw_key: str) -> AuthResult | None:
    """Check Valkey for a previously verified API key."""
    try:
        r = _get_redis()
        cache_key = f"z3rno:auth:{hashlib.sha256(raw_key.encode()).hexdigest()}"
        cached = await r.get(cache_key)
        if cached:
            parts = str(cached).split("|")
            if len(parts) == 2:  # noqa: PLR2004
                return AuthResult(
                    org_id=UUID(parts[0]),
                    api_key_id=UUID(parts[1]) if parts[1] != "none" else None,
                )
    except Exception:
        logger.warning("Valkey auth cache read failed, falling through to DB", exc_info=True)
    return None


async def _set_cache(raw_key: str, result: AuthResult) -> None:
    """Cache a verified API key in Valkey with TTL."""
    try:
        settings = get_settings()
        r = _get_redis()
        cache_key = f"z3rno:auth:{hashlib.sha256(raw_key.encode()).hexdigest()}"
        value = f"{result.org_id}|{result.api_key_id or 'none'}"
        await r.setex(cache_key, settings.api_key_cache_ttl, value)
    except Exception:
        logger.warning("Valkey auth cache write failed", exc_info=True)


async def _verify_against_db(raw_key: str) -> AuthResult | None:
    """Verify an API key against the api_keys table.

    Finds rows whose stored prefix matches the start of the raw key,
    then BCrypt-verifies the remaining suffix against the stored hash.
    """
    engine = _get_engine()

    async with AsyncSession(engine) as session:
        # Find active, non-revoked, non-expired keys whose prefix
        # matches the beginning of the raw key.
        result = await session.execute(
            text("""
                SELECT id, org_id, prefix, key_hash
                FROM api_keys
                WHERE revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
                  AND starts_with(:raw_key, prefix)
            """),
            {"raw_key": raw_key},
        )
        rows = result.fetchall()

        for row in rows:
            key_id, org_id, prefix, key_hash = row
            suffix = raw_key[len(prefix) :]
            if not suffix:
                continue

            # BCrypt verify is CPU-bound — run in thread pool
            try:
                is_valid = await asyncio.to_thread(
                    bcrypt.checkpw,
                    suffix.encode("utf-8"),
                    bytes(key_hash),
                )
            except (ValueError, TypeError):
                # Invalid BCrypt hash format (e.g., dev seed uses SHA-256)
                continue

            if is_valid:
                # Update last_used_at (best-effort, don't block auth)
                try:
                    await session.execute(
                        text("UPDATE api_keys SET last_used_at = now() WHERE id = :id"),
                        {"id": key_id},
                    )
                    await session.commit()
                except Exception:
                    logger.warning("Failed to update api_key last_used_at", exc_info=True)

                return AuthResult(
                    org_id=UUID(str(org_id)),
                    api_key_id=UUID(str(key_id)),
                )

    return None


async def _dev_key_lookup() -> AuthResult | None:
    """For dev bypass without Z3RNO_DEV_ORG_ID: find the first tenant's org_id."""
    try:
        engine = _get_engine()
        async with AsyncSession(engine) as session:
            result = await session.execute(
                text("SELECT org_id FROM tenants ORDER BY created_at LIMIT 1")
            )
            row = result.fetchone()
            if row:
                return AuthResult(org_id=UUID(str(row[0])))
    except Exception:
        logger.warning("Dev key DB lookup failed", exc_info=True)
    return None


def _extract_api_key(request: Request) -> str | None:
    """Extract API key from Authorization or X-API-Key header."""
    # Try Authorization: Bearer <key>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        key = auth_header[7:].strip()
        return key if key else None

    # Try X-API-Key header
    key = request.headers.get("X-API-Key")
    return key if key else None
