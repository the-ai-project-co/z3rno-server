"""Request/response schemas for audit operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuditQueryParams(BaseModel):
    """Query parameters for GET /v1/audit."""

    agent_id: UUID | None = None
    user_id: UUID | None = None
    operation: str | None = None
    memory_id: UUID | None = None
    memory_type: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class AuditEntryResponse(BaseModel):
    """A single audit log entry."""

    id: int
    agent_id: UUID | None = None
    user_id: UUID | None = None
    operation: str
    memory_id: UUID | None = None
    memory_type: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    created_at: datetime


class AuditPageResponse(BaseModel):
    """Paginated audit log response."""

    entries: list[AuditEntryResponse]
    total: int
    page: int
    page_size: int
    has_next: bool
