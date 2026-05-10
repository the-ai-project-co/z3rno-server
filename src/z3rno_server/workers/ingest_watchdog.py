"""Celery beat task: fail orphaned ``ingest_jobs`` rows.

If ``IngestPipeline.run()`` crashes before it can transition its row
out of ``running`` — process killed, asyncpg disconnect, OOM — the
row sits in ``running`` forever. There's no other recovery path. This
task scans for stale rows and transitions them to ``failed`` so the
client polling ``GET /v1/ingest/{job_id}`` eventually gets a definitive
answer instead of a perpetual ``running``.

Configuration (env vars; defaults conservative — designed to *never*
false-positive on a slow-but-progressing job):

  * ``INGEST_WATCHDOG_ENABLED`` — master switch (default: ``true``)
  * ``INGEST_WATCHDOG_INTERVAL_SECONDS`` — beat tick (default: ``300``)
  * ``INGEST_WATCHDOG_STALE_AFTER_SECONDS`` — threshold (default: ``3600``;
    one hour. Multi-GB ingests finish well inside this.)
  * ``INGEST_WATCHDOG_BATCH_SIZE`` — rows per tick (default: ``100``)

The task is RLS-bypass — the worker DB role has BYPASSRLS, so it sees
``ingest_jobs`` across every tenant.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from z3rno_core.ingest.state import mark_stale_running_jobs_failed
from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno",
)

INGEST_WATCHDOG_ENABLED = (
    os.environ.get("INGEST_WATCHDOG_ENABLED", "true").lower() == "true"
)
INGEST_WATCHDOG_INTERVAL_SECONDS = float(
    os.environ.get("INGEST_WATCHDOG_INTERVAL_SECONDS", "300")
)
INGEST_WATCHDOG_STALE_AFTER_SECONDS = int(
    os.environ.get("INGEST_WATCHDOG_STALE_AFTER_SECONDS", "3600")
)
INGEST_WATCHDOG_BATCH_SIZE = int(os.environ.get("INGEST_WATCHDOG_BATCH_SIZE", "100"))


def _engine() -> AsyncEngine:
    return create_async_engine(DATABASE_URL, pool_size=2, max_overflow=0)


@celery_app.task(name="z3rno.ingest_watchdog")
def ingest_watchdog() -> dict[str, int | list[str]]:
    """Transition stale ``running`` ingest_jobs to ``failed``.

    Returns ``{"failed_count": int, "job_ids": [str, ...]}``.
    """
    if not INGEST_WATCHDOG_ENABLED:
        return {"failed_count": 0, "job_ids": []}

    async def _run() -> dict[str, int | list[str]]:
        engine = _engine()
        try:
            async with engine.begin() as conn:
                stale = await mark_stale_running_jobs_failed(
                    conn,
                    stale_after_seconds=INGEST_WATCHDOG_STALE_AFTER_SECONDS,
                    limit=INGEST_WATCHDOG_BATCH_SIZE,
                )
        finally:
            await engine.dispose()
        return {"failed_count": len(stale), "job_ids": [str(i) for i in stale]}

    result = asyncio.run(_run())
    if result["failed_count"]:
        logger.warning(
            "ingest_watchdog: failed %d stale running jobs (>%ds)",
            result["failed_count"],
            INGEST_WATCHDOG_STALE_AFTER_SECONDS,
        )
    return result


# Beat schedule entry — registered when this module is imported.
if INGEST_WATCHDOG_ENABLED:
    celery_app.conf.beat_schedule = {
        **getattr(celery_app.conf, "beat_schedule", {}),
        "z3rno-ingest-watchdog": {
            "task": "z3rno.ingest_watchdog",
            "schedule": INGEST_WATCHDOG_INTERVAL_SECONDS,
        },
    }
