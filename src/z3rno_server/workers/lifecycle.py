"""Celery tasks for memory lifecycle management.

Scheduled by pg_cron, executed by Celery workers. Each task creates
its own async engine and runs the z3rno-core engine function.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno",
)


def _get_async_engine():  # type: ignore[no-untyped-def]
    """Create a one-shot async engine for worker tasks."""
    return create_async_engine(DATABASE_URL, pool_size=2, max_overflow=0)


@celery_app.task(name="z3rno.sweep_expired_memories")
def sweep_expired_memories(org_id: str) -> dict[str, int | list[str]]:
    """Sweep and soft-delete memories past their TTL.

    Called every 5 minutes via pg_cron -> Celery handoff.
    """
    from uuid import UUID

    from z3rno_core.engine.lifecycle import sweep_expired_memories as _sweep

    async def _run() -> dict[str, int | list[str]]:
        engine = _get_async_engine()
        try:
            async with AsyncSession(engine) as session:
                conn = await session.connection()
                result = await _sweep(conn, org_id=UUID(org_id))
                await session.commit()
                return {
                    "expired_count": result.expired_count,
                    "memory_ids": [str(mid) for mid in result.memory_ids],
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@celery_app.task(name="z3rno.decay_importance")
def decay_importance(org_id: str) -> dict[str, int]:
    """Apply exponential decay to importance scores.

    Called daily via pg_cron -> Celery handoff.
    """
    from uuid import UUID

    from z3rno_core.engine.lifecycle import decay_importance as _decay

    async def _run() -> dict[str, int]:
        engine = _get_async_engine()
        try:
            async with AsyncSession(engine) as session:
                conn = await session.connection()
                result = await _decay(conn, org_id=UUID(org_id))
                await session.commit()
                return {
                    "decayed_count": result.decayed_count,
                    "below_threshold_count": result.below_threshold_count,
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@celery_app.task(name="z3rno.enforce_retention_caps")
def enforce_retention_caps(org_id: str) -> dict[str, int]:
    """Enforce retention caps for all memory types.

    Called hourly via pg_cron -> Celery handoff.
    """
    from uuid import UUID

    from z3rno_core.engine.lifecycle import enforce_retention_cap as _enforce

    async def _run() -> dict[str, int]:
        engine = _get_async_engine()
        try:
            async with AsyncSession(engine) as session:
                conn = await session.connection()
                # enforce_retention_cap needs agent_id and max_count per type;
                # the full implementation would iterate all agents for this org.
                # For now, call with defaults for each memory type.
                total_evicted = 0
                from z3rno_core.models.enums import MemoryType

                for mem_type in MemoryType:
                    # Look up lifecycle policy for this org+type
                    from sqlalchemy import text as sa_text

                    policy_result = await conn.execute(
                        sa_text("""
                            SELECT max_count FROM lifecycle_policies
                            WHERE org_id = CAST(:org_id AS uuid)
                              AND memory_type = CAST(:mem_type AS memory_type_enum)
                        """),
                        {"org_id": org_id, "mem_type": mem_type.value},
                    )
                    policy_row = policy_result.fetchone()
                    if not policy_row or not policy_row[0]:
                        continue

                    # Get all agents for this org
                    agents_result = await conn.execute(
                        sa_text("""
                            SELECT DISTINCT agent_id FROM memories
                            WHERE org_id = CAST(:org_id AS uuid)
                              AND deleted_at IS NULL
                        """),
                        {"org_id": org_id},
                    )
                    for agent_row in agents_result.fetchall():
                        result = await _enforce(
                            conn,
                            org_id=UUID(org_id),
                            agent_id=agent_row[0],
                            memory_type=mem_type,
                            max_count=policy_row[0],
                        )
                        total_evicted += result.evicted_count

                await session.commit()
                return {"evicted_count": total_evicted}
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@celery_app.task(name="z3rno.ensure_audit_partitions")
def ensure_audit_partitions() -> dict[str, int | list[str]]:
    """Create audit_log partitions for the next 3 months.

    Called daily via pg_cron -> Celery handoff.
    """
    from z3rno_core.engine.lifecycle import ensure_audit_partitions as _ensure

    async def _run() -> dict[str, int | list[str]]:
        engine = _get_async_engine()
        try:
            async with AsyncSession(engine) as session:
                conn = await session.connection()
                result = await _ensure(conn, months_ahead=3)
                await session.commit()
                return {
                    "created_count": result.created_count,
                    "partition_names": result.partition_names,
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())
