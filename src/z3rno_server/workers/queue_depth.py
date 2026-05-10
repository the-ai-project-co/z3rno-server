"""Inspect Celery queue depth via Valkey for backpressure.

Celery (with the Redis/Valkey broker) stores pending tasks as a list
keyed by the queue name. ``LLEN`` returns the number of in-flight
tasks. Used by the ingest endpoints to return 503 + Retry-After when
the queue is too deep, so clients back off rather than piling more
work on a saturated worker pool.

Kept narrow: one async helper, sync-safe wrapper, no caching. The
read is O(1) on Valkey, fine to call once per ingest enqueue.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from z3rno_server.config import get_settings

logger = logging.getLogger(__name__)

# The default Celery task queue name — matches
# ``task_default_queue`` in workers.celery_app.
DEFAULT_QUEUE_NAME = "z3rno"


async def get_queue_depth(queue_name: str = DEFAULT_QUEUE_NAME) -> int:
    """Return the number of pending tasks in ``queue_name``.

    Returns ``0`` on any Valkey failure — the safety property is "if we
    can't tell, let the request through" (fail-open). The rate-limit
    middleware uses the same posture. A persistently unreachable
    broker is its own problem, surfaced by other healthchecks.
    """
    settings = get_settings()
    try:
        r = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.effective_valkey_url, decode_responses=False
        )
        try:
            depth = await r.llen(queue_name)
            return int(depth)
        finally:
            await r.aclose()
    except Exception:
        logger.warning(
            "queue_depth.lookup_failed", extra={"queue": queue_name}, exc_info=True
        )
        return 0
