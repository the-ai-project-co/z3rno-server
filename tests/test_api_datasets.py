"""HTTP-contract tests for ``/v1/datasets`` (Phase B.1).

In-process FastAPI ``TestClient``; the DB layer and rate-limit Valkey
check are patched. Live-DB CRUD round-trip is covered in the core
integration suite (when run with ``DATABASE_URL`` set).
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
    os.environ.pop("INGEST_ENABLED", None)
    import z3rno_server.main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.create_app(), raise_server_exceptions=False)


@pytest.fixture
def app_on() -> Iterator[TestClient]:
    os.environ["INGEST_ENABLED"] = "true"
    os.environ.setdefault("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
    os.environ.setdefault("Z3RNO_DEV_ORG_ID", str(uuid4()))

    import z3rno_server.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()

    from z3rno_server.dependencies import get_db

    app.dependency_overrides[get_db] = _fake_db_session

    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
    ) as m_rl:
        m_rl.return_value = (True, 999, 0)
        yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.clear()
    os.environ.pop("INGEST_ENABLED", None)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer z3rno_sk_test_localdev"}


# ---------------------------------------------------------------------------
# Flag OFF
# ---------------------------------------------------------------------------


class TestDatasetsDisabled:
    def test_post_returns_404(self, app_off: TestClient) -> None:
        r = app_off.post(
            "/v1/datasets",
            json={"name": "test"},
            headers=_auth_headers(),
        )
        assert r.status_code == 404

    def test_get_list_returns_404(self, app_off: TestClient) -> None:
        r = app_off.get("/v1/datasets", headers=_auth_headers())
        assert r.status_code == 404

    def test_openapi_excludes_datasets(self, app_off: TestClient) -> None:
        spec = app_off.get("/openapi.json").json()
        for p in spec["paths"]:
            assert "/v1/datasets" not in p


# ---------------------------------------------------------------------------
# Flag ON
# ---------------------------------------------------------------------------


class TestDatasetsEnabled:
    def test_openapi_includes_datasets(self, app_on: TestClient) -> None:
        spec = app_on.get("/openapi.json").json()
        assert "/v1/datasets" in spec["paths"]
        assert "/v1/datasets/{dataset_id}" in spec["paths"]

    def test_post_validation_rejects_empty_name(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/datasets",
            json={"name": ""},
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_validation_rejects_extra_fields(self, app_on: TestClient) -> None:
        r = app_on.post(
            "/v1/datasets",
            json={"name": "ok", "rogue": "x"},
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_post_unauthenticated_returns_401(self, app_on: TestClient) -> None:
        r = app_on.post("/v1/datasets", json={"name": "x"})
        assert r.status_code == 401

    def test_get_unauthenticated_returns_401(self, app_on: TestClient) -> None:
        r = app_on.get("/v1/datasets")
        assert r.status_code == 401

    def test_get_list_with_pagination_params(self, app_on: TestClient) -> None:
        r = app_on.get(
            "/v1/datasets?limit=10&offset=0",
            headers=_auth_headers(),
        )
        # Empty mocked DB → empty list, total=0
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 10

    def test_get_list_rejects_bad_limit(self, app_on: TestClient) -> None:
        r = app_on.get(
            "/v1/datasets?limit=10000",
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_get_one_returns_404_when_missing(self, app_on: TestClient) -> None:
        r = app_on.get(f"/v1/datasets/{uuid4()}", headers=_auth_headers())
        assert r.status_code == 404
