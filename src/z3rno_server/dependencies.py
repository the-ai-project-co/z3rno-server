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
_read_engine = None
_read_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine(settings: Settings | None = None):  # type: ignore[no-untyped-def]
    """Lazily create the async primary engine."""
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
    """Lazily create the primary session factory."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def _get_read_engine(settings: Settings | None = None):  # type: ignore[no-untyped-def]
    """Phase G slice 1 — lazily create the read-replica engine.

    Returns ``None`` when ``database_read_url`` is empty (replica
    routing disabled). Callers fall back to the primary engine in
    that case.
    """
    global _read_engine  # noqa: PLW0603
    s = settings or get_settings()
    if not s.database_read_url:
        return None
    if _read_engine is None:
        _read_engine = create_async_engine(
            s.database_read_url,
            pool_size=s.database_pool_size,
            max_overflow=s.database_max_overflow,
            echo=s.debug,
        )
    return _read_engine


def _get_read_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Lazily create the read-replica session factory; ``None`` when
    routing is disabled."""
    global _read_session_factory  # noqa: PLW0603
    eng = _get_read_engine()
    if eng is None:
        return None
    if _read_session_factory is None:
        _read_session_factory = async_sessionmaker(
            eng,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _read_session_factory


def _reset_engine_cache() -> None:
    """Test-only — drop cached engines so a config flip is picked up."""
    global _engine, _session_factory, _read_engine, _read_session_factory  # noqa: PLW0603
    _engine = None
    _session_factory = None
    _read_engine = None
    _read_session_factory = None


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
        # Set RLS context if authenticated:
        # 1. Switch to the z3rno_app role so RLS policies are enforced
        #    (the connection pool connects as superuser z3rno which bypasses RLS)
        # 2. Set the session variable that RLS policies inspect
        org_id = getattr(request.state, "org_id", None)
        if org_id:
            await session.execute(text("SET LOCAL ROLE z3rno_app"))
            await session.execute(text(f"SET LOCAL app.current_org_id = '{org_id}'"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _check_replica_lag(session: AsyncSession, threshold_seconds: float) -> bool:
    """Return ``True`` if the replica is within the lag budget.

    ``pg_last_xact_replay_timestamp()`` is NULL on the primary, in
    which case ``COALESCE`` gives 0 — i.e. a primary masquerading as
    a replica always passes. Genuine replicas return the delta from
    last replayed commit to ``now()``. Query failure (extension
    missing, permission denied) is treated as healthy so a
    misconfigured replica doesn't silently route everything back to
    primary without telemetry.
    """
    try:
        result = await session.execute(
            text(
                "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())), 0)"
            )
        )
        lag = float(result.scalar() or 0)
    except Exception:
        return True
    return lag <= threshold_seconds


async def get_read_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Phase G slice 1 — yield a read-only session bound to the replica.

    Behavior:
      * ``database_read_url`` empty → falls through to ``get_db`` (primary).
      * Replica configured, lag check disabled → routes to replica.
      * Replica configured, lag ≤ threshold → routes to replica.
      * Replica configured, lag > threshold → falls back to primary
        so a lagging replica never serves stale reads.

    RLS context (org_id) is set the same way the primary path sets it,
    so the same policies apply to replica queries.
    """
    settings = get_settings()
    factory = _get_read_session_factory()
    if factory is None:
        # Routing disabled — defer entirely to the primary path.
        async for session in get_db(request):
            yield session
        return

    async with factory() as session:
        if settings.read_replica_lag_check_enabled and not await _check_replica_lag(
            session, settings.read_replica_lag_threshold_seconds
        ):
            # Replica is too lagged — close and fall back.
            await session.close()
            async for primary in get_db(request):
                yield primary
            return

        org_id = getattr(request.state, "org_id", None)
        if org_id:
            await session.execute(text("SET LOCAL ROLE z3rno_app"))
            await session.execute(text(f"SET LOCAL app.current_org_id = '{org_id}'"))
        try:
            yield session
            # Read-only path — no commit needed, but harmless if one slips in.
        except Exception:
            await session.rollback()
            raise


# Type alias for read-only request scopes — routes to the replica when
# DATABASE_READ_URL is set, otherwise transparently uses the primary.
ReadDbSession = Annotated[AsyncSession, Depends(get_read_db)]
