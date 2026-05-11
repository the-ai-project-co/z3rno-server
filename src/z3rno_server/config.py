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

    # Phase G slice 1 — read-replica routing. When ``database_read_url``
    # is set, read-only GET endpoints route their SELECTs to the
    # replica. The replica's WAL replay lag is checked at session
    # acquire time; if it exceeds ``read_replica_lag_threshold_seconds``
    # the request falls back to the primary so a lagging replica can
    # never serve stale reads. Off by default (empty URL → all traffic
    # goes to primary, byte-identical to pre-G.1).
    database_read_url: str = ""
    read_replica_lag_check_enabled: bool = True
    read_replica_lag_threshold_seconds: float = 5.0

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

    # =========================================================================
    # Phase A — Forge pipeline (LLM-driven distillation).
    # All Phase A surfaces are dormant unless DISTILL_ENABLED=true.
    # When false, /v1/distill is not registered, no Celery task is enqueued,
    # and existing endpoints behave byte-identically to pre-Phase-A.
    # =========================================================================
    distill_enabled: bool = False

    # LLM Gateway — shared by extraction and summarization. Provider-agnostic
    # via LiteLLM; defaults to OpenAI gpt-4o-mini for cost / latency balance.
    llm_provider: str = "openai"  # openai | anthropic | gemini | bedrock | ollama
    llm_model: str = "openai/gpt-4o-mini"
    llm_api_key: str = ""  # falls back to OPENAI_API_KEY when LLM_PROVIDER=openai
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    # Structured-output framework selector.
    # "instructor" is the only supported value in Phase A; "baml" reserved.
    structured_output_framework: str = "instructor"

    # Forge pipeline tuning.
    distill_chunk_size: int = 1024  # tokens
    distill_chunk_overlap: int = 128  # tokens
    distill_max_concurrency: int = 4  # asyncio.Semaphore for per-job LLM calls
    distill_summary_style: str = "concise"  # concise | bullet | abstractive

    # Phase F slice 1 — when true, the Forge aborts the transaction if
    # it can't stamp distill_provenance + a matching distill audit-chain
    # entry on every Memo. Off by default; flip on for regulated tenants
    # who need the per-Memo audit guarantee.
    distill_provenance_required: bool = False

    # Phase F slice 4 — when true, AUTO recall asks the MemoryTierRouter
    # for one or more tiers and fans the delegate strategy out across
    # them. Off by default; per-request opt-in via tier_route in the
    # recall body always wins over this default.
    memory_tier_auto_route: bool = False

    # Phase F slice 2 — compliance-graded retrieval. When enabled, the
    # server applies a role-aware RedactionFilter to recall results
    # before they leave the box. Rules supplied via YAML file; an
    # empty path uses the built-in defaults (email, SSN, credit card,
    # US phone). Off by default — opt-in for regulated tenants.
    retrieval_redaction_enabled: bool = False
    retrieval_redaction_rules_path: str = ""

    # Phase F slice 5 — forget-with-proof. When enabled, every
    # successful forget() emits a Merkle-rooted, ed25519-signed
    # certificate row in ``forget_certificates`` and the
    # ``GET /v1/forget/{cert_id}`` endpoint is registered. The
    # signing key must be a PEM-encoded unencrypted Ed25519 private
    # key on disk; the matching ``signer_key_id`` is the rotation-
    # friendly label stamped on each cert.
    forget_proof_enabled: bool = False
    forget_proof_signing_key_path: str = ""
    forget_proof_signer_key_id: str = "default"

    # Phase F slice 6 — distributed workers. Pluggable backend
    # routing the Forge / Ingest / Refine Celery tasks. Celery
    # default keeps the existing Valkey-broker path; ``modal`` and
    # ``k8s_jobs`` light up only when the operator has installed
    # the matching SDK and configured the per-backend keys below.
    distributed_backend: str = "celery"
    modal_app_name: str = "z3rno"
    k8s_jobs_namespace: str = "z3rno"
    k8s_jobs_image: str = ""

    @property
    def effective_llm_api_key(self) -> str:
        """Return LLM_API_KEY if set, otherwise fall back to OPENAI_API_KEY.

        Lets operators reuse a single OPENAI_API_KEY for both embeddings and
        the Forge LLM Gateway when both target OpenAI.
        """
        return self.llm_api_key or self.openai_api_key

    # =========================================================================
    # Phase B.1 — Ingestion surface (loaders + datasets + /v1/ingest).
    # All Phase B.1 surfaces are dormant unless INGEST_ENABLED=true.
    # When false, /v1/ingest and /v1/datasets are not registered and the
    # ingest worker rejects messages without DB I/O — existing endpoints
    # behave byte-identically to pre-Phase-B.
    # =========================================================================
    ingest_enabled: bool = False

    # Storage backend for raw artifacts (uploaded files, fetched URLs).
    # Phase B.1 ships "local" only; Phase B.2 will add "s3".
    storage_backend: str = "local"
    storage_local_dir: str = "/var/lib/z3rno/artifacts"

    # Ingest tuning.
    ingest_max_file_bytes: int = 50 * 1024 * 1024  # 50 MB hard cap on uploads
    ingest_max_csv_rows: int = 10_000  # cap CSV row expansion to prevent runaway extraction
    ingest_default_chunk_size: int = 1024  # tokens; can be overridden per-request
    ingest_auto_distill: bool = True  # chain ingest -> Forge automatically

    # URL loader.
    url_fetch_timeout_seconds: float = 15.0
    url_allowed_schemes: str = "http,https"  # comma-separated allowlist

    @property
    def url_allowed_schemes_list(self) -> list[str]:
        """Parse comma-separated URL scheme allowlist."""
        return [s.strip().lower() for s in self.url_allowed_schemes.split(",") if s.strip()]

    # =========================================================================
    # Phase B.2 — Multimodal + S3 + Tavily (all default-dormant).
    # Each capability is independently gated; turning on multimodal does not
    # require S3, etc.
    # =========================================================================

    # --- Multimodal (image + audio) ---
    multimodal_enabled: bool = False
    # Phase B.2.1: provider dispatch. "litellm" (default) routes to OpenAI
    # vision + Whisper API; "local" runs CLIP + openai-whisper on-device
    # via z3rno-core[multimodal-local]; "stub" is a deterministic test
    # double. The `vision_model` / `audio_model` fields are reinterpreted
    # per provider — see :func:`get_multimodal_provider`.
    multimodal_provider: str = "litellm"  # litellm | local | stub
    multimodal_vision_model: str = "openai/gpt-4o-mini"
    multimodal_audio_model: str = "whisper-1"
    multimodal_api_key: str = ""  # falls back to OPENAI_API_KEY when empty
    multimodal_max_audio_bytes: int = 25 * 1024 * 1024  # OpenAI Whisper cap
    multimodal_max_image_bytes: int = 20 * 1024 * 1024

    @property
    def effective_multimodal_api_key(self) -> str:
        """Return MULTIMODAL_API_KEY if set, otherwise fall back to
        OPENAI_API_KEY. Lets operators reuse one key for embeddings,
        the Forge LLM Gateway, *and* multimodal calls."""
        return self.multimodal_api_key or self.openai_api_key

    # --- S3 storage backend (selected via STORAGE_BACKEND=s3) ---
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""  # leave empty for AWS S3; set for MinIO/etc
    s3_prefix: str = "z3rno"
    s3_access_key_id: str = ""  # leave empty to use the default AWS credential chain
    s3_secret_access_key: str = ""

    # --- Tavily web search (selected via TAVILY_API_KEY) ---
    tavily_api_key: str = ""
    tavily_search_depth: str = "basic"  # basic | advanced
    tavily_max_results: int = 5

    # --- URL loader: opt-in Playwright fallback for JS-rendered pages ---
    url_playwright_enabled: bool = False
    url_playwright_min_chars: int = 200  # threshold below which we fall back
    url_playwright_timeout_seconds: float = 30.0

    # =========================================================================
    # Phase D — Graph Intelligence (refine, ontology, feedback).
    # All Phase D surfaces are dormant unless REFINE_ENABLED=true.
    # When false, /v1/feedback is not registered and the eventual
    # refine() Celery task (slice 3) will self-reject.
    # =========================================================================
    refine_enabled: bool = False

    # Slice 3 will read these; declared now so the env-var surface stays
    # stable across slices and operators only edit config once.
    refine_schedule: str = "cron:0 */6 * * *"  # cron expression for beat scheduler
    feedback_weight_decay: float = 0.95  # exponential decay applied per refine cycle

    # --- Slice 4: ontology grounding ---
    # ``none`` (default) → skip resolver; Forge writes ontology_uri NULL.
    # ``rdflib`` → load OWL/TTL from ONTOLOGY_FILE_PATH via the rdflib
    # extra and resolve every distilled Entity to a canonical URI.
    ontology_resolver: str = "none"  # none | rdflib
    ontology_file_path: str = ""
    ontology_matching_strategy: str = "fuzzy"  # exact | fuzzy
    ontology_fuzzy_threshold: float = 0.80

    # --- Slice 4: refine infer + summarize stages ---
    # Both are opt-in; default off so a flag-on REFINE_ENABLED tenant
    # without an LLM key still gets dedupe + reweight + prune.
    refine_infer_enabled: bool = False
    refine_summarize_enabled: bool = False
    refine_infer_max_candidates: int = 50  # cap LLM calls per cycle

    # --- Slice 5: code-graph extraction ---
    # When enabled, every ingest of a code source also runs the
    # tree-sitter extractor and writes function/class/import/call
    # Memos + edges. Requires `pip install 'z3rno-core[codegraph]'`.
    codegraph_enabled: bool = False
    codegraph_languages: str = "python,typescript"

    @property
    def codegraph_languages_list(self) -> list[str]:
        return [s.strip().lower() for s in self.codegraph_languages.split(",") if s.strip()]

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
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 10

    # Phase C.4: gate for the raw-Cypher retrieval strategy. With this
    # disabled (default), strategy="CYPHER" returns 403 from the recall
    # endpoint — the strategy exists but the operator has chosen not
    # to expose it. Enable per deployment if you trust the caller path.
    allow_cypher_query: bool = False

    # Celery queue-depth backpressure — when the broker has more than this
    # many pending tasks, /v1/ingest endpoints return 503 + Retry-After so
    # clients back off rather than piling more onto a saturated worker.
    # Set to 0 to disable backpressure entirely.
    celery_queue_depth_threshold: int = 1000
    # Retry-After value returned with the 503. A short value (seconds, not
    # minutes) since most ingest workloads drain quickly.
    celery_queue_depth_retry_after_seconds: int = 30

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
