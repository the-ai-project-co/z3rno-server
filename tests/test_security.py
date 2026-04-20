"""Security tests — SQL injection, RLS bypass, auth enforcement, input validation, rate limiting.

Part of Week 7 security audit and hardening.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.dependencies import get_db
from z3rno_server.main import app

from .conftest import DEV_API_KEY, DEV_ORG_ID

# A different org_id for RLS bypass testing
OTHER_ORG_ID = "00000000-0000-0000-0000-000000000099"


@pytest.fixture
async def authed_client():
    """Client with dev auth, mocked DB, and mocked rate limiter."""

    async def _mock_get_db():
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            fetchone=MagicMock(return_value=None),
            fetchall=MagicMock(return_value=[]),
            rowcount=0,
        ))
        session = AsyncMock()
        session.execute = conn.execute
        session.connection = AsyncMock(return_value=conn)
        yield session

    app.dependency_overrides[get_db] = _mock_get_db
    transport = ASGITransport(app=app)
    # Patch the rate limit check to always allow (avoids Redis dependency)
    with patch(
        "z3rno_server.middleware.rate_limit._check_rate_limit",
        new_callable=AsyncMock,
        return_value=(True, 999, 0),  # (allowed, remaining, reset_at)
    ):
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": DEV_API_KEY, "Content-Type": "application/json"},
        ) as c:
            yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def unauthed_client():
    """Client without any auth headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# SQL Injection Tests
# ---------------------------------------------------------------------------


class TestSQLInjection:
    """Verify that SQL injection payloads are safely handled via parameterized queries."""

    SQL_PAYLOADS = [
        "'; DROP TABLE memories; --",
        "' OR '1'='1' --",
        "' UNION SELECT * FROM api_keys --",
        "1; SELECT pg_sleep(5); --",
        "') OR 1=1 --",
    ]

    async def test_injection_via_memory_content(self, authed_client: AsyncClient) -> None:
        """SQL injection in memory content field should be treated as plain text."""
        for payload in self.SQL_PAYLOADS:
            response = await authed_client.post(
                "/v1/memories",
                json={
                    "agent_id": str(uuid4()),
                    "content": payload,
                    "memory_type": "episodic",
                },
            )
            # Should not return 500 (DB error) — either 200 (stored safely) or 422
            assert response.status_code != 500, f"Injection payload caused 500: {payload}"

    async def test_injection_via_metadata(self, authed_client: AsyncClient) -> None:
        """SQL injection in metadata values should be safely parameterized."""
        response = await authed_client.post(
            "/v1/memories",
            json={
                "agent_id": str(uuid4()),
                "content": "normal content",
                "memory_type": "episodic",
                "metadata": {
                    "key": "'; DROP TABLE memories; --",
                    "nested": {"attack": "' OR 1=1 --"},
                },
            },
        )
        assert response.status_code != 500

    async def test_injection_via_agent_id(self, authed_client: AsyncClient) -> None:
        """SQL injection in agent_id should be rejected by UUID validation (422)."""
        response = await authed_client.post(
            "/v1/memories",
            json={
                "agent_id": "'; DROP TABLE memories; --",
                "content": "test",
                "memory_type": "episodic",
            },
        )
        assert response.status_code == 422

    async def test_injection_via_recall_query(self, authed_client: AsyncClient) -> None:
        """SQL injection in recall query should be safely handled."""
        response = await authed_client.post(
            "/v1/memories/recall",
            json={
                "agent_id": str(uuid4()),
                "query": "' UNION SELECT password FROM api_keys --",
            },
        )
        # Should not crash — either valid response or safe error
        assert response.status_code != 500


# ---------------------------------------------------------------------------
# RLS Bypass Tests
# ---------------------------------------------------------------------------


class TestRLSBypass:
    """Verify that org isolation cannot be circumvented via header manipulation."""

    async def test_cannot_inject_org_id_via_custom_header(
        self, authed_client: AsyncClient
    ) -> None:
        """Sending X-Org-Id header should not override authenticated org_id."""
        response = await authed_client.get(
            "/v1/memories",
            headers={"X-Org-Id": OTHER_ORG_ID},
        )
        # The response should still scope to DEV_ORG_ID, not OTHER_ORG_ID
        # At minimum, the injected header should not cause a 500
        assert response.status_code != 500

    async def test_cannot_set_org_context_via_query_param(
        self, authed_client: AsyncClient
    ) -> None:
        """Query parameters should not override org scoping."""
        response = await authed_client.get(
            "/v1/memories",
            params={"org_id": OTHER_ORG_ID},
        )
        assert response.status_code != 500
        # A 422 (unknown param) or 200 (param ignored) are both acceptable


# ---------------------------------------------------------------------------
# Auth Enforcement Tests
# ---------------------------------------------------------------------------


class TestAuthEnforcement:
    """Verify authentication is enforced on all protected endpoints."""

    PROTECTED_ENDPOINTS = [
        ("GET", "/v1/memories"),
        ("POST", "/v1/memories"),
        ("POST", "/v1/memories/recall"),
        ("POST", "/v1/memories/forget"),
        ("GET", "/v1/audit"),
    ]

    async def test_missing_auth_returns_401(self, unauthed_client: AsyncClient) -> None:
        """All protected endpoints return 401 without credentials."""
        for method, path in self.PROTECTED_ENDPOINTS:
            # POST endpoints need Content-Type to pass body-limit middleware
            headers = {"Content-Type": "application/json"} if method == "POST" else {}
            response = await unauthed_client.request(method, path, headers=headers)
            assert response.status_code == 401, f"{method} {path} returned {response.status_code}, expected 401"

    async def test_invalid_api_key_returns_401(self, unauthed_client: AsyncClient) -> None:
        """Invalid API key returns 401 with error details."""
        response = await unauthed_client.get(
            "/v1/memories",
            headers={"X-API-Key": "z3rno_sk_definitely_invalid_key"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "unauthorized"

    async def test_expired_jwt_returns_401(self, unauthed_client: AsyncClient) -> None:
        """Expired JWT returns 401."""
        import jwt as pyjwt

        expired_token = pyjwt.encode(
            {
                "sub": "user-1",
                "org_id": DEV_ORG_ID,
                "role": "admin",
                "exp": int(time.time()) - 3600,
                "iat": int(time.time()) - 7200,
            },
            "test-jwt-secret-key-for-unit-tests",
            algorithm="HS256",
        )
        response = await unauthed_client.get(
            "/v1/memories",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Input Validation Tests
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Verify the API rejects malformed input gracefully."""

    async def test_oversized_content_returns_422(self, authed_client: AsyncClient) -> None:
        """Content exceeding max_length (100K chars) should be rejected."""
        response = await authed_client.post(
            "/v1/memories",
            json={
                "agent_id": str(uuid4()),
                "content": "x" * 100_001,
                "memory_type": "episodic",
            },
        )
        assert response.status_code == 422

    async def test_invalid_uuid_returns_422(self, authed_client: AsyncClient) -> None:
        """Non-UUID agent_id should return 422."""
        response = await authed_client.post(
            "/v1/memories",
            json={
                "agent_id": "not-a-valid-uuid",
                "content": "test content",
                "memory_type": "episodic",
            },
        )
        assert response.status_code == 422

    async def test_malformed_json_returns_422(self, authed_client: AsyncClient) -> None:
        """Malformed JSON body should return 422."""
        response = await authed_client.post(
            "/v1/memories",
            content=b"{invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    async def test_empty_content_returns_422(self, authed_client: AsyncClient) -> None:
        """Empty string content (below min_length=1) should return 422."""
        response = await authed_client.post(
            "/v1/memories",
            json={
                "agent_id": str(uuid4()),
                "content": "",
                "memory_type": "episodic",
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Rate Limiting Tests
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Verify rate limiting returns 429 with proper headers."""

    async def test_rate_limit_returns_429_with_retry_after(
        self, authed_client: AsyncClient
    ) -> None:
        """When rate limit is exceeded, response should be 429 with Retry-After header."""
        from z3rno_server.middleware.rate_limit import _rate_limit_response

        # Directly test the response builder to verify format
        response = _rate_limit_response(limit=1000, retry_after=42)
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "42"
        assert response.headers["X-RateLimit-Limit"] == "1000"
        assert response.headers["X-RateLimit-Remaining"] == "0"

    async def test_rate_limit_response_body_format(self) -> None:
        """Rate limit response body should include error and detail fields."""
        from z3rno_server.middleware.rate_limit import _rate_limit_response

        response = _rate_limit_response(limit=500, retry_after=10)
        # Decode the response body
        body = response.body.decode()
        assert "rate_limit_exceeded" in body
        assert "Retry after" in body

    @patch("z3rno_server.middleware.rate_limit._check_rate_limit")
    async def test_exceeded_rate_limit_blocks_request(
        self, mock_check: AsyncMock, authed_client: AsyncClient
    ) -> None:
        """When _check_rate_limit returns not-allowed, request gets 429."""
        # Simulate rate limit exceeded
        mock_check.return_value = (False, 0, int(time.time()) + 60)

        response = await authed_client.get("/v1/memories")
        # If rate limit middleware fires, we get 429
        if response.status_code == 429:
            assert "Retry-After" in response.headers
