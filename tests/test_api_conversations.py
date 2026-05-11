"""HTTP-contract tests for ``/v1/conversations`` (Phase G slice 2).

In-process FastAPI TestClient; ``get_db`` + ``get_read_db`` are
mocked. The conversation table behaviour is covered separately by
the core unit tests + the testcontainer integration suite.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


def _fake_conv_row(
    *,
    cid: UUID,
    org: UUID,
    agent: UUID,
    turn_count: int = 0,
    last_summary_turn: int = 0,
    cadence: int = 10,
) -> MagicMock:
    now = datetime.now(UTC)
    row = MagicMock()
    values = [
        cid,
        org,
        agent,
        None,
        None,
        cadence,
        turn_count,
        last_summary_turn,
        {},
        now,
        now,
        None,
    ]
    row.__getitem__ = lambda self, i: values[i]  # type: ignore[misc]
    return row


def _fake_db_factory(conv_row: MagicMock | None = None) -> object:
    """Return a fake ``get_db`` that yields a session with a connection
    whose ``execute`` returns sensible defaults for every endpoint
    under test."""

    async def _gen() -> AsyncGenerator[MagicMock, None]:
        session = MagicMock()
        conn = MagicMock()

        async def _exec(*_a: object, **_k: object) -> MagicMock:
            r = MagicMock()
            r.fetchone = MagicMock(return_value=conv_row)
            r.fetchall = MagicMock(return_value=[])
            r.scalar = MagicMock(return_value=0)
            r.rowcount = 0
            return r

        conn.execute = AsyncMock(side_effect=_exec)
        session.connection = AsyncMock(return_value=conn)
        session.commit = AsyncMock(return_value=None)
        session.rollback = AsyncMock(return_value=None)
        yield session

    return _gen


@pytest.fixture
def app(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    import z3rno_server.main as main_module

    importlib.reload(main_module)
    fastapi_app = main_module.create_app()

    # By default, every fetchone returns a conv row so 404 paths can
    # be tested by overriding inside the individual test.
    cid = UUID(int=42)
    org = UUID(int=7)
    agent = UUID(int=8)
    row = _fake_conv_row(cid=cid, org=org, agent=agent, turn_count=0)
    fake = _fake_db_factory(conv_row=row)

    from z3rno_server.dependencies import get_db, get_read_db

    fastapi_app.dependency_overrides[get_db] = fake
    fastapi_app.dependency_overrides[get_read_db] = fake

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
# OpenAPI surface
# ---------------------------------------------------------------------------


def test_openapi_lists_conversation_routes(app: TestClient) -> None:
    spec = app.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/v1/conversations" in paths
    assert "/v1/conversations/{conversation_id}" in paths
    assert "/v1/conversations/{conversation_id}/turns" in paths


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_post_conversation_rejects_extra_fields(app: TestClient) -> None:
    r = app.post(
        "/v1/conversations",
        json={"agent_id": str(uuid4()), "rogue": "field"},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_post_conversation_requires_agent_id(app: TestClient) -> None:
    r = app.post("/v1/conversations", json={}, headers=_auth())
    assert r.status_code == 422


def test_post_conversation_rejects_zero_cadence(app: TestClient) -> None:
    r = app.post(
        "/v1/conversations",
        json={"agent_id": str(uuid4()), "summary_cadence": 0},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_post_turn_rejects_unknown_role(app: TestClient) -> None:
    r = app.post(
        f"/v1/conversations/{uuid4()}/turns",
        json={"memory_id": str(uuid4()), "turn_role": "robot"},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_get_unauthenticated_returns_401(app: TestClient) -> None:
    r = app.get(f"/v1/conversations/{uuid4()}")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy paths (mocked DB returns success)
# ---------------------------------------------------------------------------


def test_post_conversation_returns_metadata(app: TestClient) -> None:
    agent = uuid4()
    r = app.post(
        "/v1/conversations",
        json={"agent_id": str(agent), "title": "demo", "summary_cadence": 5},
        headers=_auth(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The mocked SELECT returns a fixed row — assert the response shape.
    assert "id" in body
    assert "summary_cadence" in body
    assert "turn_count" in body
    assert body["turn_count"] == 0


def test_get_conversation_returns_metadata(app: TestClient) -> None:
    r = app.get(f"/v1/conversations/{UUID(int=42)}", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(UUID(int=42))


def test_get_turns_returns_empty_list_when_no_rows(app: TestClient) -> None:
    r = app.get(f"/v1/conversations/{UUID(int=42)}/turns", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["turns"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# Recall request gains the conversation_id field
# ---------------------------------------------------------------------------


def test_recall_request_accepts_conversation_id() -> None:
    from z3rno_server.schemas.memories import RecallRequest

    body = RecallRequest(
        agent_id=uuid4(),
        query="what did I say earlier",
        conversation_id=uuid4(),
    )
    assert body.conversation_id is not None
