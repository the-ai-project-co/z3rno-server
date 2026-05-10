"""z3rno_server.workers.ingest — Celery task for IngestPipeline (Phase B.1).

Bridges the synchronous Celery worker world to the async
:class:`z3rno_core.ingest.IngestPipeline`. Self-gates on
``INGEST_ENABLED``; auto-discovered by ``celery_app``.

When ``INGEST_AUTO_DISTILL=true`` (default), every successful ingest
schedules :func:`forge_distill` for the newly created memory IDs and
records the resulting ``distill_job_id`` back on the ``ingest_jobs``
row. The chained Forge run picks up exactly the work the IngestPipeline
just produced.

Registered name: ``z3rno.ingest_run``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from z3rno_core.distill.graph_writer import insert_distill_job
from z3rno_core.engine.embedding import EmbeddingProvider, LiteLLMEmbeddingProvider
from z3rno_core.ingest import IngestInput, IngestOptions, IngestPipeline, IngestRunSummary
from z3rno_core.loaders import get_default_registry
from z3rno_core.security.rls import set_org_context
from z3rno_core.storage import LocalStorageBackend, StorageBackend
from z3rno_server.config import Settings, get_settings
from z3rno_server.workers.celery_app import celery_app
from z3rno_server.workers.forge import forge_distill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(settings: Settings) -> AsyncEngine:
    """One-shot engine for a single Celery task invocation."""
    return create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        echo=False,
    )


def _make_storage(settings: Settings) -> StorageBackend:
    """Construct the configured storage backend.

    Phase B.1 only ships ``local``. Phase B.2 will add ``s3`` to this
    factory.
    """
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorageBackend(settings.storage_local_dir)
    raise ValueError(f"unsupported STORAGE_BACKEND={backend!r} (Phase B.1 ships only 'local')")


def _make_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    """Reuse the existing embedding provider so ingested Memos are
    embeddable on the same model as the rest of the system.

    Returns ``None`` when no key is configured — the Memo is stored
    with NULL embedding and the standard ``z3rno.generate_embedding``
    task can fill it in later.
    """
    if not (settings.openai_api_key or settings.effective_llm_api_key):
        return None
    return LiteLLMEmbeddingProvider(model=settings.embedding_model)


def _decode_input(payload: dict[str, Any]) -> IngestInput:
    """Reconstruct :class:`IngestInput` from a Celery JSON payload.

    File ingests carry their bytes through Celery as a hex string so
    the JSON serializer can round-trip them. Tiny by Celery standards
    because :data:`Settings.ingest_max_file_bytes` caps the payload
    size; large attachments belong on the storage backend, not in the
    message bus.
    """
    kind = payload["kind"]
    if kind == "file" and payload.get("content_hex") is not None:
        content = bytes.fromhex(payload["content_hex"])
    else:
        content = None
    return IngestInput(
        kind=kind,
        text=payload.get("text"),
        url=payload.get("url"),
        content=content,
        filename=payload.get("filename"),
        content_type=payload.get("content_type"),
    )


def _build_post_ingest(
    settings: Settings,
    *,
    org_id: UUID,
    agent_id: UUID,
    request_id: str | None,
) -> Callable[[IngestRunSummary], Awaitable[UUID | None]] | None:
    """Build the ``post_ingest`` callback that fans out to forge_distill.

    Returns ``None`` if either ``INGEST_AUTO_DISTILL=false`` or
    ``DISTILL_ENABLED=false`` — both signals mean "ingest only, no
    automatic graph build."
    """
    if not (settings.ingest_auto_distill and settings.distill_enabled):
        return None

    async def _hook(summary: IngestRunSummary) -> UUID | None:
        if not summary.memory_ids:
            return None
        distill_job_id = uuid4()
        # Pre-insert the distill_jobs row inline so the FK from
        # ingest_jobs.distill_job_id is satisfied immediately — without
        # this, the IngestPipeline's update_ingest_job step would race
        # the forge worker and could violate fk_ingest_jobs_distill.
        # The forge worker's own "insert if not exists" check becomes a
        # no-op when it finds the row already there.
        engine = _make_engine(settings)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(lambda c: set_org_context(c, org_id))
                await insert_distill_job(
                    conn,
                    job_id=distill_job_id,
                    org_id=org_id,
                    agent_id=agent_id,
                    memory_ids=summary.memory_ids,
                    model=settings.llm_model,
                    chunk_size=settings.distill_chunk_size,
                    chunk_overlap=settings.distill_chunk_overlap,
                    max_concurrency=settings.distill_max_concurrency,
                )
        finally:
            await engine.dispose()

        forge_distill.apply_async(
            kwargs={
                "job_id": str(distill_job_id),
                "org_id": str(org_id),
                "agent_id": str(agent_id),
                "memory_ids": [str(m) for m in summary.memory_ids],
                "request_id": request_id,
            },
        )
        return distill_job_id

    return _hook


def _summary_to_dict(summary: IngestRunSummary) -> dict[str, Any]:
    """Map :class:`IngestRunSummary` to a JSON-safe dict."""
    return {
        "job_id": str(summary.job_id),
        "status": summary.status,
        "memory_ids": [str(m) for m in summary.memory_ids],
        "skipped_existing": [str(m) for m in summary.skipped_existing],
        "source_uri": summary.source_uri,
        "content_type": summary.content_type,
        "filename": summary.filename,
        "file_size": summary.file_size,
        "distill_job_id": str(summary.distill_job_id) if summary.distill_job_id else None,
        "error": summary.error,
        "started_at": summary.started_at.isoformat() if summary.started_at else None,
        "completed_at": summary.completed_at.isoformat() if summary.completed_at else None,
    }


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="z3rno.ingest_run",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def ingest_run(
    self: Any,
    *,
    job_id: str,
    org_id: str,
    agent_id: str,
    payload: dict[str, Any],
    dataset_id: str | None = None,
    options: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run the IngestPipeline for one input and return a JSON-safe summary.

    The task is idempotent: :class:`IngestPipeline` consults
    ``ingest_jobs`` before running and ``find_memory_by_source_uri``
    before storing, so a Celery retry after a partial completion never
    duplicates work or memories.
    """
    settings = get_settings()
    if not settings.ingest_enabled:
        logger.warning(
            "ingest_run invoked while INGEST_ENABLED=false (job_id=%s); refusing",
            job_id,
        )
        return {
            "job_id": job_id,
            "status": "rejected",
            "reason": "ingest_disabled",
        }

    async def _run() -> dict[str, Any]:
        engine = _make_engine(settings)
        try:
            pipeline = IngestPipeline(
                registry=get_default_registry(),
                storage=_make_storage(settings),
                embedding_provider=_make_embedding_provider(settings),
                url_fetch_max_bytes=settings.ingest_max_file_bytes,
                url_fetch_timeout_seconds=settings.url_fetch_timeout_seconds,
                url_allowed_schemes=tuple(settings.url_allowed_schemes_list),
            )
            ingest_input = _decode_input(payload)
            opts = IngestOptions(
                auto_distill=(options or {}).get("auto_distill", settings.ingest_auto_distill),
                chunk_size=(options or {}).get("chunk_size", settings.ingest_default_chunk_size),
                chunk_overlap=(options or {}).get("chunk_overlap", settings.distill_chunk_overlap),
                summary_style=(options or {}).get("summary_style", settings.distill_summary_style),
            )
            post_ingest = (
                _build_post_ingest(
                    settings,
                    org_id=UUID(org_id),
                    agent_id=UUID(agent_id),
                    request_id=request_id,
                )
                if opts.auto_distill
                else None
            )

            summary = await pipeline.run(
                engine,
                org_id=UUID(org_id),
                agent_id=UUID(agent_id),
                ingest_input=ingest_input,
                dataset_id=UUID(dataset_id) if dataset_id else None,
                job_id=UUID(job_id),
                options=opts,
                post_ingest=post_ingest,
                request_id=request_id,
            )
            return _summary_to_dict(summary)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("ingest_run failed; will retry (job_id=%s)", job_id)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
