"""Phase G slice 7 — OpenTelemetry tracing setup.

Initialises an OTLP exporter + FastAPI auto-instrumentation when
``OTEL_ENABLED=true``. With the flag off, all OTel calls are
no-ops so a non-observability deploy pays no runtime cost.

Each of the seven Z3rno verbs (store / recall / forget / audit /
ingest / distill / refine) gets a span automatically via the
FastAPI auto-instrumentor — the route path is the span name. The
helper ``trace_span("verb")`` is provided for the few worker-side
flows that don't run inside a FastAPI request.

W3C trace context (``traceparent`` header) is propagated by default
so request traces stitch across server → worker → external LLM via
LiteLLM's HTTP layer.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager, suppress
from typing import Any

from fastapi import FastAPI

from z3rno_server.config import Settings

logger = logging.getLogger(__name__)

_INITIALISED = False


def setup_observability(app: FastAPI, settings: Settings) -> None:
    """One-shot init. Idempotent — repeat calls are a no-op."""
    global _INITIALISED  # noqa: PLW0603
    if _INITIALISED or not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
            FastAPIInstrumentor,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import (  # noqa: PLC0415
            BatchSpanProcessor,
        )
    except ImportError:
        logger.warning(
            "OTEL_ENABLED=true but OpenTelemetry packages aren't installed; "
            "tracing is disabled."
        )
        return

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name or "z3rno-server",
            "service.namespace": "z3rno",
            "deployment.environment": settings.otel_environment or "production",
        }
    )
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            )
        )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _INITIALISED = True
    logger.info(
        "OpenTelemetry tracing enabled (endpoint=%s)",
        settings.otel_exporter_otlp_endpoint or "<console>",
    )


@contextmanager
def trace_span(name: str, **attributes: Any) -> Any:
    """Manual span helper for worker-side flows.

    No-op when OTel isn't initialised so callers don't need to gate
    the call. Attributes are pushed onto the span; values are
    coerced to OTel-compatible types (str / int / float / bool).
    """
    if not _INITIALISED:
        yield None
        return
    try:
        from opentelemetry import trace  # noqa: PLC0415
    except ImportError:
        yield None
        return
    tracer = trace.get_tracer("z3rno.worker")
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            with suppress(Exception):
                span.set_attribute(k, v)
        yield span
