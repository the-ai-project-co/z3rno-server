"""Test health endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.main import app


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    """Create a test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient) -> None:
    """Health endpoint returns 200."""
    response = await client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


async def test_ready(client: AsyncClient) -> None:
    """Ready endpoint returns 200."""
    response = await client.get("/v1/ready")
    assert response.status_code == 200
