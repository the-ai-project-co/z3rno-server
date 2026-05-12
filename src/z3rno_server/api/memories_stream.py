"""Phase G slice 5 — streaming recall.

``POST /v1/memories/recall/stream`` returns text/event-stream. For
TRACE the handler emits one ``step`` event per refinement step (so
the client gets the first vector seeds in ~30-50ms instead of waiting
for the full multi-step run). For non-TRACE strategies, exactly one
``results`` event lands followed by ``done``.

SSE format:

  event: step
  data: {"step": 0, "query": "...", "results": [...], "elapsed_ms": 42.1}

  event: results
  data: {"results": [...], "strategy_used": "VECTOR", "total": 5}

  event: done
  data: {"elapsed_ms": 123.4}

All payloads share the ``RecallResultItem`` shape used by the
non-streaming endpoint.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from z3rno_core.engine import NoOpEmbeddingProvider, recall
from z3rno_core.engine.recall import RecallResponse
from z3rno_core.retrieval import UnknownStrategyError, registered_strategies
from z3rno_server.config import get_settings
from z3rno_server.dependencies import DbSession, ReadDbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.memories import RecallRequest
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/memories", tags=["memories"])


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="no tenant context")
    return org_id  # type: ignore[no-any-return]


def _serialize_result(r: Any) -> dict[str, Any]:
    return {
        "memory_id": str(r.memory_id),
        "content": r.content,
        "summary": r.summary,
        "memory_type": r.memory_type,
        "similarity_score": getattr(r, "relevance_score", 0.0),
        "importance_score": r.importance_score,
        "relevance_score": r.relevance_score,
        "recall_count": r.recall_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "metadata": dict(r.metadata or {}),
        "score_components": dict(getattr(r, "score_components", {}) or {}),
    }


@router.post(
    "/recall/stream",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
    summary="Recall memories with per-step SSE streaming",
)
async def recall_stream(
    body: RecallRequest,
    request: Request,
    db: DbSession,
    read_db: ReadDbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> EventSourceResponse:
    """Stream recall events as SSE. TRACE emits one ``step`` per
    refinement; other strategies emit one ``results``.

    The handler runs recall() in a background task and pushes events
    onto an asyncio.Queue that the SSE generator consumes — keeps the
    handler async-clean without blocking on the recall coroutine.
    """
    org_id = _get_org_id(request)
    settings = get_settings()
    started_at = time.perf_counter()

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def _step_cb(step: int, query: str, results: Any) -> None:
        await queue.put(
            (
                "step",
                {
                    "step": int(step),
                    "query": query,
                    "results": [_serialize_result(r) for r in results],
                    "elapsed_ms": (time.perf_counter() - started_at) * 1000.0,
                },
            )
        )

    async def _run_recall() -> None:
        try:
            read_conn = await read_db.connection()
            write_conn = await db.connection()
            resp: RecallResponse = await recall(
                read_conn,
                write_conn=write_conn,
                org_id=org_id,
                agent_id=body.agent_id,
                query=body.query,
                strategy=body.strategy,
                rerank=body.rerank,
                role=body.role,
                memory_type=body.memory_type,
                # v0.21.2 — renamed; v0.21.1 — user_id is a real predicate.
                metadata_filter=body.metadata_filter,
                user_id=body.user_id,
                conversation_id=body.conversation_id,
                top_k=body.top_k,
                similarity_threshold=body.similarity_threshold,
                time_range=body.time_range,
                as_of=body.as_of,
                include_deleted=body.include_deleted,
                embedding_provider=NoOpEmbeddingProvider(),
                tier_route=(
                    body.tier_route
                    if body.tier_route is not None
                    else settings.memory_tier_auto_route
                ),
                step_callback=_step_cb,
                request_id=getattr(request.state, "request_id", None),
            )
        except UnknownStrategyError:
            await queue.put(
                (
                    "error",
                    {
                        "detail": (
                            f"unknown strategy {body.strategy!r}; known: "
                            f"{', '.join(registered_strategies())}"
                        ),
                    },
                )
            )
            await queue.put(("done", {}))
            return
        except Exception as exc:
            await queue.put(("error", {"detail": str(exc)}))
            await queue.put(("done", {}))
            return

        await queue.put(
            (
                "results",
                {
                    "results": [_serialize_result(r) for r in resp.results],
                    "total": resp.total,
                    "strategy_used": resp.strategy_used,
                    "strategies_considered": list(resp.strategies_considered),
                    "reranked": resp.reranked,
                },
            )
        )
        await queue.put(
            ("done", {"elapsed_ms": (time.perf_counter() - started_at) * 1000.0})
        )

    task = asyncio.create_task(_run_recall())

    async def _event_gen() -> Any:
        try:
            while True:
                kind, payload = await queue.get()
                yield {"event": kind, "data": json.dumps(payload, default=str)}
                if kind == "done":
                    break
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(_event_gen())
