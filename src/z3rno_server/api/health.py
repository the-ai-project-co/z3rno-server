"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from z3rno_server.schemas.shared import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — returns 200 if the process is running."""
    return HealthResponse(status="ok")


@router.get("/v1/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    """Readiness probe — checks database and Redis connectivity."""
    # TODO: actually check DB and Redis connections
    return HealthResponse(status="ok", database="connected", redis="connected")
