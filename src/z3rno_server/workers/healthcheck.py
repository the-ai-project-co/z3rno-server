"""Worker healthcheck task.

Provides a lightweight Celery task that workers can execute to prove liveness.
Called by the GET /v1/worker/health endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime

from z3rno_server.workers.celery_app import celery_app


@celery_app.task(name="z3rno.worker_ping", bind=True, ignore_result=False)  # type: ignore[misc]
def worker_ping(self):  # type: ignore[no-untyped-def]
    """Return a simple OK response to prove the worker is alive."""
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "worker": self.request.hostname,
    }
