"""Prometheus metrics for the ingest pipeline.

These supplement the per-route HTTP counters that
``prometheus-fastapi-instrumentator`` already provides. The HTTP layer
shows you "an ingest request arrived"; these show you "what happened
to that ingest after it was enqueued."

Metrics
-------
``z3rno_ingest_jobs_total{status, kind}``
    Counter incremented once per terminal state transition. Labels:

      * ``status``: ``enqueued | completed | failed | rejected``
      * ``kind``: ``text | url | file | s3_uri``

    Note that ``running`` is not a terminal state — the counter is
    incremented when the pipeline returns (or raises). Time-in-running
    is captured by the duration histogram.

``z3rno_ingest_pipeline_duration_seconds{kind, status}``
    Histogram of pipeline wall-clock duration. Labels match the
    counter. Buckets are tuned to typical Z3rno ingest workloads
    (sub-second to ~10 minutes).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Histogram

INGEST_JOBS_TOTAL = Counter(
    "z3rno_ingest_jobs_total",
    "Total ingest job terminal-state transitions, labelled by outcome and kind.",
    labelnames=("status", "kind"),
)

INGEST_PIPELINE_DURATION_SECONDS = Histogram(
    "z3rno_ingest_pipeline_duration_seconds",
    "End-to-end ingest pipeline duration in seconds.",
    labelnames=("kind", "status"),
    # Sub-second up to ~17 minutes. Most text/url ingests are < 5 s;
    # large PDF + S3 + auto-distill can run to a few minutes; pathological
    # cases hit the upper bucket and indicate a real problem.
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 180.0, 600.0, 1000.0),
)


@contextmanager
def record_ingest_outcome(kind: str) -> Iterator[dict[str, str]]:
    """Record terminal-state + duration for one ingest run.

    Usage in the worker::

        with record_ingest_outcome(kind=ingest_input.kind) as outcome:
            summary = await pipeline.run(...)
            outcome["status"] = "completed" if summary.status == "completed" else "failed"

    The context manager always emits ``z3rno_ingest_jobs_total`` and
    ``z3rno_ingest_pipeline_duration_seconds``. If the wrapped block
    raises before the caller sets ``outcome["status"]``, the outcome
    defaults to ``failed`` so we don't miss crashed-pipeline cases.
    """
    outcome: dict[str, str] = {"status": "failed"}
    start = time.perf_counter()
    try:
        yield outcome
    finally:
        elapsed = time.perf_counter() - start
        status = outcome.get("status", "failed")
        INGEST_JOBS_TOTAL.labels(status=status, kind=kind).inc()
        INGEST_PIPELINE_DURATION_SECONDS.labels(kind=kind, status=status).observe(elapsed)
