"""Tests for AuthMiddleware — extraction, dev bypass, JWT auth, and rejection."""

from __future__ import annotations

import time

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.main import app

from .conftest import DEV_API_KEY, DEV_ORG_ID

# JWT test constants
JWT_SECRET = "test-jwt-secret-key-for-unit-tests"
JWT_ORG_ID = "00000000-0000-0000-0000-000000000099"
JWT_USER_ID = "user-123"
JWT_ROLE = "admin"


def _make_jwt(
    *,
    sub: str = JWT_USER_ID,
    org_id: str = JWT_ORG_ID,
    role: str = JWT_ROLE,
    secret: str = JWT_SECRET,
    algorithm: str = "HS256",
    exp_offset: int = 3600,
    extra: dict | None = None,
    omit: list[str] | None = None,
) -> str:
    """Create a JWT token for testing."""
    now = int(time.time())
    payload: dict = {
        "sub": sub,
        "org_id": org_id,
        "role": role,
        "exp": now + exp_offset,
        "iat": now,
    }
    if extra:
        payload.update(extra)
    if omit:
        for key in omit:
            payload.pop(key, None)
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set JWT_SECRET_KEY for all tests in this module."""
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    """Create a test client *without* auth headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Public paths bypass auth ---


async def test_health_bypasses_auth(client: AsyncClient) -> None:
    """/v1/health is public and should return 200 without credentials."""
    response = await client.get("/v1/health")
    assert response.status_code == 200


async def test_docs_bypasses_auth(client: AsyncClient) -> None:
    """/docs is public and should return 200 without credentials."""
    response = await client.get("/docs")
    assert response.status_code == 200


async def test_openapi_json_bypasses_auth(client: AsyncClient) -> None:
    """/openapi.json is public and should return 200 without credentials."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200


# --- Missing API key ---


async def test_missing_api_key_returns_401(client: AsyncClient) -> None:
    """Request without any API key header returns 401."""
    response = await client.get("/v1/memories")
    assert response.status_code == 401
    data = response.json()
    assert data["error"] == "unauthorized"


# --- Dev bypass with Bearer token ---


async def test_bearer_dev_key_passes_auth(client: AsyncClient) -> None:
    """Authorization: Bearer <dev_key> should authenticate via dev bypass."""
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": f"Bearer {DEV_API_KEY}"},
    )
    # Should NOT be 401 — dev key bypasses DB verification
    assert response.status_code != 401


async def test_bearer_prefix_only_returns_401(client: AsyncClient) -> None:
    """Authorization header with 'Bearer ' but empty key should return 401."""
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": "Bearer   "},
    )
    assert response.status_code == 401


# --- Dev bypass with X-API-Key header ---


async def test_x_api_key_dev_key_passes_auth(client: AsyncClient) -> None:
    """X-API-Key header with dev key should authenticate via dev bypass."""
    response = await client.get(
        "/v1/memories",
        headers={"X-API-Key": DEV_API_KEY},
    )
    assert response.status_code != 401


# --- Invalid / unknown keys ---


async def test_invalid_api_key_returns_401(client: AsyncClient) -> None:
    """A key that doesn't match the dev key and isn't in DB returns 401."""
    response = await client.get(
        "/v1/memories",
        headers={"X-API-Key": "definitely-not-a-valid-key"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"] == "unauthorized"
    assert "Invalid" in data["detail"]


async def test_empty_x_api_key_returns_401(client: AsyncClient) -> None:
    """X-API-Key header with empty string is treated as missing."""
    response = await client.get(
        "/v1/memories",
        headers={"X-API-Key": ""},
    )
    assert response.status_code == 401


# --- Auth result attaches org_id ---


async def test_dev_key_sets_org_id_on_request() -> None:
    """Dev bypass should attach the configured org_id to request.state."""
    from z3rno_server.middleware.auth import AuthResult, verify_api_key

    result = await verify_api_key(DEV_API_KEY)
    assert result is not None
    assert isinstance(result, AuthResult)
    assert str(result.org_id) == DEV_ORG_ID
    assert result.api_key_id is None  # Dev bypass doesn't set api_key_id


# --- JWT authentication ---


async def test_valid_jwt_passes_auth(client: AsyncClient) -> None:
    """A valid JWT with correct secret, claims, and non-expired should authenticate."""
    token = _make_jwt()
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Should NOT be 401 — valid JWT passes auth
    assert response.status_code != 401


async def test_expired_jwt_returns_401(client: AsyncClient) -> None:
    """An expired JWT should be rejected with 401."""
    token = _make_jwt(exp_offset=-3600)  # Expired 1 hour ago
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"] == "unauthorized"
    assert "JWT" in data["detail"]


async def test_invalid_jwt_signature_returns_401(client: AsyncClient) -> None:
    """A JWT signed with the wrong secret should be rejected with 401."""
    token = _make_jwt(secret="wrong-secret-key")
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"] == "unauthorized"
    assert "JWT" in data["detail"]


async def test_jwt_verify_returns_auth_result() -> None:
    """verify_jwt should return AuthResult with org_id, user_id, and role."""
    from uuid import UUID

    from z3rno_server.middleware.auth import verify_jwt

    token = _make_jwt()
    result = verify_jwt(token)
    assert result is not None
    assert result.org_id == UUID(JWT_ORG_ID)
    assert result.user_id == JWT_USER_ID
    assert result.role == JWT_ROLE
    assert result.api_key_id is None


async def test_jwt_missing_required_claims_returns_none() -> None:
    """A JWT missing required claims (e.g., org_id) should return None."""
    from z3rno_server.middleware.auth import verify_jwt

    token = _make_jwt(omit=["org_id"])
    result = verify_jwt(token)
    assert result is None


async def test_jwt_invalid_org_id_returns_none() -> None:
    """A JWT with a non-UUID org_id should return None."""
    from z3rno_server.middleware.auth import verify_jwt

    token = _make_jwt(org_id="not-a-uuid")
    result = verify_jwt(token)
    assert result is None


async def test_jwt_without_secret_key_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """When jwt_secret_key is empty, JWT auth should be rejected."""
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    from z3rno_server.middleware.auth import verify_jwt

    token = _make_jwt()
    result = verify_jwt(token)
    assert result is None
