"""Seed the dev DB with a tenant + agents matching the dev-bypass UUIDs.

The auth middleware's dev bypass attaches Z3RNO_DEV_ORG_ID to every request
authenticated with Z3RNO_API_KEY. For engine.store/recall to succeed, that
org_id must exist in the tenants table and at least one agent must exist
under it.

Idempotent: running twice is a no-op.

Usage:
    DATABASE_URL=postgresql+psycopg://z3rno:z3rno_dev_password@localhost:5432/z3rno \\
        uv run python -m loadtest.seed_loadtest
"""

from __future__ import annotations

import os
import sys
from uuid import UUID

from sqlalchemy import create_engine, text

DEV_ORG_ID = UUID(os.environ.get("Z3RNO_DEV_ORG_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
AGENT_IDS = [
    UUID("11111111-1111-1111-1111-111111111111"),
    UUID("22222222-2222-2222-2222-222222222222"),
    UUID("33333333-3333-3333-3333-333333333333"),
]


def main() -> int:
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://z3rno:z3rno_dev_password@localhost:5432/z3rno",
    )
    if "asyncpg" in db_url:
        db_url = db_url.replace("+asyncpg", "+psycopg")

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tenants (org_id, name, plan_tier, settings)
                VALUES (:org_id, 'Loadtest Tenant', 'pro', '{}'::jsonb)
                ON CONFLICT (org_id) DO NOTHING
                """
            ),
            {"org_id": str(DEV_ORG_ID)},
        )

        for i, agent_id in enumerate(AGENT_IDS, start=1):
            conn.execute(
                text(
                    """
                    INSERT INTO agents (id, org_id, external_id, name, agent_metadata)
                    VALUES (:id, :org_id, :external_id, :name, '{}'::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": str(agent_id),
                    "org_id": str(DEV_ORG_ID),
                    "external_id": f"loadtest-agent-{i}",
                    "name": f"Loadtest Agent {i}",
                },
            )

    print(f"Seeded org {DEV_ORG_ID} with {len(AGENT_IDS)} agents.")
    print("Set in your shell:")
    print(
        f"  export LOAD_AGENT_IDS={','.join(str(a) for a in AGENT_IDS)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
