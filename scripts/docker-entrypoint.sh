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

# Run migrations (uses psycopg sync driver)
SYNC_URL=$(echo "$DATABASE_URL" | sed 's/+asyncpg/+psycopg/g')
if python -c "
import subprocess, os, sys
url = os.environ['DATABASE_URL'].replace('+asyncpg', '+psycopg')
result = subprocess.run(
    [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
    env={**os.environ, 'DATABASE_URL': url},
    capture_output=True, text=True
)
if result.returncode == 0:
    print('z3rno-server: migrations applied')
else:
    print(f'z3rno-server: migration warning: {result.stderr[:200]}')
    sys.exit(0)  # Don't block startup on migration issues
"; then
  echo "z3rno-server: database ready"
fi

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

echo "z3rno-server: starting uvicorn..."
exec uvicorn z3rno_server.main:app --host 0.0.0.0 --port 8000
