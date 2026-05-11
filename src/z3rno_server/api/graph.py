"""z3rno_server.api.graph — ``GET /v1/graph/data`` (Phase E slice 5).

The viewer's single source of truth: returns a Memo subgraph
(nodes + edges) in one RLS-scoped call. Driven by ``dataset_id``,
``agent_id``, and/or ``memo_type`` query parameters; capped by
``limit`` to keep payload size bounded.

Why a dedicated endpoint instead of composing from recall + a future
relationships-by-id route: a single SQL pass over ``memories`` +
``memory_relationships`` is materially cheaper than a recall (which
runs strategy machinery, fusion, audit writes) plus a follow-up. And
the viewer doesn't need a query — it needs a slice of the graph.

Public — read-only — RBAC: admin / write / read.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text as sa_text

from z3rno_server.dependencies import DbSession
from z3rno_server.middleware.rbac import require_role
from z3rno_server.schemas.graph import (
    GraphDataResponse,
    GraphEdge,
    GraphNode,
    GraphScope,
)
from z3rno_server.schemas.shared import ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/graph", tags=["graph"])


_DEFAULT_LIMIT = 200
_MAX_LIMIT = 5000


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


@router.get(
    "/data",
    response_model=GraphDataResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
    summary="Fetch a Memo subgraph for the /graph viewer",
)
async def get_graph_data(
    request: Request,
    db: DbSession,
    _rbac: None = require_role("admin", "write", "read"),
    dataset_id: Annotated[UUID | None, Query()] = None,
    agent_id: Annotated[UUID | None, Query()] = None,
    memo_type: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
) -> GraphDataResponse:
    """Return nodes + edges for the requested scope.

    At least one of ``dataset_id`` / ``agent_id`` must be supplied so
    callers can't paint the entire org's graph in a single request.
    """
    if dataset_id is None and agent_id is None:
        raise HTTPException(
            status_code=400,
            detail="provide at least one of dataset_id or agent_id",
        )

    _ = _get_org_id(request)  # asserts auth + activates RLS for this session
    conn = await db.connection()

    # --- 1. nodes ---------------------------------------------------------
    where_parts = ["valid_to IS NULL", "deleted_at IS NULL"]
    params: dict[str, object] = {"limit": limit}
    if dataset_id is not None:
        where_parts.append("dataset_id = CAST(:dataset_id AS uuid)")
        params["dataset_id"] = str(dataset_id)
    if agent_id is not None:
        where_parts.append("agent_id = CAST(:agent_id AS uuid)")
        params["agent_id"] = str(agent_id)
    if memo_type is not None:
        where_parts.append("memo_type = :memo_type")
        params["memo_type"] = memo_type

    where_sql = " AND ".join(where_parts)
    node_rows = (
        await conn.execute(
            sa_text(f"""
                SELECT id, memo_type, content, importance_score, metadata
                FROM public.memories
                WHERE {where_sql}
                ORDER BY importance_score DESC, created_at DESC
                LIMIT :limit
            """),  # noqa: S608 — interpolated identifiers are constant whitelist
            params,
        )
    ).fetchall()

    nodes = [
        GraphNode(
            id=row[0],
            memo_type=row[1],
            content=row[2] or "",
            importance_score=float(row[3]) if row[3] is not None else 0.0,
            metadata=row[4] if row[4] else {},
        )
        for row in node_rows
    ]

    # --- 2. edges between the returned nodes ------------------------------
    edges: list[GraphEdge] = []
    if nodes:
        node_ids = [str(n.id) for n in nodes]
        edge_rows = (
            await conn.execute(
                sa_text("""
                    SELECT id, source_memory_id, target_memory_id,
                           relationship_type::text, weight, metadata
                    FROM public.memory_relationships
                    WHERE source_memory_id = ANY(CAST(:ids AS uuid[]))
                      AND target_memory_id = ANY(CAST(:ids AS uuid[]))
                """),
                {"ids": node_ids},
            )
        ).fetchall()
        edges = [
            GraphEdge(
                id=row[0],
                source=row[1],
                target=row[2],
                predicate=str(row[3]),
                weight=float(row[4]) if row[4] is not None else 1.0,
                metadata=row[5] if row[5] else {},
            )
            for row in edge_rows
        ]

    return GraphDataResponse(
        nodes=nodes,
        edges=edges,
        scope=GraphScope(
            dataset_id=dataset_id,
            agent_id=agent_id,
            memo_type=memo_type,
        ),
        truncated=len(nodes) >= limit,
    )
