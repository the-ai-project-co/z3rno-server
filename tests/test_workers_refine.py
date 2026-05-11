"""Unit test for the refine Celery task (Phase D slice 3).

The full pipeline path is covered by core's pipeline unit + the
integration tests; this only verifies that the worker self-rejects
when ``REFINE_ENABLED=false`` (the safety net for an operator who
flips the flag off while a beat-scheduled run is in flight).
"""

from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

from z3rno_server.workers.refine import refine_run


def test_refine_run_self_rejects_when_flag_off() -> None:
    os.environ.pop("REFINE_ENABLED", None)
    # get_settings() is cached — patch to force a fresh read.
    with patch("z3rno_server.workers.refine.get_settings") as m_settings:
        settings = m_settings.return_value
        settings.refine_enabled = False
        result = refine_run.run(
            job_id=str(uuid4()),
            org_id=str(uuid4()),
            dataset_id=None,
            trigger="api",
        )
    assert result == {
        "job_id": result["job_id"],
        "status": "rejected",
        "reason": "refine_disabled",
    }
