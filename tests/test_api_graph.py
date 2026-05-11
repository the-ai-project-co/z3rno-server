"""HTTP-contract tests for ``GET /v1/graph/data`` (Phase E slice 5).

In-process FastAPI TestClient with patched DB + rate limit. Live RLS
isolation is covered when the integration suite runs against a real
postgres; this file pins the request/response contract.
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
def app() -> Iterator[TestClient]:
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    import z3rno_server.main as main_module

    importlib.reload(main_module)
    fastapi_app = main_module.create_app()

    from z3rno_server.dependencies import get_db

    fastapi_app.dependency_overrides[get_db] = _fake_db_session

    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
    ) as m_rl:
        m_rl.return_value = (True, 999, 0)
        yield TestClient(fastapi_app, raise_server_exceptions=True)

    fastapi_app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer z3rno_sk_test_localdev"}


# ---------------------------------------------------------------------------
# Always-registered: route should be in the OpenAPI spec.
# ---------------------------------------------------------------------------


def test_openapi_exposes_graph_data(app: TestClient) -> None:
    spec = app.get("/openapi.json").json()
    assert "/v1/graph/data" in spec["paths"]
    assert "get" in spec["paths"]["/v1/graph/data"]


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class TestGraphDataContract:
    def test_requires_dataset_or_agent(self, app: TestClient) -> None:
        r = app.get("/v1/graph/data", headers=_auth())
        assert r.status_code == 400
        assert "dataset_id" in r.json()["detail"]

    def test_accepts_dataset_id_only(self, app: TestClient) -> None:
        r = app.get(
            "/v1/graph/data",
            params={"dataset_id": str(uuid4())},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["nodes"] == []
        assert body["edges"] == []
        assert body["truncated"] is False
        assert body["scope"]["dataset_id"] is not None
        assert body["scope"]["agent_id"] is None

    def test_accepts_agent_id_only(self, app: TestClient) -> None:
        r = app.get(
            "/v1/graph/data",
            params={"agent_id": str(uuid4())},
            headers=_auth(),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["scope"]["agent_id"] is not None

    def test_accepts_memo_type_filter(self, app: TestClient) -> None:
        r = app.get(
            "/v1/graph/data",
            params={"dataset_id": str(uuid4()), "memo_type": "PERSON"},
            headers=_auth(),
        )
        assert r.status_code == 200
        assert r.json()["scope"]["memo_type"] == "PERSON"

    def test_rejects_oversize_limit(self, app: TestClient) -> None:
        r = app.get(
            "/v1/graph/data",
            params={"dataset_id": str(uuid4()), "limit": 999_999},
            headers=_auth(),
        )
        assert r.status_code == 422

    def test_requires_auth(self, app: TestClient) -> None:
        r = app.get("/v1/graph/data", params={"agent_id": str(uuid4())})
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Edge composition: confirm the second SQL call only fires when nodes exist.
# ---------------------------------------------------------------------------


def test_no_edge_query_when_node_set_is_empty(app: TestClient) -> None:
    """If the node query returns [], we skip the edge round-trip — verified
    by patching the session.connection() call_count."""

    async def _instrumented_db() -> AsyncGenerator[MagicMock, None]:
        session = MagicMock()
        conn = MagicMock()
        conn.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: []))
        session.connection = AsyncMock(return_value=conn)
        yield session
        # On teardown: only the node query should have run.
        assert conn.execute.await_count == 1

    from z3rno_server.dependencies import get_db

    app.app.dependency_overrides[get_db] = _instrumented_db
    try:
        r = app.get(
            "/v1/graph/data",
            params={"dataset_id": str(uuid4())},
            headers=_auth(),
        )
        assert r.status_code == 200
    finally:
        # Restore the original fixture override.
        app.app.dependency_overrides[get_db] = _fake_db_session
