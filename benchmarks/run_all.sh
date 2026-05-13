#!/usr/bin/env bash
# Run the full bench suite. Stands up the dev compose stack with the
# harness override applied, seeds a corpus, then runs every script in
# this directory that produces a JSON report. Outputs land under
# ``last-run/`` (or the first CLI arg).
#
# What runs:
#   microbench   — per-verb latency at c=1
#   throughput   — sustained ops/sec at higher concurrency
#   load_ramp    — concurrency ramp 1..30
#   forge_burst  — Forge ingest+distill pipeline (needs INGEST/DISTILL on)
#   pentest      — security probes
#
# ``forge_burst`` is opt-in via the FORGE=1 env var because it needs an
# LLM key and the Forge feature flags. ``pentest`` skips its rate-limit
# probe when the server reports rate limiting off, which is the bench
# default.
#
# CLI: ./benchmarks/run_all.sh [results_dir]
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

cd "$HARNESS_DIR"

echo ""
echo "================================================================"
echo "  microbench"
echo "================================================================"
"$PY" microbench.py "$RESULTS_DIR/microbench.json"

echo ""
echo "================================================================"
echo "  throughput"
echo "================================================================"
"$PY" throughput.py "$RESULTS_DIR/throughput.json"

echo ""
echo "================================================================"
echo "  load_ramp"
echo "================================================================"
"$PY" load_ramp.py "$RESULTS_DIR/load_ramp.json"

if [ "${FORGE:-0}" = "1" ]; then
  echo ""
  echo "================================================================"
  echo "  forge_burst"
  echo "================================================================"
  "$PY" forge_burst.py "$RESULTS_DIR/forge_burst.json"
else
  echo ""
  echo "(skipping forge_burst — set FORGE=1 to include)"
fi

echo ""
echo "================================================================"
echo "  pentest"
echo "================================================================"
"$PY" pentest.py "$RESULTS_DIR/pentest.json"

echo ""
echo "--- done. results in $RESULTS_DIR ---"
ls -la "$RESULTS_DIR"
