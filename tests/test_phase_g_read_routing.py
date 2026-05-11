"""Phase G slice 1 — read-replica routing tests.

Pin the three branches of ``get_read_db``:

  1. ``database_read_url`` empty → routing disabled; falls back to
     ``get_db`` (primary).
  2. Replica configured + lag ≤ threshold → routes to replica.
  3. Replica configured + lag > threshold → falls back to primary.

Plus a basic check of ``_check_replica_lag`` so a misconfigured replica
(query throws) is treated as healthy rather than silently disabling
routing forever.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from z3rno_server.dependencies import (
    _check_replica_lag,
    _get_read_engine,
    _get_read_session_factory,
    _reset_engine_cache,
    get_read_db,
)


def _settings(read_url: str, *, lag_check: bool = True, threshold: float = 5.0) -> MagicMock:
    s = MagicMock()
    s.database_url = "postgresql+asyncpg://primary/db"
    s.database_read_url = read_url
    s.database_pool_size = 5
    s.database_max_overflow = 0
    s.debug = False
    s.read_replica_lag_check_enabled = lag_check
    s.read_replica_lag_threshold_seconds = threshold
    return s


# ---------------------------------------------------------------------------
# _get_read_engine: only constructed when database_read_url is set
# ---------------------------------------------------------------------------


def test_read_engine_none_when_url_empty() -> None:
    _reset_engine_cache()
    with patch(
        "z3rno_server.dependencies.get_settings",
        return_value=_settings(""),
    ):
        assert _get_read_engine() is None
        assert _get_read_session_factory() is None


def test_read_engine_constructed_when_url_set() -> None:
    _reset_engine_cache()
    with patch(
        "z3rno_server.dependencies.get_settings",
        return_value=_settings("postgresql+asyncpg://replica/db"),
    ):
        eng = _get_read_engine()
        assert eng is not None
        # Cached on second call.
        assert _get_read_engine() is eng
        # Factory mirrors the engine.
        assert _get_read_session_factory() is not None


# ---------------------------------------------------------------------------
# _check_replica_lag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_replica_lag_within_threshold() -> None:
    """Lag of 1.0s with a 5.0s threshold → healthy."""
    session = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = 1.0
    session.execute = AsyncMock(return_value=fake_result)
    assert await _check_replica_lag(session, threshold_seconds=5.0) is True


@pytest.mark.asyncio
async def test_check_replica_lag_over_threshold() -> None:
    """Lag of 10.0s with a 5.0s threshold → unhealthy."""
    session = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = 10.0
    session.execute = AsyncMock(return_value=fake_result)
    assert await _check_replica_lag(session, threshold_seconds=5.0) is False


@pytest.mark.asyncio
async def test_check_replica_lag_swallows_errors_as_healthy() -> None:
    """Query failure (no extension, permission denied, primary
    masquerading as replica) is treated as healthy so a misconfigured
    replica doesn't silently flip all traffic to primary."""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("permission denied"))
    assert await _check_replica_lag(session, threshold_seconds=5.0) is True


# ---------------------------------------------------------------------------
# get_read_db routing branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_read_db_falls_back_to_primary_when_disabled() -> None:
    """No DATABASE_READ_URL → defers entirely to ``get_db``."""
    _reset_engine_cache()
    request = MagicMock()

    primary_sentinel = SimpleNamespace(name="primary")

    async def fake_get_db(_req: object):  # type: ignore[no-untyped-def]
        yield primary_sentinel

    with (
        patch(
            "z3rno_server.dependencies.get_settings",
            return_value=_settings(""),
        ),
        patch(
            "z3rno_server.dependencies.get_db",
            fake_get_db,
        ),
    ):
        gen = get_read_db(request)
        session = await gen.__anext__()
        assert session is primary_sentinel
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_get_read_db_routes_to_replica_when_healthy() -> None:
    """Replica configured, lag within budget → yield the replica session."""
    _reset_engine_cache()
    request = MagicMock()
    request.state = SimpleNamespace()  # no org_id → skip RLS path

    replica_session = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = 0.5  # plenty of headroom
    replica_session.execute = AsyncMock(return_value=fake_result)
    replica_session.close = AsyncMock()

    class _CM:
        async def __aenter__(self_inner) -> object:  # noqa: N805
            return replica_session

        async def __aexit__(self_inner, *_args: object) -> None:  # noqa: N805
            return None

    factory = MagicMock(return_value=_CM())

    with (
        patch(
            "z3rno_server.dependencies.get_settings",
            return_value=_settings("postgresql+asyncpg://replica/db"),
        ),
        patch(
            "z3rno_server.dependencies._get_read_session_factory",
            return_value=factory,
        ),
    ):
        gen = get_read_db(request)
        session = await gen.__anext__()
        assert session is replica_session
        # Drive the generator to completion so the CM exits cleanly.
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_get_read_db_falls_back_when_replica_lagging() -> None:
    """Replica configured but lag exceeds threshold → primary path."""
    _reset_engine_cache()
    request = MagicMock()
    request.state = SimpleNamespace()

    replica_session = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = 30.0  # way over the 5s default
    replica_session.execute = AsyncMock(return_value=fake_result)
    replica_session.close = AsyncMock()

    class _CM:
        async def __aenter__(self_inner) -> object:  # noqa: N805
            return replica_session

        async def __aexit__(self_inner, *_args: object) -> None:  # noqa: N805
            return None

    factory = MagicMock(return_value=_CM())

    primary_sentinel = SimpleNamespace(name="primary-fallback")

    async def fake_get_db(_req: object):  # type: ignore[no-untyped-def]
        yield primary_sentinel

    with (
        patch(
            "z3rno_server.dependencies.get_settings",
            return_value=_settings("postgresql+asyncpg://replica/db"),
        ),
        patch(
            "z3rno_server.dependencies._get_read_session_factory",
            return_value=factory,
        ),
        patch(
            "z3rno_server.dependencies.get_db",
            fake_get_db,
        ),
    ):
        gen = get_read_db(request)
        session = await gen.__anext__()
        # Must yield the primary fallback, not the lagging replica.
        assert session is primary_sentinel
        replica_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_read_db_skips_lag_check_when_disabled() -> None:
    """READ_REPLICA_LAG_CHECK_ENABLED=false → skip the check entirely
    so the lag-query roundtrip cost is avoided for operators who trust
    their topology."""
    _reset_engine_cache()
    request = MagicMock()
    request.state = SimpleNamespace()

    replica_session = MagicMock()
    replica_session.execute = AsyncMock()
    replica_session.close = AsyncMock()

    class _CM:
        async def __aenter__(self_inner) -> object:  # noqa: N805
            return replica_session

        async def __aexit__(self_inner, *_args: object) -> None:  # noqa: N805
            return None

    factory = MagicMock(return_value=_CM())

    with (
        patch(
            "z3rno_server.dependencies.get_settings",
            return_value=_settings("postgresql+asyncpg://replica/db", lag_check=False),
        ),
        patch(
            "z3rno_server.dependencies._get_read_session_factory",
            return_value=factory,
        ),
    ):
        gen = get_read_db(request)
        session = await gen.__anext__()
        assert session is replica_session
        # Lag query must NOT have run.
        replica_session.execute.assert_not_called()
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
