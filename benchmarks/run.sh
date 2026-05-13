#!/usr/bin/env bash
# One-shot wrapper: stand up the dev compose stack with the harness
# override applied, seed N memories, run the load ramp, save results.
#
# Defaults assume you're running this from z3rno-server with the bundled
# docker-compose.dev.yml. Override STACK_DIR for a different stack location.
#
# Requirements:
#   * docker compose
#   * Python 3 with the z3rno SDK installed (`pip install z3rno` or a venv;
#     point $PY at it)
#   * Postgres container reachable as ``z3rno-pg`` (the dev compose default)
#
# Usage:
#   ./benchmarks/run.sh [results_dir]
#
# Env overrides:
#   STACK_DIR    Stack root (default: repo root, where docker-compose.dev.yml lives)
#   PY           Python interpreter (default: python3)
#   SEED_N       Memories to seed (default: 200)
set -euo pipefail

HARNESS_DIR=$(cd "$(dirname "$0")" && pwd)
STACK_DIR=${STACK_DIR:-$(cd "$HARNESS_DIR/.." && pwd)}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.dev.yml}
PY=${PY:-python3}
RESULTS_DIR=${1:-$HARNESS_DIR/last-run}
SEED_N=${SEED_N:-200}

mkdir -p "$RESULTS_DIR"

echo "--- stack: layering harness override on $STACK_DIR/$COMPOSE_FILE ---"
(
  cd "$STACK_DIR"
  docker compose -f "$COMPOSE_FILE" -f "$HARNESS_DIR/compose.override.yml" \
    down -v >/dev/null 2>&1 || true
  docker compose -f "$COMPOSE_FILE" -f "$HARNESS_DIR/compose.override.yml" \
    up -d >/dev/null
)

echo "--- wait for /v1/health ---"
for _ in $(seq 1 60); do
  curl -fsS http://localhost:8000/v1/health >/dev/null 2>&1 && { echo "up"; break; }
  sleep 2
done

echo "--- confirm rate_limit_enabled=false ---"
curl -fsS http://localhost:8000/v1/limits \
  -H "Authorization: Bearer z3rno_sk_user_test" | \
  "$PY" -c "import json,sys; b=json.load(sys.stdin); print('rate_limit_enabled=',b.get('rate_limit_enabled'))"

echo "--- seed $SEED_N memories ---"
"$PY" "$HARNESS_DIR/seed.py" "$SEED_N"

echo "--- run load ramp ---"
"$PY" "$HARNESS_DIR/load_ramp.py" "$RESULTS_DIR/load_ramp.json"

echo "--- done. results in $RESULTS_DIR ---"
ls -la "$RESULTS_DIR"
