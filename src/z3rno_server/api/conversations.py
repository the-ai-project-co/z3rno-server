"""Phase G slice 2 — conversation REST endpoints.

Three routes, always registered (no feature flag — the underlying
tables exist after Migration 028 and the surface is additive):

  * POST /v1/conversations             — create
  * GET  /v1/conversations/{id}        — fetch metadata
  * POST /v1/conversations/{id}/turns  — append a turn
  * GET  /v1/conversations/{id}/turns  — list turns (paginated)

Recall scoped to a conversation goes through the existing
``POST /v1/memories/recall`` with the new ``conversation_id``
field on ``RecallRequest`` — see ``schemas.memories``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from z3rno_core.conversations import (
    ConversationNotFoundError,
    add_turn,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_turns,
    needs_summary,
)
from z3rno_server.dependencies import DbSession, ReadDbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.conversations import (
    ConversationCreate,
    ConversationResponse,
    TurnAddRequest,
    TurnAddResponse,
    TurnListResponse,
    TurnResponse,
)
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="no tenant context")
    return org_id  # type: ignore[no-any-return]


@router.post(
    "",
    response_model=ConversationResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Create a conversation",
)
async def post_conversation(
    body: ConversationCreate,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> ConversationResponse:
    org_id = _get_org_id(request)
    conn = await db.connection()
    conv = await create_conversation(
        conn,
        org_id=org_id,
        agent_id=body.agent_id,
        user_id=body.user_id,
        title=body.title,
        summary_cadence=body.summary_cadence,
        metadata=body.metadata,
    )
    return ConversationResponse(
        id=conv.id,
        agent_id=conv.agent_id,
        user_id=conv.user_id,
        title=conv.title,
        summary_cadence=conv.summary_cadence,
        turn_count=conv.turn_count,
        last_summary_turn=conv.last_summary_turn,
        metadata=conv.metadata,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Fetch conversation metadata",
)
async def get_one_conversation(
    conversation_id: UUID,
    request: Request,
    db: ReadDbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> ConversationResponse:
    org_id = _get_org_id(request)
    conn = await db.connection()
    try:
        conv = await get_conversation(
            conn, org_id=org_id, conversation_id=conversation_id
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return ConversationResponse(
        id=conv.id,
        agent_id=conv.agent_id,
        user_id=conv.user_id,
        title=conv.title,
        summary_cadence=conv.summary_cadence,
        turn_count=conv.turn_count,
        last_summary_turn=conv.last_summary_turn,
        metadata=conv.metadata,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.post(
    "/{conversation_id}/turns",
    response_model=TurnAddResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Append a turn",
)
async def post_turn(
    conversation_id: UUID,
    body: TurnAddRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> TurnAddResponse:
    """Stamp an existing Memo as the next turn.

    Returns ``needs_summary=true`` when the cadence threshold has been
    crossed since the last summary — clients use this signal to enqueue
    a Forge summarization job and then call back with
    ``mark_summary_emitted`` once the summary Memo has been recorded.
    """
    org_id = _get_org_id(request)
    conn = await db.connection()
    try:
        turn_index = await add_turn(
            conn,
            org_id=org_id,
            conversation_id=conversation_id,
            memory_id=body.memory_id,
            turn_role=body.turn_role,
        )
        conv = await get_conversation(
            conn, org_id=org_id, conversation_id=conversation_id
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return TurnAddResponse(turn_index=turn_index, needs_summary=needs_summary(conv))


@router.get(
    "/{conversation_id}/turns",
    response_model=TurnListResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="List turns in order",
)
async def get_turns(
    conversation_id: UUID,
    request: Request,
    db: ReadDbSession,
    _rbac: None = require_role("admin", "write", "read"),
    after_turn: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> TurnListResponse:
    org_id = _get_org_id(request)
    conn = await db.connection()
    try:
        # 404 cross-tenant / soft-deleted via the explicit lookup.
        await get_conversation(conn, org_id=org_id, conversation_id=conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    rows = await list_turns(
        conn,
        org_id=org_id,
        conversation_id=conversation_id,
        after_turn=after_turn,
        limit=limit,
    )
    return TurnListResponse(
        turns=[
            TurnResponse(
                memory_id=t.memory_id,
                turn_index=t.turn_index,
                turn_role=t.turn_role,
                content=t.content,
                created_at=t.created_at,
            )
            for t in rows
        ],
        total=len(rows),
        conversation_id=conversation_id,
    )


@router.delete(
    "/{conversation_id}",
    status_code=204,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Soft-delete a conversation",
)
async def delete_one_conversation(
    conversation_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> None:
    """v0.19.3 — sets ``deleted_at`` on the conversation row.

    Existing turn Memos stay queryable via the standard recall
    surface (so audit/history isn't lost); the conversation just no
    longer accepts new turns and its endpoints 404. Idempotent on
    repeated DELETEs.
    """
    org_id = _get_org_id(request)
    conn = await db.connection()
    deleted = await delete_conversation(
        conn, org_id=org_id, conversation_id=conversation_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="conversation not found")
