"""v0.20.3 — per-tenant budget management.

Auth model: **org-self-management.** Any admin/write member of the
calling org can read or update their own org's budget overrides via
``/v1/tenants/me/budgets``. RLS keeps cross-tenant inspection out
of reach by construction.

  * GET  /v1/tenants/me/budgets — current overrides + effective
    (after resolve_budgets merge with server defaults).
  * PUT  /v1/tenants/me/budgets — replace overrides. Zero / missing
    fields mean "inherit server default" (matches resolve_budgets).

A platform-admin endpoint (``/v1/tenants/{org_id}/budgets``) is a
v0.21 follow-up if the demand surfaces — for now, the calling
org's auth context pins ``org_id`` and the explicit form would
403 cross-tenant anyway.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as sa_text

from z3rno_core.usage import Budgets, resolve_budgets
from z3rno_server.config import get_settings
from z3rno_server.dependencies import DbSession, ReadDbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.shared import ErrorResponse
from z3rno_server.schemas.tenants import TenantBudgetsRequest, TenantBudgetsResponse

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="no tenant context")
    return org_id  # type: ignore[no-any-return]


def _server_defaults() -> Budgets:
    """Build server-global Budgets from env. Same shape the worker
    pipelines use via _make_budgets()."""
    s = get_settings()
    return Budgets(
        daily_tokens=s.usage_budget_daily_tokens,
        daily_llm_calls=s.usage_budget_daily_llm_calls,
        daily_embeddings=s.usage_budget_daily_embeddings,
        monthly_tokens=s.usage_budget_monthly_tokens,
        monthly_llm_calls=s.usage_budget_monthly_llm_calls,
        monthly_embeddings=s.usage_budget_monthly_embeddings,
    )


def _budgets_to_request(b: Budgets) -> TenantBudgetsRequest:
    return TenantBudgetsRequest(
        daily_tokens=b.daily_tokens,
        daily_llm_calls=b.daily_llm_calls,
        daily_embeddings=b.daily_embeddings,
        monthly_tokens=b.monthly_tokens,
        monthly_llm_calls=b.monthly_llm_calls,
        monthly_embeddings=b.monthly_embeddings,
    )


@router.get(
    "/me/budgets",
    response_model=TenantBudgetsResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Read this org's budget overrides + effective caps",
)
async def get_my_budgets(
    request: Request,
    db: ReadDbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> TenantBudgetsResponse:
    org_id = _get_org_id(request)
    conn = await db.connection()
    row = (
        await conn.execute(
            sa_text(
                "SELECT usage_budget FROM tenants "
                "WHERE org_id = CAST(:org AS uuid) LIMIT 1"
            ),
            {"org": str(org_id)},
        )
    ).fetchone()
    overrides_raw = dict(row[0]) if row and row[0] else {}
    overrides = TenantBudgetsRequest(**{
        k: int(overrides_raw.get(k, 0) or 0)
        for k in TenantBudgetsRequest.model_fields
    })
    effective = await resolve_budgets(
        conn, org_id=org_id, defaults=_server_defaults()
    )
    return TenantBudgetsResponse(
        overrides=overrides,
        effective=_budgets_to_request(effective),
    )


@router.put(
    "/me/budgets",
    response_model=TenantBudgetsResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Replace this org's budget overrides",
)
async def put_my_budgets(
    body: TenantBudgetsRequest,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> TenantBudgetsResponse:
    org_id = _get_org_id(request)
    conn = await db.connection()

    # Persist the override blob. Empty dict is fine — resolve_budgets
    # treats a NULL or empty row as "no overrides" identically.
    payload = body.model_dump()
    await conn.execute(
        sa_text(
            "UPDATE tenants SET usage_budget = CAST(:b AS jsonb) "
            "WHERE org_id = CAST(:org AS uuid)"
        ),
        {"b": json.dumps(payload), "org": str(org_id)},
    )

    # Return both the stored overrides AND the resolved effective
    # caps so the client can render "your cap is X" in one round-trip.
    effective = await resolve_budgets(
        conn, org_id=org_id, defaults=_server_defaults()
    )
    return TenantBudgetsResponse(
        overrides=body,
        effective=_budgets_to_request(effective),
    )
