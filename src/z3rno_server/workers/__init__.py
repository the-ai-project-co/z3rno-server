"""Celery worker tasks.

Importing this package registers all task modules with the celery_app
instance so a single ``celery -A z3rno_server.workers.celery_app worker``
or ``... beat`` invocation picks up every task and the beat schedule.
"""

from z3rno_server.workers import (  # noqa: F401  (side-effect imports register tasks)
    audit_drain,
    embeddings,
    forge,
    healthcheck,
    ingest,
    ingest_watchdog,
    lifecycle,
)
