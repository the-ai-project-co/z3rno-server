"""z3rno_server.api.search — POST /v1/ingest/search endpoint (Phase B.2).

Asks the configured search provider (Tavily) for the top N URLs, then
enqueues a separate ``ingest_run`` task for each. Returns 202 with a
list of job_ids — one per URL — so the caller can poll each
independently via ``GET /v1/ingest/{job_id}``.

Registered only when ``INGEST_ENABLED=true`` AND ``TAVILY_API_KEY`` is
configured.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request

from z3rno_core.ingest.state import (
    get_search_batch_aggregate,
    insert_ingest_job,
)
from z3rno_core.scrapers import SearchError, TavilyScraper
from z3rno_server.config import get_settings
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.search import (
    SearchBatchStatus,
    SearchIngestJob,
    SearchIngestRequest,
    SearchIngestResponse,
)
from z3rno_server.schemas.shared import ErrorResponse
from z3rno_server.workers.ingest import ingest_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


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
    "/search",
    response_model=SearchIngestResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Search the web for query and ingest the top N results",
)
async def enqueue_search_ingest(
    body: SearchIngestRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> SearchIngestResponse:
    """Run the configured search provider on ``query``, enqueue one
    ingest job per URL hit, return the list of job_ids."""
    settings = get_settings()
    if not settings.tavily_api_key:
        # Defensive: should not be reachable since the router only
        # registers when the key is present, but rate-limit operators
        # who unset the key without a restart.
        raise HTTPException(status_code=503, detail="search provider not configured")

    org_id = _get_org_id(request)

    scraper = TavilyScraper(
        api_key=settings.tavily_api_key,
        search_depth=settings.tavily_search_depth,
    )
    try:
        results = await scraper.search(body.query, max_results=body.max_results)
    except SearchError as exc:
        logger.warning("ingest.search.provider_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="search provider failed") from exc

    batch_id = uuid4()
    if not results:
        return SearchIngestResponse(
            query=body.query,
            dataset_id=body.dataset_id,
            enqueued_at=datetime.now().astimezone(),
            batch_id=batch_id,
            jobs=[],
        )

    options_dict: dict[str, Any] = {}
    if body.options is not None:
        options_dict = {k: v for k, v in body.options.model_dump().items() if v is not None}

    conn = await db.connection()
    jobs: list[SearchIngestJob] = []

    for hit in results:
        job_id = uuid4()
        try:
            await insert_ingest_job(
                conn,
                job_id=job_id,
                org_id=org_id,
                agent_id=body.agent_id,
                kind="url",
                dataset_id=body.dataset_id,
                source_uri=hit.url,
                search_batch_id=batch_id,
            )
        except Exception as exc:
            logger.exception(
                "ingest.search.insert_failed",
                extra={"job_id": str(job_id), "url": hit.url},
            )
            raise HTTPException(status_code=500, detail="failed to persist ingest job") from exc

        try:
            ingest_run.apply_async(
                kwargs={
                    "job_id": str(job_id),
                    "org_id": str(org_id),
                    "agent_id": str(body.agent_id),
                    "payload": {"kind": "url", "url": hit.url},
                    "dataset_id": str(body.dataset_id) if body.dataset_id else None,
                    "options": options_dict or None,
                    "request_id": getattr(request.state, "request_id", None),
                },
            )
        except Exception as exc:
            logger.exception(
                "ingest.search.celery_dispatch_failed",
                extra={"job_id": str(job_id)},
            )
            raise HTTPException(status_code=503, detail="background worker unavailable") from exc

        jobs.append(SearchIngestJob(job_id=job_id, url=hit.url, title=hit.title))

    return SearchIngestResponse(
        query=body.query,
        dataset_id=body.dataset_id,
        enqueued_at=datetime.now().astimezone(),
        batch_id=batch_id,
        jobs=jobs,
    )


@router.get(
    "/search/{batch_id}",
    response_model=SearchBatchStatus,
    summary="Poll aggregate status for a search ingest batch",
    responses={
        404: {"model": ErrorResponse, "description": "Batch not found for tenant"},
    },
)
async def get_search_batch_status(
    batch_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> SearchBatchStatus:
    """Aggregate status for every ingest job tagged with ``batch_id``.

    RLS isolates by ``org_id`` — a 404 covers both "no such batch" and
    "batch belongs to another tenant."
    """
    org_id = _get_org_id(request)
    conn = await db.connection()
    aggregate = await get_search_batch_aggregate(
        conn, org_id=org_id, batch_id=batch_id
    )
    if aggregate is None:
        raise HTTPException(status_code=404, detail="search batch not found")
    return SearchBatchStatus(batch_id=batch_id, **aggregate)
