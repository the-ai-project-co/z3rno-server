#!/usr/bin/env bash
# ============================================================================
# Z3rno Health Check Script
#
# Verifies that all services in the Z3rno stack are reachable and healthy.
#
# Usage:
#   ./scripts/healthcheck.sh
#
# Environment variables (all optional, sensible defaults for docker-compose):
#   POSTGRES_HOST       (default: localhost)
#   POSTGRES_PORT       (default: 5432)
#   POSTGRES_USER       (default: z3rno)
#   POSTGRES_PASSWORD   (default: z3rno_dev_password)
#   POSTGRES_DB         (default: z3rno)
#   SERVER_URL          (default: http://localhost:8000)
#   VALKEY_HOST         (default: localhost)
#   VALKEY_PORT         (default: 6379)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-z3rno}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-z3rno_dev_password}"
POSTGRES_DB="${POSTGRES_DB:-z3rno}"
SERVER_URL="${SERVER_URL:-http://localhost:8000}"
VALKEY_HOST="${VALKEY_HOST:-localhost}"
VALKEY_PORT="${VALKEY_PORT:-6379}"

PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
check_pass() {
  echo "  [PASS] $1"
  PASS=$((PASS + 1))
}

check_fail() {
  echo "  [FAIL] $1"
  FAIL=$((FAIL + 1))
}

# ---------------------------------------------------------------------------
# 1. PostgreSQL is reachable
# ---------------------------------------------------------------------------
echo ""
echo "=== PostgreSQL ==="
if PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1;" > /dev/null 2>&1; then
  check_pass "PostgreSQL is reachable at $POSTGRES_HOST:$POSTGRES_PORT"
else
  check_fail "Cannot connect to PostgreSQL at $POSTGRES_HOST:$POSTGRES_PORT"
fi

# ---------------------------------------------------------------------------
# 2. Required extensions are loaded
# ---------------------------------------------------------------------------
echo ""
echo "=== PostgreSQL Extensions ==="
REQUIRED_EXTENSIONS=("vector" "age" "pg_cron" "pgaudit")

for ext in "${REQUIRED_EXTENSIONS[@]}"; do
  if PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_extension WHERE extname = '$ext';" 2>/dev/null | grep -q 1; then
    check_pass "Extension '$ext' is loaded"
  else
    check_fail "Extension '$ext' is NOT loaded"
  fi
done

# ---------------------------------------------------------------------------
# 3. z3rno-server /v1/health returns 200
# ---------------------------------------------------------------------------
echo ""
echo "=== Z3rno Server ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${SERVER_URL}/v1/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  check_pass "/v1/health returned 200"
else
  check_fail "/v1/health returned $HTTP_CODE (expected 200)"
fi

# ---------------------------------------------------------------------------
# 4. z3rno-server /v1/ready returns 200
# ---------------------------------------------------------------------------
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${SERVER_URL}/v1/ready" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  check_pass "/v1/ready returned 200"
else
  check_fail "/v1/ready returned $HTTP_CODE (expected 200)"
fi

# ---------------------------------------------------------------------------
# 5. Valkey is reachable
# ---------------------------------------------------------------------------
echo ""
echo "=== Valkey ==="
if command -v valkey-cli > /dev/null 2>&1; then
  PONG=$(valkey-cli -h "$VALKEY_HOST" -p "$VALKEY_PORT" ping 2>/dev/null || echo "")
  if [ "$PONG" = "PONG" ]; then
    check_pass "Valkey is reachable at $VALKEY_HOST:$VALKEY_PORT"
  else
    check_fail "Valkey did not respond to PING at $VALKEY_HOST:$VALKEY_PORT"
  fi
elif command -v redis-cli > /dev/null 2>&1; then
  PONG=$(redis-cli -h "$VALKEY_HOST" -p "$VALKEY_PORT" ping 2>/dev/null || echo "")
  if [ "$PONG" = "PONG" ]; then
    check_pass "Valkey is reachable at $VALKEY_HOST:$VALKEY_PORT"
  else
    check_fail "Valkey did not respond to PING at $VALKEY_HOST:$VALKEY_PORT"
  fi
else
  check_fail "Neither redis-cli nor valkey-cli found in PATH"
fi

# ---------------------------------------------------------------------------
# 6. Migrations are up to date
# ---------------------------------------------------------------------------
echo ""
echo "=== Migrations ==="
# Check via the Alembic current vs head. If the server exposes a migration
# status endpoint, prefer that; otherwise try running alembic directly.
MIGRATION_STATUS=$(curl -s "${SERVER_URL}/v1/health" 2>/dev/null || echo "")
if echo "$MIGRATION_STATUS" | grep -qi '"migrations"' 2>/dev/null; then
  # Server reports migration status in health endpoint
  if echo "$MIGRATION_STATUS" | grep -qi '"up_to_date":\s*true\|"migrations_current":\s*true'; then
    check_pass "Migrations are up to date (reported by server)"
  else
    check_fail "Migrations may not be up to date (check server health response)"
  fi
elif command -v alembic > /dev/null 2>&1; then
  CURRENT=$(PGPASSWORD="$POSTGRES_PASSWORD" alembic -x "db_url=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" current 2>/dev/null || echo "error")
  HEAD=$(PGPASSWORD="$POSTGRES_PASSWORD" alembic -x "db_url=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" heads 2>/dev/null || echo "error")
  if [ "$CURRENT" != "error" ] && [ "$HEAD" != "error" ] && echo "$CURRENT" | grep -q "(head)"; then
    check_pass "Migrations are up to date (alembic current shows head)"
  else
    check_fail "Migrations may not be at head revision"
  fi
else
  # Fallback: check that the alembic_version table exists and has a row
  HAS_VERSION=$(PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM alembic_version;" 2>/dev/null || echo "0")
  if [ "$HAS_VERSION" -gt 0 ] 2>/dev/null; then
    check_pass "Alembic version table exists with $HAS_VERSION revision(s) (alembic CLI not available for full check)"
  else
    check_fail "No alembic_version table found or it is empty"
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
TOTAL=$((PASS + FAIL))
echo ""
echo "==========================================="
echo "  Results: $PASS/$TOTAL passed, $FAIL failed"
echo "==========================================="
echo ""

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi

exit 0
