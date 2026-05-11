"""``JobBackend`` ABC + result shape."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class JobBackendError(Exception):
    """Raised when a backend can't be loaded (missing extras, missing
    config) or a dispatch fails before the task has been accepted by
    the remote system. Once accepted, failures are observed via the
    usual per-backend mechanism (Celery result backend, Modal job
    log, K8s Job status)."""


@dataclass(frozen=True)
class DispatchResult:
    """Returned by ``JobBackend.enqueue``.

    ``job_id`` is the backend-native handle (Celery task id, Modal
    call id, K8s Job name). ``backend`` is the lower-case backend
    name so callers can log / route based on it without sniffing
    types.
    """

    job_id: str
    backend: str
    extra: dict[str, Any]


class JobBackend(ABC):
    """One-shot enqueue surface — all backends implement just this.

    Per-backend nuance (queues, GPU profiles, resource limits) is
    expressed through ``options`` rather than method explosion so the
    dispatch helper stays generic.
    """

    name: str = ""

    @abstractmethod
    def enqueue(
        self,
        *,
        task_name: str,
        payload: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> DispatchResult: ...
