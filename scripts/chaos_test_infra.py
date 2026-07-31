#!/usr/bin/env python3
"""Kills Redpanda and ClickHouse (SIGKILL to PID 1, from inside the
container - see scripts/chaos_test.py's docstring for why: a host-side
`docker kill` is treated as an intentional stop and suppresses Docker's own
`unless-stopped` restart policy, only an in-container process death
doesn't) and measures real recovery - the shared infra tier every
application service depends on, not yet covered by scripts/chaos_test.py's
per-application-service tests.

Two things chaos_test.py doesn't need to check that this does, because
Redpanda/ClickHouse are genuinely different from the four stateless
application services:

  - **Data durability**: killing the process must not lose what's on disk
    (the volume survives a process restart inside the same container -
    this is not the same claim as "the container was recreated", which
    scripts/breaking_point_test.py's data-generator restarts do intentionally
    reset). Checked by comparing a row/topic count before and after.
  - **Cascading reconnection**: every application service loses its
    Kafka/ClickHouse connection simultaneously when the shared infra goes
    down, not just one consumer - checked by confirming *all four*
    application services resume their own throughput counters afterward,
    not just that Redpanda/ClickHouse's own health endpoint recovers.

Requires: the full docker-compose stack up (`make up`), `docker` on PATH.

Usage:
    python scripts/chaos_test_infra.py
    python scripts/chaos_test_infra.py --target redpanda
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
CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_HTTP_PORT = os.environ.get("CLICKHOUSE_HTTP_PORT", "8123")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")

# Every application service's own throughput counter, to confirm a
# cascading reconnection actually happened everywhere, not just that the
# infra component's own health check recovered.
APP_THROUGHPUT_QUERIES = {
    "ingestion": "sum(ingestion_events_total)",
    "feature-service": "sum(feature_windows_emitted_total)",
    "ml-inference": "sum(ml_features_consumed_total)",
    "api-gateway": "sum(api_model_metrics_relayed_total)",
}

TIMEOUT_S = 90.0


def promql_instant(expr: str) -> float | None:
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": expr}, timeout=10)
        resp.raise_for_status()
        result = resp.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except Exception:  # noqa: BLE001 - a scrape hiccup shouldn't kill the whole chaos run
        return None


def wait_until(predicate, timeout_s: float, interval_s: float = 1.0) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if predicate():
            return time.monotonic() - started
        time.sleep(interval_s)
    return None


def kill_pid1(container: str) -> None:
    subprocess.run(["docker", "exec", container, "sh", "-c", "kill -9 1"], capture_output=True, text=True, check=True)


def redpanda_healthy() -> bool:
    try:
        out = subprocess.run(
            ["docker", "exec", "redpanda", "rpk", "cluster", "health"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
        # rpk pads the value with enough spaces to align the whole table
        # ("Healthy:                          true") - slicing a fixed
        # number of characters after the label missed "true" entirely on a
        # real run; check the *line* containing the label instead.
        health_line = next((line for line in out.splitlines() if "Healthy:" in line), "")
        return "true" in health_line
    except Exception:  # noqa: BLE001 - "not reachable" IS "not healthy"
        return False


def redpanda_topic_count() -> int | None:
    try:
        out = subprocess.run(
            ["docker", "exec", "redpanda", "rpk", "topic", "list"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        # header line + one line per topic
        lines = [line for line in out.strip().splitlines() if line.strip()]
        return max(len(lines) - 1, 0)
    except Exception:  # noqa: BLE001
        return None


def clickhouse_healthy() -> bool:
    try:
        return httpx.get(f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_HTTP_PORT}/ping", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def clickhouse_row_count(table: str) -> int | None:
    try:
        resp = httpx.post(
            f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_HTTP_PORT}/",
            params={"database": "risk", "query": f"SELECT count() AS n FROM {table} FORMAT JSONEachRow"},
            auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
            timeout=10,
        )
        resp.raise_for_status()
        return int(json.loads(resp.text.strip())["n"])
    except Exception:  # noqa: BLE001
        return None


def app_services_recovered(baselines: dict[str, float | None], timeout_s: float) -> dict[str, dict]:
    """After the infra component is back, confirm every dependent app
    service's own throughput counter actually resumed increasing - proves
    the reconnection cascaded correctly, not just that the shared
    component's own health check passed."""
    results = {}
    for name, query in APP_THROUGHPUT_QUERIES.items():
        baseline = baselines.get(name)

        def resumed(query=query, baseline=baseline):
            current = promql_instant(query)
            return current is not None and baseline is not None and current > baseline

        recovery_s = wait_until(resumed, timeout_s)
        results[name] = {"baseline": baseline, "recovered": recovery_s is not None, "recovery_s": recovery_s}
    return results


def run_redpanda() -> dict:
    print("\n=== redpanda ===")
    topics_before = redpanda_topic_count()
    app_baselines = {name: promql_instant(q) for name, q in APP_THROUGHPUT_QUERIES.items()}
    print(f"topics before: {topics_before}, app baselines: {app_baselines}")

    print("kill -9 PID 1 inside redpanda...")
    kill_pid1("redpanda")

    process_recovery_s = wait_until(redpanda_healthy, TIMEOUT_S)
    print(f"process_recovery_s (rpk cluster health): {process_recovery_s}")

    topics_after = redpanda_topic_count()
    data_intact = topics_before is not None and topics_after is not None and topics_after >= topics_before
    print(f"topics after: {topics_after}, data_intact={data_intact}")

    app_recovery = app_services_recovered(app_baselines, TIMEOUT_S) if process_recovery_s is not None else {}
    print(f"app service reconnection: {json.dumps(app_recovery, indent=2)}")

    return {
        "topics_before": topics_before,
        "topics_after": topics_after,
        "data_intact": data_intact,
        "process_recovery_s": process_recovery_s,
        "app_service_recovery": app_recovery,
        "fully_recovered": process_recovery_s is not None and data_intact and all(r["recovered"] for r in app_recovery.values()),
    }


def run_clickhouse() -> dict:
    print("\n=== clickhouse ===")
    rows_before = clickhouse_row_count("alerts")
    app_baselines = {name: promql_instant(q) for name, q in APP_THROUGHPUT_QUERIES.items()}
    print(f"risk.alerts rows before: {rows_before}, app baselines: {app_baselines}")

    print("kill -9 PID 1 inside clickhouse...")
    kill_pid1("clickhouse")

    process_recovery_s = wait_until(clickhouse_healthy, TIMEOUT_S)
    print(f"process_recovery_s (/ping): {process_recovery_s}")

    rows_after = clickhouse_row_count("alerts")
    data_intact = rows_before is not None and rows_after is not None and rows_after >= rows_before
    print(f"risk.alerts rows after: {rows_after}, data_intact={data_intact}")

    # ClickHouse recovering doesn't require Kafka reconnection the way
    # Redpanda's outage does - ingestion/feature-service never touch
    # ClickHouse directly for their own throughput - so instead of the
    # app-service cascade above, just check that feature-service's own
    # ClickHouse sink resumes actually writing new rows (risk.features
    # arrives frequently enough to observe this quickly), not merely that
    # /ping answers again.
    features_at_recovery = clickhouse_row_count("features") if process_recovery_s is not None else None
    writes_resumed_s = None
    if features_at_recovery is not None:
        writes_resumed_s = wait_until(lambda: (clickhouse_row_count("features") or 0) > features_at_recovery, TIMEOUT_S)
    print(f"writes_resumed_s: {writes_resumed_s}")

    return {
        "rows_before": rows_before,
        "rows_after": rows_after,
        "data_intact": data_intact,
        "process_recovery_s": process_recovery_s,
        "writes_resumed_s": writes_resumed_s,
        "fully_recovered": process_recovery_s is not None and data_intact,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=["redpanda", "clickhouse"], help="only test this one (default: both)")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    results = {}
    if args.target in (None, "redpanda"):
        results["redpanda"] = run_redpanda()
        if args.target is None:
            print("\nsettling 15s before the next target...")
            time.sleep(15)
    if args.target in (None, "clickhouse"):
        results["clickhouse"] = run_clickhouse()

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "results": results}

    out_path = os.path.abspath(args.out)
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    existing["chaos_test_infra"] = report
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    print("\n" + json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out_path}")

    if not all(r.get("fully_recovered") for r in results.values()):
        raise SystemExit("one or more infra targets did not fully recover")


if __name__ == "__main__":
    main()
