"""Prometheus metric definitions for z3rno-server.

Exposing application-level metrics (not just HTTP request counters) so
operators can alert on real workload signals: ingest job churn, audit
drain queue depth, etc. The Prometheus client registry is process-global
— defining a metric here registers it on the default registry, which
``prometheus-fastapi-instrumentator`` exposes at ``GET /metrics``.

Importing this module is intentionally side-effect-only.
"""

from z3rno_server.observability.ingest_metrics import (
    INGEST_JOBS_TOTAL,
    INGEST_PIPELINE_DURATION_SECONDS,
    record_ingest_outcome,
)
from z3rno_server.observability.tracing import setup_observability, trace_span

__all__ = [
    "INGEST_JOBS_TOTAL",
    "INGEST_PIPELINE_DURATION_SECONDS",
    "record_ingest_outcome",
    "setup_observability",
    "trace_span",
]
