"""Celery beat task: drain the per-org audit hash chain.

Engine operations write events to ``audit_log_pending`` (parallel-safe,
no chain). This task reads that queue, computes the per-org SHA-256
chain, and writes chained rows into ``audit_log``. It runs on a short
interval so the lag between an operation and its appearance in the
audit log stays sub-second.

Concurrency control:

  * Per-org transactional advisory lock inside ``drain_audit_chain``.
    Two workers calling at the same time on the same org are safe —
    the second one returns 0 immediately.
  * RLS: the drainer needs to see pending rows across all orgs. It
    sets ``app.current_org_id`` once per org loop iteration so RLS
    matches. ``list_orgs_with_pending`` in the discovery query runs
    with no org context and assumes the worker DB role can read
    audit_log_pending unrestricted (the role bypasses RLS, or RLS is
    disabled on the session).

The interval is controlled by ``AUDIT_DRAIN_INTERVAL_SECONDS`` (default
1.0). Lower it for tighter visibility, raise it for less DB churn.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from z3rno_core.engine.audit import flush_audit_chain, list_orgs_with_pending
from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno",
)

AUDIT_DRAIN_INTERVAL_SECONDS = float(
    os.environ.get("AUDIT_DRAIN_INTERVAL_SECONDS", "1.0")
)
AUDIT_DRAIN_BATCH_SIZE = int(os.environ.get("AUDIT_DRAIN_BATCH_SIZE", "500"))
AUDIT_DRAIN_MAX_ORGS_PER_TICK = int(
    os.environ.get("AUDIT_DRAIN_MAX_ORGS_PER_TICK", "100")
)


def _engine() -> AsyncEngine:
    return create_async_engine(DATABASE_URL, pool_size=2, max_overflow=0)


@celery_app.task(name="z3rno.audit_drain")
def audit_drain() -> dict[str, int]:
    """Drain pending audit rows for every org with backlog.

    Returns a small status dict: ``{orgs_drained, rows_drained}``.
    """

    async def _run() -> dict[str, int]:
        engine = _engine()
        rows_drained = 0
        orgs_drained = 0
        try:
            async with engine.begin() as conn:
                # Discover orgs with backlog. Caller role is expected to
                # bypass RLS; if it doesn't, the periodic task simply sees
                # whatever orgs match its session context (still correct,
                # just less efficient at high org counts).
                orgs = await list_orgs_with_pending(
                    conn, limit=AUDIT_DRAIN_MAX_ORGS_PER_TICK
                )

            for org_id in orgs:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("SELECT set_config('app.current_org_id', :o, false)"),
                        {"o": str(org_id)},
                    )
                    n = await flush_audit_chain(
                        conn, org_id, batch_size=AUDIT_DRAIN_BATCH_SIZE
                    )
                if n:
                    rows_drained += n
                    orgs_drained += 1
        finally:
            await engine.dispose()
        return {"orgs_drained": orgs_drained, "rows_drained": rows_drained}

    result = asyncio.run(_run())
    if result["rows_drained"]:
        logger.info(
            "audit_drain: drained %d rows across %d orgs",
            result["rows_drained"],
            result["orgs_drained"],
        )
    return result


# Celery beat schedule entry — Celery Beat picks this up automatically when
# imported via celery_app.conf.beat_schedule.
celery_app.conf.beat_schedule = {
    **getattr(celery_app.conf, "beat_schedule", {}),
    "z3rno-audit-drain": {
        "task": "z3rno.audit_drain",
        "schedule": AUDIT_DRAIN_INTERVAL_SECONDS,
    },
}
