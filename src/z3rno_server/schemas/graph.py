"""Pydantic schemas for ``GET /v1/graph/data`` (Phase E slice 5).

Returns a denormalised nodes + edges payload for the graph viewer.
Both shapes intentionally mirror the JSON the front-end consumes —
this endpoint is the single source of truth for the viewer's data
contract.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GraphNode(BaseModel):
    """One Memo as it appears on the viewer's graph."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    memo_type: str | None
    content: str
    importance_score: float
    metadata: dict[str, Any]


class GraphEdge(BaseModel):
    """One memory_relationships row, denormalised for the viewer."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source: UUID
    target: UUID
    predicate: str
    weight: float
    metadata: dict[str, Any]


class GraphScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID | None
    agent_id: UUID | None
    memo_type: str | None


class GraphDataResponse(BaseModel):
    """``GET /v1/graph/data`` body."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    scope: GraphScope
    truncated: bool = Field(
        ...,
        description="True when the node limit was hit and additional Memos exist out of view.",
    )
