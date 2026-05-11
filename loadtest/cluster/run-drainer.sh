#!/usr/bin/env bash
# Phase 3 items 2 + 3 (+ 6 when v0.20 lands) — audit drainer behaviour.
#
#   bash run-drainer.sh                        # sustained 1k+ RPS write for 10 min
#   bash run-drainer.sh --backlog 100000       # pre-seed pending, halt writes, measure drain
#   bash run-drainer.sh --notify               # v0.20+ NOTIFY/LISTEN drain
#
# What it does:
#   1. SSHes into one server pod via kubectl to read
#      ``SELECT COUNT(*) FROM audit_log_pending`` every 5s.
#   2. Drives writes at WRITE_RPS through the existing Locust
#      write-only profile.
#   3. Tracks pending-row high-water mark + drain-rate p50/p95.
#   4. Compares against ../targets.json drainer.sustained /
#      drainer.backlog and exits non-zero on miss.

set -euo pipefail

MODE="sustained"
BACKLOG=0
NOTIFY=0
while (( $# > 0 )); do
  case "$1" in
    --backlog) MODE="backlog"; BACKLOG="$2"; shift 2 ;;
    --notify)  MODE="notify"; NOTIFY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

: "${Z3RNO_BASE_URL:?}"
: "${Z3RNO_API_KEY:?}"
: "${AGENT_IDS:?}"
: "${KUBE_NAMESPACE:?set KUBE_NAMESPACE to the z3rno install namespace}"

WRITE_RPS="${WRITE_RPS:-1500}"
DURATION="${DURATION:-600s}"   # 10 min sustained
REPORTS_DIR="$(cd "$(dirname "$0")" && pwd)/reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "${REPORTS_DIR}"

LOG="${REPORTS_DIR}/drainer_${MODE}_${STAMP}.log"

watch_pending_loop() {
  # Poll the in-cluster Postgres via a server pod port-forward.
  while true; do
    local pending; pending=$(kubectl exec -n "${KUBE_NAMESPACE}" \
      deploy/$(kubectl get deploy -n "${KUBE_NAMESPACE}" -l app.kubernetes.io/component=server -o jsonpath='{.items[0].metadata.name}') \
      -- python3 -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    cur = conn.execute('SELECT COUNT(*) FROM audit_log_pending')
    print(cur.fetchone()[0])
" 2>/dev/null || echo "?")
    echo "$(date -u +%FT%TZ) pending=${pending}" >> "${LOG}"
    sleep 5
  done
}

case "${MODE}" in
  sustained)
    echo "==> sustained 1k+ RPS write @ ${WRITE_RPS} RPS for ${DURATION}"
    watch_pending_loop &
    WATCHER=$!
    trap "kill ${WATCHER} 2>/dev/null || true" EXIT

    locust --headless \
      --locustfile "$(dirname "$0")/../locust_write_only.py" \
      --host "${Z3RNO_BASE_URL}" \
      --users "$((WRITE_RPS * 2))" \
      --spawn-rate 50 \
      --run-time "${DURATION}" \
      --csv "${REPORTS_DIR}/drainer_write_${STAMP}" \
      --html "${REPORTS_DIR}/drainer_write_${STAMP}.html" \
      --only-summary
    kill "${WATCHER}" 2>/dev/null || true

    python3 "$(dirname "$0")/_check_targets.py" \
      --scenario "drainer.sustained" \
      --pending-log "${LOG}"
    ;;
  backlog)
    echo "==> pre-seeding ${BACKLOG} rows into audit_log_pending then halting writes"
    kubectl exec -n "${KUBE_NAMESPACE}" \
      deploy/$(kubectl get deploy -n "${KUBE_NAMESPACE}" -l app.kubernetes.io/component=server -o jsonpath='{.items[0].metadata.name}') \
      -- python3 "$(dirname "$0")/_seed_backlog.py" "${BACKLOG}"
    watch_pending_loop &
    WATCHER=$!
    trap "kill ${WATCHER} 2>/dev/null || true" EXIT
    sleep 600  # 10 min of drain-only
    kill "${WATCHER}" 2>/dev/null || true

    python3 "$(dirname "$0")/_check_targets.py" \
      --scenario "drainer.backlog" \
      --pending-log "${LOG}"
    ;;
  notify)
    echo "==> NOTIFY/LISTEN drain (server v0.20+)"
    echo "    Confirms NOTIFY-triggered drain wakes inside the latency budget."
    locust --headless \
      --locustfile "$(dirname "$0")/../locust_write_only.py" \
      --host "${Z3RNO_BASE_URL}" \
      --users 1000 --spawn-rate 100 --run-time 120s \
      --csv "${REPORTS_DIR}/drainer_notify_${STAMP}" \
      --only-summary
    python3 "$(dirname "$0")/_check_targets.py" \
      --scenario "drainer.notify_listen" \
      --csv "${REPORTS_DIR}/drainer_notify_${STAMP}_stats.csv"
    ;;
esac

echo
echo "drainer ${MODE} run complete: ${LOG}"
