"""z3rno_server.api.ingest — POST /v1/ingest endpoint (Phase B.1).

Three routes wire the IngestPipeline into the FastAPI surface:

  * ``POST /v1/ingest``        — JSON body for ``text`` or ``url`` kinds
                                  (discriminated on ``kind``).
  * ``POST /v1/ingest/file``   — multipart upload for the ``file`` kind.
                                  We split this from the JSON endpoint
                                  because mixing structured JSON and
                                  binary file bytes in one request is
                                  always awkward — clients pick the
                                  endpoint that matches the payload
                                  type.
  * ``GET  /v1/ingest/{job_id}`` — poll job status.

All three are registered only when ``INGEST_ENABLED=true``. With the
flag off the OpenAPI spec is byte-identical to the pre-Phase-B server.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import text as sa_text

from z3rno_core.ingest.state import insert_ingest_job, update_ingest_job
from z3rno_core.storage import (
    PresignedUrlNotSupportedError,
    StorageError,
)
from z3rno_server.config import get_settings
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.observability import INGEST_JOBS_TOTAL
from z3rno_server.schemas.ingest import (
    IngestJobResponse,
    IngestJobStatus,
    IngestRequest,
    IngestTextRequest,
    IngestUploadUrlRequest,
    IngestUploadUrlResponse,
    IngestUrlRequest,
    LoaderDescriptor,
    LoadersResponse,
)
from z3rno_server.schemas.shared import ErrorResponse
from z3rno_server.workers.ingest import _make_storage, ingest_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


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


def _options_dict(req: IngestTextRequest | IngestUrlRequest) -> dict[str, Any]:
    if req.options is None:
        return {}
    return {k: v for k, v in req.options.model_dump().items() if v is not None}


def _enqueue(
    *,
    job_id: UUID,
    org_id: UUID,
    agent_id: UUID,
    payload: dict[str, Any],
    dataset_id: UUID | None,
    options: dict[str, Any],
    request_id: str | None,
) -> None:
    """Send the run to Celery; map dispatch failures to a 503."""
    try:
        ingest_run.apply_async(
            kwargs={
                "job_id": str(job_id),
                "org_id": str(org_id),
                "agent_id": str(agent_id),
                "payload": payload,
                "dataset_id": str(dataset_id) if dataset_id else None,
                "options": options or None,
                "request_id": request_id,
            },
        )
    except Exception as exc:
        logger.exception("ingest.enqueue.celery_dispatch_failed", extra={"job_id": str(job_id)})
        INGEST_JOBS_TOTAL.labels(status="rejected", kind=str(payload.get("kind", "?"))).inc()
        raise HTTPException(status_code=503, detail="background worker unavailable") from exc

    INGEST_JOBS_TOTAL.labels(status="enqueued", kind=str(payload.get("kind", "?"))).inc()


# ---------------------------------------------------------------------------
# POST /v1/ingest — JSON (text + url)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=IngestJobResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Enqueue a text or URL ingest job",
)
async def enqueue_ingest_json(
    body: IngestRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> IngestJobResponse:
    """Validate, persist an ``ingest_jobs`` row, dispatch the Celery
    task, return ``202`` + ``job_id``."""
    org_id = _get_org_id(request)
    job_id = uuid4()

    ingest_kind: Literal["text", "url"]
    if body.kind == "text":
        payload = {
            "kind": "text",
            "text": body.text,
            "filename": body.filename,
            "content_type": body.content_type,
        }
        ingest_kind = "text"
    else:  # body.kind == "url"
        payload = {
            "kind": "url",
            "url": body.url,
        }
        ingest_kind = "url"

    try:
        conn = await db.connection()
        await insert_ingest_job(
            conn,
            job_id=job_id,
            org_id=org_id,
            agent_id=body.agent_id,
            kind=ingest_kind,
            dataset_id=body.dataset_id,
            source_uri=None,
            content_type=getattr(body, "content_type", None) or payload.get("content_type"),
            filename=getattr(body, "filename", None) or payload.get("filename"),
        )
    except Exception as exc:
        logger.exception("ingest.enqueue.insert_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=500, detail="failed to persist ingest job") from exc

    _enqueue(
        job_id=job_id,
        org_id=org_id,
        agent_id=body.agent_id,
        payload=payload,
        dataset_id=body.dataset_id,
        options=_options_dict(body),
        request_id=getattr(request.state, "request_id", None),
    )

    return IngestJobResponse(
        job_id=job_id,
        kind=ingest_kind,
        status="queued",
        dataset_id=body.dataset_id,
        enqueued_at=datetime.now().astimezone(),
    )


# ---------------------------------------------------------------------------
# POST /v1/ingest/file — multipart upload
# ---------------------------------------------------------------------------


@router.post(
    "/file",
    response_model=IngestJobResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
    },
    summary="Enqueue a file-upload ingest job (multipart/form-data)",
)
async def enqueue_ingest_file(
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File(description="The artifact to ingest.")],
    agent_id: Annotated[UUID, Form()],
    dataset_id: Annotated[UUID | None, Form()] = None,
    auto_distill: Annotated[bool | None, Form()] = None,
    chunk_size: Annotated[int | None, Form(ge=64, le=8192)] = None,
    chunk_overlap: Annotated[int | None, Form(ge=0, le=2048)] = None,
    _rbac: None = require_role("admin", "write"),
) -> IngestJobResponse:
    """Validate the upload, persist an ``ingest_jobs`` row, dispatch
    Celery with the file bytes (capped at ``INGEST_MAX_FILE_BYTES``)."""
    settings = get_settings()
    org_id = _get_org_id(request)

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > settings.ingest_max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file size {len(raw)} bytes exceeds INGEST_MAX_FILE_BYTES="
                f"{settings.ingest_max_file_bytes}"
            ),
        )

    job_id = uuid4()
    filename = file.filename
    content_type = file.content_type

    try:
        conn = await db.connection()
        await insert_ingest_job(
            conn,
            job_id=job_id,
            org_id=org_id,
            agent_id=agent_id,
            kind="file",
            dataset_id=dataset_id,
            source_uri=None,  # populated by the worker after storage write
            content_type=content_type,
            filename=filename,
            file_size=len(raw),
        )
    except Exception as exc:
        logger.exception("ingest.enqueue.insert_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=500, detail="failed to persist ingest job") from exc

    options: dict[str, Any] = {}
    if auto_distill is not None:
        options["auto_distill"] = auto_distill
    if chunk_size is not None:
        options["chunk_size"] = chunk_size
    if chunk_overlap is not None:
        options["chunk_overlap"] = chunk_overlap

    _enqueue(
        job_id=job_id,
        org_id=org_id,
        agent_id=agent_id,
        payload={
            "kind": "file",
            "content_hex": raw.hex(),
            "filename": filename,
            "content_type": content_type,
        },
        dataset_id=dataset_id,
        options=options,
        request_id=getattr(request.state, "request_id", None),
    )

    return IngestJobResponse(
        job_id=job_id,
        kind="file",
        status="queued",
        dataset_id=dataset_id,
        enqueued_at=datetime.now().astimezone(),
    )


# ---------------------------------------------------------------------------
# POST /v1/ingest/upload-url — presigned direct-to-storage upload (Phase B.2.1)
# ---------------------------------------------------------------------------


@router.post(
    "/upload-url",
    response_model=IngestUploadUrlResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
    summary="Issue a presigned upload URL for direct-to-storage ingestion",
)
async def issue_upload_url(
    body: IngestUploadUrlRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> IngestUploadUrlResponse:
    """Issue a presigned PUT URL the client uploads to directly.

    The ingest_jobs row is created in ``awaiting_upload`` status. After
    the client PUTs the bytes to ``upload_url`` it must call
    ``POST /v1/ingest/finalize/{job_id}`` to start the worker.
    """
    settings = get_settings()
    org_id = _get_org_id(request)

    if body.file_size is not None and body.file_size > settings.ingest_max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"declared file_size {body.file_size} exceeds INGEST_MAX_FILE_BYTES="
                f"{settings.ingest_max_file_bytes}"
            ),
        )

    storage = _make_storage(settings)
    try:
        presigned = await storage.presigned_put_url(
            org_id=org_id,
            content_type=body.content_type,
            filename=body.filename,
        )
    except PresignedUrlNotSupportedError as exc:
        raise HTTPException(
            status_code=501,
            detail="storage backend does not support presigned upload URLs",
        ) from exc
    except StorageError as exc:
        logger.exception("ingest.upload_url.storage_failed")
        raise HTTPException(status_code=502, detail="storage backend error") from exc

    job_id = uuid4()
    try:
        conn = await db.connection()
        await insert_ingest_job(
            conn,
            job_id=job_id,
            org_id=org_id,
            agent_id=body.agent_id,
            kind="file",
            dataset_id=body.dataset_id,
            source_uri=presigned.source_uri,
            content_type=presigned.content_type,
            filename=body.filename,
            file_size=body.file_size,
            status="awaiting_upload",
        )
    except Exception as exc:
        logger.exception("ingest.upload_url.insert_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=500, detail="failed to persist ingest job") from exc

    return IngestUploadUrlResponse(
        job_id=job_id,
        upload_url=presigned.upload_url,
        source_uri=presigned.source_uri,
        expires_at=presigned.expires_at,
        content_type=presigned.content_type,
    )


# ---------------------------------------------------------------------------
# POST /v1/ingest/finalize/{job_id} — kick the worker after client upload
# ---------------------------------------------------------------------------


@router.post(
    "/finalize/{job_id}",
    response_model=IngestJobResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    summary="Mark a presigned upload as complete and enqueue the worker",
)
async def finalize_upload(
    job_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> IngestJobResponse:
    """Transition an ``awaiting_upload`` job into the worker pipeline.

    Re-finalizing a job is rejected with ``409`` (any non-awaiting
    status). RLS isolates the lookup so cross-tenant finalize attempts
    return ``404``.
    """
    org_id = _get_org_id(request)
    _ = org_id  # asserts auth + RLS context

    conn = await db.connection()
    row = (
        await conn.execute(
            sa_text("""
                SELECT id, agent_id, dataset_id,
                       status::text, source_uri, content_type, filename
                FROM ingest_jobs
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": str(job_id)},
        )
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="ingest job not found")

    status_now = row[3]
    if status_now != "awaiting_upload":
        raise HTTPException(
            status_code=409,
            detail=f"ingest job is not awaiting_upload (status={status_now!r})",
        )

    source_uri: str | None = row[4]
    if not source_uri:
        raise HTTPException(status_code=409, detail="ingest job missing source_uri")

    agent_id: UUID = row[1]
    dataset_id: UUID | None = row[2]
    content_type: str | None = row[5]
    filename: str | None = row[6]

    try:
        await update_ingest_job(conn, job_id=job_id, status="queued")
    except Exception as exc:
        logger.exception("ingest.finalize.update_failed", extra={"job_id": str(job_id)})
        raise HTTPException(status_code=500, detail="failed to update ingest job") from exc

    payload = {
        "kind": "s3_uri",
        "source_uri": source_uri,
        "content_type": content_type,
        "filename": filename,
    }
    _enqueue(
        job_id=job_id,
        org_id=org_id,
        agent_id=agent_id,
        payload=payload,
        dataset_id=dataset_id,
        options={},
        request_id=getattr(request.state, "request_id", None),
    )

    return IngestJobResponse(
        job_id=job_id,
        kind="file",
        status="queued",
        dataset_id=dataset_id,
        enqueued_at=datetime.now().astimezone(),
    )


# ---------------------------------------------------------------------------
# GET /v1/ingest/loaders — what the server can ingest right now
# ---------------------------------------------------------------------------


@router.get(
    "/loaders",
    response_model=LoadersResponse,
    summary="What loaders are active on this server",
)
async def get_loaders(
    request: Request,
    _rbac: None = require_role("admin", "write", "read"),
) -> LoadersResponse:
    """Describe the currently-registered loaders + the relevant feature flags.

    Lets SDK consumers + operators answer "what can this deployment
    actually ingest?" before submitting a job. Reflects the same registry
    the worker uses (multimodal loaders only attached when
    ``MULTIMODAL_ENABLED=true``).
    """
    # Import here to avoid the heavy worker module at app-startup time.
    from z3rno_server.workers.ingest import _make_loader_registry  # noqa: PLC0415

    settings = get_settings()
    registry = _make_loader_registry(settings)
    descriptors = registry.describe_loaders()
    return LoadersResponse(
        multimodal_enabled=settings.multimodal_enabled,
        playwright_enabled=settings.url_playwright_enabled,
        loaders=[LoaderDescriptor(**d) for d in descriptors],
    )


# ---------------------------------------------------------------------------
# GET /v1/ingest/{job_id} — status
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}",
    response_model=IngestJobStatus,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Get the status of an ingest job",
)
async def get_ingest_status(
    job_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> IngestJobStatus:
    """Return the full ``ingest_jobs`` row for ``job_id``.

    RLS isolates jobs by ``org_id`` automatically — a 404 is returned
    both when the job doesn't exist and when it exists in another tenant.
    """
    _ = _get_org_id(request)  # asserts auth + activates RLS for this session

    conn = await db.connection()
    row = (
        await conn.execute(
            sa_text("""
                SELECT id, agent_id, dataset_id,
                       kind::text, status::text, source_uri, content_type,
                       filename, file_size,
                       memory_ids, memos_written,
                       distill_job_id,
                       error, warnings, started_at, completed_at,
                       created_at, updated_at
                FROM ingest_jobs
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": str(job_id)},
        )
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="ingest job not found")

    return IngestJobStatus(
        job_id=row[0],
        agent_id=row[1],
        dataset_id=row[2],
        kind=row[3],
        status=row[4],
        source_uri=row[5],
        content_type=row[6],
        filename=row[7],
        file_size=row[8],
        memory_ids=list(row[9] or []),
        memos_written=row[10],
        distill_job_id=row[11],
        error=row[12],
        warnings=list(row[13] or []),
        started_at=row[14],
        completed_at=row[15],
        created_at=row[16],
        updated_at=row[17],
    )
