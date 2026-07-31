#!/usr/bin/env python3
"""Throughput, latency percentiles, and per-container CPU/memory cost,
measured against the live system over a fixed window — not estimated, not
claimed. Complements tests/eval/run_eval.py (detection quality) with the
"platform" half of docs/metrics.md. Writes into the same
docs/benchmarks/latest.json rather than overwriting it.

Requires: the full docker-compose stack up and running (`make up`), and
`docker` on PATH for the CPU/memory snapshot (skipped with a warning if
containers aren't reachable, e.g. when the pipeline is run outside Docker).

Usage:
    python scripts/load_test.py --duration-s 60
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

CONTAINERS = ["ingestion", "feature-service", "ml-inference", "api-gateway", "redpanda", "clickhouse"]


def clickhouse_query(query: str) -> list[dict]:
    base_url = f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_HTTP_PORT}"
    resp = httpx.post(
        f"{base_url}/",
        params={"database": CLICKHOUSE_DB, "query": f"{query} FORMAT JSONEachRow", "output_format_json_quote_64bit_integers": "0"},
        auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
        timeout=60,
    )
    resp.raise_for_status()
    return [json.loads(line) for line in resp.text.strip().splitlines() if line]


def promql_instant(expr: str) -> float | None:
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": expr}, timeout=10)
        resp.raise_for_status()
        result = resp.json()["data"]["result"]
        if not result:
            return None
        return float(result[0]["value"][1])
    except Exception:  # noqa: BLE001 — Prometheus being unreachable shouldn't kill the whole load test
        return None


def latency_percentiles(since_seconds: int) -> dict:
    rows = clickhouse_query(
        f"SELECT quantile(0.50)(latency_ingest_to_alert_ms) AS p50, "
        f"quantile(0.95)(latency_ingest_to_alert_ms) AS p95, "
        f"quantile(0.99)(latency_ingest_to_alert_ms) AS p99, "
        f"count() AS n "
        f"FROM alerts WHERE ts > now() - INTERVAL {since_seconds} SECOND"
    )
    return rows[0] if rows else {"p50": None, "p95": None, "p99": None, "n": 0}


def throughput(since_seconds: int) -> dict:
    def count(table: str) -> int:
        ts_col = "ts_ingest" if table == "raw_events" else ("window_end" if table == "features" else "ts")
        rows = clickhouse_query(f"SELECT count() AS n FROM {table} WHERE {ts_col} > now() - INTERVAL {since_seconds} SECOND")
        return int(rows[0]["n"]) if rows else 0

    raw = count("raw_events")
    features = count("features")
    alerts = count("alerts")
    return {
        "raw_events_per_s": raw / since_seconds,
        "features_per_s": features / since_seconds,
        "alerts_per_s": alerts / since_seconds,
    }


def hop_latency_p99_ms() -> dict:
    return {
        "ingestion_ws_to_kafka_ms": promql_instant("histogram_quantile(0.99, sum(rate(ingestion_ws_to_kafka_ms_bucket[5m])) by (le))"),
        "feature_window_emit_lag_ms": promql_instant("histogram_quantile(0.99, sum(rate(feature_window_emit_lag_ms_bucket[5m])) by (le))"),
        "ml_inference_ms": promql_instant("histogram_quantile(0.99, sum(rate(ml_inference_ms_bucket[5m])) by (le))"),
    }


def container_resource_snapshot() -> dict:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except Exception as e:  # noqa: BLE001 — resource snapshot is best-effort, not the point of the test
        return {"error": str(e)}

    snapshot = {}
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, cpu, mem = parts
        if name in CONTAINERS:
            snapshot[name] = {"cpu_percent": cpu, "memory": mem}
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-s", type=int, default=60)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    print(f"measuring for {args.duration_s}s against the live system...")
    time.sleep(args.duration_s)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": args.duration_s,
        "throughput": throughput(args.duration_s),
        "latency_ingest_to_alert_ms": latency_percentiles(args.duration_s),
        "hop_latency_p99_ms": hop_latency_p99_ms(),
        "container_resources": container_resource_snapshot(),
    }

    out_path = os.path.abspath(args.out)
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    existing["load_test"] = report
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    print(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
