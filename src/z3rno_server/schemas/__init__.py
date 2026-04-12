"""Pydantic request/response schemas - the API contract.

These schemas generate the OpenAPI spec that both SDKs consume.
Field names use snake_case (Python convention) and are serialized
as camelCase via Pydantic model_config alias_generator.
"""

from __future__ import annotations

from z3rno_server.schemas.audit import AuditPageResponse, AuditQueryParams
from z3rno_server.schemas.memories import (
    ForgetRequest,
    ForgetResponse,
    MemoryResponse,
    RecallRequest,
    RecallResponse,
    RecallResultItem,
    RelationshipInput,
    StoreMemoryRequest,
)
from z3rno_server.schemas.shared import ErrorResponse, HealthResponse

__all__ = [
    "AuditPageResponse",
    "AuditQueryParams",
    "ErrorResponse",
    "ForgetRequest",
    "ForgetResponse",
    "HealthResponse",
    "MemoryResponse",
    "RecallRequest",
    "RecallResponse",
    "RecallResultItem",
    "RelationshipInput",
    "StoreMemoryRequest",
]
