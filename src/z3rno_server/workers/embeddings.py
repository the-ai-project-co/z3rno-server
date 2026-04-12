"""Celery tasks for async embedding generation.

When store() is called without waiting for embedding, a Celery task
is enqueued to generate the embedding in the background.
"""

from __future__ import annotations

from z3rno_server.workers.celery_app import celery_app


@celery_app.task(name="z3rno.generate_embedding")
def generate_embedding(memory_id: str, content: str, model: str) -> dict[str, str | bool]:
    """Generate an embedding for a memory and update the row.

    Args:
        memory_id: UUID of the memory to embed.
        content: Text content to embed.
        model: Embedding model name (e.g. text-embedding-3-small).
    """
    # TODO: call embedding provider, update memories.embedding column
    return {"success": False, "reason": "not_implemented"}
