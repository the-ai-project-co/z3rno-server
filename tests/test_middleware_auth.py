"""Tests for AuthMiddleware."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.main import app


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


# --- Bearer token extraction ---


async def test_bearer_token_extraction(client: AsyncClient) -> None:
    """Authorization: Bearer <key> should authenticate successfully."""
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": "Bearer my-secret-key"},
    )
    # Should NOT be 401; the request passes auth and hits the actual route
    assert response.status_code != 401


async def test_bearer_prefix_only_returns_401(client: AsyncClient) -> None:
    """Authorization header with 'Bearer ' but empty key should return 401."""
    response = await client.get(
        "/v1/memories",
        headers={"Authorization": "Bearer   "},
    )
    assert response.status_code == 401


# --- X-API-Key header extraction ---


async def test_x_api_key_header_extraction(client: AsyncClient) -> None:
    """X-API-Key header should authenticate successfully."""
    response = await client.get(
        "/v1/memories",
        headers={"X-API-Key": "test-key-value"},
    )
    assert response.status_code != 401


# --- Empty API key ---


async def test_empty_x_api_key_returns_401(client: AsyncClient) -> None:
    """X-API-Key header with empty string is treated as missing by starlette."""
    response = await client.get(
        "/v1/memories",
        headers={"X-API-Key": ""},
    )
    # Empty string header is still sent but evaluates falsy in _extract_api_key
    assert response.status_code == 401
