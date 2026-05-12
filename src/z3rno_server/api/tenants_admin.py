"""v0.22.1 — cross-tenant budget admin (slice 21.3).

Sister surface to ``/v1/tenants/me/budgets`` for managed-hosting
providers. Lets a superadmin principal set budgets *on behalf of*
a tenant without holding that tenant's auth.

  * GET  /v1/tenants/{org_id}/budgets — overrides + effective
  * PUT  /v1/tenants/{org_id}/budgets — replace overrides

Auth posture:
  * Routes register only when ``settings.superadmin_enabled=true``
    AND ``settings.superadmin_api_key`` is non-empty.
  * The auth middleware stamps ``role="superadmin"`` after matching
    the env-keyed key; ``require_superadmin()`` rejects every other
    role including the ``role=None`` API-key path that the
    backward-compat ``require_role`` lets through.

RLS handling:
  Each handler runs ``SET LOCAL app.current_org_id`` to the target
  ``org_id`` from the URL path so the same RLS-scoped SQL the
  ``/me/budgets`` route uses works cross-tenant. No engine change
  and no privileged Postgres role required.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import text as sa_text

from z3rno_core.usage import resolve_budgets
from z3rno_server.api.tenants import _budgets_to_request, _server_defaults
from z3rno_server.dependencies import DbSession, ReadDbSession
from z3rno_server.middleware.rbac import require_superadmin
from z3rno_server.schemas.shared import ErrorResponse
from z3rno_server.schemas.tenants import TenantBudgetsRequest, TenantBudgetsResponse

router = APIRouter(prefix="/v1/tenants", tags=["tenants-admin"])


async def _assert_org_exists(conn, org_id: UUID) -> None:  # type: ignore[no-untyped-def]
    row = (
        await conn.execute(
            sa_text(
                "SELECT 1 FROM tenants WHERE org_id = CAST(:org AS uuid) LIMIT 1"
            ),
            {"org": str(org_id)},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"tenant {org_id} not found")


@router.get(
    "/{org_id}/budgets",
    response_model=TenantBudgetsResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="[superadmin] Read any tenant's budget overrides + effective caps",
)
async def admin_get_budgets(
    org_id: UUID,
    db: ReadDbSession,
    _rbac: None = require_superadmin(),
) -> TenantBudgetsResponse:
    conn = await db.connection()
    # Scope RLS to the target org so the SELECT in this conn sees the
    # right row. Must happen before the SELECT.
    await conn.execute(
        sa_text(f"SET LOCAL app.current_org_id = '{org_id}'")
    )
    await _assert_org_exists(conn, org_id)

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
    overrides = TenantBudgetsRequest(
        **{
            k: int(overrides_raw.get(k, 0) or 0)
            for k in TenantBudgetsRequest.model_fields
        }
    )
    effective = await resolve_budgets(
        conn, org_id=org_id, defaults=_server_defaults()
    )
    return TenantBudgetsResponse(
        overrides=overrides,
        effective=_budgets_to_request(effective),
    )


@router.put(
    "/{org_id}/budgets",
    response_model=TenantBudgetsResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="[superadmin] Replace any tenant's budget overrides",
)
async def admin_put_budgets(
    org_id: UUID,
    body: TenantBudgetsRequest,
    db: DbSession,
    _rbac: None = require_superadmin(),
) -> TenantBudgetsResponse:
    conn = await db.connection()
    await conn.execute(
        sa_text(f"SET LOCAL app.current_org_id = '{org_id}'")
    )
    await _assert_org_exists(conn, org_id)

    payload = body.model_dump()
    await conn.execute(
        sa_text(
            "UPDATE tenants SET usage_budget = CAST(:b AS jsonb) "
            "WHERE org_id = CAST(:org AS uuid)"
        ),
        {"b": json.dumps(payload), "org": str(org_id)},
    )
    effective = await resolve_budgets(
        conn, org_id=org_id, defaults=_server_defaults()
    )
    return TenantBudgetsResponse(
        overrides=body,
        effective=_budgets_to_request(effective),
    )
