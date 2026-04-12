"""Tests for application configuration (Settings)."""

from __future__ import annotations

from z3rno_server.config import Settings


def test_default_database_url() -> None:
    """Default database_url should point to local dev Postgres."""
    settings = Settings()
    assert "localhost" in settings.database_url
    assert "z3rno" in settings.database_url


def test_default_redis_url() -> None:
    """Default redis_url should point to localhost."""
    settings = Settings()
    assert settings.redis_url == "redis://localhost:6379/0"


def test_default_server_port() -> None:
    """Default server port is 8000."""
    settings = Settings()
    assert settings.server_port == 8000


def test_default_rate_limit() -> None:
    """Default rate limit is 60 per minute."""
    settings = Settings()
    assert settings.rate_limit_per_minute == 60
    assert settings.rate_limit_burst == 10


def test_default_log_level() -> None:
    """Default log level is INFO."""
    settings = Settings()
    assert settings.log_level == "INFO"


def test_cors_origin_list_parses_comma_separated() -> None:
    """cors_origin_list should split the comma-separated cors_origins string."""
    settings = Settings(cors_origins="http://a.com, http://b.com, http://c.com")
    result = settings.cors_origin_list
    assert result == ["http://a.com", "http://b.com", "http://c.com"]


def test_cors_origin_list_handles_single_origin() -> None:
    """cors_origin_list should work with a single origin (no commas)."""
    settings = Settings(cors_origins="http://only.com")
    assert settings.cors_origin_list == ["http://only.com"]


def test_cors_origin_list_strips_empty_entries() -> None:
    """cors_origin_list should skip empty entries from trailing commas."""
    settings = Settings(cors_origins="http://a.com,,http://b.com,")
    result = settings.cors_origin_list
    assert result == ["http://a.com", "http://b.com"]


def test_extra_env_vars_are_ignored() -> None:
    """Settings should accept (ignore) unknown fields due to extra='ignore'."""
    settings = Settings(totally_unknown_field="whatever")  # type: ignore[call-arg]
    assert not hasattr(settings, "totally_unknown_field")
