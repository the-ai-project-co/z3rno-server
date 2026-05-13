#!/usr/bin/env bash
# Sample pg_stat_activity + pg_locks + pg_stat_progress_vacuum every 100 ms
# for HOLD_S seconds. Emits one JSON-per-line per sample to:
#
#   pg_activity.jsonl   — live backends with wait events
#   pg_locks.jsonl      — lock graph snapshot
#   pg_autovac.jsonl    — anything in pg_stat_progress_vacuum
#
# Use alongside recall_load.py when investigating a latency tail. Run the
# load and the sampler in parallel; afterwards correlate slow requests
# (recall.jsonl) against PG state at the same timestamps. The V0-22-3
# RCA used exactly this harness to falsify the autovacuum hypothesis.
#
# Assumes a container named ``z3rno-pg`` running Postgres. Tweak if your
# stack uses a different container name.
#
# CLI: ./pg_sampler.sh [HOLD_S=90] [OUT_DIR=.]
set -euo pipefail

HOLD_S=${1:-90}
OUT_DIR=${2:-.}
PG_CONTAINER=${PG_CONTAINER:-z3rno-pg}

ACT=$OUT_DIR/pg_activity.jsonl
LOCKS=$OUT_DIR/pg_locks.jsonl
AUTOVAC=$OUT_DIR/pg_autovac.jsonl
: > "$ACT" ; : > "$LOCKS" ; : > "$AUTOVAC"

end=$(($(date +%s) + HOLD_S))
while [ "$(date +%s)" -lt "$end" ]; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)

  # 1) Live backends with wait events. Filter to z3rno DB.
  docker exec "$PG_CONTAINER" psql -U z3rno -d z3rno -At -F '|' -c "
    SELECT '$ts', pid, COALESCE(state,''),
           COALESCE(wait_event_type,''), COALESCE(wait_event,''),
           EXTRACT(EPOCH FROM (now() - state_change))::numeric(10,3),
           COALESCE(substring(query for 200), '')
    FROM pg_stat_activity
    WHERE datname='z3rno' AND state IS NOT NULL AND backend_type='client backend'
  " 2>/dev/null | while IFS='|' read ts pid state wt we age qry; do
    printf '{"ts":"%s","pid":%s,"state":"%s","wait_event_type":"%s","wait_event":"%s","age_s":%s,"query":%s}\n' \
      "$ts" "$pid" "$state" "$wt" "$we" "$age" \
      "$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$qry")"
  done >> "$ACT" || true

  # 2) Lock graph snapshot — who holds what, who's waiting.
  docker exec "$PG_CONTAINER" psql -U z3rno -d z3rno -At -F '|' -c "
    SELECT '$ts', l.pid, l.mode, l.granted::text, l.locktype,
           COALESCE(c.relname, '<no rel>')
    FROM pg_locks l
    LEFT JOIN pg_class c ON c.oid = l.relation
    WHERE l.database = (SELECT oid FROM pg_database WHERE datname='z3rno')
  " 2>/dev/null | while IFS='|' read ts pid mode granted locktype rel; do
    printf '{"ts":"%s","pid":%s,"mode":"%s","granted":%s,"locktype":"%s","relation":"%s"}\n' \
      "$ts" "$pid" "$mode" "$granted" "$locktype" "$rel"
  done >> "$LOCKS" || true

  # 3) Autovacuum progress — anything in flight.
  docker exec "$PG_CONTAINER" psql -U z3rno -d z3rno -At -F '|' -c "
    SELECT '$ts', pid, datname, COALESCE(relid::regclass::text, '<none>'),
           phase, heap_blks_total, heap_blks_scanned, index_vacuum_count
    FROM pg_stat_progress_vacuum
  " 2>/dev/null | while IFS='|' read ts pid db rel phase htot hscan iv; do
    printf '{"ts":"%s","pid":%s,"db":"%s","relation":"%s","phase":"%s","heap_blks_total":%s,"heap_blks_scanned":%s,"index_vacuum_count":%s}\n' \
      "$ts" "$pid" "$db" "$rel" "$phase" "$htot" "$hscan" "$iv"
  done >> "$AUTOVAC" || true

  sleep 0.1
done
echo "sampler done: $(wc -l < "$ACT") activity, $(wc -l < "$LOCKS") locks, $(wc -l < "$AUTOVAC") autovac"
