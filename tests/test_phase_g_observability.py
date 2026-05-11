"""Phase G slice 7 — observability setup tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from z3rno_server.observability import tracing as obs


def _settings(**overrides: object) -> MagicMock:
    s = MagicMock()
    s.otel_enabled = False
    s.otel_service_name = "z3rno-server"
    s.otel_environment = "test"
    s.otel_exporter_otlp_endpoint = ""
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _reset_module() -> None:
    obs._INITIALISED = False


def test_setup_no_op_when_disabled() -> None:
    _reset_module()
    app = MagicMock()
    obs.setup_observability(app, _settings(otel_enabled=False))
    # When the flag is off the helper returns without touching the
    # FastAPI app or initialising the SDK.
    assert obs._INITIALISED is False


def test_setup_initialises_when_enabled() -> None:
    _reset_module()
    app = MagicMock()
    fake_provider = MagicMock()
    fake_resource = MagicMock()
    fake_processor = MagicMock()
    fake_exporter = MagicMock()
    fake_instrumentor = MagicMock()

    with (
        patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": MagicMock(
                    OTLPSpanExporter=lambda **_: fake_exporter
                ),
                "opentelemetry.instrumentation.fastapi": MagicMock(
                    FastAPIInstrumentor=fake_instrumentor
                ),
                "opentelemetry.sdk.resources": MagicMock(
                    Resource=MagicMock(create=lambda *_args, **_kw: fake_resource)
                ),
                "opentelemetry.sdk.trace": MagicMock(
                    TracerProvider=lambda **_kw: fake_provider
                ),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=lambda *_args, **_kw: fake_processor
                ),
            },
        ),
        patch("opentelemetry.trace.set_tracer_provider"),
    ):
        obs.setup_observability(
            app,
            _settings(
                otel_enabled=True,
                otel_exporter_otlp_endpoint="otel-collector:4317",
            ),
        )
    assert obs._INITIALISED is True
    fake_instrumentor.instrument_app.assert_called_once_with(app)


def test_setup_handles_missing_otel_packages() -> None:
    """When OTEL is enabled but the SDK isn't installed, setup logs
    a warning and stays uninstrumented rather than crashing the app."""
    _reset_module()
    app = MagicMock()
    with patch.dict("sys.modules", {"opentelemetry": None}):
        obs.setup_observability(app, _settings(otel_enabled=True))
    assert obs._INITIALISED is False


def test_trace_span_is_noop_when_uninitialised() -> None:
    _reset_module()
    with obs.trace_span("test_op", k="v") as span:
        assert span is None


def test_setup_is_idempotent() -> None:
    """Calling setup twice doesn't reinitialise (avoids duplicate
    span processors when the app reloads in tests)."""
    _reset_module()
    obs._INITIALISED = True  # pretend already initialised
    app = MagicMock()
    obs.setup_observability(app, _settings(otel_enabled=True))
    # No exceptions, no second initialisation — the early return
    # path is taken.
    assert obs._INITIALISED is True
