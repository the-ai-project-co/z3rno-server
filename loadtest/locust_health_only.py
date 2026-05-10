"""Health-only — measures raw framework throughput (no auth, no DB)."""

from __future__ import annotations

from locust import HttpUser, between, task


class HealthUser(HttpUser):
    wait_time = between(0.0, 0.05)

    @task
    def health(self) -> None:
        self.client.get("/v1/health", name="GET /v1/health")
