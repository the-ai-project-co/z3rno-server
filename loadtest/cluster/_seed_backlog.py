"""Pre-seed audit_log_pending with N rows so the backlog-catch-up
scenario (Phase 3 item 3) can measure drain rate from a known
starting state.

Invoked via ``kubectl exec`` from run-drainer.sh; expects
DATABASE_URL set in the pod env.
"""

from __future__ import annotations

import os
import sys

import psycopg


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set in pod env", file=sys.stderr)
        return 1

    print(f"Seeding {n} rows into audit_log_pending …", flush=True)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # INSERT … SELECT generate_series is the fastest path.
        cur.execute("""
            INSERT INTO audit_log_pending (
                pending_id, org_id, operation, agent_id, details, ts
            )
            SELECT
                gen_random_uuid(),
                (SELECT org_id FROM tenants LIMIT 1),
                'recall',
                gen_random_uuid(),
                '{"backlog_seed": true}'::jsonb,
                now()
            FROM generate_series(1, %s)
        """, (n,))
        conn.commit()
    print(f"Seeded {n} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
