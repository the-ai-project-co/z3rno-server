"""v0.22.1 — /v1/tenants/{org_id}/budgets contract tests (slice 21.3)."""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncGenerator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

SA_KEY = "z3rno_sk_test_superadmin"
TENANT_KEY = "z3rno_sk_test_localdev"
TARGET_ORG = uuid4()


def _make_db_session(*, org_exists: bool = True):  # type: ignore[no-untyped-def]
    """Fake session whose first SELECT returns a hit (org-exists probe)
    and whose second returns the overrides row."""

    async def _gen() -> AsyncGenerator[MagicMock, None]:
        session = MagicMock()
        conn = MagicMock()
        results: list[MagicMock] = []
        # 1) SET LOCAL — no return; 2) exists probe; 3) SELECT overrides.
        existence = MagicMock()
        existence.fetchone = MagicMock(return_value=(1,) if org_exists else None)
        overrides_row = MagicMock()
        overrides_row.fetchone = MagicMock(return_value=None)
        # set_local + exists + select_overrides + (for PUT: + update)
        results = [MagicMock(), existence, overrides_row, MagicMock()]
        conn.execute = AsyncMock(side_effect=results)
        session.connection = AsyncMock(return_value=conn)
        session.commit = AsyncMock(return_value=None)
        session.rollback = AsyncMock(return_value=None)
        yield session

    return _gen


@pytest.fixture
def app_with_admin() -> Iterator[TestClient]:
    os.environ["Z3RNO_API_KEY"] = TENANT_KEY
    os.environ["Z3RNO_DEV_ORG_ID"] = str(uuid4())
    os.environ["SUPERADMIN_ENABLED"] = "true"
    os.environ["SUPERADMIN_API_KEY"] = SA_KEY

    import z3rno_server.main as main_module
    importlib.reload(main_module)
    fastapi_app = main_module.create_app()

    from z3rno_server.dependencies import get_db, get_read_db
    fastapi_app.dependency_overrides[get_db] = _make_db_session()
    fastapi_app.dependency_overrides[get_read_db] = _make_db_session()

    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
    ) as m_rl:
        m_rl.return_value = (True, 999, 0)
        yield TestClient(fastapi_app, raise_server_exceptions=True)

    fastapi_app.dependency_overrides.clear()
    for k in ("SUPERADMIN_ENABLED", "SUPERADMIN_API_KEY"):
        os.environ.pop(k, None)


@pytest.fixture
def app_without_admin() -> Iterator[TestClient]:
    """Flag off — admin routes must not register."""
    os.environ["Z3RNO_API_KEY"] = TENANT_KEY
    os.environ["Z3RNO_DEV_ORG_ID"] = str(uuid4())
    os.environ.pop("SUPERADMIN_ENABLED", None)
    os.environ.pop("SUPERADMIN_API_KEY", None)

    import z3rno_server.main as main_module
    importlib.reload(main_module)
    fastapi_app = main_module.create_app()

    return TestClient(fastapi_app, raise_server_exceptions=True)


def _sa_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {SA_KEY}"}


def _tenant_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TENANT_KEY}"}


# ---------------------------------------------------------------------------
# Registration gating
# ---------------------------------------------------------------------------


def test_routes_unregistered_when_flag_off(app_without_admin: TestClient) -> None:
    spec = app_without_admin.get("/openapi.json").json()
    assert "/v1/tenants/{org_id}/budgets" not in spec["paths"]


def test_routes_registered_when_flag_on(app_with_admin: TestClient) -> None:
    spec = app_with_admin.get("/openapi.json").json()
    assert "/v1/tenants/{org_id}/budgets" in spec["paths"]


# ---------------------------------------------------------------------------
# AuthN / AuthZ
# ---------------------------------------------------------------------------


def test_get_401_without_auth(app_with_admin: TestClient) -> None:
    r = app_with_admin.get(f"/v1/tenants/{TARGET_ORG}/budgets")
    assert r.status_code == 401


def test_get_403_with_tenant_key(app_with_admin: TestClient) -> None:
    """A regular API key has role=None — backward-compat slips through
    require_role but require_superadmin rejects it."""
    r = app_with_admin.get(
        f"/v1/tenants/{TARGET_ORG}/budgets", headers=_tenant_auth()
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_returns_zeros_when_no_overrides(app_with_admin: TestClient) -> None:
    r = app_with_admin.get(
        f"/v1/tenants/{TARGET_ORG}/budgets", headers=_sa_auth()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overrides"]["daily_tokens"] == 0


def test_put_round_trips(app_with_admin: TestClient) -> None:
    r = app_with_admin.put(
        f"/v1/tenants/{TARGET_ORG}/budgets",
        json={"daily_tokens": 50000, "monthly_tokens": 1000000},
        headers=_sa_auth(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overrides"]["daily_tokens"] == 50000
    assert body["overrides"]["monthly_tokens"] == 1000000


def test_put_rejects_negative(app_with_admin: TestClient) -> None:
    r = app_with_admin.put(
        f"/v1/tenants/{TARGET_ORG}/budgets",
        json={"daily_tokens": -1},
        headers=_sa_auth(),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Unknown org
# ---------------------------------------------------------------------------


def test_get_404_when_org_missing(app_with_admin: TestClient) -> None:
    """Override get_read_db with a session that reports org_exists=False."""
    from z3rno_server.dependencies import get_read_db
    app_with_admin.app.dependency_overrides[get_read_db] = _make_db_session(
        org_exists=False
    )
    r = app_with_admin.get(
        f"/v1/tenants/{uuid4()}/budgets", headers=_sa_auth()
    )
    assert r.status_code == 404, r.text
