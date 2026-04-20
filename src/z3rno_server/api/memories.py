"""Memory API endpoints: store, recall, forget."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as sa_text

from z3rno_core.engine import (
    MemoryNotFoundError,
    NoOpEmbeddingProvider,
    StoreError,
    UpdateError,
    forget,
    get_memory,
    recall,
    store,
    update_memory,
)
from z3rno_core.engine.forget import ForgetError
from z3rno_core.engine.store import RelationshipInput as EngineRelInput
from z3rno_core.models.enums import MemoryType
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.memories import (
    BatchStoreRequest,
    BatchStoreResponse,
    ForgetRequest,
    ForgetResponse,
    MemoryHistoryResponse,
    MemoryResponse,
    MemoryVersionResponse,
    RecallRequest,
    RecallResponse,
    RecallResultItem,
    StoreMemoryRequest,
    UpdateMemoryRequest,
)
from z3rno_server.schemas.shared import ErrorResponse

logger = logging.getLogger(__name__)

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
    _rbac: None = require_role("admin", "write"),
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

    # Enqueue async embedding generation via Celery worker
    _enqueue_embedding(str(result.memory_id), body.content)

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
    _rbac: None = require_role("admin", "write", "read"),
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
    _rbac: None = require_role("admin", "write"),
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


@router.post(
    "/batch",
    response_model=BatchStoreResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Store multiple memories in a single request",
)
async def batch_store_memories(
    body: BatchStoreRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> BatchStoreResponse:
    """Store multiple memories atomically in a single transaction."""
    org_id = _get_org_id(request)
    results: list[MemoryResponse] = []

    conn = await db.connection()
    for mem in body.memories:
        relationships = (
            [
                EngineRelInput(
                    target_memory_id=r.target_memory_id,
                    relationship_type=r.relationship_type,
                    weight=r.weight,
                    metadata=r.metadata,
                )
                for r in mem.relationships
            ]
            if mem.relationships
            else None
        )

        try:
            result = await store(
                conn,
                org_id=org_id,
                agent_id=mem.agent_id,
                content=mem.content,
                memory_type=MemoryType(mem.memory_type),
                embedding_provider=NoOpEmbeddingProvider(),
                user_id=mem.user_id,
                metadata=mem.metadata,
                relationships=relationships,
                ttl_seconds=mem.ttl_seconds,
                importance=mem.importance,
                request_id=getattr(request.state, "request_id", None),
            )
        except StoreError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Enqueue async embedding generation
        _enqueue_embedding(str(result.memory_id), mem.content)

        results.append(
            MemoryResponse(
                id=result.memory_id,
                agent_id=mem.agent_id,
                content=mem.content,
                memory_type=mem.memory_type,
                importance_score=result.importance_score,
                recall_count=0,
                embedding_model=result.embedding_model,
                created_at=result.created_at,
                metadata=mem.metadata or {},
            )
        )

    return BatchStoreResponse(results=results, stored_count=len(results))


@router.get(
    "/{memory_id}",
    response_model=MemoryResponse,
    responses={404: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Get a single memory by ID",
)
async def get_memory_by_id(
    memory_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> MemoryResponse:
    """Retrieve a single memory by its UUID."""
    org_id = _get_org_id(request)

    try:
        conn = await db.connection()
        result = await get_memory(conn, org_id=org_id, memory_id=memory_id)
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return MemoryResponse(
        id=result.id,
        agent_id=result.agent_id,
        content=result.content,
        memory_type=result.memory_type,
        importance_score=result.importance_score,
        recall_count=result.recall_count,
        embedding_model=result.embedding_model,
        created_at=result.created_at,
        metadata=result.metadata,
    )


@router.get(
    "/{memory_id}/history",
    response_model=MemoryHistoryResponse,
    responses={404: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Get temporal history of a memory",
)
async def get_memory_history_endpoint(
    memory_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> MemoryHistoryResponse:
    """Return all temporal versions of a memory (SCD Type 2 history)."""
    _get_org_id(request)  # Ensure authenticated

    conn = await db.connection()

    # Query all temporal versions directly via async connection
    result = await conn.execute(
        sa_text("""
            SELECT id, content, memory_type, importance_score,
                   valid_from, valid_to, metadata
            FROM memories
            WHERE id = CAST(:memory_id AS uuid)
            ORDER BY valid_from ASC
        """),
        {"memory_id": str(memory_id)},
    )
    rows = result.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")

    versions = [
        MemoryVersionResponse(
            id=row[0],
            content=row[1],
            memory_type=row[2],
            importance_score=float(row[3]),
            valid_from=row[4],
            valid_to=row[5],
            metadata=row[6] or {},
        )
        for row in rows
    ]

    return MemoryHistoryResponse(
        memory_id=memory_id,
        versions=versions,
        total=len(versions),
    )


@router.patch(
    "/{memory_id}",
    response_model=MemoryResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
    summary="Update a memory",
)
async def update_memory_endpoint(
    memory_id: UUID,
    body: UpdateMemoryRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> MemoryResponse:
    """Update a memory's content, metadata, or importance score."""
    org_id = _get_org_id(request)

    if body.content is None and body.metadata is None and body.importance is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of content, metadata, or importance must be provided",
        )

    try:
        conn = await db.connection()
        result = await update_memory(
            conn,
            org_id=org_id,
            memory_id=memory_id,
            content=body.content,
            metadata=body.metadata,
            importance=body.importance,
            request_id=getattr(request.state, "request_id", None),
        )
    except UpdateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return MemoryResponse(
        id=result.id,
        agent_id=result.agent_id,
        content=result.content,
        memory_type=result.memory_type,
        importance_score=result.importance_score,
        recall_count=result.recall_count,
        embedding_model=result.embedding_model,
        created_at=result.created_at,
        metadata=result.metadata,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enqueue_embedding(memory_id: str, content: str) -> None:
    """Best-effort enqueue of async embedding generation via Celery."""
    try:
        from z3rno_server.workers.embeddings import generate_embedding  # noqa: PLC0415

        generate_embedding.delay(memory_id, content, "text-embedding-3-small")
    except Exception:
        logger.debug("Celery unavailable, skipping embedding enqueue for %s", memory_id)
