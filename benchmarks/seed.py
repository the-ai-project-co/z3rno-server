"""Seed N memories so recall benches have something to retrieve.

CLI: python seed.py [N=200] [BASE=http://localhost:8000] [API_KEY=z3rno_sk_user_test]
"""
from __future__ import annotations

import os
import sys

from z3rno import Z3rnoClient

BASE = os.environ.get("Z3RNO_BENCH_BASE", "http://localhost:8000")
API_KEY = os.environ.get("Z3RNO_BENCH_KEY", "z3rno_sk_user_test")
AGENT = os.environ.get(
    "Z3RNO_BENCH_AGENT", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)

PHRASES = [
    "dashboard performance metric",
    "user session timeout",
    "graphql query optimization",
    "redis cache invalidation",
    "embedding model latency",
]


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    c = Z3rnoClient(base_url=BASE, api_key=API_KEY)
    for i in range(n):
        c.store(
            agent_id=AGENT,
            content=f"{PHRASES[i % len(PHRASES)]} item {i}",
            memory_type="semantic",
            importance=0.5,
        )
    print(f"seeded {n} memories at {BASE}")


if __name__ == "__main__":
    main()
