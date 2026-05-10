"""Locust load-test harness for z3rno-server v0.6.0.

Targets the synchronous hot endpoints. Asynchronous ingest/distill paths are
excluded — they enqueue to Valkey and the dev-compose worker is a placeholder.
For ingest/distill load tests, run against the prod-compose stack with a real
worker, or against a Helm deployment.

Run:
  cd z3rno-server/loadtest
  uvx locust -f locustfile.py --host http://localhost:8000 \\
      --users 500 --spawn-rate 50 --run-time 5m --headless \\
      --csv reports/run_500 --html reports/run_500.html

Tuning knobs (env vars):
  LOAD_API_KEY        Bearer token (default: z3rno_sk_test_localdev)
  LOAD_AGENT_IDS      Comma-separated UUIDs (set by seed_loadtest.py)
  LOAD_RECALL_TOP_K   default 10
"""

from __future__ import annotations

import os
import random
import string
import uuid

from locust import HttpUser, between, task


def _env_uuids(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [u for u in (s.strip() for s in raw.split(",")) if u]


_AGENT_IDS = _env_uuids("LOAD_AGENT_IDS") or [
    # Seeded by the server's docker-entrypoint.sh on first boot.
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
]
_API_KEY = os.environ.get("LOAD_API_KEY", "z3rno_sk_test_localdev")
_RECALL_TOP_K = int(os.environ.get("LOAD_RECALL_TOP_K", "10"))
_MEMORY_TYPES = ["working", "episodic", "semantic", "procedural"]


def _rand_text(min_words: int = 8, max_words: int = 40) -> str:
    n = random.randint(min_words, max_words)
    return " ".join(
        "".join(random.choices(string.ascii_lowercase, k=random.randint(3, 9)))
        for _ in range(n)
    )


class Z3rnoUser(HttpUser):
    """Realistic mix of synchronous endpoints.

    Weights mirror the expected traffic shape for a memory-heavy AI app:
    recall is the hot read path; store is the hot write path; audit and
    sessions ride along; health pings simulate orchestrator probes.
    """

    wait_time = between(0.05, 0.3)

    def on_start(self) -> None:
        self.client.headers.update(
            {
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            }
        )
        self._memory_pool: list[str] = []  # for forget mix later if added

    @task(weight=1)
    def health(self) -> None:
        self.client.get("/v1/health", name="GET /v1/health")

    @task(weight=40)
    def store(self) -> None:
        body = {
            "agent_id": random.choice(_AGENT_IDS),
            "content": _rand_text(),
            "memory_type": random.choice(_MEMORY_TYPES),
        }
        with self.client.post(
            "/v1/memories",
            json=body,
            name="POST /v1/memories",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                try:
                    mem_id = resp.json().get("id")
                    if mem_id and len(self._memory_pool) < 50:
                        self._memory_pool.append(mem_id)
                except ValueError:
                    pass
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}")

    @task(weight=50)
    def recall(self) -> None:
        body = {
            "agent_id": random.choice(_AGENT_IDS),
            "query": _rand_text(min_words=2, max_words=8),
            "top_k": _RECALL_TOP_K,
            "similarity_threshold": 0.0,
        }
        self.client.post(
            "/v1/memories/recall",
            json=body,
            name="POST /v1/memories/recall",
        )

    @task(weight=5)
    def audit(self) -> None:
        self.client.get(
            "/v1/audit?limit=50",
            name="GET /v1/audit?limit=50",
        )

    @task(weight=4)
    def session_start_end(self) -> None:
        body = {"agent_id": random.choice(_AGENT_IDS)}
        with self.client.post(
            "/v1/sessions",
            json=body,
            name="POST /v1/sessions",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201):
                resp.failure(f"unexpected status {resp.status_code}")
                return
            try:
                session_id = resp.json().get("id") or resp.json().get("session_id")
            except ValueError:
                session_id = None
            resp.success()
        if session_id:
            self.client.post(
                f"/v1/sessions/{session_id}/end",
                name="POST /v1/sessions/{id}/end",
            )

    # Random UUID fallback: if seeding hasn't run, agent_ids may not exist;
    # the server will return a domain error instead of a 5xx, so this is
    # still a useful negative-path stress signal.
    @staticmethod
    def _random_agent_id() -> str:
        return str(uuid.uuid4())
