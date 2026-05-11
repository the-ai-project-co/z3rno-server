"""Pydantic schemas for ``/v1/refine`` (Phase D slice 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RefineRequest(BaseModel):
    """Body of ``POST /v1/refine``. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID | None = Field(
        default=None,
        description="Scope the run to one dataset. NULL ⇒ run across all datasets in the org.",
    )


class RefineJobResponse(BaseModel):
    """``POST /v1/refine`` response — minimal enqueue ack."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: str = "queued"
    dataset_id: UUID | None
    enqueued_at: datetime


class RefineJobStatus(BaseModel):
    """``GET /v1/refine/{job_id}`` response — the full row."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: str
    dataset_id: UUID | None
    trigger: str
    memos_scanned: int
    memos_deduped: int
    edges_reweighted: int
    edges_pruned: int
    feedback_drained: int
    job_metadata: dict[str, Any]
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
