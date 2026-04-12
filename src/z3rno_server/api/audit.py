"""Audit log API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from z3rno_server.schemas.audit import AuditPageResponse, AuditQueryParams
from z3rno_server.schemas.shared import ErrorResponse

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get(
    "",
    response_model=AuditPageResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Query the audit log",
)
async def query_audit(
    params: AuditQueryParams = Depends(),  # noqa: B008
) -> AuditPageResponse:
    """Query the audit log with filters and pagination."""
    # TODO: implement with z3rno_core.engine.audit()
    return AuditPageResponse(
        entries=[],
        total=0,
        page=params.page,
        page_size=params.page_size,
        has_next=False,
    )
