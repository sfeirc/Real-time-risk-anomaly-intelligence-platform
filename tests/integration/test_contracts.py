#!/usr/bin/env python3
"""Validates real event payloads against schemas/*.schema.json —
docs/data-contracts.md's promise that "drift between services fails CI
instead of failing at 3am in production."

Two modes:
  - live (default): pulls actual recent rows out of a running ClickHouse
    (`docker compose up`) and validates those — the strongest form of this
    test, since it catches drift in what services *actually* produce, not
    just what a hand-written example claims they produce.
  - offline (--offline, used in plain CI without the full stack up):
    validates hand-written example payloads matching docs/data-contracts.md
    against the same schema files, so schema-file/doc drift is still
    caught even with no running system.

Usage:
    python tests/integration/test_contracts.py            # live
    python tests/integration/test_contracts.py --offline    # no stack needed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
from jsonschema.validators import validator_for

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"

CLICKHOUSE_URL = "http://localhost:8123"
CLICKHOUSE_AUTH = ("default", "risk_dev_only")


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text())


def validate(schema_name: str, payload: dict, label: str) -> list[str]:
    schema = load_schema(schema_name)
    validator_cls = validator_for(schema)
    validator = validator_cls(schema)
    errors = [f"{label}: {e.message} at {'.'.join(str(p) for p in e.path)}" for e in validator.iter_errors(payload)]
    return errors


def clickhouse_query(query: str) -> list[dict]:
    resp = httpx.post(
        f"{CLICKHOUSE_URL}/",
        params={"database": "risk", "query": f"{query} FORMAT JSONEachRow", "output_format_json_quote_64bit_integers": "0"},
        auth=CLICKHOUSE_AUTH,
        timeout=15,
    )
    resp.raise_for_status()
    return [json.loads(line) for line in resp.text.strip().splitlines() if line]


def raw_event_row_to_payload(row: dict) -> dict:
    is_market = bool(row.get("m_symbol"))
    payload = (
        {
            "symbol": row["m_symbol"], "price": row["m_price"], "size": row["m_size"], "side": row["m_side"],
            "bid": row["m_bid"], "ask": row["m_ask"], "exchange_latency_ms": row["m_exchange_latency_ms"],
        }
        if is_market
        else {
            "txn_id": row["p_txn_id"], "merchant_id": row["p_merchant_id"], "account_id_hash": row["p_account_id_hash"],
            "amount": row["p_amount"], "currency": row["p_currency"], "channel": row["p_channel"],
            "country": row["p_country"], "processing_latency_ms": row["p_processing_latency_ms"], "status": row["p_status"],
        }
    )
    return {
        "event_id": row["event_id"], "domain": row["domain"], "entity_key": row["entity_key"], "source": row["source"],
        "seq": row["seq"], "ts_event": row["ts_event"].replace(" ", "T") + "Z", "ts_ingest": row["ts_ingest"].replace(" ", "T") + "Z",
        "corrupted": row["corrupted"], "scenario_label": row["scenario_label"] or None, "payload": payload,
    }


def feature_row_to_payload(row: dict) -> dict:
    row = dict(row)
    row["window_start"] = row["window_start"].replace(" ", "T") + "Z"
    row["window_end"] = row["window_end"].replace(" ", "T") + "Z"
    return row


def alert_row_to_payload(row: dict) -> dict:
    return {
        "alert_id": row["alert_id"],
        "entity_key": row["entity_key"],
        "domain": row["domain"],
        "ts": row["ts"].replace(" ", "T") + "Z",
        "window_end": row["window_end"].replace(" ", "T") + "Z",
        "anomaly_score": row["anomaly_score"],
        "severity": row["severity"],
        "action": row["action"],
        "detectors": {**{"xgboost": None}, **row["detectors"]},
        "explanation": {"probable_cause": row["probable_cause"], "top_features": json.loads(row["top_features"])},
        "model_version": row["model_version"],
        "drift_flag": row["drift_flag"],
        "latency_ingest_to_alert_ms": row["latency_ingest_to_alert_ms"],
    }


def run_live() -> list[str]:
    errors = []

    raw_rows = clickhouse_query("SELECT * FROM raw_events ORDER BY ts_ingest DESC LIMIT 5")
    if not raw_rows:
        return ["no rows in risk.raw_events — run the pipeline first, or use --offline"]
    for row in raw_rows:
        errors += validate("raw_event.schema.json", raw_event_row_to_payload(row), f"raw_events[{row['event_id']}]")

    feature_rows = clickhouse_query("SELECT * EXCEPT(ingest_date) FROM features ORDER BY window_end DESC LIMIT 5")
    for row in feature_rows:
        errors += validate("feature_event.schema.json", feature_row_to_payload(row), f"features[{row['entity_key']}@{row['window_end']}]")

    alert_rows = clickhouse_query("SELECT * EXCEPT(ingest_date) FROM alerts ORDER BY ts DESC LIMIT 5")
    for row in alert_rows:
        errors += validate("alert_event.schema.json", alert_row_to_payload(row), f"alerts[{row['alert_id']}]")

    return errors


def run_offline() -> list[str]:
    errors = []
    errors += validate(
        "raw_event.schema.json",
        {
            "event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1", "domain": "market", "entity_key": "BTC-USD",
            "source": "synthetic-exchange-1", "seq": 1, "ts_event": "2026-01-01T00:00:00.000Z", "ts_ingest": None,
            "corrupted": False, "scenario_label": None,
            "payload": {"symbol": "BTC-USD", "price": 65000.0, "size": 0.1, "side": "buy", "bid": 64990.0, "ask": 65010.0, "exchange_latency_ms": 3.5},
        },
        "offline example: raw_event (market)",
    )
    errors += validate(
        "feature_event.schema.json",
        {
            "entity_key": "BTC-USD", "domain": "market", "window_start": "2026-01-01T00:00:00.000Z", "window_end": "2026-01-01T00:00:02.000Z",
            "window_size_s": 2.0, "count": 20, "throughput_eps": 10.0, "latency_p50_ms": 5.0, "latency_p99_ms": 12.0, "error_rate": 0.0,
            "vwap": 65000.0, "spread_bps": 2.0, "realized_vol": 0.5, "order_imbalance": 0.0,
            "ewma_mean": 0.5, "ewma_var": 0.01, "zscore": 0.1, "primary_metric": 0.5,
        },
        "offline example: feature_event (market)",
    )
    errors += validate(
        "alert_event.schema.json",
        {
            "alert_id": "9069c681-fa99-41dd-98a2-c4af7df360d1", "entity_key": "BTC-USD", "domain": "market",
            "ts": "2026-01-01T00:00:02.100Z", "window_end": "2026-01-01T00:00:02.000Z", "anomaly_score": 0.8,
            "severity": "alert", "action": "alert",
            "detectors": {"zscore": 0.7, "ewma": 0.6, "cusum": 0.5, "isolation_forest": 0.8, "autoencoder": 0.9, "xgboost": None},
            "explanation": {"probable_cause": "volatility_spike", "top_features": [{"feature": "realized_vol", "value": 3.0, "baseline": 0.5, "contribution": 2.5}]},
            "model_version": "v0.1.0", "drift_flag": False, "latency_ingest_to_alert_ms": 100.0,
        },
        "offline example: alert_event",
    )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    errors = run_offline() if args.offline else run_live()

    if errors:
        print(f"{len(errors)} contract violation(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("all sampled payloads match their schemas")


if __name__ == "__main__":
    main()
