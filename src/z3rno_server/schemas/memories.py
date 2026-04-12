"""Request/response schemas for memory operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RelationshipInput(BaseModel):
    """Input for creating a memory relationship."""

    target_memory_id: UUID
    relationship_type: str = Field(
        ...,
        pattern="^(derived_from|contradicts|supports|supersedes|related_to|caused_by)$",
    )
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoreMemoryRequest(BaseModel):
    """POST /v1/memories - store a new memory."""

    agent_id: UUID
    content: str = Field(..., min_length=1, max_length=100000)
    memory_type: str = Field(
        default="episodic",
        pattern="^(working|episodic|semantic|procedural)$",
    )
    user_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    relationships: list[RelationshipInput] = Field(default_factory=list)
    ttl_seconds: int | None = Field(default=None, ge=1)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryResponse(BaseModel):
    """Response for a stored memory."""

    id: UUID
    agent_id: UUID
    content: str
    memory_type: str
    importance_score: float
    recall_count: int
    embedding_model: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallRequest(BaseModel):
    """POST /v1/memories/recall - recall memories."""

    agent_id: UUID
    query: str | None = None
    memory_type: str | None = None
    filters: dict[str, Any] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    similarity_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    time_range: tuple[datetime, datetime] | None = None
    as_of: datetime | None = None
    include_deleted: bool = False


class RecallResultItem(BaseModel):
    """A single recall result."""

    memory_id: UUID
    content: str
    summary: str | None = None
    memory_type: str
    similarity_score: float
    importance_score: float
    relevance_score: float
    recall_count: int
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallResponse(BaseModel):
    """Response for a recall query."""

    results: list[RecallResultItem]
    total: int
    query: str | None = None


class ForgetRequest(BaseModel):
    """POST /v1/memories/forget - forget memories."""

    agent_id: UUID
    memory_id: UUID | None = None
    memory_ids: list[UUID] | None = None
    hard_delete: bool = False
    cascade: bool = False
    reason: str | None = None


class ForgetResponse(BaseModel):
    """Response for a forget operation."""

    deleted_count: int
    hard_deleted: bool
    cascade_count: int
    memory_ids: list[UUID]
