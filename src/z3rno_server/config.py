"""Application configuration via pydantic-settings.

Loads from environment variables and .env files. All settings have
sensible defaults for local development.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Z3rno server configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno"
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # Valkey (accepts VALKEY_URL; falls back to REDIS_URL for backward compat)
    valkey_url: str = ""
    redis_url: str = "redis://localhost:6379/0"  # backward-compat fallback

    @property
    def effective_valkey_url(self) -> str:
        """Return VALKEY_URL if set, otherwise fall back to REDIS_URL."""
        return self.valkey_url or self.redis_url

    # Embedding
    embedding_model: str = "text-embedding-3-small"
    embedding_provider: str = "litellm"
    openai_api_key: str = ""

    # API
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    api_key_header: str = "X-API-Key"

    # Auth — dev bypass (local development only)
    z3rno_api_key: str = ""  # If set, this key bypasses DB verification
    z3rno_dev_org_id: str = ""  # Org ID to use with dev API key
    api_key_cache_ttl: int = 60  # Valkey cache TTL for verified API keys (seconds)

    # JWT authentication (dashboard users)
    jwt_secret_key: str = ""  # HMAC secret for JWT signing (required for JWT auth)
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60  # Token expiry in minutes

    # Rate limiting
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 10

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # Server
    server_host: str = "0.0.0.0"  # noqa: S104
    server_port: int = 8000
    debug: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    """Factory for settings (cached at module level)."""
    return Settings()
