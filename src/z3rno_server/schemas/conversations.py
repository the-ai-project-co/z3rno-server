"""Request / response schemas for Phase G slice 2 — conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    user_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    summary_cadence: int = Field(default=10, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationResponse(BaseModel):
    id: UUID
    agent_id: UUID
    user_id: UUID | None = None
    title: str | None = None
    summary_cadence: int
    turn_count: int
    last_summary_turn: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class TurnAddRequest(BaseModel):
    """Append a turn to a conversation.

    The Memo must already exist (the typical flow is: POST
    /v1/memories to create the Memo, then POST /v1/conversations/{id}/turns
    to stamp it). One round-trip aggregate land in v0.19.
    """

    model_config = ConfigDict(extra="forbid")

    memory_id: UUID
    turn_role: str = Field(pattern="^(user|assistant|system|tool|summary)$")


class TurnAddResponse(BaseModel):
    turn_index: int
    needs_summary: bool


class TurnResponse(BaseModel):
    memory_id: UUID
    turn_index: int
    turn_role: str
    content: str
    created_at: datetime


class TurnListResponse(BaseModel):
    turns: list[TurnResponse]
    total: int
    conversation_id: UUID
