#!/usr/bin/env python3
"""Kills each core application service mid-stream (SIGKILL - immediate, no
graceful shutdown, the harshest realistic failure mode: an OOM kill or a
crashing process, not a clean shutdown) and measures real recovery time, not
a claimed one. This is the test that actually exercises the crash-and-
reprocess path docs/roadmap.md's "Kafka semantics" entry describes, instead
of just describing it - and depends on every service's `restart:
unless-stopped` policy in docker-compose.yml (added alongside this script:
a killed container doesn't come back on its own otherwise).

Kills PID 1 *from inside* the container (`docker exec <name> kill -9 1`),
not `docker kill <name>` from the host - found the hard way, by running
this and watching every target fail to recover. Docker's restart policies
(including `unless-stopped`) only apply when the container's main process
dies on its own; `docker kill`/`docker stop` are treated as an intentional
user action and explicitly suppress the restart policy until the container
is started again by hand. Killing PID 1 from inside is what actually
resembles the failure this policy exists for (the process crashing or
getting OOM-killed), and every Dockerfile here uses exec-form `CMD` (no
shell wrapper), so PID 1 is always the real application process, not an
intermediate shell.

Two recovery signals, not one, because "the process is back" and "the
pipeline is back" are different claims:
  - process_recovery_s: time from kill until /health responds again. This
    can be fast even before Kafka/WS reconnection finishes, since the
    health server starts early in each service's startup (see each
    service's main.rs/main.py) - it proves the container restarted, not
    that data is flowing again.
  - pipeline_recovery_s: time from kill until the service's own
    steady-throughput Prometheus counter is *higher* than its pre-kill
    value, confirmed twice a few seconds apart (one increase could be a
    stale scrape) - this is the number that actually matters.

Requires: the full docker-compose stack up and running (`make up`), and
`docker` on PATH.

Usage:
    python scripts/chaos_test.py                          # every target, one at a time
    python scripts/chaos_test.py --target ml-inference       # just one
    python scripts/chaos_test.py --timeout-s 90
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone

import httpx

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# Each target: its own /health port, and a PromQL expression for a counter
# that only goes up while the service is genuinely doing its job (not just
# "the HTTP server answered") - model-metrics for api-gateway rather than
# alerts-relayed, since alerts are sparse/threshold-gated but model-metrics
# fires on a fixed ~30s cadence per domain regardless of anomaly activity.
TARGETS = {
    "ingestion": {
        "health_url": "http://localhost:9101/health",
        "throughput_query": "sum(ingestion_events_total)",
    },
    "feature-service": {
        "health_url": "http://localhost:9102/health",
        "throughput_query": "sum(feature_windows_emitted_total)",
    },
    "ml-inference": {
        "health_url": "http://localhost:8010/health",
        "throughput_query": "sum(ml_features_consumed_total)",
    },
    "api-gateway": {
        "health_url": "http://localhost:8180/health",
        "throughput_query": "sum(api_model_metrics_relayed_total)",
    },
}


def promql_instant(expr: str) -> float | None:
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": expr}, timeout=10)
        resp.raise_for_status()
        result = resp.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except Exception:  # noqa: BLE001 - a scrape hiccup shouldn't kill the whole chaos run
        return None


def is_healthy(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except Exception:  # noqa: BLE001 - connection refused/reset IS "not healthy", not an error to propagate
        return False


def wait_until(predicate, timeout_s: float, interval_s: float = 0.5) -> float | None:
    """Polls `predicate` until it's true or `timeout_s` elapses. Returns the
    elapsed seconds on success, None on timeout."""
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if predicate():
            return time.monotonic() - started
        time.sleep(interval_s)
    return None


def run_one(target: str, cfg: dict, timeout_s: float) -> dict:
    print(f"\n=== {target} ===")
    baseline = promql_instant(cfg["throughput_query"])
    print(f"baseline throughput counter: {baseline}")

    print(f"docker exec {target} kill -9 1  (SIGKILL to PID 1, from inside the container)...")
    kill_started = time.monotonic()
    subprocess.run(["docker", "exec", target, "sh", "-c", "kill -9 1"], capture_output=True, text=True, check=True)

    process_recovery_s = wait_until(lambda: is_healthy(cfg["health_url"]), timeout_s)
    print(f"process_recovery_s: {process_recovery_s}")

    def throughput_resumed() -> bool:
        current = promql_instant(cfg["throughput_query"])
        if current is None or baseline is None:
            return False
        if current <= baseline:
            return False
        # confirm twice, a beat apart, so one stale/racy scrape can't pass this
        time.sleep(2)
        current2 = promql_instant(cfg["throughput_query"])
        return current2 is not None and current2 > baseline

    pipeline_recovery_s = None
    if process_recovery_s is not None:
        remaining = max(timeout_s - (time.monotonic() - kill_started), 1.0)
        pipeline_recovery_s = wait_until(throughput_resumed, remaining)
    print(f"pipeline_recovery_s: {pipeline_recovery_s}")

    return {
        "baseline_throughput_counter": baseline,
        "process_recovery_s": process_recovery_s,
        "pipeline_recovery_s": pipeline_recovery_s,
        "recovered": process_recovery_s is not None and pipeline_recovery_s is not None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=list(TARGETS), help="only test this one service (default: all, one at a time)")
    parser.add_argument("--timeout-s", type=float, default=60.0, help="max seconds to wait for each recovery signal")
    parser.add_argument("--settle-s", type=float, default=10.0, help="seconds to let a service run normally before the next kill")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    targets = [args.target] if args.target else list(TARGETS)
    results = {}
    for target in targets:
        results[target] = run_one(target, TARGETS[target], args.timeout_s)
        if target != targets[-1]:
            print(f"settling {args.settle_s}s before the next target...")
            time.sleep(args.settle_s)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeout_s": args.timeout_s,
        "results": results,
    }

    out_path = os.path.abspath(args.out)
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    existing["chaos_test"] = report
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    print("\n" + json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out_path}")

    if not all(r["recovered"] for r in results.values()):
        raise SystemExit("one or more targets did not recover within --timeout-s")


if __name__ == "__main__":
    main()
