"""Memory API endpoints: store, recall, forget."""

from __future__ import annotations

from fastapi import APIRouter

from z3rno_server.schemas.memories import (
    ForgetRequest,
    ForgetResponse,
    RecallRequest,
    RecallResponse,
    StoreMemoryRequest,
)
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/memories", tags=["memories"])


@router.post(
    "",
    response_model=dict[str, str],
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Store a new memory",
)
async def store_memory(request: StoreMemoryRequest) -> dict[str, str]:
    """Store a new memory with optional embedding, relationships, and TTL."""
    # TODO: implement with z3rno_core.engine.store()
    return {"id": "placeholder", "status": "not_implemented"}


@router.post(
    "/recall",
    response_model=RecallResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Recall memories by query",
)
async def recall_memories(request: RecallRequest) -> RecallResponse:
    """Recall memories using vector similarity, filters, and temporal queries."""
    # TODO: implement with z3rno_core.engine.recall()
    return RecallResponse(results=[], total=0, query=request.query)


@router.post(
    "/forget",
    response_model=ForgetResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Forget (delete) memories",
)
async def forget_memories(request: ForgetRequest) -> ForgetResponse:
    """Soft or hard delete memories with optional cascade."""
    # TODO: implement with z3rno_core.engine.forget()
    return ForgetResponse(
        deleted_count=0,
        hard_deleted=request.hard_delete,
        cascade_count=0,
        memory_ids=[],
    )
