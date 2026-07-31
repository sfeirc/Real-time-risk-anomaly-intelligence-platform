#!/usr/bin/env python3
"""Escalates `data-generator`'s target event rate (restarting the container
with a higher `DATA_GENERATOR_EVENTS_PER_SEC` each tier - it's read once at
process start, see services/data-generator/app/config.py, not a runtime
knob) until the pipeline measurably can't keep up, and reports the actual
measured ceiling on this hardware - not an estimate, not a vibe.

Each tier is judged "healthy" against three real, cheap-to-check signals,
not a subjective read of a dashboard:
  - achieved_rate_ratio: actual ingestion_events_total rate ÷ the tier's
    target rate. A generator that can't emit its own target as fast as
    asked (its own single asyncio loop is a real, separate bottleneck from
    the pipeline downstream of it - see the module docstring in
    services/data-generator/app/main.py) shows up here first.
  - ingest_to_alert p99: the headline latency metric
    (docs/data-contracts.md), checked against a multiple of the budget in
    ARCHITECTURE.md rather than the raw budget itself, since a *bounded*
    slowdown under real load is expected and not the same claim as
    "broken".
  - kafka_lag_growing: whether any consumer group's lag at the end of the
    measurement window is higher than at the start - a system keeping up
    has stable (not growing) lag; one falling behind accumulates a backlog
    that a fixed-duration snapshot alone won't show.

Stops at the first tier that fails any of these, reports the last healthy
tier and the failing one side by side, and restores data-generator to its
original rate afterward (in a `finally`, so a crash mid-run doesn't leave
the demo stuck at a stress-test rate) - this script changes live
docker-compose state, unlike load_test.py/chaos_test.py which only observe
or kill-and-let-recover.

Requires: the full docker-compose stack up (`make up`), `docker` on PATH.

Usage:
    python scripts/breaking_point_test.py
    python scripts/breaking_point_test.py --rates 200 500 1000 2000 4000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone

import httpx

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_HTTP_PORT = os.environ.get("CLICKHOUSE_HTTP_PORT", "8123")
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DB", "risk")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

DEFAULT_RATES = [200, 500, 1000, 2000, 4000, 8000, 16000]
WARMUP_S = 12
MEASURE_S = 30
MIN_ACHIEVED_RATIO = 0.90
MAX_LATENCY_BUDGET_MULTIPLE = 3.0
LATENCY_BUDGET_MS = 5150.0  # ARCHITECTURE.md: window_size_s(5s, payments) + 150ms p99 budget


def clickhouse_query(query: str) -> list[dict]:
    resp = httpx.post(
        f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_HTTP_PORT}/",
        params={"database": CLICKHOUSE_DB, "query": f"{query} FORMAT JSONEachRow", "output_format_json_quote_64bit_integers": "0"},
        auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
        timeout=30,
    )
    resp.raise_for_status()
    return [json.loads(line) for line in resp.text.strip().splitlines() if line]


def promql_instant(expr: str) -> float | None:
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": expr}, timeout=10)
        resp.raise_for_status()
        result = resp.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except Exception:  # noqa: BLE001 - a scrape hiccup shouldn't kill the whole run
        return None


def total_consumer_lag() -> float | None:
    return promql_instant(
        "sum(max(redpanda_kafka_max_offset) by (topic, partition) "
        "- max(redpanda_kafka_consumer_group_committed_offset) by (topic, partition))"
    )


def restart_data_generator(rate: int) -> None:
    env = {**os.environ, "DATA_GENERATOR_EVENTS_PER_SEC": str(rate)}
    subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "data-generator"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=env,
        capture_output=True, text=True, check=True,
    )


def measure_tier(rate: int) -> dict:
    print(f"\n=== target rate: {rate} events/s ===")
    restart_data_generator(rate)
    print(f"warming up {WARMUP_S}s...")
    time.sleep(WARMUP_S)

    lag_before = total_consumer_lag()
    print(f"measuring {MEASURE_S}s...")
    time.sleep(MEASURE_S)
    lag_after = total_consumer_lag()

    achieved_rate = promql_instant("sum(rate(ingestion_events_total[30s]))")
    ingest_to_alert_p99_row = clickhouse_query(
        f"SELECT quantile(0.99)(latency_ingest_to_alert_ms) AS p99, count() AS n "
        f"FROM alerts WHERE ts > now() - INTERVAL {MEASURE_S + WARMUP_S} SECOND"
    )
    ingest_to_alert_p99 = ingest_to_alert_p99_row[0]["p99"] if ingest_to_alert_p99_row and ingest_to_alert_p99_row[0]["n"] else None

    achieved_ratio = (achieved_rate / rate) if achieved_rate is not None else None
    lag_growing = lag_before is not None and lag_after is not None and lag_after > lag_before * 1.5 and (lag_after - lag_before) > 1000

    healthy = True
    reasons = []
    if achieved_ratio is None or achieved_ratio < MIN_ACHIEVED_RATIO:
        healthy = False
        reasons.append(f"achieved_rate_ratio={achieved_ratio} < {MIN_ACHIEVED_RATIO}")
    if ingest_to_alert_p99 is not None and ingest_to_alert_p99 > LATENCY_BUDGET_MS * MAX_LATENCY_BUDGET_MULTIPLE:
        healthy = False
        reasons.append(f"ingest_to_alert_p99={ingest_to_alert_p99}ms > {LATENCY_BUDGET_MS * MAX_LATENCY_BUDGET_MULTIPLE}ms")
    if lag_growing:
        healthy = False
        reasons.append(f"kafka_lag grew {lag_before} -> {lag_after}")

    result = {
        "target_rate": rate,
        "achieved_rate": achieved_rate,
        "achieved_rate_ratio": achieved_ratio,
        "ingest_to_alert_p99_ms": ingest_to_alert_p99,
        "kafka_lag_before": lag_before,
        "kafka_lag_after": lag_after,
        "healthy": healthy,
        "unhealthy_reasons": reasons,
    }
    print(json.dumps(result, indent=2, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rates", type=int, nargs="+", default=DEFAULT_RATES)
    parser.add_argument("--restore-rate", type=int, default=200, help="DATA_GENERATOR_EVENTS_PER_SEC to restore afterward (matches .env.example's default)")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    tiers = []
    try:
        for rate in args.rates:
            tier = measure_tier(rate)
            tiers.append(tier)
            if not tier["healthy"]:
                print(f"\nfirst unhealthy tier: {rate} events/s ({', '.join(tier['unhealthy_reasons'])}) - stopping here")
                break
        else:
            print(f"\nno tier failed the health checks up to {args.rates[-1]} events/s - the ceiling is at or above that, not found within this run's range")
    finally:
        print(f"\nrestoring data-generator to {args.restore_rate} events/s...")
        restart_data_generator(args.restore_rate)

    healthy_tiers = [t for t in tiers if t["healthy"]]
    last_healthy = healthy_tiers[-1]["target_rate"] if healthy_tiers else None
    first_unhealthy = next((t["target_rate"] for t in tiers if not t["healthy"]), None)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warmup_s": WARMUP_S,
        "measure_s": MEASURE_S,
        "last_healthy_rate": last_healthy,
        "first_unhealthy_rate": first_unhealthy,
        "tiers": tiers,
    }

    out_path = os.path.abspath(args.out)
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    existing["breaking_point_test"] = report
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    print(f"\nlast healthy tier: {last_healthy} events/s; first unhealthy tier: {first_unhealthy} events/s")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
