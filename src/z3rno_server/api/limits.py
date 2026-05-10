"""``GET /v1/limits`` — surface ingest/multimodal caps to SDK consumers.

Lets clients ask "what are this server's caps?" instead of hitting a
413 / size error and reverse-engineering them. Routed through the
normal auth middleware — caller still needs a valid API key.

The shape is forward-compatible: future caps can be added without
breaking older SDKs (they ignore unknown keys).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from z3rno_server.config import get_settings

router = APIRouter(tags=["limits"])


class LimitsResponse(BaseModel):
    """Body of ``GET /v1/limits``."""

    model_config = ConfigDict(extra="forbid")

    # Ingest
    ingest_max_file_bytes: int
    ingest_max_csv_rows: int
    ingest_default_chunk_size: int
    url_fetch_timeout_seconds: float
    # Multimodal
    multimodal_max_image_bytes: int
    multimodal_max_audio_bytes: int
    # Rate limit (when enabled)
    rate_limit_enabled: bool
    rate_limit_per_minute: int


@router.get(
    "/v1/limits",
    response_model=LimitsResponse,
    summary="Server-side limits for ingest and multimodal payloads",
)
async def get_limits() -> LimitsResponse:
    """Return every cap a client might want to know before submitting a job."""
    s = get_settings()
    return LimitsResponse(
        ingest_max_file_bytes=s.ingest_max_file_bytes,
        ingest_max_csv_rows=s.ingest_max_csv_rows,
        ingest_default_chunk_size=s.ingest_default_chunk_size,
        url_fetch_timeout_seconds=s.url_fetch_timeout_seconds,
        multimodal_max_image_bytes=s.multimodal_max_image_bytes,
        multimodal_max_audio_bytes=s.multimodal_max_audio_bytes,
        rate_limit_enabled=s.rate_limit_enabled,
        rate_limit_per_minute=s.rate_limit_per_minute,
    )
