"""Session endpoints - Redis-only, no relational table.

Sessions are stored as Redis hashes at key session:{session_id} with
a 24h TTL. Memories link to sessions via metadata.session_id.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from z3rno_server.config import get_settings
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

# Module-level Redis client (lazy init)
_redis: aioredis.Redis | None = None  # type: ignore[type-arg]


def _get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Get or create the async Redis client for sessions."""
    global _redis  # noqa: PLW0603
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


class StartSessionRequest(BaseModel):
    """POST /v1/sessions - start a new session."""

    agent_id: UUID
    session_type: str = "conversation"
    metadata: dict[str, str] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Session state response."""

    session_id: UUID
    agent_id: UUID
    session_type: str
    started_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)


class EndSessionResponse(BaseModel):
    """Response for ending a session."""

    session_id: UUID
    transitioned_count: int


SESSION_TTL = 86400  # 24 hours


@router.post("", response_model=SessionResponse, summary="Start a new session")
async def start_session(
    body: StartSessionRequest,
    _rbac: None = require_role("admin", "write"),
) -> SessionResponse:
    """Start a new session. Stores session state in Redis with 24h TTL."""
    session_id = uuid4()
    now = datetime.now().astimezone()

    try:
        r = _get_redis()
        key = f"session:{session_id}"
        await r.hset(
            key,
            mapping={
                "agent_id": str(body.agent_id),
                "session_type": body.session_type,
                "started_at": now.isoformat(),
                "metadata": json.dumps(body.metadata),
            },
        )
        await r.expire(key, SESSION_TTL)
    except Exception:
        logger.warning("Failed to write session to Redis", exc_info=True)

    return SessionResponse(
        session_id=session_id,
        agent_id=body.agent_id,
        session_type=body.session_type,
        started_at=now,
        metadata=body.metadata,
    )


@router.post(
    "/{session_id}/end",
    response_model=EndSessionResponse,
    summary="End a session",
)
async def end_session(
    session_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> EndSessionResponse:
    """End a session. Transitions working memories tagged with this session to episodic."""
    org_id = getattr(request.state, "org_id", None)
    transitioned = 0

    if org_id:
        try:
            conn = await db.connection()
            # Find working memories tagged with this session_id
            # and transition them to episodic
            result = await conn.execute(
                text("""
                    UPDATE memories
                    SET memory_type = CAST('episodic' AS memory_type_enum),
                        updated_at = now()
                    WHERE org_id = CAST(:org_id AS uuid)
                      AND memory_type = CAST('working' AS memory_type_enum)
                      AND deleted_at IS NULL
                      AND memory_metadata @> CAST(:session_filter AS jsonb)
                """),
                {
                    "org_id": str(org_id),
                    "session_filter": json.dumps({"session_id": str(session_id)}),
                },
            )
            transitioned = result.rowcount
        except Exception:
            logger.warning("Failed to transition session memories", exc_info=True)

    # Delete the Redis session key
    try:
        r = _get_redis()
        await r.delete(f"session:{session_id}")
    except Exception:
        logger.warning("Failed to delete session from Redis", exc_info=True)

    return EndSessionResponse(session_id=session_id, transitioned_count=transitioned)


@router.get("/{session_id}", response_model=SessionResponse, summary="Get session state")
async def get_session(
    session_id: UUID,
    _rbac: None = require_role("admin", "write"),
) -> SessionResponse:
    """Get the current state of a session from Redis."""
    try:
        r = _get_redis()
        data = await r.hgetall(f"session:{session_id}")
        if data:
            metadata = json.loads(data.get("metadata", "{}"))
            return SessionResponse(
                session_id=session_id,
                agent_id=UUID(data["agent_id"]),
                session_type=data.get("session_type", "conversation"),
                started_at=datetime.fromisoformat(data["started_at"]),
                metadata=metadata,
            )
    except Exception:
        logger.warning("Failed to read session from Redis", exc_info=True)

    raise HTTPException(status_code=404, detail="Session not found")
