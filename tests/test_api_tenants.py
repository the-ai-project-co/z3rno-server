"""v0.20.3 — /v1/tenants/me/budgets contract tests."""

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
    # By default no override row exists → empty.
    fake_result = MagicMock()
    fake_result.fetchone = MagicMock(return_value=None)
    fake_result.fetchall = MagicMock(return_value=[])
    conn.execute = AsyncMock(return_value=fake_result)
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

    from z3rno_server.dependencies import get_db, get_read_db

    fastapi_app.dependency_overrides[get_db] = _fake_db_session
    fastapi_app.dependency_overrides[get_read_db] = _fake_db_session

    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
    ) as m_rl:
        m_rl.return_value = (True, 999, 0)
        yield TestClient(fastapi_app, raise_server_exceptions=True)

    fastapi_app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer z3rno_sk_test_localdev"}


def test_openapi_lists_budget_routes(app: TestClient) -> None:
    spec = app.get("/openapi.json").json()
    assert "/v1/tenants/me/budgets" in spec["paths"]
    methods = spec["paths"]["/v1/tenants/me/budgets"]
    assert "get" in methods
    assert "put" in methods


def test_get_returns_empty_overrides_when_unset(app: TestClient) -> None:
    r = app.get("/v1/tenants/me/budgets", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overrides"]["daily_tokens"] == 0
    assert body["overrides"]["monthly_tokens"] == 0
    # Effective also defaults to zero (server defaults are 0 in test env).
    assert body["effective"]["daily_tokens"] == 0


def test_put_validates_non_negative(app: TestClient) -> None:
    r = app.put(
        "/v1/tenants/me/budgets",
        json={"daily_tokens": -1},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_put_rejects_extra_fields(app: TestClient) -> None:
    r = app.put(
        "/v1/tenants/me/budgets",
        json={"daily_tokens": 1000, "rogue": "field"},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_put_round_trips_overrides(app: TestClient) -> None:
    """PUT returns the just-set overrides + the resolved effective."""
    # Patch resolve_budgets to return what the new override would yield.
    from z3rno_core.usage import Budgets

    async def _fake_resolve(_conn, *, org_id, defaults):
        return Budgets(daily_tokens=5000, monthly_tokens=100_000)

    with patch("z3rno_server.api.tenants.resolve_budgets", side_effect=_fake_resolve):
        r = app.put(
            "/v1/tenants/me/budgets",
            json={"daily_tokens": 5000, "monthly_tokens": 100_000},
            headers=_auth(),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overrides"]["daily_tokens"] == 5000
    assert body["overrides"]["monthly_tokens"] == 100_000
    assert body["effective"]["daily_tokens"] == 5000


def test_get_unauthenticated_returns_401(app: TestClient) -> None:
    r = app.get("/v1/tenants/me/budgets")
    assert r.status_code == 401


def test_put_unauthenticated_returns_401(app: TestClient) -> None:
    r = app.put("/v1/tenants/me/budgets", json={})
    assert r.status_code == 401
