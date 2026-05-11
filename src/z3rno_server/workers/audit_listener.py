"""v0.20.2 — long-running NOTIFY listener that wakes the audit drain.

Deploys as a dedicated singleton pod alongside beat. The pod opens a
LISTEN connection on ``z3rno_audit_pending`` and, on every
notification, fires ``celery_app.send_task("z3rno.audit_drain")`` to
trigger an immediate drain.

The existing periodic-poll ``z3rno.audit_drain`` beat schedule stays
in place but moves to a much longer interval (60s default in the
chart) so a stuck listener can't grow the queue unbounded.

Entry point: ``z3rno-audit-listener`` (console_script). The
``Z3RNO_AUDIT_LISTEN_ENABLED=true`` env-gate controls registration on
import; with the flag off the script logs + exits cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from z3rno_core.engine.audit import listen_for_audit_pending
from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno",
    )


async def _main() -> None:
    stop_event = asyncio.Event()

    def _on_signal(*_: object) -> None:
        logger.info("audit_listener: received signal, draining + exiting")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal)

    async def _on_notify(payload: str | None) -> None:
        # Each notification fires one drain task. Celery's broker
        # collapses overlapping requests via the audit_drain task's
        # per-org advisory lock, so a burst of NOTIFYs doesn't
        # multiply the work — second arrival returns 0 immediately.
        logger.debug("audit_listener: notify payload=%s", payload)
        celery_app.send_task("z3rno.audit_drain")

    logger.info(
        "audit_listener: starting LISTEN on z3rno_audit_pending; broker=%s",
        celery_app.conf.broker_url,
    )
    await listen_for_audit_pending(
        _dsn(), on_notify=_on_notify, stop_event=stop_event
    )
    logger.info("audit_listener: exited cleanly")


def main() -> None:
    """Console_script entry point: ``z3rno-audit-listener``."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if os.environ.get("Z3RNO_AUDIT_LISTEN_ENABLED", "false").lower() != "true":
        logger.info(
            "audit_listener: Z3RNO_AUDIT_LISTEN_ENABLED!=true; exiting"
        )
        return
    asyncio.run(_main())


if __name__ == "__main__":
    main()
