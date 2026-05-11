"""z3rno_server.api.refine — POST + GET /v1/refine (Phase D slice 3).

Registered only when ``REFINE_ENABLED=true``. Mirrors the Phase A
distill endpoint shape: ``POST`` enqueues a Celery job and returns
202 + job_id; ``GET`` polls the ``refine_jobs`` row.

Admin-only — refine mutates Memos (dedupe) and edges (reweight /
prune); ``read``-scoped clients have no business firing it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as sa_text

from z3rno_core.refine.state import insert_refine_job
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.refine import RefineJobResponse, RefineJobStatus, RefineRequest
from z3rno_server.schemas.shared import ErrorResponse
from z3rno_server.workers.refine import refine_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/refine", tags=["refine"])


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


@router.post(
    "",
    response_model=RefineJobResponse,
    status_code=202,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Enqueue a refine run",
)
async def enqueue_refine(
    body: RefineRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin"),
) -> RefineJobResponse:
    org_id = _get_org_id(request)
    job_id = uuid4()

    try:
        conn = await db.connection()
        await insert_refine_job(
            conn,
            job_id=job_id,
            org_id=org_id,
            dataset_id=body.dataset_id,
            trigger="api",
            status="queued",
        )
    except Exception as exc:
        logger.exception("refine.enqueue.insert_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=500, detail="failed to persist refine job") from exc

    try:
        refine_run.apply_async(
            kwargs={
                "job_id": str(job_id),
                "org_id": str(org_id),
                "dataset_id": str(body.dataset_id) if body.dataset_id else None,
                "trigger": "api",
            },
        )
    except Exception as exc:
        logger.exception("refine.enqueue.celery_dispatch_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=503, detail="background worker unavailable") from exc

    return RefineJobResponse(
        job_id=job_id,
        status="queued",
        dataset_id=body.dataset_id,
        enqueued_at=datetime.now().astimezone(),
    )


@router.get(
    "/{job_id}",
    response_model=RefineJobStatus,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Get refine job status",
)
async def get_refine_status(
    job_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> RefineJobStatus:
    _ = _get_org_id(request)

    conn = await db.connection()
    row = (
        await conn.execute(
            sa_text("""
                SELECT id, status::text, dataset_id, trigger,
                       memos_scanned, memos_deduped,
                       edges_reweighted, edges_pruned, feedback_drained,
                       job_metadata, error,
                       started_at, completed_at, created_at, updated_at
                FROM refine_jobs
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": str(job_id)},
        )
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="refine job not found")

    return RefineJobStatus(
        job_id=row[0],
        status=row[1],
        dataset_id=row[2],
        trigger=row[3],
        memos_scanned=row[4],
        memos_deduped=row[5],
        edges_reweighted=row[6],
        edges_pruned=row[7],
        feedback_drained=row[8],
        job_metadata=row[9] or {},
        error=row[10],
        started_at=row[11],
        completed_at=row[12],
        created_at=row[13],
        updated_at=row[14],
    )
