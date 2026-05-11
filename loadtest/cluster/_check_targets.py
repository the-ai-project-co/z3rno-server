"""Helper: read a Locust stats CSV (or a pending-rows log) and compare
against the threshold block in ``targets.json``. Exit non-zero on miss.

Used by every ``run-*.sh`` script in this directory so the
pass/fail logic lives in exactly one place.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_targets() -> dict[str, dict[str, object]]:
    with (HERE / "targets.json").open() as f:
        return json.load(f)


def _check_locust_csv(scenario: str, csv_path: Path, targets: dict[str, object]) -> bool:
    """Locust ``_stats.csv``: one row per endpoint + an Aggregated row."""
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        agg = next((r for r in reader if r["Name"] == "Aggregated"), None)
    if agg is None:
        print(f"  {scenario}: no Aggregated row in {csv_path}", file=sys.stderr)
        return False

    rps = float(agg.get("Requests/s", "0") or 0)
    p95 = float(agg.get("95%", "0") or 0)
    fail_count = float(agg.get("Failure Count", "0") or 0)
    req_count = float(agg.get("Request Count", "1") or 1)
    err_rate = fail_count / req_count if req_count else 0.0

    rps_min = float(targets.get("rps_min", 0) or 0)
    p95_max = float(targets.get("p95_max_ms", 1e9) or 1e9)
    err_max = float(targets.get("error_rate_max", 1.0) or 1.0)

    ok = rps >= rps_min and p95 <= p95_max and err_rate <= err_max
    status = "OK" if ok else "FAIL"
    print(
        f"  [{status}] {scenario}: rps={rps:.0f} (≥{rps_min:.0f}) "
        f"p95={p95:.0f}ms (≤{p95_max:.0f}) "
        f"err={err_rate:.3%} (≤{err_max:.3%})"
    )
    return ok


def _check_pending_log(scenario: str, log_path: Path, targets: dict[str, object]) -> bool:
    """``pending=<int>`` lines emitted by the drainer watcher loop."""
    high_water = 0
    samples = 0
    drain_deltas: list[int] = []
    last: int | None = None
    with log_path.open() as f:
        for line in f:
            if "pending=" not in line:
                continue
            try:
                v = int(line.split("pending=")[1].strip())
            except (ValueError, IndexError):
                continue
            samples += 1
            high_water = max(high_water, v)
            if last is not None and v < last:
                drain_deltas.append(last - v)
            last = v

    rows_max = int(targets.get("pending_rows_max", 0) or 0)
    drain_min = int(targets.get("drain_rate_rows_per_s_min", 0) or 0)

    avg_drain_rate = sum(drain_deltas) / max(len(drain_deltas), 1) / 5.0  # 5s poll
    ok = True
    if rows_max and high_water > rows_max:
        ok = False
    if drain_min and avg_drain_rate < drain_min:
        ok = False

    status = "OK" if ok else "FAIL"
    print(
        f"  [{status}] {scenario}: high_water={high_water} (≤{rows_max or 'n/a'}) "
        f"avg_drain={avg_drain_rate:.0f} rows/s "
        f"(≥{drain_min or 'n/a'}) over {samples} samples"
    )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario", action="append", required=True,
        help="One or more scenario keys from targets.json",
    )
    parser.add_argument(
        "--csv", action="append", default=[],
        help="Locust _stats.csv path, paired with --scenario in order",
    )
    parser.add_argument(
        "--pending-log", default=None,
        help="drainer watcher log path",
    )
    args = parser.parse_args()

    targets = _load_targets()
    all_ok = True

    if args.pending_log:
        for s in args.scenario:
            block = targets.get(s, {})
            ok = _check_pending_log(s, Path(args.pending_log), block)
            all_ok = all_ok and ok

    for s, csv_path in zip(args.scenario, args.csv, strict=False):
        block = targets.get(s, {})
        ok = _check_locust_csv(s, Path(csv_path), block)
        all_ok = all_ok and ok

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
