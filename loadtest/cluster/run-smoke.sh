#!/usr/bin/env bash
# Phase 3 item 5 — helm-on-cluster end-to-end smoke.
#
# Boots a kind cluster (or uses ${KUBECONFIG} when set), installs the
# Z3rno chart, waits for every pod ready, and probes /v1/health.
#
# Acceptance: every Deployment ready in ≤ 180s + GET /v1/health → 200.
# See ../targets.json:smoke.helm.

set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-z3rno-smoke}"
NAMESPACE="${NAMESPACE:-z3rno-system}"
CHART_DIR="${CHART_DIR:-$(cd "$(dirname "$0")/../../../../z3rno-helm/charts/z3rno" && pwd)}"
REPORTS_DIR="$(cd "$(dirname "$0")" && pwd)/reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
REPORT="${REPORTS_DIR}/smoke_${STAMP}.log"
mkdir -p "${REPORTS_DIR}"

exec > >(tee -a "${REPORT}") 2>&1

started_at=$(date +%s)

echo "==> Phase 3 item 5 smoke @ ${STAMP}"
echo "    cluster: ${CLUSTER_NAME}"
echo "    chart:   ${CHART_DIR}"

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "==> creating kind cluster ${CLUSTER_NAME}"
  kind create cluster --name "${CLUSTER_NAME}" --wait 60s 2>/dev/null || true
fi

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> helm dependency update + install"
helm dependency update "${CHART_DIR}" >/dev/null
helm upgrade --install z3rno "${CHART_DIR}" \
  --namespace "${NAMESPACE}" \
  --wait \
  --timeout 180s \
  --set valkey.persistence.enabled=false \
  --set secrets.databaseUrl="postgresql://z3rno:z3rno_dev_password@localhost:5432/z3rno"

echo "==> pod listing"
kubectl get pods -n "${NAMESPACE}" -o wide

echo "==> ready-check on every deployment"
for dep in $(kubectl get deploy -n "${NAMESPACE}" -o jsonpath='{.items[*].metadata.name}'); do
  kubectl wait --for=condition=available --timeout=120s -n "${NAMESPACE}" "deploy/${dep}"
done

now=$(date +%s)
elapsed=$((now - started_at))
echo "==> all deployments ready in ${elapsed}s"

# Threshold check
python3 -c "
import json, sys
with open('$(dirname "$0")/targets.json') as f:
    t = json.load(f)['smoke.helm']
if ${elapsed} > t['ready_within_seconds_max']:
    print(f'FAIL: ready in {${elapsed}}s > target {t[\"ready_within_seconds_max\"]}s', file=sys.stderr)
    sys.exit(1)
print(f'OK: ready in {${elapsed}}s (target ≤ {t[\"ready_within_seconds_max\"]}s)')
"

echo "==> probing /v1/health"
SERVER_POD=$(kubectl get pod -n "${NAMESPACE}" \
  -l app.kubernetes.io/component=server \
  -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n "${NAMESPACE}" "${SERVER_POD}" -- \
  curl -fsS -o /dev/null -w "health_status=%{http_code} health_time=%{time_total}s\n" \
  http://localhost:8000/v1/health

echo "==> teardown"
helm uninstall z3rno -n "${NAMESPACE}" >/dev/null || true
if [[ -z "${KUBECONFIG:-}" ]]; then
  kind delete cluster --name "${CLUSTER_NAME}" >/dev/null || true
fi

echo "==> smoke OK"
