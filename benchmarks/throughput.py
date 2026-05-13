"""Throughput — sustained ops/sec for store + recall_VECTOR.

Spawns N asyncio workers, each running the verb for ``DURATION_S``
seconds, at each concurrency level in ``LEVELS``. Reports per-level
ops/sec, total ops, and latency percentiles. Where ``load_ramp.py``
covers short holds across many levels, ``throughput.py`` covers long
holds at higher concurrency — measures the sustained ceiling.

CLI: python throughput.py [out.json]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from _common import AGENT, API_KEY, BASE, check_rate_limit_off, summarize
from z3rno import AsyncZ3rnoClient

DURATION_S = int(os.environ.get("Z3RNO_BENCH_THROUGHPUT_S", "20"))
LEVELS = [int(x) for x in os.environ.get(
    "Z3RNO_BENCH_THROUGHPUT_LEVELS", "10,50,200"
).split(",")]


async def _worker(label: str, client: AsyncZ3rnoClient, duration_s: int) -> list[float]:
    samples: list[float] = []
    end = time.perf_counter() + duration_s
    while time.perf_counter() < end:
        t0 = time.perf_counter()
        try:
            if label == "store":
                await client.store(
                    agent_id=AGENT, content=f"throughput {uuid4()}",
                    memory_type="episodic",
                )
            else:
                await client.recall(
                    agent_id=AGENT, query="dashboard performance",
                    top_k=5, strategy="VECTOR",
                )
            samples.append((time.perf_counter() - t0) * 1000.0)
        except Exception:  # noqa: BLE001
            samples.append(-1.0)
    return samples


async def run_level(label: str, concurrency: int) -> dict:
    print(f"  {label} @ {concurrency}x for {DURATION_S}s...", file=sys.stderr)
    async with AsyncZ3rnoClient(base_url=BASE, api_key=API_KEY) as client:
        tasks = [_worker(label, client, DURATION_S) for _ in range(concurrency)]
        all_samples = await asyncio.gather(*tasks)
    flat = [s for ts in all_samples for s in ts]
    ok = [s for s in flat if s >= 0]
    failures = [s for s in flat if s < 0]
    return {
        "label": label,
        "concurrency": concurrency,
        "duration_s": DURATION_S,
        "total_ops": len(flat),
        "successes": len(ok),
        "failures": len(failures),
        "ops_per_sec": round(len(ok) / DURATION_S, 1),
        **summarize(ok),
    }


async def main() -> None:
    check_rate_limit_off()
    results: list[dict] = []
    for label in ("store", "recall"):
        for c in LEVELS:
            r = await run_level(label, c)
            results.append(r)
            print(
                f"    -> {r['successes']}/{r['total_ops']} ops, "
                f"{r['ops_per_sec']} ops/sec, p95 {r['p95_ms']} ms",
                file=sys.stderr,
            )

    out = {
        "ts_utc": time.strftime("%FT%TZ", time.gmtime()),
        "duration_per_level_s": DURATION_S,
        "levels": LEVELS,
        "base": BASE,
        "results": results,
    }
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("throughput.json")
    raw_path.write_text(json.dumps(out, indent=2))

    print()
    print("| Verb | Concurrent | ops/sec | p50 (ms) | p95 (ms) | p99 (ms) | failures |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        print(
            f"| `{r['label']}` | {r['concurrency']} | {r['ops_per_sec']} | "
            f"{r['p50_ms']:.1f} | {r['p95_ms']:.1f} | {r['p99_ms']:.1f} | "
            f"{r['failures']} |"
        )


if __name__ == "__main__":
    asyncio.run(main())
