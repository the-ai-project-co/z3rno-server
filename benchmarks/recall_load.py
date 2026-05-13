"""Long-hold recall load at fixed concurrency, per-request JSONL output.

Use this when you want to investigate latency tails — every request
emits its own line with wall-clock start/end timestamps so you can
correlate slow requests against a concurrently-running pg_sampler.sh.

CLI: python recall_load.py <concurrency> <hold_seconds> <out.jsonl>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime

from z3rno import AsyncZ3rnoClient

BASE = os.environ.get("Z3RNO_BENCH_BASE", "http://localhost:8000")
API_KEY = os.environ.get("Z3RNO_BENCH_KEY", "z3rno_sk_user_test")
AGENT = os.environ.get(
    "Z3RNO_BENCH_AGENT", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)


async def _worker(client, deadline, out_fh, worker_id):  # type: ignore[no-untyped-def]
    while time.perf_counter() < deadline:
        started_wall = datetime.now(UTC).isoformat()
        t0 = time.perf_counter()
        try:
            r = await client.recall(
                agent_id=AGENT, query="dashboard performance",
                top_k=5, strategy="VECTOR",
            )
            ok, n = True, len(r.results)
        except Exception as exc:  # noqa: BLE001
            ok, n = False, str(exc)[:80]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        ended_wall = datetime.now(UTC).isoformat()
        out_fh.write(json.dumps({
            "worker": worker_id,
            "started_wall": started_wall,
            "ended_wall": ended_wall,
            "elapsed_ms": round(elapsed_ms, 2),
            "ok": ok,
            "results": n if ok else None,
            "error": None if ok else n,
        }) + "\n")
        out_fh.flush()


async def main() -> None:
    concurrency = int(sys.argv[1])
    hold_s = int(sys.argv[2])
    out_path = sys.argv[3]

    deadline = time.perf_counter() + hold_s
    print(f"recall_load: c={concurrency} hold={hold_s}s out={out_path}",
          file=sys.stderr)

    with open(out_path, "w") as fh:
        async with AsyncZ3rnoClient(base_url=BASE, api_key=API_KEY) as client:
            tasks = [_worker(client, deadline, fh, i) for i in range(concurrency)]
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
