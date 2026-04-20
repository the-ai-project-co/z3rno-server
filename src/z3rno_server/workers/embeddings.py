"""Celery tasks for async embedding generation.

When store() is called, the memory is saved without an embedding.
This task generates the embedding in the background and updates
the memory row.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from z3rno_server.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://z3rno:z3rno_dev_password@localhost:5432/z3rno",
)


def _get_async_engine() -> AsyncEngine:
    """Create a one-shot async engine for worker tasks."""
    return create_async_engine(DATABASE_URL, pool_size=2, max_overflow=0)


@celery_app.task(name="z3rno.generate_embedding", bind=True, max_retries=3)
def generate_embedding(
    self: Any,
    memory_id: str,
    content: str,
    model: str,
) -> dict[str, str | bool]:
    """Generate an embedding for a memory and update the row.

    Args:
        memory_id: UUID of the memory to embed.
        content: Text content to embed.
        model: Embedding model name (e.g. text-embedding-3-small).
    """
    from z3rno_core.engine.embedding import LiteLLMEmbeddingProvider

    async def _run() -> dict[str, str | bool]:
        engine = _get_async_engine()
        try:
            provider = LiteLLMEmbeddingProvider(model=model)
            embedding = await provider.embed_text(content)

            if not embedding:
                return {"success": False, "reason": "empty_embedding"}

            # Format embedding as pgvector literal
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

            async with AsyncSession(engine) as session:
                await session.execute(
                    text("""
                        UPDATE memories
                        SET embedding = CAST(:vec AS vector),
                            embedding_model = :model,
                            updated_at = now()
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {"vec": vec_str, "model": model, "id": memory_id},
                )
                await session.commit()

            return {"success": True, "memory_id": memory_id, "model": model}
        except Exception as exc:
            logger.warning("Embedding generation failed for %s: %s", memory_id, exc, exc_info=True)
            raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
        finally:
            await engine.dispose()

    return asyncio.run(_run())
