"""z3rno_server.api.feedback — ``POST /v1/feedback`` (Phase D slice 2).

One endpoint, RLS-isolated, registered only when
``REFINE_ENABLED=true``. Slice 3's refine pipeline drains the
``feedback`` table to update edge weights and Memo importance.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from z3rno_core.refine import record_feedback
from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.feedback import FeedbackCreate, FeedbackResponse
from z3rno_server.schemas.shared import ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=401, detail="No org context")
    if not isinstance(org_id, UUID):
        try:
            org_id = UUID(str(org_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid org context") from exc
    return org_id


@router.post(
    "",
    response_model=FeedbackResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Record a feedback signal on a Memo or edge",
)
async def create_feedback(
    body: FeedbackCreate,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> FeedbackResponse:
    """Insert one row into the ``feedback`` table.

    The Phase D refine pipeline (slice 3) drains this table on its
    scheduled cadence to update edge weights from accumulated signals.
    """
    org_id = _get_org_id(request)

    conn = await db.connection()
    try:
        feedback_id = await record_feedback(
            conn,
            org_id=org_id,
            agent_id=body.agent_id,
            signal=body.signal,
            memory_id=body.memory_id,
            edge_id=body.edge_id,
            reason=body.reason,
        )
    except ValueError as exc:
        # Mirrors the Pydantic validator — kept defensive in case a
        # future caller bypasses Pydantic.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=400, detail="feedback violates a database constraint"
        ) from exc
    except Exception as exc:
        logger.exception("feedback.create.insert_failed")
        raise HTTPException(status_code=500, detail="failed to record feedback") from exc

    return FeedbackResponse(
        id=feedback_id,
        memory_id=body.memory_id,
        edge_id=body.edge_id,
        signal=body.signal,
        created_at=datetime.now(UTC),
    )
