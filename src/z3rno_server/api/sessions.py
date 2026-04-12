"""Session endpoints - Redis-only, no relational table.

Sessions are stored as Redis hashes at key session:{session_id} with
a 24h TTL. Memories link to sessions via metadata.session_id.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


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


@router.post("", response_model=SessionResponse, summary="Start a new session")
async def start_session(body: StartSessionRequest) -> SessionResponse:
    """Start a new session. Stores session state in Redis with 24h TTL."""
    session_id = uuid4()
    now = datetime.now().astimezone()

    # TODO: Write to Redis hash at session:{session_id}
    # redis.hset(f"session:{session_id}", mapping={
    #     "agent_id": str(body.agent_id),
    #     "session_type": body.session_type,
    #     "started_at": now.isoformat(),
    #     **body.metadata,
    # })
    # redis.expire(f"session:{session_id}", 86400)  # 24h TTL

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
async def end_session(session_id: UUID) -> EndSessionResponse:
    """End a session. Transitions working memories to episodic."""
    # TODO: Read session from Redis, find working memories with this session_id,
    # transition them to episodic, delete Redis key

    return EndSessionResponse(session_id=session_id, transitioned_count=0)


@router.get("/{session_id}", response_model=SessionResponse, summary="Get session state")
async def get_session(session_id: UUID) -> SessionResponse:
    """Get the current state of a session from Redis."""
    # TODO: Read from Redis hash
    raise HTTPException(status_code=404, detail="Session not found")
