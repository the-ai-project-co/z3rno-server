"""Locust load tests for z3rno-server.

Run headless:
    locust -f tests/load/locustfile.py --headless -u 100 -r 10 -t 60s

Run with web UI:
    locust -f tests/load/locustfile.py

Environment variables:
    Z3RNO_API_KEY   - API key for authentication (default: z3rno_sk_test_localdev)
    Z3RNO_HOST      - Target host (default: http://localhost:8000)
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, task


class Z3rnoUser(HttpUser):
    """Simulated user exercising z3rno-server memory endpoints."""

    wait_time = between(0.1, 0.5)
    host = os.environ.get("Z3RNO_HOST", "http://localhost:8000")

    def on_start(self) -> None:
        """Set up auth header and shared state for the user session."""
        api_key = os.environ.get("Z3RNO_API_KEY", "z3rno_sk_test_localdev")
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        # Track memory IDs created during the session for get/forget operations
        self.memory_ids: list[str] = []
        self.agent_id = str(uuid.uuid4())

    @task(3)
    def store_memory(self) -> None:
        """Store a new memory (weight 3)."""
        memory_types = ["episodic", "semantic", "procedural"]
        payload = {
            "agent_id": self.agent_id,
            "content": f"Load test memory {uuid.uuid4().hex[:12]}: The quick brown fox jumps over the lazy dog.",
            "memory_type": random.choice(memory_types),  # noqa: S311
            "metadata": {
                "source": "locust_load_test",
                "session_id": str(uuid.uuid4()),
            },
            "importance": round(random.uniform(0.1, 1.0), 2),  # noqa: S311
        }
        with self.client.post(
            "/v1/memories",
            json=payload,
            headers=self.headers,
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                try:
                    data = response.json()
                    memory_id = data.get("id")
                    if memory_id:
                        self.memory_ids.append(memory_id)
                except Exception:
                    pass
                response.success()
            else:
                response.failure(f"Store failed: {response.status_code}")

    @task(5)
    def recall_memories(self) -> None:
        """Recall memories by query (weight 5)."""
        queries = [
            "What did I learn about machine learning?",
            "How do I deploy the application?",
            "Important meeting notes from last week",
            "Configuration settings for production",
            "User feedback and bug reports",
        ]
        payload = {
            "agent_id": self.agent_id,
            "query": random.choice(queries),  # noqa: S311
            "top_k": random.choice([5, 10, 20]),  # noqa: S311
        }
        with self.client.post(
            "/v1/memories/recall",
            json=payload,
            headers=self.headers,
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Recall failed: {response.status_code}")

    @task(2)
    def get_memory(self) -> None:
        """Get a single memory by ID (weight 2)."""
        if not self.memory_ids:
            return
        memory_id = random.choice(self.memory_ids)  # noqa: S311
        with self.client.get(
            f"/v1/memories/{memory_id}",
            headers=self.headers,
            catch_response=True,
        ) as response:
            if response.status_code in (200, 404):
                # 404 is acceptable if memory was forgotten
                response.success()
            else:
                response.failure(f"Get failed: {response.status_code}")

    @task(1)
    def forget_memory(self) -> None:
        """Forget (delete) a memory (weight 1)."""
        if not self.memory_ids:
            return
        memory_id = self.memory_ids.pop()
        payload = {
            "agent_id": self.agent_id,
            "memory_id": memory_id,
            "reason": "load test cleanup",
        }
        with self.client.post(
            "/v1/memories/forget",
            json=payload,
            headers=self.headers,
            catch_response=True,
        ) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Forget failed: {response.status_code}")
