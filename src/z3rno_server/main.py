"""FastAPI application factory for z3rno-server."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from z3rno_server.api.api_keys import router as api_keys_router
from z3rno_server.api.audit import router as audit_router
from z3rno_server.api.health import router as health_router
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
        version="0.0.1",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware (order matters — outermost first, innermost last)
    # Chain: request_id -> logging -> body_limit -> auth -> rate_limit -> route
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
    app.include_router(memories_router)
    app.include_router(audit_router)
    app.include_router(sessions_router)
    app.include_router(api_keys_router)
    app.include_router(worker_router)

    # Prometheus metrics — auto-instruments all endpoints with request count,
    # latency histograms, and error rates. Exposed at GET /metrics.
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    return app


app = create_app()
