"""FastAPI application factory for z3rno-server."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from z3rno_server.api.audit import router as audit_router
from z3rno_server.api.health import router as health_router
from z3rno_server.api.memories import router as memories_router
from z3rno_server.config import get_settings
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

    # Middleware (order matters — outermost first)
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

    return app


app = create_app()
