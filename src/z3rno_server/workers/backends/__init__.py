"""Phase F slice 6 — pluggable job backends.

The Forge / Ingest / Refine tasks all enqueue work through one
``enqueue_job(task_name, payload)`` seam. Three backends sit behind
that seam:

  * ``celery`` (default) — existing path; ``celery_app.send_task`` to
    a Valkey-backed worker pool.
  * ``modal`` — defer to a Modal function. Operator-controlled cost
    profile (per-job-second billing, GPU autoscale).
  * ``k8s_jobs`` — submit a one-shot K8s Job per task. Right for
    self-hosted clusters that already have Jobs + KEDA / Argo.

The CeleryBackend is always importable. Modal + K8s impls are
lazy-imported on first use so a celery-only deploy doesn't have to
install the matching SDK.

Selection is driven by ``DISTRIBUTED_BACKEND`` server-side; the
existing direct ``celery_app.send_task`` callsites still work — the
new dispatch is purely additive.
"""

from __future__ import annotations

from z3rno_server.workers.backends.base import (
    DispatchResult,
    JobBackend,
    JobBackendError,
)
from z3rno_server.workers.backends.celery_backend import CeleryBackend
from z3rno_server.workers.backends.dispatch import (
    enqueue_job,
    get_backend,
    reset_backend_cache,
)

__all__ = [
    "CeleryBackend",
    "DispatchResult",
    "JobBackend",
    "JobBackendError",
    "enqueue_job",
    "get_backend",
    "reset_backend_cache",
]
