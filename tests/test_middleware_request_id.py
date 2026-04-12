"""Tests for RequestIdMiddleware."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.main import app


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    """Create a test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_generates_request_id_when_not_provided(client: AsyncClient) -> None:
    """Response should contain X-Request-ID even if the request didn't send one."""
    response = await client.get("/v1/health")
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    # Should be a valid UUID
    uuid.UUID(request_id)


async def test_uses_provided_request_id(client: AsyncClient) -> None:
    """When X-Request-ID is sent, the same value should appear in the response."""
    custom_id = "custom-req-id-12345"
    response = await client.get(
        "/v1/health",
        headers={"X-Request-ID": custom_id},
    )
    assert response.headers.get("X-Request-ID") == custom_id


async def test_request_id_in_response_headers(client: AsyncClient) -> None:
    """X-Request-ID must always be present in response headers."""
    response = await client.get("/v1/health")
    assert "X-Request-ID" in response.headers
