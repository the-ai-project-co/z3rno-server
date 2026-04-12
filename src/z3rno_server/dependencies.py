"""FastAPI dependency injection - database sessions, auth context."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from z3rno_server.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Engine + session factory (module-level singletons)
# ---------------------------------------------------------------------------

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine(settings: Settings | None = None):  # type: ignore[no-untyped-def]
    """Lazily create the async engine."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        s = settings or get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_size=s.database_pool_size,
            max_overflow=s.database_max_overflow,
            echo=s.debug,
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazily create the session factory."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


# ---------------------------------------------------------------------------
# Request-scoped dependencies
# ---------------------------------------------------------------------------


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session with RLS org context set.

    Sets `SET LOCAL app.current_org_id` if the request has an org_id
    from the auth middleware, activating Row-Level Security.
    """
    factory = _get_session_factory()
    async with factory() as session:
        # Set RLS context if authenticated
        org_id = getattr(request.state, "org_id", None)
        if org_id:
            await session.execute(text(f"SET LOCAL app.current_org_id = '{org_id}'"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]
