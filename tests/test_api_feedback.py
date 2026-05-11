"""HTTP-contract tests for ``/v1/feedback`` (Phase D slice 2).

In-process FastAPI TestClient; DB session and rate-limit Valkey are
patched. Migration 023's CHECK constraints + the live-DB RLS isolation
are covered by ``z3rno-core/tests/test_phase_d_schema_integration.py``.
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
    session = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(
        return_value=MagicMock(
            fetchone=lambda: None, fetchall=lambda: [], scalar=lambda: 0, rowcount=0
        )
    )
    session.connection = AsyncMock(return_value=conn)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    yield session


@pytest.fixture
def app_off() -> TestClient:
    os.environ.pop("REFINE_ENABLED", None)
    import z3rno_server.main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.create_app(), raise_server_exceptions=False)


@pytest.fixture
def app_on() -> Iterator[TestClient]:
    os.environ["REFINE_ENABLED"] = "true"
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    import z3rno_server.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()

    from z3rno_server.dependencies import get_db

    app.dependency_overrides[get_db] = _fake_db_session

    from z3rno_server.dependencies import get_read_db as _grd_for_override

    app.dependency_overrides[_grd_for_override] = _fake_db_session

    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
    ) as m_rl:
        m_rl.return_value = (True, 999, 0)
        yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.clear()
    os.environ.pop("REFINE_ENABLED", None)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer z3rno_sk_test_localdev"}


# ---------------------------------------------------------------------------
# Flag OFF
# ---------------------------------------------------------------------------


class TestFeedbackDisabled:
    def test_post_returns_404(self, app_off: TestClient) -> None:
        r = app_off.post(
            "/v1/feedback",
            json={"agent_id": str(uuid4()), "memory_id": str(uuid4()), "signal": 1},
            headers=_auth_headers(),
        )
        assert r.status_code == 404

    def test_openapi_excludes_feedback(self, app_off: TestClient) -> None:
        spec = app_off.get("/openapi.json").json()
        for p in spec["paths"]:
            assert "/v1/feedback" not in p


# ---------------------------------------------------------------------------
# Flag ON
# ---------------------------------------------------------------------------


class TestFeedbackEnabled:
    def test_openapi_includes_feedback(self, app_on: TestClient) -> None:
        spec = app_on.get("/openapi.json").json()
        assert "/v1/feedback" in spec["paths"]

    def test_post_happy_path_returns_201(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={
                "agent_id": str(uuid4()),
                "memory_id": str(uuid4()),
                "signal": 1,
                "reason": "helpful",
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["signal"] == 1
        assert body["memory_id"] is not None
        assert body["edge_id"] is None

    def test_post_with_edge_id_returns_201(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={
                "agent_id": str(uuid4()),
                "edge_id": "e:works_for:42",
                "signal": -1,
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["edge_id"] == "e:works_for:42"
        assert body["memory_id"] is None

    def test_post_rejects_no_target(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={"agent_id": str(uuid4()), "signal": 1},
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_rejects_both_targets(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={
                "agent_id": str(uuid4()),
                "memory_id": str(uuid4()),
                "edge_id": "e:1",
                "signal": 1,
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_rejects_signal_out_of_range(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={
                "agent_id": str(uuid4()),
                "memory_id": str(uuid4()),
                "signal": 2,
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_rejects_extra_fields(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={
                "agent_id": str(uuid4()),
                "memory_id": str(uuid4()),
                "signal": 1,
                "rogue": "x",
            },
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_requires_auth(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/feedback",
            json={"agent_id": str(uuid4()), "memory_id": str(uuid4()), "signal": 1},
        )
        assert r.status_code in (401, 403)
