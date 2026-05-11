"""Celery backend — the default path. Mirrors what direct
``celery_app.send_task`` callsites already do, but routed through
the ``JobBackend`` seam so other backends can replace it without
touching the task code.
"""

from __future__ import annotations

from typing import Any

from z3rno_server.workers.backends.base import (
    DispatchResult,
    JobBackend,
    JobBackendError,
)


class CeleryBackend(JobBackend):
    name = "celery"

    def enqueue(
        self,
        *,
        task_name: str,
        payload: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> DispatchResult:
        from z3rno_server.workers.celery_app import (
            celery_app,
        )

        opts = options or {}
        try:
            result = celery_app.send_task(
                task_name,
                kwargs=payload,
                queue=opts.get("queue", "z3rno"),
                priority=opts.get("priority"),
                countdown=opts.get("countdown"),
            )
        except Exception as exc:
            raise JobBackendError(
                f"celery dispatch of {task_name} failed: {exc}"
            ) from exc
        return DispatchResult(
            job_id=result.id,
            backend=self.name,
            extra={"queue": opts.get("queue", "z3rno")},
        )
