"""Celery application configuration.

Uses Valkey as both broker and result backend.
"""

from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "z3rno",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="z3rno",
    worker_prefetch_multiplier=1,
)

# Auto-discover tasks from the workers module
celery_app.autodiscover_tasks(["z3rno_server.workers"])
