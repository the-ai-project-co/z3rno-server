"""Audit log API endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from z3rno_core.engine import audit
from z3rno_server.dependencies import DbSession
from z3rno_server.schemas.audit import AuditEntryResponse, AuditPageResponse, AuditQueryParams
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/audit", tags=["audit"])


def _get_org_id(request: Request) -> UUID:
    """Extract org_id from request state."""
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=401, detail="No org context")
    return org_id  # type: ignore[no-any-return]


@router.get(
    "",
    response_model=AuditPageResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Query the audit log",
)
async def query_audit(
    request: Request,
    db: DbSession,
    params: AuditQueryParams = Depends(),  # noqa: B008
) -> AuditPageResponse:
    """Query the audit log with filters and pagination."""
    org_id = _get_org_id(request)

    time_range = None
    if params.start_time and params.end_time:
        time_range = (params.start_time, params.end_time)

    conn = await db.connection()
    page = await audit(
        conn,
        org_id=org_id,
        agent_id=params.agent_id,
        user_id=params.user_id,
        operation=params.operation,
        memory_id=params.memory_id,
        memory_type=params.memory_type,
        time_range=time_range,
        page=params.page,
        page_size=params.page_size,
    )

    entries = [
        AuditEntryResponse(
            id=e.id,
            agent_id=e.agent_id,
            user_id=e.user_id,
            operation=e.operation,
            memory_id=e.memory_id,
            memory_type=e.memory_type,
            details=e.details,
            ip_address=e.ip_address,
            created_at=e.created_at,
        )
        for e in page.entries
    ]

    return AuditPageResponse(
        entries=entries,
        total=page.total,
        page=page.page,
        page_size=page.page_size,
        has_next=page.has_next,
    )
