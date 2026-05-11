"""z3rno_server.workers.forge — Celery task for the Forge pipeline (Phase A).

Bridges the synchronous Celery worker world to the async
:class:`z3rno_core.forge.ForgePipeline`. Sets RLS context inside the
pipeline (the orchestrator does this per-transaction), tracks
``distill_jobs`` lifecycle, and surfaces failure to Celery so retry
backoff can re-execute idempotently.

Registered name: ``z3rno.forge_distill``. Auto-discovered by
:mod:`z3rno_server.workers.celery_app` when it scans this package.

The task is **dormant unless ``DISTILL_ENABLED=true``**. The API endpoint
that enqueues it (Task 13) gates on the same flag, so flipping the flag
off is enough to neutralize Phase A end-to-end.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from z3rno_core.distill import StubLLMGateway, get_llm_gateway
from z3rno_core.engine.embedding import EmbeddingProvider, LiteLLMEmbeddingProvider
from z3rno_core.forge import ForgeOptions, ForgePipeline
from z3rno_server.config import Settings, get_settings
from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(settings: Settings) -> AsyncEngine:
    """Construct a small worker-scoped async engine.

    Workers don't share the FastAPI request engine; each task creates a
    short-lived engine and disposes it on exit. Pool sizing kept tight
    because worker tasks are usually long-running and few in flight.
    """
    return create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        echo=False,
    )


def _make_gateway(settings: Settings) -> Any:
    """Construct an LLM gateway from server settings.

    Uses the ``stub`` provider when no LLM credentials are configured —
    keeps the worker importable in environments that haven't supplied a
    real key (e.g. CI smoke tests). Production deployments must supply
    an ``LLM_API_KEY`` (or reuse ``OPENAI_API_KEY``) when they flip
    ``DISTILL_ENABLED=true``.
    """
    api_key = settings.effective_llm_api_key
    if not api_key:
        logger.warning(
            "forge_distill: no LLM_API_KEY/OPENAI_API_KEY configured; using stub gateway"
        )
        return StubLLMGateway(model=settings.llm_model)
    return get_llm_gateway(
        provider="litellm",
        model=settings.llm_model,
        api_key=api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _make_ontology_resolver(settings: Settings) -> object | None:
    """Construct an :class:`OntologyResolver` when the operator opted in.

    Returns ``None`` when ``ONTOLOGY_RESOLVER=none`` (default) — the
    Forge pipeline then skips grounding entirely. Construction is
    lazy-imported so deployments that never set the flag don't need
    the ``[ontology]`` extra.
    """
    if settings.ontology_resolver == "none":
        return None
    if settings.ontology_resolver != "rdflib":
        logger.warning(
            "forge: unknown ONTOLOGY_RESOLVER=%r; resolver disabled",
            settings.ontology_resolver,
        )
        return None
    if not settings.ontology_file_path:
        logger.warning(
            "forge: ONTOLOGY_RESOLVER=rdflib but ONTOLOGY_FILE_PATH empty; resolver disabled"
        )
        return None
    try:
        from z3rno_core.ontology import OntologyResolver, load_ontology

        index = load_ontology(settings.ontology_file_path)
    except Exception as exc:
        logger.warning("forge: ontology load failed (%s); resolver disabled", exc)
        return None
    resolver: object = OntologyResolver(
        index,
        strategy=settings.ontology_matching_strategy,
        fuzzy_threshold=settings.ontology_fuzzy_threshold,
    )
    return resolver


def _make_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    """Embeddings for Forge-written Memos.

    Reuses the existing :class:`LiteLLMEmbeddingProvider` so distilled
    Memos receive embeddings on the same model the rest of the system
    uses. Returns ``None`` when no API key is configured — the Memos are
    persisted with NULL embedding and the standard embedding worker
    (``z3rno.generate_embedding``) can fill them later.
    """
    if not (settings.openai_api_key or settings.effective_llm_api_key):
        return None
    return LiteLLMEmbeddingProvider(model=settings.embedding_model)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="z3rno.forge_distill",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def forge_distill(
    self: Any,
    *,
    job_id: str,
    org_id: str,
    agent_id: str,
    memory_ids: list[str],
    api_key_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run the Forge over ``memory_ids`` and return a JSON-safe summary.

    The task is fully idempotent — :class:`ForgePipeline` consults
    ``entity_provenance`` before processing each memory, so a Celery
    retry after a partial completion only redoes the unfinished work.

    Returns a dict suitable for JSON serialization (Celery's default
    serializer) so the result is queryable via the result backend.
    """
    settings = get_settings()
    if not settings.distill_enabled:
        logger.warning(
            "forge_distill invoked while DISTILL_ENABLED=false (job_id=%s); refusing", job_id
        )
        return {
            "job_id": job_id,
            "status": "rejected",
            "reason": "distill_disabled",
        }

    async def _run() -> dict[str, Any]:
        engine = _make_engine(settings)
        try:
            gateway = _make_gateway(settings)
            embedding_provider = _make_embedding_provider(settings)
            pipeline = ForgePipeline(
                gateway=gateway,
                embedding_provider=embedding_provider,
                ontology_resolver=_make_ontology_resolver(settings),
                options=ForgeOptions(
                    chunk_size=settings.distill_chunk_size,
                    chunk_overlap=settings.distill_chunk_overlap,
                    max_concurrency=settings.distill_max_concurrency,
                    summary_style=settings.distill_summary_style,
                    provenance_required=settings.distill_provenance_required,
                ),
            )
            summary = await pipeline.run(
                engine,
                org_id=UUID(org_id),
                agent_id=UUID(agent_id),
                memory_ids=[UUID(m) for m in memory_ids],
                job_id=UUID(job_id),
                api_key_id=UUID(api_key_id) if api_key_id else None,
                request_id=request_id,
            )
            return {
                "job_id": str(summary.job_id),
                "status": summary.status,
                "memories_processed": summary.memories_processed,
                "memories_skipped": summary.memories_skipped,
                "chunks_total": summary.chunks_total,
                "chunks_failed": summary.chunks_failed,
                "entities_extracted": summary.entities_extracted,
                "relationships_extracted": summary.relationships_extracted,
                "memos_written": summary.memos_written,
                "error": summary.error,
                "skipped_memory_ids": [str(m) for m in summary.skipped_memory_ids],
                "started_at": summary.started_at.isoformat() if summary.started_at else None,
                "completed_at": summary.completed_at.isoformat() if summary.completed_at else None,
            }
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        # Re-raise via Celery retry so the orchestrator can also pick up
        # where it left off on the next attempt (idempotent via
        # already_distilled). Capped retries.
        logger.exception("forge_distill failed; will retry (job_id=%s)", job_id)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
