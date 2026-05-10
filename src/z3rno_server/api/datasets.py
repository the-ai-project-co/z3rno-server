"""z3rno_server.api.datasets — datasets CRUD endpoints (Phase B.1).

Routes:

  * ``POST /v1/datasets``        — create
  * ``GET  /v1/datasets``        — list (paginated)
  * ``GET  /v1/datasets/{id}``   — fetch one
  * ``DELETE /v1/datasets/{id}`` — soft-delete the dataset and detach
                                    every memory whose ``dataset_id``
                                    points at it (memo rows are NOT
                                    deleted; only the FK is cleared).

All routes are RLS-isolated by ``org_id``. Registered only when
``INGEST_ENABLED=true``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError

from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.datasets import (
    DatasetCreate,
    DatasetDeleteResponse,
    DatasetListResponse,
    DatasetResponse,
)
from z3rno_server.schemas.shared import ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _row_to_response(row: object) -> DatasetResponse:
    return DatasetResponse(
        id=row[0],  # type: ignore[index]
        name=row[1],  # type: ignore[index]
        description=row[2],  # type: ignore[index]
        created_at=row[3],  # type: ignore[index]
        updated_at=row[4],  # type: ignore[index]
    )


# ---------------------------------------------------------------------------
# POST /v1/datasets — create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DatasetResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    summary="Create a dataset",
)
async def create_dataset(
    body: DatasetCreate,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> DatasetResponse:
    """Create a new dataset for the caller's org. Names are unique
    per-org via ``UNIQUE (org_id, name)`` from Migration 016."""
    org_id = _get_org_id(request)
    dataset_id = uuid4()

    conn = await db.connection()
    try:
        await conn.execute(
            sa_text("""
                INSERT INTO datasets (id, org_id, name, description, created_at, updated_at)
                VALUES (
                    CAST(:id AS uuid),
                    CAST(:org_id AS uuid),
                    :name, :description,
                    now(), now()
                )
            """),
            {
                "id": str(dataset_id),
                "org_id": str(org_id),
                "name": body.name,
                "description": body.description,
            },
        )
    except IntegrityError as exc:
        # uq_datasets_org_name violation
        raise HTTPException(
            status_code=409, detail=f"dataset name {body.name!r} already exists in this org"
        ) from exc
    except Exception as exc:
        logger.exception("datasets.create.insert_failed", extra={"id": str(dataset_id)})
        raise HTTPException(status_code=500, detail="failed to create dataset") from exc

    now = datetime.now(UTC)
    return DatasetResponse(
        id=dataset_id,
        name=body.name,
        description=body.description,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# GET /v1/datasets — list
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=DatasetListResponse,
    responses={401: {"model": ErrorResponse}},
    summary="List datasets in the caller's org",
)
async def list_datasets(
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DatasetListResponse:
    _ = _get_org_id(request)
    conn = await db.connection()

    rows = (
        await conn.execute(
            sa_text("""
                SELECT id, name, description, created_at, updated_at
                FROM datasets
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
    ).fetchall()
    total = (
        await conn.execute(sa_text("SELECT count(*) FROM datasets WHERE deleted_at IS NULL"))
    ).scalar() or 0

    return DatasetListResponse(
        items=[_row_to_response(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /v1/datasets/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/{dataset_id}",
    response_model=DatasetResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Fetch one dataset by id",
)
async def get_dataset(
    dataset_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
) -> DatasetResponse:
    _ = _get_org_id(request)
    conn = await db.connection()

    row = (
        await conn.execute(
            sa_text("""
                SELECT id, name, description, created_at, updated_at
                FROM datasets
                WHERE id = CAST(:id AS uuid)
                  AND deleted_at IS NULL
            """),
            {"id": str(dataset_id)},
        )
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return _row_to_response(row)


# ---------------------------------------------------------------------------
# DELETE /v1/datasets/{id} — soft delete + detach memories
# ---------------------------------------------------------------------------


@router.delete(
    "/{dataset_id}",
    response_model=DatasetDeleteResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Soft-delete a dataset and detach its memories",
)
async def delete_dataset(
    dataset_id: UUID,
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write"),
) -> DatasetDeleteResponse:
    """Soft-delete the dataset row and clear the ``dataset_id`` FK on
    every memory still pointing at it. Memo rows themselves are
    preserved so historical lineage stays intact."""
    _ = _get_org_id(request)
    conn = await db.connection()

    row = (
        await conn.execute(
            sa_text("""
                UPDATE datasets SET deleted_at = now(), updated_at = now()
                WHERE id = CAST(:id AS uuid)
                  AND deleted_at IS NULL
                RETURNING id
            """),
            {"id": str(dataset_id)},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="dataset not found")

    detached = (
        await conn.execute(
            sa_text("""
                UPDATE memories SET dataset_id = NULL, updated_at = now()
                WHERE dataset_id = CAST(:id AS uuid)
                RETURNING 1
            """),
            {"id": str(dataset_id)},
        )
    ).rowcount or 0

    return DatasetDeleteResponse(
        id=dataset_id,
        deleted_at=datetime.now(UTC),
        detached_memory_count=int(detached),
    )
