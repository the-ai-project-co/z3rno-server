"""Write-only — measures the store path under concurrency.

Previously bottlenecked by the per-org audit hash chain. After the
v0.7.0 async-drain fix, store should scale up to the framework ceiling.
"""

from __future__ import annotations

import os
import random
import string

from locust import HttpUser, between, task

_AGENT_ID = os.environ.get(
    "LOAD_AGENT_ID", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)
_API_KEY = os.environ.get("LOAD_API_KEY", "z3rno_sk_test_localdev")
_TYPES = ["working", "episodic", "semantic", "procedural"]


def _rand_text() -> str:
    n = random.randint(8, 40)
    return " ".join(
        "".join(random.choices(string.ascii_lowercase, k=random.randint(3, 9)))
        for _ in range(n)
    )


class WriteOnlyUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self) -> None:
        self.client.headers.update(
            {
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            }
        )

    @task
    def store(self) -> None:
        body = {
            "agent_id": _AGENT_ID,
            "content": _rand_text(),
            "memory_type": random.choice(_TYPES),
        }
        self.client.post(
            "/v1/memories",
            json=body,
            name="POST /v1/memories",
        )
