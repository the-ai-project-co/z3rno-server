"""HTTP-contract tests for ``/v1/ingest/search`` (Phase B.2)."""

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
    session.connection = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    yield session


def _reload_app() -> object:
    import z3rno_server.main as main_module

    importlib.reload(main_module)
    return main_module.create_app()


@pytest.fixture
def app_off() -> TestClient:
    """Default — INGEST_ENABLED=false → no search route."""
    os.environ.pop("INGEST_ENABLED", None)
    os.environ.pop("TAVILY_API_KEY", None)
    return TestClient(_reload_app(), raise_server_exceptions=False)


@pytest.fixture
def app_ingest_only() -> Iterator[TestClient]:
    """INGEST_ENABLED=true but Tavily unset → search route still gone."""
    os.environ["INGEST_ENABLED"] = "true"
    os.environ.pop("TAVILY_API_KEY", None)
    app = _reload_app()
    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
    ) as m_rl:
        m_rl.return_value = (True, 999, 0)
        yield TestClient(app, raise_server_exceptions=False)
    os.environ.pop("INGEST_ENABLED", None)


@pytest.fixture
def app_on() -> Iterator[TestClient]:
    """Both flags on; Tavily client + Celery + DB session patched."""
    os.environ["INGEST_ENABLED"] = "true"
    os.environ["TAVILY_API_KEY"] = "tvly-fake"
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    app = _reload_app()
    from z3rno_server.dependencies import get_db

    app.dependency_overrides[get_db] = _fake_db_session

    fake_tavily = MagicMock()
    fake_tavily.search.return_value = {
        "results": [
            {"url": "https://a.com", "title": "A", "content": "snippet a"},
            {"url": "https://b.com", "title": "B", "content": "snippet b"},
        ]
    }

    with (
        patch("tavily.TavilyClient", return_value=fake_tavily),
        patch("z3rno_server.api.search.insert_ingest_job", new_callable=AsyncMock) as m_insert,
        patch("z3rno_server.api.search.ingest_run.apply_async") as m_dispatch,
        patch(
            "z3rno_server.middleware.rate_limit._check_rate_limit",
            new_callable=AsyncMock,
        ) as m_rl,
    ):
        m_insert.return_value = None
        m_dispatch.return_value = None
        m_rl.return_value = (True, 999, 0)
        yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.clear()
    os.environ.pop("INGEST_ENABLED", None)
    os.environ.pop("TAVILY_API_KEY", None)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer z3rno_sk_test_localdev"}


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


class TestSearchGating:
    def test_off_returns_404(self, app_off: TestClient) -> None:
        r = app_off.post(
            "/v1/ingest/search",
            json={"query": "x", "agent_id": str(uuid4())},
            headers=_auth(),
        )
        assert r.status_code == 404

    def test_ingest_on_tavily_off_unreachable(self, app_ingest_only: TestClient) -> None:
        # When the search router isn't registered, FastAPI either returns
        # 404 (no path match) or 405 (path matched to /v1/ingest/{job_id}
        # but with a wrong method). Both signal "this endpoint isn't here."
        r = app_ingest_only.post(
            "/v1/ingest/search",
            json={"query": "x", "agent_id": str(uuid4())},
            headers=_auth(),
        )
        assert r.status_code in {404, 405}

    def test_openapi_excludes_search_when_off(self, app_off: TestClient) -> None:
        spec = app_off.get("/openapi.json").json()
        for p in spec["paths"]:
            assert p != "/v1/ingest/search"


# ---------------------------------------------------------------------------
# Validation + happy path
# ---------------------------------------------------------------------------


class TestSearchEnabled:
    def test_openapi_includes_search(self, app_on: TestClient) -> None:
        spec = app_on.get("/openapi.json").json()
        assert "/v1/ingest/search" in spec["paths"]

    def test_happy_path_returns_jobs(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/ingest/search",
            json={
                "query": "z3rno smart memory",
                "agent_id": str(uuid4()),
                "max_results": 2,
            },
            headers=_auth(),
        )
        assert r.status_code == 202
        body = r.json()
        assert len(body["jobs"]) == 2
        assert body["jobs"][0]["url"] == "https://a.com"
        assert body["jobs"][0]["title"] == "A"
        assert "job_id" in body["jobs"][0]
        assert body["query"] == "z3rno smart memory"

    def test_empty_query_rejected(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/ingest/search",
            json={"query": "", "agent_id": str(uuid4())},
            headers=_auth(),
        )
        assert r.status_code == 422

    def test_max_results_above_cap_rejected(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/ingest/search",
            json={"query": "x", "agent_id": str(uuid4()), "max_results": 999},
            headers=_auth(),
        )
        assert r.status_code == 422

    def test_extra_fields_rejected(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/ingest/search",
            json={"query": "x", "agent_id": str(uuid4()), "rogue": "x"},
            headers=_auth(),
        )
        assert r.status_code == 422

    def test_unauthenticated_returns_401(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/ingest/search",
            json={"query": "x", "agent_id": str(uuid4())},
        )
        assert r.status_code == 401
