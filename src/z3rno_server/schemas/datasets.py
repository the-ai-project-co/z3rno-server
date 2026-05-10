"""Pydantic schemas for the ``/v1/datasets`` endpoints (Phase B.1).

Datasets are project-level containers that group memories under an
organization. They become first-class in Phase B.1 alongside the
ingestion surface — every ingest can optionally be scoped to a
``dataset_id`` and recall queries can filter on it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatasetCreate(BaseModel):
    """Body of ``POST /v1/datasets``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4_000)


class DatasetResponse(BaseModel):
    """One dataset row."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class DatasetListResponse(BaseModel):
    """``GET /v1/datasets`` body — paginated."""

    model_config = ConfigDict(extra="forbid")

    items: list[DatasetResponse]
    total: int
    limit: int
    offset: int


class DatasetDeleteResponse(BaseModel):
    """``DELETE /v1/datasets/{id}`` body."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deleted_at: datetime
    detached_memory_count: int = Field(
        ...,
        description=(
            "Number of memories that were detached from this dataset (their "
            "`dataset_id` was set to NULL). Memo rows themselves are not deleted."
        ),
    )
