#!/usr/bin/env bash
# Clean up everything spun up by the Phase 3 runbook.
# Deletes kind cluster (or your real cluster's z3rno namespace).
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-z3rno-smoke}"
NAMESPACE="${NAMESPACE:-z3rno-system}"

if [[ -n "${KUBECONFIG:-}" ]]; then
  echo "==> uninstalling z3rno from ${NAMESPACE}"
  helm uninstall z3rno -n "${NAMESPACE}" >/dev/null || true
  kubectl delete namespace "${NAMESPACE}" --wait=false >/dev/null || true
else
  echo "==> deleting kind cluster ${CLUSTER_NAME}"
  kind delete cluster --name "${CLUSTER_NAME}" >/dev/null || true
fi

# Locust workers are colocated with the runbook host, no extra cleanup
# unless you launched the distributed flavour — those are short-lived
# subprocesses and die with --run-time.

echo "Teardown complete. Reports under loadtest/cluster/reports/ are preserved."
