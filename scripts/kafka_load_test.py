#!/usr/bin/env python3
"""Produces synthetic RawEvent JSON directly onto the `raw-events` Kafka
topic via aiokafka, bypassing data-generator's WebSocket bridge to
`ingestion` entirely.

Why this exists: `scripts/breaking_point_test.py` found the standard
end-to-end path (data-generator → ingestion → Kafka → feature-service →
ml-inference → ClickHouse) plateaus at ~190 events/s regardless of a higher
configured target, and traced that ceiling to data-generator's single
WebSocket connection to ingestion - every container's CPU stayed low
throughout, meaning the *downstream* pipeline was never actually pushed
hard (see docs/metrics.md §7). This script removes that WS bridge from the
picture: it produces straight onto the topic `ingestion` normally writes
to, so what's measured here is feature-service's, ml-inference's, and
ClickHouse's own throughput ceiling, not the synthetic test harness's.

Runs several concurrent producer coroutines (a single tight loop still has
per-iteration Python/asyncio overhead - concurrency is what actually lets
aiokafka's own internal batching do the work) for a fixed duration, and
reports the true achieved produce rate (measured locally - the strongest
signal, since it doesn't depend on any downstream service being reachable)
alongside feature-service/ml-inference's consume rates, Kafka consumer lag
before/after, and a container CPU/memory snapshot.

Requires: the full docker-compose stack up (`make up`), reachable from the
host via Redpanda's external listener (localhost:19092 by default - see
docker-compose.yml's `redpanda` service).

Usage:
    python scripts/kafka_load_test.py --duration-s 60 --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import time
import uuid
from datetime import datetime, timezone

import httpx
from aiokafka import AIOKafkaProducer

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS_EXTERNAL", "localhost:19092")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
TOPIC = "raw-events"

# Deliberately *not* data-generator's real symbols (BTC-USD etc.) - this
# script inserts straight into risk.raw_events/risk.features, the same
# tables the live demo's real entities use. Reusing a real symbol would mix
# synthetic load-test volume into that entity's actual EWMA/z-score
# baseline and corrupt the live dashboard/eval numbers for it, not just add
# harmless extra rows.
MARKET_SYMBOLS = [f"SYNTH-MKT-{i:02d}" for i in range(5)]
MERCHANTS = [f"synth_merch_load_{i:02d}" for i in range(8)]
CONTAINERS = ["feature-service", "ml-inference", "clickhouse", "redpanda"]


def make_event(rng: random.Random, seq: int) -> tuple[str, dict]:
    if rng.random() < 0.6:
        entity_key = rng.choice(MARKET_SYMBOLS)
        price = rng.uniform(100, 70000)
        event = {
            "event_id": str(uuid.uuid4()),
            "domain": "market",
            "entity_key": entity_key,
            "source": "kafka-load-test",
            "seq": seq,
            "ts_event": datetime.now(timezone.utc).isoformat(),
            "ts_ingest": datetime.now(timezone.utc).isoformat(),
            "corrupted": False,
            "scenario_label": None,
            "payload": {
                "symbol": entity_key, "price": price, "size": rng.uniform(0.01, 5),
                "side": rng.choice(["buy", "sell"]), "bid": price - 1, "ask": price + 1,
                "exchange_latency_ms": rng.uniform(1, 10),
            },
        }
    else:
        entity_key = rng.choice(MERCHANTS)
        event = {
            "event_id": str(uuid.uuid4()),
            "domain": "payments",
            "entity_key": entity_key,
            "source": "kafka-load-test",
            "seq": seq,
            "ts_event": datetime.now(timezone.utc).isoformat(),
            "ts_ingest": datetime.now(timezone.utc).isoformat(),
            "corrupted": False,
            "scenario_label": None,
            "payload": {
                "txn_id": str(uuid.uuid4()), "merchant_id": entity_key,
                "account_id_hash": f"acct_{rng.randint(1, 1000)}",
                "amount": round(rng.uniform(5, 500), 2), "currency": "USD",
                "channel": "card_present", "country": "US",
                "processing_latency_ms": rng.uniform(5, 50),
                "status": "approved",
            },
        }
    return entity_key, event


async def producer_worker(worker_id: int, duration_s: float, counters: dict) -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BROKERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8"),
        compression_type="lz4",
        linger_ms=5,
    )
    await producer.start()
    rng = random.Random(worker_id)
    seq = 0
    sent = 0
    deadline = time.monotonic() + duration_s
    try:
        while time.monotonic() < deadline:
            seq += 1
            entity_key, event = make_event(rng, seq)
            await producer.send(TOPIC, key=entity_key, value=event)
            sent += 1
    finally:
        await producer.stop()
        counters[worker_id] = sent


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


def container_resource_snapshot() -> dict:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except Exception as e:  # noqa: BLE001 - resource snapshot is best-effort, not the point of the test
        return {"error": str(e)}
    snapshot = {}
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] in CONTAINERS:
            snapshot[parts[0]] = {"cpu_percent": parts[1], "memory": parts[2]}
    return snapshot


async def run(duration_s: float, concurrency: int) -> dict:
    print(f"producing with {concurrency} concurrent workers for {duration_s}s against {KAFKA_BROKERS}...")

    lag_before = total_consumer_lag()
    fs_before = promql_instant("sum(feature_events_consumed_total)")
    ml_before = promql_instant("sum(ml_features_consumed_total)")

    started = time.monotonic()
    counters: dict[int, int] = {}
    await asyncio.gather(*(producer_worker(i, duration_s, counters) for i in range(concurrency)))
    elapsed = time.monotonic() - started

    total_produced = sum(counters.values())
    produced_rate = total_produced / elapsed

    print("settling 10s so consumers can catch up on the tail of the burst...")
    await asyncio.sleep(10)

    lag_after = total_consumer_lag()
    fs_after = promql_instant("sum(feature_events_consumed_total)")
    ml_after = promql_instant("sum(ml_features_consumed_total)")
    resources = container_resource_snapshot()

    result = {
        "duration_s": duration_s,
        "concurrency": concurrency,
        "total_produced": total_produced,
        "produced_rate_events_per_s": produced_rate,
        "kafka_lag_before": lag_before,
        "kafka_lag_after": lag_after,
        "feature_service_consumed_rate_events_per_s": ((fs_after - fs_before) / (elapsed + 10)) if fs_before is not None and fs_after is not None else None,
        "ml_inference_consumed_rate_events_per_s": ((ml_after - ml_before) / (elapsed + 10)) if ml_before is not None and ml_after is not None else None,
        "container_resources": resources,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    result = asyncio.run(run(args.duration_s, args.concurrency))
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), **result}

    out_path = os.path.abspath(args.out)
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    existing["kafka_direct_load_test"] = report
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    print("\n" + json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
