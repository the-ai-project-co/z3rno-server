"""Phase F slice 5 — forget-proof retrieval.

``GET /v1/forget/{cert_id}`` returns the signed certificate matching
``cert_id``. The endpoint is registered only when
``FORGET_PROOF_ENABLED=true`` server-side. RLS keeps the query
scoped to the caller's tenant so a cert_id from another org 404s.
"""

from __future__ import annotations

import base64
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from z3rno_server.dependencies import ReadDbSession
from z3rno_server.middleware.rbac import require_role

router = APIRouter(prefix="/v1/forget", tags=["forget"])


class ForgetCertificateResponse(BaseModel):
    """Public shape of a forget certificate row.

    Bytes fields are base64-encoded for transport. The verifier CLI
    rebuilds the canonical payload from these fields and checks the
    signature against the operator-published public key.
    """

    cert_id: UUID
    org_id: UUID
    agent_id: UUID | None
    memory_ids: list[UUID]
    merkle_root_b64: str
    signature_b64: str
    signer_key_id: str
    audit_seq_start: int | None
    audit_seq_end: int | None
    hard_delete: bool
    signed_at: str


def _get_org_id(request: Request) -> UUID:
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="no tenant context")
    return org_id  # type: ignore[no-any-return]


@router.get(
    "/{cert_id}",
    response_model=ForgetCertificateResponse,
    summary="Fetch a forget-with-proof certificate",
)
async def get_forget_certificate(
    cert_id: UUID,
    request: Request,
    db: ReadDbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> ForgetCertificateResponse:
    org_id = _get_org_id(request)
    conn = await db.connection()
    result = await conn.execute(
        sa_text("""
            SELECT cert_id, org_id, agent_id, memory_ids,
                   merkle_root, signature, signer_key_id,
                   audit_seq_start, audit_seq_end,
                   hard_delete, signed_at
            FROM forget_certificates
            WHERE org_id = CAST(:org_id AS uuid)
              AND cert_id = CAST(:cert_id AS uuid)
        """),
        {"org_id": str(org_id), "cert_id": str(cert_id)},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="certificate not found")

    return ForgetCertificateResponse(
        cert_id=row[0],
        org_id=row[1],
        agent_id=row[2],
        memory_ids=list(row[3] or []),
        merkle_root_b64=base64.b64encode(bytes(row[4])).decode("ascii"),
        signature_b64=base64.b64encode(bytes(row[5])).decode("ascii"),
        signer_key_id=row[6],
        audit_seq_start=row[7],
        audit_seq_end=row[8],
        hard_delete=bool(row[9]),
        signed_at=row[10].isoformat(),
    )
