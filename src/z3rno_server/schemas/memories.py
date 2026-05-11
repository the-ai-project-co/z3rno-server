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
    # Phase C: strategy selection. Case-insensitive. AUTO is the default
    # and in C.1 delegates to VECTOR; later slices wire it to an LLM
    # classifier that picks among VECTOR / LEXICAL / GRAPH / etc.
    strategy: str = "AUTO"
    # Phase C.3: opt-in cross-encoder re-ranking.
    rerank: bool = False
    # Phase C.4: operator-supplied raw Cypher for ``strategy="CYPHER"``.
    # Honoured only when ALLOW_CYPHER_QUERY=true on the server; the
    # strategy itself enforces the gate (returns 403 when off).
    raw_cypher: str | None = None
    # Phase F slice 4: opt-in 4-tier memory routing. With strategy=AUTO
    # and no explicit memory_type, the server asks the MemoryTierRouter
    # for one or more tiers and fans the delegate strategy out across
    # them. No-op for other strategies. When omitted, falls back to the
    # server-side MEMORY_TIER_AUTO_ROUTE default.
    tier_route: bool | None = None
    # Phase F slice 2: caller role for compliance-graded retrieval.
    # When RETRIEVAL_REDACTION_ENABLED=true server-side, the configured
    # RedactionFilter scrubs PII per this role before the response
    # leaves the box. Unknown role → fallback_role's rules apply.
    role: str | None = None
    # Phase G slice 2: scope recall to a single conversation. When set,
    # only Memos whose memories.conversation_id matches are returned.
    # Strategies inherit the filter via build_where_clause.
    conversation_id: UUID | None = None


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
    # Phase C: per-source signals (vector, lexical, graph_distance, …).
    # Optional — clients ignoring it keep the existing flat shape.
    score_components: dict[str, float] = Field(default_factory=dict)


class RecallResponse(BaseModel):
    """Response for a recall query."""

    results: list[RecallResultItem]
    total: int
    query: str | None = None
    # Phase C: strategy provenance. ``strategy_used`` is what actually
    # ran (after AUTO routing + re-rank); ``strategies_considered`` is
    # the AUTO candidate list for explainability.
    strategy_used: str = "VECTOR"
    strategies_considered: list[str] = Field(default_factory=list)
    reranked: bool = False
    elapsed_ms: float = 0.0


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
    # Phase F slice 5: populated when FORGET_PROOF_ENABLED=true and the
    # signing key is loadable. Clients can retrieve the full certificate
    # at ``GET /v1/forget/{cert_id}``.
    cert_id: UUID | None = None


# --- Memory History (SCD Type 2) ---


class MemoryVersionResponse(BaseModel):
    """A single temporal version of a memory."""

    id: UUID
    content: str
    memory_type: str
    importance_score: float
    valid_from: datetime
    valid_to: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryHistoryResponse(BaseModel):
    """Response for memory history query."""

    memory_id: UUID
    versions: list[MemoryVersionResponse]
    total: int


# --- Batch Store ---


class BatchStoreRequest(BaseModel):
    """POST /v1/memories/batch - store multiple memories."""

    memories: list[StoreMemoryRequest] = Field(..., min_length=1, max_length=100)


class BatchStoreResponse(BaseModel):
    """Response for batch store operation."""

    results: list[MemoryResponse]
    stored_count: int


# --- Update Memory ---


class UpdateMemoryRequest(BaseModel):
    """PATCH /v1/memories/{id} - update a memory."""

    content: str | None = Field(default=None, min_length=1, max_length=100000)
    metadata: dict[str, Any] | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
