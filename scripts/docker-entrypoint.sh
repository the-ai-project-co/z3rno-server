#!/bin/sh
# z3rno-server Docker entrypoint
# Runs migrations and seeds dev data on first startup, then starts uvicorn.

set -e

echo "z3rno-server: starting up..."

# Wait for PostgreSQL to be ready
until python -c "
import asyncio, asyncpg, os
async def check():
    url = os.environ.get('DATABASE_URL', '').replace('+asyncpg', '').replace('postgresql', 'postgres')
    if '+asyncpg' in os.environ.get('DATABASE_URL', ''):
        url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql')
    conn = await asyncpg.connect(url.replace('postgresql+asyncpg', 'postgresql').replace('+asyncpg', ''))
    await conn.close()
asyncio.run(check())
" 2>/dev/null; do
  echo "z3rno-server: waiting for PostgreSQL..."
  sleep 1
done

echo "z3rno-server: PostgreSQL is ready"

# Run migrations against the alembic tree shipped inside z3rno-core
# (v0.20.2+). Fails loud — a migration error is a real ship problem,
# not a "warning". Pre-v0.20.2 fallback shells out to alembic if the
# helper isn't available (older z3rno-core wheels).
python -c "
import os, sys

url = os.environ['DATABASE_URL'].replace('+asyncpg', '+psycopg')

try:
    from z3rno_core.alembic_helpers import upgrade_to_head
except ImportError:
    # z3rno-core <0.20.2 — fall back to the legacy subprocess path
    # (which requires alembic.ini + migrations/ on disk at the CWD).
    import subprocess
    print('z3rno-server: z3rno-core <0.20.2 detected, using legacy alembic subprocess')
    res = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
        env={**os.environ, 'DATABASE_URL': url},
    )
    sys.exit(res.returncode)

print('z3rno-server: running migrations via bundled alembic tree...')
upgrade_to_head(url)
print('z3rno-server: migrations applied')
"
echo "z3rno-server: database ready"

# Seed dev tenant if it doesn't exist (dev mode only)
if [ -n "$Z3RNO_DEV_ORG_ID" ]; then
  python -c "
import asyncio, asyncpg, os

async def seed():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(url)
    # Check if dev tenant exists
    row = await conn.fetchrow(
        'SELECT org_id FROM tenants WHERE org_id = \$1',
        os.environ['Z3RNO_DEV_ORG_ID']
    )
    if not row:
        await conn.execute(
            'INSERT INTO tenants (org_id, name, plan_tier) VALUES (\$1, \$2, \$3)',
            os.environ['Z3RNO_DEV_ORG_ID'], 'Development', 'pro'
        )
        await conn.execute(
            'INSERT INTO agents (id, org_id, name) VALUES (\$1, \$2, \$3)',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            os.environ['Z3RNO_DEV_ORG_ID'],
            'Default Dev Agent'
        )
        print('z3rno-server: dev tenant + agent seeded')
    else:
        print('z3rno-server: dev tenant already exists')
    await conn.close()

asyncio.run(seed())
" 2>/dev/null || echo "z3rno-server: dev seed skipped (non-critical)"
fi

echo "z3rno-server: starting uvicorn (workers=${UVICORN_WORKERS:-1})..."
exec uvicorn z3rno_server.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-1}"
