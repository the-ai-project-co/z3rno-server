"""Tests for memory API endpoints (stub responses without DB)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.main import app


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    """Create a test client with auth header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as c:
        yield c


async def test_store_returns_422_for_empty_body(client: AsyncClient) -> None:
    """POST /v1/memories with empty body should return 422."""
    response = await client.post("/v1/memories", json={})
    assert response.status_code == 422


async def test_store_validates_memory_type(client: AsyncClient) -> None:
    """POST /v1/memories with invalid memory_type should return 422."""
    response = await client.post(
        "/v1/memories",
        json={
            "agent_id": str(uuid4()),
            "content": "test",
            "memory_type": "invalid_type",
        },
    )
    assert response.status_code == 422


async def test_recall_returns_422_for_empty_body(client: AsyncClient) -> None:
    """POST /v1/memories/recall with empty body should return 422."""
    response = await client.post("/v1/memories/recall", json={})
    assert response.status_code == 422


async def test_forget_returns_422_for_empty_body(client: AsyncClient) -> None:
    """POST /v1/memories/forget with empty body should return 422."""
    response = await client.post("/v1/memories/forget", json={})
    assert response.status_code == 422


async def test_unauthenticated_request_returns_401() -> None:
    """Request without API key should return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/memories", json={"content": "test"})
        assert response.status_code == 401
