"""Phase G slice 5 — SSE recall stream contract tests.

Patches ``recall()`` so the handler exercises the queue + SSE
generator without actually hitting the DB.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


async def _fake_db_session() -> AsyncGenerator[MagicMock, None]:
    session = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=MagicMock())
    session.connection = AsyncMock(return_value=conn)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    yield session


def _fake_result() -> MagicMock:
    r = MagicMock()
    r.memory_id = uuid4()
    r.content = "hello"
    r.summary = None
    r.memory_type = "episodic"
    r.importance_score = 0.5
    r.relevance_score = 0.8
    r.recall_count = 0
    r.created_at = datetime.now(UTC)
    r.metadata = {}
    r.score_components = {}
    return r


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


def _parse_sse(body: str) -> list[dict[str, object]]:
    """Naive SSE parser: split on blank lines, pull event + data."""
    events: list[dict[str, object]] = []
    cur_event = "message"
    cur_data: list[str] = []
    for raw in body.splitlines():
        if not raw:
            if cur_data:
                try:
                    parsed = json.loads("\n".join(cur_data))
                except json.JSONDecodeError:
                    parsed = {"raw": "\n".join(cur_data)}
                events.append({"event": cur_event, "data": parsed})
            cur_event = "message"
            cur_data = []
            continue
        if raw.startswith("event:"):
            cur_event = raw.split(":", 1)[1].strip()
        elif raw.startswith("data:"):
            cur_data.append(raw.split(":", 1)[1].lstrip())
    return events


def test_openapi_lists_recall_stream(app: TestClient) -> None:
    spec = app.get("/openapi.json").json()
    assert "/v1/memories/recall/stream" in spec["paths"]


def test_recall_stream_emits_results_then_done_for_non_trace(app: TestClient) -> None:
    """Non-TRACE strategy: exactly one ``results`` event + ``done``."""
    fake_resp = MagicMock()
    fake_resp.results = [_fake_result()]
    fake_resp.total = 1
    fake_resp.strategy_used = "VECTOR"
    fake_resp.strategies_considered = ["VECTOR"]
    fake_resp.reranked = False

    with patch(
        "z3rno_server.api.memories_stream.recall",
        new_callable=AsyncMock,
        return_value=fake_resp,
    ):
        r = app.post(
            "/v1/memories/recall/stream",
            json={"agent_id": str(uuid4()), "query": "x", "strategy": "VECTOR"},
            headers=_auth(),
        )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    kinds = [e["event"] for e in events]
    assert "results" in kinds
    assert kinds[-1] == "done"
    results_event = next(e for e in events if e["event"] == "results")
    assert results_event["data"]["strategy_used"] == "VECTOR"  # type: ignore[index]
    assert results_event["data"]["total"] == 1  # type: ignore[index]


def test_recall_stream_emits_step_events_when_trace_callback_fires(
    app: TestClient,
) -> None:
    """Drive ``step_callback`` from the patched recall so the handler
    pushes a ``step`` event onto the SSE queue."""
    fake_resp = MagicMock()
    fake_resp.results = []
    fake_resp.total = 0
    fake_resp.strategy_used = "TRACE"
    fake_resp.strategies_considered = ["TRACE"]
    fake_resp.reranked = False

    async def _fake_recall(*args: object, **kwargs: object) -> MagicMock:
        cb = kwargs.get("step_callback")
        if cb is not None:
            await cb(0, "seed query", [_fake_result(), _fake_result()])
            await cb(1, "refined query", [_fake_result()])
        return fake_resp

    with patch(
        "z3rno_server.api.memories_stream.recall",
        side_effect=_fake_recall,
    ):
        r = app.post(
            "/v1/memories/recall/stream",
            json={"agent_id": str(uuid4()), "query": "x", "strategy": "TRACE"},
            headers=_auth(),
        )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    kinds = [e["event"] for e in events]
    # Should see: step (x2), results, done
    assert kinds.count("step") == 2
    assert kinds.count("results") == 1
    assert kinds[-1] == "done"
    # First event should be a step (i.e. arrives before the final results).
    assert kinds[0] == "step"


def test_recall_stream_unknown_strategy_emits_error_event(app: TestClient) -> None:
    from z3rno_core.retrieval import UnknownStrategyError

    with patch(
        "z3rno_server.api.memories_stream.recall",
        new_callable=AsyncMock,
        side_effect=UnknownStrategyError("ZZZ"),
    ):
        r = app.post(
            "/v1/memories/recall/stream",
            json={"agent_id": str(uuid4()), "strategy": "ZZZ"},
            headers=_auth(),
        )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    kinds = [e["event"] for e in events]
    assert "error" in kinds
    assert kinds[-1] == "done"


def test_recall_stream_requires_auth() -> None:
    """No fixture — just sanity-check the route exists and gates auth."""
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    import z3rno_server.main as main_module

    importlib.reload(main_module)
    fastapi_app = main_module.create_app()
    with TestClient(fastapi_app, raise_server_exceptions=False) as client:
        r = client.post(
            "/v1/memories/recall/stream",
            json={"agent_id": str(UUID(int=42))},
        )
    assert r.status_code in (401, 403)
