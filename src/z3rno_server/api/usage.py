"""Phase G slice 6 — usage telemetry endpoint.

``GET /v1/usage`` returns today + month-to-date counters for the
caller's org. Operators read it for billing dashboards; agents read
it to surface "remaining budget" UI before queueing a Forge job.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from z3rno_core.usage import get_usage
from z3rno_server.dependencies import ReadDbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/usage", tags=["usage"])


class UsageBreakdown(BaseModel):
    tokens: int = 0
    embeddings: int = 0
    llm_calls: int = 0
    storage_bytes: int = 0


class UsageResponse(BaseModel):
    org_id: UUID
    today: date
    daily: UsageBreakdown = Field(default_factory=UsageBreakdown)
    monthly: UsageBreakdown = Field(default_factory=UsageBreakdown)
    by_day: dict[str, dict[str, int]] = Field(default_factory=dict)


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="no tenant context")
    return org_id  # type: ignore[no-any-return]


@router.get(
    "",
    response_model=UsageResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Daily + month-to-date usage counters",
)
async def get_org_usage(
    request: Request,
    db: ReadDbSession,
    _rbac: None = require_role("admin", "write", "read"),
    on: date | None = Query(default=None, description="Override 'today' (UTC date)"),
) -> UsageResponse:
    org_id = _get_org_id(request)
    today = on or datetime.now(UTC).date()
    month_start = today.replace(day=1)

    conn = await db.connection()
    daily = await get_usage(conn, org_id=org_id, since=today, until=today)
    monthly = await get_usage(
        conn, org_id=org_id, since=month_start, until=today
    )

    return UsageResponse(
        org_id=org_id,
        today=today,
        daily=UsageBreakdown(
            tokens=daily.tokens,
            embeddings=daily.embeddings,
            llm_calls=daily.llm_calls,
            storage_bytes=daily.storage_bytes,
        ),
        monthly=UsageBreakdown(
            tokens=monthly.tokens,
            embeddings=monthly.embeddings,
            llm_calls=monthly.llm_calls,
            storage_bytes=monthly.storage_bytes,
        ),
        by_day={
            d.isoformat(): v for d, v in monthly.by_day.items()
        },
    )
