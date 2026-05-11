#!/usr/bin/env bash
# Phase 3 items 1 + 4 — 10k-concurrent stress (R/W/mixed) on a real cluster.
#
# Drives a distributed Locust master from this host against
# ``Z3RNO_BASE_URL`` with ``USERS`` concurrent virtual users spread
# across ``WORKERS`` Locust worker pods (defaults: 6 workers @ 1667
# users each → 10k).
#
# Usage:
#   bash run-stress.sh [USERS] [--tenants N]   # default USERS=10000
#
# Pass-criteria: see ../targets.json (rps_min / p95_max_ms / error_rate_max
# per scenario).

set -euo pipefail

USERS="${1:-10000}"
TENANTS=1
if [[ "${2:-}" == "--tenants" ]]; then
  TENANTS="${3:-1}"
fi

: "${Z3RNO_BASE_URL:?set Z3RNO_BASE_URL to the cluster ingress}"
: "${Z3RNO_API_KEY:?set Z3RNO_API_KEY}"
: "${AGENT_IDS:?set AGENT_IDS=uuid1,uuid2,... (≥ TENANTS values)}"

WORKERS="${LOCUST_WORKERS:-6}"
DURATION="${DURATION:-300s}"
SPAWN_RATE="${SPAWN_RATE:-50}"

REPORTS_DIR="$(cd "$(dirname "$0")" && pwd)/reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "${REPORTS_DIR}"

run_scenario() {
  local name="$1" locust_file="$2"
  local prefix="${REPORTS_DIR}/stress_${name}_${USERS}u_${STAMP}"
  echo
  echo "==> stress.${name}: users=${USERS} workers=${WORKERS} duration=${DURATION}"
  locust \
    --headless \
    --locustfile "$(dirname "$0")/../${locust_file}" \
    --host "${Z3RNO_BASE_URL}" \
    --users "${USERS}" \
    --spawn-rate "${SPAWN_RATE}" \
    --run-time "${DURATION}" \
    --processes "${WORKERS}" \
    --csv "${prefix}" \
    --html "${prefix}.html" \
    --only-summary
}

echo "Stress run: USERS=${USERS} TENANTS=${TENANTS}"
echo "Target host: ${Z3RNO_BASE_URL}"
echo "Reports → ${REPORTS_DIR}/stress_*_${STAMP}.{csv,html}"

# Three back-to-back scenarios — read, write, mixed. The existing
# locustfiles under z3rno-server/loadtest/ cover each profile.
run_scenario "read"  "locust_read_heavy.py"
run_scenario "write" "locust_write_only.py"
run_scenario "mixed" "locustfile.py"

# Threshold check
python3 "$(dirname "$0")/_check_targets.py" \
  --scenario "stress.10k_read"  --csv "${REPORTS_DIR}/stress_read_${USERS}u_${STAMP}_stats.csv" \
  --scenario "stress.10k_write" --csv "${REPORTS_DIR}/stress_write_${USERS}u_${STAMP}_stats.csv" \
  --scenario "stress.10k_mixed" --csv "${REPORTS_DIR}/stress_mixed_${USERS}u_${STAMP}_stats.csv"

echo
echo "stress run complete: ${REPORTS_DIR}/stress_*_${STAMP}.html"
