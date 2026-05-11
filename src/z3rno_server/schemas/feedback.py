"""Pydantic schemas for ``POST /v1/feedback`` (Phase D slice 2).

Mirrors the ``feedback`` table from Migration 023. Exactly one of
``memory_id`` / ``edge_id`` is required, validated both here and at
the database tier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeedbackCreate(BaseModel):
    """Body of ``POST /v1/feedback``."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID = Field(..., description="Agent recording the feedback.")
    memory_id: UUID | None = Field(
        default=None,
        description="Target Memo id. Mutually exclusive with edge_id.",
    )
    edge_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Target AGE edge id. Mutually exclusive with memory_id.",
    )
    signal: Literal[-1, 0, 1] = Field(
        ...,
        description="-1 (negative), 0 (neutral), +1 (positive).",
    )
    reason: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def _require_exactly_one_target(self) -> FeedbackCreate:
        if (self.memory_id is None) == (self.edge_id is None):
            raise ValueError("exactly one of memory_id or edge_id must be provided")
        return self


class FeedbackResponse(BaseModel):
    """``POST /v1/feedback`` response body."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    memory_id: UUID | None
    edge_id: str | None
    signal: int
    created_at: datetime
