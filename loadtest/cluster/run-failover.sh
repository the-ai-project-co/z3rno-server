#!/usr/bin/env bash
# Phase 3 item 7 — cross-region failover / HA.
#
# Requires a multi-region z3rno deploy (helm chart 0.3.0+
# multiRegion.enabled=true; v0.19 chart 0.5.0 adds the
# CloudNativePG subchart for primary/replica). Uses the operator's
# DNS GTM (Route 53 / Cloudflare Load Balancer / GCLB) — the script
# only drives traffic and measures the RTO; it cannot trigger DNS
# failover from here.
#
# Acceptance: RTO ≤ 60s, error spike during cutover ≤ 0.5%.

set -euo pipefail

: "${Z3RNO_PRIMARY_REGION_URL:?set the primary region's endpoint}"
: "${Z3RNO_SECONDARY_REGION_URL:?set the secondary region's endpoint}"
: "${Z3RNO_GTM_URL:?set the GTM endpoint that clients actually call}"
: "${Z3RNO_API_KEY:?}"

REPORTS_DIR="$(cd "$(dirname "$0")" && pwd)/reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${REPORTS_DIR}/failover_${STAMP}.log"
mkdir -p "${REPORTS_DIR}"

echo "==> failover test starting @ ${STAMP}" | tee "${LOG}"
echo "    primary:   ${Z3RNO_PRIMARY_REGION_URL}"
echo "    secondary: ${Z3RNO_SECONDARY_REGION_URL}"
echo "    GTM:       ${Z3RNO_GTM_URL}"

echo "==> baseline: 100 req/s to GTM for 60s"
locust --headless \
  --locustfile "$(dirname "$0")/../locust_health_only.py" \
  --host "${Z3RNO_GTM_URL}" \
  --users 100 --spawn-rate 50 --run-time 60s \
  --csv "${REPORTS_DIR}/failover_baseline_${STAMP}" \
  --only-summary

echo
echo "*** MANUAL STEP ***"
echo "    Disable the primary region now. Two common ways:"
echo "      gcloud:  gcloud compute forwarding-rules delete <primary-lb>"
echo "      aws:     aws elbv2 modify-target-group --health-check-protocol HTTPS \\"
echo "                                              --health-check-path /v1/nope"
echo "      manual:  scale primary's z3rno-server deployment to 0 replicas"
echo
echo "    The GTM should reroute to ${Z3RNO_SECONDARY_REGION_URL} within RTO budget."
echo "    Press <enter> the instant you've triggered the failover."
read -r

CUTOVER_TS=$(date +%s)
echo "    cutover triggered @ $(date -u -r ${CUTOVER_TS} +%FT%TZ)" | tee -a "${LOG}"

echo "==> measuring RTO (max 120s window)"
locust --headless \
  --locustfile "$(dirname "$0")/../locust_health_only.py" \
  --host "${Z3RNO_GTM_URL}" \
  --users 100 --spawn-rate 100 --run-time 120s \
  --csv "${REPORTS_DIR}/failover_cutover_${STAMP}" \
  --only-summary

python3 "$(dirname "$0")/_check_targets.py" \
  --scenario "failover.cross_region" \
  --csv "${REPORTS_DIR}/failover_cutover_${STAMP}_stats.csv"

echo
echo "failover report → ${LOG}"
