"""Backend selection + the public ``enqueue_job(...)`` seam.

One singleton ``JobBackend`` per process, picked from
``DISTRIBUTED_BACKEND`` at first access. ``reset_backend_cache()`` is
exposed for tests that need to flip backends mid-run.
"""

from __future__ import annotations

from typing import Any

from z3rno_server.config import get_settings
from z3rno_server.workers.backends.base import (
    DispatchResult,
    JobBackend,
    JobBackendError,
)
from z3rno_server.workers.backends.celery_backend import CeleryBackend

_BACKEND: JobBackend | None = None


def reset_backend_cache() -> None:
    """Force the next ``get_backend()`` call to re-read config."""
    global _BACKEND  # noqa: PLW0603
    _BACKEND = None


def _build_backend(name: str) -> JobBackend:
    n = name.strip().lower()
    if n in ("", "celery"):
        return CeleryBackend()
    if n == "modal":
        from z3rno_server.workers.backends.modal_backend import (
            ModalBackend,
        )

        settings = get_settings()
        return ModalBackend(
            app_name=getattr(settings, "modal_app_name", "z3rno") or "z3rno"
        )
    if n in ("k8s", "k8s_jobs", "kubernetes"):
        from z3rno_server.workers.backends.k8s_backend import (
            K8sJobsBackend,
        )

        settings = get_settings()
        return K8sJobsBackend(
            namespace=getattr(settings, "k8s_jobs_namespace", "z3rno") or "z3rno",
            image=getattr(settings, "k8s_jobs_image", "") or None,
        )
    raise JobBackendError(f"unknown DISTRIBUTED_BACKEND: {name!r}")


def get_backend() -> JobBackend:
    global _BACKEND  # noqa: PLW0603
    if _BACKEND is None:
        settings = get_settings()
        _BACKEND = _build_backend(getattr(settings, "distributed_backend", "celery"))
    return _BACKEND


def enqueue_job(
    task_name: str,
    payload: dict[str, Any],
    *,
    options: dict[str, Any] | None = None,
) -> DispatchResult:
    """Dispatch ``task_name`` with ``payload`` through the configured
    backend. Returns a ``DispatchResult`` whose ``job_id`` is the
    backend-native handle clients poll with."""
    return get_backend().enqueue(task_name=task_name, payload=payload, options=options)
