"""API key management endpoints: create, list, revoke."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from uuid import UUID

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


# --- Schemas ---


class CreateApiKeyRequest(BaseModel):
    """POST /v1/api-keys - create a new API key."""

    name: str = Field(..., min_length=1, max_length=255)


class CreateApiKeyResponse(BaseModel):
    """Response for creating a new API key (only time the full key is visible)."""

    id: UUID
    name: str
    key: str
    prefix: str
    created_at: datetime


class ApiKeyListItem(BaseModel):
    """A single API key in the list response (never includes the key itself)."""

    id: UUID
    name: str
    prefix: str
    last_used_at: datetime | None = None
    created_at: datetime


# --- Helpers ---


def _get_org_id(request: Request) -> UUID:
    """Extract org_id from request state (set by auth middleware)."""
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=401, detail="No org context")
    return org_id  # type: ignore[no-any-return]


# --- Endpoints ---


@router.post(
    "",
    response_model=CreateApiKeyResponse,
    status_code=201,
    responses={401: {"model": ErrorResponse}},
    summary="Create a new API key",
)
async def create_api_key(
    body: CreateApiKeyRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin"),
) -> CreateApiKeyResponse:
    """Create a new API key for the authenticated organization.

    The full key is returned only once in this response. It cannot be
    retrieved again — only the prefix is stored.
    """
    org_id = _get_org_id(request)

    # Generate key: prefix + suffix
    prefix = "z3rno_sk_" + secrets.token_hex(8)
    suffix = secrets.token_hex(32)
    full_key = prefix + suffix

    # BCrypt hash the suffix (CPU-bound, run in thread pool)
    suffix_hash = await asyncio.to_thread(
        bcrypt.hashpw,
        suffix.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    )

    conn = await db.connection()
    result = await conn.execute(
        text("""
            INSERT INTO api_keys (org_id, name, prefix, key_hash, created_at, updated_at)
            VALUES (
                CAST(:org_id AS uuid),
                :name,
                :prefix,
                :key_hash,
                now(), now()
            )
            RETURNING id, created_at
        """),
        {
            "org_id": str(org_id),
            "name": body.name,
            "prefix": prefix,
            "key_hash": suffix_hash,
        },
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create API key")

    return CreateApiKeyResponse(
        id=row[0],
        name=body.name,
        key=full_key,
        prefix=prefix,
        created_at=row[1],
    )


@router.get(
    "",
    response_model=list[ApiKeyListItem],
    responses={401: {"model": ErrorResponse}},
    summary="List API keys for the authenticated org",
)
async def list_api_keys(
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin"),
) -> list[ApiKeyListItem]:
    """List all API keys for the authenticated organization.

    Never returns the key itself — only id, name, prefix, and timestamps.
    """
    org_id = _get_org_id(request)

    conn = await db.connection()
    result = await conn.execute(
        text("""
            SELECT id, name, prefix, last_used_at, created_at
            FROM api_keys
            WHERE org_id = CAST(:org_id AS uuid)
              AND revoked_at IS NULL
            ORDER BY created_at DESC
        """),
        {"org_id": str(org_id)},
    )
    rows = result.fetchall()

    return [
        ApiKeyListItem(
            id=row[0],
            name=row[1],
            prefix=row[2],
            last_used_at=row[3],
            created_at=row[4],
        )
        for row in rows
    ]


@router.delete(
    "/{key_id}",
    status_code=204,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin"),
) -> Response:
    """Revoke an API key by setting revoked_at = now().

    Returns 204 No Content on success, 404 if the key doesn't exist
    or doesn't belong to the authenticated org.
    """
    org_id = _get_org_id(request)

    conn = await db.connection()
    result = await conn.execute(
        text("""
            UPDATE api_keys
            SET revoked_at = now(), updated_at = now()
            WHERE id = CAST(:key_id AS uuid)
              AND org_id = CAST(:org_id AS uuid)
              AND revoked_at IS NULL
            RETURNING id
        """),
        {"key_id": str(key_id), "org_id": str(org_id)},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found")

    return Response(status_code=204)
