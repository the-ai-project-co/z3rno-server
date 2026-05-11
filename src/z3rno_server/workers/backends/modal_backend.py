"""Modal backend — lazy import.

Each Z3rno task name maps to a registered Modal function in a
companion app file (``z3rno_server/workers/modal/z3rno_modal_app.py``).
At dispatch time we look the function up by name and ``.spawn(...)``
it (Modal's async fire-and-forget). The Modal call id is returned
as the ``job_id``.

If the ``modal`` package isn't installed (operator hasn't picked
``DISTRIBUTED_BACKEND=modal``), the lazy import raises
``JobBackendError`` with a clear remediation message — the celery
default still works.
"""

from __future__ import annotations

from typing import Any

from z3rno_server.workers.backends.base import (
    DispatchResult,
    JobBackend,
    JobBackendError,
)


class ModalBackend(JobBackend):
    name = "modal"

    def __init__(self, app_name: str = "z3rno") -> None:
        self.app_name = app_name

    def _lookup(self, task_name: str) -> Any:
        try:
            import modal  # type: ignore[import-not-found]
        except ImportError as exc:
            raise JobBackendError(
                "Modal backend requested but the `modal` package is not "
                "installed. `pip install modal` and authenticate."
            ) from exc
        try:
            return modal.Function.lookup(self.app_name, task_name)
        except Exception as exc:
            raise JobBackendError(
                f"Modal function lookup failed for "
                f"app={self.app_name!r} task={task_name!r}: {exc}"
            ) from exc

    def enqueue(
        self,
        *,
        task_name: str,
        payload: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> DispatchResult:
        fn = self._lookup(task_name)
        try:
            call = fn.spawn(**payload)
        except Exception as exc:
            raise JobBackendError(
                f"Modal spawn of {task_name} failed: {exc}"
            ) from exc
        return DispatchResult(
            job_id=call.object_id,
            backend=self.name,
            extra={"app": self.app_name},
        )
