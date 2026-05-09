"""Pydantic request / response schemas for ``/v1/distill`` (Phase A).

Strictly typed against the Forge orchestrator's contract — every field
maps directly to a column of ``distill_jobs`` (Migration 015) or to a
parameter of :class:`z3rno_core.forge.ForgePipeline.run`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SummaryStyleLiteral = Literal["concise", "bullet", "abstractive"]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class DistillRequest(BaseModel):
    """Body of ``POST /v1/distill``."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID = Field(
        ...,
        description="Agent whose memories should be distilled. Must belong to the caller's org.",
    )
    memory_ids: list[UUID] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Source memory IDs to distill. RLS still applies — IDs not visible to the org are skipped silently.",
    )
    chunk_size: int | None = Field(
        default=None,
        ge=64,
        le=8192,
        description="Override DISTILL_CHUNK_SIZE for this job (tokens).",
    )
    chunk_overlap: int | None = Field(
        default=None,
        ge=0,
        le=2048,
        description="Override DISTILL_CHUNK_OVERLAP for this job (tokens). Must be less than chunk_size.",
    )
    max_concurrency: int | None = Field(
        default=None,
        ge=1,
        le=32,
        description="Override DISTILL_MAX_CONCURRENCY for the per-chunk LLM fan-out.",
    )
    summary_style: SummaryStyleLiteral | None = Field(
        default=None,
        description="Override DISTILL_SUMMARY_STYLE for this job.",
    )
    include_summary: bool = Field(
        default=True,
        description="If false, skip the rolling summarization pass.",
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class DistillJobResponse(BaseModel):
    """Body of ``POST /v1/distill`` — returned immediately after enqueue."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: str = Field(default="queued", description="Job state at enqueue time.")
    memory_ids: list[UUID]
    enqueued_at: datetime


class DistillJobStatus(BaseModel):
    """Body of ``GET /v1/distill/{job_id}`` — full row state."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    agent_id: UUID
    status: str
    model: str
    memory_ids: list[UUID]
    chunk_size: int
    chunk_overlap: int
    max_concurrency: int
    chunks_total: int
    chunks_failed: int
    entities_extracted: int
    relationships_extracted: int
    memos_written: int
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
