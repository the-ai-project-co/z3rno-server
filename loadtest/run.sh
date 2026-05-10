#!/usr/bin/env bash
# Drive Locust runs for z3rno-server stress testing.
#
# Usage:
#   ./run.sh sanity      # 60s, 100 users  — validates the stack
#   ./run.sh small       # 5m, 500 users
#   ./run.sh medium      # 5m, 1000 users
#   ./run.sh all         # sanity + small + medium, sequential
#
# Pre-reqs (run from z3rno-server/):
#   docker compose -f docker-compose.dev.yml \
#                  -f loadtest/docker-compose.loadtest.yml up -d --build
#   # (entrypoint runs migrations + seeds the dev tenant + agent automatically)
#   uv run python -m loadtest.seed_loadtest   # adds 3 extra agents (optional)

set -euo pipefail

HOST="${LOAD_HOST:-http://localhost:8000}"
SCENARIO="${1:-sanity}"
REPORTS="$(cd "$(dirname "$0")" && pwd)/reports"
mkdir -p "$REPORTS"

run_locust() {
    local name="$1" users="$2" rate="$3" duration="$4"
    local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
    local prefix="$REPORTS/${name}_${users}u_${stamp}"
    echo
    echo "=========================================================="
    echo " Scenario: $name | users=$users spawn=$rate duration=$duration"
    echo " Host:     $HOST"
    echo " Prefix:   $prefix"
    echo "=========================================================="
    uvx --with locust==2.31.8 locust \
        -f "$(dirname "$0")/locustfile.py" \
        --host "$HOST" \
        --users "$users" \
        --spawn-rate "$rate" \
        --run-time "$duration" \
        --headless \
        --csv "$prefix" \
        --html "${prefix}.html" \
        --only-summary
    echo "Wrote: ${prefix}_stats.csv, ${prefix}.html"
}

case "$SCENARIO" in
    sanity)  run_locust sanity   100   25  60s ;;
    small)   run_locust small    500   50  5m ;;
    medium)  run_locust medium  1000  100  5m ;;
    all)
        run_locust sanity   100   25  60s
        run_locust small    500   50  5m
        run_locust medium  1000  100  5m
        ;;
    *)
        echo "Unknown scenario: $SCENARIO"
        echo "Usage: $0 {sanity|small|medium|all}"
        exit 1
        ;;
esac

echo
echo "Done. Reports in: $REPORTS"
