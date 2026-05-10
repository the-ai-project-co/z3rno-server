"""FastAPI application factory for z3rno-server."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from z3rno_server.api.api_keys import router as api_keys_router
from z3rno_server.api.audit import router as audit_router
from z3rno_server.api.health import router as health_router
from z3rno_server.api.limits import router as limits_router
from z3rno_server.api.memories import router as memories_router
from z3rno_server.api.sessions import router as sessions_router
from z3rno_server.api.worker import router as worker_router
from z3rno_server.config import get_settings
from z3rno_server.middleware.auth import AuthMiddleware
from z3rno_server.middleware.body_limit import BodyLimitMiddleware
from z3rno_server.middleware.logging import LoggingMiddleware
from z3rno_server.middleware.rate_limit import RateLimitMiddleware
from z3rno_server.middleware.request_id import RequestIdMiddleware


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Z3rno Memory API",
        description="AI Agent Memory Database — store, recall, forget, audit",
        version="0.5.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware (order matters — outermost first, innermost last)
    # Chain: request_id -> logging -> body_limit -> auth -> rate_limit -> route
    if settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(health_router)
    app.include_router(limits_router)
    app.include_router(memories_router)
    app.include_router(audit_router)
    app.include_router(sessions_router)
    app.include_router(api_keys_router)
    app.include_router(worker_router)

    # Phase A — gated. The /v1/distill router is only registered when the
    # operator opts in via DISTILL_ENABLED=true. With the flag off the
    # OpenAPI spec is byte-identical to pre-Phase-A and POST /v1/distill
    # returns a 404 from the FastAPI router (no surface exposed).
    if settings.distill_enabled:
        from z3rno_server.api.distill import router as distill_router  # noqa: PLC0415

        app.include_router(distill_router)

    # Phase B.1 — gated. /v1/ingest is registered only when
    # INGEST_ENABLED=true. /v1/datasets ships in the same opt-in surface
    # (Task 34); both are dormant by default.
    if settings.ingest_enabled:
        from z3rno_server.api.datasets import router as datasets_router  # noqa: PLC0415
        from z3rno_server.api.ingest import router as ingest_router  # noqa: PLC0415

        app.include_router(ingest_router)
        app.include_router(datasets_router)

        # Phase B.2 — /v1/ingest/search registers only when Tavily is configured.
        if settings.tavily_api_key:
            from z3rno_server.api.search import router as search_router  # noqa: PLC0415

            app.include_router(search_router)

    # Prometheus metrics — auto-instruments all endpoints with request count,
    # latency histograms, and error rates. Exposed at GET /metrics.
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    return app


app = create_app()
