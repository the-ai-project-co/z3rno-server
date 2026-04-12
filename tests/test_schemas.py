"""Tests for Pydantic request/response schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from z3rno_server.schemas.audit import AuditQueryParams
from z3rno_server.schemas.memories import ForgetRequest, RecallRequest, StoreMemoryRequest

# --- StoreMemoryRequest ---


class TestStoreMemoryRequest:
    """Tests for StoreMemoryRequest validation."""

    def test_valid_request(self) -> None:
        """A well-formed request should pass validation."""
        req = StoreMemoryRequest(
            agent_id=uuid4(),
            content="Hello world",
            memory_type="episodic",
        )
        assert req.content == "Hello world"
        assert req.memory_type == "episodic"

    def test_content_min_length(self) -> None:
        """Empty content should be rejected (min_length=1)."""
        with pytest.raises(ValidationError) as exc_info:
            StoreMemoryRequest(agent_id=uuid4(), content="")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("content",) for e in errors)

    def test_valid_memory_types(self) -> None:
        """All four valid memory types should be accepted."""
        for mt in ("working", "episodic", "semantic", "procedural"):
            req = StoreMemoryRequest(
                agent_id=uuid4(),
                content="test",
                memory_type=mt,
            )
            assert req.memory_type == mt

    def test_invalid_memory_type(self) -> None:
        """Invalid memory_type should fail pattern validation."""
        with pytest.raises(ValidationError) as exc_info:
            StoreMemoryRequest(
                agent_id=uuid4(),
                content="test",
                memory_type="invalid",
            )
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("memory_type",) for e in errors)

    def test_default_memory_type_is_episodic(self) -> None:
        """Default memory_type should be 'episodic'."""
        req = StoreMemoryRequest(agent_id=uuid4(), content="test")
        assert req.memory_type == "episodic"


# --- RecallRequest ---


class TestRecallRequest:
    """Tests for RecallRequest validation."""

    def test_valid_request(self) -> None:
        """A well-formed recall request should pass validation."""
        req = RecallRequest(agent_id=uuid4(), query="search term", top_k=5)
        assert req.top_k == 5

    def test_default_top_k(self) -> None:
        """Default top_k should be 10."""
        req = RecallRequest(agent_id=uuid4())
        assert req.top_k == 10

    def test_top_k_minimum(self) -> None:
        """top_k below 1 should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RecallRequest(agent_id=uuid4(), top_k=0)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("top_k",) for e in errors)

    def test_top_k_maximum(self) -> None:
        """top_k above 100 should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RecallRequest(agent_id=uuid4(), top_k=101)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("top_k",) for e in errors)

    def test_top_k_boundary_values(self) -> None:
        """top_k of 1 and 100 should both be accepted."""
        req_min = RecallRequest(agent_id=uuid4(), top_k=1)
        req_max = RecallRequest(agent_id=uuid4(), top_k=100)
        assert req_min.top_k == 1
        assert req_max.top_k == 100


# --- ForgetRequest ---


class TestForgetRequest:
    """Tests for ForgetRequest validation."""

    def test_with_single_memory_id(self) -> None:
        """ForgetRequest should accept a single memory_id."""
        mid = uuid4()
        req = ForgetRequest(agent_id=uuid4(), memory_id=mid)
        assert req.memory_id == mid
        assert req.memory_ids is None

    def test_with_memory_ids_list(self) -> None:
        """ForgetRequest should accept a list of memory_ids."""
        ids = [uuid4(), uuid4()]
        req = ForgetRequest(agent_id=uuid4(), memory_ids=ids)
        assert req.memory_ids == ids
        assert req.memory_id is None

    def test_with_both_memory_id_and_memory_ids(self) -> None:
        """ForgetRequest should accept both memory_id and memory_ids together."""
        single_id = uuid4()
        batch_ids = [uuid4(), uuid4()]
        req = ForgetRequest(
            agent_id=uuid4(),
            memory_id=single_id,
            memory_ids=batch_ids,
        )
        assert req.memory_id == single_id
        assert req.memory_ids == batch_ids

    def test_default_hard_delete_is_false(self) -> None:
        """hard_delete defaults to False."""
        req = ForgetRequest(agent_id=uuid4())
        assert req.hard_delete is False


# --- AuditQueryParams ---


class TestAuditQueryParams:
    """Tests for AuditQueryParams defaults."""

    def test_default_page(self) -> None:
        """Default page is 1."""
        params = AuditQueryParams()
        assert params.page == 1

    def test_default_page_size(self) -> None:
        """Default page_size is 50."""
        params = AuditQueryParams()
        assert params.page_size == 50

    def test_optional_fields_default_to_none(self) -> None:
        """All optional filter fields default to None."""
        params = AuditQueryParams()
        assert params.agent_id is None
        assert params.user_id is None
        assert params.operation is None
        assert params.memory_id is None
        assert params.memory_type is None
        assert params.start_time is None
        assert params.end_time is None

    def test_page_minimum(self) -> None:
        """page below 1 should be rejected."""
        with pytest.raises(ValidationError):
            AuditQueryParams(page=0)

    def test_page_size_maximum(self) -> None:
        """page_size above 100 should be rejected."""
        with pytest.raises(ValidationError):
            AuditQueryParams(page_size=101)
