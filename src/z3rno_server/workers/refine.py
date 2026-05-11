"""z3rno_server.workers.refine — Celery task for the Refine pipeline (Phase D).

Bridges synchronous Celery to the async
:class:`z3rno_core.refine.RefinePipeline`. Self-rejects when
``REFINE_ENABLED=false`` so flipping the flag is enough to neutralize
Phase D end-to-end. Auto-discovered by
:mod:`z3rno_server.workers.celery_app`.

The task runs RLS-scoped — it sets ``app.current_org_id`` on its
connection before invoking the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from z3rno_core.distill import StubLLMGateway, get_llm_gateway
from z3rno_core.refine import RefineOptions, RefinePipeline
from z3rno_core.usage import Budgets
from z3rno_server.config import Settings, get_settings
from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _make_refine_budgets(settings: Settings) -> Budgets | None:
    """v0.19.2 — server-default Budgets for refine. Returns None when
    every cap is zero so the pipeline's pre-flight stays fast-path."""
    b = Budgets(
        daily_tokens=settings.usage_budget_daily_tokens,
        daily_llm_calls=settings.usage_budget_daily_llm_calls,
        daily_embeddings=settings.usage_budget_daily_embeddings,
        monthly_tokens=settings.usage_budget_monthly_tokens,
        monthly_llm_calls=settings.usage_budget_monthly_llm_calls,
        monthly_embeddings=settings.usage_budget_monthly_embeddings,
    )
    return None if b.is_empty() else b


def _make_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        echo=False,
    )


def _make_gateway(settings: Settings) -> Any | None:
    """Build an LLM gateway iff any LLM-driven refine stage is enabled.

    Returns None when neither infer nor summarize are on — saves the
    operator from having to set LLM_API_KEY for a dedupe-only refine.
    """
    if not (settings.refine_infer_enabled or settings.refine_summarize_enabled):
        return None
    api_key = settings.effective_llm_api_key
    if not api_key:
        logger.warning(
            "refine_run: infer/summarize enabled but no LLM key configured; using stub"
        )
        return StubLLMGateway(model=settings.llm_model)
    return get_llm_gateway(
        provider="litellm",
        model=settings.llm_model,
        api_key=api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


@celery_app.task(
    name="z3rno.refine_run",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def refine_run(
    self: Any,
    *,
    job_id: str,
    org_id: str,
    dataset_id: str | None = None,
    trigger: str = "api",
) -> dict[str, Any]:
    """Execute one refine pass and return a JSON-safe summary."""
    settings = get_settings()
    if not settings.refine_enabled:
        logger.warning(
            "refine_run invoked while REFINE_ENABLED=false (job_id=%s); refusing", job_id
        )
        return {"job_id": job_id, "status": "rejected", "reason": "refine_disabled"}

    async def _run() -> dict[str, Any]:
        engine = _make_engine(settings)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL app.current_org_id = '{org_id}'"))
                pipeline = RefinePipeline(
                    options=RefineOptions(
                        feedback_weight_decay=settings.feedback_weight_decay,
                        trigger=trigger,
                        infer_enabled=settings.refine_infer_enabled,
                        summarize_enabled=settings.refine_summarize_enabled,
                        infer_max_candidates=settings.refine_infer_max_candidates,
                        budgets=_make_refine_budgets(settings),
                    ),
                    gateway=_make_gateway(settings),
                )
                summary = await pipeline.run(
                    conn,
                    org_id=UUID(org_id),
                    dataset_id=UUID(dataset_id) if dataset_id else None,
                    job_id=UUID(job_id),
                )
                return {
                    "job_id": str(summary.job_id),
                    "status": summary.status,
                    "memos_scanned": summary.memos_scanned,
                    "memos_deduped": summary.memos_deduped,
                    "edges_reweighted": summary.edges_reweighted,
                    "edges_pruned": summary.edges_pruned,
                    "feedback_drained": summary.feedback_drained,
                    "error": summary.error,
                }
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("refine_run failed; will retry (job_id=%s)", job_id)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
