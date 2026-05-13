"""Forge ingest+distill burst.

Submits ``len(CORPUS)`` short text ingests in parallel; auto-distill
chains each. Times wall-clock + per-stage p50/p95 from job rows.
Bounded by LLM concurrency caps, so this is a *worst-realistic*
number, not a contrived single-shot ideal.

Requires:
  * ``INGEST_ENABLED=true`` and ``DISTILL_ENABLED=true`` on the server.
  * ``INGEST_AUTO_DISTILL=true`` (default) so each ingest chains.
  * ``OPENAI_API_KEY`` (or whichever provider via ``LLM_API_KEY``).

CLI: python forge_burst.py [out.json]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from _common import (
    AGENT,
    API_KEY,
    BASE,
    check_rate_limit_off,
    percentile,
)
from z3rno import Z3rnoClient

CORPUS = [
    "Alice Lovelace worked on the Analytical Engine with Charles Babbage in 1843.",
    "Mary Somerville mentored Ada Lovelace in mathematics during her teenage years.",
    "Charles Babbage designed the Difference Engine before the Analytical Engine.",
    "The Difference Engine was funded by the British government in 1822.",
    "Ada published the Notes on the Analytical Engine in 1843, including the first algorithm.",
    "George Boole's work on logic predated the development of digital computing.",
    "Alan Turing built on the theoretical foundations laid by Babbage and Boole.",
    "Grace Hopper wrote the first compiler in 1952, called the A-0 system.",
    "Margaret Hamilton coined the term 'software engineering' during the Apollo program.",
    "Frances Allen pioneered program optimization techniques at IBM.",
]


def main() -> None:
    check_rate_limit_off()
    client = Z3rnoClient(base_url=BASE, api_key=API_KEY, timeout=180.0)

    t0 = time.perf_counter()
    print(f"submitting {len(CORPUS)} ingests...", file=sys.stderr)
    jobs = [client.ingest_text(agent_id=AGENT, text=text) for text in CORPUS]
    submit_elapsed = time.perf_counter() - t0
    print(f"  all submitted in {submit_elapsed:.1f}s", file=sys.stderr)

    ingest_times: list[float] = []
    distill_times: list[float] = []
    distill_job_ids: list[str] = []

    for j in jobs:
        t_start = time.perf_counter()
        for _ in range(120):  # 6 min cap
            status = client.get_ingest_status(j.job_id)
            if status.status == "completed":
                ingest_times.append(time.perf_counter() - t_start)
                if status.distill_job_id:
                    distill_job_ids.append(str(status.distill_job_id))
                break
            if status.status == "failed":
                print(f"  ingest {j.job_id} FAILED: {status.error}", file=sys.stderr)
                break
            time.sleep(3)

    for did in distill_job_ids:
        t_start = time.perf_counter()
        for _ in range(120):
            s = client.get_distill_status(did)
            if s.status == "completed":
                distill_times.append(time.perf_counter() - t_start)
                break
            if s.status == "failed":
                print(f"  distill {did} FAILED: {s.error}", file=sys.stderr)
                break
            time.sleep(3)

    wall = time.perf_counter() - t0

    out = {
        "ts_utc": time.strftime("%FT%TZ", time.gmtime()),
        "corpus_size": len(CORPUS),
        "submit_elapsed_s": round(submit_elapsed, 2),
        "wall_elapsed_s": round(wall, 2),
        "ingest_times_s": [round(t, 2) for t in ingest_times],
        "distill_times_s": [round(t, 2) for t in distill_times],
        "ingest_p50_s": round(percentile(ingest_times, 50), 2),
        "ingest_p95_s": round(percentile(ingest_times, 95), 2),
        "distill_p50_s": round(percentile(distill_times, 50), 2),
        "distill_p95_s": round(percentile(distill_times, 95), 2),
        "ingest_done": len(ingest_times),
        "distill_done": len(distill_times),
        "base": BASE,
    }
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("forge_burst.json")
    raw_path.write_text(json.dumps(out, indent=2))

    print()
    print(f"Corpus: {len(CORPUS)} short biographical sentences")
    print(f"Submit time: {submit_elapsed:.1f}s")
    print(f"Wall-clock end-to-end: {wall:.1f}s")
    print()
    print(f"Ingest completion: {len(ingest_times)}/{len(jobs)}")
    print(f"  ingest p50: {out['ingest_p50_s']}s   p95: {out['ingest_p95_s']}s")
    print()
    print(f"Distill completion: {len(distill_times)}/{len(distill_job_ids)}")
    print(f"  distill p50: {out['distill_p50_s']}s   p95: {out['distill_p95_s']}s")


if __name__ == "__main__":
    main()
