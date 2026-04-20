"""Worker health endpoint.

Provides a healthcheck for Celery workers by dispatching a ping task
and waiting for the result with a short timeout.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["worker"])

logger = logging.getLogger(__name__)


@router.get("/v1/worker/health")
async def worker_health() -> JSONResponse:
    """Check Celery worker health by dispatching a ping task.

    Returns 200 if a worker responds within 5 seconds, 503 otherwise.
    """
    try:
        from z3rno_server.workers.healthcheck import worker_ping

        result: Any = worker_ping.delay().get(timeout=5)
        return JSONResponse(status_code=200, content=result)
    except Exception:
        logger.warning("Worker healthcheck failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "detail": "No Celery worker responded within timeout",
            },
        )
