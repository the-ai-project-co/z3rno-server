"""v0.20.3 — per-tenant budget management schemas.

Mirrors the field set on ``z3rno_core.usage.Budgets`` with one
addition: empty / zero means "inherit server default" everywhere.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TenantBudgetsRequest(BaseModel):
    """PUT body. Zero / missing → inherit server default."""

    model_config = ConfigDict(extra="forbid")

    daily_tokens: int = Field(default=0, ge=0)
    daily_llm_calls: int = Field(default=0, ge=0)
    daily_embeddings: int = Field(default=0, ge=0)
    monthly_tokens: int = Field(default=0, ge=0)
    monthly_llm_calls: int = Field(default=0, ge=0)
    monthly_embeddings: int = Field(default=0, ge=0)


class TenantBudgetsResponse(BaseModel):
    """GET / PUT response. Includes the resolved (after-merge) effective
    budget so dashboards can render "your cap" without doing the merge
    again client-side."""

    overrides: TenantBudgetsRequest
    effective: TenantBudgetsRequest
