"""Celery tasks for memory lifecycle management.

Scheduled by pg_cron, executed by Celery workers.
"""

from __future__ import annotations

from z3rno_server.workers.celery_app import celery_app


@celery_app.task(name="z3rno.sweep_expired_memories")
def sweep_expired_memories(org_id: str) -> dict[str, int]:
    """Sweep and soft-delete memories past their TTL.

    Called every 5 minutes via pg_cron -> Celery handoff.
    """
    # TODO: create sync engine, call z3rno_core.engine.sweep_expired_memories()
    return {"expired_count": 0}


@celery_app.task(name="z3rno.decay_importance")
def decay_importance(org_id: str) -> dict[str, int]:
    """Apply exponential decay to importance scores.

    Called daily via pg_cron -> Celery handoff.
    """
    # TODO: create sync engine, call z3rno_core.engine.decay_importance()
    return {"decayed_count": 0}


@celery_app.task(name="z3rno.enforce_retention_caps")
def enforce_retention_caps(org_id: str) -> dict[str, int]:
    """Enforce retention caps for all memory types.

    Called hourly via pg_cron -> Celery handoff.
    """
    # TODO: create sync engine, call z3rno_core.engine.enforce_retention_cap()
    return {"expired_count": 0}
