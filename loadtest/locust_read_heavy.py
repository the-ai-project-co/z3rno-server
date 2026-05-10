"""Read-heavy variant: 95% recall, 5% health. No writes.

Used to measure the read ceiling independently of the per-org audit
hash chain that serializes writes within a single org.
"""

from __future__ import annotations

import os
import random
import string

from locust import HttpUser, between, task

_AGENT_ID = os.environ.get(
    "LOAD_AGENT_ID",
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
)
_API_KEY = os.environ.get("LOAD_API_KEY", "z3rno_sk_test_localdev")


def _rand_query() -> str:
    n = random.randint(2, 8)
    return " ".join(
        "".join(random.choices(string.ascii_lowercase, k=random.randint(3, 9)))
        for _ in range(n)
    )


class ReadOnlyUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self) -> None:
        self.client.headers.update(
            {
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            }
        )

    @task(weight=95)
    def recall(self) -> None:
        body = {
            "agent_id": _AGENT_ID,
            "query": _rand_query(),
            "top_k": 10,
            "similarity_threshold": 0.0,
        }
        self.client.post(
            "/v1/memories/recall",
            json=body,
            name="POST /v1/memories/recall",
        )

    @task(weight=5)
    def health(self) -> None:
        self.client.get("/v1/health", name="GET /v1/health")
