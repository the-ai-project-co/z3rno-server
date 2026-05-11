"""Phase F slice 6 — backend selection + dispatch tests.

The CeleryBackend's actual ``send_task`` is mocked out; the Modal +
K8s backends are tested by asserting their lazy imports fail with
``JobBackendError`` (since neither dep is installed in CI). The
acceptance bar: switching ``DISTRIBUTED_BACKEND`` via env flips the
backend used by ``enqueue_job(...)`` without touching task callsites.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from z3rno_server.workers.backends import (
    CeleryBackend,
    JobBackendError,
    enqueue_job,
    get_backend,
    reset_backend_cache,
)
from z3rno_server.workers.backends.base import DispatchResult


def _settings_with(backend: str) -> MagicMock:
    s = MagicMock()
    s.distributed_backend = backend
    s.modal_app_name = "z3rno"
    s.k8s_jobs_namespace = "z3rno"
    s.k8s_jobs_image = ""
    return s


def test_default_backend_is_celery() -> None:
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with(""),
    ):
        b = get_backend()
    assert isinstance(b, CeleryBackend)
    assert b.name == "celery"


def test_explicit_celery_backend() -> None:
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with("celery"),
    ):
        b = get_backend()
    assert isinstance(b, CeleryBackend)


def test_unknown_backend_raises() -> None:
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with("bogus"),
    ), pytest.raises(JobBackendError, match="unknown DISTRIBUTED_BACKEND"):
        get_backend()


def test_celery_backend_dispatch_returns_handle() -> None:
    """CeleryBackend should call ``send_task`` and return its id."""
    reset_backend_cache()
    fake_result = MagicMock()
    fake_result.id = "celery-abc-123"

    fake_app = MagicMock()
    fake_app.send_task.return_value = fake_result

    with (
        patch(
            "z3rno_server.workers.backends.dispatch.get_settings",
            return_value=_settings_with("celery"),
        ),
        patch(
            "z3rno_server.workers.celery_app.celery_app",
            fake_app,
        ),
    ):
        result = enqueue_job(
            "z3rno.forge_distill",
            payload={"org_id": "x", "job_id": "y"},
            options={"queue": "forge", "priority": 5},
        )
    assert isinstance(result, DispatchResult)
    assert result.job_id == "celery-abc-123"
    assert result.backend == "celery"
    fake_app.send_task.assert_called_once()
    args, kwargs = fake_app.send_task.call_args
    assert args == ("z3rno.forge_distill",)
    assert kwargs["queue"] == "forge"
    assert kwargs["priority"] == 5
    assert kwargs["kwargs"] == {"org_id": "x", "job_id": "y"}


def test_celery_send_task_failure_wraps_as_backend_error() -> None:
    reset_backend_cache()
    fake_app = MagicMock()
    fake_app.send_task.side_effect = RuntimeError("broker down")

    with (
        patch(
            "z3rno_server.workers.backends.dispatch.get_settings",
            return_value=_settings_with("celery"),
        ),
        patch(
            "z3rno_server.workers.celery_app.celery_app",
            fake_app,
        ),
        pytest.raises(JobBackendError, match="celery dispatch"),
    ):
        enqueue_job("z3rno.forge_distill", payload={})


def test_modal_backend_fails_loud_without_dep() -> None:
    """If modal isn't installed, the lazy import path must raise
    ``JobBackendError`` rather than crashing with ModuleNotFoundError."""
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with("modal"),
    ):
        backend = get_backend()
    with (
        patch.dict("sys.modules", {"modal": None}),
        pytest.raises(JobBackendError, match=r"modal.*not installed"),
    ):
        backend.enqueue(task_name="z3rno.forge_distill", payload={})


def test_k8s_backend_fails_loud_without_dep() -> None:
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with("k8s_jobs"),
    ):
        backend = get_backend()
    with (
        patch.dict("sys.modules", {"kubernetes": None}),
        pytest.raises(JobBackendError, match=r"kubernetes.*not installed"),
    ):
        backend.enqueue(task_name="z3rno.forge_distill", payload={})


def test_k8s_render_payload_into_env() -> None:
    """K8s render must JSON-serialize the payload into the env so the
    pod's entrypoint can decode it. Pure rendering, no API call."""
    from z3rno_server.workers.backends.k8s_backend import K8sJobsBackend

    backend = K8sJobsBackend(namespace="ns-test", image="ghcr.io/test:1.0")
    manifest = backend._render(
        task_name="z3rno.refine_run",
        payload={"org_id": "abc", "dataset_id": "def"},
    )
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/test:1.0"
    env_names = {e["name"] for e in container["env"]}
    assert {"Z3RNO_TASK_NAME", "Z3RNO_TASK_PAYLOAD", "Z3RNO_TASK_DISPATCH_TS"} <= env_names
    payload_entry = next(e for e in container["env"] if e["name"] == "Z3RNO_TASK_PAYLOAD")
    assert "abc" in payload_entry["value"]
    assert "def" in payload_entry["value"]
    # Job name is deterministic-ish.
    assert manifest["metadata"]["name"].startswith("z3rno-z3rno-refine-run-")
    assert manifest["metadata"]["labels"]["z3rno.task"] == "z3rno.refine_run"


def test_reset_backend_cache_picks_up_new_config() -> None:
    """Setting DISTRIBUTED_BACKEND and resetting must return a fresh
    backend on the next get_backend() call."""
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with("celery"),
    ):
        b1 = get_backend()
    reset_backend_cache()
    with patch(
        "z3rno_server.workers.backends.dispatch.get_settings",
        return_value=_settings_with("k8s_jobs"),
    ):
        b2 = get_backend()
    assert b1.name != b2.name
