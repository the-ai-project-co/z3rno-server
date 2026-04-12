"""Memory API endpoints: store, recall, forget."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from z3rno_core.engine import (
    NoOpEmbeddingProvider,
    StoreError,
    forget,
    recall,
    store,
)
from z3rno_core.engine.forget import ForgetError
from z3rno_core.engine.store import RelationshipInput as EngineRelInput
from z3rno_core.models.enums import MemoryType
from z3rno_server.dependencies import DbSession
from z3rno_server.schemas.memories import (
    ForgetRequest,
    ForgetResponse,
    MemoryResponse,
    RecallRequest,
    RecallResponse,
    RecallResultItem,
    StoreMemoryRequest,
)
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/memories", tags=["memories"])


def _get_org_id(request: Request) -> UUID:
    """Extract org_id from request state (set by auth middleware)."""
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=401, detail="No org context")
    return org_id  # type: ignore[no-any-return]


@router.post(
    "",
    response_model=MemoryResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Store a new memory",
)
async def store_memory(
    body: StoreMemoryRequest,
    request: Request,
    db: DbSession,
) -> MemoryResponse:
    """Store a new memory with optional embedding, relationships, and TTL."""
    org_id = _get_org_id(request)

    # Map relationships
    relationships = (
        [
            EngineRelInput(
                target_memory_id=r.target_memory_id,
                relationship_type=r.relationship_type,
                weight=r.weight,
                metadata=r.metadata,
            )
            for r in body.relationships
        ]
        if body.relationships
        else None
    )

    try:
        conn = await db.connection()
        result = await store(
            conn,
            org_id=org_id,
            agent_id=body.agent_id,
            content=body.content,
            memory_type=MemoryType(body.memory_type),
            embedding_provider=NoOpEmbeddingProvider(),  # TODO: real provider
            user_id=body.user_id,
            metadata=body.metadata,
            relationships=relationships,
            ttl_seconds=body.ttl_seconds,
            importance=body.importance,
            request_id=getattr(request.state, "request_id", None),
        )
    except StoreError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return MemoryResponse(
        id=result.memory_id,
        agent_id=body.agent_id,
        content=body.content,
        memory_type=body.memory_type,
        importance_score=result.importance_score,
        recall_count=0,
        embedding_model=result.embedding_model,
        created_at=result.created_at,
        metadata=body.metadata or {},
    )


@router.post(
    "/recall",
    response_model=RecallResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Recall memories by query",
)
async def recall_memories(
    body: RecallRequest,
    request: Request,
    db: DbSession,
) -> RecallResponse:
    """Recall memories using vector similarity, filters, and temporal queries."""
    org_id = _get_org_id(request)

    conn = await db.connection()
    results = await recall(
        conn,
        org_id=org_id,
        agent_id=body.agent_id,
        query=body.query,
        embedding_provider=NoOpEmbeddingProvider() if body.query else None,
        memory_type=body.memory_type,
        filters=body.filters,
        top_k=body.top_k,
        similarity_threshold=body.similarity_threshold,
        time_range=body.time_range,
        as_of=body.as_of,
        include_deleted=body.include_deleted,
        request_id=getattr(request.state, "request_id", None),
    )

    items = [
        RecallResultItem(
            memory_id=r.memory_id,
            content=r.content,
            summary=r.summary,
            memory_type=r.memory_type,
            similarity_score=r.similarity_score,
            importance_score=r.importance_score,
            relevance_score=r.relevance_score,
            recall_count=r.recall_count,
            created_at=r.created_at,
            metadata=r.metadata,
        )
        for r in results
    ]

    return RecallResponse(results=items, total=len(items), query=body.query)


@router.post(
    "/forget",
    response_model=ForgetResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Forget (delete) memories",
)
async def forget_memories(
    body: ForgetRequest,
    request: Request,
    db: DbSession,
) -> ForgetResponse:
    """Soft or hard delete memories with optional cascade."""
    org_id = _get_org_id(request)

    try:
        conn = await db.connection()
        result = await forget(
            conn,
            org_id=org_id,
            agent_id=body.agent_id,
            memory_id=body.memory_id,
            memory_ids=body.memory_ids,
            hard_delete=body.hard_delete,
            cascade=body.cascade,
            reason=body.reason,
            request_id=getattr(request.state, "request_id", None),
        )
    except ForgetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return ForgetResponse(
        deleted_count=result.deleted_count,
        hard_deleted=result.hard_deleted,
        cascade_count=result.cascade_count,
        memory_ids=result.memory_ids,
    )
