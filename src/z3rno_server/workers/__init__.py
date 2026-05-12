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
    # v0.21.1 — register the refine_run + refine_scheduler_tick tasks
    # with the celery_app so the worker can execute /v1/refine jobs.
    # Pre-fix, only the FastAPI route was registered (via REFINE_ENABLED);
    # the worker never imported the module, so refine jobs sat at
    # status=queued indefinitely. Bug H in
    # z3rno-local/improvements/operator-notes/
    # V0-21-STARTER-KIT-SMOKE-2026-05-12.md.
    refine,
)
