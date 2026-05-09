"""z3rno_server.api.distill — POST /v1/distill endpoint (Phase A).

Wires the Forge pipeline into the FastAPI surface. Two routes:

  * ``POST /v1/distill``           — enqueue a distillation job; returns ``job_id``.
  * ``GET  /v1/distill/{job_id}``  — poll job status.

Behavior is gated by ``DISTILL_ENABLED`` in ``z3rno_server.config.Settings``.
When the flag is off, the router is **not registered** with the FastAPI app
(see ``main.py``). With it on, both endpoints are visible in OpenAPI and
behave normally.

Auth, RLS context, rate limiting, and audit logging follow the same
conventions as :mod:`z3rno_server.api.memories`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as sa_text

from z3rno_core.distill.graph_writer import insert_distill_job
from z3rno_server.config import get_settings
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.distill import (
    DistillJobResponse,
    DistillJobStatus,
    DistillRequest,
)
from z3rno_server.schemas.shared import ErrorResponse
from z3rno_server.workers.forge import forge_distill

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/distill", tags=["distill"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=401, detail="No org context")
    if not isinstance(org_id, UUID):
        try:
            org_id = UUID(str(org_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid org context") from exc
    return org_id


# ---------------------------------------------------------------------------
# POST /v1/distill — enqueue
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DistillJobResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
    summary="Enqueue a Forge distillation job",
)
async def enqueue_distill(
    body: DistillRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> DistillJobResponse:
    """Validate, persist a ``distill_jobs`` row, enqueue the Celery task, return ``job_id``.

    The endpoint is **non-blocking**: the LLM work happens in the worker.
    Clients should poll :func:`get_distill_status` (or subscribe to the
    underlying Celery result via a future SDK feature) until ``status``
    becomes ``completed`` or ``failed``.
    """
    settings = get_settings()
    if (
        body.chunk_overlap is not None
        and body.chunk_size is not None
        and body.chunk_overlap >= body.chunk_size
    ):
        raise HTTPException(
            status_code=400,
            detail="chunk_overlap must be strictly less than chunk_size",
        )

    org_id = _get_org_id(request)

    chunk_size = body.chunk_size or settings.distill_chunk_size
    chunk_overlap = body.chunk_overlap or settings.distill_chunk_overlap
    max_concurrency = body.max_concurrency or settings.distill_max_concurrency

    job_id = uuid4()

    try:
        conn = await db.connection()
        await insert_distill_job(
            conn,
            job_id=job_id,
            org_id=org_id,
            agent_id=body.agent_id,
            memory_ids=body.memory_ids,
            model=settings.llm_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_concurrency=max_concurrency,
        )
    except Exception as exc:
        logger.exception("distill.enqueue.insert_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=500, detail="failed to persist distill job") from exc

    # Enqueue in Celery. The worker re-checks DISTILL_ENABLED before running.
    try:
        forge_distill.apply_async(
            kwargs={
                "job_id": str(job_id),
                "org_id": str(org_id),
                "agent_id": str(body.agent_id),
                "memory_ids": [str(m) for m in body.memory_ids],
                "request_id": getattr(request.state, "request_id", None),
            },
        )
    except Exception as exc:
        logger.exception("distill.enqueue.celery_dispatch_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=503, detail="background worker unavailable") from exc

    return DistillJobResponse(
        job_id=job_id,
        status="queued",
        memory_ids=body.memory_ids,
        enqueued_at=datetime.now().astimezone(),
    )


# ---------------------------------------------------------------------------
# GET /v1/distill/{job_id} — status
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}",
    response_model=DistillJobStatus,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Get the status of a Forge distillation job",
)
async def get_distill_status(
    job_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> DistillJobStatus:
    """Return the full ``distill_jobs`` row for ``job_id``.

    RLS isolates jobs by ``org_id`` automatically — a 404 is returned
    both when the job doesn't exist and when it exists in another tenant.
    """
    _ = _get_org_id(request)  # asserts auth + activates RLS for this session

    conn = await db.connection()
    row = (
        await conn.execute(
            sa_text("""
                SELECT id, agent_id, status::text, model, memory_ids,
                       chunk_size, chunk_overlap, max_concurrency,
                       chunks_total, chunks_failed,
                       entities_extracted, relationships_extracted, memos_written,
                       error, started_at, completed_at, created_at, updated_at
                FROM distill_jobs
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": str(job_id)},
        )
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="distill job not found")

    return DistillJobStatus(
        job_id=row[0],
        agent_id=row[1],
        status=row[2],
        model=row[3],
        memory_ids=list(row[4]),
        chunk_size=row[5],
        chunk_overlap=row[6],
        max_concurrency=row[7],
        chunks_total=row[8],
        chunks_failed=row[9],
        entities_extracted=row[10],
        relationships_extracted=row[11],
        memos_written=row[12],
        error=row[13],
        started_at=row[14],
        completed_at=row[15],
        created_at=row[16],
        updated_at=row[17],
    )
