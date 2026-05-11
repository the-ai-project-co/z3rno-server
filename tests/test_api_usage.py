"""Phase G slice 6 — GET /v1/usage contract tests."""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncGenerator, Iterator
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


async def _fake_db_session() -> AsyncGenerator[MagicMock, None]:
    session = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: []))
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


def test_openapi_lists_usage_route(app: TestClient) -> None:
    spec = app.get("/openapi.json").json()
    assert "/v1/usage" in spec["paths"]


def test_get_usage_returns_zeroes_when_no_counters(app: TestClient) -> None:
    r = app.get("/v1/usage", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["daily"]["tokens"] == 0
    assert body["monthly"]["tokens"] == 0
    assert body["daily"]["llm_calls"] == 0


def test_get_usage_aggregates_from_counters(app: TestClient) -> None:
    """Patch get_usage so we don't depend on the mocked SQL rowset
    shape — the endpoint just relays the helper."""
    from z3rno_core.usage.counters import UsageWindow

    fake_today = date(2026, 5, 15)
    daily_window = UsageWindow(
        org_id=UUID(int=1),
        since=fake_today,
        until=fake_today,
        tokens=42,
        llm_calls=3,
    )
    monthly_window = UsageWindow(
        org_id=UUID(int=1),
        since=fake_today.replace(day=1),
        until=fake_today,
        tokens=1500,
        llm_calls=20,
        embeddings=7,
        by_day={fake_today: {"tokens": 42, "llm_calls": 3}},
    )

    async def _fake_get_usage(*_args: object, since: date, **_kwargs: object) -> object:
        return daily_window if since == fake_today else monthly_window

    with patch("z3rno_server.api.usage.get_usage", side_effect=_fake_get_usage):
        r = app.get(f"/v1/usage?on={fake_today.isoformat()}", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["daily"]["tokens"] == 42
    assert body["monthly"]["tokens"] == 1500
    assert body["monthly"]["embeddings"] == 7
    assert "2026-05-15" in body["by_day"]


def test_get_usage_requires_auth(app: TestClient) -> None:
    r = app.get("/v1/usage")
    assert r.status_code in (401, 403)
