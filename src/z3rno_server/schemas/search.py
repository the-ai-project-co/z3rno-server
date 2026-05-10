"""Pydantic schemas for ``/v1/ingest/search`` (Phase B.2)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from z3rno_server.schemas.ingest import IngestOptionsRequest


class SearchIngestRequest(BaseModel):
    """Body of ``POST /v1/ingest/search``."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    query: str = Field(..., min_length=1, max_length=4_000)
    dataset_id: UUID | None = None
    max_results: int = Field(default=5, ge=1, le=20)
    options: IngestOptionsRequest | None = None


class SearchIngestJob(BaseModel):
    """One enqueued ingest job from a search hit."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    url: str
    title: str = ""


class SearchIngestResponse(BaseModel):
    """Body of ``POST /v1/ingest/search`` — one entry per discovered URL."""

    model_config = ConfigDict(extra="forbid")

    query: str
    dataset_id: UUID | None
    enqueued_at: datetime
    # Tag joining every child job — poll the whole batch in one round-trip
    # via ``GET /v1/ingest/search/{batch_id}`` instead of N child calls.
    batch_id: UUID
    jobs: list[SearchIngestJob]


class SearchBatchStatus(BaseModel):
    """Body of ``GET /v1/ingest/search/{batch_id}`` — aggregate counts."""

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    total: int
    queued: int
    running: int
    completed: int
    failed: int
    job_ids: list[UUID]
