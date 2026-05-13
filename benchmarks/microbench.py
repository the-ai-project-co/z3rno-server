"""Microbenchmark — latency by verb + strategy at concurrency=1.

Per-op p50/p95/p99 across N iterations against a warm local stack. No
concurrency — measures single-shot latency, not throughput. Writes a
JSON blob to the path given on the CLI (default ``microbench.json``)
+ a markdown table to stdout.

CLI: python microbench.py [out.json]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from _common import AGENT, API_KEY, BASE, check_rate_limit_off, summarize
from z3rno import Z3rnoClient

N = int(__import__("os").environ.get("Z3RNO_BENCH_N", "200"))


def bench(label: str, fn, n: int = N, warmup: int = 5) -> dict:  # type: ignore[no-untyped-def]
    """Run ``fn`` N times, capturing per-op latency.

    Destructive ops (e.g. forget) consume test data on every call; callers
    pass ``warmup=0`` so the timed iterations don't run dry.
    """
    print(f"  warming up {label} ({warmup}x)...", file=sys.stderr)
    warm_fail = 0
    for _ in range(warmup):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            warm_fail += 1
            print(f"    warm-up failure: {type(e).__name__}: {e}", file=sys.stderr)
    if warmup and warm_fail == warmup:
        return {"label": label, "n": 0, "failures": -1,
                "note": "all warm-ups failed; skipped", **summarize([])}

    print(f"  {n}x {label}...", file=sys.stderr)
    samples: list[float] = []
    failures = 0
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001
            failures += 1
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {
        "label": label,
        "n": n,
        "failures": failures,
        **summarize(samples),
    }


def main() -> None:
    check_rate_limit_off()
    client = Z3rnoClient(base_url=BASE, api_key=API_KEY)

    # Seed a small corpus so recall has something to retrieve.
    seed_contents = [
        "Alice prefers dark mode and weekly digest emails",
        "Bob asked about the dashboard performance last week",
        "The pricing tier on mobile is showing wrong info",
        "Export-to-CSV timed out for the Q3 report",
        "User wants to see invoice details in the sidebar",
        "Dashboard was slow this morning around 9am UTC",
        "Tier change from pro to enterprise on 2026-03-15",
        "Customer escalated billing dispute through support channel",
    ]
    seed_ids: list[str] = []
    for content in seed_contents:
        m = client.store(agent_id=AGENT, content=content, memory_type="episodic")
        seed_ids.append(str(m.id))

    results: list[dict] = []
    results.append(
        bench(
            "store",
            lambda: client.store(
                agent_id=AGENT, content=f"bench memo {uuid4()}",
                memory_type="episodic",
            ),
        )
    )
    for strategy in ("VECTOR", "LEXICAL", "AUTO"):
        results.append(
            bench(
                f"recall_{strategy}",
                lambda s=strategy: client.recall(
                    agent_id=AGENT, query="dashboard performance",
                    top_k=5, strategy=s,
                ),
            )
        )
    results.append(bench("audit", lambda: client.audit(agent_id=AGENT, page_size=20)))
    if seed_ids:
        target = seed_ids[0]
        results.append(bench("get_memory", lambda: client.get_memory(target)))

    conv = client.create_conversation(agent_id=AGENT, title="bench")
    cid = str(conv.id)

    def _store_and_turn() -> None:
        m = client.store(
            agent_id=AGENT, content=f"turn {uuid4()}", memory_type="episodic"
        )
        client.add_turn(cid, memory_id=str(m.id), turn_role="user")

    results.append(bench("store_and_turn", _store_and_turn, n=N // 2))

    def _session_pair() -> None:
        s = client.start_session(agent_id=AGENT)
        client.end_session(s.session_id)

    results.append(bench("session_open_close", _session_pair, n=50))

    # Forget is destructive — one memo per call. warmup=0 so the timed
    # iterations have a memo to consume.
    delete_ids = [
        client.store(agent_id=AGENT, content=f"will-be-forgotten {uuid4()}").id
        for _ in range(100)
    ]
    idx = [0]

    def _forget() -> None:
        client.forget(agent_id=AGENT, memory_id=str(delete_ids[idx[0]]))
        idx[0] += 1

    results.append(bench("forget", _forget, n=len(delete_ids), warmup=0))

    out = {
        "ts_utc": time.strftime("%FT%TZ", time.gmtime()),
        "n_iters": N,
        "base": BASE,
        "results": results,
    }
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("microbench.json")
    raw_path.write_text(json.dumps(out, indent=2))

    print()
    print("| Verb | p50 (ms) | p95 (ms) | p99 (ms) | min | max | failures |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        print(
            f"| `{r['label']}` | {r['p50_ms']:.1f} | {r['p95_ms']:.1f} | "
            f"{r['p99_ms']:.1f} | {r['min_ms']:.1f} | {r['max_ms']:.1f} | "
            f"{r['failures']} |"
        )


if __name__ == "__main__":
    main()
