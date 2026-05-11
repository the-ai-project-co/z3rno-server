#!/usr/bin/env bash
# Phase 3 item 8 — AUTO routing accuracy benchmark.
#
# Despite living in the cluster runbook, this scenario does NOT need a
# cluster — it's a local LLM call against the labeled query set in
# z3rno-core/tests/eval/retrieval_auto_routing.jsonl. It lives here
# for grouping; one operator command, one report.
#
# Pre-req: OPENAI_API_KEY (or LLM_API_KEY) in env.

set -euo pipefail

CORE_DIR="$(cd "$(dirname "$0")/../../../../z3rno-core" && pwd)"
REPORTS_DIR="$(cd "$(dirname "$0")" && pwd)/reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
REPORT="${REPORTS_DIR}/auto_routing_${STAMP}.txt"
mkdir -p "${REPORTS_DIR}"

: "${OPENAI_API_KEY:?set OPENAI_API_KEY (or LLM_API_KEY) for the routing benchmark}"

echo "==> AUTO routing accuracy benchmark @ ${STAMP}"
echo "    model: ${LLM_MODEL:-openai/gpt-4o-mini}"

cd "${CORE_DIR}"
Z3RNO_RUN_EVAL=1 uv run pytest \
  tests/test_auto_routing_eval.py::test_auto_routing_accuracy_meets_threshold \
  -v -s 2>&1 | tee "${REPORT}"

# The test asserts ≥ 80%; if it passed, we're under target.
if ! grep -q "PASSED" "${REPORT}"; then
  echo "FAIL: AUTO routing accuracy below threshold; see ${REPORT}" >&2
  exit 1
fi

ACCURACY=$(grep "AUTO routing accuracy:" "${REPORT}" | sed 's/.*= //')
echo
echo "OK: AUTO routing accuracy = ${ACCURACY}"
echo "Report → ${REPORT}"
