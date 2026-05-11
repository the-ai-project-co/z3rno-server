"""Integration tests for the /v1/distill API surface (Phase A).

These exercise the FastAPI request path **without** a running Celery
worker — the Celery task's ``apply_async`` is patched so we verify the
HTTP contract (status codes, validation, OpenAPI exposure) and the
enqueue intent, not the worker's behavior. Worker behavior is covered
by ``test_distill_integration.py`` in z3rno-core.

Two scenarios:
  * ``DISTILL_ENABLED=false`` (default) — no /v1/distill route is
    registered; both endpoints return 404.
  * ``DISTILL_ENABLED=true``            — endpoints accept requests,
    insert distill_jobs rows, and dispatch the Celery task.

The DB layer is not required for the "off" scenario. The "on" scenario
mocks the ``insert_distill_job`` helper so we don't need a live DB.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncGenerator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


async def _fake_db_session() -> AsyncGenerator[MagicMock, None]:
    """Yield a stand-in AsyncSession that records calls but never connects."""
    session = MagicMock()
    session.connection = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    yield session


@pytest.fixture
def app_off() -> TestClient:
    """App built with DISTILL_ENABLED=false (default)."""
    os.environ.pop("DISTILL_ENABLED", None)
    import z3rno_server.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def app_on() -> Iterator[TestClient]:
    """App built with DISTILL_ENABLED=true.

    Patches the DB insert and Celery dispatch so the route can be
    exercised without a live Postgres or worker.
    """
    os.environ["DISTILL_ENABLED"] = "true"
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    import z3rno_server.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()

    # Override the DB session dependency so the route never opens a real
    # connection. Validation (422) fires before the handler body runs anyway,
    # but the dependency itself opens a connection eagerly — so we replace it.
    from z3rno_server.dependencies import get_db

    app.dependency_overrides[get_db] = _fake_db_session

    from z3rno_server.dependencies import get_read_db as _grd_for_override

    app.dependency_overrides[_grd_for_override] = _fake_db_session

    # Patch insert_distill_job (called from the API handler), Celery dispatch,
    # and the rate-limit Valkey check (no Redis available in test env).
    with (
        patch("z3rno_server.api.distill.insert_distill_job", new_callable=AsyncMock) as mock_insert,
        patch("z3rno_server.api.distill.forge_distill.apply_async") as mock_dispatch,
        patch(
            "z3rno_server.middleware.rate_limit._check_rate_limit",
            new_callable=AsyncMock,
        ) as mock_rl,
    ):
        mock_insert.return_value = None
        mock_dispatch.return_value = None
        mock_rl.return_value = (True, 999, 0)  # (allowed, remaining, reset_at)
        yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.clear()

    os.environ.pop("DISTILL_ENABLED", None)


def _auth_headers() -> dict[str, str]:
    """API-key bypass headers for the dev tier (matches existing test pattern)."""
    return {"Authorization": "Bearer z3rno_sk_test_localdev"}


# ---------------------------------------------------------------------------
# Flag OFF — endpoints not registered
# ---------------------------------------------------------------------------


class TestDistillDisabled:
    def test_post_distill_returns_404(self, app_off: TestClient) -> None:
        r = app_off.post(
            "/v1/distill",
            json={
                "agent_id": str(uuid4()),
                "memory_ids": [str(uuid4())],
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 404

    def test_get_status_returns_404(self, app_off: TestClient) -> None:
        r = app_off.get(
            f"/v1/distill/{uuid4()}",
            headers=_auth_headers(),
        )
        assert r.status_code == 404

    def test_openapi_spec_excludes_distill(self, app_off: TestClient) -> None:
        r = app_off.get("/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        assert "/v1/distill" not in spec["paths"]


# ---------------------------------------------------------------------------
# Flag ON — endpoints registered (DB + Celery patched)
# ---------------------------------------------------------------------------


class TestDistillEnabled:
    def test_openapi_spec_includes_distill(self, app_on: TestClient) -> None:
        r = app_on.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        assert "/v1/distill" in paths
        assert "/v1/distill/{job_id}" in paths

    def test_post_validation_rejects_empty_memory_ids(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/distill",
            json={"agent_id": str(uuid4()), "memory_ids": []},
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_validation_rejects_overlap_ge_chunk_size(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/distill",
            json={
                "agent_id": str(uuid4()),
                "memory_ids": [str(uuid4())],
                "chunk_size": 128,
                "chunk_overlap": 200,
            },
            headers=_auth_headers(),
        )
        assert r.status_code in {400, 422}

    def test_post_validation_rejects_extra_fields(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/distill",
            json={
                "agent_id": str(uuid4()),
                "memory_ids": [str(uuid4())],
                "rogue_field": "should be rejected",
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_unauthenticated_returns_401(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/distill",
            json={"agent_id": str(uuid4()), "memory_ids": [str(uuid4())]},
        )
        assert r.status_code == 401
