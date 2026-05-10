"""Pydantic request / response schemas for ``/v1/ingest`` (Phase B.1).

Two request shapes:

  * **JSON** — ``IngestTextRequest`` and ``IngestUrlRequest`` (discriminated
    on ``kind``). Used for text / URL ingestion where the payload is small
    and structured.
  * **Multipart** — :class:`IngestFileRequest` is built field-by-field from
    Form fields in the route handler; we don't expose it as a standalone
    Pydantic body.

Response shape mirrors the ``ingest_jobs`` row so polling
``GET /v1/ingest/{job_id}`` is a thin SELECT.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Per-job options (subset of IngestOptions exposed to API callers)
# ---------------------------------------------------------------------------


class IngestOptionsRequest(BaseModel):
    """Per-job tuning. Defaults inherit from server settings when omitted."""

    model_config = ConfigDict(extra="forbid")

    auto_distill: bool | None = Field(
        default=None,
        description="Override INGEST_AUTO_DISTILL for this job.",
    )
    chunk_size: int | None = Field(default=None, ge=64, le=8192)
    chunk_overlap: int | None = Field(default=None, ge=0, le=2048)
    summary_style: Literal["concise", "bullet", "abstractive"] | None = None


# ---------------------------------------------------------------------------
# JSON requests (text + url)
# ---------------------------------------------------------------------------


class _IngestRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    dataset_id: UUID | None = None
    options: IngestOptionsRequest | None = None


class IngestTextRequest(_IngestRequestBase):
    """Plain-text ingest — no fetch, no file upload."""

    kind: Literal["text"] = "text"
    text: str = Field(..., min_length=1, max_length=4_000_000)
    filename: str | None = Field(default=None, max_length=500)
    content_type: str | None = Field(default=None, max_length=200)


class IngestUrlRequest(_IngestRequestBase):
    """URL ingest — server fetches the URL and routes by Content-Type."""

    kind: Literal["url"] = "url"
    url: str = Field(..., min_length=8, max_length=4_096)


IngestRequest = Annotated[
    IngestTextRequest | IngestUrlRequest,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class IngestJobResponse(BaseModel):
    """Body of ``POST /v1/ingest`` and ``POST /v1/ingest/file`` — returned
    immediately after enqueue."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    kind: Literal["text", "url", "file"]
    status: str = Field(default="queued")
    dataset_id: UUID | None = None
    enqueued_at: datetime


class IngestJobStatus(BaseModel):
    """Body of ``GET /v1/ingest/{job_id}`` — full row state."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    agent_id: UUID
    dataset_id: UUID | None
    kind: Literal["text", "url", "file"]
    status: str
    source_uri: str | None
    content_type: str | None
    filename: str | None
    file_size: int | None
    memory_ids: list[UUID]
    memos_written: int
    distill_job_id: UUID | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
