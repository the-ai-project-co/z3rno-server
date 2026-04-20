"""End-to-end integration test for the z3rno-server memory lifecycle.

Tests the full store -> recall -> forget -> verify-forgotten cycle
with audit log verification. Uses mocked engine layer to avoid
requiring a live PostgreSQL/Valkey instance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from z3rno_server.dependencies import get_db
from z3rno_server.main import app

from .conftest import DEV_API_KEY, DEV_ORG_ID

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AGENT_ID = uuid4()
MEMORY_ID = uuid4()
NOW = datetime.now(tz=timezone.utc)


def _make_store_result() -> MagicMock:
    """Create a mock StoreResult matching z3rno_core.engine.store return."""
    result = MagicMock()
    result.memory_id = MEMORY_ID
    result.importance_score = 0.75
    result.embedding_model = "text-embedding-3-small"
    result.created_at = NOW
    return result


def _make_recall_result(content: str) -> MagicMock:
    """Create a mock RecallResult matching z3rno_core.engine.recall return."""
    item = MagicMock()
    item.memory_id = MEMORY_ID
    item.content = content
    item.summary = None
    item.memory_type = "episodic"
    item.similarity_score = 0.95
    item.importance_score = 0.75
    item.relevance_score = 0.85
    item.recall_count = 1
    item.created_at = NOW
    item.metadata = {}
    return item


def _make_forget_result() -> MagicMock:
    """Create a mock ForgetResult matching z3rno_core.engine.forget return."""
    result = MagicMock()
    result.deleted_count = 1
    result.hard_deleted = False
    result.cascade_count = 0
    result.memory_ids = [MEMORY_ID]
    return result


def _make_audit_page(operations: list[str]) -> MagicMock:
    """Create a mock AuditPage with given operations."""
    page = MagicMock()
    entries = []
    for i, op in enumerate(operations):
        entry = MagicMock()
        entry.id = i + 1
        entry.agent_id = AGENT_ID
        entry.user_id = None
        entry.operation = op
        entry.memory_id = MEMORY_ID
        entry.memory_type = "episodic"
        entry.details = {}
        entry.ip_address = "127.0.0.1"
        entry.created_at = NOW
        entries.append(entry)
    page.entries = entries
    page.total = len(entries)
    page.page = 1
    page.page_size = 50
    page.has_next = False
    return page


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    """Create a test client with dev auth and mocked DB."""

    async def _mock_get_db():  # type: ignore[no-untyped-def]
        session = AsyncMock()
        # session.connection() must be awaitable and return a mock connection
        mock_conn = AsyncMock()
        session.connection = AsyncMock(return_value=mock_conn)
        yield session

    app.dependency_overrides[get_db] = _mock_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": DEV_API_KEY},
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_memory_lifecycle_e2e(client: AsyncClient) -> None:
    """Full lifecycle: store -> recall -> forget -> verify forgotten -> audit."""
    content = "The capital of France is Paris."

    # Step 1: Store a memory
    with patch("z3rno_server.api.memories.store", new_callable=AsyncMock) as mock_store:
        mock_store.return_value = _make_store_result()

        response = await client.post(
            "/v1/memories",
            json={
                "agent_id": str(AGENT_ID),
                "content": content,
                "memory_type": "episodic",
            },
        )

    assert response.status_code == 200, f"Store failed: {response.text}"
    store_data = response.json()
    memory_id = store_data["id"]
    assert store_data["content"] == content
    assert store_data["memory_type"] == "episodic"
    assert store_data["agent_id"] == str(AGENT_ID)

    # Step 2: Recall the memory
    with patch("z3rno_server.api.memories.recall", new_callable=AsyncMock) as mock_recall:
        mock_recall.return_value = [_make_recall_result(content)]

        response = await client.post(
            "/v1/memories/recall",
            json={
                "agent_id": str(AGENT_ID),
                "query": "What is the capital of France?",
                "top_k": 5,
            },
        )

    assert response.status_code == 200, f"Recall failed: {response.text}"
    recall_data = response.json()
    assert recall_data["total"] >= 1
    assert any(r["content"] == content for r in recall_data["results"])
    assert recall_data["results"][0]["memory_id"] == memory_id

    # Step 3: Forget the memory
    with patch("z3rno_server.api.memories.forget", new_callable=AsyncMock) as mock_forget:
        mock_forget.return_value = _make_forget_result()

        response = await client.post(
            "/v1/memories/forget",
            json={
                "agent_id": str(AGENT_ID),
                "memory_id": memory_id,
            },
        )

    assert response.status_code == 200, f"Forget failed: {response.text}"
    forget_data = response.json()
    assert forget_data["deleted_count"] == 1
    assert memory_id in forget_data["memory_ids"]

    # Step 4: Verify recall no longer returns it
    with patch("z3rno_server.api.memories.recall", new_callable=AsyncMock) as mock_recall:
        mock_recall.return_value = []  # Empty - memory is forgotten

        response = await client.post(
            "/v1/memories/recall",
            json={
                "agent_id": str(AGENT_ID),
                "query": "What is the capital of France?",
                "top_k": 5,
            },
        )

    assert response.status_code == 200, f"Post-forget recall failed: {response.text}"
    recall_data = response.json()
    assert recall_data["total"] == 0
    assert not any(r["memory_id"] == memory_id for r in recall_data["results"])

    # Step 5: Verify audit log has all 3 operations (store, recall, forget)
    with patch("z3rno_server.api.audit.audit", new_callable=AsyncMock) as mock_audit:
        mock_audit.return_value = _make_audit_page(["store", "recall", "forget"])

        response = await client.get(
            "/v1/audit",
            params={"agent_id": str(AGENT_ID)},
        )

    assert response.status_code == 200, f"Audit failed: {response.text}"
    audit_data = response.json()
    operations = [e["operation"] for e in audit_data["entries"]]
    assert "store" in operations, "Audit log missing 'store' operation"
    assert "recall" in operations, "Audit log missing 'recall' operation"
    assert "forget" in operations, "Audit log missing 'forget' operation"
