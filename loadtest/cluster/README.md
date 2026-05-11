# Phase 3 cluster validation runbook

Single-command harness for the eight cluster-scale validations in
`z3rno-process-docs/improvements/PHASE-3-DEFERRED-TESTS.md`. The local
rig in `../run.sh` caps at ~1,500 concurrent users; this directory
drives the same scenarios at 10k+ against a real GKE / EKS cluster
plus a distributed Locust master.

Cost ballpark: **~$25 per cycle** on a 4-node n2-standard-4 + Cloud
SQL `db-custom-8-32GB`; ~half a day end-to-end including warm-up,
teardown, and report generation. Two or three cycles for a launch
validation = ~$75–150.

## Pre-reqs

- `kubectl` + `helm` 3.16+ + `kind` (for the dry-run smoke) or real
  cluster credentials.
- `gh` for downloading the per-run report artifact.
- Cluster of choice: GKE (`gcloud container clusters create …`) or
  EKS (`eksctl create cluster …`). Sizing in `terraform/`.
- LLM key (`OPENAI_API_KEY` etc.) only required for items that
  exercise Forge — items 1–7 don't need it.

## One-command run

```bash
export Z3RNO_BASE_URL=https://api.<your-cluster>.example.com
export Z3RNO_API_KEY=z3rno_sk_...
export AGENT_IDS="<uuid>,<uuid>,<uuid>"  # at least 3, one per test agent

# Item 1: 10k concurrent — read / write / mixed
bash run-stress.sh 10000

# Item 2: drainer throughput under sustained 1k+ RPS write
bash run-drainer.sh

# Item 5: helm-on-cluster end-to-end smoke
bash run-smoke.sh
```

Each script writes a timestamped JSON + HTML report under `reports/`
and prints the pass/fail summary on stdout. Targets are pinned per
the deferred-tests doc; deviations are flagged with a non-zero exit
code so the runbook integrates cleanly with a CI pipeline.

## Scenario → script map

| Phase 3 item | Script | Notes |
|---|---|---|
| 1. 10k concurrent (R/W/mixed) | `run-stress.sh 10000` | 8–12 server replicas × 4 uvicorn workers |
| 2. Drainer throughput @ sustained 1k RPS write | `run-drainer.sh` | Watches `audit_log_pending` row-count drain |
| 3. Backlog catch-up behaviour | `run-drainer.sh --backlog 100000` | Pre-seeds pending then measures drain rate |
| 4. Distributed multi-tenant load shape | `run-stress.sh 10000 --tenants 50` | 50 orgs × 200 users each |
| 5. Helm-on-cluster end-to-end smoke | `run-smoke.sh` | Apply chart, wait ready, hit `/v1/health` + `/v1/memories` |
| 6. NOTIFY/LISTEN-driven drain | `run-drainer.sh --notify` | Requires server v0.20+ where the listener lands |
| 7. Cross-region failover / HA | `run-failover.sh` | Multi-region cluster + DNS GTM required |
| 8. AUTO routing accuracy | `run-eval.sh` | LOCAL run — no cluster needed; runs via z3rno-evals |

Sticking to the same script-per-item layout keeps the runbook
greppable: each script's docstring lists its acceptance bar and
exact exit conditions.

## What "pass" means per item

The thresholds are codified in `targets.json`. Snapshot:

```json
{
  "stress.10k_read":  {"rps_min": 5000, "p95_max_ms": 1000, "error_rate_max": 0.001},
  "stress.10k_write": {"rps_min": 2000, "p95_max_ms": 2000, "error_rate_max": 0.001},
  "stress.10k_mixed": {"rps_min": 3000, "p95_max_ms": 1500, "error_rate_max": 0.001},
  "drainer.sustained": {"pending_rows_max": 10000, "lag_p95_seconds_max": 60},
  "drainer.backlog":   {"drain_rate_rows_per_s_min": 2000},
  "smoke.helm":        {"ready_within_seconds_max": 180},
  "auto_routing":      {"accuracy_min": 0.80}
}
```

Each script reads the relevant block; missing the floor → non-zero exit.

## Tear-down

```bash
bash teardown.sh
```

Deletes the kind / GKE / EKS cluster and all Locust workers. Cloud
billing stops at this point. Reports survive in `reports/`.
